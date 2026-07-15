"""P3/P4：依 model 的批次併發上限（max_workers_for）+ 單域有界重試（_attrs_pack domain_retry）。"""

from __future__ import annotations

import pytest

from app.judge import prejudge


def _mock_pack_env(monkeypatch, fake_call) -> None:
    """把 _attrs_pack 依賴全數 mock 到最小（2 域、空 schema、no-op sleep），只留重試邏輯受測。"""
    import app.judge.prompt_source as ps

    monkeypatch.setattr(ps, "DOMAIN_PROMPT_IDS", ("01_C-1_content", "03_C-3_supplier"))
    monkeypatch.setattr(
        ps,
        "load",
        lambda pid: {"system": f"SYS::{pid}", "user_template": "{POLARITY}{TEXT}", "schema": {}},
    )
    monkeypatch.setattr(prejudge, "_l2_label_map", lambda: {})
    monkeypatch.setattr(prejudge, "_attr_effort", lambda: None)
    monkeypatch.setattr(prejudge, "_finalize_attr_l2", lambda item, text, a, valid: a)
    monkeypatch.setattr("app.judge.llm.client.is_stub", lambda: False)
    monkeypatch.setattr(prejudge, "_call", fake_call)
    monkeypatch.setattr("time.sleep", lambda *_: None)  # 重試延遲 no-op（測試不等待）


def test_attrs_pack_single_domain_bounded_retry(monkeypatch) -> None:
    """單域第一次失敗、第二次成功 → 該域結果仍併入（domain_retry 止血，僅重打該域）。"""
    monkeypatch.setattr(prejudge, "_domain_retry", lambda: 1)
    calls: dict[str, int] = {}

    def _fake_call(system, user, stage, model, *, schema, effort, label=None):
        calls[system] = calls.get(system, 0) + 1
        if "03_C-3" in system and calls[system] == 1:
            raise RuntimeError("boom")
        return {"attributions": [{"l2_code": "X", "confidence": 0.9}]}

    _mock_pack_env(monkeypatch, _fake_call)
    out = prejudge._attrs_pack({"item_id": "i"}, "文字", "gpt-5-mini", 6, "negative")
    assert len(out) == 2  # 兩域各一條（C-3 靠重試補上）
    assert calls["SYS::03_C-3_supplier"] == 2 and calls["SYS::01_C-1_content"] == 1  # 只重打失敗域


def test_attrs_pack_domain_exhausts_retry_raises(monkeypatch) -> None:
    """單域連續失敗至耗盡 → 整筆 fail-loud（拋出，交批次層計 failed）。"""
    monkeypatch.setattr(prejudge, "_domain_retry", lambda: 1)

    def _fake_call(system, user, stage, model, *, schema, effort, label=None):
        if "03_C-3" in system:
            raise RuntimeError("persistent")
        return {"attributions": [{"l2_code": "X", "confidence": 0.9}]}

    _mock_pack_env(monkeypatch, _fake_call)
    with pytest.raises(RuntimeError, match="persistent"):
        prejudge._attrs_pack({"item_id": "i"}, "文字", "gpt-5-mini", 6, "negative")


def test_domain_retry_reads_config(monkeypatch) -> None:
    """_domain_retry 讀 judgment.json prejudge.domain_retry；缺則預設 1；負值/None 夾 0。"""
    monkeypatch.setattr(prejudge, "_prejudge_cfg", lambda: {"domain_retry": 2})
    assert prejudge._domain_retry() == 2
    monkeypatch.setattr(prejudge, "_prejudge_cfg", lambda: {})
    assert prejudge._domain_retry() == 1  # 缺 → 預設
    monkeypatch.setattr(prejudge, "_prejudge_cfg", lambda: {"domain_retry": 0})
    assert prejudge._domain_retry() == 0  # 0＝關閉


# ── P5：AIMD 自適應併發 governor ──
def test_governor_aimd_backoff_and_probe(monkeypatch) -> None:
    """樂觀起於 ceiling；429 乘性收縮（cooldown 內只一次）；清空 probe_interval 加性回升；floor 夾住。"""
    from app.judge import prejudge_batch as pb

    clock = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    g = pb._ConcurrencyGovernor(64, floor=2, backoff=0.5, probe_interval_s=3.0, cooldown_s=5.0)
    assert g.current() == 64  # 樂觀起步＝ceiling

    g.on_429()
    assert g.current() == 32  # 乘性收縮 ×0.5
    clock[0] = 1001
    g.on_429()  # cooldown(至 1005) 內 → 不反應
    assert g.current() == 32
    clock[0] = 1006
    g.on_429()  # cooldown 過 → 再收縮
    assert g.current() == 16
    clock[0] = 1009  # 清空 probe_interval(3s) 無 429 → +1 回升
    assert g.current() == 17
    clock[0] = 1012
    assert g.current() == 18

    g2 = pb._ConcurrencyGovernor(3, floor=2, backoff=0.5, cooldown_s=0.0)
    g2.on_429()
    assert g2.current() == 2  # int(3*0.5)=1 → floor 夾到 2；ceiling 也夾（不超過 3）


def test_adaptive_concurrency_config(monkeypatch) -> None:
    """adaptive_concurrency 讀 judgment.json；預設 enabled=True、backoff=0.5、probe=3、floor=2。"""
    monkeypatch.setattr(prejudge, "_prejudge_cfg", lambda: {})
    d = prejudge.adaptive_concurrency()
    assert d == {"enabled": True, "backoff": 0.5, "probe_interval_s": 3.0, "floor": 2}
    monkeypatch.setattr(
        prejudge, "_prejudge_cfg", lambda: {"adaptive_concurrency": {"enabled": False, "floor": 4}}
    )
    d = prejudge.adaptive_concurrency()
    assert d["enabled"] is False and d["floor"] == 4


def test_max_workers_for_table_hit_and_default(monkeypatch) -> None:
    """查表命中回該 model 值；缺該 model 回 max_workers_default；缺整表回硬 fallback 32。"""
    monkeypatch.setattr(
        prejudge,
        "_prejudge_cfg",
        lambda: {
            "max_workers_by_model": {"gpt-5.5": 16, "gpt-5-mini": 64},
            "max_workers_default": 24,
        },
    )
    assert prejudge.max_workers_for("gpt-5.5") == 16
    assert prejudge.max_workers_for("gpt-5-mini") == 64
    assert prejudge.max_workers_for("unknown-model") == 24  # 缺該 model → default

    monkeypatch.setattr(prejudge, "_prejudge_cfg", lambda: {})  # 缺整個表
    assert prejudge.max_workers_for("anything") == 32  # 硬 fallback（與呼叫端 env 天花板無關）


# ── P2：失敗筆明細 snapshot + 隱式重撈上限 ──
def test_bump_records_failed_items_with_cap(monkeypatch) -> None:
    """_bump(ok=False) 記錄 item 明細；超過 _MAX_FAILED_ITEMS 只計數並設 failed_items_truncated。"""
    from app.judge import prejudge_batch as pb

    jid = "test_job_bump"
    pb._jobs[jid] = pb._new_snapshot(total=5, model="m")
    try:
        pb._bump(jid, ok=False, item_id="i1", source_id="s1", error="boom")
        pb._bump(jid, ok=True)
        snap = pb.get_job(jid)
        assert (snap["failed"], snap["ok"], snap["processed"]) == (1, 1, 2)
        assert snap["failed_items"] == [{"item_id": "i1", "source_id": "s1", "error": "boom"}]
        assert snap["failed_items_truncated"] is False
        monkeypatch.setattr(pb, "_MAX_FAILED_ITEMS", 1)  # 已滿 → 下一筆只計數
        pb._bump(jid, ok=False, item_id="i2", error="boom2")
        snap = pb.get_job(jid)
        assert len(snap["failed_items"]) == 1 and snap["failed_items_truncated"] is True
    finally:
        pb._jobs.pop(jid, None)


def test_capped_source_ids_after_consecutive_failures(temp_db) -> None:
    """最新成功後連續失敗 ≥ N → 入 capped；成功事件後只算其後失敗（歸零）；從未成功者算全部 failure。"""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import insert as sa_insert

    from app.core.db import prejudge_targets as pt
    from app.core.db import tables as T

    src = "product_reviews"
    base = datetime(2026, 7, 14, tzinfo=timezone.utc)

    def ev(sid: str, kind: str, minute: int) -> None:
        with T.get_engine().begin() as c:
            c.execute(
                sa_insert(T.judgment_history).values(
                    source=src,
                    source_id=sid,
                    kind=kind,
                    created_at=base + timedelta(minutes=minute),
                )
            )

    for i in range(3):  # A：3 連續失敗、從未成功 → capped（>=3）
        ev("A", "failure", i)
    for k, m in [("failure", 0), ("failure", 1), ("judgment", 2), ("failure", 3)]:
        ev("B", k, m)  # B：2 失敗→成功→1 失敗；成功後僅 1 failure < 3 → 不 capped
    with T.get_engine().connect() as c:
        assert pt._capped_source_ids(c, src, 3) == {"A"}
        assert pt._capped_source_ids(c, src, 2) == {"A"}  # B 成功後只 1 失敗，仍 < 2
        assert pt._capped_source_ids(c, src, 1) == {"A", "B"}  # A、B 成功後皆 ≥1 失敗
        assert pt._capped_source_ids(c, src, 0) == set()  # <1＝停用
