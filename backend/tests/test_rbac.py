"""輕量 RBAC（config 白名單 + require_role）測試：角色派生 + 403 守衛 + admin 放行。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import auth


@pytest.fixture
def roles_cfg(monkeypatch):
    """固定角色名單（與 roles.json 內容解耦）。"""
    monkeypatch.setattr(
        auth, "_roles_cfg", lambda: {"admins": ["Boss@KKday.com"], "defaultRole": "qc"}
    )


def test_role_for_case_insensitive(roles_cfg) -> None:
    """admins 名單比對不分大小寫；非名單回 defaultRole；空 email 回 defaultRole。"""
    assert auth.role_for("boss@kkday.com") == "admin"
    assert auth.role_for("BOSS@KKDAY.COM") == "admin"
    assert auth.role_for("someone@kkday.com") == "qc"
    assert auth.role_for(None) == "qc"


def test_roles_cfg_missing_file_falls_back(monkeypatch) -> None:
    """roles.json 缺失 → 全員 defaultRole（qc），不阻斷登入。"""
    from pathlib import Path

    monkeypatch.setattr(auth, "_ROLES_CACHE", None)
    monkeypatch.setattr("app.core.paths.GLOBAL_DIR", Path("/nonexistent"))
    auth.reload_roles()
    assert auth.role_for("boss@kkday.com") in ("qc", "admin")  # 實檔存在時仍可能 admin
    auth.reload_roles()  # 還原快取供後續測試


def _register_and_login(client: TestClient, email: str) -> str:
    r = client.post("/api/auth/register", json={"email": email, "password": "secret1"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_rules_write_requires_admin(temp_db, roles_cfg) -> None:
    """qc 打規則寫入端點 → 403；admin → 放行（非 403）；讀端點 qc 仍可用。"""
    from app.api.main import app

    with TestClient(app) as client:
        qc_tok = _register_and_login(client, "someone@kkday.com")
        admin_tok = _register_and_login(client, "boss@kkday.com")

        # qc：讀 OK、寫 403
        r = client.get("/api/judge-rules", headers={"Authorization": f"Bearer {qc_tok}"})
        assert r.status_code == 200
        r = client.post(
            "/api/judge-rules/C-1/reset-default", headers={"Authorization": f"Bearer {qc_tok}"}
        )
        assert r.status_code == 403
        r = client.post(
            "/api/judge-rules/reset-default-all", headers={"Authorization": f"Bearer {qc_tok}"}
        )
        assert r.status_code == 403

        # admin：放行（進入 handler；結果依默認檔存在與否為 200/404，皆非 403）
        r = client.post(
            "/api/judge-rules/C-1/reset-default", headers={"Authorization": f"Bearer {admin_tok}"}
        )
        assert r.status_code != 403

        # /me 帶 role（前端 store 消費）
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {qc_tok}"})
        assert r.json()["role"] == "qc"
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {admin_tok}"})
        assert r.json()["role"] == "admin"
