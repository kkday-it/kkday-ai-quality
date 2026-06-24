<script setup lang="ts">
import { ref, onMounted, computed, reactive } from 'vue';
import VChart from 'vue-echarts';
import { use } from 'echarts/core';
import { HeatmapChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, VisualMapComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { countBy, maxBy } from 'lodash-es';
import { getFindings } from '@/api';
import { StateGuard, CardSection } from '@/components';
import { FindingCard, KpiCard } from '../components';
import {
  DIMS,
  VERDICT_KEYS as VKEYS,
  VERDICT_LABELS as VLABEL,
  STATUS_OPTS,
  CONTENT_VERDICTS as CONTENT,
} from '../constants';
import { flatFinding as flat } from '../utils';

use([HeatmapChart, GridComponent, TooltipComponent, VisualMapComponent, CanvasRenderer]);

// 維度規則覆蓋對照與缺口門檻：屬本頁分析邏輯，不外溢至共用 constants。
const RULE_COVERED: Record<string, boolean> = { 行程流程: true };
const GAP_THRESHOLD = 2;

const all = ref<any[]>([]);
const loading = ref(true);
const error = ref('');

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
  const byDim = countBy(
    all.value.filter((f) => f.dimension !== 'non_content'),
    'dimension',
  );
  const top = maxBy(Object.entries(byDim), ([, n]) => n);
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

// ── 「問題明細」篩選器（與上半部圖表解耦）：填條件 → 搜索套用 / 重置，再分頁 ──
// 維度/verdict/狀態＝下拉；prod/pkg/order/supplier OID＝輸入子字串。draft=輸入中，applied=已套用。
type Filt = { dim: string; verdict: string; status: string; prod: string; pkg: string; order: string; supplier: string };
const emptyFilt = (): Filt => ({ dim: '', verdict: '', status: '', prod: '', pkg: '', order: '', supplier: '' });
const draft = reactive<Filt>(emptyFilt());
const applied = reactive<Filt>(emptyFilt());
const idMatch = (val: unknown, q: string) =>
  !q.trim() || String(val ?? '').toLowerCase().includes(q.trim().toLowerCase());
const filtered = computed(() =>
  all.value.filter((f) =>
    (!applied.dim || f.dimension === applied.dim) &&
    (!applied.verdict || f.verdict === applied.verdict) &&
    (!applied.status || f.status === applied.status) &&
    idMatch(f.prod_oid, applied.prod) &&
    idMatch(f.pkg_oid, applied.pkg) &&
    idMatch(f.order_oid, applied.order) &&
    idMatch(f.supplier_oid, applied.supplier),
  ),
);
const doSearch = () => { Object.assign(applied, draft); page.value = 1; };
const doReset = () => { Object.assign(draft, emptyFilt()); Object.assign(applied, emptyFilt()); page.value = 1; };

// 分頁（內容區內部滾動，分頁固定底部）
const page = ref(1);
const pageSize = ref(10);
const paged = computed(() => {
  const start = (page.value - 1) * pageSize.value;
  return filtered.value.slice(start, start + pageSize.value);
});
const onPageSizeChange = (size: number) => { pageSize.value = size; page.value = 1; };

const option = computed(() => ({
  tooltip: { position: 'top' },
  grid: { height: '62%', top: '6%', left: '16%', right: '4%' },
  xAxis: { type: 'category', data: VLABEL, splitArea: { show: true } },
  yAxis: { type: 'category', data: DIMS, splitArea: { show: true } },
  visualMap: { min: 0, max: maxN.value, calculable: true, orient: 'horizontal', left: 'center', bottom: '2%' },
  series: [{ type: 'heatmap', data: DIMS.flatMap((d, di) => VKEYS.map((v, vi) => [vi, di, mtx.value[d][v]])), label: { show: true } }],
}));

const gaps = computed(() =>
  DIMS.map((d) => ({ d, pain: mtx.value[d].content_missing + mtx.value[d].content_unclear }))
    .filter((g) => g.pain >= GAP_THRESHOLD && !RULE_COVERED[g.d])
    .sort((a, b) => b.pain - a.pain),
);

// 上半部（KPI 卡 / 規則缺口）點擊 → 開「獨立抽屜」查看子集，不寫入下方「問題明細」篩選器（解耦）
const drawerVisible = ref(false);
const drawerKind = ref('all');
const drawerDim = ref('');
const openDrawer = (kind: string) => { drawerKind.value = kind; drawerVisible.value = true; };
const openDimDrawer = (d: string) => { drawerDim.value = d; drawerKind.value = 'dim'; drawerVisible.value = true; };
const drawerList = computed(() => {
  if (drawerKind.value === 'dim') return all.value.filter((f) => f.dimension === drawerDim.value);
  if (drawerKind.value === 'content') return all.value.filter((f) => CONTENT.has(f.verdict));
  if (drawerKind.value === 'topdim') return all.value.filter((f) => f.dimension === kpi.value.topDim);
  if (drawerKind.value === 'lowconf') return all.value.filter((f) => f.status === 'new' && f.confidence < 0.7 && CONTENT.has(f.verdict));
  return all.value;
});
const drawerTitle = computed(() => {
  if (drawerKind.value === 'dim') return `規則缺口 · ${drawerDim.value}`;
  const m: Record<string, string> = { all: '全部 Findings', content: '確認為內容問題', topdim: `最痛維度 · ${kpi.value.topDim}`, lowconf: '低信心待人工' };
  return m[drawerKind.value] || '';
});
</script>

<template>
  <StateGuard
    :loading="loading"
    :error="error"
    :empty="!all.length"
    empty-text="尚無判決資料，請至「PM／AM 單品」拉評論判決"
  >
    <div>
      <a-row :gutter="14" class="mb-4">
        <a-col :span="6"><KpiCard label="本期 Findings" :value="kpi.total" subtext="點擊查看全部 →" @click="openDrawer('all')" /></a-col>
        <a-col :span="6"><KpiCard label="確認為內容問題" :value="kpi.contentPct" unit="%" subtext="點擊查看內容問題 →" @click="openDrawer('content')" /></a-col>
        <a-col :span="6"><KpiCard label="最痛維度" :value="kpi.topDim" :subtext="`${kpi.topN} 筆 · 點擊查看 →`" @click="openDrawer('topdim')" /></a-col>
        <a-col :span="6"><KpiCard label="低信心待人工" :value="kpi.lowConf" subtext="點擊查看 →" @click="openDrawer('lowconf')" /></a-col>
      </a-row>

      <CardSection title="維度 × verdict 熱力矩陣（概覽）" hint="問題分布熱點概覽 · 查看明細請用下方「問題明細」篩選器" class="mb-4">
        <v-chart :option="option" class="h-[440px]" autoresize />
      </CardSection>

      <CardSection title="⚠ 規則缺口" hint="高頻缺漏/模糊但無對應規則 → 補 rules.json 閉環" class="mb-4">
        <a-empty v-if="!gaps.length" description="目前無明顯規則缺口" />
        <div
          v-for="g in gaps"
          :key="g.d"
          class="mb-2 flex items-center gap-2.5 rounded-lg border border-l-[3px] border-[#f0f0f0] border-l-[#fb923c] px-3 py-2.5"
        >
          <b>{{ g.d }}</b>
          <span class="text-xs text-[#86909c]">缺漏+模糊 {{ g.pain }} 筆 · 現有規則無對應</span>
          <a-link class="ml-auto" @click="openDimDrawer(g.d)">查看明細 →</a-link>
        </div>
      </CardSection>

      <a-card
        title="問題明細"
        class="flex max-h-[calc(100vh-140px)] flex-col"
        :header-style="{ height: 'auto', paddingTop: '20px', paddingBottom: '20px' }"
        :body-style="{ flex: '1', minHeight: '0', display: 'flex', flexDirection: 'column', paddingTop: '12px' }"
      >
        <template #extra>
          <a-space wrap>
            <a-select v-model="draft.dim" placeholder="維度" allow-clear size="small" class="w-[112px]" @change="doSearch">
              <a-option v-for="d in DIMS" :key="d" :value="d">{{ d }}</a-option>
            </a-select>
            <a-select v-model="draft.verdict" placeholder="verdict" allow-clear size="small" class="w-[108px]" @change="doSearch">
              <a-option v-for="(l, i) in VLABEL" :key="VKEYS[i]" :value="VKEYS[i]">{{ l }}</a-option>
            </a-select>
            <a-select v-model="draft.status" placeholder="狀態" allow-clear size="small" class="w-[100px]" @change="doSearch">
              <a-option v-for="s in STATUS_OPTS" :key="s.k" :value="s.k">{{ s.l }}</a-option>
            </a-select>
            <a-input v-model="draft.prod" placeholder="商品 prod_oid" allow-clear size="small" class="w-[128px]" @press-enter="doSearch" @clear="doSearch" />
            <a-input v-model="draft.pkg" placeholder="方案 pkg_oid" allow-clear size="small" class="w-[128px]" @press-enter="doSearch" @clear="doSearch" />
            <a-input v-model="draft.order" placeholder="訂單 order_oid" allow-clear size="small" class="w-[136px]" @press-enter="doSearch" @clear="doSearch" />
            <a-input v-model="draft.supplier" placeholder="供應商 supplier_oid" allow-clear size="small" class="w-[148px]" @press-enter="doSearch" @clear="doSearch" />
            <a-button type="primary" size="small" @click="doSearch">搜索</a-button>
            <a-button size="small" @click="doReset">重置</a-button>
          </a-space>
        </template>

        <div class="min-h-0 flex-1 overflow-y-auto px-1 pt-1">
          <a-empty v-if="!filtered.length" description="無符合條件的問題，可調整篩選或重置" class="p-10" />
          <FindingCard v-for="f in paged" :key="f.finding_id" :f="f" />
        </div>
        <div v-if="filtered.length" class="mt-1 flex flex-none justify-end border-t border-[#f0f0f0] pt-3">
          <a-pagination
            v-model:current="page"
            :total="filtered.length"
            :page-size="pageSize"
            :page-size-options="[10, 20, 50, 100]"
            size="small"
            show-total
            show-jumper
            show-page-size
            @page-size-change="onPageSizeChange"
          />
        </div>
      </a-card>

      <a-drawer v-model:visible="drawerVisible" :width="760" :title="drawerTitle" unmount-on-close>
        <div class="mb-3 text-xs text-[#86909c]">共 {{ drawerList.length }} 筆</div>
        <a-empty v-if="!drawerList.length" description="無資料" />
        <FindingCard v-for="f in drawerList" :key="f.finding_id" :f="f" />
      </a-drawer>
    </div>
  </StateGuard>
</template>
