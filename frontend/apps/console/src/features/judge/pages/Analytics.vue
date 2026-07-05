<script setup lang="ts">
import { ref, onMounted, computed, reactive } from 'vue';
import { countBy, maxBy } from 'lodash-es';
import { getFindings } from '@/api';
import { StateGuard, TableLayout, KpiCard } from '@/components';
import { FindingCard } from '../components';
import { DIMS, STATUS_OPTS } from '../constants';
import { flatFinding as flat } from '../utils';

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
  const byDim = countBy(
    all.value.filter((f) => f.dimension !== 'non_content'),
    'dimension',
  );
  const top = maxBy(Object.entries(byDim), ([, n]) => n);
  return {
    total: t,
    topDim: top ? top[0] : '—',
    topN: top ? top[1] : 0,
  };
});

// ── 「問題明細」篩選器（與上半部圖表解耦）：填條件 → 搜索套用 / 重置，再分頁 ──
// 維度/狀態＝下拉；prod/pkg/order/supplier OID＝輸入子字串。draft=輸入中，applied=已套用。
type Filt = {
  dim: string;
  status: string;
  prod: string;
  pkg: string;
  order: string;
  supplier: string;
};
const emptyFilt = (): Filt => ({
  dim: '',
  status: '',
  prod: '',
  pkg: '',
  order: '',
  supplier: '',
});
const draft = reactive<Filt>(emptyFilt());
const applied = reactive<Filt>(emptyFilt());
const idMatch = (val: unknown, q: string) =>
  !q.trim() ||
  String(val ?? '')
    .toLowerCase()
    .includes(q.trim().toLowerCase());
const filtered = computed(() =>
  all.value.filter(
    (f) =>
      (!applied.dim || f.dimension === applied.dim) &&
      (!applied.status || f.status === applied.status) &&
      idMatch(f.prod_oid, applied.prod) &&
      idMatch(f.pkg_oid, applied.pkg) &&
      idMatch(f.order_oid, applied.order) &&
      idMatch(f.supplier_oid, applied.supplier),
  ),
);
const doSearch = () => {
  Object.assign(applied, draft);
  page.value = 1;
};
const doReset = () => {
  Object.assign(draft, emptyFilt());
  Object.assign(applied, emptyFilt());
  page.value = 1;
};

// 分頁（內容區內部滾動，分頁固定底部）
const page = ref(1);
const pageSize = ref(10);
const paged = computed(() => {
  const start = (page.value - 1) * pageSize.value;
  return filtered.value.slice(start, start + pageSize.value);
});
const onPageSizeChange = (size: number) => {
  pageSize.value = size;
  page.value = 1;
};

// 上半部（KPI 卡）點擊 → 開「獨立抽屜」查看子集，不寫入下方「問題明細」篩選器（解耦）
const drawerVisible = ref(false);
const drawerKind = ref('all');
const openDrawer = (kind: string) => {
  drawerKind.value = kind;
  drawerVisible.value = true;
};
const drawerList = computed(() => {
  if (drawerKind.value === 'topdim')
    return all.value.filter((f) => f.dimension === kpi.value.topDim);
  return all.value;
});
const drawerTitle = computed(() => {
  const m: Record<string, string> = {
    all: '全部 Findings',
    topdim: `最痛維度 · ${kpi.value.topDim}`,
  };
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
    <div class="flex h-full flex-col">
      <a-row :gutter="14" class="mb-4 shrink-0">
        <a-col :span="12"
          ><KpiCard
            label="本期 Findings"
            :value="kpi.total"
            subtext="點擊查看全部 →"
            @click="openDrawer('all')"
        /></a-col>
        <a-col :span="12"
          ><KpiCard
            label="最痛維度"
            :value="kpi.topDim"
            :subtext="`${kpi.topN} 筆 · 點擊查看 →`"
            @click="openDrawer('topdim')"
        /></a-col>
      </a-row>

      <TableLayout title="問題明細">
        <template #extra>
          <a-space wrap>
            <a-select
              v-model="draft.dim"
              placeholder="維度"
              allow-clear
              size="small"
              class="w-[112px]"
              @change="doSearch"
            >
              <a-option v-for="d in DIMS" :key="d" :value="d">{{ d }}</a-option>
            </a-select>
            <a-select
              v-model="draft.status"
              placeholder="狀態"
              allow-clear
              size="small"
              class="w-[100px]"
              @change="doSearch"
            >
              <a-option v-for="s in STATUS_OPTS" :key="s.k" :value="s.k">{{ s.l }}</a-option>
            </a-select>
            <a-input
              v-model="draft.prod"
              placeholder="商品 prod_oid"
              allow-clear
              size="small"
              class="w-[128px]"
              @press-enter="doSearch"
              @clear="doSearch"
            />
            <a-input
              v-model="draft.pkg"
              placeholder="方案 pkg_oid"
              allow-clear
              size="small"
              class="w-[128px]"
              @press-enter="doSearch"
              @clear="doSearch"
            />
            <a-input
              v-model="draft.order"
              placeholder="訂單 order_oid"
              allow-clear
              size="small"
              class="w-[136px]"
              @press-enter="doSearch"
              @clear="doSearch"
            />
            <a-input
              v-model="draft.supplier"
              placeholder="供應商 supplier_oid"
              allow-clear
              size="small"
              class="w-[148px]"
              @press-enter="doSearch"
              @clear="doSearch"
            />
            <a-button type="primary" size="small" @click="doSearch">搜索</a-button>
            <a-button size="small" @click="doReset">重置</a-button>
          </a-space>
        </template>

        <div class="min-h-0 flex-1 overflow-y-auto px-1 pt-1">
          <a-empty
            v-if="!filtered.length"
            description="無符合條件的問題，可調整篩選或重置"
            class="p-10"
          />
          <FindingCard v-for="f in paged" :key="f.finding_id" :f="f" />
        </div>
        <template #footer>
          <div
            v-if="filtered.length"
            class="flex justify-end border-t border-[#f0f0f0] pt-3"
          >
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
        </template>
      </TableLayout>

      <a-drawer v-model:visible="drawerVisible" :width="760" :title="drawerTitle" unmount-on-close>
        <div class="mb-3 text-xs text-[#86909c]">共 {{ drawerList.length }} 筆</div>
        <a-empty v-if="!drawerList.length" description="無資料" />
        <FindingCard v-for="f in drawerList" :key="f.finding_id" :f="f" />
      </a-drawer>
    </div>
  </StateGuard>
</template>
