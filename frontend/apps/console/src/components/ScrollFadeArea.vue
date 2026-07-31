<script setup lang="ts">
/**
 * 可捲動內容區（底部漸隱 + 「還有更多」提示）：內容超出 `maxHeight` 時，底部以遮罩淡出並浮出提示，
 * 使用者捲到底後兩者一起消失；內容本來就沒超出則完全不出現。
 *
 * 解決的問題：定高捲動區在表格列 / 抽屜內很容易被誤讀成「內容就這麼多」——macOS 的 overlay
 * scrollbar 靜止時不可見，切齊的文字邊界又讀起來像段落結束，使用者根本不知道可以往下捲。
 *
 * 兩種高度模式（皆走預設插槽放內容）：
 * - **定高**：`<ScrollFadeArea max-height="10rem">`，取代原本的 `class="max-h-40 overflow-y-auto"`
 * - **撐滿父層**：不傳 `max-height`，改在根元素掛 `class="min-h-0 flex-1"`（或 `h-full`），
 *   取代原本的 `class="min-h-0 flex-1 overflow-auto"`；前提同既有慣例——父層高度鏈要通。
 */
import { computed, ref } from 'vue';
import { useElementSize, useScroll } from '@vueuse/core';
import { IconDown } from '@arco-design/web-vue/es/icon';

const props = withDefaults(
  defineProps<{
    /**
     * 內容區最大高度（任意 CSS 長度，超出即捲動）。
     * 留空＝不設上限、改由父層決定高度（撐滿模式：在根元素掛 `min-h-0 flex-1` 或 `h-full`）。
     */
    maxHeight?: string;
    /** 底部提示文字；傳空字串則只保留漸隱遮罩、不顯提示。 */
    hint?: string;
    /** 底部漸隱遮罩高度（px）。 */
    fadeHeight?: number;
    /**
     * 掛在「內容層」的 class（原本寫在捲動容器上的佈局 class 放這，如 `flex flex-col gap-4`）。
     * 根元素的 class 走一般 fallthrough——那層負責外框（高度/邊框/底色），內容層負責排版。
     */
    contentClass?: string;
  }>(),
  { maxHeight: '', hint: '向下捲動查看更多', fadeHeight: 28, contentClass: '' },
);

const scrollEl = ref<HTMLElement | null>(null);
const contentEl = ref<HTMLElement | null>(null);

/**
 * `arrivedState` 只在捲動事件後更新——內容未超出高度時永遠不會觸發捲動，初始值會停在
 * `bottom: false`，只靠它會對「根本不用捲」的短內容誤顯提示。故另以「內容高 vs 視窗高」判定
 * 是否真的可捲；兩者皆走 VueUse（內部 ResizeObserver 自動跟上內容/容器尺寸變動，免手寫
 * listener 與 onUnmounted 清理）。
 */
const { arrivedState } = useScroll(scrollEl, { offset: { bottom: 2 } });
const { height: viewportH } = useElementSize(scrollEl);
const { height: contentH } = useElementSize(contentEl);

/** 內容確實超出視窗高度才算可捲（留 1px 容差，避免 subpixel 誤判）。 */
const scrollable = computed(() => contentH.value - viewportH.value > 1);
/** 已捲到底、或根本不需要捲 → 收起遮罩與提示。 */
const atEnd = computed(() => !scrollable.value || arrivedState.bottom);

const areaStyle = computed(() => ({
  ...(props.maxHeight ? { maxHeight: props.maxHeight } : {}),
  '--scroll-fade-h': `${props.fadeHeight}px`,
}));

/**
 * 對外交出捲動容器本身（同 `StickyTabs.getScrollEl()` 慣例）：消費端若要自行捲動定位
 * （如 diff 對齊首個變動、串流捲到底），拿這個元素操作，不需知道本元件內部結構。
 */
function getScrollEl(): HTMLElement | null {
  return scrollEl.value;
}

defineExpose({ getScrollEl });
</script>

<template>
  <!-- flex-col：撐滿模式下把父層給的高度傳遞給內層捲動容器；定高模式下高度仍由 maxHeight 決定 -->
  <div class="relative flex flex-col">
    <div
      ref="scrollEl"
      class="min-h-0 flex-1 overflow-y-auto pr-1"
      :class="{ 'scroll-fade': !atEnd }"
      :style="areaStyle"
    >
      <!-- 量測內容真實高度用的內層：直接量捲動容器只會拿到被 max-height 夾住的視窗高 -->
      <div ref="contentEl" :class="contentClass"><slot /></div>
    </div>

    <!-- 提示浮層放在捲動容器「外面」：mask 會連同容器內的浮層一起淡出，塞進去就看不見了 -->
    <div
      v-if="hint"
      class="pointer-events-none absolute inset-x-0 bottom-0 flex justify-center transition-opacity duration-200"
      :class="atEnd ? 'opacity-0' : 'opacity-100'"
      aria-hidden="true"
    >
      <span
        class="flex items-center gap-0.5 rounded-full border border-[var(--color-border-2)] bg-[var(--color-bg-2)] px-1.5 py-px text-[10px] leading-4 text-[var(--color-text-3)] shadow-sm"
      >
        <IconDown />
        {{ hint }}
      </span>
    </div>
  </div>
</template>

<style scoped>
/**
 * 底部漸隱用 mask 而非疊一層漸層色塊：本元件常置於 a-table 列內，而 Arco 的列 hover 會換底色，
 * 疊色塊在 hover 時會露出色差；mask 直接把內容本身淡出，與底色無關，深淺主題也通用。
 */
.scroll-fade {
  -webkit-mask-image: linear-gradient(
    to bottom,
    #000 calc(100% - var(--scroll-fade-h)),
    transparent
  );
  mask-image: linear-gradient(to bottom, #000 calc(100% - var(--scroll-fade-h)), transparent);
}
</style>
