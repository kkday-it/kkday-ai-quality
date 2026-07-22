<script setup lang="ts">
import { onMounted } from 'vue';
import { StateGuard } from '@/components';
import { useSettingsConfigsStore } from '@/stores';
import { LlmConnectionCard } from '../components';
import { PROVIDERS } from '../constants';

// 🤖 LLM 連線 tab：每供應商（openai/gemini/bytedance）固定一條連線（base_url + token），
// 全項目共用（去帳戶隔離）。模型旋鈕（model/thinking/reasoning/temperature）不在此——
// 已下沉各功能區（初判分類、Prompt 調試台、Prompt 沙盒）就地配置＋「存為此區默認」，
// 團隊共用同一份默認、員工進同功能區沿用（見 @/components 的 LlmKnobs / LlmConfigPicker）。
const store = useSettingsConfigsStore();
onMounted(() => store.loadAll());

const onSave = (provider: string, payload: { baseUrl: string; token?: string }) =>
  store.saveLlmConnection(provider, payload.baseUrl, payload.token);
</script>

<template>
  <StateGuard :loading="store.loading">
    <div>
      <LlmConnectionCard
        v-for="p in PROVIDERS"
        :key="p.id"
        :provider="p.id"
        :connection="store.llmConnections[p.id]"
        :token-known="store.llmTokens[p.id] ?? ''"
        :has-token="!!store.providerHasToken[p.id]"
        @save="(payload) => onSave(p.id, payload)"
      />
      <p class="mb-0 mt-1 text-[13px] leading-[1.7] text-[#4e5969]">
        每供應商僅一條連線、全項目共用；模型 / 思考模式 / reasoning effort / temperature
        已移至各功能區（初判分類、Prompt 調試台、Prompt 沙盒）自行配置，並可各自「存為此區默認」供團隊共用。
      </p>
    </div>
  </StateGuard>
</template>
