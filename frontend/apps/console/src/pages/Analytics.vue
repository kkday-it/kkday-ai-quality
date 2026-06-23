<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import VChart from 'vue-echarts';
import { use } from 'echarts/core';
import { HeatmapChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, VisualMapComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { getAggregate } from '../api/client';

use([HeatmapChart, GridComponent, TooltipComponent, VisualMapComponent, CanvasRenderer]);

const DIMS = ['商品定位', '行程流程', '費用資訊', '集合資訊', '使用兌換', '成團條件', '限制與風險', '承諾與SLA', 'non_content'];
const VERDICTS = ['real_config_issue', 'content_missing', 'content_unclear', 'customer_misread', 'escalate_ops'];

const kpi = ref<Record<string, any>>({});
const matrix = ref<Array<{ dimension: string; verdict: string; count: number }>>([]);

onMounted(async () => {
  const a = await getAggregate();
  kpi.value = a.kpi || {};
  matrix.value = a.matrix || [];
});

const option = computed(() => ({
  tooltip: { position: 'top' },
  grid: { height: '62%', top: '6%', left: '16%', right: '4%' },
  xAxis: { type: 'category', data: VERDICTS, splitArea: { show: true }, axisLabel: { rotate: 25 } },
  yAxis: { type: 'category', data: DIMS, splitArea: { show: true } },
  visualMap: {
    min: 0,
    max: Math.max(1, ...matrix.value.map((m) => m.count)),
    calculable: true,
    orient: 'horizontal',
    left: 'center',
    bottom: '2%',
  },
  series: [
    {
      type: 'heatmap',
      data: matrix.value.map((m) => [VERDICTS.indexOf(m.verdict), DIMS.indexOf(m.dimension), m.count]),
      label: { show: true },
      emphasis: { itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0,0,0,0.4)' } },
    },
  ],
}));
</script>

<template>
  <div>
    <a-row :gutter="16" style="margin-bottom: 16px">
      <a-col :span="8"><a-card><a-statistic title="Findings 總數" :value="kpi.total || 0" /></a-card></a-col>
      <a-col :span="8"><a-card><a-statistic title="內容問題占比" :value="(kpi.content_issue_pct || 0) * 100" :precision="1" suffix="%" /></a-card></a-col>
      <a-col :span="8"><a-card>
        <div style="color: #86909c; font-size: 13px; margin-bottom: 6px">最痛 dimension</div>
        <div style="font-size: 24px; font-weight: 600">{{ kpi.top_dimension || '-' }}</div>
      </a-card></a-col>
    </a-row>
    <a-card title="dimension × verdict 熱力矩陣">
      <v-chart :option="option" style="height: 480px" autoresize />
    </a-card>
  </div>
</template>
