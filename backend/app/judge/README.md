# app/judge — 判決引擎

初判歸因（prejudge）核心 + 批量編排 + 上傳落庫 + LLM client + 離線準確度分析 + 信心校準。判準一律讀
`app/core` 的 `ai_judge`/`global_rule`（禁在此自寫判準）；無 token（stub）走啟發式讓零 key 跑通閉環。

| 項目 | 職責 |
|---|---|
| `prejudge.py` | 單條進線 → **多歸因** TicketFinding 清單（`to_findings`）：Stage0 略過純好評 → Stage1 極性閘門 → Stage2 多歸因（候選域 canon 聚焦選 L1/L2/L3 + 信心）。cascade（config-gated）走 Stage A 多域→逐域 Stage B。**G1 自動確認路由**（`_route_status`）：auto_accept+judged→`auto_confirmed`（免人工佇列）+ `audit_sample_rate` 抽樣回 new 防自動化偏誤。 |
| `prejudge_batch.py` | in-mem job registry + ThreadPool 併發判決（copy_context 帶 contextvar）+ 進度/花費快照 + 暫停/恢復/停止（背壓逐筆提交）。 |
| `ingest/` | 上傳落庫：`entry.read_sheets`（CSV/xlsx 讀成工作表）+ `upload_batch`（背景 job 分塊 → `db.insert_source_batch` 各來源專表 + $ 欄淨化）。 |
| `llm/client.py` | LLM client（Structured Outputs、prompt caching、per-provider token、usage sink 回報、stub 模式）+ gateway 分派：`env.llm_gateway` 預設 `openai`（SDK 直呼），設 `litellm` 走 LiteLLM 統一 gateway（cost 正規化，fallback/語意快取待 Phase 7）；對外介面不變。 |
| `accuracy.py` | 離線準確度報表：**label-free**（Cleanlab 一致性自證，有循環論證侷限）+ **真值監督**（`analyze_supervised`：true_label 到位時算 L1 域真準確率 + P/R/F1 + 誤判對，sklearn.metrics）；`run()` 同產 accuracy.{md,json} + accuracy_supervised.{md,json}。非線上服務。 |
| `calibration.py` | 信心校準（conf_raw→calibrated）：以人工 true_label 擬合 isotonic/Platt（sklearn，`.[accuracy]` extra，缺則優雅 skip）+ ECE 前後對比；`apply_calibration` 為純 numpy（線上安全，無擬合則 identity）。補回已刪 calibration 表閉環；線上套用/閾值建議屬 Phase 4。 |
| `dspy_engine.py` | **DSPy 判決引擎鷹架（Phase 3 旗艦，選用 `.[dspy]`）**：polarity/L1-L3 改 DSPy Signature 承載、以 true_label 編譯自優化（BootstrapFewShot→MIPROv2）；**業務語義（evidence-cap/abstain/淨化）reuse prejudge**，DSPy 僅替換 LLM 分類層。⚠️ 需標註(≥50)+LLM key 才能編譯/執行→現為 **flagged-off 並行鷹架**，`compile_and_persist` 無 label/key 優雅 skip、不接主路徑；mock 可測結構/metric/組合。 |
