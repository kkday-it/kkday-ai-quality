# SD ② 如何建置 dashboard（L5）

> 對應 Aaron 四問之②。兩出口，唯讀聚合。Vue3 + Arco + vue-echarts。
> 落點 `frontend/apps/console/`；資料來自後端 Finding store。交付 7/1–7/3。

## 目標
把 `TicketFinding`（SSOT）呈現成兩個出口，皆唯讀聚合、不改商品。

## 後端 API（`app/api`）
| method | path | 回傳 |
|---|---|---|
| GET | `/api/findings?prod_oid=&dimension=&verdict=&status=` | `TicketFinding[]` |
| GET | `/api/findings/aggregate` | `{ matrix, kpi, rule_gaps, trend }` |
| PATCH | `/api/findings/{id}/status` | 更新 status（confirmed/dismissed/fixed）|

`aggregate` 結構：
```json
{
  "matrix": [{ "dimension": "費用資訊", "verdict": "content_unclear", "count": 12 }],
  "kpi": { "total": 0, "content_issue_pct": 0.0, "top_dimension": "" },
  "rule_gaps": [{ "dimension": "承諾與SLA", "count": 9, "has_rule": false }],
  "trend": [{ "week": "2026-W25", "dimension": "行程流程", "count": 5 }]
}
```

## 出口B｜RD/品控 分析頁（進門第一眼）
- **KPI 列**（Arco `Statistic`）：本月 Findings 數、確認內容問題 %、最痛 dimension。
- **熱力矩陣**（vue-echarts `heatmap`）：列＝8 dimension，欄＝5 verdict，格值＝count、深淺＝量級。
- **下鑽**：點格 → 該 `(dimension, verdict)` 清單（Arco `Table`）+ 週趨勢 sparkline。
- **規則缺口面板**：高頻 content_unclear/missing 但 `has_rule=false` → 標紅 + 「建議新增審核規則」CTA。

## 出口A｜PM/AM 單品頁
- 入口：選/搜 `prod_oid`（Arco `Select`），或從 B 下鑽帶入。
- 版面：該商品 Finding 依 `suspected_field` 分組（Arco `Collapse`），`is_primary` 置頂、再依 confidence 排序。
- 卡片（Arco `Card`）：dimension+verdict 徽章（`Tag`）、客戶原話、頁面 evidence、**客服標準答案（綠底可複製）**、recommended_action。
- 動作：`writer_handoff=true`→「✎ 重生」鈕（就地預覽不寫回）；否→「複製客服答案」+「開後台」。狀態：確認/忽略/已修 → PATCH。

## 技術 / 非目標
- MVP：批次預聚合 `aggregate.json` 前端直讀；量 ≤ 數千/商品，全量載入無壓力。
- 不引重 BI；熱力矩陣/sparkline 用 ECharts 自繪。
- 非目標(v1)：管理層 BI、跨商品待辦彙總、自動寫回、即時刷新。

## 驗收
兩分頁讀 Finding 並正確聚合；空狀態/錯誤不報錯；status 寫回生效。

## 交付
7/1–7/3（3d）。依賴：④ 產出的 Finding store。
