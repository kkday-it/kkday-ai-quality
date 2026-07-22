<script setup lang="ts">
/**
 * 左側矮長條收合觸發 + 懸浮面板：純佈局元件，不含業務邏輯，只管「按鈕觸發顯示/隱藏 slot 內容」。
 * 觸發鈕為固定高度的窄直排長條（不隨旁邊內容撐滿高，與主內容視覺解耦）；面板用 `v-show`
 * （非 `v-if`）——保持掛載，讓 slot 內元件（如版本選擇器）的預設值/emit 即使收合也立即生效，
 * 不因收合而延遲初始化。
 *
 * 面板結構＝可捲動 body（預設 slot）+ 可選的固定底部動作列（`#footer` 具名 slot，放取消/確認
 * 等按鈕——面板本身就是一張「設定表單」，動作按鈕收在面板內，不佔用外層 drawer 的 footer）。
 *
 * 放在跨 feature 共用層（非 features/judge）：元件不含任何業務邏輯，純排版/容器結構（見
 * `.claude/rules/frontend-vue.md` 佈局性質元件抽離準則——判準看內容是否耦合業務，不是看
 * 目前消費端剛好都在哪個 feature）。
 */
withDefaults(
  defineProps<{
    /** 觸發長條上的文字（直排顯示）。 */
    label: string;
    /** 面板是否展開。 */
    modelValue: boolean;
    /** 展開後面板容器的 class（管寬度/最大高即可；邊框/背景/陰影/內距/捲動由元件內建）。 */
    panelClass?: string;
    /** true：面板以絕對定位懸浮在觸發長條右側（不佔版面寬度，開合不推擠旁邊內容）；
     * false（預設）：面板為並排 flex 子項，展開會佔用寬度推開內容。 */
    floating?: boolean;
  }>(),
  { panelClass: 'min-w-0 flex-1', floating: false },
);
defineEmits<{ (e: 'update:modelValue', v: boolean): void }>();
</script>

<template>
  <div class="relative flex min-h-0 flex-none" :class="floating ? '' : 'gap-3'">
    <button
      type="button"
      class="h-24 flex-none self-start rounded-lg text-xs text-white"
      style="
        width: 28px;
        border: none;
        cursor: pointer;
        background: rgb(var(--primary-6));
        writing-mode: vertical-rl;
        letter-spacing: 2px;
      "
      :aria-pressed="modelValue"
      :title="(modelValue ? '收合' : '展開') + label"
      @click="$emit('update:modelValue', !modelValue)"
    >
      {{ label }}
    </button>
    <div
      v-show="modelValue"
      class="flex flex-col overflow-hidden"
      :class="[
        panelClass,
        floating
          ? 'absolute left-full top-0 z-10 ml-2 rounded-lg border bg-[var(--color-bg-2)] shadow-lg'
          : '',
      ]"
    >
      <div class="min-h-0 flex-1 overflow-auto p-4">
        <slot />
      </div>
      <div
        v-if="$slots.footer"
        class="flex flex-none items-center justify-end gap-2 border-t px-4 py-3"
      >
        <slot name="footer" />
      </div>
    </div>
  </div>
</template>
