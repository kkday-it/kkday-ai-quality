# app/judge — 初判引擎

初判歸因（prejudge）核心 + 批量編排 + 上傳落庫 + LLM client + 離線 label-free 準確度分析。判準一律讀
`prompts/*.md`（Prompt-as-Source，經 `prompt_source` 載入；禁在此自寫判準）；分類結構
索引讀 `app/core.ai_judge`；極性閘門/證據政策/信心閾值/prejudge 旋鈕讀
`prejudge.json`＋`verdict.json`（`prejudge._cfg` 合併載入）；無 token（stub）走啟發式讓
零 key 跑通閉環。

| 項目 | 職責 |
|---|---|
| `prompt_source.py` | 初判 prompt 唯一真相源載入層：`prompts/*.md`（7 支：00_polarity + 01~06_C-N）DB-first→檔案 fallback；`parse_md`/`load`（`## Taxonomy` 派生 code 注入 Schema `l2_code.enum`）/`validate`（各節可解析＋`## Taxonomy` 可解析且≥1 facet）/`structure()`（六域分類結構自 `## Taxonomy` root 派生，供 `ai_judge` loader 建索引）。 |
| `prejudge.py` | 單條進線 → **多歸因** TicketFinding 清單（`to_findings`）：Stage1 極性閘門（`_pack_polarity` 吃 00_polarity）→ 域路由剪枝（`domain_router.decide`，config 開關、預設關）→ Stage2 多歸因（`_attrs_pack` 域 prompt 並行各自判斷是否命中該域，`pids` 可選子集 → 合流去重排序 + 信心閘門；候選域全空手自動補跑其餘域兜底；單域失敗 `domain_retry` 有界重試、耗盡整筆 fail-loud）。**G1 自動確認路由**（`_route_status`）：auto_accept+judged→`auto_confirmed`（免人工佇列）+ `audit_sample_rate` 抽樣回 new 防自動化偏誤。批次 effort 硬上限 `cap_batch_reasoning_effort`（防 xhigh 檔位誤用於全量批次）。stub 模式極性走純 rating 啟發式分流。 |
| `domain_router.py` | **Embedding 域路由**（候選域剪枝）：text → OpenAI embedding（`client.embed_one`）→ 每域 LogisticRegression 機率（純 python 內積，零 numpy 依賴）→ 高召回閾值選候選域。三層防線：①訓練端 holdout recall ≥99.5% 才給閾值、弱域 always_on ②候選空手兜底補跑（`_resolve_attrs_multi`）③ `shadow_rate` 影子抽樣全跑＋虛擬比對、漏域落 `attribution_history kind='router_shadow'`。權重＝`data/router/weights.json`（`scripts/tools/train_domain_router.py` 訓練）；任何故障 fail-open 全域跑。config＝prejudge.json `prejudge.domain_router`（enabled 預設 false）。 |
| `prejudge_batch.py` | job 快照共用機制層見 `core.job_registry.JobStore`；ThreadPool 併發初判（copy_context 帶 contextvar）+ 進度/花費快照 + 暫停/恢復/停止（背壓逐筆提交）+ 失敗筆明細 `failed_items`（上限 200，供前端「重新初判本批失敗筆」）+ 失敗留痕 `attribution_history kind='failure'`（供隱式重撈上限 `max_implicit_retries`）+ 依生效 model 的併發上限（`prejudge.max_workers_for` ∩ env 硬天花板）作 ceiling + **AIMD 自適應併發** `_ConcurrencyGovernor`（item 因 429 失敗→乘性收縮、清空→加性回升，保證有能力時爬回 ceiling、過載才降；prejudge.json `adaptive_concurrency` 旋鈕）。 |
| `prompt_debug.py` | 售後根因 Prompt 調試：Google Doc 分類 SSOT 渲染、strict Structured Outputs、SSE token 串流、category 相依欄位校驗與單次 usage/費用估算；只記 `llm_usage`，不污染正式 attributions/attribution_history。 |
| `prompt_eval.py` | Prompt 評測共用核心：`domain_verdicts()` 六域並行診斷（B0 overlay：`reason`/`abstain_reason` 動態附 schema，不改 md、production 初判零影響，`pids` 可選子集）；`sandbox_classify` 供歸因列表「Prompt 測試」沙盒——使用者任意勾選 prompt 子集、ungated（不受正式歸因閘門限制）；`compute_domain_metrics`/`compute_polarity_metrics` 純函式指標，供 CLI `scripts/tools/eval_prompt_single.py` 獨立引用。 |
| `prompt_sandbox.py` | Prompt 測試沙盒輕量 job registry（同用 `core.job_registry.JobStore`）：`start` 立即回 job_id，背景 thread bind `run_log`（不設筆數上限）→ 逐筆並行 `sandbox_classify` → 結束落 `prompt_sandbox_runs`（results + 完整 log 快照）。與 `prejudge_batch.py` 同 pattern 但不含暫停/取消/自適應併發。 |
| `ingest/` | 上傳落庫：`entry.read_sheets`（CSV/xlsx 讀成工作表）+ `upload_batch`（背景 job 分塊 → `db.insert_source_batch` 各來源專表 + $ 欄淨化；job 快照亦走 `core.job_registry.JobStore`）。 |
| `llm/client.py` | LLM client（Structured Outputs、prompt caching、per-provider token、usage sink 回報、stub 模式）+ OpenAI SDK 直呼（`_complete`；base_url 可覆寫打各 OpenAI-compatible 端點）+ **exact-match 結果快取**（diskcache·`data/llm_cache`·`env.llm_exact_cache`：key=model+messages+response_format+effort 雜湊，prompt 內嵌規則正文→失效粒度自動精準；命中零 token 零延遲不落 llm_usage；讀取閘 `set_llm_cache_read`——批次開/顯式重新初判與 A/B 評測關，寫入恆開）+ serving tier（flex -50%·flex 429 resource_unavailable 回退標準）+ **typed-exception 錯誤分流**（timeout/一般 429/5xx 快速失敗、不做無用降級；僅非 OpenAI 400 才 json_schema→json_object 降級）；對外介面不變。 |
| `accuracy/` | 離線準確度報表（package）：僅 **label-free**（`labelfree.py`，Cleanlab 一致性自證，有循環論證侷限；以 attributions.l2_code 為分類軸）；`run()` 產 accuracy.{md,json}。非線上服務。 |
