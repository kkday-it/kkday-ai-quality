# app/judge — 判決引擎

初判歸因（prejudge）核心 + 批量編排 + 上傳落庫 + LLM client + 離線 label-free 準確度分析。判準一律讀
`docs/prompts/*.md`（Prompt-as-Source，經 `prompt_source` 載入；禁在此自寫判準）；分類結構
索引讀 `app/core.ai_judge`；極性閘門/證據政策（含 `evidence_gated_domains`）/信心閾值/prejudge 旋鈕讀
`judgment.json`（`prejudge._cfg`，2026-07-13 起併入原 `global_rule.json`）；無 token（stub）走啟發式讓
零 key 跑通閉環。

| 項目 | 職責 |
|---|---|
| `prompt_source.py` | 判決 prompt 唯一真相源載入層：`docs/prompts/*.md`（7 支：00_polarity + 01~06_C-N）DB-first→檔案 fallback；`parse_md`/`load`/`validate`（存檔自洽 drift 護欄：facet_catalog codes == Schema l2_code enum）/`structure()`（六域分類結構，供 `ai_judge` loader 建索引，取代已退役的 DB 規則樹）。 |
| `prejudge.py` | 單條進線 → **多歸因** TicketFinding 清單（`to_findings`）：Stage0 略過純好評 → Stage1 極性閘門（`_pack_polarity` 吃 00_polarity）→ Stage2 多歸因（`_attrs_pack` 六域 prompt 並行各自判斷是否命中該域 → 合流去重排序 + 信心閘門）。**G1 自動確認路由**（`_route_status`）：auto_accept+judged→`auto_confirmed`（免人工佇列）+ `audit_sample_rate` 抽樣回 new 防自動化偏誤。 |
| `prejudge_batch.py` | in-mem job registry + ThreadPool 併發判決（copy_context 帶 contextvar）+ 進度/花費快照 + 暫停/恢復/停止（背壓逐筆提交）。 |
| `prompt_eval.py` | 單支 Prompt 評測核心（Prompt-as-Source 調適閉環量測層）：抽樣（production md5 全表 / B1 filtered 篩選子集 / B3 mock 邊界測試集）→ 跑該支 prompt → 純函式指標（`compute_domain_metrics`/`compute_polarity_metrics`）+ 分歧清單；`domain_verdicts()` 六域並行診斷（B0 overlay：`reason`/`abstain_reason` 動態附 schema，只在評測期注入，不改 md、production 判決零影響）；`classify_one` 單條 dry-run（歸因列表「測試」，不落庫）；`run_eval` 供 UI `/v1/judgment/prompt-eval` 與 CLI `scripts/tools/eval_prompt_single.py` 共用。 |
| `prompt_testcases.py` | 邊界測試集驗證 + CSV 解析（B3）：`validate_row`（gold_l1 對 `prompt_source.structure()` 域清單驗、gold_l2 對該域 facets 驗、expected_polarity 三態驗）+ `parse_csv`（CSV bytes → 合法/錯誤 rows，錯誤含行號）；CSV 上傳走本模組獨立輕量 parse，不進 `ingest/` 管線（語義不同：`ingest` 為 5 反饋來源設計）。 |
| `ingest/` | 上傳落庫：`entry.read_sheets`（CSV/xlsx 讀成工作表）+ `upload_batch`（背景 job 分塊 → `db.insert_source_batch` 各來源專表 + $ 欄淨化）。 |
| `llm/client.py` | LLM client（Structured Outputs、prompt caching、per-provider token、usage sink 回報、stub 模式）+ OpenAI SDK 直呼（`_complete`；base_url 可覆寫打各 OpenAI-compatible 端點）+ **exact-match 結果快取**（diskcache·`data/llm_cache`·`env.llm_exact_cache`：key=model+messages+response_format+effort 雜湊，prompt 內嵌規則正文→失效粒度自動精準；命中零 token 零延遲不落 llm_usage；讀取閘 `set_llm_cache_read`——批次開/顯式重判與 A/B 評測關，寫入恆開）+ serving tier（flex -50%·429 自動回退標準）；對外介面不變。 |
| `accuracy/` | 離線準確度報表（package）：僅 **label-free**（`labelfree.py`，Cleanlab 一致性自證，有循環論證侷限；以 judgments.l2_code 為分類軸）；`run()` 產 accuracy.{md,json}。非線上服務。 |

> 2026-07-13：判決引擎全面退役 legacy JSON 規則樹路徑（cascade Stage A/B、`_attrs_l2_multi`、
> `_finalize_attr`/`_sanitize_l3` 等 26 個函式），`prompt_pack`（`_attrs_pack`/`_pack_polarity`）為
> 唯一判決引擎；`dspy_engine.py`（Phase 3 實驗性鷹架，zero production caller）與 `rule_refeed.py`
> （反哺飛輪，寫回對象隨樹一併消失）同批退役刪除。
> 2026-07-14：標真值（true_label）功能整支退役——連帶移除 `accuracy/supervised.py` +
> `accuracy/ensemble_agreement.py`（監督臂失去人工真值來源）與 `calibration.py`（零生產呼叫、
> 100% true_label 監督擬合）；凍結的跨廠 `ensemble.py` + `_ensemble_attrs`/`_use_config`/`_sample_hit`
> 一併刪除（生產不可達死碼）。判決恆單模型（model_votes 欄保留供歷史相容，恆 NULL）。
