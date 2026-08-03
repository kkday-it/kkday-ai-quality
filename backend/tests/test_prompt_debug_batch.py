"""跑批的「首筆探測 → 收斂形狀 → 併發沿用」契約測試。

這條路徑先前零覆蓋，卻是整批正確性的樞紐：首筆探測走完整降級迴圈
（`prompt_debug._request_compat`）把 kwargs 就地收斂，`_settle_request_shape` 抽出收斂結果，
其餘所有 worker 直接沿用。收斂形狀一旦漏帶或帶錯，錯的不是一筆而是整批。
"""

from __future__ import annotations

import json

import pytest

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


# ── 多配置並行（create_and_start_group）────────────────────────────────────────


def _entry(name: str, model: str, provider: str, **knobs) -> dict:
    """組一筆 group entry（router `_parse_config_entries_form` + `_effective_or_400` 的產物形狀）。"""
    overrides = {"provider": provider, "model": model, **knobs}
    return {"config_name": name, "overrides": overrides, "effective": dict(overrides)}


def _fake_snapshot(run_id: str) -> dict:
    """`create_and_start` 回傳的**真實形狀**（`_public(_new_snapshot(...))` 的關鍵欄位）。

    ⚠️ **夾具形狀不真，斷言再多都是裝飾**：這裡最要緊的是帶上 `ok_count: 0`。
    2026-07-31 之前的假替身只回 `{"run_id", "status"}`，於是 `create_and_start_group` 那行
    `{**ident, "ok": True, **snapshot}` 在測試裡永遠不會發生 key 覆蓋——`assert all(m["ok"])`
    一路綠燈，而 production 每一次多模型跑批都因為 snapshot 自帶的 `ok`（累計成功筆數，
    新 run 恆為 0）把布林旗標吃掉而誤報「啟動失敗」，連群組輪詢都沒啟動。
    任何未來改動只要又把整包 snapshot 展開進成員，就會被這個形狀當場抓到。
    """
    return {"run_id": run_id, "status": "running", "ok_count": 0, "failed": 0, "processed": 0}


def _group_kwargs(**over) -> dict:
    """`create_and_start_group` 的固定樣板參數；各測試只覆寫 `entries`。"""
    return {
        "input_name": "t.csv",
        "input_bytes": b"x",
        "sheet": "",
        "id_column": "session_oid",
        "text_column": "conversation_full",
        "limit": 0,
        "workers": 1,
        "system_prompt": "",
        **over,
    }


def test_create_and_start_group_rejects_empty_entries() -> None:
    """entries 為空 → 明確拋錯，不靜默建一個空群組。"""
    with pytest.raises(ValueError, match="至少需選擇一個模型配置"):
        batch.create_and_start_group(**_group_kwargs(entries=[]))


def test_create_and_start_group_rejects_too_many_entries() -> None:
    """超過 `_MAX_ENTRIES_PER_GROUP` → 拒絕整個請求，不部分執行。"""
    entries = [
        _entry(f"cfg-{i}", f"model-{i}", "openai") for i in range(batch._MAX_ENTRIES_PER_GROUP + 1)
    ]
    with pytest.raises(ValueError, match="一次最多同時跑"):
        batch.create_and_start_group(**_group_kwargs(entries=entries))


def test_create_and_start_group_same_model_different_knobs_both_run(monkeypatch) -> None:
    """**同一個 model、不同旋鈕的兩筆配置，兩筆都要真的跑起來。**

    這是 entries 從「以 model 名為 key 的 dict」改成 list 的理由，也是它的回歸鎖：dict 形狀下
    兩筆會撞 key，後一筆靜默覆蓋前一筆——使用者選了 2 筆、只跑了 1 筆，而且不會收到任何提示。
    而「同 model 比不同 effort」正是具名配置最典型的用途。
    """
    calls: list[dict] = []

    def fake_create_and_start(**kwargs):
        calls.append(kwargs)
        return _fake_snapshot(f"run-{len(calls)}")

    monkeypatch.setattr(batch, "create_and_start", fake_create_and_start)

    result = batch.create_and_start_group(
        **_group_kwargs(
            entries=[
                _entry("省錢版", "gpt-5.4-mini", "openai", reasoning_effort="medium"),
                _entry("燒錢版", "gpt-5.4-mini", "openai", reasoning_effort="xhigh"),
            ]
        )
    )

    assert len(calls) == 2, "兩筆同 model 配置必須各起一個 run"
    assert [c["overrides"]["reasoning_effort"] for c in calls] == ["medium", "xhigh"]
    assert [c["config_name"] for c in calls] == ["省錢版", "燒錢版"]
    assert {m["run_id"] for m in result["members"]} == {"run-1", "run-2"}


def test_create_and_start_group_each_entry_carries_own_provider_and_knobs(monkeypatch) -> None:
    """每筆 entry 自帶完整 provider + 旋鈕，彼此不互相污染。

    這是缺陷⑤（多模型混用同一 provider/token）的回歸鎖：若 group 邏輯共用同一個 overrides
    dict 物件而非逐筆取自己的，後面那筆會覆寫前面那筆。
    """
    calls: list[dict] = []

    def fake_create_and_start(**kwargs):
        calls.append(kwargs)
        return _fake_snapshot(f"run-for-{kwargs['effective']['model']}")

    monkeypatch.setattr(batch, "create_and_start", fake_create_and_start)

    result = batch.create_and_start_group(
        **_group_kwargs(
            workers=2,
            entries=[
                _entry("OpenAI 省錢", "gpt-5.4-mini", "openai", thinking="default"),
                _entry("字節高階", "seed-2-0-lite-260428", "bytedance", thinking="enabled"),
            ],
        )
    )

    assert len(calls) == 2
    assert {c["overrides"]["model"] for c in calls} == {"gpt-5.4-mini", "seed-2-0-lite-260428"}
    # 旋鈕逐筆獨立（舊契約是全體共用一份 overrides，做不到這件事）
    assert {c["overrides"]["thinking"] for c in calls} == {"default", "enabled"}
    for c in calls:
        assert c["group_id"] == result["group_id"]

    by_name = {m["config_name"]: m for m in result["members"]}
    assert by_name["OpenAI 省錢"]["provider"] == "openai"
    assert by_name["字節高階"]["provider"] == "bytedance"
    assert all(m["started"] for m in result["members"])
    # ⚠️ 這條是本輪 bug 的回歸鎖：成員的 started 必須是**布林 True**，不能是 snapshot
    # 帶進來的 `ok_count`（整數 0）。用 `is True` 而非 truthy 判定，數字 1 也不放過。
    assert all(m["started"] is True for m in result["members"])
    assert all("ok_count" not in m for m in result["members"]), (
        "成員不得整包展開 snapshot——那正是布林旗標被計數欄位吃掉的原因"
    )


def test_create_and_start_group_one_entry_failure_does_not_block_others(monkeypatch) -> None:
    """一筆配置建 run 失敗（如該供應商沒配 token）不影響其餘配置繼續啟動——
    這是「一筆大量 429 不拖累另一筆」設計要求在**啟動階段**的對應驗證。
    """

    def fake_create_and_start(**kwargs):
        model = kwargs["effective"]["model"]
        if model == "broken-model":
            raise ValueError("目前配置沒有可用 API token")
        return _fake_snapshot(f"run-for-{model}")

    monkeypatch.setattr(batch, "create_and_start", fake_create_and_start)

    result = batch.create_and_start_group(
        **_group_kwargs(
            workers=2,
            entries=[
                _entry("壞掉的", "broken-model", "gemini"),
                _entry("正常的", "gpt-5.4-mini", "openai"),
            ],
        )
    )

    by_name = {m["config_name"]: m for m in result["members"]}
    assert by_name["壞掉的"]["started"] is False
    assert "token" in by_name["壞掉的"]["error"]
    # 失敗成員仍保有識別欄位（前端要據此標示「哪一筆沒跑起來」）
    assert by_name["壞掉的"]["model"] == "broken-model"
    assert by_name["壞掉的"]["provider"] == "gemini"
    assert by_name["正常的"]["started"] is True
    assert by_name["正常的"]["run_id"] == "run-for-gpt-5.4-mini"


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


def test_create_and_start_writes_group_id_and_config_name_only_when_given(
    tmp_path, monkeypatch
) -> None:
    """`group_id` / `config_name` 都是 `create_and_start` 的純附加欄位：帶了才寫進 manifest，
    不帶（腳本直呼）manifest 裡完全不出現該 key——不是「寫空字串」。

    `config_name` 存的是**名字快照**而非 config id：配置日後被改名或刪除，歷史 run 仍讀得懂
    「當時用的是什麼設定」，這正是具名配置改造要解的追溯問題。
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
        limit=0,
        workers=1,
        system_prompt="固定的測試 Prompt",
        overrides=None,
        effective={"model": "gpt-5.4-mini", "provider": "openai"},
        group_id="my-group",
        config_name="跑批·省錢",
    )
    manifest_with = _json.loads((tmp_path / snap["run_id"] / "manifest.json").read_text())
    assert manifest_with["group_id"] == "my-group"
    assert manifest_with["config_name"] == "跑批·省錢"

    snap2 = batch.create_and_start(
        input_name="t.csv",
        input_bytes=csv_bytes,
        sheet="",
        id_column="session_oid",
        text_column="conversation_full",
        limit=0,
        workers=1,
        system_prompt="固定的測試 Prompt",
        overrides=None,
        effective={"model": "gpt-5.4-mini", "provider": "openai"},
    )
    manifest_without = _json.loads((tmp_path / snap2["run_id"] / "manifest.json").read_text())
    assert "group_id" not in manifest_without
    assert "config_name" not in manifest_without


# ── 成功判準單一化 / 空 JSON 物件正規化 ────────────────────────────────────────


@pytest.mark.parametrize(
    ("record", "expected", "why"),
    [
        ({"parsed": {"L1": "x"}, "error": None}, True, "正常成功"),
        ({"parsed": {"L1": "x"}, "error": "boom"}, False, "有 error 一律失敗"),
        ({"parsed": None, "error": "bad"}, False, "解析失敗"),
        (
            {"parsed": {}, "error": None},
            False,
            "空 JSON 物件＝失敗（過去六處判準各說各話的那一格）",
        ),
        ({"parsed": [], "error": None}, False, "非 dict"),
        ({}, False, "空紀錄"),
    ],
)
def test_is_success_single_predicate(record: dict, expected: bool, why: str) -> None:
    """`_is_success` 是全模組唯一判準——尤其 `parsed == {}` 必須一致判為失敗。

    過去 `bool(parsed)` 與 `isinstance(parsed, dict)` 兩套判準並存於六處：即時進度算失敗
    （且 error 是空字串 → UI 顯示「未知錯誤」）、最終 CSV／preds 算成功、續跑認定已完成
    **永遠不重試**、重啟後 disk 推導又翻回成功。同一筆資料四種說法。
    """
    assert batch._is_success(record) is expected, why


def test_empty_json_object_is_normalized_to_failure_with_explicit_error() -> None:
    """模型吐出字面 `{}` → 源頭就標成 bad_output 且帶**非空** error。

    這是「未知錯誤」的第二個來源：`_bump` 的 failed_items 會寫 `record["error"] or ""`，
    源頭若留 `error=None`，UI 就只能顯示一個空白的紅框。
    """
    from app.judge.llm import client as llm_client

    assert llm_client._loads_lenient("{}") == {}, "前提：空物件是合法 JSON，不會回 None"
    record = {"parsed": {}, "error": "AI 輸出是空的 JSON 物件（{}），沒有任何欄位"}
    assert batch._is_success(record) is False
    assert record["error"].strip(), "失敗紀錄的 error 不得為空——那正是「未知錯誤」的來源"


# ── id_column 保留字守衛 ───────────────────────────────────────────────────────


def test_id_column_rejects_output_field_names() -> None:
    """ID 欄名撞輸出契約欄名 → 擋在寫入邊界。

    它被當動態 dict key 用（CSV 欄頭 + jsonl 紀錄），撞名會讓 `id_column: item_id` 被後面的
    字面 key 靜默覆蓋，該列的 item id 直接消失、續跑的斷點比對也一起失效。
    """
    for name in ("summary", "L1", "sentiment"):
        with pytest.raises(ValueError, match="與跑批輸出欄位同名"):
            batch._assert_id_column_free(name)


def test_id_column_rejects_record_field_names() -> None:
    """撞 jsonl 紀錄的固定欄名（model/status/error…）同樣擋下。"""
    for name in ("model", "status", "error", "cost_usd"):
        with pytest.raises(ValueError, match="與跑批輸出欄位同名"):
            batch._assert_id_column_free(name)


def test_id_column_allows_normal_and_item_id() -> None:
    """一般欄名放行；`item_id` 也放行——紀錄組裝處已針對它條件展開，是安全的。"""
    for name in ("session_oid", "order_id", "item_id", ""):
        batch._assert_id_column_free(name)


def test_group_rejects_reserved_id_column_before_starting_anything(monkeypatch) -> None:
    """群組跑批在**啟動任何 run 之前**就擋下——不要先跑掉一半才發現欄名有問題。"""
    started: list[dict] = []
    monkeypatch.setattr(batch, "create_and_start", lambda **kw: started.append(kw) or {})
    with pytest.raises(ValueError, match="與跑批輸出欄位同名"):
        batch.create_and_start_group(
            **_group_kwargs(id_column="summary", entries=[_entry("a", "gpt-5.4-mini", "openai")])
        )
    assert started == [], "校驗必須前置，不得已經起了 run 才報錯"


# ── _disk_summary：重啟後由磁碟推導 ────────────────────────────────────────────


def _write_jsonl(run_dir, records: list[dict]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "raw_results.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8"
    )


def _manifest(run_id: str = "run-x") -> dict:
    return {
        "run_id": run_id,
        "model": "gpt-5.4-mini",
        "input_name": "t.csv",
        "created_at": "2026-07-31T00:00:00+00:00",
        "id_column": "session_oid",
    }


def test_disk_summary_dedupes_by_id_so_rerun_does_not_overcount(tmp_path) -> None:
    """重跑不截斷 jsonl（同 id 會有多筆），磁碟推導必須依 id 取最後一筆。

    不去重的話，重跑過的 run 在 server 重啟後 `processed` 會超過輸入的實際筆數——
    使用者看到「處理 6 / 目標 3」這種不可能的數字。`_load_completed` 一直有去重，
    `_disk_summary` 過去沒有，兩邊對同一份檔案給出不同的總數。
    """
    run_dir = tmp_path / "run-x"
    _write_jsonl(
        run_dir,
        [
            {"session_oid": "a", "parsed": None, "error": "boom"},  # 第一次失敗
            {"session_oid": "a", "parsed": {"L1": "x"}, "error": None},  # 重跑後成功
            {"session_oid": "b", "parsed": {"L1": "y"}, "error": None},
        ],
    )
    snap = batch._disk_summary(run_dir, _manifest())
    assert snap["processed"] == 2, "3 行 jsonl 但只有 2 個 id"
    assert snap["ok_count"] == 2, "同 id 取最後一筆＝重跑後的成功"
    assert snap["failed"] == 0


def test_disk_summary_reports_unknown_total_as_none_not_zero(tmp_path) -> None:
    """中斷推導拿不到「本次選中總數」→ 回 None（未知），不是 0。

    回 0 會讓前端算出 0% 並在同一張卡片上並列顯示「目標 0」與「成功 2」，
    自相矛盾且看不出是「未知」還是「真的沒有」。
    """
    run_dir = tmp_path / "run-x"
    _write_jsonl(run_dir, [{"session_oid": "a", "parsed": {"L1": "x"}, "error": None}])
    snap = batch._disk_summary(run_dir, _manifest())
    assert snap["total"] is None
    assert snap["status"] == "interrupted"


def test_disk_summary_key_set_matches_live_snapshot(tmp_path) -> None:
    """磁碟推導與即時快照的**鍵集必須一致**。

    `get_run()` 回哪一種只取決於 in-mem 快照還在不在，鍵集不同會讓消費端隨機少欄位
    （過去 disk 版少了 `started_at` / `triggered_by`）。
    """
    run_dir = tmp_path / "run-x"
    _write_jsonl(run_dir, [{"session_oid": "a", "parsed": {"L1": "x"}, "error": None}])
    disk = batch._disk_summary(run_dir, _manifest())
    live = batch._public(batch._new_snapshot(_manifest(), total=1, resumed=0, pending=1))
    live["triggered_by"] = ""  # _launch 額外塞的欄位（見該函式）
    assert set(live) - set(disk) == set(), f"disk 版少了：{set(live) - set(disk)}"


def test_disk_summary_survives_corrupt_lines(tmp_path) -> None:
    """壞行只跳過該行、不整份放棄，且**留下 log**（過去是完全靜默）。"""
    run_dir = tmp_path / "run-x"
    run_dir.mkdir(parents=True)
    (run_dir / "raw_results.jsonl").write_text(
        '{"session_oid": "a", "parsed": {"L1": "x"}, "error": null}\n{ 這行壞掉\n',
        encoding="utf-8",
    )
    snap = batch._disk_summary(run_dir, _manifest())
    assert snap["ok_count"] == 1


# ── resume_run：檢查與啟動必須原子 ─────────────────────────────────────────────


def test_resume_run_holds_lock_across_check_and_launch(monkeypatch, tmp_path) -> None:
    """「檢查是否在跑 → 啟動」必須在同一把鎖內，否則兩個併發續跑會共用同一個 run_dir。

    後果不只是重複計費：後啟動的那條會覆寫 `_cancels[run_id]`，讓前一條**永遠取消不掉**；
    兩條各自 `_finalize`，後寫的蓋掉先寫的，results.csv 會真的掉資料。

    這裡用白箱方式驗證臨界區真的涵蓋「檢查」那一步——在 `_store.get` 被呼叫的當下嘗試
    非阻塞取鎖，取得到就代表檢查發生在鎖外（＝TOCTOU 窗口還在）。
    """
    observed: dict[str, bool] = {}
    real_get = batch._store.get

    def spy_get(run_id: str):
        if run_id == "run-lock-probe":
            acquired = batch._resume_lock.acquire(blocking=False)
            observed["lock_free_during_check"] = acquired
            if acquired:
                batch._resume_lock.release()
        return real_get(run_id)

    monkeypatch.setattr(batch._store, "get", spy_get)
    monkeypatch.setattr(batch, "read_manifest", lambda rid: _manifest(rid))
    monkeypatch.setattr(batch, "_prepare_plan", lambda *a, **k: pytest.fail("不該走到啟動"))

    # model 不符會在鎖內提早拋錯——足夠讓 `_store.get` 這一步跑到
    with pytest.raises(ValueError, match="與本 run 斷點不相容"):
        batch.resume_run("run-lock-probe", {"model": "另一個-model"})

    assert observed.get("lock_free_during_check") is False, (
        "檢查當下鎖必須是被持有的；能取到鎖＝檢查在鎖外，TOCTOU 窗口仍存在"
    )


# ── 併發自動化（_resolve_workers）──


def test_resolve_workers_auto_uses_per_model_table(monkeypatch) -> None:
    """全自動：per-model 查表 ∩ env 硬天花板 ∩ _WORKERS_CAP，不再吃使用者輸入。"""
    from app.judge import prejudge

    monkeypatch.setattr(prejudge, "max_workers_for", lambda m: {"slow": 8, "fast": 999}.get(m, 24))
    assert batch._resolve_workers("slow") == 8
    assert batch._resolve_workers("fast") == batch._WORKERS_CAP  # 被本模組硬上限夾住
    assert batch._resolve_workers("unlisted") == 24


def test_resolve_workers_override_still_honoured(monkeypatch) -> None:
    """腳本直呼仍可顯式指定；0/None＝自動（前端就是送 0）。"""
    from app.judge import prejudge

    monkeypatch.setattr(prejudge, "max_workers_for", lambda m: 24)
    assert batch._resolve_workers("m", 4) == 4
    assert batch._resolve_workers("m", 0) == 24
    assert batch._resolve_workers("m", None) == 24
    assert batch._resolve_workers("m", 9999) == batch._WORKERS_CAP  # 覆寫也不得越過硬上限
    assert batch._resolve_workers("m", -5) == 1  # 夾到下限，不會產生 0 併發的 executor


# ── 耗時統計（sessions.json）──


def test_sessions_accumulate_across_resume(tmp_path) -> None:
    """續跑的耗時要**累加**，不是被最後一段覆蓋——這是本功能存在的理由。"""
    run_dir = tmp_path / "run-x"
    run_dir.mkdir(parents=True)

    before = batch._open_session(run_dir, "2026-07-31T08:00:00+00:00")
    assert before == 0.0  # 第一段之前沒有累積
    batch._close_session(
        run_dir, finished_at="2026-07-31T08:00:30+00:00", status="cancelled", processed=5
    )

    before2 = batch._open_session(run_dir, "2026-07-31T09:00:00+00:00")
    assert before2 == 30.0  # 帶回第一段的 30 秒
    batch._close_session(
        run_dir, finished_at="2026-07-31T09:00:10+00:00", status="done", processed=9
    )

    sessions = batch._read_sessions(run_dir)
    assert len(sessions) == 2
    assert sum(batch._session_seconds(s) for s in sessions) == 40.0


def test_elapsed_total_is_not_wall_clock(tmp_path) -> None:
    """累計耗時只算「真的在跑」的時間；中斷後隔一小時才續跑，那一小時不該被算進去。"""
    run_dir = tmp_path / "run-x"
    run_dir.mkdir(parents=True)
    batch._open_session(run_dir, "2026-07-31T08:00:00+00:00")
    batch._close_session(
        run_dir, finished_at="2026-07-31T08:00:30+00:00", status="cancelled", processed=5
    )
    snapshot = {
        "started_at": "2026-07-31T09:00:00+00:00",
        "finished_at": "2026-07-31T09:00:10+00:00",
        "elapsed_before_sec": batch._open_session(run_dir, "2026-07-31T09:00:00+00:00"),
    }
    fields = batch._elapsed_fields(snapshot, run_dir)
    assert fields["elapsed_sec"] == 10.0  # 本段
    assert fields["elapsed_total_sec"] == 40.0  # 累計；牆鐘會是 3610 秒


def test_elapsed_fields_accepts_legacy_epoch_started_at(tmp_path) -> None:
    """改造前落盤的 run 其 started_at 是 epoch float；不該因為格式換了就整排顯示未知。"""
    run_dir = tmp_path / "run-x"
    run_dir.mkdir(parents=True)
    fields = batch._elapsed_fields(
        {"started_at": 1785239284.0, "finished_at": 1785239326.0}, run_dir
    )
    assert fields["elapsed_sec"] == 42.0


def test_elapsed_unknown_stays_none_not_zero(tmp_path) -> None:
    """未知≠0：中斷推導的 run 沒有本段起點，回 None 讓前端顯示「—」。"""
    run_dir = tmp_path / "run-x"
    run_dir.mkdir(parents=True)
    fields = batch._elapsed_fields({"started_at": None}, run_dir)
    assert fields["elapsed_sec"] is None
    assert fields["elapsed_total_sec"] is None


def test_close_session_is_idempotent(tmp_path) -> None:
    """重複收尾不得覆寫已收尾的段落（`_finalize` 在 error 路徑上可能被走到兩次）。"""
    run_dir = tmp_path / "run-x"
    run_dir.mkdir(parents=True)
    batch._open_session(run_dir, "2026-07-31T08:00:00+00:00")
    batch._close_session(
        run_dir, finished_at="2026-07-31T08:00:30+00:00", status="done", processed=5
    )
    batch._close_session(
        run_dir, finished_at="2026-07-31T10:00:00+00:00", status="error", processed=0
    )
    sessions = batch._read_sessions(run_dir)
    assert len(sessions) == 1
    assert sessions[0]["status"] == "done"
    assert sessions[0]["processed"] == 5


def test_read_sessions_survives_corrupt_file(tmp_path) -> None:
    """耗時是輔助資訊：檔案壞掉只讓耗時從本段重算，不該讓整個 run 讀不出來。"""
    run_dir = tmp_path / "run-x"
    run_dir.mkdir(parents=True)
    (run_dir / "sessions.json").write_text("{ 這行壞掉", encoding="utf-8")
    assert batch._read_sessions(run_dir) == []


# ── DB 取數（build_db_input_csv）──


def test_build_db_input_csv_requires_known_source() -> None:
    with pytest.raises(ValueError, match="未知的反饋來源"):
        batch.build_db_input_csv("nope", ["1"])


def test_build_db_input_csv_requires_ids() -> None:
    with pytest.raises(ValueError, match="沒有可用的 ID"):
        batch.build_db_input_csv("conversations", ["", "   "])


def test_build_db_input_csv_dedupes_and_reports_missing(monkeypatch) -> None:
    """保序去重；查無的 id 靜默消失是 `get_items_by_ids` 的行為，這裡必須算出來回報。"""
    from app.core import db

    monkeypatch.setattr(
        db,
        "get_items_by_ids",
        lambda ids, source: [
            {"session_oid": i, "conversation_full": f"對話 {i}"} for i in ids if i != "missing"
        ],
    )
    id_col, text_col, data, stats = batch.build_db_input_csv(
        "conversations", ["a", "b", "a", "missing"]
    )
    assert (id_col, text_col) == ("session_oid", "content")
    assert stats == {
        "requested": 3,  # a/b/missing（重複的 a 已去重）
        "found": 2,
        "missing": 1,
        "empty_conversations": 0,
        "valid_rows": 2,
    }
    body = data.decode("utf-8")
    assert body.splitlines()[0] == "session_oid,content"


def test_build_db_input_csv_skips_empty_conversations(monkeypatch) -> None:
    """內容為空的列不送判（送了只會白燒一次呼叫），但要計數。"""
    from app.core import db

    monkeypatch.setattr(
        db,
        "get_items_by_ids",
        lambda ids, source: [
            {"session_oid": "a", "conversation_full": "有內容"},
            {"session_oid": "b", "conversation_full": ""},
        ],
    )
    _, _, _, stats = batch.build_db_input_csv("conversations", ["a", "b"])
    assert stats["empty_conversations"] == 1
    assert stats["valid_rows"] == 1


def test_build_db_input_csv_rejects_all_empty_batch(monkeypatch) -> None:
    """整批撈不到東西要當場拒絕，不要讓使用者等一個註定是空的批。"""
    from app.core import db

    monkeypatch.setattr(db, "get_items_by_ids", lambda ids, source: [])
    with pytest.raises(ValueError, match="撈不到任何可跑的對話"):
        batch.build_db_input_csv("conversations", ["a", "b"])


def test_build_db_input_csv_chunks_large_id_lists(monkeypatch) -> None:
    """分塊查：單次 IN (...) 塞數千個 bind param 會撞 Postgres 參數上限。"""
    from app.core import db

    seen_chunk_sizes: list[int] = []

    def _fake(ids, source):
        seen_chunk_sizes.append(len(ids))
        return [{"session_oid": i, "conversation_full": "x"} for i in ids]

    monkeypatch.setattr(db, "get_items_by_ids", _fake)
    batch.build_db_input_csv("conversations", [str(i) for i in range(1200)])
    assert seen_chunk_sizes == [500, 500, 200]
