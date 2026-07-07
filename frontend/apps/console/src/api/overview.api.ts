// 質檢概覽（overview 首頁）真實指標 API：僅 AI 法官可自 DB 聚合的部分（縮窄真接）。
import { j } from './http.api';

/** 單月內容類占比列（judged_at 月分組、distinct 進線去重）。 */
export interface AiJudgeMonthlyRow {
  ym: string; // 'YYYY-MM'
  judged: number;
  content: number;
  ratio_pct: number;
}

/** /api/overview/ai-judge 回應（口徑見後端 db.ai_judge_overview_stats docstring）。 */
export interface AiJudgeOverviewResp {
  monthly: AiJudgeMonthlyRow[];
  totals: {
    judged_items: number;
    attributed_rows: number;
    content_items: number;
    content_share_pct: number;
  };
}

/** 取 AI 法官真實指標（overview 首頁覆蓋 mock 的 ai_judge 區塊用）。 */
export const getAiJudgeOverview = () => j<AiJudgeOverviewResp>('/api/overview/ai-judge');
