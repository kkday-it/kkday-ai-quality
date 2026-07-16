/** 後端 run_log.emit 的條目形狀（backend/app/judge/run_log.py）；`PrejudgeLogDrawer`（判決歷史回看落庫快照）
 * 與 Prompt 測試沙盒（即時 SSE + 歷史回看 log 快照）共用同一形狀，供 `PrejudgeLogView` 渲染。 */
export interface LogEntry {
  ts: number;
  /** stage｜llm_request｜llm_prompt｜llm_response｜llm_note｜error */
  kind: string;
  stage: string;
  message: string;
  /** 同一次 LLM 調用的分組鍵（polarity / C-1..C-6）；前端據此把 request/prompt/response 聚合成一個 tab。 */
  label?: string;
  data?: Record<string, unknown>;
}
