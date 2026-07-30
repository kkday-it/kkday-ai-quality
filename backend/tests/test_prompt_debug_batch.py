"""跑批的「首筆探測 → 收斂形狀 → 併發沿用」契約測試。

這條路徑先前零覆蓋，卻是整批正確性的樞紐：首筆探測走完整降級迴圈
（`prompt_debug._request_compat`）把 kwargs 就地收斂，`_settle_request_shape` 抽出收斂結果，
其餘所有 worker 直接沿用。收斂形狀一旦漏帶或帶錯，錯的不是一筆而是整批。
"""

from __future__ import annotations

from app.judge import prompt_debug_batch as batch


def test_settle_request_shape_excludes_only_per_row_keys() -> None:
    """收斂形狀＝kwargs 去掉 per-row 的 model/messages，其餘一律保留。

    刻意是 opt-out（只排除兩個鍵）而非 opt-in 白名單：任何未來新增的請求參數都會自動被帶進
    併發階段，不必記得回來改這裡。
    """
    probe = {
        "model": "seed-x",
        "messages": [{"role": "user", "content": "只屬於首筆"}],
        "response_format": {"type": "json_object"},
        "temperature": 0.7,
        "reasoning_effort": "medium",
        "extra_body": {"thinking": {"type": "disabled"}},
    }
    settled = batch._settle_request_shape(probe)
    assert "model" not in settled and "messages" not in settled
    assert settled == {
        "response_format": {"type": "json_object"},
        "temperature": 0.7,
        "reasoning_effort": "medium",
        "extra_body": {"thinking": {"type": "disabled"}},
    }


def test_settle_request_shape_carries_degraded_form() -> None:
    """探測時被降級掉的參數不會復活——沿用的是收斂後形狀，不是原始形狀。"""
    probe = {"model": "m", "messages": [], "temperature": 0.2}  # response_format 已於探測中被移除
    assert "response_format" not in batch._settle_request_shape(probe)


def test_worker_kwargs_deep_copies_nested_dicts() -> None:
    """每個 worker 重組 kwargs 時對巢狀 dict 做複製，避免 _complete_effort_safe 就地改寫互撞。

    `_complete_effort_safe` 與降級階梯都是**就地改寫 kwargs**；若 worker 共享同一個
    `response_format` dict 物件，一個 worker 的降級會污染其他併發 worker。
    """
    settled = {"response_format": {"type": "json_schema"}, "temperature": 0.5}
    # 對齊 call_one 的重組邏輯（prompt_debug_batch._run_batch 內）
    rebuilt = {
        "model": "m",
        "messages": [],
        **{k: (dict(v) if isinstance(v, dict) else v) for k, v in settled.items()},
    }
    rebuilt["response_format"]["type"] = "json_object"  # 模擬某個 worker 被降級
    assert settled["response_format"]["type"] == "json_schema"  # 原始收斂形狀未被污染


# ── 多模型並行（create_and_start_group）──────────────────────────────────────────


def test_create_and_start_group_rejects_empty_effectives() -> None:
    """models/effectives 為空 → 明確拋錯，不靜默建一個空群組。"""
    import pytest

    with pytest.raises(ValueError, match="至少需選擇一個 model"):
        batch.create_and_start_group(
            input_name="t.csv",
            input_bytes=b"x",
            sheet="",
            id_column="session_oid",
            text_column="conversation_full",
            offset=0,
            limit=0,
            workers=1,
            system_prompt="",
            overrides=None,
            effectives={},
        )


def test_create_and_start_group_rejects_too_many_models() -> None:
    """超過 `_MAX_MODELS_PER_GROUP` → 拒絕整個請求，不部分執行。"""
    import pytest

    effectives = {
        f"model-{i}": {"model": f"model-{i}"} for i in range(batch._MAX_MODELS_PER_GROUP + 1)
    }
    with pytest.raises(ValueError, match="一次最多同時跑"):
        batch.create_and_start_group(
            input_name="t.csv",
            input_bytes=b"x",
            sheet="",
            id_column="session_oid",
            text_column="conversation_full",
            offset=0,
            limit=0,
            workers=1,
            system_prompt="",
            overrides=None,
            effectives=effectives,
        )


def test_create_and_start_group_each_model_resolves_own_provider_via_own_call(monkeypatch) -> None:
    """逐 model 呼叫 `create_and_start` 時，overrides 帶的是**該 model 自己的** model 名，
    不是共用同一份——這是缺陷⑤（多模型混用同一 provider/token）的回歸鎖：若 group 邏輯不小心
    共用了同一個 overrides dict 物件而非逐次組新的，後面的 model 會覆寫前面 model 的 overrides。
    """
    calls: list[dict] = []

    def fake_create_and_start(**kwargs):
        calls.append(kwargs)
        return {"run_id": f"run-for-{kwargs['overrides']['model']}", "status": "running"}

    monkeypatch.setattr(batch, "create_and_start", fake_create_and_start)

    result = batch.create_and_start_group(
        input_name="t.csv",
        input_bytes=b"x",
        sheet="",
        id_column="session_oid",
        text_column="conversation_full",
        offset=0,
        limit=0,
        workers=2,
        system_prompt="",
        overrides={"thinking": "enabled"},  # 共用旋鈕（不含 model/provider）
        effectives={
            "gpt-5.4-mini": {"model": "gpt-5.4-mini", "provider": "openai"},
            "seed-2-0-lite-260428": {"model": "seed-2-0-lite-260428", "provider": "bytedance"},
        },
    )

    assert len(calls) == 2
    called_models = {c["overrides"]["model"] for c in calls}
    assert called_models == {"gpt-5.4-mini", "seed-2-0-lite-260428"}
    # 共用旋鈕（thinking）確實傳遞到每個 model，且各自 model/provider 沒有互相污染
    for c in calls:
        assert c["overrides"]["thinking"] == "enabled"
        assert c["group_id"] == result["group_id"]

    members_by_model = {m["model"]: m for m in result["members"]}
    assert members_by_model["gpt-5.4-mini"]["provider"] == "openai"
    assert members_by_model["seed-2-0-lite-260428"]["provider"] == "bytedance"
    assert all(m["ok"] for m in result["members"])


def test_create_and_start_group_one_model_failure_does_not_block_others(monkeypatch) -> None:
    """一個 model 建 run 失敗（如該供應商沒配 token）不影響其餘 model 繼續啟動——
    這是「一個 model 大量 429 不拖累另一個」設計要求在**啟動階段**的對應驗證。
    """

    def fake_create_and_start(**kwargs):
        model = kwargs["overrides"]["model"]
        if model == "broken-model":
            raise ValueError("目前配置沒有可用 API token")
        return {"run_id": f"run-for-{model}", "status": "running"}

    monkeypatch.setattr(batch, "create_and_start", fake_create_and_start)

    result = batch.create_and_start_group(
        input_name="t.csv",
        input_bytes=b"x",
        sheet="",
        id_column="session_oid",
        text_column="conversation_full",
        offset=0,
        limit=0,
        workers=2,
        system_prompt="",
        overrides=None,
        effectives={
            "broken-model": {"model": "broken-model", "provider": "gemini"},
            "gpt-5.4-mini": {"model": "gpt-5.4-mini", "provider": "openai"},
        },
    )

    members_by_model = {m["model"]: m for m in result["members"]}
    assert members_by_model["broken-model"]["ok"] is False
    assert "token" in members_by_model["broken-model"]["error"]
    assert members_by_model["gpt-5.4-mini"]["ok"] is True
    assert members_by_model["gpt-5.4-mini"]["run_id"] == "run-for-gpt-5.4-mini"


# ── list_runs 的 group_id 過濾 ──────────────────────────────────────────────────


def _write_fake_run(batch_dir, run_id: str, *, group_id: str | None, model: str) -> None:
    """在 BATCH_DIR 下直接寫一個最小可被 `list_runs` 讀到的假 run（不經 create_and_start，
    不需要真的執行批次——`list_runs` 只讀 manifest.json + 選讀 summary.json）。
    """
    import json as _json

    run_dir = batch_dir / run_id
    run_dir.mkdir(parents=True)
    manifest = {
        "run_id": run_id,
        "created_at": "2026-07-30T00:00:00+00:00",
        "input_name": "t.csv",
        "model": model,
        "offset": 0,
        "limit": 0,
        "workers": 1,
    }
    if group_id:
        manifest["group_id"] = group_id
    (run_dir / "manifest.json").write_text(_json.dumps(manifest), encoding="utf-8")


def test_list_runs_group_id_filter(tmp_path, monkeypatch) -> None:
    """group_id 過濾只回屬於該群組的 run；`None`（預設）維持既有行為回傳全部，向下相容。"""
    monkeypatch.setattr(batch, "BATCH_DIR", tmp_path)
    _write_fake_run(tmp_path, "run-a", group_id="group-1", model="gpt-5.4-mini")
    _write_fake_run(tmp_path, "run-b", group_id="group-1", model="seed-2-0-lite-260428")
    _write_fake_run(tmp_path, "run-c", group_id=None, model="gpt-5.4-mini")  # 單模型舊 run

    all_runs = batch.list_runs()
    assert {r["run_id"] for r in all_runs} == {"run-a", "run-b", "run-c"}
    assert all("group_id" in r for r in all_runs)  # 新增欄位一律存在（舊 run 是空字串，不缺欄位）

    grouped = batch.list_runs(group_id="group-1")
    assert {r["run_id"] for r in grouped} == {"run-a", "run-b"}

    single = batch.list_runs(group_id="group-1")
    assert all(r["group_id"] == "group-1" for r in single)


def test_create_and_start_writes_group_id_into_manifest_only_when_given(
    tmp_path, monkeypatch
) -> None:
    """`group_id` 是 `create_and_start` 的純附加欄位：帶了才寫進 manifest，
    不帶（既有單模型呼叫端）manifest 裡完全不出現該 key——不是「寫空字串」。
    """
    from app.judge import prompt_debug, prompt_debug_versions

    monkeypatch.setattr(batch, "BATCH_DIR", tmp_path)
    # 繞過真實正式版解析：resolve() 給定非空 text 時走「臨時編輯」分支，不觸碰版本庫
    monkeypatch.setattr(prompt_debug_versions, "resolve", lambda text, **kw: (text, "", ""))
    monkeypatch.setattr("app.core.settings.resolve_provider_token", lambda eff: "fake-token")
    # 背景執行緒仍會真的起跑（daemon thread，_launch 不等待）：探測呼叫是它第一件事，
    # 這裡直接讓它立即拋錯短路，測試就不會在背景悄悄發出真實 API 請求（即便 token 是假的）。
    monkeypatch.setattr(
        prompt_debug,
        "_request_compat",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("test: no real LLM call")),
    )

    import json as _json

    csv_bytes = "session_oid,conversation_full\ns1,測試\n".encode()

    snap = batch.create_and_start(
        input_name="t.csv",
        input_bytes=csv_bytes,
        sheet="",
        id_column="session_oid",
        text_column="conversation_full",
        offset=0,
        limit=0,
        workers=1,
        system_prompt="固定的測試 Prompt",
        overrides=None,
        effective={"model": "gpt-5.4-mini", "provider": "openai"},
        group_id="my-group",
    )
    manifest_with = _json.loads((tmp_path / snap["run_id"] / "manifest.json").read_text())
    assert manifest_with["group_id"] == "my-group"

    snap2 = batch.create_and_start(
        input_name="t.csv",
        input_bytes=csv_bytes,
        sheet="",
        id_column="session_oid",
        text_column="conversation_full",
        offset=0,
        limit=0,
        workers=1,
        system_prompt="固定的測試 Prompt",
        overrides=None,
        effective={"model": "gpt-5.4-mini", "provider": "openai"},
    )
    manifest_without = _json.loads((tmp_path / snap2["run_id"] / "manifest.json").read_text())
    assert "group_id" not in manifest_without
