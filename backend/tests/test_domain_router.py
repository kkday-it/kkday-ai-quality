"""域路由（domain_router）+ 剪枝管線掛點 + 等價指標 確定性測試——全程不需 LLM key / 不碰 DB。

鎖三層安全防線的 code-side 行為：
- decide 行為矩陣（enabled × shadow_rate）與 fail-open（權重缺失 / embedding 失敗 → 不剪枝）。
- _candidates 閾值/always_on/保底 top-1。
- _resolve_attrs_multi 兜底補跑（候選全空手 → 補跑其餘域，最壞＝全跑）。
- report_shadow best-effort（DB 掛掉不拋）。
- compute_equivalence_metrics 純函式口徑。
"""

from __future__ import annotations

import pytest

from app.judge import domain_router, prejudge
from app.judge.prompt_eval import compute_equivalence_metrics

# 域詞彙表＝機器值（content/supplier…＝judgments.l1_code 同詞彙表；C-x 碼只在檔名/日誌）
_WEIGHTS = {
    "version": 1,
    "embedding_model": "text-embedding-3-small",
    "dim": 3,
    "domains": {
        "content": {"coef": [10.0, 0.0, 0.0], "intercept": 0.0, "threshold": 0.5},
        "supplier": {"coef": [0.0, 10.0, 0.0], "intercept": 0.0, "threshold": 0.5},
        "service": {
            "coef": [0.0, 0.0, 10.0],
            "intercept": -20.0,
            "threshold": None,
        },  # 無閾值＝恆跑
    },
}


@pytest.fixture
def router_env(monkeypatch):
    """固定權重 + embedding + 域→pid 映射（與檔案系統 / LLM / prompt_source 解耦）。"""
    monkeypatch.setattr(domain_router, "_load_weights", lambda: dict(_WEIGHTS))
    monkeypatch.setattr(
        domain_router,
        "_dom_pid_map",
        lambda: {
            "content": "01_C-1_content",
            "supplier": "03_C-3_supplier",
            "service": "05_C-5_service",
        },
    )


def _cfg(monkeypatch, **kw):
    cfg = {"enabled": False, "shadow_rate": 0.0, "embedding_model": "m", **kw}
    monkeypatch.setattr(domain_router, "_router_cfg", lambda: cfg)


# ── decide 行為矩陣 ─────────────────────────────────────────────────────
def test_decide_disabled_zero_overhead(monkeypatch) -> None:
    """關閉且無 shadow → 不算 embedding（零開銷）、不剪枝。"""
    _cfg(monkeypatch, enabled=False, shadow_rate=0.0)

    def _boom(*a, **k):  # embedding 被呼叫即失敗——證明零開銷
        raise AssertionError("embedding 不應被呼叫")

    monkeypatch.setattr(domain_router, "_load_weights", _boom)
    d = domain_router.decide("text", "negative")
    assert d.pids is None and d.shadow is False


def test_decide_enabled_prunes_by_threshold(router_env, monkeypatch) -> None:
    """開啟：機率過閾值的域 + 無閾值域（視同 always_on）入候選。"""
    _cfg(monkeypatch, enabled=True)
    # vec=[1,0,0] → content sigmoid(10)≈1 過閾；supplier sigmoid(0)=0.5 過閾(≥0.5)；service 無閾值恆入
    monkeypatch.setattr(domain_router.client, "embed_one", lambda text, model: [1.0, 0.0, 0.0])
    d = domain_router.decide("text", "negative")
    assert d.pids == ["01_C-1_content", "03_C-3_supplier", "05_C-5_service"]
    # vec=[1,-1,0] → supplier sigmoid(-10)≈0 被剪；content 過閾；service 恆入
    monkeypatch.setattr(domain_router.client, "embed_one", lambda text, model: [1.0, -1.0, 0.0])
    d = domain_router.decide("text", "negative")
    assert d.pids == ["01_C-1_content", "05_C-5_service"]
    assert d.shadow is False and d.probs is not None


def test_decide_always_on_union_and_top1_floor(router_env, monkeypatch) -> None:
    """config always_on 併入候選；全域都被剪時保底 top-1（v1 不做零域跳過）。"""
    weights = {
        "version": 1,
        "dim": 3,
        "domains": {
            "content": {"coef": [10.0, 0.0, 0.0], "intercept": -20.0, "threshold": 0.9},
            "supplier": {"coef": [0.0, 10.0, 0.0], "intercept": -20.0, "threshold": 0.9},
        },
    }
    monkeypatch.setattr(domain_router, "_load_weights", lambda: weights)
    monkeypatch.setattr(domain_router.client, "embed_one", lambda text, model: [1.0, 0.5, 0.0])
    # 全被剪 → 保底 argmax（content 的 z=-10 > supplier 的 z=-15）
    _cfg(monkeypatch, enabled=True)
    d = domain_router.decide("text", "negative")
    assert d.pids == ["01_C-1_content"]
    # always_on 併入
    _cfg(monkeypatch, enabled=True, always_on=["supplier"])
    d = domain_router.decide("text", "negative")
    assert d.pids == ["01_C-1_content", "03_C-3_supplier"]


def test_decide_fail_open(router_env, monkeypatch) -> None:
    """embedding 掛 / 權重缺失 → 不剪枝（fail-open），絕不拋。"""
    _cfg(monkeypatch, enabled=True)
    monkeypatch.setattr(domain_router.client, "embed_one", lambda text, model: None)
    assert domain_router.decide("t", "negative").pids is None
    monkeypatch.setattr(domain_router, "_load_weights", lambda: None)
    assert domain_router.decide("t", "negative").pids is None


def test_decide_shadow_forces_full_run_semantics(router_env, monkeypatch) -> None:
    """shadow 抽中：仍算出 pids（供虛擬比對）但 shadow=True——呼叫端據此全跑。"""
    _cfg(monkeypatch, enabled=True, shadow_rate=1.0)
    monkeypatch.setattr(domain_router.client, "embed_one", lambda text, model: [1.0, 0.0, 0.0])
    d = domain_router.decide("text", "negative")
    assert d.shadow is True and d.pids is not None


def test_shadow_missed_same_vocabulary(router_env) -> None:
    """回歸鎖（審查確認 P1）：候選域與命中域必須同詞彙表（域機器值）比對——
    路由選對域時 missed 必為空；真漏域才進 missed。"""
    # 路由候選=supplier(pid 03_C-3_supplier)，實際命中 supplier → 漏域必為空（修正前恆假陽性）
    d = domain_router.RouterDecision(pids=["03_C-3_supplier"], shadow=True, probs=None)
    cand, hit, missed = domain_router._shadow_missed(d, [{"l1_domain_code": "supplier"}])
    assert cand == {"supplier"} and hit == {"supplier"} and missed == []
    # 真漏域：命中 content 但候選只有 supplier → missed=[content]
    _, _, missed = domain_router._shadow_missed(d, [{"l1_domain_code": "content"}])
    assert missed == ["content"]


def test_report_shadow_swallows_db_errors(router_env, monkeypatch) -> None:
    """影子留痕 best-effort：DB 不可用也不拋（絕不阻斷判決）。"""
    from app.core.db import tables as T

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(T, "get_engine", _boom)
    d = domain_router.RouterDecision(pids=["01_C-1_content"], shadow=True, probs={"content": 0.9})
    domain_router.report_shadow(  # 不應拋出
        d, [{"l1_domain_code": "supplier"}], source="product_reviews", source_id="X"
    )


# ── 剪枝管線掛點（_resolve_attrs_multi 兜底補跑）────────────────────────
def test_resolve_attrs_multi_prune_no_fallback_when_hit(monkeypatch) -> None:
    """候選域有產出 → 只跑候選（不補跑其餘域）。"""
    monkeypatch.setattr(prejudge.client, "is_stub", lambda: False)
    calls: list = []

    def _fake_pack(item, text, model, max_n, polarity="negative", *, versions=None, pids=None):
        calls.append(pids)
        return [{"l1_domain_code": "content", "l2_code": "C-1-1", "confidence": 0.9}]

    monkeypatch.setattr(prejudge, "_attrs_pack", _fake_pack)
    monkeypatch.setattr(prejudge, "_evidence_policy", lambda: {})
    out = prejudge._resolve_attrs_multi({}, "t", "m", 3, candidate_pids=["01_C-1_content"])
    assert len(out) == 1 and calls == [["01_C-1_content"]]  # 單次呼叫、只帶候選


def test_resolve_attrs_multi_prune_fallback_reruns_rest(monkeypatch) -> None:
    """候選域全空手 → 兜底補跑其餘域（合流再過閘門）；最壞＝現行全跑。"""
    monkeypatch.setattr(prejudge.client, "is_stub", lambda: False)
    calls: list = []

    def _fake_pack(item, text, model, max_n, polarity="negative", *, versions=None, pids=None):
        calls.append(pids)
        if pids == ["01_C-1_content"]:  # 候選域空手
            return []
        return [{"l1_domain_code": "supplier", "l2_code": "C-3-2", "confidence": 0.8}]

    monkeypatch.setattr(prejudge, "_attrs_pack", _fake_pack)
    monkeypatch.setattr(prejudge, "_evidence_policy", lambda: {})
    out = prejudge._resolve_attrs_multi({}, "t", "m", 3, candidate_pids=["01_C-1_content"])
    assert [a["l1_domain_code"] for a in out] == ["supplier"]
    assert len(calls) == 2 and calls[0] == ["01_C-1_content"]  # 第二趟＝其餘域
    from app.judge import prompt_source

    assert set(calls[1]) == set(prompt_source.DOMAIN_PROMPT_IDS) - {"01_C-1_content"}


def test_resolve_attrs_multi_no_prune_no_fallback(monkeypatch) -> None:
    """未帶候選（candidate_pids=None，路由關閉）→ 單次全域呼叫、零行為變更。"""
    monkeypatch.setattr(prejudge.client, "is_stub", lambda: False)
    calls: list = []

    def _fake_pack(item, text, model, max_n, polarity="negative", *, versions=None, pids=None):
        calls.append(pids)
        return []

    monkeypatch.setattr(prejudge, "_attrs_pack", _fake_pack)
    monkeypatch.setattr(prejudge, "_evidence_policy", lambda: {})
    assert prejudge._resolve_attrs_multi({}, "t", "m", 3) == []
    assert calls == [None]  # 不剪枝：單趟、pids=None（全 6 域）、空手也不補跑


# ── 等價指標（P0 閘門口徑）─────────────────────────────────────────────
def test_compute_equivalence_metrics_perfect_and_divergent() -> None:
    """完全一致 → 全 1.0；分歧案例 → 各指標按口徑計。"""
    same = {
        "polarity": "negative",
        "sentiment": 1,
        "n_findings": 2,
        "facets": [["supplier", "C-3-2"], ["service", "C-5-1"]],
        "primary": ["supplier", "C-3-2"],
    }
    m = compute_equivalence_metrics([{"a": same, "b": dict(same)}])
    assert m["polarity_agree"] == 1.0 and m["facet_jaccard_mean"] == 1.0
    assert m["count_equal"] == 1.0 and m["primary_agree"] == 1.0 and m["count_mae"] == 0.0
    # 分歧：facets 交 1 聯 3 → jaccard 1/3；count 2 vs 1；primary 不同
    diff_b = {
        "polarity": "negative",
        "sentiment": 2,
        "n_findings": 1,
        "facets": [["supplier", "C-3-2"], ["content", "C-1-1"]],
        "primary": ["content", "C-1-1"],
    }
    m = compute_equivalence_metrics([{"a": same, "b": diff_b}])
    assert m["polarity_agree"] == 1.0 and m["sentiment_agree"] == 0.0
    assert m["count_equal"] == 0.0 and m["count_mae"] == 1.0
    assert m["facet_jaccard_mean"] == pytest.approx(1 / 3, abs=1e-4)
    assert m["primary_agree"] == 0.0
    # 兩邊皆空 findings → jaccard=1、primary 皆 None＝一致
    empty = {"polarity": "positive", "sentiment": 5, "n_findings": 0, "facets": [], "primary": None}
    m = compute_equivalence_metrics([{"a": empty, "b": dict(empty)}])
    assert m["facet_jaccard_mean"] == 1.0 and m["primary_agree"] == 1.0
