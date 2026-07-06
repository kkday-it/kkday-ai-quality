<script setup lang="ts">
import { IconDragDotVertical } from '@arco-design/web-vue/es/icon';
import LlmConfigEditor from './LlmConfigEditor.vue';
import { composeLlmLabel } from '../utils';
import type { LlmConfig } from '../types';

// 單一 LLM config 卡片：以手風琴面板（a-collapse-item）呈現。
// header＝自動拼接名（provider/model/reasoning）+ 狀態徽章；extra＝啟用開關 / 刪除；
// body＝展開即為 LlmConfigEditor 表單本身（無「編輯」中間步驟，展開＝可編輯）。
// 不加收合 preview：拼接名已含 provider+model，preview 會重複（與 QC 不同，QC 標題是手動名故需 preview）。
// itemKey：對應 AccordionGroup 的 active-key（Arco 由 a-collapse-item 的 vnode key 解析），須與面板 default-active 一致。
defineProps<{
  config: LlmConfig;
  active: boolean;
  itemKey: string;
  providerTokens?: Record<string, string>;
}>();
defineEmits<{
  (e: 'delete'): void;
  (e: 'activate'): void;
  (e: 'save', payload: { config: LlmConfig; tokenPatch?: Record<string, string> }): void;
  (e: 'cancel'): void;
}>();
</script>

<template>
  <!-- :key 提供 a-collapse-item 名稱，供手風琴單開與 default-active 對應 -->
  <a-collapse-item :key="itemKey">
    <!-- header：自動拼接名（唯讀，不再手動改名）。標題已含 provider+model，故不另加 preview -->
    <template #header>
      <!-- 拖曳把手（SortableJS handle）：@click.stop 防點把手誤觸手風琴展開 -->
      <IconDragDotVertical class="drag-handle mr-1 shrink-0 cursor-move text-[var(--color-text-3)]" @click.stop />
      <span class="truncate font-medium">{{ composeLlmLabel(config) }}</span>
      <!-- 狀態徽章：所有卡片皆顯示，僅顏色/文字依啟用狀態不同（綠＝啟用中 / 灰＝未啟用） -->
      <a-tag class="ml-2" :color="active ? 'green' : 'gray'" size="small">
        {{ active ? '啟用中' : '未啟用' }}
      </a-tag>
    </template>

    <!-- extra 為 header 右側操作區，整體 .stop 避免點按鈕/開關時連帶折疊面板 -->
    <template #extra>
      <a-space :size="8" @click.stop>
        <!-- 啟用開關：開＝設為當前判決使用的模型；已啟用者 disabled（只能透過開啟另一張卡片來切換，避免無啟用狀態） -->
        <a-switch
          :model-value="active"
          :disabled="active"
          size="small"
          :title="active ? '當前啟用中' : '開啟以設為啟用'"
          @change="$emit('activate')"
        />
        <a-popconfirm content="確定刪除此配置？" type="warning" @ok="$emit('delete')">
          <a-button size="mini" status="danger">刪除</a-button>
        </a-popconfirm>
      </a-space>
    </template>

    <!-- body：展開即為編輯表單本身；編輯器自管草稿，儲存/取消由父層處理 -->
    <LlmConfigEditor
      :model-value="config"
      :provider-tokens="providerTokens ?? {}"
      @save="(payload) => $emit('save', payload)"
      @cancel="$emit('cancel')"
    />
  </a-collapse-item>
</template>
