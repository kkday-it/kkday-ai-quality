<script setup lang="ts">
import { ref } from 'vue';
import Settings from './Settings.vue';
import DatasourceSettings from './DatasourceSettings.vue';

// ⚙️ 配置 tab：Arco Collapse「手風琴模式」統一兩個折疊面板 —— ① LLM 模型配置 ② QC DB 配置。
// 各面板自帶「儲存配置 / 測試連線」與說明（封裝於子元件）。accordion：一次僅展開一個。
const active = ref<string[]>(['llm']); // accordion 由 Arco 保證單開；v-model 型別仍為陣列
</script>

<template>
  <a-collapse v-model:active-key="active" accordion :bordered="false" class="config-collapse">
    <a-collapse-item key="llm" header="⚙️ LLM 模型配置">
      <Settings />
    </a-collapse-item>
    <a-collapse-item key="qc_db" header="🗄️ QC DB（PostgreSQL）">
      <DatasourceSettings />
    </a-collapse-item>
  </a-collapse>
</template>

<style scoped>
/* Arco collapse 的 header/content 為第三方深層 DOM，且 collapse / collapse-item 皆無
   header-style / body-style prop（僅 a-card 有），故依專案樣式鐵律第 3 順位用 :deep 覆寫，
   把每個面板做成白底卡片。色彩用 arco 語意 token，暗色模式自動對應。 */
.config-collapse :deep(.arco-collapse-item) {
  margin-bottom: 12px;
  border: 1px solid var(--color-neutral-3);
  border-radius: 8px;
  overflow: hidden;
  background-color: var(--color-bg-2);
}

.config-collapse :deep(.arco-collapse-item:last-child) {
  margin-bottom: 0;
}

.config-collapse :deep(.arco-collapse-item-header),
.config-collapse :deep(.arco-collapse-item-content) {
  background-color: var(--color-bg-2);
}
</style>
