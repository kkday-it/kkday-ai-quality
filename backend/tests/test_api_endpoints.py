"""API 端點契約測試（FastAPI TestClient + 隔離 PostgreSQL 測試庫）。

建立目前缺失的端點層安全網：settings（遮罩 + stub_mode）、findings（狀態覆核，含 404 與成功回填）、
problems（列表契約）。此網亦為未來 main.py 拆 router（Phase 5）的回歸保障——拆分前後端點行為須一致。
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
    """本地模式無登入系統：/api/auth/me 不帶 Authorization header 也直接回固定身分。"""
    r = client.get("/api/auth/me")
    assert r.status_code == 200 and r.json().get("user_id") == "local"


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


# ── findings：狀態覆核 ──────────────────────────────────────
def _seed_one_finding() -> str:
    """種一筆 product_reviews 列 + 對應歸因，回 finding_id（供狀態端點成功路徑測試）。"""
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
                recommended_action="no_action",
            )
        ],
    )
    return fid


def test_patch_finding_status_not_found_and_success(client, auth_headers) -> None:
    assert (
        client.patch(
            "/api/findings/nope/verdict", json={"status": "confirmed"}, headers=auth_headers
        ).status_code
        == 404
    )
    fid = _seed_one_finding()
    r = client.patch(
        f"/api/findings/{fid}/verdict", json={"status": "confirmed"}, headers=auth_headers
    )
    assert r.status_code == 200 and r.json()["status"] == "confirmed"


def test_patch_finding_status_rejects_invalid_value(client, auth_headers) -> None:
    # Literal 僅 confirmed/dismissed/new（new＝撤銷覆核）；fixed 已撤除、其餘非法值 → 422
    assert (
        client.patch(
            "/api/findings/x/verdict", json={"status": "fixed"}, headers=auth_headers
        ).status_code
        == 422
    )


# ── problems ──────────────────────────────────────────────────────
def test_problems_list_contract(client, auth_headers) -> None:
    r = client.get("/api/problems?source=product_reviews", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"rows", "total"} and body["total"] == 0


def test_problems_status_filter_and_model_in_dto(client, auth_headers) -> None:
    """status 篩選命中/未命中 + attribution DTO 帶 model/notes_count（列表 model 標籤資料源）。"""
    _seed_one_finding()
    hit = client.get("/api/problems?source=product_reviews&status=new", headers=auth_headers).json()
    assert hit["total"] == 1
    a = hit["rows"][0]["attributions"][0]
    assert "model" in a and a["notes_count"] == 0
    miss = client.get(
        "/api/problems?source=product_reviews&status=dismissed", headers=auth_headers
    ).json()
    assert miss["total"] == 0


# ── findings：備註 / 批量初判 ─────────────────────────────────
def test_batch_status_endpoint(client, auth_headers) -> None:
    """批量初判端點：空清單 422；成功回實際更新數（同值冪等跳過語義在 db 層測）。"""
    assert (
        client.patch(
            "/api/findings/batch/verdict",
            json={"source": "product_reviews", "source_ids": [], "status": "confirmed"},
            headers=auth_headers,
        ).status_code
        == 422
    )
    _seed_one_finding()
    r = client.patch(
        "/api/findings/batch/verdict",
        json={"source": "product_reviews", "source_ids": ["R1"], "status": "confirmed"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["updated"] == 1 and body["status"] == "confirmed"


# ── judgment-history（評論級歸因歷史時間軸）──────────────────────
def test_attribution_history_endpoints(client, auth_headers) -> None:
    """歷史列表（初判事件）+ 評論級備註新增。"""
    _seed_one_finding()
    r = client.get(
        "/api/attribution-history?source=product_reviews&source_id=R1", headers=auth_headers
    )
    assert r.status_code == 200
    events = r.json()
    assert len(events) == 1 and events[0]["kind"] == "prejudge"
    rn = client.post(
        "/api/attribution-history/notes",
        json={"source": "product_reviews", "source_id": "R1", "content": "e2e 備註"},
        headers=auth_headers,
    )
    assert rn.status_code == 200 and rn.json()["kind"] == "note"
    events = client.get(
        "/api/attribution-history?source=product_reviews&source_id=R1", headers=auth_headers
    ).json()
    assert [e["kind"] for e in events] == ["prejudge", "note"]  # 舊到新：初判先發生


# ── prompt-sandbox（歸因列表 Prompt 測試沙盒）────────────────────────
def test_prompt_sandbox_start_rejects_stub_unconditionally(client, auth_headers, monkeypatch):
    """dev 零 key 時仍拒跑（無條件，非僅正式環境）——測試工具刻意比 /prejudge 嚴格，避免假結果。"""
    from app.core import config

    monkeypatch.setattr(config.env, "openai_api_key", "")
    r = client.post(
        "/api/v1/prejudge/prompt-sandbox",
        json={"source": "product_reviews", "item_ids": ["R1"], "prompt_ids": ["polarity"]},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "stub" in r.json()["detail"]


def test_prompt_sandbox_count_item_ids_priority(client, auth_headers):
    """item_ids 顯式給定時優先採用，不論 scope／filters（比照 /prejudge/count 同一套解析）。"""
    r = client.post(
        "/api/v1/prejudge/prompt-sandbox/count",
        json={
            "source": "product_reviews",
            "item_ids": ["R1", "R2", "R3"],
            "prompt_ids": ["polarity"],
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json() == {"total": 3}


def test_prompt_sandbox_count_resolves_scope_all(client, auth_headers):
    """scope=all 時依 stages 目標選取（預設 unjudged）解析——與初判分類同一套 _resolve_target_ids，
    未落任何 finding 的列即命中，零改動重用 db.prejudge_target_ids。"""
    db.insert_source_batch(
        "product_reviews",
        [
            {
                "rec_oid": "SBX1",
                "create_date": "2026-06-01 10:00:00",
                "prod_oid": "P1",
                "order_snap_json": "{}",
            }
        ],
    )
    r = client.post(
        "/api/v1/prejudge/prompt-sandbox/count",
        json={"source": "product_reviews", "scope": "all", "prompt_ids": ["polarity"]},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["total"] >= 1


def test_prompt_sandbox_status_unknown_job_404(client, auth_headers):
    r = client.get(
        "/api/v1/prejudge/prompt-sandbox/status?job_id=psbxjob_不存在", headers=auth_headers
    )
    assert r.status_code == 404


def test_prompt_sandbox_runs_list_and_detail(client, auth_headers):
    """歷史列表/詳情端點直接讀已落庫的沙盒 run（不重跑 job，隔離驗證 GET 端點契約）。"""
    run_id = db.insert_sandbox_run(
        {
            "source": "product_reviews",
            "scope": "single",
            "item_ids": ["R1"],
            "prompt_ids": ["polarity", "C-1"],
            "item_count": 1,
            "results": [{"source_id": "R1", "polarity": "negative", "prompts": []}],
            "log": [{"ts": 1.0, "kind": "stage", "stage": "job", "message": "測試"}],
            "model": "gpt-5-mini",
            "triggered_by": "qc@kkday.com",
            "job_id": "psbxjob_seed",
        }
    )

    r_list = client.get("/api/v1/prejudge/prompt-sandbox/runs", headers=auth_headers)
    assert r_list.status_code == 200
    body = r_list.json()
    assert body["total"] == 1
    assert "results" not in body["items"][0] and "log" not in body["items"][0]

    r_detail = client.get(f"/api/v1/prejudge/prompt-sandbox/runs/{run_id}", headers=auth_headers)
    assert r_detail.status_code == 200
    detail = r_detail.json()
    assert detail["results"][0]["source_id"] == "R1"
    assert detail["log"][0]["message"] == "測試"


def test_prompt_sandbox_run_detail_unknown_404(client, auth_headers):
    r = client.get("/api/v1/prejudge/prompt-sandbox/runs/psbx_不存在", headers=auth_headers)
    assert r.status_code == 404


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
