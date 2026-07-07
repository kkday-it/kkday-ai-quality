# SD ① 如何整合（L1 接入）

> 對應 Aaron 四問之①。負面訊號 → `NormalizedTicket`。交付 6/23–6/24（無外部依賴）。
> schema 見 `backend/app/core/schema.py`；落點 `backend/app/judge/ingest/`。

## 目標
把多來源負面訊號正規化成統一 `NormalizedTicket`，下游 L2–L4 不感知來源。MVP 單管道＝**商品差評**。

## adapter 介面（Protocol）
```python
class TicketSourceAdapter(Protocol):
    source: Literal["review", "ticket", "order_message"]
    def fetch(self, prod_oid: str, *, since: str = "", until: str = "",
              limit: int = 50) -> list[NormalizedTicket]: ...
```
- MVP 實作 `ReviewAdapter`（差評優先 `sort=RATING_ASC`）；`TicketAdapter` 預留（6/30 工單 API 後）。
- 換來源只新增 adapter，parser 與 L2–L5 不動。

## parser 映射（評論 → NormalizedTicket）
| NormalizedTicket | 來源（fetch_reviews 回應）|
|---|---|
| `ticket_id` | review `id`（冪等鍵）|
| `source` | `"review"` |
| `prod_oid` | 請求參數注入（回應體不含）|
| `rating` | `rating`（1–5，嚴重度訊號）|
| `comment` | `body.origin`（+ `title.origin` 前綴）|
| `created_at` | `postDate` |
| `cs_conversation` | 評論無 → `[]`（工單來源才有）|

## 冪等 / 錯誤
- 冪等鍵 `ticket_id`；store 以此去重，重跑覆蓋不產重複 Finding。
- 單筆 parse 失敗 → dead-letter（`findings/_deadletter.jsonl`），不中斷批次。
- 評論無 `cs_conversation` → L4 缺 ground truth，`action_detail` 退為「需 PM 補事實」。

## 驗收
- 輸入商品 150665 → 輸出 10 筆 `NormalizedTicket`（含纜車案例，rating=1）。
- 重跑同商品不產重複。

## 交付
6/23–6/24（2d）。依賴：無（評論 API 已驗證可拉）。
