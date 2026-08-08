"""人工糾正歸因：四種操作的落庫值、事件形狀，與全部 4xx 驗證路徑。

這是 `attribution_tbl` 有史以來第一條人工寫入路徑，而它同時是「人工託管」狀態的入口——一旦寫過，
該則反饋的重新初判行為就改變了（不再覆蓋現值）。所以除了驗每個動作寫對值，也要鎖住兩件事：

1. **理由必填**：這是避免重蹈 2026-08-04 人工判決軸覆轍的設計核心（舊版只有兩顆沒有資訊量的
   按鈕，6,242 條裡只有 1 個人按過），不是形式主義
2. **自然鍵衝突擋在 API 層**：若讓它落到 DB 才炸，會是 UniqueViolation → 500，而不是可讀的 409
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import db
from app.core.db import tables as T
from app.core.schema import TicketFinding
from tests._factories import review_row

_SRC = "reviews"
_REASON = "AI 把出發時間誤解為集合時間，實際文意是集合"


@pytest.fixture
def client(temp_db):
    """TestClient（綁 temp_db 隔離庫）。"""
    import app.api.main as m

    return TestClient(m.app)


def _finding(rec_oid: str, l1: str = "content", l2: str = "C-1-1") -> TicketFinding:
    return TicketFinding(
        ticket_id=rec_oid,
        recommended_action="no_action",
        l1_domain_code=l1,
        l1_label=l1,
        l2_code=l2,
        l2_label=l2,
        polarity="negative",
        sentiment_score=2,
        summary={"zh-tw": "頁面資訊與現場不符"},
        model_used="gpt-5-mini",
    )


def _seed(rec_oid: str, findings: list[TicketFinding] | None = None) -> list[int]:
    """建一則反饋 + 歸因，回 attribution_oid 清單。"""
    db.insert_source_batch(_SRC, [review_row(rec_oid)])
    db.replace_source_findings(
        _SRC,
        rec_oid,
        findings if findings is not None else [_finding(rec_oid)],
        params={"model": "gpt-5-mini"},
        job_id="pj_test",
    )
    jg = T.attributions
    with T.get_engine().connect() as c:
        return [
            r[0]
            for r in c.execute(
                jg.select().with_only_columns(jg.c.attribution_oid).where(jg.c.source_id == rec_oid)
            )
        ]


def _row(oid: int) -> dict:
    jg = T.attributions
    with T.get_engine().connect() as c:
        return dict(c.execute(jg.select().where(jg.c.attribution_oid == oid)).mappings().first())


def _events(rec_oid: str, kind: str) -> list[dict]:
    return [e for e in db.list_attribution_history(_SRC, rec_oid) if e["kind"] == kind]


# ── 四種操作的落庫值 ──────────────────────────────────────────────────────────


def test_correct_writes_human_values_and_event(client) -> None:
    """改分類：值改對、標記人工託管、信心改 human 分層、事件記下欄位級 delta。"""
    (oid,) = _seed("C1")
    r = client.post(
        "/api/attributions/correct",
        json={
            "source": _SRC,
            "source_id": "C1",
            "attribution_oid": oid,
            "changes": {"l2_code": "C-3-1"},
            "reason": _REASON,
        },
    )
    assert r.status_code == 200, r.text

    row = _row(oid)
    assert row["l2_code"] == "C-3-1"
    assert row["l1_code"] != "content", "L1 應由 L2 推導，不該還停在舊域"
    assert row["l1_label"] and row["l2_label"], "label 必須由服務端解析填上"
    assert row["is_human_corrected"] is True
    assert row["review_status"] == "corrected"
    assert row["correction_reason"] == _REASON
    # 信心：分層改 human 而非 NULL（否則人工列會從分層篩選與 by_tier 聚合整批消失）
    assert row["conf_tier"] == "human"
    assert row["conf_value"] is None and row["conf_raw"] is None

    # DTO 的顯示來源改為人工
    assert r.json()["attribution"]["origin"] == "human"

    (ev,) = _events("C1", "correction")
    assert ev["params"]["op"] == "update"
    assert ev["params"]["changed"]["l2_code"] == ["C-1-1", "C-3-1"], "欄位級 delta＝金標回餵的判準"
    assert ev["content"] == _REASON, "理由存 typed 欄，要能被搜尋/導出"
    assert ev["attributions"], "事件須帶動作後的完整現值快照（前端 diff 邏輯共用）"


def test_create_adds_manual_attribution(client) -> None:
    """人工新增 AI 漏掉的歸因：標 is_manual_created、無 model、不自動採納。"""
    _seed("C2")
    r = client.post(
        "/api/attributions/create",
        json={
            "source": _SRC,
            "source_id": "C2",
            "values": {
                "l2_code": "C-3-1",
                "sentiment_score": 1,
                "summary": "現場退款糾紛，AI 完全沒判到",
            },
            "reason": _REASON,
        },
    )
    assert r.status_code == 200, r.text
    oid = r.json()["attribution"]["attribution_oid"]
    row = _row(oid)
    assert row["is_manual_created"] is True
    assert row["model"] is None, "人工新增列沒有初判模型"
    assert row["is_auto_accepted"] is False
    assert row["summary"] == {"zh-tw": "現場退款糾紛，AI 完全沒判到"}
    assert row["polarity"] == "negative", "新增時傾向同樣由情緒分派生"
    assert _events("C2", "correction")[0]["params"]["op"] == "create"


def test_delete_is_tombstone_not_hard_delete(client) -> None:
    """標記誤判＝tombstone：列還在（佔住自然鍵防復活），**歸因**讀取層看不到。

    ⚠️ 這裡刻意分開斷言兩個不同的不變式（2026-08-07 收斂）：
    - **歸因層**：那條歸因不得出現在任何列表／統計 → `attributions == []`
    - **反饋層**：這則反饋仍然「判過」→ 仍出現在 `judged=True`，且不會被當成未初判
      （若這裡回到 `judged=False`，就會與 `prejudge_targets` 的口徑打架，
      使用者會看到「篩出未初判 1 筆、按下去目標數 0 筆」）
    """
    (oid,) = _seed("C3")
    r = client.post(
        "/api/attributions/delete",
        json={"source": _SRC, "source_id": "C3", "attribution_oid": oid, "reason": _REASON},
    )
    assert r.status_code == 200, r.text
    assert _row(oid)["is_deleted"] is True, "必須保留列，硬刪會讓重新初判把它悄悄復活"

    res = db.list_problems(source=_SRC, judged=True)
    assert res["total"] == 1, "反饋層：標記誤判不改變「判過」這件事"
    (row,) = res["rows"]
    assert row["attributions"] == [], "歸因層：tombstone 的歸因不得出現在列表"
    assert row["judge_state"] == "dismissed"
    assert row["dismissed_count"] == 1
    assert db.list_problems(source=_SRC, judged=False)["total"] == 0, (
        "歸因全被標記誤判的反饋被當成「未初判」——會被批量初判無限重撈"
    )


def test_restore_undoes_tombstone(client) -> None:
    """還原：撤銷 tombstone 後列表又看得到。"""
    (oid,) = _seed("C4")
    body = {"source": _SRC, "source_id": "C4", "attribution_oid": oid, "reason": _REASON}
    client.post("/api/attributions/delete", json=body)
    r = client.post("/api/attributions/restore", json=body)
    assert r.status_code == 200, r.text
    assert _row(oid)["is_deleted"] is False
    res = db.list_problems(source=_SRC, judged=True)
    assert res["total"] == 1
    assert len(res["rows"][0]["attributions"]) == 1, "還原後歸因要回到列表上"
    assert res["rows"][0]["judge_state"] == "judged"


def test_confirm_marks_reviewed_via_review_status(client) -> None:
    """複審確認：標 `review_status='confirmed'`，**不動三個 is_ 旗標**。

    ⚠️ 這不代表「不進人工託管」——2026-08-07 起 `human_touched_cond()` 也看
    `review_status == 'confirmed'`，所以確認過的反饋一樣受保護（重新初判走待審建議而非整組覆蓋，
    見 test_suggestions.test_confirm_alone_latches_human_managed）。此處鎖的是**用哪個欄位表達**：
    確認不是修改，不該把 `is_human_corrected` 染成 true，否則導出與 UI 會把它顯示成「人工改過」。
    """
    (oid,) = _seed("C5")
    r = client.post(
        "/api/attributions/confirm",
        json={
            "source": _SRC,
            "source_id": "C5",
            "attribution_oid": oid,
            "confirmed_fields": ["l1_code", "l2_code"],
        },
    )
    assert r.status_code == 200, r.text
    row = _row(oid)
    assert row["review_status"] == "confirmed"
    assert row["is_human_corrected"] is False, "確認不是修改，不得把該列顯示成「人工改過」"
    assert row["is_manual_created"] is False and row["is_deleted"] is False
    (ev,) = _events("C5", "review_confirm")
    assert ev["params"]["confirmed_fields"] == ["l1_code", "l2_code"]


# ── 驗證路徑（每條都是可讀的 4xx，不是讓 DB 拋 500）──────────────────────────


@pytest.mark.parametrize("reason", ["", "   ", "錯"])
def test_reason_is_mandatory(client, reason) -> None:
    """理由空白或過短一律 422——這是設計核心，不是形式主義。"""
    (oid,) = _seed("V1")
    r = client.post(
        "/api/attributions/correct",
        json={
            "source": _SRC,
            "source_id": "V1",
            "attribution_oid": oid,
            "changes": {"sentiment_score": 3},
            "reason": reason,
        },
    )
    assert r.status_code == 422 and "理由" in r.json()["detail"]


def test_cross_feedback_oid_is_404(client) -> None:
    """拿別則反饋的 attribution_oid 來改 → 404（擋跨反饋越權改值）。"""
    (oid_a,) = _seed("V2a")
    _seed("V2b")
    r = client.post(
        "/api/attributions/correct",
        json={
            "source": _SRC,
            "source_id": "V2b",
            "attribution_oid": oid_a,
            "changes": {"sentiment_score": 3},
            "reason": _REASON,
        },
    )
    assert r.status_code == 404


def test_slot_conflict_is_409_not_500(client) -> None:
    """改成同反饋已存在的面向 → 409。落到 DB 才炸的話會是 UniqueViolation → 500。"""
    oids = _seed("V3", [_finding("V3", l2="C-1-1"), _finding("V3", l2="C-1-2")])
    r = client.post(
        "/api/attributions/correct",
        json={
            "source": _SRC,
            "source_id": "V3",
            "attribution_oid": oids[0],
            "changes": {"l2_code": "C-1-2"},
            "reason": _REASON,
        },
    )
    assert r.status_code == 409 and "已有該面向" in r.json()["detail"]


def test_conflict_with_tombstone_guides_to_restore(client) -> None:
    """撞到已被標記誤判的面向 → 409，且訊息要指路「請先還原」。"""
    oids = _seed("V4", [_finding("V4", l2="C-1-1"), _finding("V4", l2="C-1-2")])
    client.post(
        "/api/attributions/delete",
        json={"source": _SRC, "source_id": "V4", "attribution_oid": oids[1], "reason": _REASON},
    )
    r = client.post(
        "/api/attributions/correct",
        json={
            "source": _SRC,
            "source_id": "V4",
            "attribution_oid": oids[0],
            "changes": {"l2_code": "C-1-2"},
            "reason": _REASON,
        },
    )
    assert r.status_code == 409 and "還原" in r.json()["detail"]


def test_unknown_taxonomy_code_is_422(client) -> None:
    """分類 code 不在分類體系內 → 422。"""
    (oid,) = _seed("V5")
    r = client.post(
        "/api/attributions/correct",
        json={
            "source": _SRC,
            "source_id": "V5",
            "attribution_oid": oid,
            "changes": {"l2_code": "C-9-9"},
            "reason": _REASON,
        },
    )
    assert r.status_code == 422 and "分類體系" in r.json()["detail"]


def test_non_editable_field_is_rejected(client) -> None:
    """改 evidence 一律擋下——它是 grounding 錨點，人改過就分不清哪些是原文。"""
    (oid,) = _seed("V6")
    r = client.post(
        "/api/attributions/correct",
        json={
            "source": _SRC,
            "source_id": "V6",
            "attribution_oid": oid,
            "changes": {"evidence": "我自己寫的佐證"},
            "reason": _REASON,
        },
    )
    assert r.status_code == 422 and "不開放人工修改" in r.json()["detail"]


@pytest.mark.parametrize(
    ("score", "polarity"),
    [(1, "negative"), (2, "negative"), (3, "neutral"), (4, "positive"), (5, "positive")],
)
def test_polarity_is_derived_from_sentiment(client, score, polarity) -> None:
    """傾向由情緒分**派生**，不是另外選的——「正向＋情緒分 1」這種矛盾組合在構造上就不可能。"""
    (oid,) = _seed(f"V7{score}")
    r = client.post(
        "/api/attributions/correct",
        json={
            "source": _SRC,
            "source_id": f"V7{score}",
            "attribution_oid": oid,
            "changes": {"sentiment_score": score},
            "reason": _REASON,
        },
    )
    assert r.status_code == 200, r.text
    assert _row(oid)["polarity"] == polarity
    assert r.json()["attribution"]["polarity"] == polarity


def test_polarity_cannot_be_sent_directly(client) -> None:
    """直接送 polarity 一律擋下——它是派生值，開放它等於允許與情緒分不一致。"""
    (oid,) = _seed("V7x")
    r = client.post(
        "/api/attributions/correct",
        json={
            "source": _SRC,
            "source_id": "V7x",
            "attribution_oid": oid,
            "changes": {"polarity": "positive"},
            "reason": _REASON,
        },
    )
    assert r.status_code == 422 and "不開放人工修改" in r.json()["detail"]


@pytest.mark.parametrize("score", [0, 6, "3", 3.5, None])
def test_invalid_sentiment_is_422(client, score) -> None:
    """情緒分超出 1-5 或非整數 → 422（不讓壞值落庫再從畫面上看出問題）。"""
    (oid,) = _seed(f"V7i{score}")
    r = client.post(
        "/api/attributions/correct",
        json={
            "source": _SRC,
            "source_id": f"V7i{score}",
            "attribution_oid": oid,
            "changes": {"sentiment_score": score},
            "reason": _REASON,
        },
    )
    assert r.status_code == 422


def test_correct_on_tombstone_is_409(client) -> None:
    """已標記誤判的列不能直接改，要先還原（避免「改了一條已刪的東西」這種模糊狀態）。"""
    (oid,) = _seed("V8")
    body = {"source": _SRC, "source_id": "V8", "attribution_oid": oid, "reason": _REASON}
    client.post("/api/attributions/delete", json=body)
    r = client.post("/api/attributions/correct", json={**body, "changes": {"sentiment_score": 3}})
    assert r.status_code == 409


def test_correction_policy_endpoint_matches_backend_whitelist(client) -> None:
    """政策端點與後端寫入白名單同源——前端據此決定表單，兩邊漂移會讓 UI 開放了寫不進去的欄。"""
    r = client.get("/api/attributions/correction-policy")
    assert r.status_code == 200
    assert r.json()["editable_fields"] == db.editable_fields()
    assert r.json()["reason_min_length"] >= 1


def test_natural_key_constraint_is_deferrable_and_blocks_upsert(temp_db) -> None:
    """自然鍵是 **DEFERRABLE 約束**，不是裸的 unique index——互換面向靠它，upsert 被它擋死。

    兩件事一起鎖住，因為它們是同一個決定的兩面（見 migration a3e58d21c9f4）：

    1. **可延後**：兩條歸因互換 L1/L2 面向時，任何順序都會在中途撞鍵。延後到 commit 檢查後，
       互換就是單一交易內兩次 UPDATE，不需要塞暫存假值繞路。
    2. **不能當 ON CONFLICT 的 arbiter**：這是 PG 對 deferrable 約束的限制，而我們**要**這個限制
       ——`replace_source_findings` 刻意是「整組刪除後重插」而非逐筆 upsert（逐筆會讓舊面向殘留
       孤兒列）。原本只寫在註解裡的約定，現在由 PG 強制。若有人日後把約束改回裸索引，這條會紅。
    """
    from sqlalchemy import text as sa_text

    with T.get_engine().connect() as c:
        deferrable = c.execute(
            sa_text(
                "SELECT condeferrable FROM pg_constraint "
                "WHERE conname = 'idx_attribution_tbl_unique01'"
            )
        ).scalar()
    assert deferrable is True, (
        "自然鍵不是 DEFERRABLE 約束——互換面向會退回「先塞暫存假值」的三步繞路"
    )

    (oid,) = _seed("V9")
    row = _row(oid)
    with pytest.raises(Exception) as exc:  # noqa: PT011 — PG 的錯誤型別經 SQLAlchemy 包裝
        with T.get_engine().begin() as c:
            c.execute(
                T.upsert(
                    T.attributions,
                    {
                        "source": _SRC,
                        "source_id": "V9",
                        "l1_code": row["l1_code"],
                        "l2_code": row["l2_code"],
                        "summary": "upsert 應該要失敗",
                    },
                    ["source", "source_id", "l1_code", "l2_code"],
                )
            )
    assert "deferrable" in str(exc.value).lower(), (
        f"upsert 沒有因為 deferrable 約束而失敗，實際錯誤：{exc.value}"
    )


def test_swap_slots_exchanges_two_live_attributions(client) -> None:
    """互換兩條歸因的面向：一次操作、兩條同時生效，且 `attribution_oid` 不變。

    這是逐條提交下唯一解不開的死結——先改哪一條都會撞上另一條佔著的面向。實作靠自然鍵已是
    DEFERRABLE 約束（migration a3e58d21c9f4），交易內延後檢查即可；**若有人把約束改回裸索引，
    這條會直接紅**（中途的暫時衝突會被 per-statement 檢查擋下）。
    """
    a_oid, b_oid = _seed("SW1", [_finding("SW1", l2="C-1-1"), _finding("SW1", l2="C-3-1")])
    before = {a_oid: _row(a_oid)["l2_code"], b_oid: _row(b_oid)["l2_code"]}
    assert before == {a_oid: "C-1-1", b_oid: "C-3-1"}

    r = client.post(
        "/api/attributions/swap",
        json={
            "source": _SRC,
            "source_id": "SW1",
            "attribution_oid_a": a_oid,
            "attribution_oid_b": b_oid,
            "reason": "AI 把兩個面向的內容寫反了",
        },
    )
    assert r.status_code == 200, r.text

    assert _row(a_oid)["l2_code"] == "C-3-1"
    assert _row(b_oid)["l2_code"] == "C-1-1"
    # oid 不變＝備註等綁面向的東西不會因互換而錯位；label 由服務端重解析，不沿用舊快照
    assert _row(a_oid)["l2_label"] and _row(a_oid)["l2_label"] != _row(b_oid)["l2_label"]
    assert _row(a_oid)["is_human_corrected"] is True
    assert _row(b_oid)["is_human_corrected"] is True

    (ev,) = _events("SW1", "correction")
    assert ev["params"]["op"] == "swap"
    assert sorted(ev["params"]["attribution_oids"]) == sorted([a_oid, b_oid])
    assert ev["params"]["changed"][str(a_oid)]["l2_code"] == ["C-1-1", "C-3-1"]


def test_swap_with_tombstone_is_409(client) -> None:
    """tombstone 不參與互換：它身上的誤判理由指的是舊面向，搬走會讓那句話變成謊言。"""
    a_oid, b_oid = _seed("SW2", [_finding("SW2", l2="C-1-1"), _finding("SW2", l2="C-3-1")])
    client.post(
        "/api/attributions/delete",
        json={"source": _SRC, "source_id": "SW2", "attribution_oid": b_oid, "reason": _REASON},
    )
    r = client.post(
        "/api/attributions/swap",
        json={
            "source": _SRC,
            "source_id": "SW2",
            "attribution_oid_a": a_oid,
            "attribution_oid_b": b_oid,
            "reason": "想把這兩條換過來",
        },
    )
    assert r.status_code == 409
    assert "還原" in r.json()["detail"], "訊息要指路（先還原），不能只說失敗"


def test_swap_same_attribution_is_422(client) -> None:
    """同一條跟自己換沒有意義，擋在 API 層。"""
    (oid,) = _seed("SW3")
    r = client.post(
        "/api/attributions/swap",
        json={
            "source": _SRC,
            "source_id": "SW3",
            "attribution_oid_a": oid,
            "attribution_oid_b": oid,
            "reason": "測試同一條互換",
        },
    )
    assert r.status_code == 422
