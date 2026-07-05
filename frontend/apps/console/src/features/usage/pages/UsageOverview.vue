<script setup lang="ts">
/**
 * 💰 AI 消耗總覽：LLM 使用量 / 成本多維度統計。
 *
 * 資料源＝llm_usage 表（每次真 LLM 呼叫落一列），經 /api/llm-usage/overview 聚合。
 * 圖表全複用 overviewCharts 現成 builder（趨勢/長條），不造輪子。
 * ⚠️ 只統計本功能上線後的呼叫；stub 模式（無 LLM token）無真實用量。
 */
import { computed, ref } from 'vue';
import VChart from 'vue-echarts';
import { use } from 'echarts/core';
import { BarChart, LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, LegendComponent, MarkLineComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { IconRefresh } from '@arco-design/web-vue/es/icon';
import { StateGuard, CardSection, KpiCard } from '@/components';
import { useUsageDashboard } from '../composables';

use([BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, CanvasRenderer]);

// 日期區間（'YYYY-MM-DD' 陣列）+ 趨勢粒度（年/月/日），驅動聚合重載
const dateRange = ref<string[]>([]);
const granularity = ref('day');

const {
  loading,
  error,
  hasData,
  kpi,
  reload,
  costTrendOption,
  callsTrendOption,
  byModelOption,
  byStageOption,
  bySourceOption,
} = useUsageDashboard(() => ({
  dateFrom: dateRange.value?.[0],
  dateTo: dateRange.value?.[1],
  granularity: granularity.value,
}));

/** 成本 USD 顯示（4 位小數）。 */
const costText = computed(() => `$${kpi.value.cost.toFixed(4)}`);
/** 千分位整數。 */
const num = (n: number) => n.toLocaleString();
</script>

<template>
  <Teleport to="#page-toolbar">
    <div class="flex items-center gap-3">
      <a-range-picker v-model="dateRange" size="small" value-format="YYYY-MM-DD" style="width: 240px" />
      <a-radio-group v-model="granularity" type="button" size="small">
        <a-radio value="year">年</a-radio>
        <a-radio value="month">月</a-radio>
        <a-radio value="day">日</a-radio>
      </a-radio-group>
      <a-button size="small" :loading="loading" @click="reload">
        <template #icon><icon-refresh /></template>
        重新整理
      </a-button>
    </div>
  </Teleport>

  <StateGuard
    :loading="loading"
    :error="error"
    :empty="!hasData"
    empty-text="尚無 AI 使用紀錄（本功能上線後的 LLM 呼叫才會統計；stub 模式無真實用量）"
  >
    <div class="flex flex-col gap-4">
      <!-- ── 核心指標 ── -->
      <CardSection title="核心指標" hint="區間內 AI 呼叫的總成本 / token / 呼叫數 / 快取節省">
        <div class="grid grid-cols-2 gap-4 md:grid-cols-4">
          <KpiCard label="總成本" :value="costText" subtext="USD（含 prompt cache 折扣）" />
          <KpiCard label="總 tokens" :value="num(kpi.tokens)" subtext="prompt + completion" />
          <KpiCard label="總呼叫數" :value="num(kpi.calls)" subtext="真 LLM 呼叫次數" />
          <KpiCard label="快取節省" :value="num(kpi.cached)" subtext="命中 prompt cache 的 tokens" />
        </div>
      </CardSection>

      <!-- 每日趨勢：成本 + 呼叫數 -->
      <a-row :gutter="[16, 16]" align="stretch">
        <a-col :span="12">
          <CardSection title="每日成本趨勢" hint="區間內各時間桶的 AI 成本（USD）">
            <v-chart :option="costTrendOption" class="h-[300px]" autoresize />
          </CardSection>
        </a-col>
        <a-col :span="12">
          <CardSection title="每日呼叫數趨勢" hint="區間內各時間桶的 LLM 呼叫次數">
            <v-chart :option="callsTrendOption" class="h-[300px]" autoresize />
          </CardSection>
        </a-col>
      </a-row>

      <!-- 各維度成本分布：模型 / 階段 / 來源（hint 短句避免擠掉標題；詳解放 desc 的 ⓘ popover）-->
      <a-row :gutter="[16, 16]" align="stretch">
        <a-col :span="8">
          <CardSection title="各模型成本" hint="USD" desc="依模型分組的成本佔比（USD），依成本高低排序。">
            <v-chart :option="byModelOption" class="h-[300px]" autoresize />
          </CardSection>
        </a-col>
        <a-col :span="8">
          <CardSection title="各階段成本" hint="USD" desc="依呼叫階段分組：polarity 極性閘門 / attribute 歸因 / domain 域分類 / attribute_b cascade / true_label 標真值評分 / translate 摘要翻譯。">
            <v-chart :option="byStageOption" class="h-[300px]" autoresize />
          </CardSection>
        </a-col>
        <a-col :span="8">
          <CardSection title="各來源成本" hint="USD" desc="依判決來源分組（product_reviews / conversations…）；ad-hoc 單次呼叫無來源者歸「（未標）」。">
            <v-chart :option="bySourceOption" class="h-[300px]" autoresize />
          </CardSection>
        </a-col>
      </a-row>
    </div>
  </StateGuard>
</template>
