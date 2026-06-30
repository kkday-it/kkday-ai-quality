<script setup lang="ts">
import { nextTick, ref } from 'vue';
import type { LLMConfig } from '../types';

// 單一 LLM config 卡片：以手風琴面板（a-collapse-item）呈現。
// header＝label（點擊可 inline 改名）+ 狀態徽章；extra＝啟用開關 / 編輯 / 刪除；body＝provider / model / base_url。
// itemKey：對應 AccordionGroup 的 active-key（Arco 由 a-collapse-item 的 vnode key 解析），須與面板 default-active 一致。
const props = defineProps<{ config: LLMConfig; active: boolean; itemKey: string }>();
const emit = defineEmits<{
  (e: 'edit'): void;
  (e: 'delete'): void;
  (e: 'activate'): void;
  (e: 'rename', label: string): void;
}>();

// inline 改名：點名稱 → 切 input，enter/blur 提交（非空且有變更才 emit），esc 取消。
const editing = ref(false);
const draft = ref('');
const inputRef = ref<{ focus?: () => void } | null>(null);
const startEdit = async () => {
  draft.value = props.config.label;
  editing.value = true;
  await nextTick();
  inputRef.value?.focus?.();
};
const commit = () => {
  if (!editing.value) return;
  editing.value = false;
  const v = draft.value.trim();
  if (v && v !== props.config.label) emit('rename', v);
};
</script>

<template>
  <!-- :key 提供 a-collapse-item 名稱，供手風琴單開與 default-active 對應 -->
  <a-collapse-item :key="itemKey">
    <!-- header 整列點擊會切換面板，故 label/輸入框 click 需 .stop 才能改名而不誤觸折疊 -->
    <template #header>
      <a-input
        v-if="editing"
        ref="inputRef"
        v-model="draft"
        size="mini"
        class="max-w-[220px]"
        @click.stop
        @blur="commit"
        @keyup.enter="commit"
        @keyup.esc="editing = false"
      />
      <span
        v-else
        class="cursor-pointer truncate font-medium hover:text-[#165dff]"
        title="點擊修改名稱"
        @click.stop="startEdit"
      >
        {{ config.label }}
      </span>
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
        <a-button size="mini" @click="$emit('edit')">編輯</a-button>
        <a-popconfirm content="確定刪除此配置？" type="warning" @ok="$emit('delete')">
          <a-button size="mini" status="danger">刪除</a-button>
        </a-popconfirm>
      </a-space>
    </template>

    <!-- body：展開後顯示連線細節 -->
    <div class="text-xs text-[#86909c]">
      {{ config.provider }} · <span class="font-mono">{{ config.model }}</span>
      <span v-if="config.base_url"> · {{ config.base_url }}</span>
    </div>
  </a-collapse-item>
</template>
