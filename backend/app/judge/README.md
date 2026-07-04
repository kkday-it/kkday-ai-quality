# app/judge — 判決引擎

初判歸因（prejudge）核心 + 批量編排 + 上傳落庫 + LLM client + 離線準確度分析。判準一律讀
`app/core` 的 `ai_judge`/`global_rule`（禁在此自寫判準）；無 token（stub）走啟發式讓零 key 跑通閉環。

| 項目 | 職責 |
|---|---|
| `prejudge.py` | 單條進線 → **多歸因** TicketFinding 清單（`to_findings`）：Stage0 略過純好評 → Stage1 極性閘門 → Stage2 多歸因（候選域 canon 聚焦選 L1/L2/L3 + 信心）。cascade（config-gated）走 Stage A 多域→逐域 Stage B。 |
| `prejudge_batch.py` | in-mem job registry + ThreadPool 併發判決（copy_context 帶 contextvar）+ 進度/花費快照 + 暫停/恢復/停止（背壓逐筆提交）。 |
| `ingest/` | 上傳落庫：`entry.read_sheets`（CSV/xlsx 讀成工作表）+ `upload_batch`（背景 job 分塊 → `db.insert_source_batch` 各來源專表 + $ 欄淨化）。 |
| `llm/client.py` | OpenAI 相容 client（Structured Outputs、prompt caching、per-provider token、usage sink 回報、stub 模式）。 |
| `accuracy.py` | 離線 label-free 準確度報表（Cleanlab confident-learning）；由 `scripts/audit/accuracy_audit` 觸發，非線上服務。 |
