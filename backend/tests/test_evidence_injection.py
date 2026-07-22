"""佐證注入鏈（prejudge × qc_evidence）單元測試——LLM/DB 全打樁。

S5 範圍：{ORDER_SNAPSHOT} 填槽 no-op 語義、摘要器截斷/缺欄容錯、
_fetch_order_snapshot_digest 各分支（stub/無 ref 域/候選剪枝/取數失敗降級）。
"""

from __future__ import annotations

import pytest

from app.core.db.qc_evidence import EvidenceResult
from app.judge import prejudge


def _sample_data() -> dict:
    """組一份 get_evidence 成功 payload 樣板。"""
    return {
        "order": {
            "order_mid": "26KK299300111",
            "order_status": "BACK",
            "price_pay": 74.37,
            "crt_dt": "2026-05-02T14:00:32+00:00",
            "lst_dt_go": "2026-06-28T00:00:00+00:00",
            "timezone": "Asia/Tokyo",
            "lang_code": "zh-hk",
            "prod_desc": "北海道富良野一日遊",
            "package_name": "富田農場彩虹花田方案",
        },
        "product_setting": {"description_module": {"PMDL_NOTICE": {"text": "當天不可退"}}},
        "pkg_basic": {
            "cancel_policy_client": {"policy_type": "3"},
            "tour_duration": {"hour": 10},
        },
        "supplier": {"supplier_name": "S社", "order_handler": "KKDAY", "msg_handler": "KKDAY"},
        "meta": {"lang": "zh-hk", "fetched_at": "2026-07-22T03:00:00+00:00", "cache": {}},
    }


# ── _render_pack_user ────────────────────────────────────────────────────────────────────
def test_render_pack_user_fills_snapshot_slot():
    """有槽模板：{ORDER_SNAPSHOT} 被替換為佐證文字。"""
    out = prejudge._render_pack_user(
        "T:{TEXT} P:{POLARITY} S:{ORDER_SNAPSHOT}", "評論", "negative", "佐證內容"
    )
    assert out == "T:評論 P:negative S:佐證內容"


def test_render_pack_user_noop_without_slot():
    """無槽模板（如 C-2 / polarity）：傳 order_snapshot 零副作用（.replace no-op）。"""
    out = prejudge._render_pack_user("T:{TEXT} P:{POLARITY}", "評論", "negative", "佐證內容")
    assert out == "T:評論 P:negative"
    assert "佐證內容" not in out


def test_render_pack_user_empty_snapshot_leaves_empty_slot():
    """無佐證（空字串）：槽位替換為空——域 prompt 依「為空時依原文判斷」降級措辭。"""
    out = prejudge._render_pack_user("S:[{ORDER_SNAPSHOT}]", "t", "negative")
    assert out == "S:[]"


# ── _summarize_evidence ──────────────────────────────────────────────────────────────────
def test_summarize_evidence_contains_key_fields():
    """摘要含訂單/商品/退改/供應商關鍵欄位。"""
    digest = prejudge._summarize_evidence(_sample_data())
    assert "26KK299300111" in digest
    assert "富田農場" in digest
    assert "退改政策" in digest
    assert "S社" in digest
    assert digest.startswith("【訂單佐證")


def test_summarize_evidence_total_cap():
    """總長封頂（summary.max_total_chars）：超長內容被截斷不爆 prompt。"""
    from app.core.db import qc_evidence

    total = int(qc_evidence.summary_cfg().get("max_total_chars", 1800))
    data = _sample_data()
    data["product_setting"]["description_module"] = {"PMDL_X": {"text": "很長" * 5000}}
    digest = prejudge._summarize_evidence(data)
    assert len(digest) <= total


def test_summarize_evidence_tolerates_missing_sections():
    """缺欄容錯：只有 order 區塊也能產出，不拋錯。"""
    digest = prejudge._summarize_evidence({"order": {"order_mid": "26KK1"}})
    assert "26KK1" in digest


# ── _fetch_order_snapshot_digest ─────────────────────────────────────────────────────────
@pytest.fixture()
def _not_stub(monkeypatch):
    """打樁 client.is_stub=False（測試環境無 token 時預設 stub 會短路取數）。"""
    monkeypatch.setattr(prejudge.client, "is_stub", lambda: False)


def test_fetch_digest_stub_short_circuits(monkeypatch):
    """stub 模式不取佐證（無法真歸因，省 production 點查）。"""
    monkeypatch.setattr(prejudge.client, "is_stub", lambda: True)
    assert prejudge._fetch_order_snapshot_digest({"order_oid": "1"}, None) == ("", "", "")


def test_fetch_digest_success(monkeypatch, _not_stub):
    """取數成功：回（摘要, status, fetched_at）；status 透傳 fetched/cache_hit。"""
    from app.core.db import qc_evidence

    monkeypatch.setattr(
        qc_evidence, "get_evidence", lambda oid: EvidenceResult("fetched", _sample_data())
    )
    digest, status, at = prejudge._fetch_order_snapshot_digest({"order_oid": "47406070"}, None)
    assert status == "fetched"
    assert "26KK299300111" in digest
    assert at == "2026-07-22T03:00:00+00:00"


def test_fetch_digest_no_order_oid(_not_stub):
    """item 無 order_oid → ("", no_order_oid, "")——與取數失敗語義分離。"""
    assert prejudge._fetch_order_snapshot_digest({"raw": {}}, None) == ("", "no_order_oid", "")


def test_fetch_digest_degraded_passthrough(monkeypatch, _not_stub):
    """取數降級（degraded_unavailable/not_found/error）：空摘要 + status 透傳（判決照走）。"""
    from app.core.db import qc_evidence

    monkeypatch.setattr(
        qc_evidence, "get_evidence", lambda oid: EvidenceResult("degraded_unavailable")
    )
    digest, status, at = prejudge._fetch_order_snapshot_digest({"order_oid": "1"}, None)
    assert (digest, status, at) == ("", "degraded_unavailable", "")


def test_fetch_digest_skips_when_candidates_not_evidence_ref(monkeypatch, _not_stub):
    """域路由候選全不在 evidence_ref 清單（如僅 C-2）→ 不取（省 production 點查）。"""
    from app.core.db import qc_evidence

    def _boom(oid):  # 不應被呼叫
        raise AssertionError("should not fetch")

    monkeypatch.setattr(qc_evidence, "get_evidence", _boom)
    out = prejudge._fetch_order_snapshot_digest({"order_oid": "1"}, ["02_C-2_quality"])
    assert out == ("", "", "")


def test_fetch_digest_fetches_when_candidate_in_ref(monkeypatch, _not_stub):
    """候選含 evidence_ref 域（如 C-1）→ 正常取數。"""
    from app.core.db import qc_evidence

    monkeypatch.setattr(
        qc_evidence, "get_evidence", lambda oid: EvidenceResult("cache_hit", _sample_data())
    )
    digest, status, _ = prejudge._fetch_order_snapshot_digest(
        {"order_oid": "1"}, ["01_C-1_content", "02_C-2_quality"]
    )
    assert status == "cache_hit"
    assert digest
