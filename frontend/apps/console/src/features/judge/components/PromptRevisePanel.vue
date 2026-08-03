<script setup lang="ts">
/**
 * 流水線步驟②「AI 定點改寫」：勾好的案例 → 旗艦模型產補丁 → 逐條勾選 → 套用 → 左右比對，
 * 產出**候選 Prompt** 交給③驗證。
 *
 * 「存為新草稿」**不在這裡**——它是定案動作、前提是③的回歸結果，放在這裡會逼使用者「跑完回歸
 * 再倒回上一步存檔」，正是本次改造要消滅的那條 Z 字形路徑。它現在在步驟④。
 *
 * 為什麼是補丁不是整篇重寫、為什麼 anchor 要驗唯一：見 `backend/app/judge/prompt_reviser.py`
 * 的模組說明（簡言之，這份 Prompt 有 2–3 萬字的實測校準層，整篇重寫會被順手砍掉且 diff 沒人審得動）。
 *
 * 模型走獨立的 `prompt_revise` 功能區——裁決跑批要便宜，改 Prompt 要聰明，兩者不共用旋鈕。
 */
import { computed, ref } from 'vue';
import { Message } from '@arco-design/web-vue';
import { AsyncSection, LlmConfigSelect, MdTextDiff } from '@/components';
import type { usePromptRevise } from '../composables';
import { useLlmAreaConfig } from '@/composables';

const props = defineProps<{
  /**
   * 改寫流程狀態（由抽屜持有並下傳）。
   *
   * 整包傳而不是攤成十幾個 prop：這些欄位彼此高度耦合（串流三態、補丁勾選、套用結果一起變動），
   * 攤開只會讓簽名爆炸且每加一個欄位就要改兩處。抽屜是唯一擁有者，面板只讀不寫（動作一律呼叫
   * 它提供的方法）。
   */
  revise: ReturnType<typeof usePromptRevise>;
  /** 改寫基準的全文（diff 左側與字元數說明用；實際送出的基準在 composable 內）。 */
  systemPrompt: string;
  /** 案例庫勾選的 id（只用於顯示與 canRun 判斷）。 */
  reviewIds: number[];
}>();

const llm = useLlmAreaConfig('prompt_revise');

/** 串流原始輸出預設收起：正常情況沒人要看 JSON，出問題時才展開追。 */
const rawVisible = ref(false);

const canRun = computed(
  () => !props.revise.streaming.value && props.reviewIds.length > 0 && !!llm.overrides.value.model,
);

/** 串流已開始但還沒吐出任何內容的空窗——這段用骨架佔位，有 delta 之後才換成真正的輸出。 */
const waitingFirstDelta = computed(
  () => props.revise.streaming.value && !props.revise.rawOutput.value,
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
  const text = props.revise.result.value?.changelog;
  if (!text) return;
  await navigator.clipboard.writeText(text);
  Message.success('已複製 CHANGELOG 條目草稿');
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

      <div class="text-xs text-[#86909c]">模型配置</div>
      <LlmConfigSelect
        v-model="llm.configId.value"
        class="mt-1"
        :configs="llm.configs.value"
        :provider-has-token="llm.providerHasToken.value"
      />
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
      <!--
        只把「已開始串流但還沒吐出任何字」那段交給 AsyncSection：它是**互斥三態**（loading 時
        預設 slot 不渲染），一旦有 delta 就必須換成真正的輸出，否則使用者反而看不到生成內容。
        改造前這裡是 `a-progress :percent="0.999"` + animate-pulse 假裝 indeterminate。
      -->
      <AsyncSection :loading="waitingFirstDelta" :skeleton-rows="2" class="mt-2">
        <pre v-show="rawVisible" class="raw-output">{{ revise.rawOutput.value }}</pre>
      </AsyncSection>
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
          @click="revise.apply()"
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
          <span class="text-[11px] text-[#86909c]">
            這就是本次的候選版；確認無誤後到下一步「回歸驗證」跑一輪，通過了才在④定案存檔
          </span>
        </div>
        <a-button
          v-if="revise.result.value?.changelog"
          type="outline"
          size="small"
          @click="copyChangelog"
          >複製 CHANGELOG 草稿</a-button
        >
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
        改寫只動 Prompt。若補丁動到受控值（L1~L4 的類名或選項），
        <code>config/ai_judge/after_sales_root_cause.json</code>
        仍要人工同步——這裡不會自動改 SSOT，兩邊不同表時判定會被 enum 硬塞。
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
