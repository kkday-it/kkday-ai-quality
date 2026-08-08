/**
 * 人工糾正工作台的狀態（頁面元件只留 template，狀態一律下沉此處）。
 *
 * **顆粒度是「一則反饋」不是「一條歸因」**（2026-08-07 重寫）：舊版抽屜寫死取
 * `record.attributions[0]`，於是一則反饋有多條歸因時第二條以後完全碰不到。正確的心智模型是
 * 「這則反饋現在有這些分類，我要調整它們」——看得到全貌、挑任一條改、補上漏判的、標記誤判的，
 * **條數可增可減**。
 *
 * **逐條提交，不做批次草稿**。兩個理由：
 * 1. 人工託管的閂鎖是「第一次寫入成功才生效」。逐條提交下，送出第 1 條該反饋就鎖住，其餘操作
 *    物理上不可能被批量重判輾過；批次草稿則有一段編輯期間該反饋仍是 AI 託管，任何人按批量初判
 *    就會 DELETE+INSERT 換掉全部 attribution_oid，送出時逐條打 404——而且是部分套用後才炸。
 * 2. 逐條的產出（N 條理由 + N 份欄位級 delta）永遠能壓成批次的產出，反過來不行。金標怎麼回餵
 *    還沒定案前，選資訊量大的那個。
 *
 * 理由一律必填——那是這套設計避免重蹈 2026-08-04 人工判決軸覆轍的核心（舊版只有兩顆沒有資訊量
 * 的按鈕，6,242 條裡只有 1 個人按過）。
 */
import { computed, reactive, ref } from 'vue';
import { useTimeoutFn } from '@vueuse/core';
import { Message } from '@arco-design/web-vue';
import {
  type AttributionNote,
  type CorrectionPolicy,
  type NoteType,
  type RecordAttributions,
  addAttributionNote,
  confirmAttribution,
  correctAttribution,
  createAttribution,
  deleteAttribution,
  getCorrectionPolicy,
  getNoteTypes,
  getRecordAttributions,
  listAttributionNotes,
  restoreAttribution,
  swapAttributionSlots,
} from '@/api';
import type { Attribution } from '../constants';

/** 剛動過的列高亮多久後自動淡出（ms）。夠久到看得見、短到不會讓三次連續操作疊三條亮列。 */
const HIGHLIGHT_MS = 2000;

/**
 * 政策載入失敗時的保守值。
 *
 * ⚠️ **不能用空陣列**：後端拿 `editable_fields` 當寫入白名單，空陣列會讓**任何** changes 都被
 * 判成「這些欄位不開放人工修改」——整個糾正功能靜默失效。這裡的值與後端 `_cfg()` 的內建預設同源。
 */
const POLICY_FALLBACK: CorrectionPolicy = {
  editable_fields: ['l1_code', 'l2_code', 'polarity', 'sentiment_score'],
  reason_min_length: 5,
  reason_max_length: 500,
};

/** 單列編輯草稿（未帶的欄＝不改，沿用現值）。 */
export interface CorrectionDraft {
  l2_code: string | undefined;
  /** 情緒分 1-5；**傾向由它派生**（負 1-2 / 中 3 / 正 4-5），故草稿不含 polarity。 */
  sentiment_score: number | undefined;
  /** 僅新增模式必填（那一列從頭到尾是人寫的，語義乾淨；改既有列不開放動 summary）。 */
  summary: string;
  reason: string;
}

/**
 * 草稿工廠——**每一列各自一份**。
 *
 * 工作台可以展開任一列編輯，若共用單一 reactive 物件，切換編輯列時前一列沒送出的輸入會污染下一列。
 */
export const emptyDraft = (): CorrectionDraft => ({
  l2_code: undefined,
  sentiment_score: undefined,
  summary: '',
  reason: '',
});

/** 本次工作階段已提交的變更（頂部「本次已提交 N 項」用，關窗即清）。 */
export interface SessionEntry {
  op: 'update' | 'create' | 'delete' | 'restore' | 'confirm' | 'swap';
  label: string;
}

const OP_TEXT: Record<SessionEntry['op'], string> = {
  update: '修改',
  create: '新增',
  delete: '標記誤判',
  restore: '還原',
  confirm: '確認正確',
  // 互換動了兩條列，記成「修改」會讓人以為只改了一條——變更記錄的用途正是回看做過什麼。
  swap: '互換面向',
};

export function useAttributionCorrection(onDone?: () => void) {
  const open = ref(false);
  const policy = ref<CorrectionPolicy | null>(null);
  /** 本次操作的座標（哪則反饋）——**不含 attribution_oid**，工作台是反饋級的。 */
  const target = reactive<{ source: string; sourceId: string }>({ source: '', sourceId: '' });

  const data = ref<RecordAttributions | null>(null);
  const loading = ref(false);
  const error = ref('');
  /** 畫面資料已過期（該反饋被重新初判、oid 全換新）——擋住後續提交並要求重新載入。 */
  const stale = ref(false);

  /** per-row 忙碌旗標；`-1` 保留給「新增」。全域一顆會讓整張表一起轉圈。 */
  const busyOid = ref<number | null>(null);
  /** 展開編輯中的列（單開：展開第二列自動收第一列，避免多份未提交編輯並存）。 */
  const editingOid = ref<number | null>(null);
  const creating = ref(false);
  /** 剛動過的列（黃底高亮 + 捲入視野的目標）。 */
  const justChangedOid = ref<number | null>(null);
  /**
   * 高亮的自動淡出計時器。
   *
   * 不淡出的話高亮會一直留到重開抽屜——連續改三條就會有三條同時亮著，「剛動的是哪一條」
   * 這個訊息反而消失了。用 `useTimeoutFn` 而非裸 `setTimeout`：元件卸載時自動清除，
   * 免得抽屜關掉後計時器還在跑、回頭寫一個已經沒人看的 ref。
   */
  const { start: startFade, stop: stopFade } = useTimeoutFn(
    () => (justChangedOid.value = null),
    HIGHLIGHT_MS,
    { immediate: false },
  );

  /**
   * 標記剛動過的列：亮起，到時自動淡出。
   *
   * **只管狀態，不碰 DOM**——把列捲入視野是元件的事（class 名與表格結構都屬於它）。
   * composable 去 `querySelector` 元件的 class 是反向依賴，換個版型就靜默失效。
   */
  const highlight = (oid: number | null) => {
    stopFade();
    justChangedOid.value = oid;
    if (oid != null) startFade();
  };

  const editDraft = reactive<CorrectionDraft>(emptyDraft());
  const createDraft = reactive<CorrectionDraft>(emptyDraft());
  const sessionLog = ref<SessionEntry[]>([]);
  /** 上一次送出的理由——供「沿用上一條理由」按鈕填入（**點了才填，不預填**）。 */
  const lastReason = ref('');

  /**
   * 這則反饋的全部備註（含整則備註與各面向備註）。
   *
   * 放在工作台而不是只放時間軸，是因為**面向備註的入口只能在這裡**：人是在看某一條歸因時才想
   * 對那個面向留話。時間軸負責「回頭讀」，工作台負責「當下寫」。
   */
  const notes = ref<AttributionNote[]>([]);
  const noteTypes = ref<NoteType[]>([]);

  /** 某個 L2 面向上的備註（舊到新）。整則備註不在此列——那是時間軸的入口。 */
  const notesForSlot = (l2Code: string | null | undefined) =>
    l2Code ? notes.value.filter((n) => n.slot?.l2_code === l2Code) : [];

  const live = computed(() => data.value?.live ?? []);
  const deleted = computed(() => data.value?.deleted ?? []);
  const humanManaged = computed(() => data.value?.human_managed ?? false);
  const suggestionCount = computed(() => data.value?.suggestion_count ?? 0);

  /**
   * 已被本反饋佔用的 (l1_code, l2_code) 面向——**含 tombstone**。
   *
   * 後端 `_assert_slot_free` 對「同反饋已有該面向」一律回 409（tombstone 也算佔用，因為自然鍵
   * 唯一索引不分是否已標記誤判）。工作台既然已經拿到含 tombstone 的完整清單，就能把這組面向
   * 在 cascader 上直接標 disabled——**把 409 從事後報錯變成事前不可選**。
   */
  const occupiedSlots = computed(() => {
    const map = new Map<string, { oid: number; dismissed: boolean }>();
    // `attribution_oid` 在共用型別上是 optional（待審建議的 add 提案還沒落庫、確實沒有 oid），
    // 但這裡的來源是持久化列，必定有值；缺值時略過而不是硬轉型，讓資料異常止於一格。
    for (const a of live.value) {
      if (a.l2?.code && a.attribution_oid) {
        map.set(a.l2.code, { oid: a.attribution_oid, dismissed: false });
      }
    }
    for (const a of deleted.value) {
      if (a.l2?.code && a.attribution_oid) {
        map.set(a.l2.code, { oid: a.attribution_oid, dismissed: true });
      }
    }
    return map;
  });

  const reasonOk = (d: CorrectionDraft) =>
    d.reason.trim().length >= (policy.value?.reason_min_length ?? 5);

  /** 修改：至少要動一個欄位，否則是沒有意義的空提交。 */
  const canSubmitEdit = computed(
    () =>
      reasonOk(editDraft) &&
      busyOid.value === null &&
      !stale.value &&
      !!(editDraft.l2_code || editDraft.sentiment_score),
  );
  /** 新增：分類、情緒分、摘要三者皆必填（後端三個都擋）。 */
  const canSubmitCreate = computed(
    () =>
      reasonOk(createDraft) &&
      busyOid.value === null &&
      !stale.value &&
      !!createDraft.l2_code &&
      !!createDraft.sentiment_score &&
      !!createDraft.summary.trim(),
  );

  /** 重新載入該反饋的全部歸因（含 tombstone）與備註。 */
  const reload = async () => {
    if (!target.sourceId) return;
    loading.value = true;
    error.value = '';
    try {
      data.value = await getRecordAttributions(target.source, target.sourceId);
      stale.value = false;
    } catch (e: unknown) {
      data.value = null;
      error.value = (e as Error)?.message || '載入歸因失敗';
    } finally {
      loading.value = false;
    }
    // 備註失敗不擋工作台：糾正本身不依賴備註，把整個抽屜打成 error 狀態是過度反應。
    try {
      notes.value = await listAttributionNotes(target.source, target.sourceId);
    } catch {
      notes.value = [];
    }
  };

  /**
   * 對某個面向留一則備註（append-only，送出即定案）。
   *
   * 綁的是**面向** `(l1_code, l2_code)` 而不是 `attribution_oid`——後者一重新初判就全部換新，
   * 備註隨即成孤兒（2026-08-04 退役的 `finding_notes` 正是這樣死的：8 列有 6 列孤兒）。
   */
  const addSlotNote = async (
    l1Code: string,
    l2Code: string,
    noteType: string,
    content: string,
  ) => {
    try {
      await addAttributionNote({
        source: target.source,
        source_id: target.sourceId,
        note_type: noteType,
        content: content.trim(),
        l1_code: l1Code,
        l2_code: l2Code,
      });
      notes.value = await listAttributionNotes(target.source, target.sourceId);
      Message.success('已留下備註');
      onDone?.();
      return true;
    } catch (e: unknown) {
      Message.error((e as Error)?.message || '留備註失敗');
      return false;
    }
  };

  /** 開啟工作台（反饋級：只需要來源與反饋 id）。 */
  const openFor = async (source: string, sourceId: string) => {
    target.source = source;
    target.sourceId = sourceId;
    Object.assign(editDraft, emptyDraft());
    Object.assign(createDraft, emptyDraft());
    editingOid.value = null;
    creating.value = false;
    justChangedOid.value = null;
    sessionLog.value = [];
    lastReason.value = '';
    stale.value = false;
    open.value = true;
    if (!policy.value) {
      try {
        policy.value = await getCorrectionPolicy();
      } catch {
        policy.value = POLICY_FALLBACK;
      }
    }
    // 互動類型值域是業務可在後台改的參照資料，載一次即可（同一 session 內不會變）。
    if (!noteTypes.value.length) {
      try {
        noteTypes.value = await getNoteTypes();
      } catch {
        noteTypes.value = [];
      }
    }
    await reload();
  };

  /** 展開某列的編輯區（單開；預填空草稿，不帶現值——只送出「動過的欄」）。 */
  const startEdit = (a: Attribution) => {
    Object.assign(editDraft, emptyDraft());
    creating.value = false;
    editingOid.value = a.attribution_oid ?? null;
  };
  const startCreate = () => {
    Object.assign(createDraft, emptyDraft());
    editingOid.value = null;
    creating.value = true;
  };
  const cancelEdit = () => {
    editingOid.value = null;
    creating.value = false;
  };

  /**
   * 送出一個動作。
   *
   * **成功後不關抽屜**——留在工作台繼續處理下一條，是反饋級設計的核心體驗。取而代之給四層回饋：
   * toast、該列黃底高亮、標題計數更新、本階段變更記錄 +1。
   */
  const _run = async (
    oid: number,
    op: SessionEntry['op'],
    fn: () => Promise<unknown>,
    okText: string,
    reason = '',
  ) => {
    busyOid.value = oid;
    try {
      await fn();
      if (reason) lastReason.value = reason;
      sessionLog.value = [...sessionLog.value, { op, label: OP_TEXT[op] }];
      Message.success(okText);
      cancelEdit();
      await reload();
      highlight(oid > 0 ? oid : null);
      onDone?.();
    } catch (e: unknown) {
      const err = e as Error & { status?: number };
      // 404＝這則反饋剛被重新初判（AI 託管下是 DELETE+INSERT，attribution_oid 全部換新）。
      // 後端的原始訊息是「歸因不存在或不屬於此反饋：oid=123」，對使用者是天書，必須翻譯。
      if (err?.status === 404) {
        stale.value = true;
        Message.error('這則反饋剛被重新初判，畫面上的資料已過期');
      } else {
        // 其餘 4xx 的 detail 是寫給人看的（如「該面向已有歸因，請直接修改那一條」），直接顯示
        Message.error(err?.message || '操作失敗');
      }
    } finally {
      busyOid.value = null;
    }
  };

  const _base = () => ({ source: target.source, source_id: target.sourceId });

  const submitEdit = (oid: number) => {
    const changes: Record<string, unknown> = {};
    if (editDraft.l2_code) changes.l2_code = editDraft.l2_code;
    if (editDraft.sentiment_score) changes.sentiment_score = editDraft.sentiment_score;
    const reason = editDraft.reason.trim();
    return _run(
      oid,
      'update',
      () => correctAttribution({ ..._base(), attribution_oid: oid, changes, reason }),
      '已更新歸因；此後重新初判不會覆蓋你的修改',
      reason,
    );
  };

  const submitCreate = () => {
    const reason = createDraft.reason.trim();
    return _run(
      -1,
      'create',
      () =>
        createAttribution({
          ..._base(),
          values: {
            l2_code: createDraft.l2_code,
            sentiment_score: createDraft.sentiment_score,
            summary: createDraft.summary.trim(),
          },
          reason,
        }),
      '已新增人工歸因',
      reason,
    );
  };

  const dismiss = (oid: number, reason: string) =>
    _run(
      oid,
      'delete',
      () => deleteAttribution({ ..._base(), attribution_oid: oid, reason }),
      '已標記為 AI 誤判（可隨時還原）',
      reason,
    );

  const restore = (oid: number, reason: string) =>
    _run(
      oid,
      'restore',
      () => restoreAttribution({ ..._base(), attribution_oid: oid, reason }),
      '已還原',
      reason,
    );

  /**
   * 與另一條歸因互換面向（一次操作、兩條同時生效）。
   *
   * ⚠️ 這不是「改兩次」的語法糖：先改哪一條都會撞上另一條佔著的面向，後端靠 DEFERRABLE 約束
   * 在單一交易內延後檢查才做得到（見 db.corrections.swap_attribution_slots）。
   */
  const swapWith = (oidA: number, oidB: number, reason: string) =>
    _run(
      oidA,
      'swap',
      () =>
        swapAttributionSlots({
          ..._base(),
          attribution_oid_a: oidA,
          attribution_oid_b: oidB,
          reason,
        }),
      '已互換兩條歸因的面向',
      reason,
    );

  /** 複審確認 AI 判對（待複審的出口；2026-08-07 起也會讓該反饋進入人工託管）。 */
  const confirmCorrect = (oid: number, fields: string[] = []) =>
    _run(
      oid,
      'confirm',
      () => confirmAttribution({ ..._base(), attribution_oid: oid, confirmed_fields: fields }),
      '已標記為確認正確；此後重新初判不會覆蓋它',
    );

  return {
    open,
    target,
    policy,
    loading,
    error,
    stale,
    live,
    deleted,
    humanManaged,
    suggestionCount,
    occupiedSlots,
    busyOid,
    editingOid,
    creating,
    justChangedOid,
    editDraft,
    createDraft,
    sessionLog,
    lastReason,
    notes,
    noteTypes,
    notesForSlot,
    addSlotNote,
    canSubmitEdit,
    canSubmitCreate,
    openFor,
    reload,
    startEdit,
    startCreate,
    cancelEdit,
    submitEdit,
    submitCreate,
    dismiss,
    restore,
    swapWith,
    confirmCorrect,
  };
}
