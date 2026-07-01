# 交接：config-ization 收尾 + 展開行完整度（給並行 session）

> 兩個 Claude session 同時在此 repo 作業。本檔由「歸因列表分表」session 留給正在做 config 重構的 session。
> 完成後請刪除本檔。所有路徑已對齊你重構後的新佈局（judgment.json 已移至 `config/ai_judge/`）。

## ‼️ 使用者定案（2 session 協調 · 最高優先）
**product_vertical（商品垂直分類）＝可編輯版本化規則**，不是唯讀 config。請**停止**把它改回唯讀 / 從 RULE_CODES 移除 / 刪 RuleManager 面板。
本 session 已完成後端（下述），並將擁有前端收尾——**另一 session 請勿再碰** `db.py` RULE_CODES、`rules.py` product_vertical、`RuleManager.vue`、product_vertical 面板、`config/ai_judge/rule_product_vertical.json`。

已由本 session 完成（後端，pytest 20 綠 / ruff clean）：
- `db.py` `RULE_CODES` += `"product_vertical"`；`reset_all_rule_defaults` 排除 `product_vertical`。
- 新 `backend/app/core/product_vertical.py`（讀 `db.get_rule_active('product_vertical')`）；**已刪** `category_groups.py`。
- `rules.py`：`_validate` 加 `product_vertical` 分支（驗 `{groups:{}}`，不套歸因 schema）；端點 `/product-vertical/resolved`。
- `main.py`：`get_problems` / `ExportProblemsIn` 參數 `category_groups`→`product_verticals`；`db.list_problems`/`export_problems_csv` 參數 `category_group`→`product_vertical`。
- seed `config/ai_judge/rule_product_vertical.json`（Tour/Exp/Charter/Tix）；**已刪** 孤兒 `config/global/product_vertical.json`。
- 測試：`test_product_vertical.py`（取代 test_category_groups.py）、`test_list_problems_filters.py` 更新。

前端已由本 session 完成（vue-tsc / eslint 綠）：新 `ProductVerticalPanel.vue`（groups 表單）+ RuleManager 獨立「商品垂直分類」選單項（面板編輯/歷史/恢復默認，不套歸因 schema）；`judgment.api.ts` 端點 `/judge-rules/product-vertical/resolved`、參數 `product_verticals`；`source-schema`/`useAttributionList`/`AttributionList` 全數 `categoryGroup*`→`productVertical*`、label「商品垂直分類」；store `RULE_LABELS_FALLBACK` 補 `product_vertical`。**另一 session 請勿再碰這些檔。**

## 0. ⚠️ 先修：你搬檔引入的不一致（會壞「恢復默認」）
你把 `config/ai_judge/rule_category_groups.json` → 改名並搬成 `config/global/product_vertical.json`，
但後端 rule 版本化仍用 rule_code = `category_groups`：
- `backend/app/core/db.py:384` `RULE_CODES = (..., "category_groups")`
- `db.py:_rule_file()` 對 `category_groups` 回 `config/ai_judge/rule_category_groups.json`（**已不存在**）→ `reset_default` / `seed_rules_from_files` 會讀不到檔。

**二選一，請定案並貫徹：**
- (A) 把概念統一改名 `category_groups` → `product_vertical`：需同步改 `db.py`(RULE_CODES/_rule_file/reset 排除清單/list_problems 註解)、`category_groups.py`（檔名+函式）、`rules.py`（validate 分支 + `/category-groups/resolved` 端點名）、前端 `judgeRules.store.ts` RULE_LABELS、`RuleManager.vue` menu key、`CategoryGroupPanel.vue`、`judgment.api.ts` 端點路徑、`source-schema.constant.ts` filter。且 `_rule_file` 需特判 `product_vertical` → `config/global/product_vertical.json`（跨目錄）。
- (B) 保持 rule_code = `category_groups`，把檔案還原為 `config/ai_judge/rule_category_groups.json`（放棄 product_vertical 改名）。

> 建議 (A) 若「product_vertical」是正式命名決定；否則 (B) 最省。

## 1. 未提交且已驗證綠的編輯（在工作樹，勿覆蓋/勿重做）
本 session 已完成並 vue-tsc/pytest 綠（在你搬 judgment.json 前）：
- `backend/app/api/main.py`：`get_problems` 補 `scores/category_groups/date_from/date_to`（CSV query，`_csv_ints`/`_csv_strs` 於檔尾）；`ExportProblemsIn` + `export_problems` 補同組；→ 轉發 `db.list_problems`。
- `backend/app/core/db.py`：`export_problems_csv` 補 `score/category_group/date_from/date_to` 並轉發 `list_problems`。
- `frontend .../components/CategoryGroupPanel.vue`：`setCodes` 去空白+去重。
- `frontend .../constants/source-schema.constant.ts`：新增 `ProblemRow`/`L3Candidate` 型別。
- `frontend .../composables/useAttributionList.ts` + `pages/AttributionList.vue`：`any` → `ProblemRow`/`L3Candidate`。

## 2. 待做：config-ization 收尾（audit P0/P1，已對齊新佈局）

### 2a. `config/ai_judge/judgment.json` 補四組 map（貼上即可）
在 `polarity_labels` 之後新增：
```json
  "polarity_colors": { "positive": "green", "negative": "red", "neutral": "gray", "unknown": "orange" },
  "action_labels": {
    "rewrite_field": "改寫欄位", "fix_contradiction": "修正矛盾", "add_missing_info": "補充缺漏",
    "clarify_wording": "改寫釐清", "penalize_breach": "計點違規", "no_action": "無需動作",
    "escalate_ops": "轉其他單位", "escalate_ux": "UX 議題"
  },
  "status_labels": {
    "new": "待處理", "confirmed": "已確認", "dismissed": "已忽略", "fixed": "已修",
    "data_missing": "缺資料", "pending_evidence": "待補證據"
  },
  "status_colors": {
    "new": "arcoblue", "confirmed": "green", "dismissed": "gray", "fixed": "cyan",
    "data_missing": "red", "pending_evidence": "orange"
  },
```

### 2b. 前端改讀 SSOT（消除各寫一份 + 補漏項）
- `features/judge/constants/action.constant.ts`：改 `import judgment from '@config/ai_judge/judgment.json'; export const ACTION_LABEL = judgment.action_labels;`（自動補上原缺的 `rewrite_field`）。
- `features/judge/constants/status.constant.ts`：`STATUS_LABEL`/`STATUS_COLOR` 改讀 judgment.json（補 `pending_evidence`），保留 `STATUS_OPTS` 衍生。
- `features/judge/constants/judgment.constant.ts`：新增 `export const POLARITY_COLORS = judgment.polarity_colors;`。
- `features/judge/pages/AttributionList.vue`：移除本地 `POLARITY_COLOR`，改用 `POLARITY_COLORS`（from `../constants`）。

### 2c. `FindingCard.vue`（假 SSOT）
`confLevel`（現硬寫 `0.85 / 0.7`）改讀 `@config/ai_judge/judgment.json` 的 `ui_confidence_bands.high/mid`。

### 2d. `frontend/packages/types/src/finding.ts`（型別漏欄）
- `RecommendedAction` union 補 `'penalize_breach'`。
- 記錄 `status` union 補 `'pending_evidence'`（`backend/app/core/schema.py:144` 已含此值、db.py:239 可達）。

### 2e. 後端死碼
`backend/app/api/main.py` 的 `SOURCE_LABELS`（review/ticket/manual/csv）為死碼（批次命名實際走 `srcmap.source_label`）→ 刪除；並把 `main.py:188/229` 的 `srcmap.source_label(...)` 改用 `sources.label_for(...)`（對齊 sources.json 改名後的 工單反饋/APP 反饋/埋點反饋）。

## 3. 待做：展開行完整度
`backend/app/core/db.py` `_enrich_problem` 的 product_reviews（spec 命中）分支 `base` 未帶 `member_uuid`/`traveller_type`（專表已有欄）→ 補：
```python
"member_uuid": row.get("member_uuid"),
"traveller_type": row.get("traveller_type"),
```
前端展開行 schema 已列這兩欄（缺值顯示「—」），補後即完整。

## 4. 驗證
- 後端：`cd backend && .venv/bin/python -m pytest -q`（原 20 綠）+ `ruff check`。
- 前端：刪 `*.tsbuildinfo` 後 `npx vue-tsc -b`（exit 0）+ `eslint`。
- 端到端：`GET /api/problems?scores=1,5&category_groups=Tour&date_from=..&date_to=..` → 200；`/api/judge-rules/<category-groups 或 product-vertical>/resolved` 帶 token → 200。
