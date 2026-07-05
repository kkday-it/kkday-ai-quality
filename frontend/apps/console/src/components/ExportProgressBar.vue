<script setup lang="ts">
/**
 * 導出實時進度條（純展示；問題列表 / 判決規則 / 圖表 PDF 三處共用）。
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

/** Arco 進度條狀態色：停止中橙 / 100% 綠 / 其餘藍。 */
const barStatus = computed(() =>
  props.status === 'cancelling' ? 'warning' : props.pct >= 100 ? 'success' : 'normal',
);

/** 進度文字：準備中（total 未知）/ 停止中 / 已處理 N/總量。 */
const text = computed(() => {
  if (props.status === 'cancelling') return '停止中…';
  if (!props.total) return `${props.label}·準備中…`;
  return `${props.label}·已處理 ${props.processed} / ${props.total}`;
});
</script>

<template>
  <div class="rounded-md border border-[#f0f0f0] bg-white px-4 py-3">
    <div class="flex items-center gap-3">
      <a-progress class="flex-1" :percent="pct / 100" :status="barStatus" />
      <a-popconfirm content="確定停止導出？已產生部分不保留，可稍後重新導出。" @ok="$emit('cancel')">
        <a-button size="small" status="danger" :disabled="status === 'cancelling'">
          {{ status === 'cancelling' ? '停止中…' : '停止' }}
        </a-button>
      </a-popconfirm>
    </div>
    <div class="mt-1 text-xs text-gray-500">{{ text }}</div>
  </div>
</template>
