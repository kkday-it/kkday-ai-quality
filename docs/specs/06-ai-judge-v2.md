# 06 · AI 法官 V2 規格（四問融合 × 法典 60 欄位 prompt 體系）

> 深化補充版。融合三組來源：① Confluence folder 2117435397 工單驅動 SA/SD（Justin v1）+ ② 總覽頁 2126970899 四問 V3 + ③ 內容治理法典 SSOT（Google Sheets 8 面向 58/60 欄位 R1-1~R5-5）。
> 本版重點：把法典**逐欄位**落地為可判斷「具體問題欄位」（如 商品定位-商品名稱）的 judge prompt 體系。
> **Confluence 整合方案（並列 folder 532676666）**：[AI 法官 — 完整實作方案（整合版）](https://kkday.atlassian.net/wiki/spaces/VM/pages/2125561898) 父頁 `2125561898` + 7 子頁（架構/四問/法典/閉環，~18 mermaid 流程圖 + 多維度可行性風險）。本 repo doc 為實作對照版。

## 一、V2 定位與融合來源

AI 法官＝三支柱「事後執法層」（審品事前 / 撰寫輔助 / **法官事後裁決**），三支柱共用同一份內容治理法典。北極星：**降低售後進線的內容類占比**。

| 融合來源 | 提供什麼 | V2 落點 |
|---|---|---|
| folder 2117435397 SA/SD（v1） | Finding=SSOT、一條 pipeline 兩出口、L2-L4 判決流（classify→adequacy→arbiter→diagnose）、仲裁表 | 判決架構（§六） |
| 總覽 2126970899 四問 V3 | ① 售前售後進線 SQL ② dashboard 問題來源×判斷 ③ function-calling tools ④ L2-L4 核心 | 四問整合（§二） |
| 法典 SSOT（Sheets） | 8 面向 60 欄位 × canon/好壞範例/機器規則 × R1-1~R5-5 | 三層落地（§三）+ 欄位 prompt（§四） |

## 二、Aaron 四問 V2 整合

| 四問 | 分層 | V3 詳規（Confluence） | repo 落地 |
|---|---|---|---|
| ① 如何整合 | L1 | [V3 ① SD](https://kkday.atlassian.net/wiki/spaces/VM/pages/2126020657)（售前售後進線 SQL） | `judge/datasource/`（reviews/product，多管道 adapter）；schema `source_channel` A/B/C |
| ② 如何建 dashboard | L5 | [V3 ② SD](https://kkday.atlassian.net/wiki/spaces/VM/pages/2126577689)（問題來源×問題判斷） | `pages/Analytics.vue`（熱力矩陣+來源 pie+篩選器）、`ProductDetail.vue` |
| ③ 如何調用資料 | L0 | [V3 ③ SD](https://kkday.atlassian.net/wiki/spaces/VM/pages/2126905385)（function-calling tools） | `judge/datasource/product.py`（商品 tool，待 order/BigQuery 接入） |
| ④ 如何產出 action 診斷 | L2–L4 | [V3 ④ SD](https://kkday.atlassian.net/wiki/spaces/VM/pages/2126708756) | `judge/{classify,adequacy,arbiter,diagnose,pipeline}.py` + **本版欄位 prompt 體系** |

## 三、內容治理法典三層落地（repo）

| 層 | 檔案 | 內容 | 用途 |
|---|---|---|---|
| **欄位法典** | `backend/app/judge/field_codex.json` | **60 欄位** × principle/canon/allow/deny/good/bad/violation/machine_rule/phase | 逐欄位 prompt 生成的資料底座 |
| **機器規則** | `backend/app/judge/judge_rules.json` | **30 條** R1-1~R5-5（檢驗邏輯/風險等級/標記訊息/verdict_hint） | arbiter 程式化機器檢查 |
| **判決 prompt** | `backend/app/judge/prompts/{面向}/{欄位}.md` | **60 個**欄位 judge prompt（generator 產出） | LLM 欄位級判決 |

載入器 `backend/app/judge/codex.py`：`all_rules`/`get_rule`/`rules_by_dimension`/`severity_of`/`contract_breach_rules`（R 規則）；`all_fields`/`get_field`/`build_field_prompt`/`write_all_field_prompts`（欄位 prompt）。

## 四、欄位級 judge prompt 體系（完全覆蓋 60 欄位）

### 4.1 統一深度模板（吸收 repo G1/GEN-1 結構 + L2-L4 雙意見/防幻覺）

每欄位 prompt 7 段：0 治理原則 → 1 Canon 唯一判準 → 2 允許✅/禁止❌ → 3 好範例(Pass) → 4 壞範例(Red Flag) → 5 機器檢查線索 → 6 嚴格 JSON 輸出（verdict 六分類）→ 7 判決鐵則（防過擬/雙意見/防幻覺/contract_breach 橋接）。

由 `codex.build_field_prompt(field)` 從 field_codex 動態生成；`write_all_field_prompts()` 批次落地 60 檔。**法典 Sheets 改 → parse_codex 重跑 → 重生 prompt**，單向同步不漂移。

### 4.2 60 欄位覆蓋表（每欄一個 judge prompt，路徑 `prompts/{面向}/`）

| 面向 | 欄位數 | 欄位（→ 各有獨立 judge prompt） |
|---|---|---|
| 商品定位 | 12 | 商品名稱 · 商品所在地 · 商品摘要 · 商品特色 · 商品主圖 · 商品相簿 · 商品影片 · 商品說明(文字) · 商品說明(圖文) · 方案名稱 · 方案描述 · 方案圖片 |
| 行程流程 | 11 | 方案即行程線 · 行程步驟 · 停留時間 · 交通方式 · 行程總時長 · 行程時間(出發/結束) · 導遊語系 · 行程亮點 · 行程圖文模組 · 行程地圖 · 行程中注意事項 |
| 費用資訊 | 4 | 包含項目 · 不包含項目 · 現場強制費用 · 兒童/嬰兒價格規則 |
| 集合資訊 | 7 | 集合地點 · 地圖定位 · 集合時間 · 集合方式 · 集合識別方式 · 多集合點選擇 · 遲到規範 |
| 使用/兌換 | 5 | 如何使用 · 是否需憑證 · 旅客識別方式 · 必要證件 · 語系確認 |
| 成團條件 | 5 | 是否保證成團 · 最低成團人數 · 最晚成團確認時間 · 未成團處理方式 · 是否獨立成團 |
| 限制與風險 | 10 | 年齡限制 · 身心健康限制 · 體能需求 · 孕婦限制 · 參加條件限制 · 行為限制 · 安全風險說明 · 安全強制要求 · 裝備穿著要求 · 天候與保險揭露 |
| 承諾與SLA | 6 | 履約可行性確認(FFC) · 訂單確認出單SLA · 未履約處理(Fallback) · 取消退款SLA · 異動通知義務 · 不可抗力處理 |
| **合計** | **60** | 完全覆蓋 Sheets 法典 8 面向 |

> 範例：`商品定位/商品名稱` → 判「超值必玩富士山一日遊」違反 canon（行銷詞）→ verdict `real_config_issue`/`content_unclear`、flag「商品名稱含促銷詞」。

## 五、ProductContentAIChecker 可沿用 prompt 審查表

> 全方位審查原專案所有 prompt 資產，標註是否可沿用至 AI 法官 V2 及對映法典欄位。

| repo prompt 資產 | 路徑 | 行數 | 類型 | 沿用價值 | 對映法典 / 用途 |
|---|---|---|---|---|---|
| 行程流程 judge（G1/G3） | `promptVersion/*.md`（最新 593 行） | 593 | 治理判決 | **高** — 深度結構模板（DEF 定義/觸發條件/判定重點/禁止過度延伸/反例） | 行程流程：方案即行程線(R1-2)、行程錯位(R5-1)；可升級 60 欄位模板的「深度範本」 |
| 過期資訊 judge（GEN-1） | `prompts/general/latest.md` | 645 | 治理判決 | **高** — 黃金規則/STOP 規則/3 種 Pattern/9 檢核欄位 | 資訊錯位過期：過期(R5-5)；跨欄位掃描範本 |
| 通用類 v2 | `promptVersion_general/*.md` | 712 | 治理判決 | 中 — GEN-1 迭代版 | 同上，prompt 工程參考 |
| 商品名稱裁判 V2 | `judge-prompt-v2-商品名稱.md` | 113 | 攥寫裁判 | **高** — 命名規範評分（機器前置檢查+子維度評分+誤判歸因） | 商品定位-商品名稱：直接強化該欄位 prompt 的評分維度 |
| writer 生成（名稱/特色/說明） | `ai_writer_mvp/promptVersion/{product_name,highlights,description}/` | 各 600+ | 攥寫生成 | **高** — `writer_handoff=True` 時重生 | 商品名稱/特色(highlights)/說明(description) 三欄重生服務 |
| L2 分類器綱要 | L2-L4 SD §4（Confluence） | — | 判決分類 | **高** — 1 工單→N 候選、verdict 初判 | 所有欄位的判決入口（先分類 dimension+suspected_field 再路由到欄位 prompt） |
| rules.json（禁詞/情緒詞） | `ai_writer_mvp/rules.json` | — | 字典 | 中 — forbidden_terms 29 + emotional_terms 10 | 商品定位/特色等「促銷詞/情緒詞」機器前置過濾 |

**沿用策略**：repo 那 2 條深度 judge prompt（G1、GEN-1）作為 60 欄位模板的「深度升級範本」——目前 60 欄位 prompt 是法典骨架（principle+canon+範例+鐵則），高頻痛點欄位（行程、集合、SLA）可再吸收 G1/GEN-1 的 DEF/STOP/Pattern 深度；商品名稱欄位直接併入 judge-prompt-v2 的評分維度。

## 六、判決架構（兩階段 + 仲裁，融合 L2-L4 SD）

```
進線(售前售後 SQL / 評論 / 工單) → NormalizedTicket
   │  classify（L2，1 工單→N 候選：dimension + suspected_field + 初判 verdict）  [LLM #1]
   ▼  路由到對應欄位 prompt：prompts/{dimension}/{suspected_field}.md
   │  欄位 judge（L3 第二意見，只看欄位原文 + 客服 ground truth）              [LLM #2]
   ▼  arbiter.reconcile（純程式仲裁表：內容證據 > 客訴語氣）
   │  + codex 機器規則交叉（judge_rules R1-R5：缺失/錯位/過期硬檢查）
   ▼  diagnose.build_action（verdict → action + writer_handoff 防幻覺 + 執行層角色/平台）
   ▼  TicketFinding（SSOT）→ 兩出口 dashboard
```

verdict 六分類（schema.Verdict）：`real_config_issue`/`content_missing`/`content_unclear`/`contract_breach`(履約違規)/`customer_misread`/`escalate_ops`。仲裁表見 specs/04 + Confluence L2-L4 SD §5.2。

## 七、里程碑與待辦

| 項目 | 狀態 |
|---|---|
| 法典三層落地（field_codex 60 / judge_rules 30 / 60 prompt + generator） | ✅ 本版完成 |
| 評論線 + dashboard 端到端（stub） | ✅ M0-M3 |
| classify/adequacy 接欄位 prompt（取代啟發式 stub） | ⏳ 待 OpenAI key 6/25 |
| 售前售後進線 SQL adapter（V3 ①）+ order/BigQuery | ⏳ Gary 申請權限 + 6/30 工單 API |
| 高頻欄位 prompt 深度升級（吸收 G1/GEN-1 範本） | ⏳ 待 golden 校調 |
| golden/eval + 信心度 calibration | ⏳ 待 golden |

> 法典完整 SSOT 仍為 Google Sheets；本 repo 為 Phase1 可執行落地（60 欄位骨架 prompt + 30 機器規則）。Sheets 改動 → `data/parse_codex.py` 重跑 → 重生 prompt，保持單向同步。
