# C-1 判官 Prompt v2 變更說明

> 對應任務：[`PRD-C1-PROMPT-V2.md`](./PRD-C1-PROMPT-V2.md)｜baseline 實測：[`C1-PROMPT-LAB-DEV-REPORT.md`](./C1-PROMPT-LAB-DEV-REPORT.md) §2.5
> 候選檔（定稿）：`evals/prompt_lab/prompts/judges/01_C-1_content_v2.md`（sha256 `42fad7a33747…`，18268B）
> 基線（凍結·未改）：`01_C-1_content.md`（sha256 `ef5f66a9…`，13724B）
> 狀態：**設計 + 零 API 驗證 + Path B preliminary 三輪迭代完成**（2026-07-13）。⚠️ preliminary＝同模型閉環 + 未人審資料，**非 merge 依據**（見 §5）。
> 迭代：`4b62f1aa`(初版三修) → `d1471abe`(v2b：補「自陳存疑/並列他因」棄權判據) → `42fad7a3`(v2c：泛化去 §2.6 過擬合，定稿)。

## 1. 目標（PRD §1）

在**不損害**既有強項（域命中、邊界區分、證據落地）前提下，主攻 **§17.3 棄權失敗**：讓判官面對「光看文本無法判定是頁面寫錯 / 現場偏離 / 旅客沒看」的輸入時**正確回空**，而非硬歸 C-1。順帶修 §17.2、§17.1 兩個契約瑕疵。

baseline 露餡點（同模型閉環 Mock，偏樂觀）：
- **§17.3**：uncertain n=40，**被迫歸因 27.5%**（硬塞 `C-1-3×5 / C-1-1×2 / C-1-4×2 / C-1-6×2`）。
- **§17.2**：L2 過度歸因 **2.98%**。
- **§17.1**：`❌` 一符兩義，未在指標上直接量到，屬契約清晰度風險。

## 2. 三處修改（改文案，未動 schema）

### 修改 A — 棄權契約（§17.3，主修）
- **改哪**：重寫 `<abstain_rules>`；並在 `<decision_process>` 插入「責任站點門檻」為 step 3；`<critical_rules>`、`<limitations>`、`<judgment_rules>` 同步呼應。
- **前**：棄權規則僅「不屬本域／找不到目錄面向 → 回空」，未處理「文本不足以定責」。
- **後**：確立 **C-1 命中的必要條件＝評論文字本身明確指認『頁面/說明/描述』的寫法問題**（沒寫/寫錯/模糊/與實際不符）。只講「現場遭遇」或「體驗結果」而未言明頁面怎麼寫 → 文本不足以區分 C-1／C-3／C-6 → **棄權**。附「評論沒提到頁面 ≠ 頁面確實沒寫」的反推禁令，及 3 組「棄權 vs 命中」對照例（費用/集合/語系）。
- **後（v2b 補強）**：加入「**旅客自陳存疑或並列他因**」判據——評論若以旅客自己的話保留了非頁面成因（「也可能是我沒看仔細」「我以為…」「也許是現場/當天不同」「跟我想的不一樣」），即使有看似指認頁面的字眼，也屬責任站點未定 → 棄權。判準是**語氣**而非比對特定字眼（v2c 泛化定稿，見 §5.3）。
- **為何**：baseline 硬塞集中在費用(C-1-3)、集合(C-1-4)、定位(C-1-1)、限制(C-1-6)——都是「症狀描述但沒指認頁面」的典型。把「需指認頁面」設為硬門檻，直接切斷硬歸路徑；殘留的 3 條硬歸則都帶旅客自我存疑語氣，由 v2b 判據補收。

### 修改 B — 多問題計數契約（§17.2）
- **改哪**：`<attribution_principles>` 選碼紀律。
- **前**：此處寫「多個問題時**取最核心**、勿被次要問題誤導」，但 `<judgment_rules>` 又寫「**列出所有**明確涉及本域的問題面向，最多 2 條」——一取一列，自相矛盾。
- **後**：統一為「列出所有**明確成立**的本域問題點（最多 2 條）；次要、模糊、需推測才成立者不列」。保留「勿被枝節誤導/勿湊數」的精確性意圖，移除「取最核心＝只取一條」的數量衝突，與 `<judgment_rules>` 對齊。
- **為何**：衝突指令讓模型時而列多條（過度歸因）。統一契約 + 強調「明確成立」以壓過度歸因。

### 修改 C — `❌` 語義去歧義（§17.1）
- **改哪**：`<domain_boundary>` 與 `<facet_catalog>` 的標記符號。
- **前**：`<domain_boundary>` 的 `❌`＝「常見誤判、應棄權/轉他域」；`<facet_catalog>` 的 `❌誤判例`（如「未標示或模糊描述」）其實是**應命中** C-1 的違規寫法——同一 `❌` 兩義。
- **後**：
  - `<domain_boundary>`：`❌` → `↪️`（常見誤判，應棄權轉他域）。
  - `<facet_catalog>`：`✅例` → `✅合規寫法`（頁面這樣寫沒問題）；`❌誤判例` → `🚩違規寫法`（頁面若這樣寫/漏寫＝本面向 C-1 問題，應命中）。並在目錄開頭加標記說明，明講與 `<domain_boundary>` 語義不同。
- **為何**：消除「命中訊號」與「棄權訊號」共用同符號的誤導。

## 3. 刻意未改（守住硬約束 PRD §2）

- **`## Schema` 與 `## User` 段與 baseline 逐字元相同**（已用 parser 驗證 `schema == baseline.schema`、`user_template == baseline.user_template`）：`l2_code` enum 仍 `C-1-1..C-1-7`、`maxItems` 仍 2、占位符 `{POLARITY}`/`{TEXT}` 齊備 → runner 不破。
- 未改基線 `01_C-1_content.md`、未改 `scripts/prompt_lab/` 引擎邏輯、未碰 `backend/app/` 生產碼。
- v2 為**新檔**；僅 `build_manifest.py` 追加 v2 到清單、`test_schemas.py` 的 judge 計數守衛改為「7 域 baseline 恆在、候選可另加」的子集斷言。

## 4. 零 API 驗證（已完成）

| 項目 | 結果 |
|---|---|
| v2 可被 `prompt_parser` 解析 | ✅ version=`01_C-1_content_v2`、定稿 sha256=`42fad7a33747…` |
| Schema 合法且與 baseline 相同 | ✅ `schema == baseline` True；enum/maxItems 不變（三輪迭代皆維持）|
| User 段與 baseline 相同 | ✅ True；`{POLARITY}`/`{TEXT}` 在 |
| System 變更範圍 | attribution_principles / critical_rules / domain_boundary / facet_catalog / decision_process / judgment_rules / abstain_rules / limitations（皆對應 §17 三風險，無多餘改動）|
| `pytest backend/tests/prompt_lab` | ✅ **51 passed**（三輪迭代後仍全綠）|
| `ruff check`（改動檔） | ✅ All checks passed |
| dry-run 計數（零 API）| Layer 1 dev 267 runs、Layer 2 explore dev 292 runs |

## 5. Path B preliminary 對比結果（2026-07-13，已完成三輪）

> **資料誠實性（PRD §4）**：現有 `ds-v1`/`ds-l2` 為 accept-all 探索版（未經人工複核、未入 Git），且 Generator=Auditor=Judge=`gpt-5.5` 同模型閉環。
> ⇒ 以下所有數字僅為 **preliminary（方向性）**，**不得**作為 merge 依據；merge 前須走「人審凍結 c1-v1（路徑 A）」或「第三模型出題 / 真實脫敏 Gold（§4 去偏）」重驗。

跑法：v2 各版對 baseline 在 `ds-v1`(L1,×3=267 runs) 與 `ds-l2`(L2,×2=292 runs) 同資料集對比，三輪皆 0 失敗。
三輪 API 花費合計約 **9.8M input / 0.3M output tokens**（單輪 ~3.3M in）。對比目錄：`tmp/prompt_lab/comparisons/c1-l2-baseline-vs-v2c` 等。

### 5.1 定稿 v2c 對 baseline（晉級門檻 PRD §6）

| 指標 | baseline | v2 目標 | **v2c 實測** | 判定 |
|---|--:|--:|--:|:--:|
| **uncertain 被迫歸因率（L2）** | 0.275 | <0.275，理想 ≤0.15 | **0.000** | ✅ 全數正確棄權 |
| uncertain 棄權率（L2）| 0.725 | 上升 | **1.000**（40/40）| ✅ |
| 域 Recall（L1 / L2）| 1.0 / 1.0 | 不回退 >1pp | 1.0 / 1.0 | ✅ 無回退 |
| 域 Specificity / 邊界 FPR（L2）| 1.0 / 0.0 | 無新增 FP | 1.0 / 0.0 | ✅ |
| 對照對 BothCorrect（L2）| 1.0 | 維持 | 1.0 | ✅ |
| L1 純正向防禦誤命中 | 0.0（n=21）| 維持 | 0.0（n=21）| ✅ |
| 證據 grounding(quote) | 1.0 | 維持 | 1.0 | ✅ |
| 域穩定性 flip（L2）| 1 | 不增 | **0** | ✅ 反而更穩 |
| L2 過度歸因 | 0.0298 | 下降 | 0.0476 | ➖ 噪音級（見 5.3）|
| domain fixed / regressed | — | fixed ≫ regressed | L1 0/0；**L2 6/0/0** | ✅ |

- **主目標（§17.3）超額達成**：uncertain 被迫歸因 **27.5% → 0%**（40 條全數正確棄權，`forced_L2={}`）。L2 compare：baseline→v2c **6 fixed / 0 regressed / 0 unchanged_wrong**——baseline 全部 6 條硬歸 uncertain 現皆正確棄權。
- **過度矯正風險未發生**：Layer 1 fixed/regressed 皆 0、域 Recall 維持 1.0、對照對 1.0——強化棄權門檻**沒有**誤殺任何「明確指認頁面」的真陽性。

### 5.2 三輪迭代（棄權率逐步收斂）

| 版本 | sha | 關鍵改動 | uncertain 被迫歸因 | vs baseline domain(L2) |
|---|---|---|--:|---|
| baseline | `ef5f66a9` | — | 0.275 | — |
| v2 初版 | `4b62f1aa` | §17.1/17.2/17.3 三修 | 0.125 | 3 fixed / 3 未修 |
| v2b | `d1471abe` | +「自陳存疑/並列他因」棄權判據 | **0.000** | 6 fixed / 0 未修 |
| **v2c（定稿）** | `42fad7a3` | 泛化例子去 §2.6 過擬合 | **0.000** | 6 fixed / 0 未修 |

初版殘留的 3 條硬歸（`unc-C-1-1-02`/`unc-C-1-3-03`/`unc-C-1-4-04`）共通點：評論帶旅客**自我存疑語氣**（「也可能是我沒看仔細」「以為…」「也許司機停不同」），卻同時有類似指認頁面的字眼。v2b 針對此加判據後全數改為正確棄權。

### 5.3 兩個已查證、**不需**改 prompt 的觀察

1. **L2 過度歸因是噪音，非回退**：over 率在四次 run 間為 `0.0298 / 0.0417 / 0.0298 / 0.0476`（baseline 亦 0.0298），擺動源於 repeats=2 下多面向案例「第二條面向」在 0/1/2 次 repeat 出現的隨機性（~5–8 instance／168）。逐案查證：**全部**域正確、主 L2 正確，只是多列一條**可辯護**的次要面向（如 `mixed-C-1-6-04` 的 C-1-7 改期通知，比 gold 單標籤更精確）。→ 屬 **gold 單標籤漏標** 假象，非 v2 缺陷。**修正在資料側**（多面向案例改多標籤，需人審），**不改 prompt**——為迁就漏標 gold 去壓這數字即 PRD §2.6 過擬合，禁止。
2. **§2.6 泛化已證**：v2b 的棄權例子曾近乎照抄失敗案例字眼（「地圖帶到正門」「希望費用更明確」）。v2c 改成通用類別描述並明示「判語氣非比對字眼」後重跑，`v2b→v2c` domain compare = **0/0/0**（行為不變）、`baseline→v2c` 仍 6 fixed/0 regressed。→ 證明棄權修復是**通用規則**在起作用，非 pattern-match 特定 Mock 文本。

### 5.4 去偏警語（務必內化）

被迫歸因 0% **被閉環高估**：這批 uncertain 是 `gpt-5.5` 生成，其「自我存疑」語氣正是 v2 判官現在賴以棄權的訊號；真實評論的模糊不會這麼工整標記。**真實世界的棄權改善幅度必小於 27.5%→0%**。定論須待第三模型出題或真實脫敏 Gold（PRD §4/§7/§23）。

## 6. 一句話

v2（定稿 `42fad7a3`）把「棄權」從一句原則升級為可操作門檻（**沒指認頁面、或旅客自己都存疑 → 不歸 C-1**），統一多問題計數契約，拆掉 `❌` 一符兩義；Mock 上被迫歸因 27.5%→0% 且零回退，並已證非過擬合——但這是**閉環高估的 preliminary**，**任何 merge 都要等去偏/真實 Gold 複驗**。over 率的擺動是噪音＋gold 漏標，修在資料側不在 prompt。
