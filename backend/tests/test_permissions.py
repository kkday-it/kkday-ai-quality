"""可替換權限框架測試：/api/auth/permissions 契約 + require_permission 分級（qc/admin/匿名）。

驗證 LocalPermissionProvider 依 role_permissions.json 派生權限：admin '*' 全量、qc 為質檢子集；
端點 require_permission 對 qc 擋 admin-tier（403）、放行 qc-tier（非 403），匿名一律 401。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def roles_cfg(monkeypatch):
    """固定角色名單（boss=admin·其餘 qc），與實檔 roles.json 解耦。"""
    monkeypatch.setattr(
        "app.core.auth._roles_cfg",
        lambda: {"admins": ["boss@kkday.com"], "defaultRole": "qc"},
    )


def _auth(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def _login(client: TestClient, email: str) -> str:
    r = client.post("/api/auth/register", json={"email": email, "password": "secret1"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_permissions_endpoint_shape_and_qc_vs_admin(temp_db, roles_cfg) -> None:
    """/api/auth/permissions 回 be2 business-list 形狀；qc 為質檢子集、admin 全量且為超集。"""
    from app.api.main import app

    with TestClient(app) as client:
        qc = _login(client, "someone@kkday.com")
        admin = _login(client, "boss@kkday.com")

        qc_body = client.get("/api/auth/permissions", headers=_auth(qc)).json()
        admin_body = client.get("/api/auth/permissions", headers=_auth(admin)).json()

        # be2 契約形狀
        assert set(qc_body.keys()) == {"value", "ttl", "startTime"}
        assert isinstance(qc_body["value"], list) and qc_body["ttl"] > 0

        qc_perms, admin_perms = set(qc_body["value"]), set(admin_body["value"])
        # qc 有質檢作業權限、無 admin-tier
        assert "finding.review.update" in qc_perms
        assert "data.source.upload" in qc_perms
        assert "judge-rule.version.manage" not in qc_perms
        assert "data.datapack.import" not in qc_perms
        # admin 全量且為 qc 超集
        assert qc_perms <= admin_perms
        assert {
            "judge-rule.version.manage",
            "config.file.write",
            "data.datapack.import",
        } <= admin_perms


def test_qc_forbidden_on_admin_tier_endpoints(temp_db, roles_cfg) -> None:
    """qc 打 admin-tier 端點（規則管理 / config 覆寫 / 資料包匯入）一律 403。"""
    from app.api.main import app

    with TestClient(app) as client:
        qc = _auth(_login(client, "someone@kkday.com"))
        assert client.post("/api/judge-rules/C-1/reset-default", headers=qc).status_code == 403
        assert client.post("/api/judge-rules/reset-default-all", headers=qc).status_code == 403
        assert (
            client.put(
                "/api/config/files/whatever.json", json={"content": {}}, headers=qc
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/admin/import/validate", files={"file": ("x.zip", b"x")}, headers=qc
            ).status_code
            == 403
        )


def test_qc_allowed_on_qc_tier_endpoints(temp_db, roles_cfg) -> None:
    """qc 打 qc-tier 端點（問題列表導出）非 401/403（通過權限，進入 handler）。"""
    from app.api.main import app

    with TestClient(app) as client:
        qc = _auth(_login(client, "someone@kkday.com"))
        r = client.post("/api/problems/export", json={}, headers=qc)
        assert r.status_code not in (401, 403), r.text


def test_admin_allowed_on_admin_tier_endpoints(temp_db, roles_cfg) -> None:
    """admin 打 admin-tier 端點非 403（進入 handler；結果依默認檔存在與否為 200/404）。"""
    from app.api.main import app

    with TestClient(app) as client:
        admin = _auth(_login(client, "boss@kkday.com"))
        assert client.post("/api/judge-rules/C-1/reset-default", headers=admin).status_code != 403


def test_anonymous_gets_401_not_403(temp_db) -> None:
    """匿名（無 token）打受權限端點一律 401（先過認證），非 403。"""
    from app.api.main import app

    with TestClient(app) as client:
        assert client.post("/api/problems/export", json={}).status_code == 401
        assert client.post("/api/judge-rules/C-1/reset-default").status_code == 401
        assert (
            client.patch("/api/findings/x/status", json={"status": "confirmed"}).status_code == 401
        )
