<script setup lang="ts">
import { nextTick, ref } from 'vue';
import type { LLMConfig } from '../types';

// 單一 LLM config 卡片：label（點擊可 inline 改名）/ provider / model / base_url + 啟用標記 + 設為啟用/編輯/刪除。
const props = defineProps<{ config: LLMConfig; active: boolean }>();
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
  <a-card :bordered="true" size="small" class="mb-2">
    <div class="flex items-start justify-between gap-3">
      <div class="min-w-0">
        <div class="flex items-center gap-2">
          <a-input
            v-if="editing"
            ref="inputRef"
            v-model="draft"
            size="mini"
            class="max-w-[220px]"
            @blur="commit"
            @keyup.enter="commit"
            @keyup.esc="editing = false"
          />
          <span
            v-else
            class="cursor-pointer truncate font-medium hover:text-[#165dff]"
            title="點擊修改名稱"
            @click="startEdit"
          >
            {{ config.label }}
          </span>
          <a-tag v-if="active" color="green" size="small">啟用中</a-tag>
        </div>
        <div class="mt-1 text-xs text-[#86909c]">
          {{ config.provider }} · <span class="font-mono">{{ config.model }}</span>
          <span v-if="config.base_url"> · {{ config.base_url }}</span>
        </div>
      </div>
      <a-space :size="4">
        <a-button v-if="!active" size="mini" type="primary" status="success" @click="$emit('activate')">
          設為啟用
        </a-button>
        <a-button size="mini" @click="$emit('edit')">編輯</a-button>
        <a-popconfirm content="確定刪除此配置？" type="warning" @ok="$emit('delete')">
          <a-button size="mini" status="danger">刪除</a-button>
        </a-popconfirm>
      </a-space>
    </div>
  </a-card>
</template>
