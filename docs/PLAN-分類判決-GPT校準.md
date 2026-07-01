# PLAN · 分類判決 GPT 落地 + 信心度校準

> 把「問題分類層級結構（完整覆蓋版 v2，Confluence 2137915398）」+「prompt-工程-準確性保證」落地到 backend judge pipeline。
> 狀態：**規劃中 · 暫不動工（2026-06-26）**。判決鏈用 **OpenAI GPT**（llm/client.py 已支援 per-stage model override，預設 gpt-5-mini）。

> 🚧 **前置阻塞（Gate -1）**：本計劃所有 Phase（含 Phase 0 golden-eval harness）**需先完成「6 源來源匯總架構」**——intake 把 conversations/freshdesk/reviews/app_feedback/mixpanel/ai_review_summary 正規化成統一 NormalizedTicket → 批次導入本地 DB（kkdb_ai_quality.db）。來源匯總未架好前，**不抽樣、不寫 classify_eval.py、不接 pipeline**。待來源匯總就位再啟動 Phase 0。

## 0. Migration Delta（新設計 vs 現有 code，先對齊免重工）

| 項目 | 現有（backend/app/judge） | 新設計（SSOT v2） | 動作 |
|---|---|---|---|
| 歸因域 | verdict 6 類（real_config/content_missing/unclear/contract_breach/customer_misread/escalate_ops） | **7 歸因域** + verdict 8（加 force_majeure ⑦ / pre_sale_inquiry） | 擴 schema verdict enum + 加 root_cause_domain |
| 分類產出 | classify 直接給單一 dimension+verdict+conf(0-1) | 進線給 **root_cause_candidates[]**（候選1..N），判決才收斂單一 | classify 改輸出候選集；arbiter 跑金字塔收斂 |
| 信心度 | 0-1 浮點（啟發式/LLM 自報） | **0-10，signal+self-consistency+logprobs 融合** | 加 confidence 融合層 + logprobs 取數 |
| 責任/action | owner_role 部分有 | responsible_party + action_owner 皆 **derived(lookup)** | 加 lookup 表（domain+判定層→owner） |
| 證據 | 無顯式欄 | **evidence_level** + 缺證據封頂硬閘 | schema 加 evidence_level，arbiter 套封頂 |
| 商品類別 | 無 | product_category(bd_tag) facet + eSIM 無②規則 | 進線帶 bd_tag；Rule 標適用商品類型 |

> ⚠️ 不破壞既有 status 契約（adequacy 5 status）與防幻覺鐵則（content_missing→不重生）。新欄位**加法擴充**，非重寫。

## Phase 0 · Golden-Eval Harness（先量測，不改 pipeline）★最先做

**目標**：抽真實 ticket 跑 Stage1+2 GPT 模板，產出預測 + 三來源信心度，供人工標註成 golden set。

- [ ] `backend/scripts/classify_eval.py`（新）：
  - 從 `data/conversations/*.csv` 分層抽樣 N=30（跨 6 tag1 / 售前售後 / 不同 bd_tag）
  - Stage1 預判：GPT-nano + **Structured Outputs(strict JSON, enum)** → symptom_tag/候選域/prior_conf
  - Stage2 判決：GPT-5.4 + 金字塔 prompt + **temp=0 + logprobs=true** → domain/verdict/suspected_field + token_prob
  - self-consistency：再 sample N=2（temp 0.7）算 agreement
  - confidence = clip(0.4·signal + 0.3·agreement·10 + 0.3·token_prob·10, cap)
  - 輸出 `backend/data/eval/classify_eval_<ts>.jsonl`（含 reasoning_trace + 三來源分數，供人工標 is_correct）
- [ ] prompt 模板落 `backend/app/judge/prompts_v2/`（stage1.txt / stage2_pyramid.txt / stage2b_dimension.txt），注入：29-tag taxonomy + 7域 + 圖5 商品類別×域表 + few-shot 4 例
- [ ] reference Canon 注入：判決時拉 `codex.adequacy_criteria` + `datasource.product` extract_fields（既有）
- **驗收**：30 筆全跑通、輸出含 logprob token_prob、無 schema 解析錯（Structured Outputs 保證）

## Phase 1 · 信心度校準（golden set → 回歸）

- [ ] 人工標註 ≥200 筆（擴 Phase 0 樣本）：每筆標 true_domain / true_verdict / is_correct
- [ ] `backend/scripts/calibrate_confidence.py`（新）：算 **ECE / Brier**、畫 reliability curve；grid/回歸搜最佳 w1/w2/w3 + 信號權重 S1~S6（最小化 ECE）
- [ ] 定版閾值：≥8 自動 / 5-7 jury / <5 人工（對齊業界 0.75-0.85）
- **驗收**：校準後 ECE 下降；產出 `judge_logic_config.json` 新增 `confidence_weights` 區塊

## Phase 1.5 · 各階段模型可配置（Settings 面板擴充）

> 模型 tier 表 **不寫死＝各階段默認**；沿用 Settings.vue「各階段覆寫（留空＝繼承全域）」機制（client.py `stage_overrides` 已支援 model/effort per-stage）。

**會 call AI 的階段 = 需要 config row**（現有只 L2/L3）：

| Settings row | pipeline | 角色 | 默認 model | effort |
|---|---|---|---|---|
| L2 分類（已有） | classify._real | 症狀+候選域·高量輕量 | nano 層 | low |
| L3 充分度（已有） | adequacy._real | 內容對照法典逐欄·細緻 | 5.4 / 5.4-mini | medium |
| L4 判決仲裁（新增·選配） | arbiter LLM 閘 | 金字塔模糊閘收斂單一域 | 5.4 / o3 | medium-high |
| 覆核 jury（新增·條件） | 高風險覆核 | ②contract_breach/conf5-7 觸發 | o3 / 5.5 | high |

**不需 config（無 AI）**：action 派發 / 責任方·action_owner derive / 確定性閘 / 機器規則。

- [ ] `Settings.vue`：新增 L4 判決仲裁、覆核 jury 兩 row（model+effort，留空繼承）；model 下拉續用 `llm.list_models()` 動態
- [ ] `llm/client.py`：`_resolve(stage)` 支援新 stage key（l4_arbiter / jury）
- [ ] 每 stage 默認值集中一處（curated default），全域 default 維持 gpt-5-mini
- [x] **模型清單改本地預設·不打 API（2026-06-26 已實作，未 push）·SSOT=`config/defaults.json`**：`/v1/models` 倒出帳號全模型（embedding/語音/影像/legacy davinci-babbage-ada/ft-kkday 垃圾、可能誤選 whisper）→ 改讀 `config/defaults.json`（前後端共用 SSOT，settings.py:22 已載入）。四處改：① defaults.json `providers[].defaultModels` 強→弱+加 o 系列、`modelMeta` 加價格(input/output $/1M)；② settings.py 暴露 `LLM_PROVIDERS`；③ `client.py list_models()` 改讀 LLM_PROVIDERS 依 base_url 選 provider 回 defaultModels（不打 /v1/models）；④ provider.constant.ts 註解改強→弱。
  - **GPT 清單（強→弱+價格）**：gpt-5.5-pro $30/$180 / gpt-5.5 $5/$30 / gpt-5.4-pro $30/$180 / o3-pro ~$20/$80 / gpt-5.4 $2.50/$15 / o3 $2/$8 / o4-mini $0.55/$2.20 / gpt-5.4-mini $0.75/$4.50 / gpt-5.4-nano $0.20/$1.25。
  - **新增模型只改 `config/defaults.json` 一處**（勿在 client.py 另寫平行清單）。待辦：Gemini/Doubao 補 defaultModels（目前各 1 placeholder）。

## Phase 2 · 接進判決鏈（改 pipeline）

- [ ] schema：加 `root_cause_domain(7)` / `root_cause_candidates[]` / `evidence_level` / `product_category` / `action_owner`；verdict enum +force_majeure +pre_sale_inquiry
- [ ] `classify.py`：_real 改輸出候選集（不單選）；注入 stage1 prompt
- [ ] `arbiter.py`：實作**互斥判準金字塔**閘0~5 收斂單一域 + 缺證據封頂硬閘（evidence_level<with_order ⇒ 禁②）
- [ ] 信心度融合層（新 `confidence.py`）：signal_prior + agreement(跑N=2) + logprobs token_prob
- [ ] action 派發：`action_owner = lookup(domain, 判定層)`（deterministic，非 LLM）
- [ ] GPT 省錢：Rule Canon+taxonomy 放固定 system prefix（**prompt caching −90%**）；批次走 **Batch API −50%**
- **驗收**：5 fixture 票端到端，7 域/候選集/evidence 封頂/action_owner 正確

## Phase 3 · Dashboard 分類成熟度（前端）

- [ ] L5 加「分類成熟度」視角：信心 ≥8 已判決 / 5-7 待覆核 / <5 待證據（三態 KPI + 濾鏡）
- [ ] 候選集未收斂的 finding 標 tentative（v-if 條件渲染）

## 跨 Provider / 模型選用（2026-06-26 確認）

- **三 provider 都支援 Structured Outputs + logprobs**：GPT(strict CFG 100%) / Gemini(responseSchema + responseLogprobs 每 token) / ByteDance Doubao-Ark(JSON schema beta + logprobs，**OpenAI 相容端點**換 base_url 即可)。
- **設計成 capability-detected 非 provider-hardcoded**：加 `supports_logprobs`/`supports_strict_schema` 旗標；缺 logprobs → 信心度公式丟 token_prob 項重分配權重（退 signal+agreement）；logprobs 欄位形狀各家不同 → 薄 adapter 抽「被選中 domain token 機率」；Structured Outputs 嚴格度不一 → 內容正確性一律 code 端驗。
- **⚠️ 不用 legacy `davinci:ft-kkday-2023-05-*`**：KKday 2023 自訓 GPT-3 davinci fine-tune（非 instruction-tuned、舊 completions、多半已退役/legacy 計費、用途不明）；下拉出現只因 list_models() 列全帳號。判決鏈一律用現行 GPT-5.x（或 Gemini/Doubao 對應）。
- **預判(L2)模型**：nano 起手（Structured Outputs+few-shot，業界 86-96%），**不需 5.4**（5.4 留給 L3 充分度/仲裁硬活）。用 golden set 實測 nano vs mini 再定。
- **預判「增加更多模型」＝多模型投票 ensemble（可選·提準）**：研究證實多便宜小模型投票可逼近單大模型、**跨家盲點不相關互補**（LLM-as-jury / cross-reviewed small-model ensemble）；附帶＝多模型一致度本身就是信心度的 agreement 信號（一魚兩吃）。**但高量階段勿每筆跑三模型** → 條件式：先單模型(nano)+logprob，**難案(token_prob低/候選打平)才 fan-out 2-3 模型(可跨 GPT/Gemini/Doubao)多數決**＝cascade→難案 ensemble，成本/準確最佳。
- **小架構調整**：Settings 預判 stage 由「單一 model 覆寫」擴成「**模型清單 + 投票策略（單一/N 模型多數決/跨 provider）**」（P1.5 延伸）。

## 依賴 / Gate
- OpenAI key：🟢 已生效（gpt-5-mini，llm/client.py settings/env）
- 商品內容 L0：方案①字面剪枝即時撈（product_refresh.py 已實作，live BQ 待 Gary 權限）
- 訂單履約：②contract_breach 需訂單佐證，BQ orders 待權限（無則 evidence_level 降、不判②）

## 對應文件
- 分類層級 SSOT：Confluence 2137915398 · repo `docs/問題分類層級結構-完整版.md`
- prompt/準確性：repo `docs/prompt-工程-準確性保證.md`
- 症狀全集：`data/symptom_taxonomy/coverage-full.md` · 映射：`docs/症狀-歸因映射表.md`
- 既有判決鏈：`backend/app/judge/{classify,arbiter,diagnose,pipeline,codex}.py` + `judge_logic_config.json`
