<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { Message } from '@arco-design/web-vue';
import { StateGuard } from '@/components';
import { useSettingsConfigsStore } from '@/stores';
import { LLMConfigCard, LLMConfigEditor } from '../components';
import { DEFAULT_LLM_FORM } from '../constants';
import { configStamp } from '../utils';
import type { LLMConfig } from '../types';

// 🤖 LLM 模型 tab：管理多套 LLM 配置（卡片清單 + 新增/編輯 modal + 刪除 + 卡片內「設為啟用」）。
const store = useSettingsConfigsStore();
onMounted(() => store.loadAll());

const modal = ref(false);
const editing = ref<LLMConfig | null>(null);
const blank = (): LLMConfig => ({
  id: crypto.randomUUID(),
  label: `LLM ${configStamp()}`,
  provider: 'openai',
  base_url: '',
  model: DEFAULT_LLM_FORM.model,
  temperature: null,
  thinking: 'off',
  reasoning_effort: 'medium',
});
const openNew = () => {
  editing.value = blank();
  modal.value = true;
};
const openEdit = (cfg: LLMConfig) => {
  editing.value = { ...cfg };
  modal.value = true;
};
const onSave = async (payload: { config: LLMConfig; tokenPatch?: Record<string, string> }) => {
  try {
    await store.saveLLMConfig(payload.config, payload.tokenPatch);
    modal.value = false;
    Message.success('已儲存 LLM 配置');
  } catch (e: any) {
    Message.error('儲存失敗：' + (e?.message || e));
  }
};
const onDelete = async (id: string) => {
  try {
    await store.deleteLLMConfig(id);
    Message.success('已刪除');
  } catch (e: any) {
    Message.error('刪除失敗：' + (e?.message || e));
  }
};
const onActivate = async (id: string) => {
  try {
    await store.setActiveLLM(id);
    Message.success('已設為啟用');
  } catch (e: any) {
    Message.error('切換失敗：' + (e?.message || e));
  }
};
const onRename = async (cfg: LLMConfig, label: string) => {
  try {
    await store.saveLLMConfig({ ...cfg, label }); // 不帶 token → 僅改名，機密不動
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
      <a-empty v-if="!store.llmConfigs.length" description="尚無 LLM 配置，點「新增配置」建立第一套" />
      <LLMConfigCard
        v-for="c in store.llmConfigs"
        :key="c.id"
        :config="c"
        :active="c.id === store.activeLLMId"
        @edit="openEdit(c)"
        @delete="onDelete(c.id)"
        @activate="onActivate(c.id)"
        @rename="(label) => onRename(c, label)"
      />
      <p class="mb-0 mt-3 text-[13px] leading-[1.7] text-[#4e5969]">
        管理多套 LLM 配置；卡片「設為啟用」即切換當前判決使用的模型（綠色徽章為啟用中）。
      </p>

      <a-modal v-model:visible="modal" :width="700" :footer="false" title="LLM 模型配置" unmount-on-close>
        <LLMConfigEditor
          v-if="editing"
          :model-value="editing"
          :provider-tokens="store.providerTokens"
          @save="onSave"
        />
      </a-modal>
    </div>
  </StateGuard>
</template>
