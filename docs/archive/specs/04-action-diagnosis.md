# SD ④ 如何產出可執行 action 診斷（L2–L4，核心）

> 對應 Aaron 四問之④。兩階段 + 雙意見交叉 + 純程式仲裁。落點 `backend/app/judge/`。
> 沿用 ProductContentAIChecker 的 prompt/規則資產（Python 直接複用）。交付 6/25–6/30（需 OpenAI key）。

## 資料流
```
NormalizedTicket
  → L2 classify(comment)            [LLM #1，只看客訴]
  → fetch_product + extract_fields  [取 suspected_field 原文]
  → L3 adequacy(field, dimension)   [LLM #2，只看商品原文、不採信抱怨]
  → L3 arbiter(classify, adequacy)  [純程式]
  → L4 diagnose(verdict)            [純程式]
  → TicketFinding
```

## L2 classify（`judge/classify.py`，1 次 LLM）
- system：8 dimension 定義（注入 rules.json）+ 邏輯欄位清單 + verdict 五分類。
- 任務：讀 `comment` → 抽 1..N 問題，逐一輸出 `dimension / problem_summary / suspected_field / tentative_verdict / confidence / is_primary`。
- 鐵則：非內容（服務/出貨）→ `dimension=non_content, tentative_verdict=escalate_ops`；只看客訴不看商品原文（保雙意見獨立）。
- 輸出：`response_format=json_schema strict`（Pydantic）。

## L3 adequacy（`judge/adequacy.py`，1 次 LLM）
- 何時：`dimension != non_content` 且 `suspected_field != none` 且取到原文。
- system：商品稽核員，**只看欄位原文、不採信客訴歸咎**。
- 輸入：`suspected_field` 原文 + `dimension` + `concern`(=problem_summary) + `cs_conversation`(若有)。
- 輸出：`status ∈ {adequate, unclear, missing, contradictory, field_empty}` + evidence。
- 客服訊號：若客服需搬政策原文才解釋 → 強烈傾向 unclear/missing。

## L3 arbiter（`judge/arbiter.py`，純程式仲裁表）
| classify.tentative | adequacy.status | → verdict | confidence |
|---|---|---|---|
| content_* | missing / field_empty | content_missing | 0.9 |
| content_* | contradictory | real_config_issue | 0.9 |
| content_* | unclear | content_unclear | 0.85 |
| content_* | adequate | customer_misread ⬇︎ | 0.8 |
| customer_misread | unclear/missing/contradictory | 採 adequacy 對映 content_* | 0.6 |
| escalate_ops（non_content）| —（跳過 L3）| escalate_ops | =classify.confidence |
> 內容證據凌駕客訴語氣。

## L4 diagnose（`judge/diagnose.py`，純程式）
| verdict | recommended_action | writer_handoff |
|---|---|---|
| real_config_issue | fix_contradiction | 條件式 |
| content_missing | add_missing_info | **False（強制·防幻覺）**|
| content_unclear | clarify_wording | 條件式 |
| customer_misread | no_action | False |
| escalate_ops | escalate_ops | False |
- 防幻覺鐵則：`content_missing` 缺事實，writer 不可生成；`action_detail` 優先擷取 `cs_conversation` 政策原文當 ground truth。
- writer_handoff 條件：verdict ∈ {content_unclear, real_config_issue} 且 suspected_field ∈ writer 3 欄。

## 信心度路由 / 驗收
- MVP：預設全進 `status=new` 待人工，蒐集 golden 後再調門檻（高信心誤判代價最高，保守）。
- 驗收（Promptfoo + golden）：verdict 準確率、suspected_field 命中率、**customer_misread 降級精確率**；**先鎖「集合/費用」2 dimension** 打穿門檻再開放 8。
- golden：`backend/fixtures/product_150665.json`（已標 expectedVerdict）。

## 交付
6/25–6/30（4d）。依賴：⚠️ OpenAI key 6/25（之前用 stub 跑通流程）。
