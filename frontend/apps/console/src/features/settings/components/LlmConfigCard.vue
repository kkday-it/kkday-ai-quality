<script setup lang="ts">
import { nextTick, ref } from 'vue';
import LlmConfigEditor from './LlmConfigEditor.vue';
import type { LlmConfig } from '../types';

// 單一 LLM config 卡片：以手風琴面板（a-collapse-item）呈現。
// header＝label（點擊可 inline 改名）+ 狀態徽章；extra＝啟用開關 / 編輯 / 刪除；body＝provider / model / base_url。
// itemKey：對應 AccordionGroup 的 active-key（Arco 由 a-collapse-item 的 vnode key 解析），須與面板 default-active 一致。
// editing 非 null 時＝本卡片進入編輯態：面板保留，於摘要行下方就地展開 LlmConfigEditor（不替換整張卡片）。
const props = defineProps<{
  config: LlmConfig;
  active: boolean;
  itemKey: string;
  editing?: LlmConfig | null;
  providerTokens?: Record<string, string>;
}>();
const emit = defineEmits<{
  (e: 'edit'): void;
  (e: 'delete'): void;
  (e: 'activate'): void;
  (e: 'rename', label: string): void;
  (e: 'save', payload: { config: LlmConfig; tokenPatch?: Record<string, string> }): void;
  (e: 'cancel'): void;
}>();

// inline 改名：點名稱 → 切 input，enter/blur 提交（非空且有變更才 emit），esc 取消。
// 命名為 renaming 以免與「編輯態」prop `editing` 衝突（後者控制配置編輯器的就地展開）。
const renaming = ref(false);
const draft = ref('');
const inputRef = ref<{ focus?: () => void } | null>(null);
const startEdit = async () => {
  draft.value = props.config.label;
  renaming.value = true;
  await nextTick();
  inputRef.value?.focus?.();
};
const commit = () => {
  if (!renaming.value) return;
  renaming.value = false;
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
        v-if="renaming"
        ref="inputRef"
        v-model="draft"
        size="mini"
        class="max-w-[220px]"
        @click.stop
        @blur="commit"
        @keyup.enter="commit"
        @keyup.esc="renaming = false"
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
        <!-- 編輯態下隱藏「編輯」鈕（避免與下方就地展開的編輯器重複），編輯器自帶取消 -->
        <a-button v-if="!editing" size="mini" @click="$emit('edit')">編輯</a-button>
        <a-popconfirm content="確定刪除此配置？" type="warning" @ok="$emit('delete')">
          <a-button size="mini" status="danger">刪除</a-button>
        </a-popconfirm>
      </a-space>
    </template>

    <!-- body：展開後顯示連線細節；進入編輯態時於其下方就地展開編輯器（面板不被替換） -->
    <div class="text-xs text-[#86909c]">
      {{ config.provider }} · <span class="font-mono">{{ config.model }}</span>
      <span v-if="config.base_url"> · {{ config.base_url }}</span>
    </div>
    <LlmConfigEditor
      v-if="editing"
      class="mt-3"
      :model-value="editing"
      :provider-tokens="providerTokens ?? {}"
      @save="(payload) => $emit('save', payload)"
      @cancel="$emit('cancel')"
    />
  </a-collapse-item>
</template>
