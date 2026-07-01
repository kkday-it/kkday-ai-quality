<script setup lang="ts">
/**
 * 歸因列表（伺服器端分頁 + 選擇驅動初判歸因 + 正負傾向 + 原始+歸因合表）。
 *
 * 分頁/篩選/排序皆走後端（/api/problems limit-offset；occurred_at DESC 穩定）；表頭固定、表身內滾動、
 * 底部完整 Arco 分頁。選取跨頁累積（複選 / 分頁選取 / 全部未判 scope）；導出走後端全量 CSV。
 * 正向/中性/數據不足 不歸因，只有負向才有 L1→L3。
 */
import { ref, computed, onMounted } from 'vue';
import { Message } from '@arco-design/web-vue';
import { IconDownload } from '@arco-design/web-vue/es/icon';
import { CardSection, StateGuard } from '@/components';
import {
  getProblems,
  startPrejudge,
  getPrejudgeStatus,
  getSettings,
  exportProblems,
} from '@/api';
import { SOURCES } from '../constants';
import verdictsCfg from '@config/ai_judge/verdicts.json';

const vdLabel = Object.fromEntries(verdictsCfg.items.map((v: any) => [v.code, v.label_zh]));
/** 信心分層 code → 繁中 label（純顯示；未知 code 回退原值）。 */
const tierLabel: Record<string, string> = {
  auto_accept: '自動採信',
  jury: 'jury 覆核',
  needs_review: '待人工',
  hold: 'HOLD',
};

/**
 * 正規化時間字串顯示：去小數秒/去 T·Z；dateOnly 或時間為 00:00:00 時只留日期。
 * 與後端 db.fmt_datetime 語義一致（評論時間含時分秒、出發日只到日）。
 */
const fmtDt = (value: unknown, dateOnly = false): string => {
  if (value === null || value === undefined || value === '') return '';
  let s = String(value).trim().replace('T', ' ');
  if (s.endsWith('Z')) s = s.slice(0, -1).trim();
  s = s.replace(/\.\d+/, ''); // 去小數秒
  if (dateOnly || s.endsWith(' 00:00:00')) return s.split(' ')[0];
  return s;
};
const POLARITY_LABEL: Record<string, string> = {
  positive: '正向',
  negative: '負向',
  neutral: '中性',
  unknown: '數據不足',
};
const POLARITY_COLOR: Record<string, string> = {
  positive: 'green',
  negative: 'red',
  neutral: 'gray',
  unknown: 'orange',
};

const SOURCE_OPTS = SOURCES.map((s) => ({ value: s.value, label: s.label }));
const source = ref('product_reviews');
const polarityFilter = ref('');
const onlyProblem = ref(false);
/** 生效的 polarity 篩選（送後端）。 */
const effPolarity = computed(() => (onlyProblem.value ? 'negative' : polarityFilter.value || undefined));

// ── LLM 模型（已保存配置）──
const llmConfigId = ref('');
const llmConfigs = ref<{ id: string; label: string; model: string }[]>([]);
const LLM_OPTS = computed(() =>
  llmConfigs.value.map((c) => ({ value: c.id, label: `${c.label}（${c.model}）` })),
);
const loadConfigs = async () => {
  try {
    const s = await getSettings();
    llmConfigs.value = (s.llm_configs || []).map((c: any) => ({
      id: c.id,
      label: c.label || c.id,
      model: c.model || '',
    }));
    llmConfigId.value = s.active_llm_config_id || llmConfigs.value[0]?.id || '';
  } catch {
    llmConfigs.value = [];
  }
};

// ── 伺服器端分頁 ──
const rows = ref<any[]>([]);
const total = ref(0);
const unjudged = ref(0);
const page = ref(1);
const pageSize = ref(20);
const loading = ref(true);
const error = ref('');

const loadPage = async () => {
  loading.value = true;
  error.value = '';
  try {
    const r = await getProblems({
      source: source.value,
      polarity: effPolarity.value,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    });
    rows.value = r.rows || [];
    total.value = r.total || 0;
  } catch (e: any) {
    error.value = '載入失敗：' + (e?.message || e);
  } finally {
    loading.value = false;
  }
};
/** 取未判筆數（全部未判按鈕顯示）。 */
const loadUnjudged = async () => {
  try {
    const r = await getProblems({ source: source.value, judged: false, limit: 1 });
    unjudged.value = r.total || 0;
  } catch {
    unjudged.value = 0;
  }
};
/** 篩選變動：回第 1 頁重載。 */
const onFilterChange = () => {
  page.value = 1;
  selectedKeys.value = [];
  loadPage();
  loadUnjudged();
};

onMounted(() => {
  loadConfigs();
  loadPage();
  loadUnjudged();
});

// ── 選取（跨頁累積；row-key=item_id）──
const selectedKeys = ref<string[]>([]);
const runCount = computed(() => selectedKeys.value.length);
const clearSelection = () => (selectedKeys.value = []);
const pageSpec = ref('');
/** 分頁選取（1,2,3,5 / 1~200）：依後端分頁抓對應頁的 item_id 加入選取。 */
const selectPages = async () => {
  const spec = pageSpec.value.trim();
  if (!spec) return;
  const pages = new Set<number>();
  for (const part of spec.split(/[,，]/)) {
    const seg = part.trim();
    if (!seg) continue;
    const m = seg.split(/[~\-～]/);
    if (m.length === 2 && +m[0] && +m[1]) {
      for (let p = Math.min(+m[0], +m[1]); p <= Math.max(+m[0], +m[1]); p++) pages.add(p);
    } else if (+seg) {
      pages.add(+seg);
    }
  }
  if (!pages.size) return;
  const lo = Math.min(...pages);
  const hi = Math.max(...pages);
  const ps = pageSize.value;
  try {
    const r = await getProblems({
      source: source.value,
      polarity: effPolarity.value,
      limit: (hi - lo + 1) * ps,
      offset: (lo - 1) * ps,
    });
    const ids: string[] = [];
    (r.rows || []).forEach((row: any, idx: number) => {
      const gp = lo + Math.floor(idx / ps); // 該列的全域分頁號
      if (pages.has(gp)) ids.push(row.item_id);
    });
    selectedKeys.value = Array.from(new Set([...selectedKeys.value, ...ids]));
    Message.success(`已選取 ${ids.length} 列（分頁 ${spec}）`);
  } catch (e: any) {
    Message.error('分頁選取失敗：' + (e?.message || e));
  }
};

// ── 初判歸因 ──
const running = ref(false);
const progress = ref({ processed: 0, total: 0, totalTokens: 0, costUsd: 0 });
const progressPct = computed(() =>
  progress.value.total ? Math.round((progress.value.processed / progress.value.total) * 100) : 0,
);
/** token 花費顯示（金額 4 位小數，token 千分位）；批量判決過程同步更新。 */
const costText = computed(() =>
  progress.value.totalTokens
    ? `${progress.value.totalTokens.toLocaleString()} tokens · ≈ $${progress.value.costUsd.toFixed(4)}`
    : '',
);
const _poll = (jobId: string) =>
  new Promise<void>((resolve) => {
    const timer = setInterval(async () => {
      try {
        const st = await getPrejudgeStatus(jobId);
        progress.value = {
          processed: st.processed || 0,
          total: st.total || progress.value.total,
          totalTokens: st.total_tokens || 0,
          costUsd: st.cost_usd || 0,
        };
        if (st.status === 'done' || st.status === 'error') {
          clearInterval(timer);
          resolve();
        }
      } catch {
        clearInterval(timer);
        resolve();
      }
    }, 1000);
  });
const _run = async (body: { item_ids?: string[]; source?: string; scope?: string }) => {
  if (running.value) return;
  running.value = true;
  progress.value = { processed: 0, total: 0, totalTokens: 0, costUsd: 0 };
  try {
    const r = await startPrejudge({ ...body, llm_config_id: llmConfigId.value || undefined });
    progress.value = { processed: 0, total: r.total, totalTokens: 0, costUsd: 0 };
    if (!r.total) {
      Message.warning('沒有可分析的對象');
      return;
    }
    await _poll(r.job_id);
    Message.success(`初判歸因完成：${progress.value.processed} 筆（模型 ${r.model}）`);
    await loadPage(); // 重載當前頁（保持頁碼，就地看到結果）
    await loadUnjudged();
  } catch (e: any) {
    Message.error('初判歸因失敗：' + (e?.message || e));
  } finally {
    running.value = false;
  }
};
const runSelected = () => {
  if (!selectedKeys.value.length) {
    Message.warning('請先勾選/分頁選取要分析的列');
    return;
  }
  _run({ item_ids: selectedKeys.value });
};
const runAll = () => _run({ source: source.value, scope: 'all' });

/** 導出 CSV（POST 全量；有勾選→只導已選，否則導符合目前篩選全部）→ blob 下載。 */
const exportCsv = async () => {
  try {
    const blob = await exportProblems({
      source: source.value,
      polarity: effPolarity.value,
      item_ids: selectedKeys.value.length ? selectedKeys.value : undefined,
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `歸因列表_${source.value}_${selectedKeys.value.length || total.value}列.csv`;
    a.click();
    URL.revokeObjectURL(url);
    Message.success('已導出 CSV');
  } catch (e: any) {
    Message.error('導出失敗：' + (e?.message || e));
  }
};

const COLS = [
  { title: '商品ID', dataIndex: 'prod_oid' },
  { title: '商品名稱', dataIndex: 'prod_name', ellipsis: true, tooltip: true },
  { title: '評論 / 內容', dataIndex: 'content', ellipsis: true, tooltip: true },
  { title: '星等', dataIndex: 'score' },
  { title: '評論時間', dataIndex: 'occurred_at', slotName: 'occurred' },
  { title: '出發日', dataIndex: 'go_date', slotName: 'godate' },
  { title: '訂單', dataIndex: 'order_mid' },
  { title: '傾向', dataIndex: 'polarity', slotName: 'pol' },
  { title: '歸因（L1→L3）', dataIndex: 'attr', slotName: 'attr' },
  { title: '判決', dataIndex: 'verdict', slotName: 'vd' },
  { title: '信心', dataIndex: 'confidence' },
  { title: '分層', dataIndex: 'confidence_tier', slotName: 'tier' },
];
</script>

<template>
  <div class="flex flex-col gap-4">
    <CardSection title="初判歸因" hint="選來源+模型 → 勾選列/分頁選取/全部未判 → 進行初判歸因（正向不分類，只有負向歸 L1→L3）">
      <div class="flex flex-wrap items-end gap-3">
        <div>
          <div class="mb-1 text-xs text-gray-500">來源</div>
          <a-select v-model="source" style="width: 150px" :options="SOURCE_OPTS" @change="onFilterChange" />
        </div>
        <div>
          <div class="mb-1 text-xs text-gray-500">LLM 模型（已保存配置）</div>
          <a-select v-model="llmConfigId" style="width: 250px" :options="LLM_OPTS" placeholder="選擇模型（預設啟用中）" />
        </div>
        <a-button type="primary" :loading="running" @click="runSelected">
          進行初判歸因（已選 {{ runCount }}）
        </a-button>
        <a-button :loading="running" @click="runAll">全部未判（{{ unjudged }}）</a-button>
        <a-button @click="exportCsv">
          <template #icon><icon-download /></template>
          導出 CSV（{{ runCount ? `已選 ${runCount}` : '全部篩選' }}）
        </a-button>
      </div>
      <div v-if="running" class="mt-3">
        <a-progress :percent="progressPct / 100" :status="progressPct >= 100 ? 'success' : 'normal'" />
        <div class="mt-1 flex flex-wrap gap-x-4 text-xs text-gray-500">
          <span>已處理 {{ progress.processed }} / {{ progress.total }} 筆…</span>
          <span v-if="costText">花費 {{ costText }}</span>
        </div>
      </div>
    </CardSection>

    <CardSection :title="`歸因列表（共 ${total} · 未判 ${unjudged}）`" hint="伺服器端分頁；勾選/分頁選取做初判歸因或導出">
      <div class="mb-2 flex flex-wrap items-center gap-3">
        <a-checkbox v-model="onlyProblem" @change="onFilterChange">僅看問題（負向）</a-checkbox>
        <a-select
          v-model="polarityFilter"
          size="small"
          style="width: 140px"
          :disabled="onlyProblem"
          :options="[
            { value: '', label: '全部傾向' },
            { value: 'negative', label: '負向' },
            { value: 'positive', label: '正向' },
            { value: 'neutral', label: '中性' },
            { value: 'unknown', label: '數據不足' },
          ]"
          @change="onFilterChange"
        />
        <a-input
          v-model="pageSpec"
          size="small"
          allow-clear
          style="width: 180px"
          placeholder="分頁選取 如 1,2,3,5 或 1~200"
          @press-enter="selectPages"
        />
        <a-button size="small" @click="selectPages">選取分頁</a-button>
        <a-button v-if="runCount" size="small" @click="clearSelection">清除選擇</a-button>
        <span class="text-xs text-gray-400">每頁 {{ pageSize }} · 已選 {{ runCount }}</span>
      </div>
      <StateGuard :loading="loading" :error="error" :empty="!rows.length" empty-text="尚無資料，請先到「資料上傳」上傳 CSV">
        <a-table
          :data="rows"
          :columns="COLS"
          :pagination="{
            current: page,
            pageSize,
            total,
            showTotal: true,
            showPageSize: true,
            showJumper: true,
          }"
          :row-selection="{ type: 'checkbox', selectedRowKeys: selectedKeys, showCheckedAll: true }"
          size="small"
          row-key="item_id"
          :scroll="{ y: 560 }"
          @page-change="(p: number) => { page = p; loadPage(); }"
          @page-size-change="(s: number) => { pageSize = s; page = 1; loadPage(); }"
          @selection-change="(keys) => (selectedKeys = keys.map(String))"
        >
          <template #occurred="{ record }">{{ fmtDt(record.occurred_at) }}</template>
          <template #godate="{ record }">{{ fmtDt(record.go_date, true) }}</template>
          <template #pol="{ record }">
            <a-tag v-if="record.polarity" size="small" :color="POLARITY_COLOR[record.polarity]">
              {{ POLARITY_LABEL[record.polarity] || record.polarity }}
            </a-tag>
            <span v-else class="text-gray-300">未判</span>
          </template>
          <template #attr="{ record }">
            <div v-if="record.l1_label || record.l3_label" class="text-xs leading-relaxed">
              <div><span class="text-gray-400">L1</span> {{ record.l1_label }}</div>
              <div v-if="record.l2_label"><span class="text-gray-400">L2</span> {{ record.l2_label }}</div>
              <div v-if="record.l3_label"><span class="text-gray-400">L3</span> {{ record.l3_label }}</div>
            </div>
            <span v-else class="text-gray-300">—</span>
          </template>
          <template #vd="{ record }">
            <a-tag v-if="record.verdict" size="small">{{ vdLabel[record.verdict] || record.verdict }}</a-tag>
          </template>
          <template #tier="{ record }">
            <span v-if="record.confidence_tier">{{ tierLabel[record.confidence_tier] || record.confidence_tier }}</span>
            <span v-else class="text-gray-300">—</span>
          </template>
        </a-table>
      </StateGuard>
    </CardSection>
  </div>
</template>
