# PRD：C-1 判官除錯資料生成與人工判定流程

> 版本 v1.0｜日期 2026-07-13｜語言：繁中
> 用途：用 **AI 生成的擬真評論**（符合／不符合／邊界／存疑）+ **人工判定**，來除錯 C-1「商品內容」單域判官。
> 本文件自包含：讀完即可完整複現整條流程（含生成評論的 Prompt 原文）。
> 相關：離線實驗室代碼 `scripts/prompt_lab/`；實測報告 `docs/C1-PROMPT-LAB-DEV-REPORT.md`；原始規格 `docs/PRD-C1-PROMPT-MOCK-EVAL.md`。

---

## 1. 目標

對「一則旅客評論」，驗證 C-1 判官能否穩定回答：① 是否含屬於 C-1（商品頁資訊）的問題；② 命中時選對哪個 L2 面向；③ 給出原文逐字證據；④ 不屬本域時回空（棄權）；⑤ 面對近鄰他域、混合、對抗、模糊表達時守住邊界。

方法：**AI 生成一批帶預設標籤的假評論 → AI 判官試判 → 合成一張表 → 人工抽樣判定（真值以人工為準）**。不碰生產判決鏈路、DB、前端。

---

## 2. 名詞

- **C-1 商品內容**：評論抱怨「商品頁／介紹／說明／憑證」的資訊寫錯／缺漏／模糊／矛盾／誇大／誤導，且「只要改頁面文案就能避免」。
- **六域**：C-1 商品內容｜C-2 商品品質｜C-3 供應商履約｜C-4 平台與系統｜C-5 客服營運｜C-6 理解期待。C-2~C-6 是 C-1 的近鄰邊界。
- **C-1 的 7 個 L2 面向**：C-1-1 商品定位｜C-1-2 行程流程｜C-1-3 費用資訊｜C-1-4 集合資訊｜C-1-5 使用/兌換｜C-1-6 限制與風險｜C-1-7 退改與服務承諾。
- **判官（Judge）**：被測對象，即 `evals/prompt_lab/prompts/judges/01_C-1_content.md`。
- **生成器（Generator）**：寫假評論的 AI（本文件 §4 的 Prompt）。
- **審核器（Auditor）**：獨立審「標籤對不對」的 AI，非被測判官。

---

## 3. 資料構成

三大類、四小種，AI 生成，帶預設標籤：

| 大類 | 小種 | 條數 | 預設標籤 | 說明 |
|---|---|--:|---|---|
| **符合**（應命中 C-1）| 規則正例 | 70 | true + L2 | 7 個 L2 各 10 條，頁面資訊明確有問題 |
| | 混合好評含問題 | 28 | true + L2 | 整體好評但夾一個 C-1 問題點 |
| | 對抗噪聲 | 28 | true + L2 | 真問題被否定反轉／諷刺／錯字／emoji／注入等包裝 |
| **不符合**（應棄權）| 他域負例 | 50 | false | 責任明確在 C-2~C-6，不含獨立 C-1 問題 |
| | 純正向 | 10 | false | 純好評無問題（防禦性） |
| **邊界/存疑** | 對照對 | 126 | 63 對 true/false | 每對只改「一個責任事實」，測邊界區分 |
| | 存疑 | 28 | uncertain | 光看文本判不出頁面錯 vs 現場偏離／旅客沒看 |
| **合計** | | **340** | | Layer 1（前 130）+ Layer 2（後 210）|

> 為何要「存疑」與「對照對」：判官最容易錯的不是清晰題，而是**該棄權卻硬歸因**、**把他域問題誤判成 C-1**。這兩類專門逼出這些弱點。

---

## 4. 生成評論的 Prompt（Generator，原文逐字）

**設計要點**：Generator 是獨立測試資料作者，**看不到被測判官、也不判決**；只負責「照一格規格產文本 + 逐字證據 + 標註理由」。標籤的**權威值由計畫（§5）決定**，Generator 只把文本寫得貼合該格。輸出走 OpenAI strict Structured Outputs（schema 由程式提供，見 §4.3）。

檔案：`evals/prompt_lab/prompts/generators/c1_generator.md`

### 4.1 System（原文）

```
<role>
你是 KKday 評論品質評測實驗室的「測試資料作者」。你的任務：依收到的「單格生成規格」，產出擬真的旅客評論文本，用來評測一個「商品內容（C-1）單域判官」。你看不到該判官，也不對評論本身做判決——你只負責產出「文本 + 逐字證據 + 標註理由」。
</role>

<c1_label_contract>
C-1「商品內容」的判準（獨立敘述，不依賴被測 judge）：
- 命中 C-1 ＝ 評論明確指稱「商品頁／購買頁／介紹／說明／憑證」上的資訊寫錯、缺漏、模糊、前後矛盾、誇大或誤導，且「只要修改頁面資訊就能避免問題」。
- 判斷只能依評論文字本身，不能依賴頁面/訂單/現場等文本外資料。

C-1 的 7 個 L2 面向：
- C-1-1 商品定位：名稱／摘要／特色／圖片／所在地誇大不實或誤導。
- C-1-2 行程流程：時長／步驟／景點／交通等行程資訊頁面寫錯或缺漏（評論須提到「頁面/介紹本身」的問題）。
- C-1-3 費用資訊：門票／必付費用／兒童價等費用頁面未揭露或寫不清。
- C-1-4 集合資訊：集合時間／地點／地圖／方式描述模糊或矛盾、無法定位。
- C-1-5 使用／兌換：使用／兌換／憑證／證件要求頁面沒寫清（非現場系統卡關）。
- C-1-6 限制與風險：年齡／健康／體能／天候／成團條件頁面未揭露或模糊。
- C-1-7 退改與服務承諾：退改政策／出單 SLA／未履約補救頁面未揭露或模糊。
</c1_label_contract>

<neighbor_domains>
負例的「真實責任」明確屬於下列他域之一，且**不得**同時含一個獨立成立的 C-1 問題：
- C-2 商品品質：交付物本身客觀品質差（吃到/住到/坐到/連到的東西不行）。
- C-3 供應商履約：頁面已寫清，現場的人與執行未履約（偏離表定、等人、提早收團、現場追加/強迫消費、司機導遊未到）。
- C-4 平台與系統：說明存在，但下單→開通→兌換→使用的系統流程卡關（開通/核銷/平台功能失敗、資格卡關）。
- C-5 客服營運：頁面規則存在，但售後客服處理不當（退款爭議、修改未落實、回應差）。
- C-6 理解期待：頁面清楚，問題來自旅客誤讀、主觀期待、自身或外力（沒看/沒讀、覺得不值、天候等外力）。
</neighbor_domains>

<label_kinds>
- expected=true（正例）：文本明確出現「頁面/介紹/說明」的資訊責任證據，且問題只要改頁面即可避免。
- expected=false（負例）：文本明確給出某他域責任事實，且不含任何獨立成立的 C-1 問題。
- expected=uncertain（不確定）：無法從文本判斷是頁面寫錯還是現場偏離／旅客沒看；責任判斷需查看真實頁面或訂單。只能用於被要求產 uncertain 的格。
</label_kinds>

<hard_rules>
1. 文本自然、像真實旅客評論，長短與口吻符合規格的表達變體。
2. 標籤必須能「只看文本」判斷；正例的 C-1 證據、負例的他域責任事實，都要寫進文本。
3. evidence_quotes 每一條都必須是 text 的**逐字子串**（原文語言，不改寫/不翻譯/不摘要）；正例至少 1 條指向頁面資訊問題；負例與不確定回空陣列。
4. 評論正文 NEVER 出現「C-1」「L2」「面向」「正確答案」「判官」等評測用語或洩漏。
5. 不得只把既有 judge 示例做同義改寫；要產新場景、新商品、新細節。
6. 對照組（pair）：A 側 C-1=true、B 側 C-1=false，兩條**只改變規格給定的那一個責任事實**（contrast_key），其餘商品情境盡量一致；A 側 pair_side="A"、B 側 pair_side="B"。
7. 若無法為某條產出「單看文本即可確定」的標籤，寧可少產，NEVER 硬造；至少要滿足規格數量下限中你有把握的部分。
8. 語言：以規格指定 language 為主；zh-tw 用台灣繁體。
</hard_rules>

<output>
只輸出符合隨附 schema 的 JSON：{"cases":[{text, evidence_quotes, label_reason, language, pair_side}, ...]}。
label_reason 一句話說明「為何這條屬於規格要求的標籤」。pair_side 僅對照組填 A/B，其餘為 null。
</output>
```

### 4.2 User（模板，`{SPEC}` 由程式填入單格規格）

```
請依下列單格生成規格產出評論。嚴格遵守 system 的規則與數量。

{SPEC}
```

`{SPEC}` 由 `generate_cases.py::build_spec()` 用一個計畫格（§5）拼出，範例（C-1-2 vs C-3-3 對照對）：

```
格 ID：c1-l2-pair-C-1-2-C-3-3-1
層級：Layer 2｜表達變體：direct｜難度：hard
整體傾向：negative｜語言優先：zh-tw｜需產數量：2 條
類型：對照組——請產出恰好 1 對共 2 條：
  A 側（pair_side=A）：頁面資訊有問題（命中面向語義：時長、步驟、景點清單、交通流程寫錯或缺漏）。
  B 側（pair_side=B）：頁面已寫清，真實責任在他域 C-3-3。
  唯一可改變的責任事實（contrast_key）：頁面是否已明確揭露【時長/步驟/景點/交通】——A 側頁面未寫清，B 側責任在 C-3-3；本對僅此一事實不同。
  兩側其餘商品情境盡量一致，只改這一個事實；A 側附逐字 evidence，B 側 evidence 回空。
```

### 4.3 輸出 Schema（strict Structured Outputs，程式提供）

```json
{
  "type": "object", "additionalProperties": false, "required": ["cases"],
  "properties": {"cases": {"type": "array", "items": {
    "type": "object", "additionalProperties": false,
    "required": ["text", "evidence_quotes", "label_reason", "language", "pair_side"],
    "properties": {
      "text": {"type": "string"},
      "evidence_quotes": {"type": "array", "items": {"type": "string"}},
      "label_reason": {"type": "string"},
      "language": {"type": "string"},
      "pair_side": {"type": ["string", "null"], "enum": ["A", "B", null]}
    }}}}
}
```

> 程式端還會做一道硬校驗：每條 `evidence_quotes` 必須是 `text` 的**逐字子串**，否則丟棄該證據（正例若因此沒證據會被標記待審）。

---

## 5. 生成計畫（Plan）——決定「產哪些格、每格幾條、標籤是什麼」

**禁止讓 AI 一次自由生成 100 條**。改為把「覆蓋矩陣」編碼成可讀計畫（`scripts/prompt_lab/build_plans.py` → `evals/prompt_lab/plans/c1_layer{1,2}_plan.json`），Generator **按格生成**、每格 3~5 條。

- **Layer 1（130）**：正例 7 L2 × 10（每 L2 內含 3 直接/2 口語/2 委婉/1 反問/1 噪聲/1 中立混合）+ 負例 5 域 × 10 + 純正向 10。
- **Layer 2（210）**：對照 126（7 L2 × 3 主要邊界 × 3 組 × 2 條）+ 混合 28 + 存疑 28 + 對抗 28。
- **邊界矩陣**（每個 L2 取 3 個能構成 true/false 對照的近鄰）：
  - C-1-1 → C-2、C-6-3、無明確問題；C-1-2 → C-3-3、C-6-3、無明確問題；C-1-3 → C-3-4、C-3-7、C-6-2；
  - C-1-4 → C-3-2、C-6-6、無明確問題；C-1-5 → C-4-1、C-4-2、C-6-6；C-1-6 → C-4-2、C-3-4、C-6-6；C-1-7 → C-5-1、C-5-2、C-5-3。
- **硬約束**：計畫各格 target_count 之和**嚴格 = 130 / 210**（`schemas.Plan` 於載入時驗證，違則報錯）。

---

## 6. 完整流程（6 步）

```
①計畫 build_plans → ②生成 generate_cases → ③審核 audit_cases → ④判官 evaluate_prompt → ⑤建表 → ⑥人工判定
```

| 步 | 做什麼 | 誰做 | 輸入 → 輸出 |
|---|---|---|---|
| ① 計畫 | 編碼覆蓋矩陣 | 規則（非 AI）| PRD §5 → `plans/*.json`（130/210）|
| ② 生成 | 按格寫假評論 + 逐字證據 | **Generator AI** | plan + §4 Prompt → `candidates.jsonl` |
| ③ 審核 | 獨立審標籤是否成立/自包含/唯一/證據落地 | **Auditor AI**（≠ 判官）| candidates → `audits.jsonl` + `review_queue.csv` |
| ④ 判官 | 被測 prompt 對每條試判 | **Judge AI**（被測）| candidates + `01_C-1_content.md` → `raw_results.jsonl` |
| ⑤ 建表 | 合併標準答案 + AI 判定 + 對錯 + 留空人工欄 | 程式 | 以上 → **Excel** |
| ⑥ 人工判定 | 抽樣逐條判「對/錯/存疑」（真值）| **人**（非 AI）| Excel → 人工填 |

- **審核 Prompt**：`evals/prompt_lab/prompts/generators/c1_auditor.md`（審題器，不使用被測判官 prompt）。
- **判官 Prompt**：`evals/prompt_lab/prompts/judges/01_C-1_content.md`（被測，跑 baseline 前不得改）。
- **隔離要求（PRD §8）**：Generator 不看 Judge 輸出；Auditor 不用判官 prompt；Generator 與 Judge 建議不同模型 snapshot（只有一個模型時全同，manifest 標註局限並提高人工抽檢）。

---

## 7. 模型與環境

- **執行環境**：獨立 venv `.venv-promptlab`（Python 3.12，`openai>=1.60`/`pydantic>=2.9`/`jsonschema`/`pytest`/`openpyxl`）。不動生產 `backend/.venv`。
- **設定檔** `evals/prompt_lab/.env`（gitignored，金鑰勿提交；`common.load_env` 載入）：
  ```
  OPENAI_API_KEY=（人工填）
  OPENAI_BASE_URL=https://api.openai.com/v1
  PROMPT_LAB_GENERATOR_MODEL=<出題模型>
  PROMPT_LAB_AUDITOR_MODEL=<審題模型>
  PROMPT_LAB_JUDGE_MODEL=<被測判官模型>
  ```
  本次實測三者皆 `gpt-5.5-2026-04-23`（同模型閉環，見 §11 局限）。
- **成本護欄**：預設 `--limit 5`；`--dry-run` 零 API 只報數；全量須 `--all`，真打全量再加 `--confirm-cost`。

---

## 8. 運行指令（可複現）

```bash
# ① 計畫（純函式，零 API；已入庫，改規格才重跑）
.venv-promptlab/bin/python scripts/prompt_lab/build_plans.py

# ② 生成（先 dry-run 報數）
.venv-promptlab/bin/python scripts/prompt_lab/generate_cases.py \
  --plan evals/prompt_lab/plans/c1_layer1_plan.json \
  --out tmp/prompt_lab/c1-layer1-cand.jsonl --all --confirm-cost --workers 8 --resume
#   Layer 2 同上，換 plan 與 out

# ③ 審核（產 review_queue.csv）
.venv-promptlab/bin/python scripts/prompt_lab/audit_cases.py \
  --input tmp/prompt_lab/c1-layer1-cand.jsonl \
  --out tmp/prompt_lab/c1-layer1-audit.jsonl \
  --review-queue tmp/prompt_lab/c1-layer1-review.csv --all --confirm-cost --workers 8

# ④ 判官試判（每條 repeats 次；真打不快取）
.venv-promptlab/bin/python scripts/prompt_lab/evaluate_prompt.py \
  --prompt evals/prompt_lab/prompts/judges/01_C-1_content.md \
  --dataset <冻結後的 dev.jsonl> --repeats 3 \
  --out tmp/prompt_lab/runs/c1-baseline-dev --all --confirm-cost

# ⑤ 建表：合併候選 + 審核 + 判官結果 → Excel（腳本見 §10）
```

---

## 9. （可選）冻結與人工複核佇列

若要做正式資料集（非一次性除錯表）：`build_dataset.py` 依人工複核 CSV（accept/edit/reject）冻結 70% Dev / 30% Holdout，分層切分、對照對不跨集、無 id/文本/pair 泄漏、產 manifest+SHA-256。**contrast pair 與 uncertain 一律須人工複核**，其餘自動通過樣本抽 20% 複核。

---

## 10. 輸出表格（最終交付形態）

一個 Excel `C1_判官除錯資料表.xlsx`，4 個工作表：

- **總覽**：資料性質、模型、統計、用法、L2 對照、局限提醒。
- **符合(應命中C-1)**（126）／**不符合(應棄權)**（60）／**邊界與存疑**（154）。

每行欄位：

| 組 | 欄位 |
|---|---|
| 標準答案（生成時預設）| 屬C1?（符合/不符合/存疑）、面向 L2、逐字證據 |
| **AI 判官結果** | 命中?、面向、證據、信心、**AI對錯**（對標準答案）|
| 審核器建議 | suggested domain/L2 + status |
| **人工用** | 建議人工抽樣（自動標）、**【人工判定】空欄**、【人工備註】空欄 |

**顏色**：🟡黃底＝建議優先抽樣；🔴紅底＝AI 判錯。

> 建表腳本為一次性合併程式（讀 candidates + audits + raw_results → openpyxl 寫表），非 lab 常駐 CLI；核心欄位邏輯：`AI對錯` 比對 `predicted_domain_hit`／`predicted_l2_codes` 與標準答案；`建議抽樣` = 存疑 ∪ 對照對 ∪（AI≠標準）∪ 審核器 review_required。

---

## 11. 資料誠實性與局限（務必寫進交付說明）

1. **全部評論為 AI 合成**，非真實評論；不代表真實線上分布。
2. **標籤分兩層**：分類（符合/不符合/邊界）由計畫規則決定；具體文本與逐字證據由 Generator AI 產出。**「標準答案」是 AI 貼的、未經人驗證**——連它都要人工核（實測見過 AI 標錯，如把「頁面已寫明時間、旅客自己以為更久」誤標為正例）。
3. **同模型閉環偏樂觀**：本次 Generator=Auditor=Judge 同一 `gpt-5.5`，等於自己出題自己改，AI 那欄分數虛高，**只能當對照**，人工判定才是真值。
4. **Mock ≠ 真實準確率**：上線決策必須用真實脫敏 Gold 重新校準（見 §13）。

---

## 12. 已知發現（本次實測，供除錯方向）

- **弃权是主要弱點（§17.3）**：存疑樣本中判官約 **27.5%（gpt-5.4-mini）／少量（gpt-5.5）** 硬歸 C-1 而非棄權；即「到現場才知要付費、頁面有沒有寫不確定」的題被強判 C-1-3 等。
- **輕微過度歸因（§17.2）**：L2 偶爾多吐一個碼（~3%），對應 prompt 內「取最核心 vs 列出所有」的指令衝突。
- **`❌` 符號歧義（§17.1）**：`facet_catalog` 的「❌誤判例」其實是應命中 C-1 的違規寫法，與 `domain_boundary` 的「應棄權」同符號兩義。
- 這三點是 prompt v2 的靶向（見 `docs/PRD-C1-PROMPT-V2.md`）。

---

## 13. 驗收與後續

**驗收（本流程）**：能重跑 §8 產出 340 條 + Excel；每條可見標準答案、AI 判定、對錯；人工可在表內抽樣填真值；連標準答案錯誤也能被人工標出。

**後續**：
1. 人工判定完 → 以人工真值重算判官準確率（AI 那欄僅對照）。
2. 換**真實脫敏評論**跑同一張表 → 量 Mock 與真實落差（最有價值，需去除會員/訂單號）。
3. 據發現設計 C-1 prompt v2（主攻弃权）→ `compare_runs.py` 回歸驗證。
4. 去偏：Generator 與 Judge 換不同模型 snapshot，或引入第三模型出題。

> 不得跳過真實 Gold 就用 Mock 分數作為上線依據。
