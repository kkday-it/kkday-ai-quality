"""上傳表頭 → DB 欄名映射的回歸：對不上的表頭必須被指出來，不得靜默落 NULL。

**背景（實際缺陷）**：`insert_source_batch` 原本以 `[c.name for c in tbl.columns]` 拿 DB 欄名去
`row.get(欄名)`，等於默認「CSV 表頭逐字等於 DB 欄名」。表頭一旦對不上（上游改欄名、我方改
schema、或單純拼錯），該欄安靜地變成 NULL，而 `inserted` 照樣把那列算成功——使用者看到「匯入
成功 N 筆」，實際上某幾欄整欄空白，且沒有任何一處會講。

本檔鎖住修復後的契約：映射走 `source_registry.header_column_map` 這個**唯一宣告**，對不上的
表頭在寫入層回報、在校驗端（上傳前）就先講清楚。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import db
from app.core.db import source_registry
from app.core.db import tables as T


@pytest.fixture
def client(temp_db):
    """TestClient（綁定 temp_db 隔離庫）。"""
    import app.api.main as m

    return TestClient(m.app)


# ── header_column_map 宣告本身 ──────────────────────────────────────────────


def test_header_map_is_identity_for_plain_columns():
    """一般來源：表頭即欄名（恆等映射涵蓋該表全部欄位）。"""
    m = source_registry.header_column_map("reviews")
    cols = {c.name for c in T.reviews.columns}
    assert cols.issubset(m), "恆等映射應涵蓋該表全部欄位"
    assert all(m[c] == c for c in cols)


def test_header_map_declares_mixpanel_aliases():
    """mixpanel：$ / 大寫表頭不是合法欄名，必須有宣告的別名。"""
    m = source_registry.header_column_map("mixpanel_tracker")
    assert m["$insert_id"] == "insert_id"
    assert m["$distinct_id"] == "distinct_id"
    assert m["$current_url"] == "current_url"
    assert m["$os"] == "os"
    assert m["Platform"] == "platform"


def test_header_map_unknown_source_is_empty():
    """未知來源回空 dict（呼叫端據此走既有的 spec is None 分支）。"""
    assert source_registry.header_column_map("no_such_source") == {}
    assert source_registry.header_column_map(None) == {}


# ── 寫入層 ──────────────────────────────────────────────────────────────────


def test_mixpanel_dollar_headers_land_in_columns(temp_db):
    """$ 表頭經宣告的別名落到正確欄位（原本由 upload_batch 的 _sanitize_row 負責，已下沉）。"""
    n = db.insert_source_batch(
        "mixpanel_tracker",
        [
            {
                "$insert_id": "MP-1",
                "$distinct_id": "D-1",
                "$current_url": "https://kkday.com/x",
                "$os": "iOS",
                "Platform": "app",
                "event": "feedback_submit",
            }
        ],
    )
    assert n == 1
    with T.get_engine().connect() as c:
        row = (
            c.execute(select(T.mixpanel_tracker).where(T.mixpanel_tracker.c.insert_id == "MP-1"))
            .mappings()
            .first()
        )
    assert row is not None, "$insert_id 未被映射成自然鍵 → 整列會被當作無特徵 id 丟棄"
    assert row["distinct_id"] == "D-1"
    assert row["current_url"] == "https://kkday.com/x"
    assert row["os"] == "iOS"
    assert row["platform"] == "app"
    assert row["event"] == "feedback_submit"


def test_unmapped_header_is_reported_not_silently_dropped(temp_db):
    """對不上的表頭必須回報給呼叫端 —— 這是「整欄靜默空白」的唯一徵兆。"""
    unmapped: set[str] = set()
    n = db.insert_source_batch(
        "reviews",
        [{"rec_oid": "R-UM-1", "rec_desc": "內容", "rec_desc_typo": "打錯的表頭"}],
        unmapped=unmapped,
    )
    assert n == 1, "多給的欄位不該讓整列失敗（上游多欄是常態）"
    assert unmapped == {"rec_desc_typo"}, "對不上的表頭必須被指出來"


def test_mapped_columns_still_persist_when_some_headers_unmapped(temp_db):
    """有對不上的表頭時，對得上的欄位仍須正常落庫（回報是附加資訊，不是中止）。"""
    db.insert_source_batch(
        "reviews",
        [{"rec_oid": "R-UM-2", "rec_desc": "正常內容", "unknown_col": "x"}],
    )
    with T.get_engine().connect() as c:
        row = c.execute(select(T.reviews).where(T.reviews.c.rec_oid == "R-UM-2")).mappings().first()
    assert row is not None
    assert row["rec_desc"] == "正常內容"


def test_headers_are_stripped_before_matching(temp_db):
    """表頭前後空白不該讓欄位對不上（試算表匯出常見）。"""
    unmapped: set[str] = set()
    db.insert_source_batch(
        "reviews", [{" rec_oid ": "R-UM-3", " rec_desc": "內容"}], unmapped=unmapped
    )
    assert unmapped == set()
    with T.get_engine().connect() as c:
        row = c.execute(select(T.reviews).where(T.reviews.c.rec_oid == "R-UM-3")).mappings().first()
    assert row is not None and row["rec_desc"] == "內容"


# ── 校驗端（上傳前的可見性）────────────────────────────────────────────────


def test_validate_reports_unmapped_headers(client):
    """`/api/inbound/validate` 必須在上傳前就把對不上的表頭列出來。"""
    csv = "rec_oid,rec_desc,rec_desc_typo\nR1,內容,打錯\n"
    r = client.post(
        "/api/inbound/validate",
        files={"file": ("reviews.csv", csv.encode("utf-8"), "text/csv")},
    )
    assert r.status_code == 200, r.text
    sheets = r.json()["sheets"]
    assert len(sheets) == 1
    sheet = sheets[0]
    assert sheet["detected_source"] == "reviews"
    assert sheet["status"] == "ok", "多給欄位不該擋上傳"
    assert sheet["unmapped_headers"] == ["rec_desc_typo"]


def test_validate_reports_no_unmapped_for_clean_headers(client):
    """表頭全對得上時 unmapped_headers 為空（避免誤報造成雜訊）。"""
    csv = "rec_oid,rec_desc\nR1,內容\n"
    r = client.post(
        "/api/inbound/validate",
        files={"file": ("reviews.csv", csv.encode("utf-8"), "text/csv")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["sheets"][0]["unmapped_headers"] == []
