# SD ③ 如何調用其他資料（L0 + function-calling tools）

> 對應 Aaron 四問之③。每資料源包成 OpenAI SDK tool，LLM 自主決定呼叫（Gary 構想）。
> 落點 `backend/app/judge/datasource/`。

## 目標
判決過程中 AI 依需要自主撈證據（商品欄位 / 訂單 / 工單），證據齊備再判 → 判更準。

## tools（OpenAI SDK function 定義）
| tool | 參數 | 回傳 | 來源 / 認證 | 狀態 |
|---|---|---|---|---|
| `fetch_reviews` | `prod_id, sort=RATING_ASC, page=1` | `reviews[]`（rating/title/body/postDate）| Review API（Nuxt BFF 或內網 Review Service）| ✅ 已驗證 |
| `fetch_product` | `prod_id, lang=zh-tw` | 原始商品 JSON | api-b2c CDN + 固定 Android header（`b2c-token1`/`x-auth-token`）| ✅ 可做 |
| `extract_fields` | `product_json` | 9 邏輯欄位 dict | 純解析（無外呼）| ✅ 可做 |
| `fetch_order` | `order_id` | 訂單詳情 | order API | ⚠️ 待權限（Gary）|
| `fetch_ticket` | `ticket_id` | 工單 + 客服對話 | FreshDesk | ⚠️ 6/30 API |

## extract_fields 欄位映射（商品 JSON → 邏輯欄位）
| 邏輯欄位 | 來源 path（沿用 general_kkday_extractor 邏輯）|
|---|---|
| `prod_name` | `data.product.name` |
| `prod_summary` | `prod_intro_summary`（別名正規化）|
| `prod_feature` | `data.product.feature` |
| `prod_schedules` | `data.product.modules.schedule` |
| `pkg_desc` | `pkg.modules.package_desc` |
| `pkg_schedules` | `pkg.modules.schedule` |

## 限制 / 注意
- 商品：語系 fallback（zh-tw 無方案 → en/ja…）；`verify=False` 沿用既有（待修正憑證）。
- 評論：proxy 路徑單品上限 150；全量走內網 Review Service。datadome：production 走後端內網避開。
- 商品 JSON 大（~10 萬 chars）→ extract_fields 後才餵 LLM，勿整包進 context。

## 驗收
- 150665：`fetch_product`→`extract_fields` 得 9 欄原文（含纜車賣點在 prod_name）；`fetch_reviews` 得差評。

## 交付
商品 tool 6/23–6/24（與①並行）；order/工單 tool 列 **P2**。
