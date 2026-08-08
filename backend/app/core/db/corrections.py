"""人工糾正歸因（改 / 增 / 標記誤判 / 還原 / 複審確認）——`attribution_tbl` 唯一的人工寫入路徑。

在此之前歸因列**完全由 AI 產生、無任何人工可改欄位**，AI 判錯時人只能重跑 AI。本模組補上這個
缺口，並讓每次糾正都留下**欄位級的對錯判準**（`changed` / `confirmed_fields`）——這是這套設計
避免重蹈 2026-08-04 人工判決軸覆轍的關鍵：舊版只有「確認／忽略」兩顆沒有資訊量的按鈕，實測
6,242 條歸因裡只有 1 個人按過。欄位級 delta 的形狀刻意對齊 `judge.prompt_regression` 的
`corrections` / `confirmed` 判準，日後可直接回餵 Prompt 迭代。

**託管狀態的入口**：任一列被本模組寫過（`is_manual_created` / `is_human_corrected` /
`is_deleted` 任一為真），該則反饋即進入「人工託管」——重新初判不再覆蓋現值，改走待審建議
（見 `findings.replace_source_findings`）。所以本模組的每個寫入都是**單向閂鎖**，是有意的。

寫入紀律（全部由服務端強制，不信任 client）：

- `l1_label` / `l2_label` 一律自 `ai_judge` 解析——label 是判決當下的分類名快照，讓 client 送
  label 等於開一個造假入口
- `conf_tier` 設字面值 `'human'` 而非 NULL：設 NULL 會讓人工列從信心分層篩選與 by_tier 聚合
  整批消失。⚠️ **但字面值本身不會讓聚合自動認得它**——`attribution._by_tier` 與其查詢端必須
  顯式處理 `conf_tier='human'`（2026-08-07 補；在那之前這裡寫的是「既有機制照舊運作」，
  是假的保證：查詢過濾 `conf_value IS NOT NULL` 會把人工列整批濾掉，而 dev 尚無人工列所以
  完全隱形）。日後新增任何吃 `conf_value` 的聚合，都要同步決定人工列怎麼算
- `conf_value` / `conf_raw` 設 NULL：原 AI 信心描述的是**舊分類**，掛在新分類上是謊言。原值
  完整保存在 correction 事件的前值快照裡，供對比 UI 顯示「原 AI 0.82」
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy import insert as sa_insert
from sqlalchemy import text as sa_text
from sqlalchemy import update as sa_update

from app.core.db import attribution_history as _history
from app.core.db import tables as T
from app.core.db._shared import attribution_dto, live_attr_cond
from app.core.paths import AI_JUDGE_DIR
from app.core.schema import polarity_for_sentiment

# 人工列的信心分層字面值（見模組 docstring）。
HUMAN_TIER = "human"


class CorrectionError(Exception):
    """人工糾正的業務規則違反；`code` 供 router 對映 HTTP 狀態碼。

    刻意不讓 db 層直接拋 HTTPException——db 模組被腳本與測試直呼，綁 FastAPI 會讓它們也得吞
    web 框架的依賴。router 端一行 mapping 即可。
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code  # not_found | conflict | invalid
        self.detail = detail


def _cfg() -> dict[str, Any]:
    """讀糾正政策（可改欄白名單 + 理由長度門檻）；檔案缺失時回內建保守值。"""
    path = AI_JUDGE_DIR / "correction.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "editable_fields": ["l1_code", "l2_code", "polarity", "sentiment_score"],
            "reason_min_length": 5,
            "reason_max_length": 500,
        }
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def editable_fields() -> list[str]:
    """可由人工修改的欄位白名單（前後端同讀 config/ai_judge/correction.json）。"""
    return list(_cfg()["editable_fields"])


def validate_reason(reason: str) -> str:
    """理由門檻檢查 → 回正規化後的理由；不合格拋 `CorrectionError('invalid')`。"""
    cfg = _cfg()
    text = (reason or "").strip()
    lo, hi = int(cfg["reason_min_length"]), int(cfg["reason_max_length"])
    if len(text) < lo:
        raise CorrectionError("invalid", f"糾正理由至少 {lo} 個字，請說明具體原因")
    if len(text) > hi:
        raise CorrectionError("invalid", f"糾正理由請控制在 {hi} 字以內（目前 {len(text)} 字）")
    return text


def _resolve_labels(l1_code: str | None, l2_code: str | None) -> dict[str, Any]:
    """分類 code → 服務端解析的 label（同時做值域校驗）；不存在的 code 拋 invalid。"""
    from app.core.judge_config import ai_judge

    out: dict[str, Any] = {}
    if l2_code is not None:
        node = ai_judge.l2_by_code(l2_code)
        if node is None:
            raise CorrectionError("invalid", f"L2 面向代碼不存在於分類體系：{l2_code}")
        # L2 節點自帶所屬域與兩層 label（鍵：l1_domain / l1_label / l2_label）——l1 一律由它推導，
        # 避免人工送出 l1/l2 不相容的組合（例如把餐飲品質掛到供應商域）。
        out["l2_code"] = l2_code
        out["l2_label"] = node["l2_label"]
        out["l1_code"] = node["l1_domain"]
        out["l1_label"] = node["l1_label"]
    elif l1_code is not None:
        label = ai_judge.domain_label(l1_code)
        if not label:
            raise CorrectionError("invalid", f"L1 域代碼不存在於分類體系：{l1_code}")
        out["l1_code"] = l1_code
        out["l1_label"] = label
    return out


def _validate_sentiment(score: Any) -> int:
    """情緒分校驗 → 回 int；不合法拋 invalid。

    **傾向不由 client 送**，一律由情緒分派生（見 `polarity_for_sentiment`）——讓人另外選傾向的話，
    「正向＋情緒分 1」這種自相矛盾的組合就有機會落庫，而且使用者也搞不清楚兩個欄位誰說了算。
    """
    if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 5:
        raise CorrectionError("invalid", f"情緒分須為 1-5 的整數（收到 {score!r}）")
    return score


def _row_of(c, source: str, source_id: str, attribution_oid: int):
    """取該反饋底下的指定歸因列；不屬於這則反饋即 not_found（擋跨反饋越權改值）。"""
    jg = T.attributions
    row = (
        c.execute(
            select(jg).where(
                jg.c.attribution_oid == attribution_oid,
                jg.c.source == source,
                jg.c.source_id == source_id,
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise CorrectionError("not_found", f"歸因不存在或不屬於此反饋：oid={attribution_oid}")
    return row


def _assert_slot_free(c, source: str, source_id: str, l1: str, l2: str, exclude_oid: int) -> None:
    """目標 (L1, L2) 面向不得已被同反饋的其他列佔用——含 tombstone。

    刻意**不自動合併**：合併要決定保留誰的摘要／證據／信心，任何選擇都是替使用者猜。直接擋下並
    在訊息裡指路，行為可預測。tombstone 也算佔用，因為自然鍵唯一索引不分是否已刪。
    """
    jg = T.attributions
    hit = (
        c.execute(
            select(jg.c.attribution_oid, jg.c.is_deleted).where(
                jg.c.source == source,
                jg.c.source_id == source_id,
                jg.c.l1_code == l1,
                jg.c.l2_code == l2,
                jg.c.attribution_oid != exclude_oid,
            )
        )
        .mappings()
        .first()
    )
    if hit is None:
        return
    if hit["is_deleted"]:
        raise CorrectionError(
            "conflict",
            f"該面向先前被標記為 AI 誤判（oid={hit['attribution_oid']}），請先還原那一條再修改",
        )
    raise CorrectionError(
        "conflict",
        f"此反饋已有該面向的歸因（oid={hit['attribution_oid']}），請直接修改那一條而非改成重複的",
    )


def _snapshot_current(c, source: str, source_id: str) -> list[dict]:
    """該反饋當前**存活**歸因 → 事件快照陣列（與 kind='prejudge' 同形，前端 diff 邏輯共用）。"""
    jg = T.attributions
    rows = (
        c.execute(
            select(jg)
            .where(jg.c.source == source, jg.c.source_id == source_id, live_attr_cond())
            .order_by(jg.c.attribution_oid)
        )
        .mappings()
        .all()
    )
    # ⚠️ 讀結果的 mapping 一律用 **DB 欄名**（`key=` 只在組查詢時生效），故此處手動對回 snapshot_of
    # 期望的鍵；直接把 row 丟進去會靜默少掉 action 與 stage 兩欄。
    return [
        _history.snapshot_of(
            {
                "polarity": r["polarity"],
                "sentiment_score": r["sentiment_score"],
                "prejudge_stage": r["prejudge_stage"],
                "l1_code": r["l1_code"],
                "l1_label": r["l1_label"],
                "l2_code": r["l2_code"],
                "l2_label": r["l2_label"],
                "conf_value": r["conf_value"],
                "conf_raw": r["conf_raw"],
                "conf_tier": r["conf_tier"],
                "summary": r["summary"],
                "evidence": r["evidence"],
                "action": r["recommended_action"],
                "is_primary": r["is_primary"],
            }
        )
        for r in rows
    ]


def _dto_of(c, attribution_oid: int) -> dict:
    """單列 → API DTO（動作後回給前端就地更新該列，免整頁重載）。"""
    jg = T.attributions
    row = c.execute(select(jg).where(jg.c.attribution_oid == attribution_oid)).mappings().first()
    return attribution_dto(dict(row)) if row else {}


def list_record_attributions(source: str, source_id: str) -> dict:
    """糾正工作台的讀取端點：一則反饋的**全部**歸因，含人工標記為 AI 誤判的 tombstone。

    這是全專案**第二個**刻意回傳 tombstone 的路徑（第一個是 `prejudge_targets`，那裡問的是
    「判過沒有」）。工作台需要它是因為「還原誤判」的入口只能長在看得到那些列的地方——
    列表與所有統計都走 `_jg_join_cond` / `_jg_exists` 的 chokepoint，一律排除 tombstone。

    **回傳形狀刻意用兩個陣列，而不是在 `attribution_dto` 上加一個 `is_deleted` 旗標**：
    加欄的話 `_ATTRIBUTION_DTO_WIRE` 凍結快照要改，而且 `/api/problems`、待審建議清單等
    **所有**既有消費端都會平白多拿一個恆為 false 的欄。用兩個陣列則 tombstone 的身分由
    「它在哪個陣列」承載，DTO 與凍結契約原封不動——既是更小的改動，也是更直白的表達。

    `human_managed` **必須**走 `suggestions.is_human_managed` 而不是在這裡用旗標重新派生：
    那個判定的定義會長（2026-08-07 才加了 `review_status='confirmed'`，補判決時還要加
    `is_verdicted`），兩份實作必然靜默分歧。

    Returns:
        `{live: [dto…], deleted: [dto…], human_managed: bool, suggestion_count: int}`；
        兩個陣列各自依 `attribution_oid` 遞增（＝初判產出順序，與列表的 fan-out 一致）。
    """
    from app.core.db import suggestions as _suggestions

    jg = T.attributions
    with T.get_engine().connect() as c:
        rows = (
            c.execute(
                select(jg)
                .where(jg.c.source == source, jg.c.source_id == source_id)
                .order_by(jg.c.attribution_oid)
            )
            .mappings()
            .all()
        )
        human_managed = _suggestions.is_human_managed(c, source, source_id)
    counts = _suggestions.pending_counts(source, [source_id])
    return {
        "live": [attribution_dto(dict(r)) for r in rows if not r["is_deleted"]],
        "deleted": [attribution_dto(dict(r)) for r in rows if r["is_deleted"]],
        "human_managed": human_managed,
        "suggestion_count": counts.get(source_id, 0),
    }


def correct_attribution(
    source: str,
    source_id: str,
    attribution_oid: int,
    *,
    changes: dict[str, Any],
    reason: str,
    author: str,
) -> dict:
    """修改一條 AI 歸因的分類／傾向（單向閂鎖：該反饋自此進入人工託管）。

    Args:
        changes: 只帶要改的欄（白名單見 `editable_fields()`）；未帶的欄沿用現值。
        reason: 糾正理由（必填，門檻見 correction.json）。
        author: 糾正者（無 SSO 時為 system）。

    Returns:
        {"attribution": 更新後的 DTO, "changed": {欄: [前值, 後值]}}

    Raises:
        CorrectionError: not_found（跨反饋 / 不存在）｜conflict（目標面向已被佔用）｜
            invalid（理由不合格 / 欄位不可改 / 值域不符 / 目標已是 tombstone）。
    """
    reason = validate_reason(reason)
    allowed = set(editable_fields())
    unknown = sorted(set(changes) - allowed)
    if unknown:
        raise CorrectionError(
            "invalid", f"這些欄位不開放人工修改：{unknown}（可改：{sorted(allowed)}）"
        )
    if not changes:
        raise CorrectionError("invalid", "沒有任何要修改的欄位")

    jg = T.attributions
    with T.get_engine().begin() as c:
        row = _row_of(c, source, source_id, attribution_oid)
        if row["is_deleted"]:
            raise CorrectionError("conflict", "該歸因已標記為 AI 誤判，請先還原再修改")

        values: dict[str, Any] = {k: v for k, v in changes.items() if k in allowed}
        if "sentiment_score" in values:
            score = _validate_sentiment(values["sentiment_score"])
            # 傾向是派生值不是輸入值：改了情緒分，傾向就跟著走同一份區間定義
            values["polarity"] = polarity_for_sentiment(score)
        if "l1_code" in values or "l2_code" in values:
            values.update(
                _resolve_labels(
                    values.get("l1_code", row["l1_code"]), values.get("l2_code", row["l2_code"])
                )
            )
            _assert_slot_free(
                c, source, source_id, values["l1_code"], values["l2_code"], attribution_oid
            )

        changed = {
            k: [row[k], v] for k, v in values.items() if row[k] != v and not k.endswith("_label")
        }
        c.execute(
            sa_update(jg)
            .where(jg.c.attribution_oid == attribution_oid)
            .values(
                **values,
                conf_tier=HUMAN_TIER,
                conf_value=None,
                conf_raw=None,
                prejudge_stage="judged",
                is_human_corrected=True,
                review_status="corrected",
                correction_reason=reason,
                modify_user=author,
                modify_date=func.now(),
            )
        )
        _history.insert_manual_event(
            c,
            source,
            source_id,
            kind="correction",
            params={"op": "update", "attribution_oid": attribution_oid, "changed": changed},
            attributions=_snapshot_current(c, source, source_id),
            author=author,
            reason=reason,
        )
        return {"attribution": _dto_of(c, attribution_oid), "changed": changed}


def create_attribution(
    source: str, source_id: str, *, values: dict[str, Any], reason: str, author: str
) -> dict:
    """人工新增一條 AI 漏掉的歸因（`is_manual_created`，無 model、無信心值）。

    `values` 必填 l2_code / polarity / summary；l1 由 L2 推導。summary 這裡必須人填——手動新增的
    列從頭到尾是人寫的，不牽涉 evidence grounding（見 correction.json 的取捨說明）。
    """
    reason = validate_reason(reason)
    l2_code = values.get("l2_code")
    if not l2_code:
        raise CorrectionError("invalid", "新增歸因必須指定 L2 面向")
    summary = values.get("summary")
    if not summary or not str(summary).strip():
        raise CorrectionError("invalid", "新增歸因必須填寫摘要")
    if values.get("sentiment_score") is None:
        raise CorrectionError("invalid", "新增歸因必須填寫情緒分（1-5；傾向由它派生）")
    score = _validate_sentiment(values["sentiment_score"])
    polarity = polarity_for_sentiment(score)

    labels = _resolve_labels(values.get("l1_code"), l2_code)
    jg = T.attributions
    with T.get_engine().begin() as c:
        _assert_slot_free(c, source, source_id, labels["l1_code"], labels["l2_code"], -1)
        oid = c.execute(
            sa_insert(jg)
            .values(
                source=source,
                source_id=source_id,
                polarity=polarity,
                sentiment_score=score,
                prejudge_stage="judged",
                **labels,
                conf_tier=HUMAN_TIER,
                summary=summary if isinstance(summary, dict) else {"zh-tw": str(summary)},
                evidence=values.get("evidence"),
                # ⚠️ 組查詢時一律用 Python key（`action`），DB 欄名 recommended_action 會被判成未消費欄
                action=values.get("action"),
                model=None,
                is_primary=False,
                is_auto_accepted=False,
                created_at=func.now(),
                is_manual_created=True,
                review_status="corrected",
                correction_reason=reason,
                create_user=author,
            )
            .returning(jg.c.attribution_oid)
        ).scalar()
        _history.insert_manual_event(
            c,
            source,
            source_id,
            kind="correction",
            params={"op": "create", "attribution_oid": oid, "changed": {}},
            attributions=_snapshot_current(c, source, source_id),
            author=author,
            reason=reason,
        )
        return {"attribution": _dto_of(c, oid), "changed": {}}


def delete_attribution(
    source: str, source_id: str, attribution_oid: int, *, reason: str, author: str
) -> dict:
    """標記一條歸因為 AI 誤判（tombstone：讀取層排除，但保留列佔住自然鍵）。

    刻意不硬刪：列留著才能讓「重新初判把人工刪掉的歸因悄悄復活」在物理上不可能——自然鍵唯一
    索引會擋下同面向的重新插入。
    """
    reason = validate_reason(reason)
    jg = T.attributions
    with T.get_engine().begin() as c:
        row = _row_of(c, source, source_id, attribution_oid)
        if row["is_deleted"]:
            raise CorrectionError("conflict", "該歸因已經是標記誤判狀態")
        c.execute(
            sa_update(jg)
            .where(jg.c.attribution_oid == attribution_oid)
            .values(
                is_deleted=True,
                review_status="corrected",
                correction_reason=reason,
                modify_user=author,
                modify_date=func.now(),
            )
        )
        _history.insert_manual_event(
            c,
            source,
            source_id,
            kind="correction",
            params={"op": "delete", "attribution_oid": attribution_oid, "changed": {}},
            attributions=_snapshot_current(c, source, source_id),
            author=author,
            reason=reason,
        )
        return {"attribution": _dto_of(c, attribution_oid), "changed": {}}


def restore_attribution(
    source: str, source_id: str, attribution_oid: int, *, reason: str, author: str
) -> dict:
    """還原被標記為誤判的歸因（撤銷 tombstone）。"""
    reason = validate_reason(reason)
    jg = T.attributions
    with T.get_engine().begin() as c:
        row = _row_of(c, source, source_id, attribution_oid)
        if not row["is_deleted"]:
            raise CorrectionError("conflict", "該歸因不是標記誤判狀態，無需還原")
        _assert_slot_free(c, source, source_id, row["l1_code"], row["l2_code"], attribution_oid)
        c.execute(
            sa_update(jg)
            .where(jg.c.attribution_oid == attribution_oid)
            .values(
                is_deleted=False,
                correction_reason=reason,
                modify_user=author,
                modify_date=func.now(),
            )
        )
        _history.insert_manual_event(
            c,
            source,
            source_id,
            kind="correction",
            params={"op": "restore", "attribution_oid": attribution_oid, "changed": {}},
            attributions=_snapshot_current(c, source, source_id),
            author=author,
            reason=reason,
        )
        return {"attribution": _dto_of(c, attribution_oid), "changed": {}}


def confirm_attribution(
    source: str,
    source_id: str,
    attribution_oid: int,
    *,
    confirmed_fields: list[str] | None = None,
    note: str = "",
    author: str,
) -> dict:
    """複審確認 AI 判對了（`review_status='confirmed'`）——待複審的出口。

    `confirmed_fields` 是「人看過且確認正確」的欄位清單，語義直接對齊
    `judge.prompt_regression` 的 `confirmed`：沒列進去的欄＝人沒看過、**不計分**。這條區分很重要
    ——拿 AI 舊判當標準答案會讓回歸分數憑空虛高。

    ⚠️ 確認**不會**讓該反饋進入人工託管（沒有動任何 `is_*` 旗標）：確認是「AI 判對了」，重新初判
    仍應照常更新它。只有真的改過值才需要保護。
    """
    fields = list(confirmed_fields or [])
    jg = T.attributions
    with T.get_engine().begin() as c:
        row = _row_of(c, source, source_id, attribution_oid)
        if row["is_deleted"]:
            raise CorrectionError("conflict", "該歸因已標記為 AI 誤判，無法確認為正確")
        c.execute(
            sa_update(jg)
            .where(jg.c.attribution_oid == attribution_oid)
            .values(review_status="confirmed", modify_user=author, modify_date=func.now())
        )
        _history.insert_manual_event(
            c,
            source,
            source_id,
            kind="review_confirm",
            params={"attribution_oid": attribution_oid, "confirmed_fields": fields},
            attributions=_snapshot_current(c, source, source_id),
            author=author,
            reason=note,
        )
        return {"attribution": _dto_of(c, attribution_oid), "confirmed_fields": fields}


def swap_attribution_slots(
    source: str,
    source_id: str,
    *,
    oid_a: int,
    oid_b: int,
    reason: str,
    author: str,
) -> dict:
    """把同一則反饋的兩條歸因**互換 L1/L2 面向**（單一交易，兩條同時生效）。

    ## 為什麼需要專用函式而不是「改兩次」

    「AI 把兩個面向的內容寫反了」在逐條提交下是死結：先改 A 會撞上 B 佔著的面向、先改 B 同理，
    而 `_assert_slot_free` 連 tombstone 都算佔用。使用者只能走「先把 A 改成第三個暫時面向 →
    改 B → 再把 A 改回來」的三步，中間態是假資料、還會在事件流留下兩筆沒有意義的糾正紀錄。

    ## 為什麼做得到

    `idx_attribution_tbl_unique01` 自 2026-08-07 起是 **DEFERRABLE 唯一約束**（migration
    `a3e58d21c9f4`），本函式在交易內 `SET CONSTRAINTS ... DEFERRED` 把檢查延到 commit——中途的
    暫時衝突合法，兩條 UPDATE 跑完才驗。**不需要塞暫存假值繞路**，而且任意多列重排都適用。

    ## 只允許兩條存活列互換

    tombstone 不參與：它身上帶著「這條被判為 AI 誤判」的 `correction_reason`，搬到別的面向之後
    那個理由會變成謊言（它指的是舊面向）。要換就先還原，語義才乾淨。

    Args:
        oid_a: 其中一條歸因的流水號。
        oid_b: 另一條（須為同一則反饋、且與 A 不同）。
        reason: 互換理由（必填，門檻同其他糾正操作）。
        author: 操作者（無 SSO 時為 system）。

    Returns:
        {"attributions": [兩條互換後的 DTO], "changed": {oid: {面向前後值}}}

    Raises:
        CorrectionError: not_found（跨反饋 / 不存在）｜invalid（同一條 / 未歸因）｜
            conflict（任一條是 tombstone）。
    """
    reason = validate_reason(reason)
    if oid_a == oid_b:
        raise CorrectionError("invalid", "互換需要兩條不同的歸因")

    jg = T.attributions
    with T.get_engine().begin() as c:
        a = _row_of(c, source, source_id, oid_a)
        b = _row_of(c, source, source_id, oid_b)
        for row in (a, b):
            if row["is_deleted"]:
                raise CorrectionError(
                    "conflict",
                    f"歸因 {row['attribution_oid']} 已標記為 AI 誤判，請先還原再互換"
                    "——它身上的誤判理由指的是舊面向，直接搬走會讓那句話變成謊言",
                )
            if not row["l2_code"]:
                raise CorrectionError(
                    "invalid", f"歸因 {row['attribution_oid']} 尚未歸因，無面向可換"
                )

        # 延後唯一性檢查到 commit：中途 A、B 會短暫共用同一個面向，per-statement 檢查會直接擋下。
        c.execute(sa_text("SET CONSTRAINTS idx_attribution_tbl_unique01 DEFERRED"))

        changed: dict[str, Any] = {}
        for src, dst in ((a, b), (b, a)):
            labels = _resolve_labels(dst["l1_code"], dst["l2_code"])
            c.execute(
                sa_update(jg)
                .where(jg.c.attribution_oid == src["attribution_oid"])
                .values(
                    **labels,
                    is_human_corrected=True,
                    review_status="corrected",
                    correction_reason=reason,
                    conf_tier=HUMAN_TIER,
                    conf_value=None,
                    conf_raw=None,
                    modify_user=author,
                    modify_date=func.now(),
                )
            )
            changed[str(src["attribution_oid"])] = {
                "l1_code": [src["l1_code"], dst["l1_code"]],
                "l2_code": [src["l2_code"], dst["l2_code"]],
            }

        _history.insert_manual_event(
            c,
            source,
            source_id,
            kind="correction",
            params={"op": "swap", "attribution_oids": [oid_a, oid_b], "changed": changed},
            attributions=_snapshot_current(c, source, source_id),
            author=author,
            reason=reason,
        )
        return {
            "attributions": [_dto_of(c, oid_a), _dto_of(c, oid_b)],
            "changed": changed,
        }
