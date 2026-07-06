"""confidence-gated ensemble 接線（prejudge._ensemble_attrs）測試：monkeypatch voter 判決，不呼叫真 LLM。"""
from contextlib import nullcontext

from app.judge import prejudge


def test_ensemble_skipped_when_all_high_conf(monkeypatch):
    """主判決全高信心（≥ auto_accept）→ 不跑任何 voter、回原 attrs + 空票（省 token）。"""
    called: list[int] = []
    monkeypatch.setattr(prejudge, "_resolve_attrs_multi", lambda *a, **k: called.append(1) or [])
    high = [{"l1_domain_code": "content", "confidence": 0.95, "l3_code": "C-1-1-1"}]
    attrs, votes = prejudge._ensemble_attrs({}, "t", high, "nano", [{"model": "gemini"}])
    assert attrs == high and votes == []
    assert called == []  # 高信心 → voter 完全沒被呼叫


def test_ensemble_triggers_on_low_conf(monkeypatch):
    """主判決有低信心 attr → 各 voter 換 config 複判 + 投票合併；votes 攤平含主判決 + 所有 voter。"""
    monkeypatch.setattr(
        prejudge,
        "_resolve_attrs_multi",
        lambda *a, **k: [{"l1_domain_code": "content", "confidence": 0.7, "l3_code": "C-1-1-1"}],
    )
    monkeypatch.setattr(prejudge, "_use_config", lambda cfg: nullcontext())
    low = [{"l1_domain_code": "content", "confidence": 0.6, "l3_code": "C-1-1-1"}]
    attrs, votes = prejudge._ensemble_attrs({}, "t", low, "nano", [{"model": "gemini"}, {"model": "seed"}])
    assert attrs[0]["l1_domain_code"] == "content"
    assert len(votes) == 3
    assert {v["model"] for v in votes} == {"nano", "gemini", "seed"}


def test_ensemble_falls_back_when_all_disputed(monkeypatch):
    """全分歧（各 voter 判不同域、無過半域）→ merged 空 → 保底回原 attrs（不因 ensemble 丟失判決）。"""
    seq = iter([
        [{"l1_domain_code": "supplier", "confidence": 0.7, "l3_code": ""}],  # voter1
        [{"l1_domain_code": "quality", "confidence": 0.7, "l3_code": ""}],  # voter2
    ])
    monkeypatch.setattr(prejudge, "_resolve_attrs_multi", lambda *a, **k: next(seq))
    monkeypatch.setattr(prejudge, "_use_config", lambda cfg: nullcontext())
    base = [{"l1_domain_code": "content", "confidence": 0.6, "l3_code": "C-1-1-1"}]
    attrs, votes = prejudge._ensemble_attrs({}, "t", base, "nano", [{"model": "gemini"}, {"model": "seed"}])
    # 3 voter 各 1/3（content/supplier/quality）皆 < 0.5 → 全丟棄 → 保底回原 attrs
    assert attrs == base
    assert len(votes) == 3


def test_sample_hit_deterministic():
    """④抽樣命中 deterministic：rate=0 全不中、rate=1 全中；同筆多次一致；rate 單調（中小的必中大的）。"""
    it = {"source": "product_reviews", "source_id": "rec_123"}
    assert prejudge._sample_hit(it, 0.0) is False
    assert prejudge._sample_hit(it, 1.0) is True
    assert prejudge._sample_hit(it, 0.5) == prejudge._sample_hit(it, 0.5)  # 同筆一致
    if prejudge._sample_hit(it, 0.3):
        assert prejudge._sample_hit(it, 0.9)  # 單調：命中 0.3 必命中 0.9


def test_ensemble_sampling_audits_high_conf(monkeypatch):
    """④抽樣稽核：高信心筆平時不 ensemble，但 sample_rate=1 命中 → 也跑 voter；rate=0 不跑。"""
    called: list[int] = []
    monkeypatch.setattr(
        prejudge,
        "_resolve_attrs_multi",
        lambda *a, **k: called.append(1) or [{"l1_domain_code": "content", "confidence": 0.9, "l3_code": "C-1-1-1"}],
    )
    monkeypatch.setattr(prejudge, "_use_config", lambda cfg: nullcontext())
    high = [{"l1_domain_code": "content", "confidence": 0.95, "l3_code": "C-1-1-1"}]
    _, votes = prejudge._ensemble_attrs({"source_id": "x"}, "t", high, "nano", [{"model": "gemini"}], sample_rate=1.0)
    assert len(votes) == 2 and called == [1]  # 主 + 1 voter（抽樣命中觸發）
    called.clear()
    _, votes0 = prejudge._ensemble_attrs({"source_id": "x"}, "t", high, "nano", [{"model": "gemini"}], sample_rate=0.0)
    assert votes0 == [] and called == []  # 高信心 + 未抽樣 → 不跑


def test_attr_effort_reads_config(monkeypatch):
    """① reasoning_effort 旋鈕：judgment.json prejudge.attribute_reasoning_effort 讀取（null→None＝不 override）。"""
    monkeypatch.setattr(prejudge, "_prejudge_cfg", lambda: {"attribute_reasoning_effort": "low"})
    assert prejudge._attr_effort() == "low"
    monkeypatch.setattr(prejudge, "_prejudge_cfg", lambda: {})
    assert prejudge._attr_effort() is None


def test_call_effort_override_and_restore(monkeypatch):
    """_call 傳 effort → 呼叫期間 current 帶該 reasoning_effort；呼叫後還原（thread-local 安全）。"""
    from app.core import settings as app_settings

    seen: dict = {}
    monkeypatch.setattr(prejudge.client, "chat_json", lambda *a, **k: seen.update(app_settings.current()) or {})
    before = dict(app_settings.current())
    prejudge._call("s", "u", "attribute", before.get("model", ""), effort="low")
    assert seen.get("reasoning_effort") == "low"  # 呼叫期間帶 low
    assert app_settings.current().get("reasoning_effort") == before.get("reasoning_effort")  # 已還原
