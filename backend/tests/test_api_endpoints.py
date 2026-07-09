"""API 端點契約測試（FastAPI TestClient + 隔離 PostgreSQL 測試庫）。

建立目前缺失的端點層安全網：auth（註冊/登入/守衛）、settings（遮罩 + stub_mode）、findings
（狀態 / 真值標註，含 404 與成功回填）、problems（列表契約）。此網亦為未來 main.py 拆 router
（Phase 5）的回歸保障——拆分前後端點行為須一致。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import db
from app.core.schema import TicketFinding


@pytest.fixture
def client(temp_db):
    """TestClient（綁定 temp_db 隔離庫；端點內 db 呼叫走 T.get_engine() 動態解析→測試庫）。"""
    import app.api.main as m

    return TestClient(m.app)


@pytest.fixture
def auth_headers(client):
    """註冊一個測試帳號並回傳 Bearer header（受保護端點共用）。"""
    r = client.post("/api/auth/register", json={"email": "qa@kkday.com", "password": "secret1"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── auth ──────────────────────────────────────────────────────────
def test_register_returns_token_and_public_user(client) -> None:
    r = client.post("/api/auth/register", json={"email": "A@KKday.com ", "password": "secret1"})
    assert r.status_code == 200
    body = r.json()
    assert body["token"] and body["user"]["email"] == "a@kkday.com"  # normalize 去空白 + 小寫
    assert "password_hash" not in body["user"]  # 不外洩雜湊


def test_register_rejects_bad_email_or_short_password(client) -> None:
    assert (
        client.post("/api/auth/register", json={"email": "noat", "password": "secret1"}).status_code
        == 400
    )
    assert (
        client.post("/api/auth/register", json={"email": "a@b.com", "password": "x"}).status_code
        == 400
    )


def test_register_duplicate_email_conflict(client) -> None:
    client.post("/api/auth/register", json={"email": "dup@kkday.com", "password": "secret1"})
    r = client.post("/api/auth/register", json={"email": "dup@kkday.com", "password": "secret1"})
    assert r.status_code == 409
    # 錯誤 code 契約（raise_api_error）：detail = {code, message}，供前端 i18n 對映
    detail = r.json()["detail"]
    assert detail["code"] == "AUTH.EMAIL_EXISTS" and detail["message"]


def test_login_failed_error_code_contract(client) -> None:
    """帳密錯 → 401 且 detail.code = AUTH.LOGIN_FAILED（error-code i18n 框架契約）。"""
    r = client.post("/api/auth/login", json={"email": "nope@kkday.com", "password": "x"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "AUTH.LOGIN_FAILED"


def test_login_success_and_wrong_password(client) -> None:
    client.post("/api/auth/register", json={"email": "u@kkday.com", "password": "secret1"})
    assert (
        client.post(
            "/api/auth/login", json={"email": "u@kkday.com", "password": "secret1"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/auth/login", json={"email": "u@kkday.com", "password": "wrong"}
        ).status_code
        == 401
    )


def test_me_requires_auth(client, auth_headers) -> None:
    assert client.get("/api/auth/me").status_code in (401, 403)  # 無 token 被守衛擋
    r = client.get("/api/auth/me", headers=auth_headers)
    assert r.status_code == 200 and r.json()["email"] == "qa@kkday.com"


# ── settings ──────────────────────────────────────────────────────
def test_settings_masked_with_stub_mode(client, auth_headers) -> None:
    r = client.get("/api/settings", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["stub_mode"] is True  # 測試無 token → stub


# ── findings：狀態 / 真值標註 ──────────────────────────────────────
def _seed_one_finding() -> str:
    """種一筆 product_reviews 列 + 對應歸因，回 finding_id（供狀態/真值端點成功路徑測試）。"""
    db.insert_source_batch(
        "product_reviews",
        [
            {
                "rec_oid": "R1",
                "create_date": "2026-06-01 10:00:00",
                "prod_oid": "P1",
                "order_snap_json": "{}",
            }
        ],
    )
    fid = "fd_product_reviews_R1__content"
    db.replace_source_findings(
        "product_reviews",
        "R1",
        [
            TicketFinding(
                finding_id=fid,
                ticket_id="R1",
                dimension="non_content",
                recommended_action="no_action",
            )
        ],
    )
    return fid


def test_patch_finding_status_requires_auth(client) -> None:
    # 匿名（無 Bearer）→ 401，不得改動任何歸因狀態
    assert client.patch("/api/findings/x/status", json={"status": "confirmed"}).status_code == 401


def test_patch_finding_status_not_found_and_success(client, auth_headers) -> None:
    assert (
        client.patch(
            "/api/findings/nope/status", json={"status": "confirmed"}, headers=auth_headers
        ).status_code
        == 404
    )
    fid = _seed_one_finding()
    r = client.patch(
        f"/api/findings/{fid}/status", json={"status": "confirmed"}, headers=auth_headers
    )
    assert r.status_code == 200 and r.json()["status"] == "confirmed"


def test_patch_finding_status_rejects_invalid_value(client, auth_headers) -> None:
    # Literal 僅 confirmed/dismissed/fixed；'new' 等非法 → 422
    assert (
        client.patch(
            "/api/findings/x/status", json={"status": "new"}, headers=auth_headers
        ).status_code
        == 422
    )


def test_patch_true_label_requires_auth(client) -> None:
    assert (
        client.patch("/api/findings/x/true_label", json={"true_label": "content"}).status_code
        == 401
    )


def test_patch_true_label_not_found_and_success(client, auth_headers) -> None:
    assert (
        client.patch(
            "/api/findings/nope/true_label", json={"true_label": "content"}, headers=auth_headers
        ).status_code
        == 404
    )
    fid = _seed_one_finding()
    r = client.patch(
        f"/api/findings/{fid}/true_label", json={"true_label": "content"}, headers=auth_headers
    )
    assert r.status_code == 200 and r.json()["true_label"] == "content"


# ── problems ──────────────────────────────────────────────────────
def test_problems_list_contract(client, auth_headers) -> None:
    r = client.get("/api/problems?source=product_reviews", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"rows", "total"} and body["total"] == 0
