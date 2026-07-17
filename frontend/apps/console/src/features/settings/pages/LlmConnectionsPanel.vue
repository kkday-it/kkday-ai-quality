<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { AccordionGroup, StateGuard } from '@/components';
import { useListDragSort } from '@/composables';
import { useSettingsConfigsStore } from '@/stores';
import { LlmConfigCard, LlmConfigEditor } from '../components';
import { DEFAULT_LLM_FORM, PROVIDERS } from '../constants';
import { composeLlmLabel } from '../utils';
import type { LlmConfig } from '../types';

// 🤖 LLM 模型 tab：管理多套 LLM 配置（卡片清單 + inline 新增/編輯 + 刪除 + 卡片內「設為啟用」）。
const store = useSettingsConfigsStore();
// 新增預設套用 openai provider preset（含 base_url，避免新增時 Base URL 空白）。
const OPENAI = PROVIDERS.find((p) => p.id === 'openai');
onMounted(() => store.loadAll());

// 新增流程：editing 僅持有「新建中的 blank config」（尚未落庫，渲染於清單尾端）。
// 既有卡片已「展開即編輯」（LlmConfigCard body 直接是表單），不再需要編輯態切換。
const editing = ref<LlmConfig | null>(null);
const isEditingNew = computed(
  () => !!editing.value && !store.llmConfigs.some((c) => c.id === editing.value!.id),
);
const blank = (): LlmConfig => ({
  id: crypto.randomUUID(),
  // 名稱由參數自動拼接（provider/model/reasoning），不再手動命名。
  label: composeLlmLabel({
    provider: 'openai',
    model: DEFAULT_LLM_FORM.model,
    reasoning_effort: DEFAULT_LLM_FORM.reasoning_effort,
  }),
  provider: 'openai',
  base_url: OPENAI?.base_url ?? '', // 預設帶入 OpenAI 端點（https://api.openai.com/v1）
  model: DEFAULT_LLM_FORM.model,
  temperature: null,
  thinking: DEFAULT_LLM_FORM.thinking,
  reasoning_effort: DEFAULT_LLM_FORM.reasoning_effort,
});
// 手風琴受控展開：activeId＝當前展開面板。預設展開第一張（loadAll 完成後第一筆就緒時補上）。
const activeId = ref('');
watch(
  () => store.llmConfigs,
  (list) => {
    if (!activeId.value && list[0]) activeId.value = list[0].id;
  },
  { immediate: true, deep: false },
);
// 單開不變量：展開任一既有面板（activeId 變真值）即丟棄尚未存的「新增」草稿，
// 確保任何交互下只有一個編輯面板展開（新增尾卡在手風琴單開控制之外，須手動互斥）。
watch(activeId, (id) => {
  if (id) editing.value = null;
});
// 新增＝先收合所有既有面板（activeId=''），只展開尾端新增編輯器。
const openNew = () => {
  activeId.value = '';
  editing.value = blank();
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

// 卡片拖動排序：header 把手拖放 → 整包持久化順序；消費端（歸因頁模型下拉）經同 store 即時反映。
const accordionRef = ref<InstanceType<typeof AccordionGroup> | null>(null);
useListDragSort(
  () => (accordionRef.value?.$el ?? null) as HTMLElement | null,
  () => store.llmConfigs,
  async (next) => {
    try {
      await store.reorderLlmConfigs(next);
    } catch (e: any) {
      Message.error('排序儲存失敗：' + (e?.message || e));
    }
  },
  { handle: '.drag-handle', draggable: '.arco-collapse-item' },
);
</script>

<template>
  <StateGuard :loading="store.loading">
    <div>
      <div class="mb-2 flex items-center justify-between">
        <span class="font-medium">🤖 LLM 模型連線</span>
        <a-button type="primary" size="small" @click="openNew">新增配置</a-button>
      </div>
      <a-empty
        v-if="!store.llmConfigs.length && !editing"
        description="尚無 LLM 配置，點「新增配置」建立第一套"
      />

      <!-- 手風琴卡片清單（單開 + 受控展開）；編輯中者於面板 body 內就地展開編輯器，面板本身保留 -->
      <AccordionGroup
        v-if="store.llmConfigs.length || isEditingNew"
        ref="accordionRef"
        v-model:active="activeId"
      >
        <LlmConfigCard
          v-for="c in store.llmConfigs"
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

        <!-- 新增配置：無對應既有面板，於清單尾端以平卡 inline 展開編輯器 -->
        <a-card v-if="isEditingNew && editing" :bordered="true" size="small" class="mb-2">
          <LlmConfigEditor
            :model-value="editing"
            :token-known="''"
            @save="onSave"
            @cancel="cancel"
          />
        </a-card>
      </AccordionGroup>

      <p class="mb-0 mt-3 text-[13px] leading-[1.7] text-[#4e5969]">
        管理多套 LLM 配置；開啟卡片右側開關即切換當前初判使用的模型（綠色徽章為啟用中）。
      </p>
    </div>
  </StateGuard>
</template>
