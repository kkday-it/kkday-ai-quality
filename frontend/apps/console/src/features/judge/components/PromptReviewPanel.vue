<script setup lang="ts">
/**
 * 人工評判區塊：把 AI 判定的每個欄位逐欄標「對／錯」，標錯就地填正解，最後連同整體修改建議
 * 存進案例庫（`prompt_debug_reviews`）。存下來的案例有兩個下游——餵給 AI 做定點改寫的證據，
 * 以及改完 Prompt 後的回歸重跑素材。
 *
 * 填正解的控件不寫死，一律由後端 `output_schema` 反推（見 `reviewControl.util`）：受控 enum
 * 會隨分類 SSOT 演進，前端手抄一份必然 drift。
 */
import { computed, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { createPromptDebugReview } from '@/api';
import { controlForField, defaultCorrection, type ReviewControl } from '../utils';

const props = defineProps<{
  /** 要評判的欄位（key/label/hint + AI 判的值）；來源＝呼叫端依 output_fields 過濾後的結果卡。 */
  fields: Array<{ key: string; label: string; hint: string; value: unknown }>;
  /** 後端 output_schema 全文，用來推導每欄填正解的控件與值域。 */
  schema?: Record<string, unknown>;
  /** AI 判定全文（存進案例的 ai_output；與 fields 的差別是它不受顯示過濾影響）。 */
  aiOutput: Record<string, unknown>;
  /** 當時的調試文本原文。 */
  conversation: string;
  /** 當時的線上 Prompt 版本；空＝臨時編輯過。 */
  promptVersion: string;
  /** 當時用的模型。 */
  model: string;
  /** 跑判中等情境下鎖住整區。 */
  disabled?: boolean;
}>();

const emit = defineEmits<{
  /** 案例存檔成功（父層可用來刷新案例數／提示開改寫抽屜）。 */
  (e: 'saved', id: number): void;
}>();

/** 逐欄評判結果；未出現在此表＝還沒看。 */
const verdicts = ref<Record<string, 'ok' | 'bad'>>({});
// 逐欄正解（只有標 bad 的欄會有值）。值型別按該欄 schema 而異（字串／布林／數字／字串陣列），
// 而各 Arco 控件的 v-model 各自只收自己那種——用 unknown 或聯集型別都無法同時滿足六種控件，
// 故此處刻意放寬為 any，型別正確性改由 controlForField / defaultCorrection 保證（有單測覆蓋）。
const corrections = ref<Record<string, any>>({});
const comment = ref('');
const saving = ref(false);

/** 每欄的控件（schema 不變就不用重算）。 */
const controls = computed<Record<string, ReviewControl>>(() =>
  Object.fromEntries(props.fields.map((f) => [f.key, controlForField(props.schema, f.key)])),
);

/** 模板用的扁平控件描述：值域全部攤成具名欄位，模板才不必寫型別斷言（vue-tsc 解析不了）。 */
interface ControlView {
  kind: ReviewControl['kind'];
  /** select 的選項。 */
  options: string[];
  /** radio 的檔位。 */
  steps: number[];
  maxItems?: number;
  itemMin?: number;
  itemMax?: number;
  min?: number;
  max?: number;
}

const controlViews = computed<Record<string, ControlView>>(() =>
  Object.fromEntries(
    Object.entries(controls.value).map(([key, c]) => {
      const view: ControlView = { kind: c.kind, options: [], steps: [] };
      if (c.kind === 'select') view.options = c.options;
      if (c.kind === 'radio') view.steps = c.options;
      if (c.kind === 'tags') Object.assign(view, { maxItems: c.maxItems, itemMin: c.itemMin, itemMax: c.itemMax });
      if (c.kind === 'number') Object.assign(view, { min: c.min, max: c.max });
      return [key, view];
    }),
  ),
);

const reviewedCount = computed(() => Object.keys(verdicts.value).length);
const badKeys = computed(() => props.fields.filter((f) => verdicts.value[f.key] === 'bad'));
/** 明確標「對」的欄——回歸時當「不准變」的判準；沒標過的欄不計分，兩者語意不同不可混用。 */
const okKeys = computed(() => props.fields.filter((f) => verdicts.value[f.key] === 'ok'));
/** 標了錯卻沒填正解的欄（下拉留空、文字框空白）——存檔前擋下來，免得存出無效案例。 */
const missingKeys = computed(() =>
  badKeys.value.filter((f) => {
    const v = corrections.value[f.key];
    if (typeof v === 'string') return !v.trim();
    if (Array.isArray(v)) return v.length === 0;
    return v === undefined || v === null;
  }),
);
/** 什麼都沒標、也沒寫建議＝沒有評判內容可存。 */
const canSave = computed(
  () => !props.disabled && (reviewedCount.value > 0 || !!comment.value.trim()),
);

/** 換一次跑判就是換一個案例，舊的評判不能延用到新結果上。 */
watch(
  () => props.aiOutput,
  () => {
    verdicts.value = {};
    corrections.value = {};
    comment.value = '';
  },
);

/** a-radio-group 的 change 回呼型別是 unknown，在此收窄，模板才不必寫斷言。 */
function onVerdictChange(key: string, value: unknown): void {
  if (value === 'ok' || value === 'bad') setVerdict(key, value);
}

/** 標對／標錯；標錯時把正解預填成 AI 原值（多數誤判只錯一個維度，改一處比重打快）。 */
function setVerdict(key: string, next: 'ok' | 'bad'): void {
  // 再點一次同一個選項＝取消標記，回到「還沒看」
  if (verdicts.value[key] === next) {
    delete verdicts.value[key];
    delete corrections.value[key];
    return;
  }
  verdicts.value[key] = next;
  if (next === 'bad') {
    const field = props.fields.find((f) => f.key === key);
    corrections.value[key] = defaultCorrection(controls.value[key], field?.value);
  } else {
    delete corrections.value[key];
  }
}

/** 全部標對：多數跑判是拿來確認回歸沒壞，一鍵標完只挑出錯的那幾欄改，比逐欄點快得多。 */
function markAllOk(): void {
  verdicts.value = Object.fromEntries(props.fields.map((f) => [f.key, 'ok' as const]));
  corrections.value = {};
}

function clearAll(): void {
  verdicts.value = {};
  corrections.value = {};
}

async function save(): Promise<void> {
  if (!canSave.value || saving.value) return;
  if (missingKeys.value.length) {
    Message.warning(`這幾欄標了錯但沒填正解：${missingKeys.value.map((f) => f.label).join('、')}`);
    return;
  }
  saving.value = true;
  try {
    const { id } = await createPromptDebugReview({
      conversation: props.conversation,
      ai_output: props.aiOutput,
      corrections: Object.fromEntries(badKeys.value.map((f) => [f.key, corrections.value[f.key]])),
      confirmed: okKeys.value.map((f) => f.key),
      comment: comment.value.trim(),
      prompt_version: props.promptVersion,
      model: props.model,
    });
    Message.success(
      badKeys.value.length
        ? `已存為案例（標錯 ${badKeys.value.length} 欄）`
        : '已存為案例（全欄皆對，可當回歸正例）',
    );
    emit('saved', id);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : '存為案例失敗');
  } finally {
    saving.value = false;
  }
}

/** AI 原值的顯示字串（唯讀展示用；正解輸入走各自控件不經過這裡）。 */
function displayValue(value: unknown): string {
  if (value === null || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE';
  if (Array.isArray(value)) return value.length ? value.join('、') : '[]（空）';
  if (typeof value === 'object') return JSON.stringify(value, null, 2);
  return String(value);
}
</script>

<template>
  <div>
    <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
      <div class="flex items-center gap-2">
        <span class="text-xs font-semibold text-[#1d2129]">人工評判</span>
        <a-tag v-if="badKeys.length" color="red" size="small">標錯 {{ badKeys.length }} 欄</a-tag>
        <a-tag v-else-if="reviewedCount" color="green" size="small">全對</a-tag>
        <span class="text-xs text-[#86909c]">已看 {{ reviewedCount }}/{{ fields.length }}</span>
      </div>
      <a-space size="mini">
        <a-button size="mini" :disabled="disabled" @click="markAllOk">全部標對</a-button>
        <a-button size="mini" :disabled="disabled || !reviewedCount" @click="clearAll"
          >清除標記</a-button
        >
      </a-space>
    </div>

    <div class="result-grid">
      <div
        v-for="field in fields"
        :key="field.key"
        class="result-item"
        :class="{
          'result-item--ok': verdicts[field.key] === 'ok',
          'result-item--bad': verdicts[field.key] === 'bad',
        }"
      >
        <div class="flex items-start justify-between gap-2">
          <div class="min-w-0">
            <div class="result-key">{{ field.label }}</div>
            <div class="result-hint">{{ field.hint }}</div>
          </div>
          <a-radio-group
            :model-value="verdicts[field.key]"
            type="button"
            size="mini"
            :disabled="disabled"
            @change="onVerdictChange(field.key, $event)"
          >
            <a-radio value="ok">對</a-radio>
            <a-radio value="bad">錯</a-radio>
          </a-radio-group>
        </div>

        <div class="result-value">{{ displayValue(field.value) }}</div>

        <div v-if="verdicts[field.key] === 'bad'" class="mt-2 flex flex-col gap-1">
          <span class="text-[10px] font-medium text-[#f53f3f]">正解</span>
          <a-select
            v-if="controlViews[field.key].kind === 'select'"
            v-model="corrections[field.key]"
            size="small"
            allow-search
            placeholder="選擇正確的值"
            :options="controlViews[field.key].options"
          />
          <a-switch
            v-else-if="controlViews[field.key].kind === 'switch'"
            v-model="corrections[field.key]"
            size="small"
          />
          <a-radio-group
            v-else-if="controlViews[field.key].kind === 'radio'"
            v-model="corrections[field.key]"
            type="button"
            size="mini"
          >
            <a-radio v-for="step in controlViews[field.key].steps" :key="step" :value="step">{{
              step
            }}</a-radio>
          </a-radio-group>
          <template v-else-if="controlViews[field.key].kind === 'tags'">
            <a-input-tag
              v-model="corrections[field.key]"
              size="small"
              :max-tag-count="controlViews[field.key].maxItems"
              placeholder="輸入後 Enter 新增"
            />
            <span class="text-[10px] leading-snug text-[#86909c]">
              最多 {{ controlViews[field.key].maxItems ?? '—' }} 個、單項
              {{ controlViews[field.key].itemMin }}–{{ controlViews[field.key].itemMax }} 字
            </span>
          </template>
          <a-input-number
            v-else-if="controlViews[field.key].kind === 'number'"
            v-model="corrections[field.key]"
            size="small"
            :min="controlViews[field.key].min"
            :max="controlViews[field.key].max"
          />
          <a-textarea
            v-else
            v-model="corrections[field.key]"
            size="small"
            :auto-size="{ minRows: 2, maxRows: 4 }"
            placeholder="填正確的內容"
          />
        </div>
      </div>
    </div>

    <div class="mt-3 flex flex-col gap-2">
      <p class="m-0 text-[11px] leading-snug text-[#86909c]">
        標「對」不只是走個形式：回歸重跑時，標過對的欄一旦被改壞會被抓出來；沒標過的欄不計分。
        所以有把握的欄請按「全部標對」再挑錯的改，別整批留空。
      </p>
      <a-textarea
        v-model="comment"
        :disabled="disabled"
        :auto-size="{ minRows: 2, maxRows: 5 }"
        placeholder="修改建議（選填）：這題應該怎麼判？Prompt 哪句話把模型帶偏了？寫得越具體，AI 改寫時越不會亂動別的段落。"
      />
      <div class="flex items-center justify-between gap-2">
        <span class="text-[11px] leading-snug text-[#86909c]">
          存下來的案例可餵給 AI 定點改寫，也會進回歸重跑清單
        </span>
        <a-button type="primary" size="small" :loading="saving" :disabled="!canSave" @click="save">
          存為案例
        </a-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.result-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.result-item {
  min-width: 0;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  background: #fafafa;
  padding: 8px 10px;
}
/* 評判狀態用左側色條標示：整格換底色會蓋掉「AI 原值 vs 正解」的層次，只染邊界最不吵 */
.result-item--ok {
  border-left: 3px solid rgb(var(--green-6));
}
.result-item--bad {
  border-left: 3px solid rgb(var(--danger-6));
  background: #fff;
}
.result-key {
  color: #4e5969;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.4;
}
.result-hint {
  margin-top: 2px;
  color: #86909c;
  font-size: 10px;
  line-height: 1.4;
}
.result-value {
  margin-top: 3px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: #1d2129;
  font-size: 12px;
  font-weight: 500;
}
</style>
