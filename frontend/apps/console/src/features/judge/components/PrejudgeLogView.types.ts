/** 後端 run_log.emit 的條目形狀（backend/app/judge/run_log.py）；`PrejudgeLogDrawer`（即時 SSE）
 * 與 Prompt 測試沙盒（即時 SSE + 歷史回看 log 快照）共用同一形狀，供 `PrejudgeLogView` 渲染。 */
export interface LogEntry {
  ts: number;
  /** stage｜llm_request｜llm_prompt｜llm_response｜llm_note｜error */
  kind: string;
  stage: string;
  message: string;
  data?: Record<string, unknown>;
}
