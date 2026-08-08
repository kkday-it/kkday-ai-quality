"""判決歸因值域主檔：播種冪等、三軸讀取、upsert 與排序，以及「停用不刪」的守則。

值域是**業務會調的參照資料**，而它的 `item_code` 會被歷史判決引用——所以這裡最該鎖住的不是
CRUD 本身，是「沒有刪除路徑」這件事：硬刪一個已被引用的 code，那些歷史列的顯示欄就永久變空白。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import db


@pytest.fixture
def client(temp_db):
    import app.api.main as m

    db.seed_dimensions_from_file()
    return TestClient(m.app)


def test_seed_is_idempotent(temp_db) -> None:
    """播種冪等：第二次跑不再新增任何列（啟動時每次都會呼叫）。"""
    first = db.seed_dimensions_from_file()
    assert first > 0, "種子檔應該要有內容"
    assert db.seed_dimensions_from_file() == 0


def test_axes_seeded_from_existing_literals(client) -> None:
    """四軸都有值，且嚴重度／建議行動的 code 與既有 Literal 同源（不憑空發明值域）。

    前三軸屬**判決歸因**（定責＋行動）；`note_type` 是**備註的互動類型**——語義與判決無關，
    但欄形完全相同，共用同一張判別式單表（見 tables.py 的表註解）。
    """
    from typing import get_args

    from app.core.schema import RecommendedAction, Severity

    dims = client.get("/api/attribution-dimensions").json()
    assert set(dims) == {"responsible_party", "severity", "verdict_action", "note_type"}
    assert {i["item_code"] for i in dims["severity"]} == set(get_args(Severity))
    assert {i["item_code"] for i in dims["verdict_action"]} == set(get_args(RecommendedAction))
    assert dims["responsible_party"], "責任方值域不得為空（六個域的 owner_role 全空，故用提案值）"
    assert "internal" in {i["item_code"] for i in dims["note_type"]}, (
        "備註類型至少要有 internal——add_note 的預設與舊 kind='note' 事件的搬遷值都是它"
    )


def test_save_upserts_and_reorder(client) -> None:
    """同 (軸, code) 再存一次＝更新而非重複；排序可整份重寫。"""
    body = {
        "dimension_code": "responsible_party",
        "item_code": "supplier",
        "item_label": "供應商（改名測試）",
        "sort_order": 9,
    }
    r = client.post("/api/attribution-dimensions/save", json=body)
    assert r.status_code == 200 and r.json()["item_label"] == "供應商（改名測試）"

    dims = client.get("/api/attribution-dimensions").json()
    supplier = [i for i in dims["responsible_party"] if i["item_code"] == "supplier"]
    assert len(supplier) == 1, "upsert 不得長出重複列"

    codes = [i["item_code"] for i in dims["responsible_party"]][::-1]
    r = client.post(
        "/api/attribution-dimensions/reorder",
        json={"dimension_code": "responsible_party", "item_codes": codes},
    )
    assert r.status_code == 200 and r.json()["updated"] == len(codes)
    after = [
        i["item_code"]
        for i in client.get("/api/attribution-dimensions").json()["responsible_party"]
    ]
    assert after == codes


def test_deactivate_hides_from_options_but_keeps_row(client) -> None:
    """停用＝從可選清單消失，但列還在（歷史判決仍解析得到 label）——這是「不刪」的具體意義。"""
    client.post(
        "/api/attribution-dimensions/save",
        json={
            "dimension_code": "severity",
            "item_code": "P3",
            "item_label": "P3 低",
            "is_active": False,
        },
    )
    active = client.get("/api/attribution-dimensions").json()["severity"]
    assert "P3" not in {i["item_code"] for i in active}

    all_items = client.get("/api/attribution-dimensions?include_inactive=true").json()["severity"]
    assert "P3" in {i["item_code"] for i in all_items}, "停用項必須留在庫裡供歷史判決解析"


def test_no_delete_endpoint_exists(client) -> None:
    """刻意沒有刪除端點——硬刪已被引用的 code 會讓歷史判決顯示空白。"""
    import app.api.main as m

    paths = {r.path for r in m.app.routes if hasattr(r, "path")}
    assert not any("dimension" in p and "delete" in p for p in paths)


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ({"dimension_code": "nope", "item_code": "x", "item_label": "X"}, "軸別不合法"),
        ({"dimension_code": "severity", "item_code": "  ", "item_label": "X"}, "code 空白"),
        ({"dimension_code": "severity", "item_code": "P9", "item_label": " "}, "label 空白"),
    ],
)
def test_save_validation(client, payload, why) -> None:
    """必填與值域校驗一律 422（不讓壞資料落庫再從畫面上看出問題）。"""
    assert client.post("/api/attribution-dimensions/save", json=payload).status_code == 422, why
