"""Prompt 草稿閉環測試：db CRUD → prompt_source 草稿注入 → 沙盒 fail-fast/雙跑形狀 → API 端點。

草稿＝未入庫的編輯中 prompt 內容（prompt_drafts 表，與 judge_rule_versions「存檔即 active」
語意分離）；沙盒以 drafts 直測草稿並可雙跑對比（見 app.judge.prompt_sandbox docstring）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import auth, db
from app.judge import prompt_eval, prompt_source

# ── 測試用最小合法 prompt md（滿足 validate：三節 + {TEXT}；polarity 無 Taxonomy）──
POLARITY_MD = """# 測試極性

## System
```
you are a judge
```

## User
```
{TEXT}
```

## Schema
```json
{"type": "object", "properties": {"polarity": {"type": "string"}}, "required": ["polarity"]}
```
"""


@pytest.fixture
def roles_cfg(monkeypatch):
    """固定角色名單（與 roles.json 內容解耦）。"""
    monkeypatch.setattr(
        auth, "_roles_cfg", lambda: {"admins": ["Boss@KKday.com"], "defaultRole": "qc"}
    )


def _register_and_login(client: TestClient, email: str) -> str:
    r = client.post("/api/auth/register", json={"email": email, "password": "secret1"})
    return r.json()["token"]


# ─────────────────────────── db 層 ───────────────────────────
def test_draft_crud_roundtrip(temp_db) -> None:
    """upsert（新建→覆蓋）→ get → list → delete 全鏈路；last-write-wins。"""
    assert db.get_prompt_draft("prompt_polarity") is None
    db.upsert_prompt_draft(
        "prompt_polarity", {"_meta": {"kind": "prompt"}, "text": "v1"}, 3, updated_by="a@kk.com"
    )
    d = db.get_prompt_draft("prompt_polarity")
    assert d["content"]["text"] == "v1" and d["base_version"] == 3
    # 覆蓋（last-write-wins）
    db.upsert_prompt_draft(
        "prompt_polarity", {"_meta": {"kind": "prompt"}, "text": "v2"}, 5, updated_by="b@kk.com"
    )
    d = db.get_prompt_draft("prompt_polarity")
    assert d["content"]["text"] == "v2" and d["base_version"] == 5 and d["updated_by"] == "b@kk.com"
    metas = db.list_prompt_drafts()
    assert [m["rule_code"] for m in metas] == ["prompt_polarity"]
    assert "content" not in metas[0]  # 列表不帶全文（體積）
    assert db.delete_prompt_draft("prompt_polarity") is True
    assert db.delete_prompt_draft("prompt_polarity") is False  # 冪等：再刪回 False 不拋錯
    assert db.get_prompt_draft("prompt_polarity") is None


# ─────────────────────────── prompt_source 草稿注入 ───────────────────────────
def test_load_draft_overrides_and_no_cache_pollution(temp_db) -> None:
    """drafts 命中：解析草稿全文；一般路徑 cache 不被汙染；drafts 優先於 versions。"""
    prompt_source.reload()
    p_active = prompt_source.load("00_polarity")
    p_draft = prompt_source.load("00_polarity", drafts={"prompt_polarity": POLARITY_MD})
    assert p_draft["title"] == "測試極性"
    assert p_active["title"] != "測試極性"
    # 草稿路徑不寫 cache：再次一般載入仍是 active 內容
    assert prompt_source.load("00_polarity")["title"] == p_active["title"]
    # drafts 優先於 versions（versions 指到不存在版本也不會被讀，草稿先命中）
    p_both = prompt_source.load(
        "00_polarity", versions={"prompt_polarity": 99999}, drafts={"prompt_polarity": POLARITY_MD}
    )
    assert p_both["title"] == "測試極性"


def test_load_domain_draft_derives_enum(temp_db) -> None:
    """域 prompt 草稿仍走 `## Taxonomy` → Schema l2_code enum 派生注入。"""
    md = prompt_source._raw_text("01_C-1_content")
    p = prompt_source.load("01_C-1_content", drafts={"prompt_C-1": md})
    enum = p["schema"]["properties"]["attributions"]["items"]["properties"]["l2_code"]["enum"]
    assert enum, "域草稿 enum 應由 Taxonomy 派生非空"


# ─────────────────────────── 沙盒 start fail-fast + 雙跑形狀 ───────────────────────────
def test_sandbox_start_rejects_bad_drafts(temp_db) -> None:
    """未知 rule_code / 驗證不過的草稿 → 不派工即拋 ValueError。"""
    from app.judge import prompt_sandbox

    eff = {"model": "m", "api_token": "t"}
    with pytest.raises(ValueError, match="未知 rule_code"):
        prompt_sandbox.start(
            "product_reviews", ["x"], ["polarity"], eff, scope="single", drafts={"bogus": "md"}
        )
    with pytest.raises(ValueError, match="草稿驗證不過"):
        prompt_sandbox.start(
            "product_reviews",
            ["x"],
            ["polarity"],
            eff,
            scope="single",
            drafts={"prompt_polarity": "no sections"},
        )


def test_sandbox_one_compare_shape(temp_db, monkeypatch) -> None:
    """compare 雙跑：item 形狀為 {source_id, text, compare, baseline, draft}，變體不重複 source_id/text。"""
    from app.judge import prompt_sandbox

    fake = {
        "source_id": "sid1",
        "text": "t",
        "polarity": "negative",
        "sentiment_score": 2,
        "prompts": [],
    }
    monkeypatch.setattr(prompt_eval, "_build_sandbox_item", lambda s, i: {"source_id": i})
    monkeypatch.setattr(
        prompt_eval, "sandbox_classify", lambda item, pids, model, versions=None, drafts=None: fake
    )
    out = prompt_sandbox._one(
        "product_reviews",
        "sid1",
        ["polarity"],
        "m",
        versions=None,
        drafts={"prompt_polarity": POLARITY_MD},
        compare=True,
    )
    assert out["compare"] is True and out["source_id"] == "sid1"
    assert set(out) == {"source_id", "text", "compare", "baseline", "draft"}
    assert "source_id" not in out["baseline"] and "text" not in out["draft"]
    # 單跑（compare=False）維持 sandbox_classify 原形狀
    out2 = prompt_sandbox._one(
        "product_reviews", "sid1", ["polarity"], "m", versions=None, drafts=None, compare=False
    )
    assert out2 is fake


def test_sandbox_pair_metrics_shapes() -> None:
    """sandbox_result_summary/sandbox_pair_metrics：沙盒結果 → 等價性指標（純函式）。"""
    a = {
        "polarity": "negative",
        "sentiment_score": 2,
        "prompts": [
            {
                "prompt_id": "C-1",
                "attributions": [{"l1_domain_code": "C-1", "l2_code": "C-1-2", "confidence": 0.9}],
            }
        ],
    }
    b = {
        "polarity": "negative",
        "sentiment_score": 2,
        "prompts": [
            {
                "prompt_id": "C-1",
                "attributions": [{"l1_domain_code": "C-1", "l2_code": "C-1-3", "confidence": 0.8}],
            }
        ],
    }
    s = prompt_eval.sandbox_result_summary(a)
    assert s["n_findings"] == 1 and s["primary"] == ["C-1", "C-1-2"]
    m = prompt_eval.sandbox_pair_metrics([(a, a), (a, b)])
    assert m["n"] == 2 and m["polarity_agree"] == 1.0
    assert m["facet_jaccard_mean"] == 0.5 and m["primary_agree"] == 0.5


# ─────────────────────────── API 端點 ───────────────────────────
def test_draft_api_full_loop(temp_db, roles_cfg) -> None:
    """GET(null) → PUT → GET → drafts 列表 → validate（合法/非法）→ DELETE 全閉環；
    非 prompt code 一律 404。"""
    from app.api.main import app

    with TestClient(app) as client:
        admin = {"Authorization": f"Bearer {_register_and_login(client, 'boss@kkday.com')}"}
        # 無草稿：200 + draft: null
        r = client.get("/api/judge-rules/prompt_polarity/draft", headers=admin)
        assert r.status_code == 200 and r.json()["draft"] is None
        # PUT（寬鬆：半成品可存）
        r = client.put(
            "/api/judge-rules/prompt_polarity/draft",
            json={"content": {"_meta": {}, "text": "draft wip"}, "base_version": 1},
            headers=admin,
        )
        assert r.status_code == 200 and r.json()["saved"] is True
        r = client.get("/api/judge-rules/prompt_polarity/draft", headers=admin)
        assert r.json()["draft"]["content"]["text"] == "draft wip"
        assert r.json()["draft"]["base_version"] == 1
        # 空 text 拒存
        r = client.put(
            "/api/judge-rules/prompt_polarity/draft",
            json={"content": {"text": "  "}, "base_version": 1},
            headers=admin,
        )
        assert r.status_code == 422
        # 草稿存在狀態列表
        r = client.get("/api/judge-rules/drafts", headers=admin)
        assert [m["rule_code"] for m in r.json()] == ["prompt_polarity"]
        # dry-run 驗證：非法（200 + valid:false）、合法
        r = client.post(
            "/api/judge-rules/prompt_polarity/validate", json={"text": "bad"}, headers=admin
        )
        assert r.status_code == 200 and r.json()["valid"] is False and r.json()["error"]
        r = client.post(
            "/api/judge-rules/prompt_polarity/validate", json={"text": POLARITY_MD}, headers=admin
        )
        assert r.json() == {"valid": True}
        # DELETE（冪等）
        r = client.delete("/api/judge-rules/prompt_polarity/draft", headers=admin)
        assert r.json()["deleted"] is True
        r = client.delete("/api/judge-rules/prompt_polarity/draft", headers=admin)
        assert r.json()["deleted"] is False
        # 非 prompt code：草稿/驗證端點 404
        for method, url in [
            ("get", "/api/judge-rules/source_mapping/draft"),
            ("post", "/api/judge-rules/source_mapping/validate"),
        ]:
            r = getattr(client, method)(
                url, headers=admin, **({"json": {"text": "x"}} if method == "post" else {})
            )
            assert r.status_code == 404


def _mk_result(sid: str, l2: str) -> dict:
    """對比測試用的單筆沙盒結果（單跑形狀）。"""
    return {
        "source_id": sid,
        "text": "t",
        "polarity": "negative",
        "sentiment_score": 2,
        "prompts": [
            {
                "prompt_id": "C-1",
                "matched": True,
                "attributions": [{"l1_domain_code": "C-1", "l2_code": l2, "confidence": 0.9}],
            }
        ],
    }


def test_runs_compare_keeps_error_items_out_of_metrics(temp_db, roles_cfg) -> None:
    """run-vs-run 對比：error item 保留於 items（帶 error 標記，不靜默消失——草稿造成初判失敗
    正是對比最該凸顯的訊號），但不計入 metrics；單邊獨有 item 另一側為 null。"""
    from app.api.main import app

    run_a = db.insert_sandbox_run(
        {
            "source": "product_reviews",
            "scope": "single",
            "item_ids": ["s1", "s2"],
            "prompt_ids": ["C-1"],
            "item_count": 2,
            "results": [_mk_result("s1", "C-1-2"), {"source_id": "s2", "error": "boom"}],
            "log": [],
            "model": "m",
            "job_id": "j_a",
        }
    )
    run_b = db.insert_sandbox_run(
        {
            "source": "product_reviews",
            "scope": "single",
            "item_ids": ["s1", "s3"],
            "prompt_ids": ["C-1"],
            "item_count": 2,
            "results": [_mk_result("s1", "C-1-3"), _mk_result("s3", "C-1-2")],
            "log": [],
            "model": "m",
            "job_id": "j_b",
        }
    )
    with TestClient(app) as client:
        tok = {"Authorization": f"Bearer {_register_and_login(client, 'someone@kkday.com')}"}
        r = client.get(
            f"/api/v1/prejudge/prompt-sandbox/runs/compare?a={run_a}&b={run_b}", headers=tok
        )
        assert r.status_code == 200
        body = r.json()
        items = {it["source_id"]: it for it in body["items"]}
        assert set(items) == {"s1", "s2", "s3"}
        assert items["s2"]["a"]["error"] == "boom" and items["s2"]["b"] is None  # 失敗筆可見
        assert items["s3"]["a"] is None  # 單邊獨有另一側 null
        assert body["metrics"]["n"] == 1  # 只有 s1 兩邊皆成功 → 進 metrics
        assert body["metrics"]["facet_jaccard_mean"] == 0.0  # C-1-2 vs C-1-3 無交集


def test_run_detail_compare_metrics(temp_db, roles_cfg) -> None:
    """雙跑對比 run 詳情：動態附 metrics（僅 compare item 進 pairs；error item 排除）。"""
    from app.api.main import app

    item = {
        "source_id": "s1",
        "text": "t",
        "compare": True,
        "baseline": {k: v for k, v in _mk_result("s1", "C-1-2").items() if k != "source_id"},
        "draft": {k: v for k, v in _mk_result("s1", "C-1-2").items() if k != "source_id"},
    }
    run_id = db.insert_sandbox_run(
        {
            "source": "product_reviews",
            "scope": "single",
            "item_ids": ["s1", "s2"],
            "prompt_ids": ["C-1"],
            "item_count": 2,
            "results": [item, {"source_id": "s2", "error": "boom"}],
            "log": [],
            "model": "m",
            "job_id": "j_c",
            "drafts": {"prompt_C-1": "# md"},
            "compare": True,
        }
    )
    with TestClient(app) as client:
        tok = {"Authorization": f"Bearer {_register_and_login(client, 'someone@kkday.com')}"}
        r = client.get(f"/api/v1/prejudge/prompt-sandbox/runs/{run_id}", headers=tok)
        assert r.status_code == 200
        body = r.json()
        assert body["compare"] is True and body["drafts"] == {"prompt_C-1": "# md"}
        assert body["metrics"]["n"] == 1 and body["metrics"]["facet_jaccard_mean"] == 1.0


def test_draft_write_requires_admin(temp_db, roles_cfg) -> None:
    """qc 對草稿寫入端點 → 403；讀端點放行（比照規則管理權限分界）。"""
    from app.api.main import app

    with TestClient(app) as client:
        qc = {"Authorization": f"Bearer {_register_and_login(client, 'someone@kkday.com')}"}
        assert (
            client.put(
                "/api/judge-rules/prompt_polarity/draft",
                json={"content": {"text": "x"}, "base_version": 1},
                headers=qc,
            ).status_code
            == 403
        )
        assert (
            client.delete("/api/judge-rules/prompt_polarity/draft", headers=qc).status_code == 403
        )
        assert client.get("/api/judge-rules/prompt_polarity/draft", headers=qc).status_code == 200
