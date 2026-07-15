// Prompt 測試沙盒「依條件批量選取」：比照 usePrejudgeJob.ts 的目標選取 pattern（targetMode/
// targetStages/draftFilters/scopeBody/refreshTargetCount），僅取其目標解析部分——不含 SSE 進度/
// 暫停/恢復/停止（沙盒自有更簡單的 job 輪詢，見 PromptSandboxDrawer.vue）。與初判分類共用同一套
// 後端標的解析（`_resolve_target_ids`），前端草稿→body 的組法亦對齊，降低維護者的認知切換成本。
import { computed, reactive, ref, toValue, type MaybeRefOrGetter } from 'vue';
import { previewPromptSandboxCount, type PromptSandboxStartBody } from '@/api';
import { emptyFilters, filtersToParams, STAGE_LABELS } from '../constants';
import type { PrejudgeListFilters } from './usePrejudgeJob';

interface PromptSandboxTargetsDeps {
  /** 目前選定來源（getter / ref / 純值）。 */
  source: MaybeRefOrGetter<string>;
  /** 生效的商品垂直分類（getter / ref / 純值；唯讀消費，接受父層 computed 或子元件唯讀 prop）。 */
  effVerticals: MaybeRefOrGetter<string[] | undefined>;
  /** 跨頁累積的勾選 review（source_id）；targetMode='selected' 時 within_ids 交集用（唯讀消費）。 */
  selectedKeys: MaybeRefOrGetter<string[]>;
  /** 頁面當前列表篩選快照（開選取器時自動帶入草稿初值；唯讀消費）。 */
  listFilters: MaybeRefOrGetter<PrejudgeListFilters>;
}

/**
 * Prompt 測試沙盒依條件批量選取控制。
 * @returns 目標選取狀態（範圍/階段/篩選草稿/預覽筆數）+ 組 body / 預覽 / 開選取器。
 */
export function usePromptSandboxTargets(deps: PromptSandboxTargetsDeps) {
  const { source, effVerticals, selectedKeys, listFilters } = deps;

  /** 選取範圍：selected＝在「已選 N 筆」內做階段+篩選目標選取（within_ids 交集）；scope＝全部資料。 */
  const targetMode = ref<'selected' | 'scope'>('scope');
  const targetStages = ref<string[]>(['unjudged']);
  const targetCount = ref(0); // 「將測試 N 筆」預覽
  const draftFilters = reactive(emptyFilters());
  /** 是否含已判階段（非 unjudged）→ 顯示傾向/信心分層/歸因分類收斂條件。 */
  const hasJudgedStage = computed(() => targetStages.value.some((s) => s !== 'unjudged'));

  const _lf = (): PrejudgeListFilters => {
    const p = filtersToParams(draftFilters);
    return {
      polarity: p.polarity,
      confidenceTier: p.confidenceTier,
      taxonomy: p.taxonomy,
      hasExternal: p.hasExternal,
      dateFrom: p.dateFrom,
      dateTo: p.dateTo,
      recOid: p.recOid,
      prodOid: p.prodOid,
      orderOid: p.orderOid,
    };
  };

  /** 組 scope=all 目標選取 body（prompt_ids 由呼叫端補齊；start/count 共用同一套 → 預覽=實跑）。 */
  const scopeBody = (promptIds: string[]): PromptSandboxStartBody => {
    const lf = _lf();
    return {
      source: toValue(source),
      scope: 'all',
      prompt_ids: promptIds,
      product_verticals: toValue(effVerticals),
      stages: targetStages.value,
      within_ids: targetMode.value === 'selected' ? [...toValue(selectedKeys)] : undefined,
      date_from: lf.dateFrom,
      date_to: lf.dateTo,
      rec_oid: lf.recOid,
      prod_oid: lf.prodOid,
      order_oid: lf.orderOid,
      has_external: lf.hasExternal === undefined ? undefined : lf.hasExternal === 'true',
      ...(hasJudgedStage.value
        ? {
            target_polarity: lf.polarity,
            confidence_tier: lf.confidenceTier,
            taxonomy: lf.taxonomy,
          }
        : {}),
    };
  };

  // 單調遞增請求序號：快速切換條件會併發多次 refresh，僅最後一次可寫入 targetCount（防慢回應覆蓋新值）。
  let countSeq = 0;
  /** 「將測試 N 筆」預覽：與實跑同一 body 打後端 count 端點。 */
  const refreshTargetCount = async (promptIds: string[]) => {
    if (!promptIds.length) {
      targetCount.value = 0;
      return;
    }
    const seq = ++countSeq;
    try {
      const r = await previewPromptSandboxCount(scopeBody(promptIds));
      if (seq === countSeq) targetCount.value = r.total;
    } catch {
      /* 預覽失敗維持上次值不阻斷操作；實跑筆數仍以後端解析為準 */
    }
  };

  /** 開依條件批量選取：目標篩選草稿自動帶入頁面當前列表篩選（可重選）。
   *  範圍預設：有勾選＝「已選內」且收全部階段（初始目標＝整個勾選集合）；
   *  無勾選＝全部資料且只測未判（安全預設，避免誤觸發全庫測試）。 */
  const openTargetPicker = () => {
    const hasSel = toValue(selectedKeys).length > 0;
    targetMode.value = hasSel ? 'selected' : 'scope';
    targetStages.value = hasSel ? Object.keys(STAGE_LABELS) : ['unjudged'];
    const lf = toValue(listFilters);
    draftFilters.dateRange = lf.dateFrom && lf.dateTo ? [lf.dateFrom, lf.dateTo] : [];
    draftFilters.recOid = lf.recOid || '';
    draftFilters.prodOid = lf.prodOid || '';
    draftFilters.orderOid = lf.orderOid || '';
    draftFilters.hasExternal = lf.hasExternal || '';
    draftFilters.tier = lf.confidenceTier || '';
    draftFilters.taxonomy = lf.taxonomy ? [...lf.taxonomy] : [];
    draftFilters.polarity = hasSel ? [] : lf.polarity?.length ? [...lf.polarity] : [];
  };

  return {
    targetMode,
    targetStages,
    draftFilters,
    targetCount,
    hasJudgedStage,
    scopeBody,
    refreshTargetCount,
    openTargetPicker,
  };
}
