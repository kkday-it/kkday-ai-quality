# app/judge — 判決引擎

初判歸因（prejudge）核心 + 批量編排 + 上傳落庫 + LLM client + 離線 label-free 準確度分析。判準一律讀
`prompts/*.md`（Prompt-as-Source，經 `prompt_source` 載入；禁在此自寫判準）；分類結構
索引讀 `app/core.ai_judge`；極性閘門/證據政策/信心閾值/prejudge 旋鈕讀
`judgment.json`（`prejudge._cfg`，2026-07-13 起併入原 `global_rule.json`）；無 token（stub）走啟發式讓
零 key 跑通閉環。

| 項目 | 職責 |
|---|---|
| `prompt_source.py` | 判決 prompt 唯一真相源載入層：`prompts/*.md`（7 支：00_polarity + 01~06_C-N）DB-first→檔案 fallback；`parse_md`/`load`（`## Taxonomy` 派生 code 注入 Schema `l2_code.enum`）/`validate`（各節可解析＋`## Taxonomy` 可解析且≥1 facet）/`structure()`（六域分類結構自 `## Taxonomy` root 派生，供 `ai_judge` loader 建索引，取代已退役的 DB 規則樹）。 |
| `prejudge.py` | 單條進線 → **多歸因** TicketFinding 清單（`to_findings`）：Stage0 略過純好評 → Stage1 極性閘門（`_pack_polarity` 吃 00_polarity）→ Stage2 多歸因（`_attrs_pack` 六域 prompt 並行各自判斷是否命中該域 → 合流去重排序 + 信心閘門；單域失敗 `domain_retry` 有界重試、耗盡整筆 fail-loud）。**G1 自動確認路由**（`_route_status`）：auto_accept+judged→`auto_confirmed`（免人工佇列）+ `audit_sample_rate` 抽樣回 new 防自動化偏誤。 |
| `prejudge_batch.py` | in-mem job registry + ThreadPool 併發判決（copy_context 帶 contextvar）+ 進度/花費快照 + 暫停/恢復/停止（背壓逐筆提交）+ 失敗筆明細 `failed_items`（上限 200，供前端「重判本批失敗筆」）+ 失敗留痕 `judgment_history kind='failure'`（供隱式重撈上限 `max_implicit_retries`）+ 依生效 model 的併發上限（`prejudge.max_workers_for` ∩ env 硬天花板）作 ceiling + **AIMD 自適應併發** `_ConcurrencyGovernor`（item 因 429 失敗→乘性收縮、清空→加性回升，保證有能力時爬回 ceiling、過載才降；judgment.json `adaptive_concurrency` 旋鈕）。 |
| `prompt_eval.py` | Prompt 評測共用核心：`domain_verdicts()` 六域並行診斷（B0 overlay：`reason`/`abstain_reason` 動態附 schema，不改 md、production 判決零影響，`pids` 可選子集）；`classify_one` 單條 production 閘門 dry-run（不落庫）；`sandbox_classify` 供歸因列表「Prompt 測試」沙盒——使用者任意勾選 prompt 子集、ungated（不受正式歸因閘門限制）；`compute_domain_metrics`/`compute_polarity_metrics` 純函式指標，供 CLI `scripts/tools/eval_prompt_single.py` 獨立引用。 |
| `prompt_sandbox.py` | Prompt 測試沙盒輕量 job registry：`start` 立即回 job_id，背景 thread bind `run_log`（不設筆數上限）→ 逐筆並行 `sandbox_classify` → 結束落 `prompt_sandbox_runs`（results + 完整 log 快照）。與 `prejudge_batch.py` 同 pattern 但不含暫停/取消/自適應併發。 |
| `ingest/` | 上傳落庫：`entry.read_sheets`（CSV/xlsx 讀成工作表）+ `upload_batch`（背景 job 分塊 → `db.insert_source_batch` 各來源專表 + $ 欄淨化）。 |
| `llm/client.py` | LLM client（Structured Outputs、prompt caching、per-provider token、usage sink 回報、stub 模式）+ OpenAI SDK 直呼（`_complete`；base_url 可覆寫打各 OpenAI-compatible 端點）+ **exact-match 結果快取**（diskcache·`data/llm_cache`·`env.llm_exact_cache`：key=model+messages+response_format+effort 雜湊，prompt 內嵌規則正文→失效粒度自動精準；命中零 token 零延遲不落 llm_usage；讀取閘 `set_llm_cache_read`——批次開/顯式重判與 A/B 評測關，寫入恆開）+ serving tier（flex -50%·flex 429 resource_unavailable 回退標準）+ **typed-exception 錯誤分流**（timeout/一般 429/5xx 快速失敗、不做無用降級；僅非 OpenAI 400 才 json_schema→json_object 降級）；對外介面不變。 |
| `accuracy/` | 離線準確度報表（package）：僅 **label-free**（`labelfree.py`，Cleanlab 一致性自證，有循環論證侷限；以 judgments.l2_code 為分類軸）；`run()` 產 accuracy.{md,json}。非線上服務。 |
