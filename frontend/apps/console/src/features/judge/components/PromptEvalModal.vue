<script setup lang="ts">
/**
 * 測試 Prompt 彈窗（Prompt-as-Source 調適閉環 UI，歸因列表工具列「測試 Prompt」唯一入口）：
 * 選一支 prompt、切換「真實列表」（現行判決，`filters` 給定時＝當前歸因列表篩選子集，B1）／
 * 「mock 列表」（B3 邊界測試集，測全部啟用中 case）→ 指標卡（域：primary/命中/棄權/多報；
 * 極性：polarity/sentiment）+ 逐案分歧表（含診斷理由 reason，B0 overlay：命中取首條歸因理由、
 * 棄權取 abstain_reason）。消耗 LLM 額度。大樣本 / golden / A/B 比較走 CLI
 * scripts/tools/eval_prompt_single.py。
 *
 * 「管理測試集」開 `PromptTestcasesDrawer`（CSV 上傳/手動新增/CRUD），免離開此彈窗即可切
 * mock 模式重測；分歧表每案可「存為測試 case」（分歧一鍵入集，邊界集自然生長）。
 */
import { computed, onMounted, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import {
  evalPrompt,
  getPromptEvalRun,
  getTaxonomyCascade,
  listPromptEvalRuns,
  type CascadeNode,
  type PrejudgeBody,
  type PromptEvalResult,
  type PromptEvalRunSummary,
} from '@/api';
import { useJudgeRulesStore } from '@/stores/judgeRules.store';
import SaveTestcaseModal, { type TestcasePrefill } from './SaveTestcaseModal.vue';
import PromptTestcasesDrawer from './PromptTestcasesDrawer.vue';
import { fmtDt } from '../utils';

const props = defineProps<{
  /** 是否顯示。 */
  visible: boolean;
  /** B1：給定時「真實列表」樣本＝此篩選子集（PrejudgeBody 同形），取代 md5 全表抽樣。 */
  filters?: PrejudgeBody;
}>();

const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>();

const n = ref(8);
const loading = ref(false);
const result = ref<PromptEvalResult | null>(null);
/** 真實列表（production，樣本＝filters 篩選子集或 md5 全表）／mock 列表（B3 邊界測試集）。 */
const testSource = ref<'production' | 'mock'>('production');
const isMock = computed(() => testSource.value === 'mock');

// 從 judgeRules store 取 7 支 prompt 的 rule_code + 中文名（SSOT，免另存一份標籤）。
const store = useJudgeRulesStore();
const selectedCode = ref('');
onMounted(async () => {
  if (!store.metas.length) await store.loadList();
  if (!selectedCode.value) {
    selectedCode.value =
      store.metas.find((m) => m.rule_code.startsWith('prompt_'))?.rule_code || 'prompt_polarity';
  }
  cascadeOpts.value = await getTaxonomyCascade();
});
const promptOptions = computed(() =>
  store.metas
    .filter((m) => m.rule_code.startsWith('prompt_'))
    .map((m) => ({ value: m.rule_code, label: store.labelFor(m.rule_code) }))
    .sort((a, b) =>
      a.value === 'prompt_polarity'
        ? -1
        : b.value === 'prompt_polarity'
          ? 1
          : a.value.localeCompare(b.value),
    ),
);

/** rule_code → 端點 prompt 參數（prompt_C-3 → C-3、prompt_polarity → polarity）。 */
const promptArg = computed(() => selectedCode.value.replace('prompt_', ''));
const isPolarity = computed(() => promptArg.value === 'polarity');

// 「存為測試 case」域機器值猜測：以中文 label 對照級聯樹 L1（同一份 SSOT，免另存 C-N→域機器值表）。
const cascadeOpts = ref<CascadeNode[]>([]);
const domainMachineForCurrent = computed(() => {
  if (isPolarity.value) return '';
  const label = store.labelFor(selectedCode.value);
  return cascadeOpts.value.find((n) => n.label === label)?.value ?? '';
});

/** 指標卡定義（依 prompt 別）：{label, value(0~1 或 null)}。 */
const metrics = computed(() => {
  const r = result.value;
  if (!r) return [];
  if (isPolarity.value) {
    return [
      { label: 'polarity 一致率', value: r.polarity_match_rate },
      { label: 'sentiment 一致率', value: r.sentiment_match_rate },
    ];
  }
  return [
    { label: 'primary 一致率', value: r.primary_match_rate },
    { label: '命中率', value: r.hit_rate },
    { label: '棄權正確率', value: r.abstain_correct_rate },
    { label: '多報率', value: r.over_report_rate },
  ];
});

/** 0~1 → 百分比字串；null → 「—」（無分母）。 */
const pct = (v?: number | null): string => (v == null ? '—' : `${Math.round(v * 100)}%`);

const mismatchColumns = [
  { title: 'id', dataIndex: 'id', width: 110, ellipsis: true, tooltip: true },
  { title: '參照', slotName: 'ref', width: 150 },
  { title: '本支', slotName: 'pack', width: 150 },
  { title: '理由', dataIndex: 'reason', width: 200, ellipsis: true, tooltip: true },
  { title: '評論', dataIndex: 'text', ellipsis: true, tooltip: true },
  { title: '', slotName: 'actions', width: 90 },
];

/** 陣列/值 → 顯示字串（歸因 code 陣列或極性字串）。 */
const fmt = (v: unknown): string =>
  Array.isArray(v) ? v.join('、') || '棄權' : String(v ?? '棄權');

async function run() {
  loading.value = true;
  result.value = null;
  try {
    result.value = isMock.value
      ? await evalPrompt(promptArg.value, n.value, undefined, 'mock')
      : await evalPrompt(promptArg.value, n.value, props.filters);
    await loadHistory(); // 本次測試已落歷史（後端同步 insert）→ 立即反映在歷史列表最上方
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '測試失敗');
  } finally {
    loading.value = false;
  }
}

// 「存為測試 case」（B3 分歧一鍵入集）：域 prompt 分歧才可存（testcase schema 要求 gold_l1）。
const saveOpen = ref(false);
const savePrefill = ref<TestcasePrefill | null>(null);
function openSaveTestcase(record: NonNullable<PromptEvalResult['mismatches']>[number]) {
  const ref_ = record.ref;
  const pack = record.pack;
  savePrefill.value = {
    text: record.text,
    goldL1: domainMachineForCurrent.value,
    goldL2:
      record.ref_primary ||
      (Array.isArray(ref_) ? ref_[0] : '') ||
      (Array.isArray(pack) ? pack[0] : '') ||
      '',
    note: `分歧案例（${promptArg.value}）：參照=${fmt(ref_)}／本支=${fmt(pack)}`,
  };
  saveOpen.value = true;
}

// 管理測試集（B3：CSV 上傳/手動新增/CRUD）——免離開此彈窗即可切 mock 模式重測。
const testcasesOpen = ref(false);

// ── 測試歷史（B2）：對「當前選中這支 prompt」查歷次測試結果，供改 prompt 前後對比 ──
const history = ref<PromptEvalRunSummary[]>([]);
const historyLoading = ref(false);
async function loadHistory() {
  if (!promptArg.value) return;
  historyLoading.value = true;
  try {
    const r = await listPromptEvalRuns(promptArg.value);
    history.value = r.items;
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '載入測試歷史失敗');
  } finally {
    historyLoading.value = false;
  }
}
/** 查看某次歷史測試：metrics JSONB 本身已是完整 PromptEvalResult 形狀（少 mismatches），
 * 併回 mismatches 即可直接複用現有指標卡/分歧表渲染，不必另寫一套歷史檢視 UI。 */
async function viewHistoryRun(runId: string) {
  try {
    const detail = await getPromptEvalRun(runId);
    result.value = {
      ...(detail.metrics as unknown as PromptEvalResult),
      mismatches: detail.mismatches as PromptEvalResult['mismatches'],
    };
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '載入測試紀錄失敗');
  }
}

// 開啟時清結果（下次開重測，避免看到殘留）+ 載入歷史。
watch(
  () => props.visible,
  (v) => {
    if (v) loadHistory();
    if (!v) result.value = null;
  },
);
// 切換選中 prompt → 重載該支的測試歷史。
watch(promptArg, () => {
  if (props.visible) loadHistory();
});
</script>

<template>
  <a-modal
    :visible="visible"
    title="測試 Prompt"
    :width="720"
    :footer="false"
    @cancel="emit('update:visible', false)"
  >
    <!-- 選 prompt + 真實/mock 列表切換 -->
    <a-row :gutter="[12, 12]" align="center" wrap class="mb-3">
      <a-col :flex="'none'"><span class="text-xs text-[var(--color-text-3)]">Prompt</span></a-col>
      <a-col :flex="'160px'">
        <a-select v-model="selectedCode" size="small" class="w-full" :options="promptOptions" />
      </a-col>
      <a-col :flex="'none'">
        <a-radio-group v-model="testSource" type="button" size="small">
          <a-radio value="production">真實列表</a-radio>
          <a-radio value="mock">mock 列表</a-radio>
        </a-radio-group>
      </a-col>
      <a-col :flex="'auto'" />
      <a-col :flex="'none'">
        <a-button size="small" type="text" @click="testcasesOpen = true">管理測試集</a-button>
      </a-col>
    </a-row>

    <!-- 配置列：樣本數 + 執行（mock 模式：測全部啟用中測試 case，不需設樣本數） -->
    <div class="mb-3 flex items-center gap-3">
      <template v-if="!isMock">
        <span class="text-xs text-[var(--color-text-3)]">樣本數</span>
        <a-input-number v-model="n" :min="1" :max="30" size="small" class="w-24" />
      </template>
      <span class="text-[11px] text-[var(--color-text-3)]">
        <template v-if="isMock">樣本＝邊界測試集全部啟用中 case（消耗 LLM）</template>
        <template v-else-if="filters">樣本＝當前歸因列表篩選子集（消耗 LLM）</template>
        <template v-else>抽現行判決 N 則為參照，只跑這支 prompt（消耗 LLM）；大樣本用 CLI</template>
      </span>
      <div class="flex-1" />
      <a-button type="primary" size="small" :loading="loading" @click="run">執行測試</a-button>
    </div>

    <!-- 指標卡 -->
    <div v-if="result" class="mb-3 grid grid-cols-4 gap-2">
      <div
        v-for="m in metrics"
        :key="m.label"
        class="rounded-lg border p-2 text-center"
        :class="{ 'col-span-2': isPolarity }"
      >
        <div class="text-lg font-semibold text-[rgb(var(--primary-6))]">{{ pct(m.value) }}</div>
        <div class="text-[11px] text-[var(--color-text-3)]">{{ m.label }}</div>
      </div>
    </div>
    <div v-if="result" class="mb-2 text-xs text-[var(--color-text-3)]">
      樣本 {{ result.n }} 則{{ result.filtered ? '（篩選子集）' : '' }} · model={{ result.model }} ·
      分歧 {{ result.mismatches.length }} 則
    </div>

    <!-- 逐案分歧 -->
    <a-table
      v-if="result && result.mismatches.length"
      :data="result.mismatches"
      :columns="mismatchColumns"
      size="small"
      :pagination="{ pageSize: 8, size: 'mini' }"
      row-key="id"
      :scroll="{ y: 260 }"
    >
      <template #ref="{ record }">
        <span class="font-mono text-xs">{{ fmt(record.ref) }}</span>
      </template>
      <template #pack="{ record }">
        <span class="font-mono text-xs">{{ fmt(record.pack) }}</span>
      </template>
      <template #actions="{ record }">
        <a-button v-if="!isPolarity" type="text" size="mini" @click="openSaveTestcase(record)"
          >存為 case</a-button
        >
      </template>
    </a-table>
    <a-empty v-else-if="result" description="無分歧（本支與現行判決一致）" />
    <div v-else class="py-8 text-center text-[var(--color-text-3)]">
      {{ isMock ? '點「執行測試」' : '設定樣本數後點「執行測試」' }}
    </div>

    <!-- 測試歷史（B2）：這支 prompt 的歷次測試結果，供改 prompt 前後對比 -->
    <a-collapse class="mt-3" :bordered="false">
      <a-collapse-item key="history" :header="`測試歷史（${history.length} 筆）`">
        <a-spin v-if="historyLoading" class="block py-4 text-center" />
        <a-table
          v-else-if="history.length"
          :data="history"
          size="mini"
          :pagination="false"
          row-key="run_id"
          :scroll="{ y: 200 }"
        >
          <template #columns>
            <a-table-column title="時間" data-index="created_at" :width="150">
              <template #cell="{ record }">{{ fmtDt(record.created_at) }}</template>
            </a-table-column>
            <a-table-column title="樣本" data-index="n" :width="60" />
            <a-table-column title="來源" data-index="source" :width="80" />
            <a-table-column title="model" data-index="model" :width="120" ellipsis tooltip />
            <a-table-column title="觸發人" data-index="triggered_by" ellipsis tooltip />
            <a-table-column title="" :width="70">
              <template #cell="{ record }">
                <a-button type="text" size="mini" @click="viewHistoryRun(record.run_id)"
                  >查看</a-button
                >
              </template>
            </a-table-column>
          </template>
        </a-table>
        <a-empty v-else description="尚無測試紀錄" :image-size="32" />
      </a-collapse-item>
    </a-collapse>
  </a-modal>

  <!-- 存為測試 case（B3 分歧一鍵入集）：帶入分歧筆的文字/猜測 gold，使用者確認/修正後入 prompt_testcases -->
  <SaveTestcaseModal v-model:visible="saveOpen" :prefill="savePrefill" />

  <!-- 管理測試集（B3：CSV 上傳/手動新增/CRUD） -->
  <PromptTestcasesDrawer v-model:visible="testcasesOpen" />
</template>
