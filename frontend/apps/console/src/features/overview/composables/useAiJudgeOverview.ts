/**
 * overview 首頁「縮窄真接」composable：抓 AI 法官真實指標，patch 進 mock 資料集的 ai_judge 區塊。
 *
 * 範圍（2026-07-07 拍板）：只有 attributions/llm_usage 可聚合的指標接真——AI 法官引擎卡、
 * content goal 北極星 intake_content_ratio、laggingTrend；審品/CVR/售後等外部系統指標
 * （Google Sheet / Tableau / Looker）維持 config/mock 示意值並於頁面標註「外部指標：示意」。
 * 載入失敗 → 整包 fallback mock（三態由頁面 header tag 呈現，不阻斷版面渲染）。
 */
import { computed, ref } from 'vue';
import { getAiJudgeOverview, type AiJudgeOverviewResp } from '@/api';
import type { Overview3 } from '../dashboard.types';

/** 'YYYY-MM' → 'M 月'（對齊 mock 的月份 label 風格）。 */
const ymLabel = (ym: string): string => `${Number(ym.slice(5))} 月`;

/**
 * 把真實指標 patch 進 mock 資料集複本（純函式，供單測）。
 * @param base mock 全集（不就地修改）
 * @param real API 回應；monthly < 2 點時僅 patch 總量值、不動 trend/spark（單點趨勢無意義）
 */
export function patchAiJudgeData(base: Overview3, real: AiJudgeOverviewResp): Overview3 {
  const out: Overview3 = structuredClone(base);
  const share = real.totals.content_share_pct;
  const ratios = real.monthly.map((m) => m.ratio_pct);
  const months = real.monthly.map((m) => ymLabel(m.ym));
  const hasTrend = ratios.length >= 2;

  // 引擎卡（ai_judge）：占比 + 歸因樣本改真值；spark 改真實月占比
  const engine = out.engines.find((e) => e.id === 'ai_judge');
  if (engine) {
    engine.metrics = [
      { label: '內容類歸因占比', value: share, unit: '%' },
      { label: '已初判進線', value: real.totals.judged_items, unit: '筆' },
      { label: '歸因樣本', value: real.totals.attributed_rows, unit: '筆' },
    ];
    if (hasTrend) engine.spark = ratios;
  }

  // content goal 北極星：intake_content_ratio 改真值（delta＝最近兩月差；占比下降＝好）
  const ns = out.goals.content.northStar.find((n) => n.key === 'intake_content_ratio');
  if (ns) {
    ns.value = share;
    if (hasTrend) {
      const d = Number((ratios[ratios.length - 1] - ratios[ratios.length - 2]).toFixed(2));
      ns.delta = d;
      ns.deltaDir = d >= 0 ? 'up' : 'down';
      ns.deltaGood = d <= 0;
      ns.spark = ratios;
    }
    ns.hint = '真實資料：attributions 內容類占比（初判時間軸·distinct 進線）。';
  }

  // content goal 落後趨勢圖：改真實月序列（target 門檻沿用 config 值）
  const lagging = out.goals.content.charts.laggingTrend;
  if (hasTrend && lagging && 'series' in lagging) {
    lagging.months = months;
    lagging.series = [{ name: '內容類歸因占比', data: ratios }];
  }
  return out;
}

/** 抓真實指標 + 合成顯示資料集；state: loading / ok / error（頁面 header tag 呈現三態）。 */
export function useAiJudgeOverview(base: Overview3) {
  const state = ref<'loading' | 'ok' | 'error'>('loading');
  const real = ref<AiJudgeOverviewResp | null>(null);

  const load = async () => {
    state.value = 'loading';
    try {
      real.value = await getAiJudgeOverview();
      state.value = 'ok';
    } catch {
      real.value = null; // fallback mock；tag 呈現失敗態，版面照常渲染
      state.value = 'error';
    }
  };
  void load();

  const data = computed<Overview3>(() => (real.value ? patchAiJudgeData(base, real.value) : base));
  return { data, state, reload: load };
}
