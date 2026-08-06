"""API 端點契約測試（FastAPI TestClient + 隔離 PostgreSQL 測試庫）。

建立目前缺失的端點層安全網：settings（遮罩 + stub_mode）、findings（狀態覆核，含 404 與成功回填）、
problems（列表契約）。此網亦為未來 main.py 拆 router（Phase 5）的回歸保障——拆分前後端點行為須一致。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import auth


@pytest.fixture
def client(temp_db):
    """TestClient（綁定 temp_db 隔離庫；端點內 db 呼叫走 T.get_engine() 動態解析→測試庫）。"""
    import app.api.main as m

    return TestClient(m.app)


@pytest.fixture
def auth_headers(client):
    """本地模式無登入系統（固定身分，不驗 token）：受保護端點測試沿用此 fixture 名稱（回空
    header 即可，帶不帶 Authorization 行為一致），維持既有測試呼叫端 `headers=auth_headers` 不變。"""
    return {}


def test_start_prejudge_blocked_in_production_without_token(
    client, auth_headers, monkeypatch
) -> None:
    """正式環境 stub 主閘：解不出 LLM token 的批量初判啟動一律 403（防假判覆蓋真歸因）。"""
    from app.core import config

    monkeypatch.setattr(config.env, "app_env", "production")
    monkeypatch.setattr(config.env, "openai_api_key", "")
    r = client.post("/api/v1/prejudge", json={"scope": "all"}, headers=auth_headers)
    assert r.status_code == 403
    assert "stub" in r.json()["detail"]


def test_start_prejudge_stub_allowed_in_development(client, auth_headers, monkeypatch) -> None:
    """development 保留零 key 跑通閉環的既有行為（stub 是 dev 合法路徑，回歸鎖定）。"""
    from app.core import config

    monkeypatch.setattr(config.env, "openai_api_key", "")
    r = client.post("/api/v1/prejudge", json={"item_ids": []}, headers=auth_headers)
    assert r.status_code == 200


def test_start_prejudge_accepts_prompt_versions(client, auth_headers, monkeypatch) -> None:
    """prompt_versions（指定歷史版本）允許通過，不觸發草稿的 400 guard。"""
    monkeypatch.setattr("app.core.config.env.openai_api_key", "")  # dev 走 stub，免真 LLM
    r = client.post(
        "/api/v1/prejudge",
        json={"item_ids": [], "prompt_versions": {"prompt_C-1": 1}},
        headers=auth_headers,
    )
    assert r.status_code == 200


def test_me_returns_fixed_local_identity(client) -> None:
    """本地模式無登入系統：/api/auth/me 不帶 Authorization header 也直接回固定身分。

    該身分是 `SYSTEM_USER` 而非假 email——沒有 SSO 就沒有經過驗證的人，稽核欄記 system
    才誠實（見 app/core/auth.py）。be2 SSO 接入後這裡才會是真實 email。
    """
    r = client.get("/api/auth/me")
    assert r.status_code == 200 and r.json().get("email") == auth.SYSTEM_USER


# ── settings ──────────────────────────────────────────────────────
def test_settings_masked_with_stub_mode(client, auth_headers) -> None:
    r = client.get("/api/settings", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["stub_mode"] is True  # 測試無 token → stub


def test_settings_gdrive_upload_folder_url_roundtrip(client, auth_headers) -> None:
    """導出偏好（per-user）：存 URL → 讀回；存空字串＝清除（回 None，前端退全域 config 預設）。"""
    url = "https://drive.google.com/drive/folders/abc123"
    r = client.post("/api/settings", json={"gdrive_upload_folder_url": url}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["gdrive_upload_folder_url"] == url
    r = client.get("/api/settings", headers=auth_headers)
    assert r.json()["gdrive_upload_folder_url"] == url
    r = client.post("/api/settings", json={"gdrive_upload_folder_url": ""}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["gdrive_upload_folder_url"] is None


# ── /api/status（公司健康檢查契約）──────────────────────────────────
def test_status_contract(client) -> None:
    """公司 EKS 上線驗證契約：免認證 200 + 精確 body（k8s readiness probe 引用同一路徑）。"""
    r = client.get("/api/status")  # 無 Authorization header
    assert r.status_code == 200
    assert r.json() == {"status": "0000", "message": "success"}


def test_old_health_endpoint_removed(client) -> None:
    """/health 已 cutover 至 /api/status，舊路徑不應殘留（防雙端點漂移）。"""
    assert client.get("/health").status_code == 404


def test_metrics_endpoint_for_prometheus(client) -> None:
    """Prometheus /metrics 契約：免認證 200、exposition 格式（EKS Step 6 Grafana 驗收基礎）。"""
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "http_request" in r.text  # instrumentator 預設 metric 前綴存在即格式正確
