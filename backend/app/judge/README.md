# app/judge — 判決引擎

初判歸因（prejudge）核心 + 批量編排 + 上傳落庫 + LLM client + 離線準確度分析 + 信心校準。判準一律讀
`docs/prompts/prompts/*.md`（Prompt-as-Source，經 `prompt_source` 載入；禁在此自寫判準）；結構索引讀
`app/core` 的 `ai_judge`/`global_rule`；無 token（stub）走啟發式讓零 key 跑通閉環。

| 項目 | 職責 |
|---|---|
| `prompt_source.py` | 判決 prompt 唯一真相源載入層：`docs/prompts/prompts/*.md`（7 支：00_polarity + 01~06_C-N）DB-first→檔案 fallback；`parse_md`/`load`/`validate`（存檔自洽 drift 護欄：facet_catalog codes == Schema l2_code enum）/`structure()`（六域分類結構，供 `ai_judge` loader 建索引，取代已退役的 DB 規則樹）。 |
| `prejudge.py` | 單條進線 → **多歸因** TicketFinding 清單（`to_findings`）：Stage0 略過純好評 → Stage1 極性閘門（`_pack_polarity` 吃 00_polarity）→ Stage2 多歸因（`_attrs_pack` 六域 prompt 並行各自判斷是否命中該域 → 合流去重排序 + 信心閘門）。**G1 自動確認路由**（`_route_status`）：auto_accept+judged→`auto_confirmed`（免人工佇列）+ `audit_sample_rate` 抽樣回 new 防自動化偏誤。 |
| `prejudge_batch.py` | in-mem job registry + ThreadPool 併發判決（copy_context 帶 contextvar）+ 進度/花費快照 + 暫停/恢復/停止（背壓逐筆提交）。 |
| `ingest/` | 上傳落庫：`entry.read_sheets`（CSV/xlsx 讀成工作表）+ `upload_batch`（背景 job 分塊 → `db.insert_source_batch` 各來源專表 + $ 欄淨化）。 |
| `llm/client.py` | LLM client（Structured Outputs、prompt caching、per-provider token、usage sink 回報、stub 模式）+ OpenAI SDK 直呼（`_complete`；base_url 可覆寫打各 OpenAI-compatible 端點）+ **exact-match 結果快取**（diskcache·`data/llm_cache`·`env.llm_exact_cache`：key=model+messages+response_format+effort 雜湊，prompt 內嵌規則正文→失效粒度自動精準；命中零 token 零延遲不落 llm_usage；讀取閘 `set_llm_cache_read`——批次開/顯式重判與 A/B 評測關，寫入恆開）+ serving tier（flex -50%·429 自動回退標準）；對外介面不變。 |
| `accuracy.py` | 離線準確度報表：**label-free**（Cleanlab 一致性自證，有循環論證侷限）+ **真值監督**（`analyze_supervised`：true_label 到位時算 L1 域真準確率 + P/R/F1 + 誤判對，sklearn.metrics）；`run()` 同產 accuracy.{md,json} + accuracy_supervised.{md,json}。非線上服務。 |
| `calibration.py` | 信心校準（conf_raw→calibrated）：以人工 true_label 擬合 isotonic/Platt（sklearn，`.[accuracy]` extra，缺則優雅 skip）+ ECE 前後對比；`apply_calibration` 為純 numpy（線上安全，無擬合則 identity）。補回已刪 calibration 表閉環；線上套用/閾值建議屬 Phase 4。 |

> 2026-07-13：判決引擎全面退役 legacy JSON 規則樹路徑（cascade Stage A/B、`_attrs_l2_multi`、
> `_finalize_attr`/`_sanitize_l3` 等 26 個函式），`prompt_pack`（`_attrs_pack`/`_pack_polarity`）為
> 唯一判決引擎；`dspy_engine.py`（Phase 3 實驗性鷹架，zero production caller）與 `rule_refeed.py`
> （反哺飛輪，寫回對象隨樹一併消失）同批退役刪除。
