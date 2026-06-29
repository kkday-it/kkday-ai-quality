# AI 法官 Prompt 工程與準確性保證（預判 / 判決 / action）

> 依據業界權威 LLM-judge / RCA / 信心度校準研究，設計三階段 prompt 架構 + 準確性技術，對齊 v3 內容治理流程（感知層→整合層→判定引擎→執行層）。2026-06-26。

## 一、權威技術 → 階段映射（為何這樣設計）

| 階段 | 採用的權威技術 | 怎麼用在 KKday | 出處 |
|---|---|---|---|
| **預判歸因 L1** | Routing-tree（每路徑獨立 prompt）+ few-shot(diverse edge·代表性最後)+ zero-shot CoT-lite + 便宜模型(GPT-4.1-nano/GPT-5.4-nano)+ Structured Outputs | 先按 tag1 分流再細分；輸出候選集**非單選**；給 prior confidence | Claude ticket routing / G-Eval / few-shot best practice |
| **判決歸因 L2-L4** | Decision-tree structured prompting（**=你的互斥判準金字塔**）+ 多候選+漸進證據(LATS)+ Reference-guided(Rule Canon 當參考答案)+ per-dimension judge(①每面向一裁判)+ evidence-sufficiency gate + anti-spurious guardrail | 逐閘判定唯一域；①比對 60 欄 Rule；每閘綁所需證據；禁混淆症狀/根因 | RCACopilot / LATS-RCA / G-Eval reference-guided / Waterloo RCA failures |
| **信心度校準** | Self-consistency(N=2-3 agreement)+ verbalized(折扣)hybrid + DINCO distractor + golden-set ECE/Brier 回歸校準 | confidence = f(signal_prior, agreement, verbalized)；金標集回歸取代拍腦袋權重 | Geng survey / Two-Samples-Enough / DINCO / G-Eval validate |
| **高風險覆核** | LLM-as-jury / Trust-Skeptic-Leader debate（**僅** ②contract_breach 或 conf 5-7 才觸發） | 多判決投票/辯論降「假陽性罰供應商」風險；高 entropy→升 Sonnet/人工 | LLM-as-jury(Arize) / Markov debate |
| **action 指派** | 多為 deterministic lookup（去冗餘，非 LLM）；僅 recommended_action 文字用 LLM | domain+判定層→action_owner；① writer_handoff | 本設計 |

**核心防錯原則（對齊 Waterloo RCA 失效模式）**：
- 禁「混淆症狀觀察者與根因來源」（客人抱怨 ≠ 供應商的錯）→ 金字塔強制先過商品頁/訂單
- evidence-sufficiency check：證據不足不得命中該閘（②無訂單禁判 contract_breach）
- KG/Rule-consistency filter：①content_missing **必須**指出缺哪個 suspected_field／違反哪條 Rule ID，否則不得判（防 spurious attribution）
- self-consistency > verbalized：**信心度別只信 LLM 自報**（過度自信），用多次判決的一致度為主

## 二、三階段 Prompt 模板

### Stage 1 · 預判分類器（L1 intake，GPT-4.1-nano / GPT-5.4-nano + Structured Outputs strict JSON）

```text
[System]
你是 KKday 客訴「預判分類器」。只做"描述性分類"，不下最終歸責判決。
鐵則：(1)只依「客人說什麼」+訂單metadata分類，禁止臆測商品頁內容；
(2)歸因域給「候選集合(1..N)」不選單一，除非症狀強指向；
(3)⑤客服營運不得進候選（判決層專屬）；(4)MECE：症狀 tag 互斥窮盡。

[症狀 taxonomy] {注入 29 列 tag1›tag2›tag3 全集}
[歸因域] ①商品內容 ②供應商履約 ③訂單交易 ④平台系統 ⑥客人理解 ⑦不可抗力（⑤排除）
[商品類別×域 適用表] {注入圖5，eSIM 無② 等}

[Few-shot]（4 個 diverse 邊界例，最具代表性放最後）
例1 eSIM 設定→… 例2 平台帳號→… 例3 退款→… 例4 找不到集合點→候選{①②⑥}

[Task] 先用 ≤3 句說明判斷理由（CoT），再輸出 JSON：
{symptom_tag1,symptom_tag2,symptom_tag3,trip_stage,product_category,
 failure_type,root_cause_candidates:[...],prior_confidence:0-10,reasoning}
```

### Stage 2 · 判決裁判（L2-L4，GPT-5.4 / o3 reasoning，decision-tree + reference-guided + logprobs）

```text
[System]
你是 KKday 內容治理「判決裁判」。依「互斥判準金字塔」逐閘判定"唯一"歸因域。
每閘需對應證據；證據不足→不命中該閘、往下或標 待補。禁無證據歸責。

[參考答案 Reference Canon]（RAG 注入，這是判 ① 的對照基準）
- 該商品 8面向/60欄 Rule（R1-1~R5-5）+ allow/deny 定義
- extract_fields 商品頁原文（prod_meetup/prod_fee/...）
- 訂單事實（時間/地點/狀態/FFC/退款）
- 客服+供應商對話原文

[互斥判準金字塔]（逐閘，每閘附"判此閘需要的證據"）
閘0 不可抗力(天災/罷工/惡劣天氣)? 需:外部事件 → ⑦ force_majeure
閘1 商品頁該寫沒寫/模糊? 需:商品頁 → ① content_missing/unclear（★必須指出缺哪欄+違反哪Rule ID）
閘2 內容寫了現場沒做到? 需:訂單+供應商回覆 → ② contract_breach
閘3 系統/交易失效? 客人可觸發=③ / 系統自動=④ → escalate_ops
閘4 客服處理失當? 需:客服紀錄 → ⑤
閘5 內容清楚客人仍誤解?(過①清楚度三判準) → ⑥ customer_misread
預設 證據不足 → 停候選集，標 待人工/待補

[Anti-spurious 守則]
- 客人抱怨「症狀」≠ 自動是供應商的錯；先過商品頁(閘1)排除①
- ① 必須引用具體 suspected_field + hit_rule_id，否則降為候選不得收斂
- ② 必須有訂單佐證，否則 evidence_level<with_order→降 content_unclear

[Task] 逐閘輸出 reasoning_trace（走到哪閘、用了哪些證據、為何命中/略過），最後 JSON：
{root_cause_domain,sub_cause,suspected_field,hit_rule_id:[...],verdict,
 evidence_used:[...],evidence_level,severity,verbalized_confidence:0-10,reasoning_trace}

[系統層] 本 prompt 跑 N=2 次（self-consistency）；agreement 計入最終 confidence。
```

### Stage 2b · ① 商品內容深鑽（per-dimension judge，G-Eval 解耦：每面向一裁判）

```text
[System] 你只評「{某一面向，如 集合資訊}」這一個維度，不評其他面向（criteria decomposition）。
[參考] 該面向對應 Rule（如 R2-1~R2-9）+ 商品頁 prod_meetup 原文 + 客訴。
[Task] 對照 Rule 逐條檢查：欄位存在?格式對?條件式必填齊? 給每條 pass/fail + 證據引用。
輸出 {dimension, per_rule:[{rule_id,pass,evidence}], dim_verdict, dim_confidence}
[probability-weighted] 若模型可給分數分佈，取期望值而非單一整數（G-Eval）。
```

### Stage 3 · Action 指派（多為 deterministic，少量 LLM）

```text
# 確定性 lookup（非 LLM，去冗餘）
action_owner = LOOKUP(root_cause_domain, 判定層)   # 見 §十二 映射表
responsible_party = LOOKUP(root_cause_domain)

# 僅這部分用 LLM：recommended_action 文案 + ① writer_handoff 重生
[System] 你是內容修正建議生成器。僅當 verdict∈{content_unclear,real_config_issue}
且 suspected_field∈{prod_name,prod_feature,prod_summary} 才產 writer 重生草稿（就地預覽不寫回）。
verdict=content_missing → writer_handoff=false（缺的是事實，禁生成）。
```

## 三、信心度校準（深化版：signal prior + self-consistency + 金標集）

研究結論：**self-consistency（多次判決一致度）比 verbalized（LLM 自報）更準**，hybrid 最佳，且 **2 個樣本就夠**。**GPT 額外提供 logprobs（token 機率）= 比 verbalized 更可信的第三信號**（自報 confidence 只是另一個會幻覺的字串）。三來源融合：

```
1. signal_prior = §十一 信號加權模型（起評+S1~S6−衝突，受封頂）        ← 規則先驗
2. agreement    = N=2 次 Stage2 判決的多數域一致度（self-consistency）  ← 經驗信號
3. token_prob   = exp(被選中 root_cause_domain token 的 logprob)        ← ★GPT 原生，取代 verbalized

confidence = clip( 0.4*signal_prior + 0.3*(agreement*10) + 0.3*(token_prob*10) , cap )
cap：①無商品頁≤5 / ②無訂單≤4 / ⑤無客服紀錄→不得判
衝突（N 次判不同域，或證據矛盾）→ confidence 砍半 + 標 needs_human
取數方式：temp=0 + logprobs=true 取 token_prob（單次）；中高風險再 sample N=2(temp 0.7) 算 agreement
verbalized 自報 → 拿掉或降到最低（research+OpenAI 都證實會幻覺、過度自信）
```

**金標集校準流程（取代拍腦袋權重，data-driven）**：
1. 人工標註 golden set（建議 ≥200 筆，跨 6 源、7 域均衡）
2. 跑判決鏈，得 (raw_confidence, 是否判對)
3. 算 **ECE / Brier score** 看校準誤差；畫 reliability curve
4. 對 signal 權重 + 上面三項係數做回歸/網格搜，最小化 ECE
5. 定期（季）重跑，weight 隨資料漂移更新

**信心度分層 → 路由（對齊 ticket routing 0.75-0.85 閾值習慣）**：
- ≥8 自動採信 → 派 action_owner
- 5-7 → **觸發 jury 覆核**（3 裁判投票 / Trust-Skeptic debate）→ 升信心或轉人工
- <5 → 待補/待人工

## 四、高風險覆核（jury / debate，僅選擇性觸發）

研究：jury panel 比單一裁判 +8~15%；但成本高 → **只對高風險用**：
- 觸發條件：verdict=contract_breach（罰供應商）或 confidence 5-7 或 severity≥P1
- 機制：3 個獨立裁判（不同 prompt 角度）投票 → 多數決；或 Trust/Skeptic 兩角色辯論一輪
- 仍分歧 / 高 entropy → 升 Sonnet 大模型或轉人工
- 低風險（⑥客人理解 / 低 severity）→ 單裁判即可，省成本

## 五、對齊 v3 內容治理整體流程

| v3 層 | 對應本 prompt 階段 |
|---|---|
| 感知層（A/B/C 捕捉 Feedback） | 進線 NormalizedTicket → Stage 1 預判 |
| 整合層（Rule ID 為共同語言彙整） | 候選集 + 跨源一致信號（S5）|
| 自動判定引擎（1/2/3A/3B 層） | Stage 2 判決金字塔 + Stage 2b ①深鑽 |
| 執行層（SCM2.0/Be2/PM/客服） | Stage 3 action_owner lookup |
| Feedback 修法回饋 | 第2層(框架未定義)→ PM 修法；金標集回歸校準 |

## 六、GPT 落地（模型線 + 原生準確度槓桿）

> ⚠️ 真實判決鏈用 **OpenAI GPT**（非 Claude）。模型名/價格為 2026-06 第三方追蹤站數據，正式以 OpenAI dashboard 為準；不變的是 tier 策略。

### 模型 tier → 階段
| 階段 | GPT 模型 | 參考價/1M | 備註 |
|---|---|---|---|
| Stage1 預判（高量分類） | GPT-4.1-nano / GPT-5.4-nano | $0.10-0.20 in | 配 Structured Outputs 足夠 |
| Stage2 判決（決策樹推理） | GPT-5.4（甜點）/ o3（推理） | $2.50 / $2 in | o3 reasoning token 計 output 價 3-10x |
| Stage2b ①深鑽（per-面向） | GPT-5.4-mini | $0.75 in | 每面向一裁判·可平行 |
| 高風險 jury | o3 / GPT-5.5 | — | 僅 ②contract_breach / conf 5-7 |
| Stage3 action | 無 LLM(lookup) + nano 產文案 | — | 去冗餘 |

### 三個 GPT 原生準確度槓桿
1. **Structured Outputs（strict JSON schema）**：GPT-5.2+ CFG grammar 遮蔽不合規 token，schema 100% 命中（舊版<40%）。domain/verdict/tag 用 enum。坑：`additionalProperties:false` 必設、**最多 5 層巢狀**（深結構要攤平）、`pattern`/`minLength` 不強制 → **只保證結構不保證內容**，hit_rule_id 存在性/suspected_field allowlist 仍要 code 驗。
2. **logprobs → 信心度**（見§三）：temp=0 + logprobs，取被選中 domain token 的 exp(logprob) 當 token_prob，比 verbalized 自報可信。G-Eval probability-weighted scoring 也原生可做。
3. **省錢槓桿**（判決鏈 T+1 批次全吃得到）：**Prompt caching −90%**（Rule Canon+29-tag taxonomy+few-shot 放固定 system prefix）｜**Batch API −50%**（離線批次）｜temp=0 單次取 logprob、中高風險再 sample N=2 算 agreement（sample-efficient）。

### 取數注意
- self-consistency 需 temp>0 sample，與「temp=0 取 logprob」衝突 → 解：**第 1 次 temp=0 取 token_prob（信心主信號）；第 2-3 次 temp=0.7 取 agreement**，兩者並用不互斥。
- o-series（o3/o4-mini）reasoning token 貴 → 判決階段預設 GPT-5.4，只有金字塔走到 ①深鑽爭議 / 高風險 jury 才升 o3。

## 出處
- LLM-as-Judge / G-Eval：montecarlo.ai, deepeval.com, Liu et al. EMNLP2023
- 信心度校準：Geng et al. 2024 survey；Two-Samples-Are-Enough (openreview 66D3rZrNjV)；DINCO (arxiv 2509.25532)
- RCA 決策樹/grounding：RCACopilot；LATS-RCA (arxiv 2605.03505)；Waterloo「Grounded or Guessing」
- 多階段驗證：LLM-as-a-Jury (arize.com)；Markov debate (arxiv 2406.03075)
- Ticket 分類 production：Claude ticket routing guide；XICTRON 2026（LLM 89-96% vs 人工 60-70%）
