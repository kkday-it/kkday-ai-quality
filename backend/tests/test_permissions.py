"""可替換權限框架測試：/api/auth/permissions 契約 + require_permission 分級（default/grants）。

驗證 LocalPermissionProvider 依 permissions.json 派生權限（無角色，email 直接對照 default ∪
grants[email]）：no_auth_grant_all=true 全通過；false 時 default 為基礎集、grants[email] 疊加、
"*" 展全量。端點 require_permission 對缺權限一律 403；本地模式無登入系統，不帶 Authorization
header 一律成功身分解析（非 401）——存取控制交給權限層，非「有無 token」。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

# permissions_cfg / as_user fixtures 定義於 conftest.py（跨測試檔共用）。


def test_permissions_endpoint_shape_and_default_vs_grants(
    temp_db, permissions_cfg, as_user
) -> None:
    """/api/auth/permissions 回 be2 business-list 形狀；default 用戶為子集、grants("*") 為全量超集。"""
    from app.api.main import app

    with TestClient(app) as client:
        as_user("someone@kkday.com")
        default_body = client.get("/api/auth/permissions").json()
        as_user("boss@kkday.com")
        grant_body = client.get("/api/auth/permissions").json()

        # be2 契約形狀
        assert set(default_body.keys()) == {"value", "ttl"}
        assert isinstance(default_body["value"], list) and default_body["ttl"] > 0

        default_perms, grant_perms = set(default_body["value"]), set(grant_body["value"])
        # default 有質檢作業權限（含資料包導入導出）、無僅 grants 授權的敏感 key
        assert "finding.review.update" in default_perms
        assert "data.source.upload" in default_perms
        assert "data.datapack.import" in default_perms
        assert "judge-rule.version.manage" not in default_perms
        assert "settings.llm-config.manage" not in default_perms
        # grants("*") 全量且為 default 超集
        assert default_perms <= grant_perms
        assert "judge-rule.version.manage" in grant_perms


def test_default_forbidden_on_grants_only_endpoints(temp_db, permissions_cfg, as_user) -> None:
    """default 用戶打僅 grants 授權的端點（規則管理）一律 403。"""
    from app.api.main import app

    with TestClient(app) as client:
        as_user("someone@kkday.com")
        assert client.post("/api/judge-rules/C-1/reset-default").status_code == 403
        assert client.post("/api/judge-rules/reset-default-all").status_code == 403


def test_default_allowed_on_datapack_import(temp_db, permissions_cfg, as_user) -> None:
    """default 用戶打資料包匯入端點非 401/403（壞 zip 為 handler 內 4xx 非權限擋）。"""
    from app.api.main import app

    with TestClient(app) as client:
        as_user("someone@kkday.com")
        r = client.post("/api/admin/import/validate", files={"file": ("x.zip", b"x")})
        assert r.status_code not in (401, 403), r.text


def test_default_allowed_on_default_tier_endpoints(temp_db, permissions_cfg, as_user) -> None:
    """default 用戶打 default-tier 端點（問題列表導出）非 401/403（通過權限，進入 handler）。"""
    from app.api.main import app

    with TestClient(app) as client:
        as_user("someone@kkday.com")
        r = client.post("/api/problems/export", json={})
        assert r.status_code not in (401, 403), r.text


def test_grants_allowed_on_grants_only_endpoints(temp_db, permissions_cfg, as_user) -> None:
    """grants("*") 用戶打 grants-only 端點非 403（進入 handler；結果依默認檔存在與否為 200/404）。"""
    from app.api.main import app

    with TestClient(app) as client:
        as_user("boss@kkday.com")
        assert client.post("/api/judge-rules/C-1/reset-default").status_code != 403


def test_local_mode_never_401_unauthenticated(temp_db) -> None:
    """本地模式無登入系統：即使不帶 Authorization header 也一律成功身分解析（非 401）——
    走現行實際部署設定（config/global/permissions.json no_auth_grant_all=true），存取控制
    交給權限層本身，不是「有無 token」。"""
    from app.api.main import app

    with TestClient(app) as client:
        assert client.post("/api/problems/export", json={}).status_code != 401
        assert client.post("/api/judge-rules/C-1/reset-default").status_code != 401
        assert (
            client.patch("/api/findings/x/verdict", json={"status": "confirmed"}).status_code != 401
        )


def test_default_allowed_on_prejudge_run(temp_db, permissions_cfg, as_user) -> None:
    """default 用戶打批量初判啟動端點非 401/403（prejudge.run 為日常主功能）。"""
    from app.api.main import app

    with TestClient(app) as client:
        as_user("someone@kkday.com")
        r = client.post("/api/v1/prejudge", json={"item_ids": []})
        assert r.status_code not in (401, 403), r.text


def test_be2_provider_transitional_delegation(temp_db, permissions_cfg) -> None:
    """be2 provider 過渡實作：get_permissions/check 與 LocalProvider 安全等價（正式契約前委派）。"""
    from app.core.permissions.be2_provider import Be2PermissionProvider
    from app.core.permissions.local_provider import LocalPermissionProvider

    user = {"user_id": "u1", "email": "someone@kkday.com"}
    assert Be2PermissionProvider().get_permissions(
        user
    ) == LocalPermissionProvider().get_permissions(user)
    assert Be2PermissionProvider().check(user, "finding.review.update") is True
    assert (
        Be2PermissionProvider().check(user, "judge-rule.version.manage") is False
    )  # default 無此 key


def test_no_auth_grant_all_bypasses_everything(monkeypatch) -> None:
    """no_auth_grant_all=true（現行實際部署狀態）→ 任何 email 皆全權，含未在 grants 名單者。"""
    monkeypatch.setattr(
        "app.core.permissions.local_provider._permissions_cfg",
        lambda: {"no_auth_grant_all": True, "default": [], "grants": {}},
    )
    from app.core.permissions.local_provider import LocalPermissionProvider
    from app.core.permissions.permission_keys import ALL_KEYS

    assert LocalPermissionProvider().get_permissions({"email": "nobody@kkday.com"}) == set(ALL_KEYS)


def test_permissions_cfg_missing_file_falls_back_to_empty(monkeypatch) -> None:
    """permissions.json 缺失 → 全員無任何權限（fail-closed），不阻斷請求本身。"""
    from pathlib import Path

    from app.core.permissions import local_provider

    monkeypatch.setattr(local_provider, "_CACHE", None)
    monkeypatch.setattr(local_provider, "GLOBAL_DIR", Path("/nonexistent"))
    local_provider.reload()
    assert (
        local_provider.LocalPermissionProvider().get_permissions({"email": "x@kkday.com"}) == set()
    )
    local_provider.reload()  # 還原快取供後續測試讀回真檔
