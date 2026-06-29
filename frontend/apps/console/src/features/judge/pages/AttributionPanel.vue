<script setup lang="ts">
/**
 * 歸因總覽儀表板（多面板 · 交叉篩選 · 分區）。
 *
 * 互動模型（參照 Superset / Tableau / Grafana 的 linked-brushing）：
 *   - 全域篩選狀態 `f`（判定層/歸因域/verdict/來源/嚴重度/信心分層）為單一真相；
 *   - 任一圖點擊＝對應維度 toggle 進 `f` → 所有面板 + 明細表連動重算；
 *   - 頂部「篩選膠囊」顯示當前條件、可逐一移除；KPI 卡與下拉同樣寫 `f`。
 * 脊椎＝判定層；標籤一律讀 config/taxonomy；ai_review_summary 為加值層不列歸因來源。
 *
 * ⚠️ 判決層重建前 judgments 為空，先用內建 SAMPLE；接回後把 `rows` 換成 getFindings()。
 */
import { reactive, ref, computed } from 'vue';
import VChart from 'vue-echarts';
import { use } from 'echarts/core';
import { PieChart, BarChart, HeatmapChart, TreemapChart, SankeyChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, VisualMapComponent, LegendComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { IconList } from '@arco-design/web-vue/es/icon';
import { CardSection } from '@/components';
import domainsCfg from '@config/taxonomy/domains.json';
import verdictsCfg from '@config/taxonomy/verdicts.json';
import tiersCfg from '@config/taxonomy/judgment_tiers.json';
import dimsCfg from '@config/taxonomy/dimensions.json';
import sevsCfg from '@config/taxonomy/severities.json';

use([
  PieChart, BarChart, HeatmapChart, TreemapChart, SankeyChart,
  GridComponent, TooltipComponent, VisualMapComponent, LegendComponent, CanvasRenderer,
]);

// ── config 對照（label 單一真相）+ 反查 ────────────────────────────
const domByCode = Object.fromEntries(domainsCfg.items.map((d) => [d.code, d]));
const domBySym = Object.fromEntries(domainsCfg.items.map((d) => [d.symbol, d]));
const vdByCode = Object.fromEntries(verdictsCfg.items.map((v) => [v.code, v]));
const dimLabel = Object.fromEntries(dimsCfg.items.map((d) => [d.code, d.label_zh]));
const SEVS = sevsCfg.items.map((s) => s.code);
const tierByCode = Object.fromEntries(tiersCfg.items.map((t) => [t.code, t.label_zh]));
const tierLabel2code = Object.fromEntries(tiersCfg.items.map((t) => [t.label_zh, t.code]));
const vdLabel2code = Object.fromEntries(verdictsCfg.items.map((v) => [v.label_zh, v.code]));
const tierOf = (() => {
  const m: Record<string, { code: string; label: string; owner: string }> = {};
  for (const t of tiersCfg.items)
    for (const v of t.verdicts) m[v] = { code: t.code, label: t.label_zh, owner: t.owner };
  return m;
})();
const DOMAIN_COLORS = ['#5b8ff9', '#61ddaa', '#f6bd16', '#e8684a', '#6dc8ec', '#9270ca', '#ff9d4d'];
const domainMeta = domainsCfg.items.map((d, i) => ({ ...d, color: DOMAIN_COLORS[i % DOMAIN_COLORS.length] }));
const colorBySym = Object.fromEntries(domainMeta.map((d) => [d.symbol, d.color]));
const symOf = (domLabel: string) => domLabel?.split(' ')[0] ?? '';

type Row = { v: string; dom?: string; dim?: string; src: string; sev: string; conf: number; n: number };
const SAMPLE: Row[] = [
  { v: 'content_missing', dim: 'meetup', src: 'conversations', sev: 'P1', conf: 0.42, n: 38 },
  { v: 'content_missing', dim: 'sla', src: 'freshdesk_tickets', sev: 'P0', conf: 0.55, n: 21 },
  { v: 'content_unclear', dim: 'fee', src: 'conversations', sev: 'P2', conf: 0.68, n: 44 },
  { v: 'content_unclear', dim: 'itinerary', src: 'product_reviews', sev: 'P2', conf: 0.6, n: 29 },
  { v: 'real_config_issue', dim: 'positioning', src: 'product_reviews', sev: 'P3', conf: 0.72, n: 17 },
  { v: 'contract_breach', src: 'product_reviews', sev: 'P0', conf: 0.58, n: 33 },
  { v: 'contract_breach', src: 'app_feedback', sev: 'P1', conf: 0.49, n: 12 },
  { v: 'escalate_ops', dom: 'platform', src: 'app_feedback', sev: 'P2', conf: 0.8, n: 51 },
  { v: 'escalate_ops', dom: 'order', src: 'mixpanel_tracker', sev: 'P2', conf: 0.85, n: 24 },
  { v: 'escalate_ops', dom: 'cs', src: 'conversations', sev: 'P1', conf: 0.7, n: 40 },
  { v: 'customer_misread', src: 'conversations', sev: 'P3', conf: 0.9, n: 26 },
  { v: 'force_majeure', src: 'conversations', sev: 'P1', conf: 0.95, n: 9 },
  { v: 'pre_sale_inquiry', src: 'conversations', sev: 'P3', conf: 0.88, n: 60 },
];
const SRC_LABEL: Record<string, string> = {
  conversations: '售前售後進線',
  freshdesk_tickets: '工單',
  product_reviews: '商品評論',
  app_feedback: 'App 回饋',
  mixpanel_tracker: '埋點',
};
const srcLabel2code = Object.fromEntries(Object.entries(SRC_LABEL).map(([c, l]) => [l, c]));
const bandOf = (c: number) => (c >= 0.8 ? '自動採信(≥.8)' : c >= 0.5 ? 'jury 覆核(.5–.7)' : 'HOLD(<.5)');

const rows = computed(() =>
  SAMPLE.map((r, i) => {
    const dom = domByCode[r.dom ?? vdByCode[r.v]?.domain ?? ''];
    const t = tierOf[r.v];
    return {
      i, ...r,
      tier: t?.label ?? '—', tierCode: t?.code ?? 'NP', owner: t?.owner ?? '',
      domSym: dom?.symbol ?? '—', domLabel: dom ? `${dom.symbol} ${dom.label_zh}` : '—',
      vLabel: vdByCode[r.v]?.label_zh ?? r.v,
      dimLabel: r.dim ? dimLabel[r.dim] : '', srcLabel: SRC_LABEL[r.src] ?? r.src, band: bandOf(r.conf),
    };
  }),
);

// ── 全域篩選狀態（cross-filter 單一真相）──────────────────────────
const f = reactive({ tier: '', domain: '', verdict: '', source: [] as string[], severity: '', band: '' });
const view = computed(() =>
  rows.value.filter(
    (r) =>
      (!f.tier || r.tierCode === f.tier) &&
      (!f.domain || r.domSym === f.domain) &&
      (!f.verdict || r.v === f.verdict) &&
      (!f.source.length || f.source.includes(r.src)) &&
      (!f.severity || r.sev === f.severity) &&
      (!f.band || r.band === f.band),
  ),
);
const reset = () => Object.assign(f, { tier: '', domain: '', verdict: '', source: [], severity: '', band: '' });
const toggle = (k: 'tier' | 'domain' | 'verdict' | 'severity' | 'band', v: string) => {
  if (!v) return;
  f[k] = f[k] === v ? '' : v;
};
const toggleSrc = (code: string) => {
  if (!code) return;
  f.source = f.source.includes(code) ? f.source.filter((x) => x !== code) : [...f.source, code];
};

// 篩選膠囊
const chips = computed(() => {
  const out: { key: string; label: string; clear: () => void }[] = [];
  if (f.tier) out.push({ key: 'tier', label: `判定層：${tierByCode[f.tier]}`, clear: () => (f.tier = '') });
  if (f.domain) out.push({ key: 'dom', label: `歸因域：${f.domain} ${domBySym[f.domain]?.label_zh ?? ''}`, clear: () => (f.domain = '') });
  if (f.verdict) out.push({ key: 'vd', label: `verdict：${vdByCode[f.verdict]?.label_zh}`, clear: () => (f.verdict = '') });
  if (f.severity) out.push({ key: 'sev', label: `嚴重度：${f.severity}`, clear: () => (f.severity = '') });
  if (f.band) out.push({ key: 'band', label: `信心：${f.band}`, clear: () => (f.band = '') });
  for (const s of f.source) out.push({ key: 'src-' + s, label: `來源：${SRC_LABEL[s]}`, clear: () => toggleSrc(s) });
  return out;
});

// ── 聚合 + 圖型 helper ───────────────────────────────────────────
const sumKey = (key: string, src = view.value) => {
  const m: Record<string, number> = {};
  for (const r of src) {
    const k = (r as any)[key];
    if (k) m[k] = (m[k] || 0) + r.n;
  }
  return m;
};

// ── 面板 option ─────────────────────────────────────────────────
const levelOption = computed(() => {
  const tree: any = {};
  for (const r of view.value) {
    const a = (tree[r.tier] ||= { name: r.tier, _c: {} });
    const b = (a._c[r.domLabel] ||= { name: r.domLabel, _c: {} });
    const c = (b._c[r.vLabel] ||= { name: r.vLabel, value: 0 });
    c.value += r.n;
  }
  const toNodes = (o: any): any[] =>
    Object.values(o).map((x: any) => (x._c ? { name: x.name, children: toNodes(x._c) } : { name: x.name, value: x.value }));
  return {
    tooltip: { formatter: (p: any) => `${p.name}：${p.value ?? ''}` },
    series: [{
      type: 'treemap', data: toNodes(tree), leafDepth: 2, roam: false, nodeClick: 'zoomToNode',
      breadcrumb: { show: true, bottom: 2, height: 22 },
      label: { show: true, formatter: '{b}  {c}', fontSize: 12, overflow: 'truncate' },
      upperLabel: { show: true, height: 22, color: '#fff', fontSize: 12 },
      levels: [
        { itemStyle: { borderColor: '#fff', borderWidth: 3, gapWidth: 3 } },
        { colorSaturation: [0.35, 0.55], itemStyle: { gapWidth: 2, borderColorSaturation: 0.6 } },
        { colorSaturation: [0.3, 0.5], itemStyle: { gapWidth: 1, borderColorSaturation: 0.6 } },
      ],
    }],
  };
});

const sankeyOption = computed(() => {
  const linkMap: Record<string, number> = {};
  const add = (s: string, t: string, n: number) => (linkMap[`${s}|${t}`] = (linkMap[`${s}|${t}`] || 0) + n);
  for (const r of view.value) {
    add(r.srcLabel, r.tier, r.n);
    add(r.tier, r.owner, r.n);
  }
  const names = new Set<string>();
  const links = Object.entries(linkMap).map(([k, value]) => {
    const [source, target] = k.split('|');
    names.add(source); names.add(target);
    return { source, target, value };
  });
  return { tooltip: { trigger: 'item' }, series: [{ type: 'sankey', data: [...names].map((name) => ({ name })), links, label: { fontSize: 11 }, lineStyle: { color: 'gradient', opacity: 0.4 } }] };
});

const heatmapOption = computed(() => {
  const doms = domainsCfg.items.map((d) => `${d.symbol} ${d.label_zh}`);
  const verds = verdictsCfg.items.map((x) => x.label_zh);
  const idxD = Object.fromEntries(domainsCfg.items.map((d, i) => [d.symbol, i]));
  const idxV = Object.fromEntries(verdictsCfg.items.map((x, i) => [x.code, i]));
  const acc: Record<string, number> = {};
  for (const r of view.value) acc[`${r.domSym}|${r.v}`] = (acc[`${r.domSym}|${r.v}`] || 0) + r.n;
  const data: [number, number, number][] = [];
  for (const [k, val] of Object.entries(acc)) {
    const [ds, vc] = k.split('|');
    if (idxD[ds] != null && idxV[vc] != null) data.push([idxV[vc], idxD[ds], val]);
  }
  return {
    tooltip: { position: 'top' },
    grid: { left: 4, right: 8, top: 8, bottom: 64, containLabel: true },
    xAxis: { type: 'category', data: verds, axisLabel: { interval: 0, rotate: 32, fontSize: 10 } },
    yAxis: { type: 'category', data: doms, axisLabel: { fontSize: 10 } },
    visualMap: { min: 0, max: Math.max(1, ...data.map((d) => d[2])), calculable: true, orient: 'horizontal', left: 'center', bottom: 0 },
    series: [{ type: 'heatmap', data, label: { show: true, fontSize: 10 } }],
  };
});

const sevStackOption = computed(() => {
  const tiers = tiersCfg.items.map((t) => t.label_zh);
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { bottom: 0, textStyle: { fontSize: 11 } },
    grid: { left: 4, right: 8, top: 16, bottom: 36, containLabel: true },
    xAxis: { type: 'category', data: tiers, axisLabel: { interval: 0, rotate: 18, fontSize: 10 } },
    yAxis: { type: 'value' },
    series: SEVS.map((s) => ({ name: s, type: 'bar', stack: 'sev', data: tiers.map((tl) => view.value.filter((r) => r.tier === tl && r.sev === s).reduce((a, r) => a + r.n, 0)) })),
  };
});

const aspectBarOption = computed(() => {
  const m = sumKey('dimLabel', view.value.filter((r) => r.dimLabel));
  const ent = Object.entries(m).sort((a, b) => b[1] - a[1]);
  return {
    tooltip: { trigger: 'axis' }, grid: { left: 4, right: 12, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: 'value' }, yAxis: { type: 'category', data: ent.map((e) => e[0]).reverse(), axisLabel: { fontSize: 11 } },
    series: [{ type: 'bar', data: ent.map((e) => e[1]).reverse(), barMaxWidth: 22, label: { show: true, position: 'right' } }],
  };
});

// 自訂樞紐＝「任一維度佔比」探索器（取代原歸因域/來源/信心三個固定圓餅）
const pvChart = ref<'pie' | 'bar' | 'treemap'>('pie');
const pvDim = ref<'tier' | 'domLabel' | 'vLabel' | 'srcLabel' | 'sev' | 'band'>('domLabel');
const PV_DIMS = [
  { value: 'domLabel', label: '歸因域' }, { value: 'srcLabel', label: '來源' }, { value: 'band', label: '信心分層' },
  { value: 'tier', label: '判定層' }, { value: 'vLabel', label: 'verdict' }, { value: 'sev', label: '嚴重度' },
];
const pivotOption = computed(() => {
  const m = sumKey(pvDim.value);
  // 維度＝歸因域時，套固定色（與頂部圖例一致）
  const color = (name: string) => (pvDim.value === 'domLabel' ? colorBySym[symOf(name)] : undefined);
  const data = Object.entries(m).map(([name, value]) => ({ name, value, itemStyle: { color: color(name) } }));
  if (pvChart.value === 'pie')
    return { tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' }, series: [{ type: 'pie', radius: ['40%', '70%'], data, label: { formatter: '{b} {d}%', fontSize: 11 } }] };
  if (pvChart.value === 'treemap')
    return { tooltip: {}, series: [{ type: 'treemap', roam: false, data, label: { show: true } }] };
  const ent = [...data].sort((a, b) => b.value - a.value);
  return {
    tooltip: { trigger: 'axis' }, grid: { left: 4, right: 12, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: 'category', data: ent.map((e) => e.name), axisLabel: { interval: 0, rotate: 24, fontSize: 10 } },
    yAxis: { type: 'value' }, series: [{ type: 'bar', data: ent, barMaxWidth: 32 }],
  };
});

// ── 圖表點擊 → 交叉篩選 ──────────────────────────────────────────
const onSankey = (p: any) => {
  if (p.dataType !== 'node') return;
  if (tierLabel2code[p.name]) toggle('tier', tierLabel2code[p.name]);
  else if (srcLabel2code[p.name]) toggleSrc(srcLabel2code[p.name]);
};
const onHeat = (p: any) => {
  const [vi, di] = p.value as number[];
  const sym = domainsCfg.items[di]?.symbol, vc = verdictsCfg.items[vi]?.code;
  const same = f.domain === sym && f.verdict === vc;
  f.domain = same ? '' : sym; f.verdict = same ? '' : vc;
};
const onSev = (p: any) => { toggle('severity', p.seriesName); toggle('tier', tierLabel2code[p.name]); };
const onPivot = (p: any) => {
  const d = pvDim.value, name = p.name;
  if (d === 'tier') toggle('tier', tierLabel2code[name]);
  else if (d === 'domLabel') toggle('domain', symOf(name));
  else if (d === 'vLabel') toggle('verdict', vdLabel2code[name]);
  else if (d === 'srcLabel') toggleSrc(srcLabel2code[name]);
  else if (d === 'sev') toggle('severity', name);
  else if (d === 'band') toggle('band', name);
};

// ── KPI + 明細表 ────────────────────────────────────────────────
const total = computed(() => rows.value.reduce((a, r) => a + r.n, 0));
const tierKpi = computed(() =>
  tiersCfg.items.map((t) => ({ code: t.code, label: t.label_zh, owner: t.owner, n: view.value.filter((r) => r.tierCode === t.code).reduce((s, r) => s + r.n, 0) })),
);
// 明細表加 owner 欄（取代原 owner 分流表；逐筆即可看派工）
const detailTable = computed(() =>
  [...view.value].sort((a, b) => b.n - a.n).map((r) => ({
    key: String(r.i), tier: r.tier, owner: r.owner, dom: r.domLabel, vd: r.vLabel, src: r.srcLabel,
    sev: r.sev, conf: r.conf.toFixed(2), n: r.n, dim: r.dimLabel || '—',
  })),
);
const DETAIL_COLS = [
  { title: '判定層', dataIndex: 'tier', width: 140 }, { title: 'owner（誰處理）', dataIndex: 'owner', width: 170 },
  { title: '歸因域', dataIndex: 'dom', width: 110 }, { title: 'verdict', dataIndex: 'vd', width: 120 },
  { title: '面向', dataIndex: 'dim', width: 80 }, { title: '來源', dataIndex: 'src', width: 110 },
  { title: '嚴重度', dataIndex: 'sev', width: 70 }, { title: '信心', dataIndex: 'conf', width: 60 },
  { title: '量', dataIndex: 'n', width: 60 },
];

const TIER_OPTS = [{ value: '', label: '全部判定層' }].concat(tiersCfg.items.map((t) => ({ value: t.code, label: t.label_zh })));
const SRC_OPTS = Object.entries(SRC_LABEL).map(([value, label]) => ({ value, label }));

// ── 問題列表（抽屜 · 隨時可叫出）─────────────────────────────────
// 全局篩選列恆常可見，故從該列開抽屜＝任何滾動位置都能查看完整問題列表。
// listKw：在全局交叉篩選結果之上，再做列表內關鍵字快速定位（全量唯讀瀏覽）。
const listVisible = ref(false);
const listKw = ref('');
const listRows = computed(() => {
  const kw = listKw.value.trim().toLowerCase();
  if (!kw) return detailTable.value;
  return detailTable.value.filter((r) =>
    [r.tier, r.owner, r.dom, r.vd, r.src, r.sev, r.dim].some((x) => String(x).toLowerCase().includes(kw)),
  );
});

// 各面板完整說明（右上 ⓘ hover/點擊展開）
const DESC: Record<string, string> = {
  level: '判定層 › 歸因域 › verdict 三層樹圖，面積＝量級。點區塊下鑽一層、麵包屑返回。一眼看哪一判定層 / 哪個域量最大。',
  sankey: '進線「來源 → 判定層 → owner」的流向與量：看每個來源主要流向哪一層、最終由誰處理。點節點＝該來源 / 判定層加入篩選。',
  heat: '歸因域(列) × verdict(欄) 交叉量級，色深＝量多。找「哪個域最常出哪種 verdict」。點格＝該域 + verdict 同時篩選。',
  sev: '各判定層的 P0–P3 嚴重度組成，看哪一層含最多高嚴重(P0/P1)。點柱段＝篩該嚴重度 + 判定層。',
  aspect: '① 商品內容的 8 面向中，哪個最常缺漏/模糊（content_missing / unclear）。僅統計 ① 內容類。',
  pivot: '任一維度（歸因域/來源/信心/判定層/verdict/嚴重度）× 圖型（圓餅/長條/樹圖）的佔比探索器——已整併原「歸因域/來源/信心」三個固定圓餅。點片段＝篩該值。',
  detail: '符合當前所有篩選的逐列明細（含 owner 派工欄），隨任一篩選即時更新。為「下鑽到單筆 finding」雛形；接 /api/findings 後即真實 finding 列表，可點列看客戶原話。',
};
</script>

<template>
  <div class="flex flex-col gap-4">
    <!--
      全局篩選列：Teleport 進 AppShell 固定 header 的 #page-toolbar，恆常可見且不隨內容滾動。
      改放固定 header 後，與內容區的 ECharts canvas 不再有重疊／合成層穿透問題，毋須 sticky / translateZ。
      篩選邏輯（f / chips / listVisible…）仍屬本元件，Teleport 僅改 DOM 掛載點、不影響響應式。
    -->
    <Teleport to="#page-toolbar">
      <div class="flex flex-col gap-2 border-b border-gray-200 bg-white px-5 py-2.5">
        <div class="flex flex-wrap items-center gap-x-3 gap-y-2">
          <span class="shrink-0 text-xs font-medium text-gray-500">全局篩選</span>
          <a-select v-model="f.tier" size="small" style="width: 150px" :options="TIER_OPTS" />
          <a-select v-model="f.source" size="small" multiple placeholder="全部來源（5 源）" style="width: 260px" :options="SRC_OPTS" :max-tag-count="2" />
          <a-button size="small" :disabled="!chips.length" @click="reset">重設全部</a-button>
          <a-tag color="orange" size="small" class="shrink-0">SAMPLE · 點任一圖即交叉篩選</a-tag>
          <a-button size="small" type="primary" class="ml-auto shrink-0" @click="listVisible = true">
            <template #icon><icon-list /></template>
            問題列表（{{ detailTable.length }}）
          </a-button>
        </div>
        <div v-if="chips.length" class="flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-gray-100 pt-2">
          <span class="shrink-0 text-xs text-gray-400">篩選中：</span>
          <a-tag v-for="c in chips" :key="c.key" closable color="arcoblue" size="small" @close="c.clear()">{{ c.label }}</a-tag>
        </div>
      </div>
    </Teleport>

    <!-- 判定層 KPI（脊椎 · 點即篩選） -->
    <div class="grid grid-cols-2 gap-3 md:grid-cols-5">
      <a-card
        v-for="k in tierKpi"
        :key="k.code"
        :class="['cursor-pointer transition', f.tier === k.code ? 'ring-2 ring-blue-400' : '']"
        :body-style="{ padding: '12px 14px' }"
        @click="toggle('tier', k.code)"
      >
        <div class="text-xs text-gray-500">{{ k.label }}</div>
        <div class="text-2xl font-semibold">{{ k.n }}</div>
        <div class="mt-1 truncate text-[11px] text-gray-400">→ {{ k.owner }}</div>
      </a-card>
    </div>

    <!-- 面板說明 · 圖例 -->
    <CardSection title="面板說明 · 圖例" hint="判定層＝脊椎 · 色塊＝歸因域 · 點圖/卡交叉篩選">
      <div class="grid grid-cols-1 gap-4 text-sm md:grid-cols-2">
        <div>
          <div class="mb-1 font-medium text-gray-600">判定層（→ 誰處理）</div>
          <ul class="space-y-1 text-gray-500">
            <li v-for="t in tiersCfg.items" :key="t.code"><b>{{ t.label_zh }}</b> → {{ t.owner }}</li>
          </ul>
        </div>
        <div>
          <div class="mb-1 font-medium text-gray-600">歸因域（色塊對照 · 7 域）</div>
          <div class="flex flex-wrap gap-x-4 gap-y-1 text-gray-500">
            <span v-for="d in domainMeta" :key="d.code" class="inline-flex items-center gap-1">
              <i class="inline-block h-3 w-3 rounded-sm" :style="{ background: d.color }" />{{ d.symbol }} {{ d.label_zh }}
            </span>
          </div>
          <div class="mb-1 mt-3 font-medium text-gray-600">信心分層</div>
          <div class="text-gray-500">≥.8 自動採信 · .5–.7 jury 覆核 · &lt;.5 HOLD 待人工</div>
        </div>
      </div>
      <div class="mt-3 border-t border-gray-100 pt-2 text-xs text-gray-400">
        互動：點圖表任一片段＝該維度加入篩選（再點取消）· 樹圖點區塊下鑽、麵包屑返回 · 膠囊可逐一移除 · 全部面板與底部明細連動。
      </div>
    </CardSection>

    <!-- 概覽（Overview first）：整體歸因分佈 -->
    <a-divider orientation="left" class="!my-1">
      概覽 · 整體歸因分佈（共 {{ total }} 筆 · 篩後 {{ view.reduce((a, r) => a + r.n, 0) }}）
    </a-divider>
    <CardSection title="層級總覽（判定層 › 歸因域 › verdict）" hint="樹圖·點區塊下鑽·麵包屑返回" :desc="DESC.level">
      <v-chart :option="levelOption" autoresize style="height: 440px" />
    </CardSection>

    <!-- 分流：問題從哪來、由誰處理 -->
    <a-divider orientation="left" class="!my-1">分流 · 問題從哪來、最常出哪種、由誰處理</a-divider>
    <div class="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <CardSection title="流程分流（來源 → 判定層 → owner）" hint="桑基·點節點篩選" :desc="DESC.sankey">
        <v-chart :option="sankeyOption" autoresize style="height: 380px" @click="onSankey" />
      </CardSection>
      <CardSection title="歸因域 × verdict 熱力" hint="點格＝域+verdict 篩選" :desc="DESC.heat">
        <v-chart :option="heatmapOption" autoresize style="height: 380px" @click="onHeat" />
      </CardSection>
    </div>

    <!-- 構成：各維度量級佔比（自訂樞紐 整併原三餅）+ 嚴重度 2D -->
    <a-divider orientation="left" class="!my-1">構成 · 各維度量級佔比</a-divider>
    <div class="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <CardSection title="維度佔比（任選）" hint="整併 歸因域/來源/信心·點片段篩選" :desc="DESC.pivot">
        <div class="mb-2 flex flex-wrap items-center gap-2">
          <a-select v-model="pvDim" size="mini" style="width: 130px" :options="PV_DIMS" />
          <a-radio-group v-model="pvChart" type="button" size="mini">
            <a-radio value="pie">圓餅</a-radio><a-radio value="bar">長條</a-radio><a-radio value="treemap">樹圖</a-radio>
          </a-radio-group>
        </div>
        <v-chart :option="pivotOption" autoresize style="height: 300px" @click="onPivot" />
      </CardSection>
      <CardSection title="嚴重度 × 判定層 堆疊" hint="點柱＝嚴重度+判定層" :desc="DESC.sev">
        <v-chart :option="sevStackOption" autoresize style="height: 300px" @click="onSev" />
      </CardSection>
    </div>

    <!-- 深鑽：① 內容唯一深鑽 -->
    <a-divider orientation="left" class="!my-1">深鑽 · 內容面向（① 唯一深鑽）</a-divider>
    <CardSection title="內容面向 Top-N（① 商品內容）" hint="哪個面向最常缺漏/模糊" :desc="DESC.aspect">
      <v-chart :option="aspectBarOption" autoresize style="height: 320px" />
    </CardSection>

    <!--
      問題列表（行動 · details on demand）：改由頂部 sticky 篩選列的按鈕開啟抽屜，
      任何滾動位置都能隨時叫出；內容隨全局交叉篩選即時更新，含 owner 派工欄。
    -->
    <a-drawer v-model:visible="listVisible" :width="960" :footer="false" unmount-on-close>
      <template #title>
        問題列表
        <span class="ml-2 text-xs font-normal text-gray-400">
          隨全局篩選即時更新 · 共 {{ detailTable.length }} 列
        </span>
      </template>
      <div class="flex flex-col gap-3">
        <div class="flex flex-wrap items-center gap-2">
          <a-input-search
            v-model="listKw"
            allow-clear
            size="small"
            placeholder="於列表內搜尋（判定層 / owner / 歸因域 / verdict / 來源 / 面向…）"
            style="max-width: 380px"
          />
          <span class="text-xs text-gray-400">符合 {{ listRows.length }} / {{ detailTable.length }} 列</span>
        </div>
        <a-table :data="listRows" :columns="DETAIL_COLS" :pagination="{ pageSize: 15 }" size="small" />
        <p class="text-xs leading-relaxed text-gray-400">{{ DESC.detail }}</p>
      </div>
    </a-drawer>
  </div>
</template>
