<script setup lang="ts">
/**
 * 售後根因 Prompt 版本列表：草稿與正式版**併為單一列表**，構成看／比／升／退的完整閉環。
 * 「取」（載入編輯器）不在這個抽屜內——見下方說明。
 *
 * 為什麼併成一張表（而非草稿／正式各一張、或雙 tab）：跨軌對比是這個面板最常用的動作
 * （「我改的這版和線上差在哪」）。兩軌在同一張表裡不需要處理「切 tab 保留篩選」的狀態問題。
 *
 * 為什麼對比拆成巢狀抽屜（`PromptVersionDiffDrawer`）：全文約 105KB，內嵌 diff 會把列表推到
 * 捲軸深處，而列表才是本抽屜的主體。選版就在對比抽屜內用兩個分組 select 切換，不在本列表
 * 每列放「設為 A／B」——47 列各掛兩顆選取鈕是純視覺噪音。
 *
 * 為什麼列操作只剩「升為正式版」「設為使用中」：這兩個動作**沒有其他入口**；「對比」與
 * 「載入編輯器」則另有更直接的入口（對比抽屜自選兩版／頁面頂部版本下拉），放進 47 列的
 * 操作欄只是重複入口＋橫向溢出的來源，故 2026-07-30 收斂掉（`emit('load', …)` 一併移除，
 * 「取」這個動作改由頁面頂部的軌別 radio + 版本 select 承擔）。
 *
 * 為什麼「升為正式版」在草稿列的操作欄、而不是編輯器上方：升版是上線動作，要升的必須是
 * **已存檔、可被 diff 與回查的那一份草稿**，不是編輯器裡可能還沒存的內容（頁面頂部另有一顆
 * 快捷「升為正式版」，走同一套確認流程，見 `promoteTarget` prop）。
 *
 * 表格用公共元件 `TableLayout`（`full-height` + `with-all` 分頁 preset，預設 50 條/頁）：
 * 它已是「抽屜內滿高滾動 + 完整分頁器」的 canonical 實作，這裡是單一主列表（47 列、要翻頁），
 * 不屬於「pagination=false 的輕量對照表」那個例外。
 */
import { type PromptDraftMeta, type PromptReleaseMeta } from '@/api';
import { TableLayout } from '@/components';
import { usePromptRelease } from '../composables';
import { PAGINATION_WITH_ALL } from '@/constants/table.constant';
import { computed, defineAsyncComponent, ref, watch } from 'vue';

// 對比抽屜點開才載（內含 diff 演算法與全文，非首屏必需）
const PromptVersionDiffDrawer = defineAsyncComponent(() => import('./PromptVersionDiffDrawer.vue'));

const props = defineProps<{
  visible: boolean;
  drafts: PromptDraftMeta[];
  releases: PromptReleaseMeta[];
  activeRelease: string;
  /** 開啟時直接對這支草稿彈出升版確認（來自頁面頂部的「升為正式版」）；空＝只是單純開列表。 */
  promoteTarget?: string;
}>();
const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void;
  /** 升版或回退成功：外層需重載 defaults（線上口徑已變）。 */
  (e: 'promoted', name: string): void;
  /** promoteTarget 已消化（確認框已彈出），請外層清掉，免得下次開抽屜又彈一次。 */
  (e: 'promoteTargetConsumed'): void;
}>();

/**
 * 升版／回退動作組（共用）：名稱建議、撞名檢查、必填理由、確認流程都在 composable 裡，
 * 本抽屜只負責版面與「哪一列按了什麼」。同一組邏輯另外兩個消費端是調試台頁面（`promotedDrafts`）
 * 與流水線步驟④的就地升版。
 */
const release = usePromptRelease({
  releases: () => props.releases,
  onDone: (name) => emit('promoted', name),
});
const { canManage, promotedDrafts, activating } = release;

// ── 併表：兩軌合成單一列表 ────────────────────────────────────────────────────

interface VersionRow {
  /** TableLayout 的 data 契約是 `Record<string, unknown>[]`，故需 index signature。 */
  [field: string]: unknown;
  key: string;
  kind: 'draft' | 'release';
  name: string;
  note: string;
  author: string;
  /** 顯示用時間（草稿＝存檔時間；正式版＝升版時間）。 */
  at: string;
  /** 正式版才有：升版來源草稿。 */
  sourceDraft: string;
  isActive: boolean;
}

const filter = ref<'all' | 'draft' | 'release'>('all');

/**
 * 分頁：沿用全域 `PAGINATION_WITH_ALL`（完整分頁器＋「全部」選項），只把預設每頁改為 50。
 * 版本數以「數十」為常態（現 47），20 條要翻三頁才看完。
 */
const listPagination = { ...PAGINATION_WITH_ALL, pageSize: 50 };

const allRows = computed<VersionRow[]>(() => [
  ...props.releases.map((r) => ({
    key: `release:${r.name}`,
    kind: 'release' as const,
    name: r.name,
    note: r.note,
    author: r.author,
    at: r.promoted_at,
    sourceDraft: r.source_draft,
    isActive: r.is_active,
  })),
  ...props.drafts.map((d) => ({
    key: `draft:${d.version}`,
    kind: 'draft' as const,
    name: d.version,
    note: d.note,
    author: d.author,
    at: d.saved_at,
    sourceDraft: '',
    isActive: false,
  })),
]);

const rows = computed(() =>
  filter.value === 'all' ? allRows.value : allRows.value.filter((r) => r.kind === filter.value),
);

// ── 對比：A / B 兩個槽，跨軌可選 ───────────────────────────────────────────────

const diffVisible = ref(false);
/** 打開對比抽屜時的初始兩版；抽屜內可自行換版，故這裡只是起點。 */
const diffA = ref('');
const diffB = ref('');

/** 預設基準＝線上正式版（沒有就退最新草稿）——「跟線上差在哪」是最常見的問法。 */
const defaultBase = computed(() => {
  const active = allRows.value.find((r) => r.isActive) ?? allRows.value[0];
  return active ? active.key : '';
});

/**
 * 打開對比抽屜。
 * @param against 指定對照版（某列的「對比」鈕帶入該列）；省略＝拿最新草稿當對照。
 */
function openDiff(against?: VersionRow): void {
  const other = against ?? allRows.value.find((r) => r.kind === 'draft') ?? allRows.value[0];
  diffB.value = other ? other.key : '';
  // 對照與基準撞同一版時，基準改取「另一個不同的版」，免得一開就是兩側相同
  diffA.value =
    defaultBase.value && defaultBase.value !== diffB.value
      ? defaultBase.value
      : (allRows.value.find((r) => r.key !== diffB.value)?.key ?? '');
  diffVisible.value = true;
}

// ── 升版（草稿 → 新正式版）────────────────────────────────────────────────────

/**
 * 外層帶 promoteTarget 進來（頁面頂部按了升版）→ 開抽屜即彈確認，不必再找那一列。
 *
 * 這段刻意**不進 composable**：它是本抽屜特有的「外部預觸發」機制，依賴 `visible` 與
 * `promoteTarget` 兩個 prop，其他消費端沒有這個概念。
 */
watch(
  () => [props.visible, props.promoteTarget] as const,
  ([open, target]) => {
    if (!open || !target) return;
    release.openPromote(target);
    emit('promoteTargetConsumed');
  },
  { immediate: true },
);

</script>

<template>
  <a-drawer
    :visible="visible"
    :width="1040"
    title="版本列表（草稿 / 正式版）"
    :footer="false"
    unmount-on-close
    :body-style="{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }"
    @cancel="emit('update:visible', false)"
  >
    <!-- 對比列：A/B 各自可來自任一軌（併表後跨軌對比天然成立） -->
    <a-row :gutter="[8, 8]" align="center" wrap class="mb-3">
      <a-col flex="none">
        <a-radio-group v-model="filter" type="button" size="small">
          <a-radio value="all">全部（{{ allRows.length }}）</a-radio>
          <a-radio value="draft">只看草稿（{{ drafts.length }}）</a-radio>
          <a-radio value="release">只看正式（{{ releases.length }}）</a-radio>
        </a-radio-group>
      </a-col>
      <a-col flex="auto" />
      <a-col flex="none">
        <span class="text-xs text-[#86909c]">兩版全文差異在對比抽屜內選擇與切換</span>
      </a-col>
      <a-col flex="none">
        <a-button type="primary" size="small" :disabled="allRows.length < 2" @click="openDiff()"
          >版本對比</a-button
        >
      </a-col>
    </a-row>

    <!-- flex-1 min-h-0 界定「滿高」範圍：TableLayout 的 full-height 只給自己 h-full，
         直接置於 flex column 下會以內容高為準而在下方留白 -->
    <div class="min-h-0 flex-1">
      <TableLayout
        full-height
        :data="rows"
        :pagination="listPagination"
        row-key="key"
        empty-text="尚無任何草稿或正式版"
      >
        <template #columns>
          <a-table-column title="類型" :width="76">
            <template #cell="{ record }">
              <a-tag :color="record.kind === 'release' ? 'green' : 'arcoblue'" size="small">
                {{ record.kind === 'release' ? '正式' : '草稿' }}
              </a-tag>
            </template>
          </a-table-column>
          <a-table-column title="版本" :width="180">
            <template #cell="{ record }">
              <span class="font-medium">{{ record.name }}</span>
              <a-tag v-if="record.isActive" color="green" size="small" class="ml-1">使用中</a-tag>
            </template>
          </a-table-column>
          <a-table-column title="備註 / 上線理由" data-index="note" ellipsis tooltip />
          <a-table-column title="來源草稿" data-index="sourceDraft" :width="150" ellipsis tooltip />
          <a-table-column title="操作人" data-index="author" :width="140" ellipsis tooltip />
          <a-table-column title="時間" data-index="at" :width="150" ellipsis tooltip />
          <a-table-column title="操作" :width="128" align="right">
            <template #cell="{ record }">
              <!--
              只放「沒有其他入口」的動作：對比可在對比抽屜內自選兩版、載入某版可用頁面頂部的
              版本下拉，兩者放進每一列只是重複入口＋47 列的視覺噪音（也是橫向溢出的來源）。
              per-row 一律 type="text"（frontend rule：列操作不用色塊分級）。
            -->
              <a-tooltip
                v-if="record.kind === 'draft' && promotedDrafts.has(record.name)"
                content="此草稿已升版過"
              >
                <a-button type="text" size="mini" disabled>已升版</a-button>
              </a-tooltip>
              <a-button
                v-else-if="record.kind === 'draft'"
                type="text"
                size="mini"
                :disabled="!canManage"
                @click="release.openPromote(record.name)"
                >升為正式版</a-button
              >
              <a-button
                v-else-if="!record.isActive"
                type="text"
                size="mini"
                :disabled="!canManage"
                :loading="activating === record.name"
                @click="release.activate(record.name)"
                >設為使用中</a-button
              >
              <span v-else class="text-xs text-[#c9cdd4]">—</span>
            </template>
          </a-table-column>
        </template>
      </TableLayout>
    </div>

    <!-- 升版確認：影響線上口徑，故用表單式 modal（不是 popconfirm） -->
    <a-modal
      v-model:visible="release.promoteVisible.value"
      title="升為正式版"
      :ok-loading="release.promoting.value"
      :ok-button-props="{ disabled: !release.canConfirmPromote.value }"
      ok-text="設為正式版"
      cancel-text="取消"
      @ok="release.confirmPromote"
    >
      <a-form :model="{ releaseName: release.releaseName.value, releaseNote: release.releaseNote.value }" layout="vertical">
        <a-form-item label="來源草稿">
          <span class="font-medium">{{ release.sourceDraft.value }}</span>
        </a-form-item>
        <a-form-item
          label="正式版名稱"
          :validate-status="!release.nameValid.value || release.nameTaken.value ? 'error' : undefined"
          :help="
            release.nameTaken.value
              ? '此名稱已存在（正式版不覆寫，請換名）'
              : !release.nameValid.value
                ? '僅允許英數與 . _ -，首字元須為英數'
                : ''
          "
        >
          <a-input v-model="release.releaseName.value" allow-clear />
        </a-form-item>
        <a-form-item
          label="上線理由（必填）"
          :validate-status="release.noteValid.value ? undefined : 'error'"
          :help="release.noteValid.value ? '' : '請寫明這版為何上線，供日後回查'"
        >
          <a-textarea v-model="release.releaseNote.value" :auto-size="{ minRows: 2, maxRows: 4 }" />
        </a-form-item>
      </a-form>
      <div class="text-xs text-[#86909c]">
        確認後 <b>{{ release.releaseName.value || '—' }}</b> 立即成為線上唯一口徑（前一版
        {{ activeRelease || '—' }}）。建議先在對比中確認差異，再升版。
      </div>
    </a-modal>

    <PromptVersionDiffDrawer
      v-model:visible="diffVisible"
      :drafts="drafts"
      :releases="releases"
      :initial-a="diffA"
      :initial-b="diffB"
    />
  </a-drawer>
</template>
