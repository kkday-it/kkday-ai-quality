<script setup lang="ts">
import { IconDragDotVertical } from '@arco-design/web-vue/es/icon';
import QcConfigEditor from './QcConfigEditor.vue';
import type { QcConfig } from '../types';

// 單一 QC config 卡片：以手風琴面板（a-collapse-item）呈現。
// header＝label（唯讀）+ 狀態徽章 + 收合時的 host preview；extra＝啟用開關 / 刪除；
// 環境不另掛標籤——已由所在環境 tab 與連線名稱（qc <env> db …）雙重表達，避免同一資訊重複三處；
// body＝展開即為 QcConfigEditor 表單本身（無「編輯」中間步驟，展開＝可編輯；改名走表單內「連線名稱」欄）。
// itemKey：對應 AccordionGroup 的 active-key（Arco 由 a-collapse-item 的 vnode key 解析），須與面板 default-active 一致。
defineProps<{
  config: QcConfig;
  active: boolean;
  itemKey: string;
  /** 是否為當前展開面板；收合時於 header 顯示 host preview，展開時交由 body 表單呈現。 */
  expanded?: boolean;
  /** 此 config 已知明文 password（供編輯器眼睛切換 / 留空不變更）。 */
  password?: string;
}>();

defineEmits<{
  (e: 'delete'): void;
  (e: 'activate'): void;
  (e: 'save', payload: { config: QcConfig; password?: string }): void;
  (e: 'cancel'): void;
}>();
</script>

<template>
  <!-- :key 提供 a-collapse-item 名稱，供手風琴單開與 default-active 對應 -->
  <a-collapse-item :key="itemKey">
    <!-- header：連線名稱（唯讀）+ 狀態；收合時於名稱下方顯示 host preview -->
    <template #header>
      <!-- 拖曳把手（SortableJS handle）：@click.stop 防點把手誤觸手風琴展開 -->
      <IconDragDotVertical
        class="drag-handle mr-1 shrink-0 cursor-move text-[var(--color-text-3)]"
        @click.stop
      />
      <span class="inline-flex flex-col">
        <span class="inline-flex items-center">
          <span class="truncate font-medium">{{ config.label }}</span>
          <!-- 狀態徽章：所有卡片皆顯示，僅顏色/文字依啟用狀態不同（綠＝啟用中 / 灰＝未啟用） -->
          <a-tag class="ml-2" :color="active ? 'green' : 'gray'" size="small">
            {{ active ? '啟用中' : '未啟用' }}
          </a-tag>
        </span>
        <!-- 收合預覽：未展開時顯示連線摘要；展開時隱藏（由 body 表單呈現），避免資訊重複 -->
        <span v-if="!expanded" class="mt-0.5 truncate text-xs text-[#86909c]">
          {{ config.host }}:{{ config.port ?? 5432 }}
        </span>
      </span>
    </template>

    <!-- extra 為 header 右側操作區，整體 .stop 避免點按鈕/開關時連帶折疊面板 -->
    <template #extra>
      <a-space :size="8" @click.stop>
        <!-- 啟用開關：開＝設為當前使用的連線；已啟用者 disabled（只能透過開啟另一張卡片來切換，避免無啟用狀態） -->
        <a-switch
          :model-value="active"
          :disabled="active"
          size="small"
          :title="active ? '當前啟用中' : '開啟以設為啟用'"
          @change="$emit('activate')"
        />
        <a-popconfirm content="確定刪除此連線？" type="warning" @ok="$emit('delete')">
          <a-button size="mini" status="danger">刪除</a-button>
        </a-popconfirm>
      </a-space>
    </template>

    <!-- body：展開即為編輯表單本身；編輯器自管草稿，儲存/取消由父層處理 -->
    <QcConfigEditor
      :model-value="config"
      :password="password ?? ''"
      @save="(payload) => $emit('save', payload)"
      @cancel="$emit('cancel')"
    />
  </a-collapse-item>
</template>
