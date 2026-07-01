# 上游參考 repo 追蹤（Upstream References）

AI 法官（本專案 `kkday-ai-quality`）為**獨立項目**，判決鏈 L1-L5 自建，但
**唯一沿用**以下審品/撰寫專案已驗證的 prompt / 規則作為判斷依據。本檔記錄上游 repo 角色、
**追蹤基線提交**與沿用狀態，供持續跟進上游最新提交、同步法典/prompt 變更。

> 跟進指令（看某 repo 自基線後的新提交）：
> `git -C /Users/alvin/Kkday/projects/<repo> log --oneline <baseline_hash>..HEAD`

## 三個 repo 角色

| repo | 角色 | 與 AI 法官關係 |
|---|---|---|
| `ProductContentAIChecker` | 審品(tour_flow_v1 G1/G3) + 撰寫(writer) + 過期(general_v1 GEN-1)。最早的規則-as-prompt 工具。 | **已沿用**：深度 prompt + machine_checks + rules.json → 本專案 `backend/app/judge/vendored/`（見該目錄 MANIFEST.md） |
| `ai_review_system` | **審品系統 review_v1（最新）**：逐欄位審品 12 欄 Pass/Failed，Rule1~Rule8（P0 平台禁止，含多模態圖片）+ Rule4 類目匹配（40 類目 per-category prompt）+ 乾淨 per-field 規則引擎。零相依 ProductContentAIChecker。 | **待評估沿用**：Rule1-8 / rule4_leaves 類目 prompt 可作欄位級/類目級判決參考；尚未 vendored |
| `kkday-ai-quality` | **AI 法官**（本專案）：事後內容爭議裁決，L1-L5 判決鏈、法典配置、dashboard。 | 主體 |

## 追蹤基線（baseline，截至 2026-06-24 盤點）

| repo | baseline commit | 日期 | 提交標題 |
|---|---|---|---|
| `ProductContentAIChecker` | `99a62c6` | 2026-06-18 12:36 | fix(checker): 杜絕「未違反」結論被計為違規——parser 改信判詞肯定斷言 |
| `ai_review_system` | `7ba76f3` | 2026-06-23 22:00 | docs: expand README with architecture, data flow and Rule4 section |

## 已沿用 vs 待跟進

### ProductContentAIChecker（已 vendored，見 `vendored/MANIFEST.md`）
- ✅ 深度 judge prompt：行程流程 G1/G3(560) / 過期 GEN-1(645) / 商品名稱(113)
- ✅ machine_checks（禁詞/長度/促銷/結構）+ rules.json（29 禁詞/10 情緒詞/8 維度）
- ⏳ 跟進：基線後若有 prompt/規則修正（如 parser、禁詞、封頂邏輯），需重新 vendor 對應檔並更新 MANIFEST

### ai_review_system（尚未沿用，候選）
- ⏳ **Rule1~Rule8**（`prompts/Rule*_P0_*.md`）：P0 平台禁止規則（部分多模態圖片，如禁 KKday logo）——文字類可轉為法官判準；圖片類視法官是否擴多模態
- ⏳ **Rule4 類目 prompt**（`prompts/rule4_leaves/CATEGORY_*.md`，40 類目）：per prod_tag 類目判準，AI 法官可做「類目敏感」判決
- ⏳ **per-field 規則引擎**（`backend/review_field_*.py`）：逐欄位 rule-gate 設計，可參考其 field→rule 映射與 rule_gates
- ⏳ 受審 12 欄位與本專案 LOGICAL_FIELDS 對齊度需比對（prod_name/gallery/gallery_video/prod_summary/prod_feature/prod_desc/prod_schedules/prod_notice/pkg_name/pkg_desc/pkg_schedules/pkg_gallery）

## 跟進流程（建議）

1. 定期（或啟動相關工作前）跑跟進指令，看上游基線後新提交。
2. 若新提交動到 prompt / 規則 / 機檢邏輯 → 評估是否影響本專案 vendored 或法典配置。
3. 需同步者：重新 vendor 對應檔（ProductContentAIChecker）或新 vendor（ai_review_system），更新 `vendored/MANIFEST.md` 與本檔基線 hash。
4. 法典 Google Sheets 變更另走 `data/parse_judge_logic.py` / `parse_codex.py` ETL（與 repo 提交無關）。
