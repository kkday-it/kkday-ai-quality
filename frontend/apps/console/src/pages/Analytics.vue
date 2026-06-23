<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import VChart from 'vue-echarts';
import { use } from 'echarts/core';
import { HeatmapChart, PieChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, VisualMapComponent, LegendComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { getFindings } from '../api/client';
import FindingCard from '../components/FindingCard.vue';

use([HeatmapChart, PieChart, GridComponent, TooltipComponent, VisualMapComponent, LegendComponent, CanvasRenderer]);

const SRC_CH: Record<string, string> = { A_platform: '平台主動', B_customer: '客人進線', C_supplier: '供應商申訴', unknown: '其他' };

const DIMS = ['商品定位', '行程流程', '費用資訊', '集合資訊', '使用兌換', '成團條件', '限制與風險', '承諾與SLA'];
const VKEYS = ['real_config_issue', 'content_missing', 'content_unclear', 'customer_misread', 'escalate_ops'];
const VLABEL = ['設定錯誤', '缺漏', '模糊', '客戶誤解', '非內容'];
const CONTENT = new Set(['real_config_issue', 'content_missing', 'content_unclear']);
const RULE_COVERED: Record<string, boolean> = { 行程流程: true };
const GAP_THRESHOLD = 2;

const all = ref<any[]>([]);
const loading = ref(true);
const error = ref('');
const flat = (r: any) => ({ ...r.finding, finding_id: r.finding_id, prod_oid: r.prod_oid, dimension: r.dimension, verdict: r.verdict, confidence: r.confidence, status: r.status });

onMounted(async () => {
  try {
    all.value = (await getFindings()).map(flat);
  } catch (e: any) {
    error.value = '載入失敗：' + (e?.message || e);
  } finally {
    loading.value = false;
  }
});

const kpi = computed(() => {
  const t = all.value.length;
  const content = all.value.filter((f) => CONTENT.has(f.verdict)).length;
  const byDim: Record<string, number> = {};
  all.value.forEach((f) => { if (f.dimension !== 'non_content') byDim[f.dimension] = (byDim[f.dimension] || 0) + 1; });
  const top = Object.entries(byDim).sort((a, b) => b[1] - a[1])[0];
  const lowConf = all.value.filter((f) => f.status === 'new' && f.confidence < 0.7 && CONTENT.has(f.verdict)).length;
  return { total: t, contentPct: t ? Math.round((content / t) * 100) : 0, topDim: top ? top[0] : '—', topN: top ? top[1] : 0, lowConf };
});

const mtx = computed(() => {
  const m: Record<string, Record<string, number>> = {};
  DIMS.forEach((d) => { m[d] = {}; VKEYS.forEach((v) => (m[d][v] = 0)); });
  all.value.forEach((f) => { if (m[f.dimension]) m[f.dimension][f.verdict]++; });
  return m;
});
const maxN = computed(() => Math.max(1, ...DIMS.flatMap((d) => VKEYS.map((v) => mtx.value[d][v]))));

const drillDim = ref('');
const drillV = ref('');
const drillList = computed(() => (!drillDim.value ? [] : all.value.filter((f) => f.dimension === drillDim.value && f.verdict === drillV.value)));

const option = computed(() => ({
  tooltip: { position: 'top' },
  grid: { height: '62%', top: '6%', left: '16%', right: '4%' },
  xAxis: { type: 'category', data: VLABEL, splitArea: { show: true } },
  yAxis: { type: 'category', data: DIMS, splitArea: { show: true } },
  visualMap: { min: 0, max: maxN.value, calculable: true, orient: 'horizontal', left: 'center', bottom: '2%' },
  series: [{ type: 'heatmap', data: DIMS.flatMap((d, di) => VKEYS.map((v, vi) => [vi, di, mtx.value[d][v]])), label: { show: true } }],
}));
const onClick = (p: any) => {
  if (!p?.data) return;
  const [vi, di] = p.data;
  if (mtx.value[DIMS[di]][VKEYS[vi]] > 0) { drillDim.value = DIMS[di]; drillV.value = VKEYS[vi]; }
};

const gaps = computed(() =>
  DIMS.map((d) => ({ d, pain: mtx.value[d].content_missing + mtx.value[d].content_unclear }))
    .filter((g) => g.pain >= GAP_THRESHOLD && !RULE_COVERED[g.d])
    .sort((a, b) => b.pain - a.pain),
);

// 感知層：問題來源分布（管道/系統）
const sourceStats = computed(() => {
  const m: Record<string, number> = {};
  all.value.forEach((f) => {
    const k = f.source_system || SRC_CH[f.source_channel] || '其他';
    m[k] = (m[k] || 0) + 1;
  });
  return Object.entries(m).map(([name, value]) => ({ name, value }));
});
const sourceOption = computed(() => ({
  tooltip: { trigger: 'item' },
  legend: { bottom: 0 },
  series: [{ type: 'pie', radius: ['40%', '68%'], data: sourceStats.value, label: { formatter: '{b}：{c}' } }],
}));
</script>

<template>
  <a-alert v-if="error" type="error" style="margin-bottom: 16px">{{ error }}</a-alert>
  <a-spin v-else-if="loading" style="display: block; text-align: center; padding: 60px" />
  <a-empty v-else-if="!all.length" description="尚無判決資料，請至「單品診斷」拉評論判決" style="padding: 60px" />
  <div v-else>
    <a-row :gutter="14" style="margin-bottom: 16px">
      <a-col :span="6"><a-card><a-statistic title="本期 Findings" :value="kpi.total" /></a-card></a-col>
      <a-col :span="6"><a-card><a-statistic title="確認為內容問題" :value="kpi.contentPct" suffix="%" /></a-card></a-col>
      <a-col :span="6"><a-card><div class="kl">最痛維度</div><div class="kv">{{ kpi.topDim }}</div><div class="ks">{{ kpi.topN }} 筆</div></a-card></a-col>
      <a-col :span="6"><a-card><a-statistic title="低信心待人工" :value="kpi.lowConf" /></a-card></a-col>
    </a-row>

    <a-card title="維度 × verdict 熱力矩陣" style="margin-bottom: 16px">
      <template #extra><span class="muted">點任一格下鑽</span></template>
      <v-chart :option="option" style="height: 440px" autoresize @click="onClick" />
    </a-card>

    <a-card title="⚠ 規則缺口" style="margin-bottom: 16px">
      <template #extra><span class="muted">高頻缺漏/模糊但無對應規則 → 補 rules.json 閉環</span></template>
      <a-empty v-if="!gaps.length" description="目前無明顯規則缺口" />
      <div v-for="g in gaps" :key="g.d" class="gap">
        <b>{{ g.d }}</b>
        <span class="muted">缺漏+模糊 {{ g.pain }} 筆 · 現有規則無對應</span>
        <a-link style="margin-left: auto">建議新增審核規則 →</a-link>
      </div>
    </a-card>

    <a-card title="感知層問題來源分布" style="margin-bottom: 16px">
      <template #extra><span class="muted">工單 / 評論 / 訂單訊息… 各來源占比</span></template>
      <v-chart :option="sourceOption" style="height: 300px" autoresize />
    </a-card>

    <a-card :title="drillDim ? `下鑽明細 · ${drillDim} × ${VLABEL[VKEYS.indexOf(drillV)]}` : '下鑽明細'">
      <a-empty v-if="!drillDim" description="點上方熱力矩陣任一格查看該類工單" />
      <template v-else>
        <div class="muted" style="margin-bottom: 10px">{{ drillList.length }} 筆</div>
        <FindingCard v-for="f in drillList" :key="f.finding_id" :f="f" />
      </template>
    </a-card>
  </div>
</template>

<style scoped>
.muted { color: #86909c; font-size: 12px; }
.gap { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border: 1px solid #f0f0f0; border-left: 3px solid #fb923c; border-radius: 8px; margin-bottom: 8px; }
.kl { color: #86909c; font-size: 13px; }
.kv { font-size: 24px; font-weight: 600; margin-top: 4px; }
.ks { color: #86909c; font-size: 12px; }
</style>
