# vendored_review — ai_review_system 沿用資產清單

從 `ai_review_system`（審品系統 review_v1，基線 commit `7ba76f3` · 2026-06-23）重整進 AI 法官的資產。
與 `../vendored/`（ProductContentAIChecker 來源）並列、來源分離。搬入 2026-06-24。

## 處置原則（用戶決策）
- **類目路由＝接線**（內容品質敏感判決，屬法官本分）
- **合規 prompt＝僅參考、不接判決鏈**（導外/酒類/醫美/餐券屬審品事前職責；法官守事後內容裁決本分）
- **多模態圖片 prompt＝不適用**（法官目前無視覺管道）

## 一、類目路由（`category_prompts/` + `prodtag_router.py` + `rule4_prodtag_defs.json`）— 接線

| 資產 | 來源 | 內容 | 沿用 |
|---|---|---|---|
| `category_prompts/CATEGORY_*.md`（33 檔）| `prompts/rule4_leaves/*.md` | T3 類目葉子 prompt（半一日遊/美食之旅/跳傘/Spa…），各含 T1→T2→T3 定義 + 精準優先紀律 | **接線**：adequacy 判準的類目敏感疊加層 |
| `rule4_prodtag_defs.json` | `data/rule4_prodtag_defs.json` | 98 碼 T1(9)/T2(37)/T3(52) 分類樹（tier/parent）| 路由用基礎資料 |
| `prodtag_router.py` | 重整自 `backend/review_prodtag.py` | `routing_codes`/`routed_prompt_blocks`/`has_prompt`（T2→T3 展開、退回 main、stdlib-only）| **接線**：`codex.adequacy_criteria(prod_tag)` 路由（Phase 2 待接） |

## 二、合規 prompt（`compliance_prompts/`）— 僅參考·不接判決鏈

| 檔 | 來源 | 規則 | 多模態 | 處置 |
|---|---|---|---|---|
| `Rule3_P0_oid056_*.md` | 同名 | 導外訊息偵測（25 觸發詞）| n | 參考 |
| `Rule5_P0_oid072_*.md` | 同名 | 語言相符（主體 vs 宣告語言）| n | 參考 |
| `Rule6_P0_oid052_*.md` | 同名 | 酒類禁賣（R1 法規）| n | 參考 |
| `Rule7_P0_oid080_*.md` | 同名 | 侵入性醫美/療效宣稱（53 詞）| n | 參考 |
| `Rule8_P0_oid082_*.md` | 同名 | 旅行社禁售純餐廳商品 | n | 參考 |
| `Rule1_P0_oid093_*.md` | 同名 | 圖片禁用 KKday logo | **y** | 不適用（無視覺）|
| `Rule2_P0_oid055_*.md` | 同名 | 圖片含導外訊息 | **y** | 不適用（無視覺）|

> **鐵則**：`compliance_prompts/` **不得**被 `pipeline` / `arbiter` / `codex` 任何 runtime 路徑 import。
> 驗證：`grep -r compliance_prompts backend/app`（除本目錄）應為 0。合規維度若日後納入法官，另案評估。

## 三、未沿用（引擎設計參考）
`review_rule_gates.py`(GateContext) → 採其設計新建 `app/judge/gates.py`（Phase 2c）；`review_field_checker.py`/`review_rules.py`/`review_models.py` 的組裝/欄位 SSOT 設計為參考，不逐字搬。

## 接線狀態
- ✅ Phase 1：資產 vendored + `prodtag_router` 重整 + 單元驗證（CATEGORY_020→半一日遊）
- ⏳ Phase 2：`codex.adequacy_criteria(prod_tag)` 路由 + pipeline 透傳 prod_tag + `gates.py`（待接）
- ⏳ prod_tag 進商品資料（schema/fixture；BQ 抽取後續）

## 跟進
上游基線 `7ba76f3`；新提交檢查見 `docs/UPSTREAM-REFS.md` / `scripts/check_upstream.sh`。
