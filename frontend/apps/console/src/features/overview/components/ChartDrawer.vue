<script setup lang="ts">
/**
 * 單一圖表放大查看抽屜（Feature 1）：大尺寸 ECharts + 底部原始資料表 + 來源連結 + PDF 匯出（複用 reportPdf）。
 */
import { computed, ref } from 'vue';
import { Message } from '@arco-design/web-vue';
import VChart from 'vue-echarts';
import { exportBlocksToPdf } from '@/shared/charts';
import { buildOption } from '../registry/chartRegistry';
import type { ChartSpec, GaugeData, BarData } from '../dashboard.types';
import type { TrendData, IntakeBreakdown, ReviewFunnel, CategoryCoverageRow } from '../types';

const visible = defineModel<boolean>('visible', { default: false });
const props = defineProps<{ spec: ChartSpec | null; data?: unknown }>();

const option = computed(() => (props.spec ? buildOption(props.spec.type, props.data) : null));
const sourceUrl = computed(() => props.spec?.source?.dashboardUrl ?? '');
const blockRef = ref<HTMLElement>();
const exporting = ref(false);

/** 依圖型把原始資料攤平成 a-table 的 columns / rows。 */
const table = computed<{ columns: { title: string; dataIndex: string }[]; rows: Record<string, unknown>[] }>(() => {
  const t = props.spec?.type;
  const d = props.data;
  if (t === 'trend') {
    const td = d as TrendData;
    const columns = [{ title: '序列', dataIndex: 'k' }, ...td.months.map((m, i) => ({ title: m, dataIndex: `v${i}` }))];
    const rows = td.series.map((s) => {
      const r: Record<string, unknown> = { k: s.name };
      s.data.forEach((v, i) => (r[`v${i}`] = `${v}${td.unit}`));
      return r;
    });
    return { columns, rows };
  }
  if (t === 'donut') {
    const dd = d as IntakeBreakdown;
    return {
      columns: [{ title: '項目', dataIndex: 'name' }, { title: '占比', dataIndex: 'value' }],
      rows: dd.items.map((it) => ({ name: it.name, value: `${it.value}${dd.unit}` })),
    };
  }
  if (t === 'funnel') {
    const fd = d as ReviewFunnel;
    return {
      columns: [{ title: '階段', dataIndex: 'name' }, { title: '留存', dataIndex: 'value' }],
      rows: fd.stages.map((s) => ({ name: s.name, value: `${s.value}${fd.unit}` })),
    };
  }
  if (t === 'coverage') {
    const rows = (d as CategoryCoverageRow[]).map((r) => ({ name: r.prod, t2: r.tier2, t3: r.tier3 }));
    return {
      columns: [{ title: '類別', dataIndex: 'name' }, { title: 'Tier2', dataIndex: 't2' }, { title: 'Tier3', dataIndex: 't3' }],
      rows,
    };
  }
  if (t === 'bar') {
    const bd = d as BarData;
    return {
      columns: [{ title: '項目', dataIndex: 'name' }, { title: '數值', dataIndex: 'value' }],
      rows: bd.items.map((it) => ({ name: it.name, value: `${it.value}${bd.unit}` })),
    };
  }
  if (t === 'gauge') {
    const g = d as GaugeData;
    return {
      columns: [{ title: '當前', dataIndex: 'v' }, { title: '目標', dataIndex: 'target' }, { title: '上限', dataIndex: 'max' }],
      rows: [{ v: `${g.value}${g.unit}`, target: g.target != null ? `${g.target}${g.unit}` : '—', max: `${g.max}${g.unit}` }],
    };
  }
  return { columns: [], rows: [] };
});

const onExport = async () => {
  if (!blockRef.value || !props.spec) return;
  exporting.value = true;
  try {
    const now = new Date();
    const stamp = now.toLocaleString('zh-TW', { hour12: false });
    await exportBlocksToPdf([blockRef.value], { title: props.spec.title, generatedAt: stamp, filters: [] }, `${props.spec.id}.pdf`);
  } catch (e: any) {
    Message.error('匯出失敗：' + (e?.message || e));
  } finally {
    exporting.value = false;
  }
};
</script>

<template>
  <a-drawer v-model:visible="visible" :width="880" :footer="false" unmount-on-close>
    <template #title>{{ spec?.title ?? '圖表' }}</template>
    <div v-if="spec" ref="blockRef" data-report-block class="bg-white">
      <p v-if="spec.hint" class="mb-2 mt-0 text-sm text-[#86909c]">{{ spec.hint }}</p>
      <v-chart v-if="option" :option="option" autoresize class="h-[420px] w-full" />
      <a-table
        v-if="table.rows.length"
        :columns="table.columns"
        :data="table.rows"
        :pagination="false"
        size="small"
        class="mt-3"
      />
    </div>
    <div class="mt-3 flex items-center gap-3">
      <a-button type="primary" :loading="exporting" @click="onExport">匯出 PDF</a-button>
      <a v-if="sourceUrl" :href="sourceUrl" target="_blank" rel="noopener noreferrer">
        <a-button type="outline">開啟來源儀表板 ↗</a-button>
      </a>
    </div>
  </a-drawer>
</template>
