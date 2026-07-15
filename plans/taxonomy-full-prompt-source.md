# Plan：分類體系全 Prompt-Source 動態化（方案甲）

> 狀態：進行中（P1 起）。目標＝分類的**類別＋層級 100% 由 prompt 定義**、runtime 動態解析成分類樹；
> 程式碼零 taxonomy/層級假設。保留 6 域並行判決引擎（域＝工程切分單位，不外顯為固定 L1）。

## 決策（已與使用者拍板）

- **甲**：保留 6 域並行引擎；分類「類別＋層級」全進 prompt，落庫改通用「分類路徑」。
- **留穩定 code**：facet_catalog 每個類別給穩定 code（label 可自由改不斷篩選/歷史）。
- **域 metadata（中文名/action/owner）＋證據閘也移進 prompt** → `domains.json` 退場、`judgment.json.evidence_gated_domains` 移出。
- **留 config（全域管線旋鈕）**：confidence_tiers / attr_min_confidence 等信心閘 / prejudge 旋鈕（model·effort·tier·max_attributions）/ auto_confirm / 管線 label / polarity_gate.attribute_when。

## 設計原則

1. **分類體系＝prompt 的 runtime 衍生視圖**（`structure()` 解析），非 DB/config 真相源；prompt 改 → `reload()` → 重 parse → 體系自動換。
2. **已判紀錄存快照**（path 的 code+label），與當前 prompt 解耦——改 label 不動歷史、結構大改就重判；新判決讀動態樹。
3. **code 穩定鍵**；存檔**自洽 drift 護欄**（facet_catalog codes == Schema enum，現有）+ 樹自洽（parent code 存在）。
4. 判準：值是「某支 prompt 自己的語義」→ prompt；「全域管線旋鈕」→ config。

## 資料模型

- **prompt `<facet_catalog>`**：可變深度——code 段數表層級（`C-1-1`＝2 層、`C-1-1-1`＝3 層…），parser 由 code prefix 建 parent；label 隨行。
- **prompt `<domain_meta>`**（新區塊）：`label` / `action` / `owner` / `evidence_gated`（bool）。
- **prompt `## Schema`**：`attributions[].code` enum＝facet_catalog 全 code（leaf 或任一層，看判準）；path 由樹派生，不由 LLM 輸出。
- **DB judgments**：去 `l1_code/l1_label/l2_code/l2_label`（+idx）；加 `category_path`（JSONB `[{code,label}...]`）+ `category_code`/`category_label`（leaf，索引/篩選熱路徑）；保留 `domain`（引擎切分，內部）。
- **DTO**：`{category_path:[{code,label}], category:{code,label}(leaf), stage, confidence, content, ...}`（去 l1/l2）。

## Phases（每階段獨立 commit + 驗證 + 分批推送）

### P1 · Prompt 動態解析（核心·先行）
- `prompt_source`：`_parse_facets`→`_parse_taxonomy`（可變深度 code→樹，prefix 建 parent，保序）；新增 `_parse_domain_meta`（解析 `<domain_meta>`）；`structure()` 回「分類樹 + 域 meta（含 evidence_gated）」；`validate` 沿用 drift 護欄 + 驗樹自洽。**向後相容**：facet 仍 2 層時樹即 2 層；`<domain_meta>` 缺時回退 domains.json（過渡）。
- 7 支域 prompt md：加 `<domain_meta>`（搬 domains.json 值 + supplier 標 `evidence_gated`）。
- `ai_judge`：改吃樹；`l2_by_code`/`cascade_tree`→通用 `node_by_code`/`taxonomy_tree`；action/owner/label/evidence_gated 從 structure() 取。
- 移除 `domains.json`、`judgment.json.evidence_gated_domains`（改讀 domain_meta）。
- 驗證：pytest（structure/validate/schema）+ 單支 prompt 測試。

### P2 · DB migration（通用分類欄）
- migration：drop l1/l2 欄+idx；add `category_path`(JSONB)/`category_code`/`category_label`(+idx)；keep `domain`。**drop 前 pg_dump**；downgrade 加回 nullable。
- **回填**：舊列 `{l1,l2}` 組成 2 層 `category_path` 快照（保歷史可顯示）。
- tables.py 同步。驗證：容器 migration + 煙霧。

### P3 · DTO / 讀寫 / 引擎輸出
- `schema.TicketFinding`：去 l1/l2，加 category_path/leaf；`to_columns` 輸出 path+leaf。
- `prejudge`：引擎輸出 category_path（leaf code → 樹派生 path）；`_derive_stage` landing＝leaf code。
- `attribution_dto` / `findings` / `judgment_history` digest / `export` / `problems`+`prejudge_targets` 篩選 → 通用 path（篩選＝path 任一節點 code IN，子樹語義）。
- 驗證：pytest。

### P4 · 前端
- `Attribution` 型別：去 l1/l2，加 category_path/category。
- 顯示：麵包屑渲染 `category_path`（可變深度）。
- 篩選：cascader 吃可變深度 `taxonomy_tree`；篩選值＝任一節點 code（子樹）。
- ⚠️ **儀表板**：`by_l1/by_l2` 固定兩層 → 改通用聚合（「當前選中層分布」或「leaf 分布」）——**P4 先出 UX 樣式確認再改**（最大未定點）。
- 驗證：vue-tsc/eslint + 走查。

### P5 · 文檔 + 退場 + 驗證
- docs-sync（root/api/db/judge/config README）；清 l1/l2 命名痕跡（走 `.claude/rules/feature-retirement.md`）；記 domains.json 退場。
- 全量驗證 + 殘留掃描全清 + 分批推送。

## 風險 / 護欄
- P2 migration 不可逆 → pg_dump + downgrade + 回填歷史 path。
- P4 儀表板可變深度 UX＝最大未定點 → 先出樣式。
- 自洽 drift 護欄（存檔即擋不一致 prompt）；每 phase 綠燈才進下一 phase。
