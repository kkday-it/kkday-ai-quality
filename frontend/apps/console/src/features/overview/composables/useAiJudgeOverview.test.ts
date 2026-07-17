/** patchAiJudgeData（縮窄真接的 mock patch 純函式）測試：真值覆蓋點 / 單點趨勢降級 / 不污染原集。 */
import { describe, expect, it } from 'vitest';
import { patchAiJudgeData } from './useAiJudgeOverview';
import type { AiJudgeOverviewResp } from '@/api';
import type { Overview3 } from '../dashboard.types';

/** 最小 mock 底集（只含 patch 會觸碰的區塊）。 */
const base = {
  meta: { title: 't', subtitle: 's', period: 'p', note: 'n', loopCaption: 'c' },
  loop: [],
  engines: [
    {
      id: 'ai_judge',
      name: 'AI 法官',
      tagline: '',
      pLevel: 'P1',
      status: '',
      statusColor: '',
      goal: '',
      metrics: [{ label: '舊', value: 1, unit: '%' }],
      spark: [1, 2],
      route: null,
      cta: '',
    },
  ],
  crossTrend: { title: '', unit: '%', months: [], series: [] },
  goals: {
    content: {
      label: 'c',
      northStar: [
        {
          key: 'intake_content_ratio',
          label: '',
          value: 14.95,
          unit: '%',
          target: 10,
          targetText: '',
          delta: 0,
          deltaDir: 'up',
          deltaGood: true,
          tone: 'core',
          hint: '',
          spark: [1],
        },
      ],
      charts: {
        laggingTrend: {
          title: '',
          unit: '%',
          months: ['1 月'],
          target: 10,
          series: [{ name: '舊', data: [9] }],
        },
      },
      sources: [],
    },
    presale: { label: 'p', northStar: [], charts: {}, sources: [] },
    postsale: { label: 'p', northStar: [], charts: {}, sources: [] },
  },
} as unknown as Overview3;

const real: AiJudgeOverviewResp = {
  monthly: [
    { ym: '2026-06', judged: 3, content: 1, ratio_pct: 33.33 },
    { ym: '2026-07', judged: 2, content: 2, ratio_pct: 8.4 },
  ],
  totals: { judged_items: 5, attributed_rows: 5, content_items: 3, content_share_pct: 8.4 },
};

describe('patchAiJudgeData', () => {
  it('真值覆蓋引擎卡 / 北極星 / laggingTrend，delta 依最近兩月差（下降＝好）', () => {
    const out = patchAiJudgeData(base, real);
    const engine = out.engines.find((e) => e.id === 'ai_judge')!;
    expect(engine.metrics[0]).toEqual({ label: '內容類歸因占比', value: 8.4, unit: '%' });
    expect(engine.spark).toEqual([33.33, 8.4]);
    const ns = out.goals.content.northStar[0];
    expect(ns.value).toBe(8.4);
    expect(ns.delta).toBeCloseTo(-24.93);
    expect(ns.deltaDir).toBe('down');
    expect(ns.deltaGood).toBe(true);
    const lag = out.goals.content.charts.laggingTrend as {
      months: string[];
      series: { data: number[] }[];
    };
    expect(lag.months).toEqual(['6 月', '7 月']);
    expect(lag.series[0].data).toEqual([33.33, 8.4]);
  });

  it('單點趨勢（monthly<2）只 patch 總量值，不動 trend/spark（單點趨勢無意義）', () => {
    const out = patchAiJudgeData(base, { ...real, monthly: real.monthly.slice(0, 1) });
    expect(out.goals.content.northStar[0].value).toBe(8.4);
    expect(out.goals.content.northStar[0].spark).toEqual([1]); // 原 mock spark 保留
    const lag = out.goals.content.charts.laggingTrend as { months: string[] };
    expect(lag.months).toEqual(['1 月']);
  });

  it('純函式：不就地污染入參 base', () => {
    patchAiJudgeData(base, real);
    expect(base.goals.content.northStar[0].value).toBe(14.95);
    expect(base.engines[0].metrics[0].label).toBe('舊');
  });
});
