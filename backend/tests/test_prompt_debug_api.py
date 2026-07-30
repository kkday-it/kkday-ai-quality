"""Prompt 調試台端點層安全網：正式版區全空的降級行為 + 升版/回退的權限閘。

為什麼要這一支：2026-07-30 的 P0 事故形狀是「`versions/release-v1.md` 從磁碟消失，index.json
仍指向它」→ `active_release()` 拋 `FileNotFoundError`，而**沒有任何 caller 捕捉、`main.py` 也
沒有全域 exception handler**，於是五個端點全裸奔成 500，前端只看到無意義的 Internal Server
Error。修法是引入 `NoActiveReleaseError` 領域例外 + app 層 handler → 404 + 可理解訊息。

這裡鎖住兩件事：
① **草稿工作流不受正式版缺失影響**（草稿中心定位的硬性保證）——`defaults` 仍回得出最新草稿；
② 真正需要正式版的端點在全空時回 **404 + 可理解訊息**，不是裸 500。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(temp_db):
    import app.api.main as m

    return TestClient(m.app)


@pytest.fixture
def with_llm_token(monkeypatch):
    """讓 `_effective_or_400` 的 token/model 檢查通過。

    ⚠️ 順序很重要：端點是**先**驗 LLM 配置（缺 token → 400）**才**解析 Prompt 版本，所以測
    「正式版缺失回 404」必須先把 token 這關餵過去，否則永遠停在 400、根本走不到要測的那段。
    這個順序本身是合理的（配置錯誤比版本缺失更基礎），故測試配合它，不是改產品碼。
    """
    from app.core import settings as app_settings

    monkeypatch.setattr(app_settings, "resolve_provider_token", lambda eff: "fake-token")


@pytest.fixture
def empty_releases(tmp_path, monkeypatch):
    """把正式版區指到一個空目錄（草稿區維持真實內容）——模擬「一支正式版都沒有」。

    直接改模組級的 `RELEASES_DIR`/`INDEX_FILE` 而非動真實檔案：測試不得破壞 repo 內的
    `prompts/conversations/versions/`（那是真實資料，P0 事故正是它消失造成的）。
    """
    from app.judge import prompt_debug_versions as pdv

    releases = tmp_path / "versions"
    releases.mkdir()
    monkeypatch.setattr(pdv, "RELEASES_DIR", releases)
    monkeypatch.setattr(pdv, "INDEX_FILE", releases / "index.json")
    return releases


def test_active_release_raises_typed_error_when_empty(empty_releases) -> None:
    """正式版區全空 → 拋 `NoActiveReleaseError`（而非裸 FileNotFoundError）。

    型別要精準是因為 app 層 handler 只認這個子型別：本專案另有語義相反的 FileNotFoundError
    （`prompt_source` 的引擎 fail-loud＝伺服器設定壞了，該回 500），不能一起被當 404。
    """
    from app.judge import prompt_debug_versions as pdv

    with pytest.raises(pdv.NoActiveReleaseError):
        pdv.active_release()
    # 仍是 FileNotFoundError 的子類——既有 `except FileNotFoundError` 呼叫端行為不變
    assert issubclass(pdv.NoActiveReleaseError, FileNotFoundError)


def test_defaults_survives_empty_releases(client, empty_releases) -> None:
    """**草稿中心的硬性保證**：正式版全空時 `defaults` 不得 500，仍要回得出最新草稿全文。

    這是 P0 當下實際壞掉的端點（頁面卡在「載入 Prompt 中…」）。
    """
    r = client.get("/api/v1/prejudge/prompt-debug/defaults")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["active_release"] == ""  # 沒有正式版
    assert body["release_prompt"] == ""
    assert body["latest_draft"]  # 但草稿還在
    assert body["system_prompt"].strip()  # 且載入口徑（最新草稿）有內容


def test_stream_returns_404_not_500_when_no_release(client, empty_releases, with_llm_token) -> None:
    """需要正式版卻一支都沒有 → 404 + 可理解訊息，不是裸 500。

    `system_prompt` 留空＝要求後端取當前正式版，正是會觸達 `active_release()` 的路徑。
    """
    r = client.post(
        "/api/v1/prejudge/prompt-debug/stream",
        json={"text": "測試對話", "system_prompt": ""},
    )
    assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.text}"
    assert "正式版" in r.json()["detail"]


def test_batch_start_returns_404_not_500_when_no_release(
    client, empty_releases, with_llm_token
) -> None:
    """`batch/start` 原本只捕 `ValueError`，`NoActiveReleaseError` 會漏出去成 500。

    這支就是「逐一在端點補 try/except 必漏」的證據——改用 app 層 handler 收斂後才一併涵蓋。
    """
    r = client.post(
        "/api/v1/prejudge/prompt-debug/batch/start",
        files={"file": ("t.csv", b"session_oid,conversation_full\ns1,x\n", "text/csv")},
        data={"system_prompt": ""},
    )
    assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.text}"
    assert "正式版" in r.json()["detail"]


def test_activate_nonexistent_release_returns_404(client) -> None:
    """回退指向不存在的正式版 → 404（不允許把 active 指標指向不存在的檔案——那正是 P0 的形狀）。"""
    r = client.post("/api/v1/prejudge/prompt-debug/releases/no-such-release/activate")
    assert r.status_code == 404, r.text


def test_activate_rejects_illegal_name(client) -> None:
    """回退的版本名走白名單守門：`..` 這類名稱在解析前就該被拒（400），不進檔案系統。"""
    r = client.post("/api/v1/prejudge/prompt-debug/releases/..%2Fevil/activate")
    # 路徑穿越可能被 starlette 路由層先擋掉（404）或到達 handler 被守門拒絕（400）——
    # 兩者都是「沒讓它碰到檔案系統」，故接受任一，但不得是 2xx/5xx
    assert r.status_code in (400, 404), r.text


def test_promote_rejects_duplicate_name(client) -> None:
    """升版撞名 → 400，**不覆蓋既有正式版**（正式版是上線口徑，覆寫等於無聲改線上）。"""
    from app.judge import prompt_debug_versions as pdv

    drafts = pdv.list_drafts()
    existing = pdv.list_releases()
    if not drafts or not existing:
        pytest.skip("需要至少一支草稿與一支既有正式版")
    r = client.post(
        "/api/v1/prejudge/prompt-debug/releases",
        json={"draft": drafts[0], "name": existing[0]["name"], "note": "撞名測試"},
    )
    assert r.status_code == 400, r.text
    assert "已存在" in r.json()["detail"]


def test_promote_and_activate_declare_version_manage_permission() -> None:
    """升版與回退都必須掛 `judge-rule.version.manage`（比存草稿的 `prejudge.run` 高一階）。

    ⚠️ **為什麼驗「宣告」而不是驗 HTTP 403**：本專案本地為單機內網、已廢除帳號登入系統，
    `config/global/permissions.json` 的 `no_auth_grant_all: true` 讓權限檢查全域放行——在這個
    環境下打端點永遠不會回 403，寫「assert 403」只會得到一個假通過的測試。故改為驗證路由上
    確實掛了正確的權限 key（be2 SSO 接入、該旗標轉 false 後，這個宣告就是真實的閘）。
    """
    from app.api.routers.v1 import prompt_debug as router_mod
    from app.core.permissions import permission_keys

    src = (router_mod.__file__ or "").replace(".pyc", ".py")
    text = __import__("pathlib").Path(src).read_text(encoding="utf-8")

    # 兩個高權限端點的 handler 定義處都應緊接著 JUDGE_RULE_MANAGE 依賴
    for handler in ("prompt_debug_promote_release", "prompt_debug_activate_release"):
        start = text.index(f"def {handler}(")
        block = text[start : start + 400]
        assert "JUDGE_RULE_MANAGE" in block, f"{handler} 未掛 judge-rule.version.manage"

    # 存草稿刻意是低門檻（實驗行為不該需要上線權限）
    start = text.index("def prompt_debug_save_draft(")
    assert "PREJUDGE_RUN" in text[start : start + 400]
    assert permission_keys.JUDGE_RULE_MANAGE != permission_keys.PREJUDGE_RUN
