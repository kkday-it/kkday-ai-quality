<script setup lang="ts">
/**
 * 回歸重跑面板（案例庫抽屜的第三分頁）：拿勾選的案例重跑候選 Prompt，逐欄比對「該修好的修好沒、
 * 該不動的動了沒」。
 *
 * 這一步是改 Prompt 的安全網。沒有它，AI 改寫只能證明「被餵進去的那條修好了」，證明不了沒把別的
 * 弄壞——而過度矯正真的會發生（CHANGELOG 2026-07-27-225310 那版初稿就是靠回歸攔下來的）。
 *
 * 模型刻意跟著「Prompt 調試台」功能區（＝實際跑批用的那顆），不用改寫用的旗艦模型：回歸要驗的是
 * 這份 Prompt 在線上模型上的表現，拿更強的模型跑會得到偏樂觀、對不上線上的結論。
 */
import { IconArrowLeft, IconPlayArrow } from '@arco-design/web-vue/es/icon';
import { computed, ref, watch } from 'vue';
import { LlmConfigSelect } from '@/components';
import { type PromptDraftMeta, type PromptReleaseMeta } from '@/api';
import type { usePromptRegression } from '../composables';
import { useLlmAreaConfig } from '@/composables';
import type { LlmOverrides } from '@/features/settings/types';
import { fmtPercent } from '@/utils';
// 相對路徑（非走 barrel）：同資料夾 cross-import，經 barrel 迴繞會把整包元件拉進來並觸發 circular dep。
import PromptVersionSelect from './PromptVersionSelect.vue';

const props = defineProps<{
  /** 回歸流程狀態（由抽屜持有並下傳；本面板只讀，跑不跑由 `run` 事件交回抽屜決定）。 */
  regression: ReturnType<typeof usePromptRegression>;
  /** 頁面上現行的 Prompt 全文。 */
  baselinePrompt: string;
  /** 套用補丁後的候選全文；空＝還沒在②套用過。 */
  candidatePrompt: string;
  /** 案例庫勾選的 id。 */
  reviewIds: number[];
  /** 草稿/正式版清單（與頁面「版本列表」同一份資料源）——回歸基準不限於「現行編輯器內容」，
   * 也可挑歷史任一版驗證（如「這版跟三天前那版比，回歸有沒有更好」）。 */
  drafts: PromptDraftMeta[];
  releases: PromptReleaseMeta[];
  /** 這份結果是否已對不上當前的候選版／案例集（抽屜判定）。 */
  stale: boolean;
}>();

const emit = defineEmits<{
  /** 請抽屜執行回歸——它要先記下「驗的是哪份全文、哪組案例」才能做失效判定。 */
  (e: 'run', targetPrompt: string, overrides: LlmOverrides): void;
  /** 回②調整補丁（改壞時的出口）。 */
  (e: 'backToPatches'): void;
  /** 改用「跑批」拉真實資料複驗。 */
  (e: 'requestBatch'): void;
}>();

const llm = useLlmAreaConfig('prompt_debug');

/** 要驗哪一份：有候選（套過補丁）時預設驗候選，否則只能驗現行；「選擇版本」驗任一歷史版。 */
const target = ref<'candidate' | 'baseline' | 'version'>('baseline');
watch(
  () => props.candidatePrompt,
  (value) => {
    if (value) target.value = 'candidate';
  },
  { immediate: true },
);

const selectedVersionKey = ref('');
const versionPromptText = ref('');
const loadingVersionText = ref(false);

const targetPrompt = computed(() => {
  if (target.value === 'version') return versionPromptText.value;
  return target.value === 'candidate' && props.candidatePrompt
    ? props.candidatePrompt
    : props.baselinePrompt;
});
const canRun = computed(
  () =>
    !props.regression.running.value &&
    props.reviewIds.length > 0 &&
    !!llm.overrides.value.model &&
    !!targetPrompt.value.trim(),
);
const snap = computed(() => props.regression.snapshot.value);
const percent = computed(() =>
  snap.value && snap.value.total ? snap.value.processed / snap.value.total : 0,
);
const progressStatus = computed(() => {
  if (snap.value?.status === 'error') return 'danger' as const;
  return snap.value?.status === 'done' ? ('success' as const) : ('normal' as const);
});

/** 值的顯示字串（與案例庫展開列同一套呈現，TRUE/FALSE 而非 Python 字面）。 */
function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE';
  if (Array.isArray(value)) return value.length ? value.join('、') : '[]（空）';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}
</script>

<template>
  <div class="flex flex-col gap-3">
    <section class="regression-card">
      <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div class="flex items-center gap-2">
          <span class="text-sm font-semibold text-[#1d2129]">回歸重跑</span>
          <a-tag v-if="reviewIds.length" color="arcoblue" size="small"
            >{{ reviewIds.length }} 則案例</a-tag
          >
          <a-tag v-else color="red" size="small">尚未勾選案例</a-tag>
        </div>
        <a-button
          type="primary"
          size="small"
          :loading="regression.running.value"
          :disabled="!canRun"
          @click="emit('run', targetPrompt, llm.overrides.value)"
        ><template #icon><icon-play-arrow /></template>
          開始回歸
        </a-button>
      </div>

      <p class="mb-3 text-[11px] leading-relaxed text-[#86909c]">
        逐欄比對兩件事：人標錯的欄有沒有修好、人標對的欄有沒有被改壞。人沒看過的欄不計分——拿 AI
        當時的判定當標準答案，等於把當時的錯誤當成正解，分數會虛高。
      </p>

      <div class="mb-3 flex flex-col gap-1">
        <a-radio-group v-model="target" type="button" size="small">
          <a-radio value="baseline"
            >現行 Prompt（{{ baselinePrompt.length.toLocaleString() }} 字元）</a-radio
          >
          <a-radio value="candidate" :disabled="!candidatePrompt">
            套用補丁後（{{ candidatePrompt ? candidatePrompt.length.toLocaleString() : '—' }} 字元）
          </a-radio>
          <a-radio value="version">選擇版本</a-radio>
        </a-radio-group>
        <span v-if="!candidatePrompt" class="text-[11px] text-[#86909c]">
          回②套用補丁後，這裡才會出現「套用補丁後」的候選版可驗
        </span>
        <span v-else-if="target !== 'candidate'" class="text-[11px] text-[#ff7d00]">
          注意：驗的不是本次候選版——④的定案發布只認「驗過候選版」的結果，這輪跑完仍會鎖著
        </span>
        <PromptVersionSelect
          v-if="target === 'version'"
          v-model="selectedVersionKey"
          v-model:text="versionPromptText"
          v-model:loading="loadingVersionText"
          class="mt-1"
          :drafts="drafts"
          :releases="releases"
          placeholder="選擇要驗證的版本（草稿或正式版）"
        />
      </div>

      <div class="mt-3 text-xs text-[#86909c]">模型配置</div>
      <LlmConfigSelect
        v-model="llm.configId.value"
        class="mt-1"
        :configs="llm.configs.value"
        :provider-has-token="llm.providerHasToken.value"
      />
    </section>

    <a-alert v-if="stale" type="warning">
      候選版或案例勾選已變更，這份回歸結果已失效——④的定案發布已鎖回去，請重新跑一次驗證。
    </a-alert>
    <a-alert v-if="regression.errorMessage.value" type="error">
      {{ regression.errorMessage.value }}
    </a-alert>

    <!-- 進度與彙總 -->
    <section v-if="snap" class="regression-card">
      <div class="mb-2 flex items-center justify-between gap-2">
        <span class="text-xs text-[#4e5969]">
          {{ snap.processed }} / {{ snap.total }} 則 · {{ snap.model }}
        </span>
        <span class="text-xs text-[#86909c]">
          US$ {{ snap.cost_usd.toFixed(6) }} · {{ snap.total_tokens.toLocaleString() }} tokens
        </span>
      </div>
      <a-progress :percent="percent" :status="progressStatus" size="small">
        <template #text="{ percent: p }">{{ fmtPercent(p) }}</template>
      </a-progress>

      <div
        v-if="snap.status !== 'running'"
        class="mt-3 grid grid-cols-4 gap-2"
        :class="{ 'opacity-40': stale }"
      >
        <div class="score-tile score-tile--good">
          <div class="score-num">{{ snap.fixed }}</div>
          <div class="score-label">修好的欄</div>
        </div>
        <div class="score-tile score-tile--bad">
          <div class="score-num">{{ snap.broken }}</div>
          <div class="score-label">改壞的欄</div>
        </div>
        <div class="score-tile">
          <div class="score-num">{{ snap.still_wrong }}</div>
          <div class="score-label">還是不對</div>
        </div>
        <div class="score-tile">
          <div class="score-num">{{ snap.held }}</div>
          <div class="score-label">守住的欄</div>
        </div>
      </div>

      <a-alert v-if="snap.failed" type="warning" class="mt-2">
        {{ snap.failed }} 則案例重跑失敗（未計入上方分數），詳見下方清單。
      </a-alert>
      <a-alert
        v-else-if="!stale && snap.status === 'done' && regression.hasRegression.value"
        type="error"
        class="mt-2"
      >
        有 {{ snap.broken }} 個原本判對的欄被改壞了——這版不該直接上線。
        <!-- 改造前這裡只有一句「回 AI 改寫取消掉相關補丁」的純文字，照做還得自己找回去 -->
        <template #action>
          <a-button size="mini" status="danger" @click="emit('backToPatches')"><template #icon><icon-arrow-left /></template>
            回上一步調整補丁
          </a-button>
        </template>
      </a-alert>
      <a-alert v-else-if="!stale && snap.status === 'done' && snap.fixed" type="success" class="mt-2">
        修好 {{ snap.fixed }} 欄、零改壞。仍建議在正式上線前拉一批真實資料複驗。
        <template #action>
          <a-button size="mini" @click="emit('requestBatch')"><template #icon><icon-play-arrow /></template>改用跑批複驗</a-button>
        </template>
      </a-alert>
    </section>

    <!-- 逐案例明細 -->
    <section v-if="snap?.cases.length" class="regression-card" :class="{ 'opacity-40': stale }">
      <div class="mb-2 text-xs font-semibold text-[#1d2129]">逐案例明細</div>
      <div class="flex flex-col gap-2">
        <div
          v-for="row in snap.cases"
          :key="row.review_id"
          class="case-row"
          :class="{ 'case-row--bad': row.broken.length || !row.ok }"
        >
          <div class="mb-1 flex flex-wrap items-center gap-2">
            <a-tag size="small">#{{ row.review_id }}</a-tag>
            <a-tag v-if="!row.ok" color="red" size="small">重跑失敗</a-tag>
            <a-tag v-else-if="row.broken.length" color="red" size="small"
              >改壞 {{ row.broken.length }}</a-tag
            >
            <a-tag v-else-if="row.still_wrong.length" color="orange" size="small"
              >未修好 {{ row.still_wrong.length }}</a-tag
            >
            <a-tag v-else-if="row.fixed.length" color="green" size="small"
              >修好 {{ row.fixed.length }}</a-tag
            >
            <a-tag v-else color="gray" size="small">無變化</a-tag>
            <span class="truncate text-xs text-[#86909c]">{{ row.preview }}</span>
          </div>

          <div v-if="row.error" class="text-xs text-[#f53f3f]">{{ row.error }}</div>

          <div v-for="item in row.broken" :key="`b-${item.field}`" class="delta delta--bad">
            <span class="delta-field">{{ item.field }}</span>
            改壞：{{ display(item.expected) }} → <b>{{ display(item.actual) }}</b>
          </div>
          <div v-for="item in row.still_wrong" :key="`s-${item.field}`" class="delta delta--warn">
            <span class="delta-field">{{ item.field }}</span>
            還是不對：應為 {{ display(item.expected) }}，實得 <b>{{ display(item.actual) }}</b>
          </div>
          <div v-for="item in row.fixed" :key="`f-${item.field}`" class="delta delta--good">
            <span class="delta-field">{{ item.field }}</span>
            已修好 → {{ display(item.actual) }}
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.regression-card {
  border: 1px solid #e5e6eb;
  border-radius: 10px;
  background: #fff;
  padding: 12px 14px;
}
.score-tile {
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  background: #fafafa;
  padding: 8px 10px;
  text-align: center;
}
.score-tile--good {
  border-color: #aff0b5;
  background: #e8ffea;
}
.score-tile--bad {
  border-color: #fdcdc5;
  background: #fff1f0;
}
.score-num {
  color: #1d2129;
  font-size: 20px;
  font-weight: 600;
  line-height: 1.2;
}
.score-label {
  margin-top: 2px;
  color: #86909c;
  font-size: 11px;
}
.case-row {
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  background: #fafafa;
  padding: 8px 10px;
}
.case-row--bad {
  border-left: 3px solid rgb(var(--danger-6));
  background: #fff;
}
.delta {
  margin-top: 2px;
  overflow-wrap: anywhere;
  font-size: 11px;
  line-height: 1.6;
}
.delta-field {
  display: inline-block;
  min-width: 150px;
  color: #4e5969;
  font-weight: 600;
}
.delta--bad {
  color: #f53f3f;
}
.delta--warn {
  color: #ff7d00;
}
.delta--good {
  color: #00b42a;
}
</style>
