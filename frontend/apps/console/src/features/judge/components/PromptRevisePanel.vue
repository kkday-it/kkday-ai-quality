<script setup lang="ts">
/**
 * AI 定點改寫面板（案例庫抽屜的第二分頁）：勾好的案例 → 旗艦模型產補丁 → 逐條勾選 → 套用 →
 * 左右比對 → 存為新草稿（不改變線上口徑，要上線再到「版本列表」升版）。
 *
 * 為什麼是補丁不是整篇重寫、為什麼 anchor 要驗唯一：見 `backend/app/judge/prompt_reviser.py`
 * 的模組說明（簡言之，這份 Prompt 有 2–3 萬字的實測校準層，整篇重寫會被順手砍掉且 diff 沒人審得動）。
 *
 * 模型走獨立的 `prompt_revise` 功能區——裁決跑批要便宜，改 Prompt 要聰明，兩者不共用旋鈕。
 */
import { computed, ref, toRef } from 'vue';
import { Message } from '@arco-design/web-vue';
import { LlmConfigPicker, LlmKnobs, MdTextDiff } from '@/components';
import { usePromptRevise } from '../composables';
import { useLlmAreaDefault } from '../composables/useLlmAreaDefault';

const props = defineProps<{
  /** 要被改寫的現行 Prompt 全文（頁面上編輯中的那份）。 */
  systemPrompt: string;
  /** 案例庫勾選的 id。 */
  reviewIds: number[];
}>();

const emit = defineEmits<{
  /** 存出了新版本，父層需通知調試台重載最新版。 */
  (e: 'savedVersion'): void;
  /** 套用補丁產出了候選全文，父層轉給「回歸重跑」分頁當驗證標的（存版前就能先驗）。 */
  (e: 'applied', prompt: string): void;
}>();

const llm = useLlmAreaDefault('prompt_revise');
const revise = usePromptRevise({
  systemPrompt: toRef(props, 'systemPrompt'),
  reviewIds: toRef(props, 'reviewIds'),
});

/** 串流原始輸出預設收起：正常情況沒人要看 JSON，出問題時才展開追。 */
const rawVisible = ref(false);

const canRun = computed(
  () => !revise.streaming.value && props.reviewIds.length > 0 && !!llm.knobs.model,
);

const STATUS_META: Record<string, { color: string; label: string; hint: string }> = {
  matched: { color: 'green', label: '可套用', hint: '原文中唯一命中' },
  not_found: {
    color: 'red',
    label: '對不上',
    hint: '模型沒有逐字複製原文（常見於自行改了標點），無法安全定位，只能自己手動改',
  },
  ambiguous: {
    color: 'orange',
    label: '撞多處',
    hint: '這段文字在全文中出現多次，套用會改到不該改的地方；需要更長的片段才能定位',
  },
};

async function copyChangelog(): Promise<void> {
  const text = revise.result.value?.changelog;
  if (!text) return;
  await navigator.clipboard.writeText(text);
  Message.success('已複製 CHANGELOG 條目草稿');
}

/** 套用後把候選全文丟給父層，「回歸重跑」分頁才能在存版之前先驗一輪。 */
async function onApply(): Promise<void> {
  await revise.apply();
  if (revise.revisedPrompt.value) emit('applied', revise.revisedPrompt.value);
}

async function onSaveVersion(): Promise<void> {
  if (await revise.saveVersion()) emit('savedVersion');
}
</script>

<template>
  <div class="flex flex-col gap-3">
    <!-- 送出設定 -->
    <section class="revise-card">
      <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div class="flex items-center gap-2">
          <span class="text-sm font-semibold text-[#1d2129]">AI 定點改寫</span>
          <a-tag v-if="reviewIds.length" color="arcoblue" size="small"
            >餵 {{ reviewIds.length }} 則案例</a-tag
          >
          <a-tag v-else color="red" size="small">尚未勾選案例</a-tag>
        </div>
        <a-space size="mini">
          <a-button v-if="revise.streaming.value" status="danger" size="small" @click="revise.abort"
            >停止</a-button
          >
          <a-button
            v-else
            type="primary"
            size="small"
            :disabled="!canRun"
            @click="revise.run(llm.overrides.value)"
          >
            產出補丁
          </a-button>
        </a-space>
      </div>

      <p class="mb-3 text-[11px] leading-relaxed text-[#86909c]">
        模型只會回「哪一段 → 換成什麼」的補丁清單，不會重寫全文——這份 Prompt 有
        {{ systemPrompt.length.toLocaleString() }}
        字元的實測校準層與判例庫，整篇重寫過去實測會被順手砍掉、分數直接掉。沒被指名的段落不會被碰到。
      </p>

      <LlmConfigPicker
        :model-value="llm.provider.value"
        :provider-has-token="llm.providerHasToken.value"
        @update:model-value="llm.setProvider"
      />
      <div class="mt-3">
        <LlmKnobs
          :model-value="llm.knobs"
          :provider="llm.provider.value"
          @update:model-value="llm.setKnobs"
        />
      </div>
    </section>

    <a-alert v-for="message in revise.warnings.value" :key="message" type="warning" class="mb-2">{{
      message
    }}</a-alert>
    <a-alert v-if="revise.errorMessage.value" type="error">{{ revise.errorMessage.value }}</a-alert>

    <!-- 串流中 / 原始輸出 -->
    <section v-if="revise.streaming.value || revise.rawOutput.value" class="revise-card">
      <div class="flex items-center justify-between gap-2">
        <div class="flex items-center gap-2">
          <a-tag v-if="revise.streaming.value" color="arcoblue" size="small">生成中</a-tag>
          <span class="text-xs text-[#86909c]">
            <template v-if="revise.meta.value">
              {{ revise.meta.value.model }} · reasoning={{ revise.meta.value.reasoning_effort }} ·
              輸入 {{ revise.meta.value.prompt_chars.toLocaleString() }} 字元
            </template>
            <template v-else>連線中…</template>
          </span>
        </div>
        <a-button size="mini" type="text" @click="rawVisible = !rawVisible">
          {{ rawVisible ? '收起原始輸出' : '看原始輸出' }}
        </a-button>
      </div>
      <a-progress
        v-if="revise.streaming.value"
        :percent="0.999"
        :show-text="false"
        size="mini"
        status="normal"
        class="mt-2 animate-pulse"
      />
      <pre v-show="rawVisible" class="raw-output mt-2">{{
        revise.rawOutput.value || '等待模型回應…'
      }}</pre>
    </section>

    <!-- 診斷 -->
    <section v-if="revise.result.value?.diagnosis" class="revise-card">
      <div class="mb-1 text-xs font-semibold text-[#1d2129]">診斷：洞在哪</div>
      <p class="m-0 whitespace-pre-wrap text-xs leading-relaxed text-[#4e5969]">
        {{ revise.result.value.diagnosis }}
      </p>
    </section>

    <!-- 補丁清單 -->
    <section v-if="revise.patches.value.length" class="revise-card">
      <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div class="flex items-center gap-2">
          <span class="text-xs font-semibold text-[#1d2129]">
            補丁 {{ revise.patches.value.length }} 條
          </span>
          <a-tag color="green" size="small">已勾 {{ revise.selected.value.length }}</a-tag>
          <a-tag v-if="revise.unusableCount.value" color="orange" size="small">
            {{ revise.unusableCount.value }} 條無法套用
          </a-tag>
        </div>
        <a-button
          type="primary"
          size="small"
          :loading="revise.applying.value"
          :disabled="!revise.canApply.value"
          @click="onApply"
        >
          套用勾選補丁
        </a-button>
      </div>

      <div class="flex flex-col gap-2">
        <div
          v-for="(patch, index) in revise.patches.value"
          :key="index"
          class="patch-item"
          :class="{ 'patch-item--off': patch.status !== 'matched' }"
        >
          <div class="flex items-start gap-2">
            <a-checkbox
              :model-value="revise.selected.value.includes(index)"
              :disabled="patch.status !== 'matched'"
              @change="revise.toggle(index)"
            />
            <div class="min-w-0 flex-1">
              <div class="mb-1 flex flex-wrap items-center gap-2">
                <a-tooltip :content="STATUS_META[patch.status].hint">
                  <a-tag :color="STATUS_META[patch.status].color" size="small">
                    {{ STATUS_META[patch.status].label }}
                  </a-tag>
                </a-tooltip>
                <span v-if="patch.status === 'ambiguous'" class="text-[11px] text-[#86909c]">
                  命中 {{ patch.occurrences }} 處
                </span>
                <span class="text-xs text-[#1d2129]">{{ patch.reason }}</span>
              </div>

              <div class="patch-diff">
                <div class="patch-side patch-side--del">
                  <div class="patch-side-head">原文</div>
                  <pre class="patch-text">{{ patch.anchor }}</pre>
                </div>
                <div class="patch-side patch-side--add">
                  <div class="patch-side-head">改成</div>
                  <pre class="patch-text">{{ patch.replacement }}</pre>
                </div>
              </div>

              <div v-if="patch.risk" class="mt-1 text-[11px] leading-snug text-[#ff7d00]">
                ⚠ 過度矯正風險：{{ patch.risk }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 套用後：左右比對 + 存版 -->
    <section v-if="revise.revisedPrompt.value" class="revise-card">
      <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div class="flex items-center gap-2">
          <span class="text-xs font-semibold text-[#1d2129]">套用後比對</span>
          <span class="text-[11px] text-[#86909c]"
            >確認無誤再存草稿；不改變線上口徑，要上線再到「版本列表」升版</span
          >
        </div>
        <a-space size="mini">
          <a-button
            v-if="revise.result.value?.changelog"
            type="outline"
            size="small"
            @click="copyChangelog"
            >複製 CHANGELOG 草稿</a-button
          >
          <a-button
            type="primary"
            size="small"
            :loading="revise.savingVersion.value"
            @click="onSaveVersion"
            >存為新草稿</a-button
          >
        </a-space>
      </div>
      <div class="diff-box">
        <MdTextDiff
          :old-text="systemPrompt"
          :new-text="revise.revisedPrompt.value"
          old-label="現行"
          new-label="套用補丁後"
        />
      </div>
      <a-alert type="warning" class="mt-2">
        存版只改 Prompt。判準若同時寫在 <code>config/ai_judge/after_sales_root_cause.json</code>
        的 calibration，仍要人工同步——這裡不會自動改
        SSOT。改完務必到「回歸重跑」驗一次有沒有改壞舊案例。
      </a-alert>
    </section>

    <!-- 費用 -->
    <div v-if="revise.usage.value" class="text-[11px] text-[#86909c]">
      本次改寫 US$ {{ revise.usage.value.cost_usd.toFixed(6) }} ·
      {{ revise.usage.value.total_tokens.toLocaleString() }} tokens ·
      {{ (revise.usage.value.latency_ms / 1000).toFixed(1) }}s
    </div>
  </div>
</template>

<style scoped>
.revise-card {
  border: 1px solid #e5e6eb;
  border-radius: 10px;
  background: #fff;
  padding: 12px 14px;
}
.raw-output {
  max-height: 200px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  border-radius: 8px;
  background: #18181b;
  color: #d4d4d8;
  padding: 10px;
  font-size: 11px;
  line-height: 1.6;
}
.patch-item {
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  background: #fafafa;
  padding: 10px;
}
/* 無法套用的補丁：整體降透明度，但仍看得到內容（模型想改哪裡本身有診斷價值） */
.patch-item--off {
  background: #f7f8fa;
  opacity: 0.75;
}
.patch-diff {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.patch-side {
  min-width: 0;
  border-radius: 6px;
  padding: 6px 8px;
}
.patch-side--del {
  background: #fff1f0;
}
.patch-side--add {
  background: #e8ffea;
}
.patch-side-head {
  margin-bottom: 2px;
  color: #86909c;
  font-size: 10px;
  font-weight: 600;
}
.patch-text {
  max-height: 180px;
  overflow: auto;
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: #1d2129;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
  line-height: 1.55;
}
/* MdTextDiff 自帶滿高邏輯，在抽屜的文檔流裡需要一個有界高度的容器 */
.diff-box {
  display: flex;
  height: 420px;
  overflow: hidden;
}
</style>
