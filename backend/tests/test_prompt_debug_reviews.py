"""Prompt 調試台人工評判案例庫：db CRUD → API 端點 → 權限。

案例＝「AI 這樣判、人認為正解是那樣」的一則證據，下游是 AI 定點改寫與回歸重跑
（見 docs/PRD-PROMPT-REVIEW-REVISE.md）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core import db

# permissions_cfg / as_user fixtures 定義於 conftest.py（跨測試檔共用）。

_AI_OUTPUT = {
    "L2": "取消政策本身僵化",
    "L1": "[101]訂單取消",
    "L3": "規則就是不可退用戶不滿",
    "sentiment": "negative",
    "multi_issue_flag": True,
    "urgency": 4,
}
_CORRECTIONS = {
    "L2": "商品規格/使用規則事前確認",
    "L3": "方案規格描述不清",
    "multi_issue_flag": False,
}


# ─────────────────────────── db 層 ───────────────────────────
def test_review_crud_roundtrip(temp_db) -> None:
    """insert → list（摘要）→ fetch（全文）→ delete 全鏈路。"""
    assert db.list_prompt_debug_reviews() == []

    review_id = db.insert_prompt_debug_review(
        conversation="[USER] 沒選日期就結帳了，請幫我取消",
        ai_output=_AI_OUTPUT,
        corrections=_CORRECTIONS,
        comment="C06 的『一律本類』把事由被證實為誤解的情境也吸進來了",
        prompt_version="2026-07-28-104331",
        model="gpt-5.4-mini",
        reviewer="justin.xu@kkday.com",
    )
    assert review_id > 0

    rows = db.list_prompt_debug_reviews()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == review_id
    assert row["corrections"] == _CORRECTIONS
    assert row["ai_output"]["L2"] == "取消政策本身僵化"
    assert row["prompt_version"] == "2026-07-28-104331"
    assert row["reviewer"] == "justin.xu@kkday.com"
    # 列表只給預覽，不回全文欄
    assert "conversation" not in row
    assert row["conversation_chars"] == len("[USER] 沒選日期就結帳了，請幫我取消")

    full = db.fetch_prompt_debug_reviews([review_id])
    assert full[0]["conversation"] == "[USER] 沒選日期就結帳了，請幫我取消"

    assert db.delete_prompt_debug_review(review_id) is True
    assert db.delete_prompt_debug_review(review_id) is False
    assert db.list_prompt_debug_reviews() == []


def test_list_truncates_long_conversation_but_reports_true_length(temp_db) -> None:
    """對話動輒上萬字，列表只回前 200 字＋真實字數（全文走 fetch，見模組 docstring）。"""
    long_text = "客" * 5000
    db.insert_prompt_debug_review(long_text, _AI_OUTPUT, {})

    row = db.list_prompt_debug_reviews()[0]
    assert len(row["conversation_preview"]) == 200
    assert row["conversation_chars"] == 5000
    # 全對＝正例，corrections 為空但案例仍成立
    assert row["corrections"] == {}


def test_list_orders_newest_first_and_fetch_keeps_requested_order(temp_db) -> None:
    """列表新→舊；fetch 照傳入 id 順序回，找不到的 id 靜默略過（呼叫端已選好目標）。"""
    first = db.insert_prompt_debug_review("第一則", _AI_OUTPUT, {})
    second = db.insert_prompt_debug_review("第二則", _AI_OUTPUT, {})

    assert [r["id"] for r in db.list_prompt_debug_reviews()] == [second, first]
    assert [r["conversation"] for r in db.fetch_prompt_debug_reviews([first, second])] == [
        "第一則",
        "第二則",
    ]
    assert db.fetch_prompt_debug_reviews([second, 999_999]) == [
        r for r in db.fetch_prompt_debug_reviews([second])
    ]
    assert db.fetch_prompt_debug_reviews([]) == []


# ─────────────────────────── API 端點 ───────────────────────────
def test_review_api_full_loop(temp_db, permissions_cfg, as_user) -> None:
    """POST → GET → DELETE 閉環；reviewer 由後端從當前身分填，不吃前端傳的值。"""
    from app.api.main import app

    as_user("boss@kkday.com")  # grants("*")
    with TestClient(app) as client:
        r = client.post(
            "/api/v1/prejudge/prompt-debug/reviews",
            json={
                "conversation": "[USER] 我要取消",
                "ai_output": _AI_OUTPUT,
                "corrections": _CORRECTIONS,
                "comment": "決勝軸寫壞了",
                "prompt_version": "2026-07-28-104331",
                "model": "gpt-5.4-mini",
            },
        )
        assert r.status_code == 201
        review_id = r.json()["id"]

        r = client.get("/api/v1/prejudge/prompt-debug/reviews")
        assert r.status_code == 200
        reviews = r.json()["reviews"]
        assert len(reviews) == 1
        assert reviews[0]["id"] == review_id
        assert reviews[0]["comment"] == "決勝軸寫壞了"
        assert reviews[0]["reviewer"] == "boss@kkday.com"

        assert (
            client.delete(f"/api/v1/prejudge/prompt-debug/reviews/{review_id}").status_code == 200
        )
        assert (
            client.delete(f"/api/v1/prejudge/prompt-debug/reviews/{review_id}").status_code == 404
        )


def test_review_api_rejects_unknown_correction_field(temp_db, permissions_cfg, as_user) -> None:
    """corrections 的鍵必須是輸出契約真有的欄位——打錯字會讓案例回歸時永遠對不上，寧可當場擋掉。"""
    from app.api.main import app

    as_user("boss@kkday.com")
    with TestClient(app) as client:
        r = client.post(
            "/api/v1/prejudge/prompt-debug/reviews",
            json={
                "conversation": "x",
                "ai_output": _AI_OUTPUT,
                "corrections": {"catgeory": "打錯字了"},
            },
        )
        assert r.status_code == 400
        assert "catgeory" in r.json()["detail"]
        assert db.list_prompt_debug_reviews() == []


def test_review_api_roundtrips_confirmed_fields(temp_db, permissions_cfg, as_user) -> None:
    """confirmed＝人明確標「對」的欄，回歸時當「不准變」的判準，必須原樣存回。"""
    from app.api.main import app

    as_user("boss@kkday.com")
    with TestClient(app) as client:
        r = client.post(
            "/api/v1/prejudge/prompt-debug/reviews",
            json={
                "conversation": "x",
                "ai_output": _AI_OUTPUT,
                "corrections": {"L2": "改成這個"},
                "confirmed": ["sentiment", "urgency"],
            },
        )
        assert r.status_code == 201
        assert db.list_prompt_debug_reviews()[0]["confirmed"] == ["sentiment", "urgency"]


def test_review_api_rejects_field_marked_both_right_and_wrong(
    temp_db, permissions_cfg, as_user
) -> None:
    """同一欄不能既在 corrections 又在 confirmed——回歸時會自相矛盾，當場擋掉。"""
    from app.api.main import app

    as_user("boss@kkday.com")
    with TestClient(app) as client:
        r = client.post(
            "/api/v1/prejudge/prompt-debug/reviews",
            json={
                "conversation": "x",
                "ai_output": _AI_OUTPUT,
                "corrections": {"L2": "改成這個"},
                "confirmed": ["L2"],
            },
        )
        assert r.status_code == 400 and "L2" in r.json()["detail"]
        assert db.list_prompt_debug_reviews() == []


def test_review_api_accepts_every_contract_field(temp_db, permissions_cfg, as_user) -> None:
    """欄位白名單以輸出契約為準：14 欄全標錯也要收得下（避免白名單漏欄反而擋掉合法評判）。"""
    from app.api.main import app
    from app.judge import prompt_debug

    all_keys = {field["key"]: "改成這個" for field in prompt_debug.OUTPUT_FIELDS}
    as_user("boss@kkday.com")
    with TestClient(app) as client:
        r = client.post(
            "/api/v1/prejudge/prompt-debug/reviews",
            json={"conversation": "x", "ai_output": _AI_OUTPUT, "corrections": all_keys},
        )
        assert r.status_code == 201
        assert set(db.list_prompt_debug_reviews()[0]["corrections"]) == set(all_keys)
