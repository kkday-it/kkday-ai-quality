<script setup lang="ts">
import { fmtPercent } from '@/utils';
/**
 * 導出實時進度條（純展示；問題列表 / 初判規則 / 圖表 PDF 三處共用）。
 *
 * 進度與狀態由父層驅動（後端 job 走 useExportJob 的 SSE；PDF 走前端逐區塊回報）；本元件只負責畫
 * 進度條 + 停止按鈕 + 文字，emit cancel 讓父層決定如何停止（後端 cancelExport / 前端 shouldCancel 旗標）。
 */
import { computed } from 'vue';

const props = withDefaults(
  defineProps<{
    /** 狀態機：running｜cancelling｜done｜error｜cancelled（父層 useExportJob.status 或本地）。 */
    status: string;
    /** 已完成量。 */
    processed: number;
    /** 總量（0＝尚未算出，顯示「準備中…」）。 */
    total: number;
    /** 進度百分比 0–100。 */
    pct: number;
    /** 進度區文案前綴（如「導出中」）。 */
    label?: string;
  }>(),
  { label: '導出中' },
);

defineEmits<{ (e: 'cancel'): void }>();

/** Arco 進度條狀態色：停止中橙 / 上傳雲端中藍（雖 100% 仍進行中）/ 100% 綠 / 其餘藍。 */
const barStatus = computed(() =>
  props.status === 'cancelling'
    ? 'warning'
    : props.status !== 'uploading' && props.pct >= 100
      ? 'success'
      : 'normal',
);

/**
 * 總量未知（後端仍在查資料）＝不確定進度：此階段是單一 SQL 查詢，後端拿不到「已撈第幾筆」，
 * 沒有可回報的百分比。改以流動條紋表達「進行中但無法計量」，避免與「卡在 0%」混淆
 * （原本這裡照樣印 0.00%，實測 48 秒不動，使用者會以為當掉）。
 */
const indeterminate = computed(() => !props.total && props.status === 'running');

/** 進度文字：查詢資料中（total 未知）/ 停止中 / 上傳雲端中 / 已處理 N/總量。 */
const text = computed(() => {
  if (props.status === 'cancelling') return '停止中…';
  if (props.status === 'uploading') return `${props.label}·上傳 Google Drive…`;
  if (!props.total) return `${props.label}·查詢資料中，總量確認後開始計算進度…`;
  return `${props.label}·已處理 ${props.processed} / ${props.total}`;
});

/** 停止鈕停用：停止已送出、或已進上傳段（bytes 已組完，上傳無中斷點）。 */
const stopDisabled = computed(() => props.status === 'cancelling' || props.status === 'uploading');
</script>

<template>
  <div class="rounded-md border border-[#f0f0f0] bg-white px-4 py-3">
    <div class="flex items-center gap-3">
      <a-progress
        class="flex-1"
        :class="{ 'is-indeterminate': indeterminate }"
        :percent="indeterminate ? 1 : pct / 100"
        :status="barStatus"
      >
        <template #text="{ percent }">
          {{ indeterminate ? '準備中…' : fmtPercent(percent) }}
        </template>
      </a-progress>
      <a-popconfirm
        content="確定停止導出？已產生部分不保留，可稍後重新導出。"
        @ok="$emit('cancel')"
      >
        <a-button size="small" status="danger" :disabled="stopDisabled">
          {{ status === 'cancelling' ? '停止中…' : '停止' }}
        </a-button>
      </a-popconfirm>
    </div>
    <div class="mt-1 text-xs text-gray-500">{{ text }}</div>
  </div>
</template>

<style scoped>
/*
 * 不確定進度的流動條紋。Arco a-progress 沒有 indeterminate 模式，條紋要疊在它內部的
 * bar 元素上——該元素由 Arco 自己渲染，Tailwind utility 與元件 prop 都觸及不到，
 * 故此處為樣式鐵律允許的 :deep() 情形（第 3 順位）。
 */
.is-indeterminate :deep(.arco-progress-line-bar) {
  background-image: linear-gradient(
    115deg,
    rgb(var(--primary-6)) 25%,
    rgb(var(--primary-4)) 25%,
    rgb(var(--primary-4)) 50%,
    rgb(var(--primary-6)) 50%,
    rgb(var(--primary-6)) 75%,
    rgb(var(--primary-4)) 75%
  );
  background-size: 32px 100%;
  animation: aiq-indeterminate-stripes 0.8s linear infinite;
}

@keyframes aiq-indeterminate-stripes {
  from {
    background-position: 0 0;
  }
  to {
    background-position: 32px 0;
  }
}

/* 動效敏感者關閉動畫：條紋靜止但仍與確定進度視覺可辨（WCAG 2.3.3） */
@media (prefers-reduced-motion: reduce) {
  .is-indeterminate :deep(.arco-progress-line-bar) {
    animation: none;
  }
}
</style>
