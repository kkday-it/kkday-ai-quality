# 05 · 內容治理規則庫（判決法典 / SSOT）

> AI 法官判決引擎的業務裁決標準。當有爭議 / 反饋發生時，以本法典判定**層級**與**對應行動**。
> 程式對應：`backend/app/core/schema.py`（Dimension / Verdict / RecommendedAction）、`backend/app/judge/diagnose.py`（`_EXEC_MAP` / `_ACTION_MAP`）。

## 使用時機

當客人負向訊號（評論 / 工單 / 訂單訊息）或供應商申訴進入，AI 法官以本法典：
1. 歸因到「哪個商品的哪個欄位」
2. 判定爭議**類型 / verdict**
3. 輸出**對應行動**與**執行層角色 / 平台**

## 8 內容核對欄位（dimension）

| 欄位 | 說明 |
|---|---|
| 商品定位 | 商品名稱 / 定位 / 賣點 |
| 行程流程 | 行程安排、順序、時長 |
| 費用資訊 | 含 / 不含、加購、價格 |
| 集合資訊 | 集合點、接送、時間 |
| 使用 / 兌換方式 | 票券綁定、兌換、入場 |
| 成團條件 | 最低成團、取消改期 |
| 限制與風險 | 健康限制、天候、年齡 |
| 承諾與 SLA | 服務承諾、履約標準 |

> 三支柱（審品 / 法官 / 攥寫）共用同一分類法（取自 ① 審品 `rules.json`）。

## 大原則

- AI 法官審完**信心度高 → 直接判決**
- AI 法官審完**信心度低 → 轉真人審**
- 系統落地：`confidence < 0.7` 且為內容問題且 `status=new` → 進「低信心待人工」KPI；adequacy 採雙意見交叉，arbiter 純程式仲裁。

## 判定類型 → verdict → 行動 → 執行層（核心對照表）

| # | 法典判定類型 | 觸發條件 | 法典行動 | verdict | 建議動作 | owner_role | exec_platform |
|---|---|---|---|---|---|---|---|
| ① | 規則庫已定義，供應商未正確撰寫 | 內容寫錯 / 矛盾 | 要求供應商修正內容 | `real_config_issue` | 修正矛盾 | Coach（AM/BD） | SCM2.0·Be2 |
| ② | 規則庫未定義，產生內容爭議 | 規則庫無對應 | 依嚴重度定級 + **回饋修法** + 要求供應商調整 | `content_missing` / `content_unclear` | 補充缺漏 / 改寫釐清 | Rule Maker（PM） | PM 後台（+ 規則缺口閉環） |
| ③ | 撰寫內容合規，但承諾履約不符 | 內容對、供應商沒做到 | **計點違規** + 要求改善 | `contract_breach` | 計點違規 | Disciplinary（ERC） | 供應商計點系統·SCM2.0 |
| ④ | 非內容類服務履約問題 | 系統 / 出貨 / 服務 | **感知通報** + 協作處理 | `escalate_ops` | 轉其他單位 | Customer Advocate（CS） | 客服系統 |

### 額外 verdict（法典 4 類之外的防呆洞察）

| verdict | 條件 | 行動 | owner_role |
|---|---|---|---|
| `customer_misread` | 內容其實已清楚、客人誤解 | 不改內容，UX / 呈現議題 | Rule Maker（PM/UED） |

> `customer_misread` 是避免把客人誤讀**誤判**成供應商過失的防呆閘，對應「撰寫內容合規」但結果非履約問題。

## verdict 全集（6）

| verdict | 中文 | 是否內容問題 | 是否進清單 |
|---|---|---|---|
| `real_config_issue` | 設定錯誤 | ✅ | ✅ PM 修改清單 |
| `content_missing` | 缺漏 | ✅ | ✅ PM 修改清單 |
| `content_unclear` | 模糊 | ✅ | ✅ PM 修改清單 |
| `contract_breach` | 履約違規 | ❌（內容合規） | ✅ 違規追蹤清單（ERC） |
| `customer_misread` | 客戶誤解 | ❌ | ❌（UX 洞察） |
| `escalate_ops` | 非內容 | ❌ | ❌（轉其他單位） |

## 防幻覺鐵則

- `content_missing`（缺事實）一律 `writer_handoff = False`，需 PM 手動補真實資訊，writer 不可生成。
- 可重生（`writer_handoff = True`）僅限 `content_unclear` / `real_config_issue` 且欄位 ∈ {prod_name, prod_feature, prod_summary}（既有事實改寫）。
- `contract_breach` / `customer_misread` / `escalate_ops` 皆不重生（非內容改寫範疇）。

## 北極星

降低售後進線的內容類占比。`contract_breach` 的計點違規回饋供應商管理，`content_missing` / `content_unclear` 的規則缺口回饋 `rules.json` 修法，形成閉環。
