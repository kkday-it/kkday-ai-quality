<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue';
import { Message } from '@arco-design/web-vue';
import { useRouter } from 'vue-router';
import {
  getPromptDebugDefaults,
  streamPromptDebug,
  type PromptDebugDefaults,
  type PromptDebugMeta,
  type PromptDebugResult,
  type PromptDebugUsage,
} from '@/api';
import { LlmConfigPicker, LlmKnobs } from '@/components';
import { useLlmAreaDefault } from '../composables/useLlmAreaDefault';

const router = useRouter();
const llm = useLlmAreaDefault('prompt_debug');

const defaults = ref<PromptDebugDefaults | null>(null);
const systemPrompt = ref('');
const inputText = ref('');
const loadingDefaults = ref(false);

const streaming = ref(false);
const rawOutput = ref('');
const result = ref<PromptDebugResult | null>(null);
const usage = ref<PromptDebugUsage | null>(null);
const meta = ref<PromptDebugMeta | null>(null);
const warnings = ref<string[]>([]);
const errorMessage = ref('');
const outputRef = ref<HTMLElement>();
let abortController: AbortController | null = null;

const canRun = computed(
  () =>
    !!llm.provider.value &&
    !!llm.knobs.model.trim() &&
    !!systemPrompt.value.trim() &&
    !!inputText.value.trim(),
);
const displayedResults = computed(() => {
  const parsed = result.value?.parsed;
  if (!parsed || !defaults.value) return [];
  return defaults.value.output_fields
    .filter((field) => Object.prototype.hasOwnProperty.call(parsed, field.key))
    .map((field) => ({ ...field, value: parsed[field.key] }));
});

async function loadDefaults(): Promise<void> {
  loadingDefaults.value = true;
  try {
    defaults.value = await getPromptDebugDefaults();
    systemPrompt.value = defaults.value.system_prompt;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : '載入預設 Prompt 失敗');
  } finally {
    loadingDefaults.value = false;
  }
}

onMounted(async () => {
  await Promise.all([loadDefaults(), llm.loadConfigs()]);
});

const samples = [
  {
    label: '憑證未送達',
    text: '[USER] 我後天就要出發，但仍沒有收到主辦單位寄出的電子票，垃圾郵件也找過了。\n[BOT] KKday 憑證已發送，但主辦單位電子票需等待寄送。\n[USER] 請幫我查還要等多久。',
  },
  {
    label: '修改日期受限',
    text: '[USER] 我訂錯日期，想把 8/12 改成 8/13。\n[BOT] 此商品規則不支援原訂單改期，只能取消後重新下單。\n[USER] 那請問要怎麼處理？',
  },
  {
    label: 'OOT 售前詢問',
    text: '[USER] 還沒下單，請問這個行程適合帶三歲小孩嗎？現場有兒童座椅嗎？\n[BOT] 請以商品頁與供應商回覆為準。',
  },
];

function resetPrompt(): void {
  if (defaults.value) systemPrompt.value = defaults.value.system_prompt;
}

function clearRun(): void {
  rawOutput.value = '';
  result.value = null;
  usage.value = null;
  meta.value = null;
  warnings.value = [];
  errorMessage.value = '';
}

async function run(): Promise<void> {
  if (!canRun.value || streaming.value) return;
  clearRun();
  streaming.value = true;
  abortController = new AbortController();
  try {
    await streamPromptDebug(
      {
        text: inputText.value,
        system_prompt: systemPrompt.value,
        overrides: llm.overrides.value,
      },
      {
        onMeta: (value) => (meta.value = value),
        onDelta: async (text) => {
          rawOutput.value += text;
          await nextTick();
          if (outputRef.value) outputRef.value.scrollTop = outputRef.value.scrollHeight;
        },
        onWarning: (message) => warnings.value.push(message),
        onResult: (value) => (result.value = value),
        onUsage: (value) => (usage.value = value),
        onError: (message) => (errorMessage.value = message),
      },
      abortController.signal,
    );
  } catch (error) {
    if ((error as Error).name !== 'AbortError') {
      errorMessage.value = error instanceof Error ? error.message : String(error);
    }
  } finally {
    streaming.value = false;
    abortController = null;
  }
}

function abort(): void {
  abortController?.abort();
}

async function saveAsDefault(): Promise<void> {
  try {
    await llm.saveAsDefault();
    Message.success('已存為本功能區默認（團隊共用）');
  } catch (error) {
    Message.error('儲存失敗：' + (error instanceof Error ? error.message : error));
  }
}

async function copyOutput(): Promise<void> {
  if (!rawOutput.value) return;
  await navigator.clipboard.writeText(rawOutput.value);
  Message.success('已複製 AI 輸出');
}

function openLlmSettings(): void {
  router.replace({ query: { ...router.currentRoute.value.query, settings: 'llm' } });
}

function displayValue(value: unknown): string {
  if (value === null || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE';
  if (typeof value === 'object') return JSON.stringify(value, null, 2);
  return String(value);
}
</script>

<template>
  <div class="flex min-h-full flex-col gap-4">
    <section class="rounded-xl border border-[#e5e6eb] bg-white px-5 py-4 shadow-sm">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div class="mb-1 flex items-center gap-2">
            <h1 class="text-lg font-semibold text-[#1d2129]">Prompt 調試台</h1>
            <a-tag color="arcoblue">售後根因分類</a-tag>
          </div>
          <p class="m-0 text-sm text-[#86909c]">
            任意貼入完整 IM session，使用可編輯 Prompt
            與臨時模型旋鈕，查看逐字串流、結構校驗與單次費用。
          </p>
        </div>
        <div v-if="defaults" class="flex flex-wrap gap-2 text-xs">
          <a-tag>{{ defaults.category_count }} 個受控分類</a-tag>
          <a-tag>{{ defaults.analyzed_rows.toLocaleString() }} 筆裁判資料</a-tag>
          <a-tag color="orange">OOT {{ (defaults.oot_rate * 100).toFixed(1) }}%</a-tag>
          <a-tag color="green">平均信心 {{ defaults.mean_confidence.toFixed(3) }}</a-tag>
        </div>
      </div>
      <div v-if="defaults" class="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[#4e5969]">
        <span>依據：</span>
        <a
          :href="defaults.sources.knowledge_document.url"
          target="_blank"
          rel="noreferrer"
          class="text-[#165dff]"
        >
          {{ defaults.sources.knowledge_document.title }}
        </a>
        <a
          :href="defaults.sources.judge_spreadsheet.url"
          target="_blank"
          rel="noreferrer"
          class="text-[#165dff]"
        >
          {{ defaults.sources.judge_spreadsheet.title }}
        </a>
        <a
          :href="defaults.sources.field_definitions_document.url"
          target="_blank"
          rel="noreferrer"
          class="text-[#165dff]"
        >
          {{ defaults.sources.field_definitions_document.title }}
        </a>
      </div>
    </section>

    <div class="debug-grid min-h-0 flex-1">
      <section class="debug-panel min-h-0">
        <div class="panel-head">
          <div>
            <div class="panel-title">System Prompt</div>
            <div class="panel-sub">
              已注入 Google Doc 的 {{ defaults?.category_count ?? '—' }} 類操作定義；可直接改寫做
              A/B 調試
            </div>
          </div>
          <a-button size="small" :disabled="loadingDefaults || streaming" @click="resetPrompt"
            >恢復預設</a-button
          >
        </div>
        <a-textarea
          v-model="systemPrompt"
          class="prompt-editor"
          :disabled="loadingDefaults || streaming"
          :auto-size="false"
          placeholder="載入 Prompt 中…"
        />
        <div class="panel-foot">
          {{ systemPrompt.length.toLocaleString() }} 字元 · 本次送出前可自由編輯，不會覆寫正式判決
          Prompt
        </div>
      </section>

      <section class="debug-panel min-h-0">
        <div class="panel-head">
          <div>
            <div class="panel-title">調試文本</div>
            <div class="panel-sub">請貼完整對話；模型會把其中的指令視為資料而非系統命令</div>
          </div>
          <a-button size="small" :disabled="streaming" @click="inputText = ''">清空</a-button>
        </div>
        <div class="mb-3 flex flex-wrap gap-2">
          <a-button
            v-for="sample in samples"
            :key="sample.label"
            size="mini"
            :disabled="streaming"
            @click="inputText = sample.text"
          >
            {{ sample.label }}
          </a-button>
        </div>
        <a-textarea
          v-model="inputText"
          class="input-editor"
          :disabled="streaming"
          :auto-size="false"
          placeholder="例如：\n[USER] 我仍未收到電子票…\n[BOT] …\n[USER] 請幫我查詢"
        />
        <div class="mt-3 flex items-center justify-between gap-3">
          <span class="text-xs text-[#86909c]">{{ inputText.length.toLocaleString() }} 字元</span>
          <a-space>
            <a-button v-if="streaming" status="danger" @click="abort">停止</a-button>
            <a-button v-else type="primary" size="large" :disabled="!canRun" @click="run">
              開始裁決
            </a-button>
          </a-space>
        </div>
      </section>

      <section class="flex min-h-0 flex-col gap-3">
        <div class="debug-panel flex-none">
          <div class="panel-head">
            <div>
              <div class="panel-title">本次 LLM 配置</div>
              <div class="panel-sub">跟隨「Prompt 調試台」功能區默認；下方調整只影響本次，不動全域默認</div>
            </div>
            <a-link @click="openLlmSettings">管理連線</a-link>
          </div>
          <a-alert v-if="!Object.keys(llm.providerHasToken.value).length" type="warning" class="mb-3">
            尚無可用 LLM 連線，請先至「設定 › LLM 連線」建立並保存 API Token。
          </a-alert>
          <LlmConfigPicker
            :model-value="llm.provider.value"
            :provider-has-token="llm.providerHasToken.value"
            @update:model-value="llm.setProvider"
          />
          <div class="mt-3">
            <LlmKnobs :model-value="llm.knobs" :provider="llm.provider.value" @update:model-value="llm.setKnobs" />
          </div>
          <div class="mt-2 flex justify-end">
            <a-button size="small" :disabled="streaming" @click="saveAsDefault">存為此區默認</a-button>
          </div>
        </div>

        <div class="debug-panel flex min-h-[360px] flex-1 flex-col">
          <div class="panel-head flex-none">
            <div>
              <div class="flex items-center gap-2">
                <div class="panel-title">AI 流式輸出</div>
                <a-tag v-if="streaming" color="arcoblue" size="small">生成中</a-tag>
                <a-tag v-else-if="result?.valid" color="green" size="small">Schema 通過</a-tag>
                <a-tag v-else-if="result" color="red" size="small">需修 Prompt</a-tag>
              </div>
              <div class="panel-sub">原始 JSON 逐 token 顯示；完成後再做欄位相依校驗</div>
            </div>
            <a-button size="small" :disabled="!rawOutput" @click="copyOutput">複製</a-button>
          </div>

          <a-alert v-if="errorMessage" type="error" class="mb-3">{{ errorMessage }}</a-alert>
          <a-alert v-for="message in warnings" :key="message" type="warning" class="mb-2">{{
            message
          }}</a-alert>

          <pre ref="outputRef" class="stream-output">{{
            rawOutput || '尚未執行。開始裁決後，這裡會逐字顯示模型輸出。'
          }}</pre>

          <div v-if="result" class="mt-3">
            <a-alert v-if="result.validation_issues.length" type="error" class="mb-3">
              <div class="font-medium">輸出契約未通過</div>
              <div v-for="issue in result.validation_issues" :key="issue" class="mt-1 text-xs">
                • {{ issue }}
              </div>
            </a-alert>
            <div v-if="displayedResults.length" class="result-grid">
              <div v-for="field in displayedResults" :key="field.key" class="result-item">
                <div class="result-key">{{ field.label }}</div>
                <div class="result-hint">{{ field.hint }}</div>
                <div class="result-value">{{ displayValue(field.value) }}</div>
              </div>
            </div>
          </div>

          <div v-if="usage" class="usage-card mt-3">
            <div class="flex items-center justify-between gap-3">
              <div>
                <div class="text-xs text-[#86909c]">本次估算費用</div>
                <div class="text-xl font-semibold text-[#1d2129]">
                  US$ {{ usage.cost_usd.toFixed(6) }}
                </div>
              </div>
              <div class="text-right text-xs text-[#4e5969]">
                <div>
                  {{ usage.total_tokens.toLocaleString() }} tokens ·
                  {{ (usage.latency_ms / 1000).toFixed(1) }}s
                </div>
                <div>
                  輸入 {{ usage.prompt_tokens.toLocaleString() }} / 輸出
                  {{ usage.completion_tokens.toLocaleString() }}
                </div>
                <div v-if="usage.cached_tokens || usage.reasoning_tokens">
                  快取 {{ usage.cached_tokens.toLocaleString() }} / 推理
                  {{ usage.reasoning_tokens.toLocaleString() }}
                </div>
              </div>
            </div>
            <div class="mt-2 text-[11px] text-[#86909c]">
              依目前模型單價與 API usage 估算，最終金額以供應商帳單為準。
            </div>
          </div>
          <div v-if="meta" class="mt-2 text-[11px] text-[#86909c]">
            {{ meta.model }} · {{ meta.provider }} · reasoning={{ meta.reasoning_effort }} ·
            temperature={{ meta.temperature ?? 'default' }}
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.debug-grid {
  display: grid;
  grid-template-columns: minmax(300px, 0.92fr) minmax(300px, 0.92fr) minmax(380px, 1.16fr);
  gap: 16px;
  align-items: stretch;
}
.debug-panel {
  border: 1px solid #e5e6eb;
  border-radius: 12px;
  background: #fff;
  padding: 16px;
  box-shadow: 0 2px 8px rgb(0 0 0 / 3%);
}
.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.panel-title {
  color: #1d2129;
  font-size: 14px;
  font-weight: 600;
}
.panel-sub,
.panel-foot {
  color: #86909c;
  font-size: 11px;
  line-height: 1.5;
}
.panel-foot {
  margin-top: 8px;
}
.prompt-editor,
.input-editor {
  width: 100%;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  line-height: 1.55;
}
.prompt-editor {
  height: calc(100vh - 300px);
  min-height: 520px;
}
.input-editor {
  height: calc(100vh - 354px);
  min-height: 460px;
}
.stream-output {
  min-height: 150px;
  max-height: 260px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  border: 1px solid #27272a;
  border-radius: 8px;
  background: #18181b;
  color: #d4d4d8;
  padding: 12px;
  font-size: 12px;
  line-height: 1.6;
}
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
.usage-card {
  border: 1px solid #bedaff;
  border-radius: 10px;
  background: #f2f7ff;
  padding: 12px;
}
@media (max-width: 1380px) {
  .debug-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .debug-grid > section:last-child {
    grid-column: 1 / -1;
  }
  .prompt-editor,
  .input-editor {
    height: 560px;
  }
}
@media (max-width: 880px) {
  .debug-grid {
    grid-template-columns: 1fr;
  }
  .debug-grid > section:last-child {
    grid-column: auto;
  }
}
</style>
