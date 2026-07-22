<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { IconPlus } from '@arco-design/web-vue/es/icon';
import { AccordionGroup, StateGuard, StickyTabs } from '@/components';
import { useListDragSort } from '@/composables';
import { useSettingsConfigsStore } from '@/stores';
import { LlmConfigCard, LlmConfigEditor } from '../components';
import { DEFAULT_LLM_FORM, PROVIDERS } from '../constants';
import { composeLlmLabel, deriveProviderId } from '../utils';
import type { LlmConfig } from '../types';

// 🤖 LLM 模型 tab：供應商（openai/gemini/bytedance）為第一層 Tabs、供應商間配置完全隔離；
// 每個供應商 tab 內各自維護多套配置卡（inline 新增/編輯/刪除/啟用/拖排），與 QC DB 連線的環境 Tabs 同構。
// 新增配置直接繼承當前 tab 的供應商（編輯器無供應商選擇），token 跨供應商殘留從結構上不存在。
// 啟用語義維持全域唯一（初判一次只用一套模型）；啟用中配置所在供應商的 tab 標題帶綠點指示。
const store = useSettingsConfigsStore();
onMounted(() => store.loadAll());

/** config 的供應商歸屬：以落庫 provider 欄為準，缺值時由 base_url 反推（歷史資料兜底）。 */
const providerOf = (c: LlmConfig) => c.provider || deriveProviderId(c.base_url);
/** 某供應商下的配置清單（供應商間完全隔離的檢視邊界）。 */
const configsOf = (pid: string) => store.llmConfigs.filter((c) => providerOf(c) === pid);
/** 啟用中配置所在的供應商 id（tab 綠點指示用）；無啟用配置回 ''。 */
const activeLlmProvider = computed(() => {
  const c = store.llmConfigs.find((x) => x.id === store.activeLlmId);
  return c ? providerOf(c) : '';
});
/** 當前供應商 tab；載入完成後一次性落點到啟用中配置所在供應商（之後尊重使用者手動切換）。 */
const activeProvider = ref(PROVIDERS[0]?.id ?? 'openai');

// 新增流程：editing 僅持有「新建中的 blank config」（尚未落庫，渲染於其所屬供應商 tab 的清單尾端）。
// 既有卡片已「展開即編輯」（LlmConfigCard body 直接是表單），不再需要編輯態切換。
const editing = ref<LlmConfig | null>(null);
const isEditingNew = computed(
  () => !!editing.value && !store.llmConfigs.some((c) => c.id === editing.value!.id),
);
// 新增預設套用當前 tab 供應商的 preset（base_url / 預設 model / thinking / reasoning 旋鈕）。
const blank = (pid: string): LlmConfig => {
  const p = PROVIDERS.find((x) => x.id === pid) ?? PROVIDERS[0];
  const model = p.defaultModel ?? p.defaultModels?.[0]?.id ?? DEFAULT_LLM_FORM.model;
  const thinking =
    p.thinking !== undefined ? (p.thinking === 'on' ? 'on' : 'off') : DEFAULT_LLM_FORM.thinking;
  const reasoning_effort =
    p.reasoning_effort !== undefined ? p.reasoning_effort : DEFAULT_LLM_FORM.reasoning_effort;
  return {
    id: crypto.randomUUID(),
    // 名稱由參數自動拼接（provider/model/reasoning），不再手動命名。
    label: composeLlmLabel({ provider: p.id, model, reasoning_effort, thinking }),
    provider: p.id,
    base_url: p.base_url ?? '',
    model,
    temperature: null,
    thinking,
    reasoning_effort,
  };
};

// 手風琴受控展開：activeId＝當前展開面板；載入後展開落點供應商的第一張。
const activeId = ref('');
const landed = ref(false);
watch(
  () => store.llmConfigs,
  (list) => {
    if (landed.value || !list.length) return;
    landed.value = true;
    activeProvider.value = activeLlmProvider.value || activeProvider.value;
    activeId.value = configsOf(activeProvider.value)[0]?.id ?? '';
  },
);
// 切換供應商 tab：丟棄未存的新增草稿、展開該供應商第一張卡（無卡則全收合）——供應商間互不牽動。
watch(activeProvider, (pid) => {
  editing.value = null;
  activeId.value = configsOf(pid)[0]?.id ?? '';
});
// 單開不變量：展開任一既有面板（activeId 變真值）即丟棄尚未存的「新增」草稿，
// 確保任何交互下只有一個編輯面板展開（新增尾卡在手風琴單開控制之外，須手動互斥）。
watch(activeId, (id) => {
  if (id) editing.value = null;
});
// 新增＝先收合當前面板，於當前供應商 tab 尾端展開新增編輯器（供應商由 tab 決定）。
const openNew = () => {
  activeId.value = '';
  editing.value = blank(activeProvider.value);
};
const cancel = () => (editing.value = null);
// 既有卡片的編輯器按「取消」＝收合該面板（草稿不落庫）。
const collapse = () => (activeId.value = '');

const onSave = async (payload: { config: LlmConfig; tokenPatch?: Record<string, string> }) => {
  try {
    await store.saveLlmConfig(payload.config, payload.tokenPatch);
    editing.value = null;
    activeId.value = payload.config.id; // 存後展開該套（新增者以正式卡片呈現並保持開啟）
    Message.success('已儲存 LLM 配置');
  } catch (e: any) {
    Message.error('儲存失敗：' + (e?.message || e));
  }
};
const onDelete = async (id: string) => {
  try {
    await store.deleteLlmConfig(id);
    if (activeId.value === id) activeId.value = '';
    Message.success('已刪除');
  } catch (e: any) {
    Message.error('刪除失敗：' + (e?.message || e));
  }
};
const onActivate = async (id: string) => {
  try {
    await store.setActiveLlm(id);
    Message.success('已設為啟用');
  } catch (e: any) {
    Message.error('切換失敗：' + (e?.message || e));
  }
};

// 卡片拖動排序：各供應商 tab 內獨立拖排；subset 新順序寫回全量列表時保留其他供應商項的原位。
// 消費端（歸因頁模型下拉）經同 store 即時反映（全量順序＝各組交錯保留）。
const accordionRefs = reactive<Record<string, InstanceType<typeof AccordionGroup> | null>>({});
const applySubsetOrder = (pid: string, subset: LlmConfig[]): LlmConfig[] => {
  const queue = [...subset];
  return store.llmConfigs.map((c) => (providerOf(c) === pid ? (queue.shift() ?? c) : c));
};
PROVIDERS.forEach((p) => {
  useListDragSort(
    () => (accordionRefs[p.id]?.$el ?? null) as HTMLElement | null,
    () => configsOf(p.id),
    async (next) => {
      try {
        await store.reorderLlmConfigs(applySubsetOrder(p.id, next));
      } catch (err: any) {
        Message.error('排序儲存失敗：' + (err?.message || err));
      }
    },
    { handle: '.drag-handle', draggable: '.arco-collapse-item' },
  );
});
</script>

<template>
  <StateGuard :loading="store.loading">
    <div>
      <!-- 供應商第一層 Tabs：每個供應商各自一份配置清單，完全隔離；新增按鈕掛 tab 列右側（extra），
           「新增到哪個供應商」由當前 active tab 語境決定，不再於按鈕文案重複標註供應商 -->
      <StickyTabs v-model:active-key="activeProvider" type="card-gutter" size="small">
        <template #extra>
          <a-button type="primary" size="small" @click="openNew">
            <template #icon><icon-plus /></template>新增配置
          </a-button>
        </template>
        <a-tab-pane v-for="p in PROVIDERS" :key="p.id">
          <template #title>
            <span class="inline-flex items-center gap-1.5">
              <!-- 綠點＝啟用中配置所在供應商 -->
              <span
                v-if="activeLlmProvider === p.id"
                class="inline-block h-2 w-2 rounded-full bg-[rgb(var(--green-6))]"
              />
              {{ p.label }}
              <span class="text-xs text-[var(--color-text-3)]">{{ configsOf(p.id).length }}</span>
            </span>
          </template>

          <a-empty
            v-if="!configsOf(p.id).length && !(isEditingNew && editing?.provider === p.id)"
            :description="`尚無 ${p.label} 配置，點「新增配置」建立（各供應商配置完全隔離）`"
          />

          <!-- 手風琴卡片清單（單開 + 預設展開第一張）；新增草稿渲染於其所屬供應商的清單尾端 -->
          <AccordionGroup
            v-if="configsOf(p.id).length || (isEditingNew && editing?.provider === p.id)"
            :ref="(el: any) => (accordionRefs[p.id] = el)"
            v-model:active="activeId"
          >
            <LlmConfigCard
              v-for="c in configsOf(p.id)"
              :key="c.id"
              :config="c"
              :item-key="c.id"
              :active="c.id === store.activeLlmId"
              :token-known="store.llmTokens[c.id] ?? ''"
              @delete="onDelete(c.id)"
              @activate="onActivate(c.id)"
              @save="onSave"
              @cancel="collapse"
            />

            <!-- 新增：於當前供應商清單尾端 inline 展開一條（供應商繼承自 tab，不可選） -->
            <a-card
              v-if="isEditingNew && editing && editing.provider === p.id"
              :bordered="true"
              size="small"
              class="mb-2"
            >
              <div class="mb-2 text-[13px] font-medium text-[var(--color-text-2)]">
                新增配置 · {{ p.label }}
              </div>
              <LlmConfigEditor
                :model-value="editing"
                :token-known="''"
                @save="onSave"
                @cancel="cancel"
              />
            </a-card>
          </AccordionGroup>
        </a-tab-pane>
      </StickyTabs>

      <p class="mb-0 mt-3 text-[13px] leading-[1.7] text-[#4e5969]">
        各供應商（OpenAI / Gemini / ByteDance）配置完全隔離；同供應商可建多套配置，
        開啟卡片右側開關即切換當前初判使用的模型（全域同時僅一套啟用，tab 綠點＝啟用中所在供應商）。
      </p>
    </div>
  </StateGuard>
</template>
