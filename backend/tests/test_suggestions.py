"""重新初判的兩種託管分支，與待審建議的採納／駁回。

**最重要的斷言在 `test_ai_managed_rejudge_behaviour_unchanged`**：沒有人工介入過的反饋，重新初判
必須與人工介入功能上線前**逐欄相同**。整套人工託管機制的安全邊際就建立在這條上——既有 6,321 列
migration 後全是 AI 託管，只要這條成立，上線就不會動到任何現有資料的行為。

人工託管分支要證明的是：`attribution_tbl` **一列都不動**（人工值即現值），AI 的新結論全部降級為
待審建議。這是讓人敢改的前提——2026-08-04 退役的人工判決軸就是死在「改了也會被下次批量蓋掉」。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import db
from app.core.db import tables as T
from app.core.schema import TicketFinding
from tests._factories import review_row

_SRC = "reviews"
_REASON = "AI 把出發時間誤解為集合時間"


@pytest.fixture
def client(temp_db):
    import app.api.main as m

    return TestClient(m.app)


def _finding(rec_oid: str, l2: str = "C-1-1", polarity: str = "negative", score: int = 2):
    return TicketFinding(
        ticket_id=rec_oid,
        recommended_action="no_action",
        l1_domain_code="content",
        l1_label="商品內容",
        l2_code=l2,
        l2_label=l2,
        polarity=polarity,
        sentiment_score=score,
        # 真實引擎一定會派生 stage；不設的話比對欄會假性不等（'' vs 'judged'）
        prejudge_stage="judged",
        summary={"zh-tw": "頁面資訊與現場不符"},
        model_used="gpt-5-mini",
    )


def _rejudge(rec_oid: str, findings: list[TicketFinding], job_id: str = "pj_t") -> dict:
    return db.replace_source_findings(
        _SRC, rec_oid, findings, params={"model": "gpt-5-mini"}, job_id=job_id
    )


def _seed(rec_oid: str, findings: list[TicketFinding] | None = None) -> list[dict]:
    db.insert_source_batch(_SRC, [review_row(rec_oid)])
    _rejudge(rec_oid, findings if findings is not None else [_finding(rec_oid)])
    return _rows(rec_oid)


def _rows(rec_oid: str) -> list[dict]:
    jg = T.attributions
    with T.get_engine().connect() as c:
        return [
            dict(r)
            for r in c.execute(
                jg.select().where(jg.c.source_id == rec_oid).order_by(jg.c.attribution_oid)
            )
            .mappings()
            .all()
        ]


def _correct(client, rec_oid: str, oid: int, changes: dict) -> None:
    r = client.post(
        "/api/attributions/correct",
        json={
            "source": _SRC,
            "source_id": rec_oid,
            "attribution_oid": oid,
            "changes": changes,
            "reason": _REASON,
        },
    )
    assert r.status_code == 200, r.text


# ── AI 託管：行為必須與改動前完全相同 ────────────────────────────────────────


def test_ai_managed_rejudge_behaviour_unchanged() -> None:
    """沒被人工碰過的反饋：重新初判仍是整組替換，且不產生任何建議。"""
    rows = _seed("A1", [_finding("A1", l2="C-1-1")])
    assert len(rows) == 1

    out = _rejudge("A1", [_finding("A1", l2="C-1-2")])
    assert out == {"mode": "replace", "written": 1, "suggested": 0}

    after = _rows("A1")
    assert len(after) == 1 and after[0]["l2_code"] == "C-1-2", "AI 託管必須整組替換"
    assert after[0]["attribution_oid"] != rows[0]["attribution_oid"], (
        "舊列刪除、新列插入（既有語義）"
    )
    assert db.list_pending_suggestions(_SRC, "A1")["items"] == []


def test_ai_managed_empty_result_still_replaces() -> None:
    """空結果也是整組替換（歸因被清空），不是轉建議。"""
    _seed("A2")
    out = _rejudge("A2", [])
    assert out["mode"] == "replace" and out["written"] == 0
    assert _rows("A2") == []


# ── 人工託管：現值一列都不動，AI 結論全轉建議 ──────────────────────────────


def test_human_managed_rejudge_writes_no_attribution_row(client) -> None:
    """人工託管後重新初判：`attribution_tbl` 零變動，AI 結論轉為建議。"""
    (row,) = _seed("H1", [_finding("H1", l2="C-1-1")])
    _correct(client, "H1", row["attribution_oid"], {"l2_code": "C-3-1"})
    before = _rows("H1")

    out = _rejudge("H1", [_finding("H1", l2="C-1-1")])
    assert out["mode"] == "suggest" and out["suggested"] >= 1

    assert _rows("H1") == before, "人工託管下 attribution_tbl 必須一列都不動"
    sug = db.list_pending_suggestions(_SRC, "H1")
    assert sug["items"], "AI 的新結論必須留在建議層讓人看得到"
    assert sug["batch_id"] and sug["model"] == "gpt-5-mini"


def test_confirm_alone_latches_human_managed(client) -> None:
    """**只按「確認正確」也算人工介入**——這是 2026-08-07 補上的漏洞。

    在此之前 `confirm_attribution` 只寫 `review_status='confirmed'`，沒設任何
    `human_touched_cond()` 看得到的旗標。於是該反饋仍是 AI 託管，下次重新初判整組 DELETE
    → 那一列連同複審記錄一起消失，**複審做完等於白做，而且沒有任何錯誤訊息**。
    """
    (row,) = _seed("H9", [_finding("H9", l2="C-1-1")])
    oid = row["attribution_oid"]

    r = client.post(
        "/api/attributions/confirm",
        json={"source": _SRC, "source_id": "H9", "attribution_oid": oid},
    )
    assert r.status_code == 200, r.text

    before = _rows("H9")
    assert before[0]["review_status"] == "confirmed"

    out = _rejudge("H9", [_finding("H9", l2="C-1-2")])
    assert out["mode"] == "suggest", (
        "確認過的反饋仍走 AI 託管的整組替換分支——review_status='confirmed' 沒進 human_touched_cond()"
    )
    assert _rows("H9") == before, "確認過的列被重新初判動到了"
    assert _rows("H9")[0]["attribution_oid"] == oid, "attribution_oid 變了＝該列被刪後重插"
    assert db.list_pending_suggestions(_SRC, "H9")["items"], "AI 的新結論要留在建議層"


def test_confirmed_feedback_shows_in_human_state_filter(client) -> None:
    """確認算人工介入的連鎖：`human_state` 篩選必須看得到它。

    `corrected` 這個 code 的前端 label 是「已人工介入」（非「已修改」），語義上本就涵蓋複審確認，
    故不需要為此新增第四種篩選值。
    """
    (row,) = _seed("H10")
    _seed("H11")
    client.post(
        "/api/attributions/confirm",
        json={"source": _SRC, "source_id": "H10", "attribution_oid": row["attribution_oid"]},
    )

    touched = [
        r["source_id"] for r in db.list_problems(source=_SRC, human_state="corrected")["rows"]
    ]
    assert touched == ["H10"], "確認過的反饋沒出現在「已人工介入」篩選中"
    ai_only = [r["source_id"] for r in db.list_problems(source=_SRC, human_state="ai_only")["rows"]]
    assert ai_only == ["H11"], "確認過的反饋仍被當成「AI 原判」"


def test_three_change_types(client) -> None:
    """三種建議型別：同面向值有異＝replace、AI 新發現＝add、AI 不再提＝remove。"""
    rows = _seed("H2", [_finding("H2", l2="C-1-1"), _finding("H2", l2="C-1-2")])
    _correct(client, "H2", rows[0]["attribution_oid"], {"sentiment_score": 1})

    # 新結果：C-1-1 傾向改了（replace）、C-1-2 不再提（remove）、C-2-1 新發現（add）
    _rejudge(
        "H2",
        [
            _finding("H2", l2="C-1-1", polarity="neutral", score=3),
            _finding("H2", l2="C-2-1"),
        ],
    )
    kinds = {i["change_type"] for i in db.list_pending_suggestions(_SRC, "H2")["items"]}
    assert kinds == {"replace", "add", "remove"}


def test_identical_result_produces_no_suggestion(client) -> None:
    """AI 判出與現值相同的結果 → 不產生建議項（否則徽記天天亮，就沒人看了）。"""
    (row,) = _seed("H3", [_finding("H3", l2="C-1-1")])
    # 只改情緒分，讓 AI 的原始結果與現值在比對欄上仍有差異以外的欄相同
    _correct(client, "H3", row["attribution_oid"], {"sentiment_score": 1})
    cur = _rows("H3")[0]
    same = _finding("H3", l2=cur["l2_code"], polarity=cur["polarity"], score=cur["sentiment_score"])
    _rejudge("H3", [same])
    replaces = [
        i for i in db.list_pending_suggestions(_SRC, "H3")["items"] if i["change_type"] == "replace"
    ]
    assert not replaces, "比對欄全等時不該產生 replace 建議"


def test_rejudge_twice_is_idempotent(client) -> None:
    """連跑兩次：舊 pending 先清光再插新的，不會堆疊。"""
    (row,) = _seed("H4", [_finding("H4", l2="C-1-1")])
    _correct(client, "H4", row["attribution_oid"], {"l2_code": "C-3-1"})
    _rejudge("H4", [_finding("H4", l2="C-1-1")], job_id="pj_a")
    first = db.list_pending_suggestions(_SRC, "H4")
    _rejudge("H4", [_finding("H4", l2="C-1-1")], job_id="pj_b")
    second = db.list_pending_suggestions(_SRC, "H4")
    assert len(second["items"]) == len(first["items"]), "重跑不得讓建議堆疊"
    assert second["batch_id"] != first["batch_id"]


def test_tombstoned_slot_reappearing_is_surfaced(client) -> None:
    """AI 又判出人工已標記為誤判的面向 → 必須產生建議（這是最該讓人看到的一種）。"""
    (row,) = _seed("H5", [_finding("H5", l2="C-1-1")])
    client.post(
        "/api/attributions/delete",
        json={
            "source": _SRC,
            "source_id": "H5",
            "attribution_oid": row["attribution_oid"],
            "reason": _REASON,
        },
    )
    _rejudge("H5", [_finding("H5", l2="C-1-1")])
    items = db.list_pending_suggestions(_SRC, "H5")["items"]
    assert items and items[0]["change_type"] == "add"


# ── 採納 / 駁回 ──────────────────────────────────────────────────────────────


def test_accept_replace_keeps_human_managed_latch(client) -> None:
    """採納 replace：現值更新，但**人工託管閂鎖保持**（否則下次重判又會靜默覆蓋）。"""
    (row,) = _seed("R1", [_finding("R1", l2="C-1-1")])
    _correct(client, "R1", row["attribution_oid"], {"sentiment_score": 1})
    _rejudge("R1", [_finding("R1", l2="C-1-1", polarity="neutral", score=3)])
    sug = db.list_pending_suggestions(_SRC, "R1")
    item = next(i for i in sug["items"] if i["change_type"] == "replace")

    r = client.post(
        "/api/attribution-suggestions/resolve",
        json={
            "source": _SRC,
            "source_id": "R1",
            "batch_id": sug["batch_id"],
            "decisions": [{"suggestion_oid": item["suggestion_oid"], "decision": "accept"}],
        },
    )
    assert r.status_code == 200 and r.json()["applied"] == 1

    after = _rows("R1")[0]
    assert after["polarity"] == "neutral" and after["sentiment_score"] == 3
    assert after["is_human_corrected"] is True, "採納 AI 建議不得解除人工託管閂鎖"
    assert db.list_pending_suggestions(_SRC, "R1")["items"] == [], "處理過的建議要消失"


def test_reject_leaves_current_value_untouched(client) -> None:
    """駁回：現值一個字都不動，建議消失。"""
    (row,) = _seed("R2", [_finding("R2", l2="C-1-1")])
    _correct(client, "R2", row["attribution_oid"], {"sentiment_score": 1})
    _rejudge("R2", [_finding("R2", l2="C-1-1", polarity="neutral", score=3)])
    sug = db.list_pending_suggestions(_SRC, "R2")
    before = _rows("R2")

    r = client.post(
        "/api/attribution-suggestions/resolve",
        json={
            "source": _SRC,
            "source_id": "R2",
            "batch_id": sug["batch_id"],
            "decisions": [
                {"suggestion_oid": i["suggestion_oid"], "decision": "reject"} for i in sug["items"]
            ],
            "reason": "AI 仍未讀到退款對話",
        },
    )
    assert r.status_code == 200 and r.json()["rejected"] == len(sug["items"])
    assert _rows("R2") == before
    assert db.list_pending_suggestions(_SRC, "R2")["items"] == []


def test_stale_batch_is_409(client) -> None:
    """batch 過期（期間又跑了一次重判）→ 409，要求重新載入而不是覆蓋新結果。"""
    (row,) = _seed("R3", [_finding("R3", l2="C-1-1")])
    _correct(client, "R3", row["attribution_oid"], {"sentiment_score": 1})
    _rejudge("R3", [_finding("R3", polarity="neutral", score=3)], job_id="pj_old")
    old = db.list_pending_suggestions(_SRC, "R3")
    _rejudge("R3", [_finding("R3", polarity="positive", score=5)], job_id="pj_new")
    fresh = db.list_pending_suggestions(_SRC, "R3")

    r = client.post(
        "/api/attribution-suggestions/resolve",
        json={
            "source": _SRC,
            "source_id": "R3",
            "batch_id": old["batch_id"],
            "decisions": [
                {"suggestion_oid": fresh["items"][0]["suggestion_oid"], "decision": "accept"}
            ],
        },
    )
    assert r.status_code == 409


# ── 列表整合 ────────────────────────────────────────────────────────────────


def test_list_exposes_suggestion_count_and_human_state_filter(client) -> None:
    """列表：每列帶 suggestion_count；human_state 三態可篩。"""
    (row,) = _seed("L1", [_finding("L1", l2="C-1-1")])
    _seed("L2", [_finding("L2", l2="C-1-1")])
    _correct(client, "L1", row["attribution_oid"], {"sentiment_score": 1})
    _rejudge("L1", [_finding("L1", polarity="neutral", score=3)])

    rows = {r["source_id"]: r for r in db.list_problems(source=_SRC)["rows"]}
    assert rows["L1"]["suggestion_count"] >= 1
    assert rows["L2"]["suggestion_count"] == 0

    corrected = db.list_problems(source=_SRC, human_state="corrected")
    assert [r["source_id"] for r in corrected["rows"]] == ["L1"]
    ai_only = db.list_problems(source=_SRC, human_state="ai_only")
    assert [r["source_id"] for r in ai_only["rows"]] == ["L2"]
    suggested = db.list_problems(source=_SRC, human_state="suggested")
    assert [r["source_id"] for r in suggested["rows"]] == ["L1"]
