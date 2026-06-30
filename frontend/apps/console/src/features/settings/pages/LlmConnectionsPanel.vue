<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { AccordionGroup, StateGuard } from '@/components';
import { useSettingsConfigsStore } from '@/stores';
import { LlmConfigCard, LlmConfigEditor } from '../components';
import { DEFAULT_LLM_FORM, PROVIDERS } from '../constants';
import { configStamp } from '../utils';
import type { LlmConfig } from '../types';

// 🤖 LLM 模型 tab：管理多套 LLM 配置（卡片清單 + inline 新增/編輯 + 刪除 + 卡片內「設為啟用」）。
const store = useSettingsConfigsStore();
// 新增預設套用 openai provider preset（含 base_url，避免新增時 Base URL 空白）。
const OPENAI = PROVIDERS.find((p) => p.id === 'openai');
onMounted(() => store.loadAll());

// inline 編輯：editing 持有「編輯中的 config」（新建＝blank，id 不在清單→渲染於尾端；編輯＝既有副本，就地取代卡片）。
const editing = ref<LlmConfig | null>(null);
const isEditingNew = computed(
  () => !!editing.value && !store.llmConfigs.some((c) => c.id === editing.value!.id)
);
const blank = (): LlmConfig => ({
  id: crypto.randomUUID(),
  label: `LLM ${configStamp()}`,
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
  { immediate: true, deep: false }
);
const openNew = () => (editing.value = blank());
// 編輯：載入副本 + 主動展開該面板（編輯器就地展開於面板 body 內，面板須為開啟態才看得到）
const openEdit = (cfg: LlmConfig) => {
  editing.value = { ...cfg };
  activeId.value = cfg.id;
};
const cancel = () => (editing.value = null);

const onSave = async (payload: { config: LlmConfig; tokenPatch?: Record<string, string> }) => {
  try {
    await store.saveLlmConfig(payload.config, payload.tokenPatch);
    editing.value = null;
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
const onRename = async (cfg: LlmConfig, label: string) => {
  try {
    await store.saveLlmConfig({ ...cfg, label }); // 不帶 token → 僅改名，機密不動
    Message.success('已更新名稱');
  } catch (e: any) {
    Message.error('改名失敗：' + (e?.message || e));
  }
};
</script>

<template>
  <StateGuard :loading="store.loading">
    <div>
      <div class="mb-2 flex items-center justify-between">
        <span class="font-medium">⚙️ LLM 模型連線</span>
        <a-button type="primary" size="small" @click="openNew">新增配置</a-button>
      </div>
      <a-empty
        v-if="!store.llmConfigs.length && !editing"
        description="尚無 LLM 配置，點「新增配置」建立第一套"
      />

      <!-- 手風琴卡片清單（單開 + 受控展開）；編輯中者於面板 body 內就地展開編輯器，面板本身保留 -->
      <AccordionGroup v-if="store.llmConfigs.length || isEditingNew" v-model:active="activeId">
        <LlmConfigCard
          v-for="c in store.llmConfigs"
          :key="c.id"
          :config="c"
          :item-key="c.id"
          :active="c.id === store.activeLlmId"
          :editing="editing && editing.id === c.id ? editing : null"
          :provider-tokens="store.providerTokens"
          @edit="openEdit(c)"
          @delete="onDelete(c.id)"
          @activate="onActivate(c.id)"
          @rename="(label) => onRename(c, label)"
          @save="onSave"
          @cancel="cancel"
        />

        <!-- 新增配置：無對應既有面板，於清單尾端以平卡 inline 展開編輯器 -->
        <a-card v-if="isEditingNew && editing" :bordered="true" size="small" class="mb-2">
          <LlmConfigEditor
            :model-value="editing"
            :provider-tokens="store.providerTokens"
            @save="onSave"
            @cancel="cancel"
          />
        </a-card>
      </AccordionGroup>

      <p class="mb-0 mt-3 text-[13px] leading-[1.7] text-[#4e5969]">
        管理多套 LLM 配置；開啟卡片右側開關即切換當前判決使用的模型（綠色徽章為啟用中）。
      </p>
    </div>
  </StateGuard>
</template>
