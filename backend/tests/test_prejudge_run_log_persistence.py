"""批量初判的執行日誌逐筆落庫：25 筆（> LOG_LIVE_MAX_ITEMS）也要每則評論各一列。

這是「批量初判查不到 LLM 執行日誌」的回歸測試——舊設計用批量筆數當閘門（>20 筆完全不收），
本測試以超過閘門的批量跑完整 `_run` 流程，驗證每則評論都有自己的日誌列可回看。
LLM 與落庫皆 monkeypatch 掉：這裡驗的是日誌管線的接線，不是初判結果本身。
"""

from __future__ import annotations

import pytest

from app.core import db
from app.judge import prejudge, prejudge_batch, run_log

_BATCH = 25  # > run_log.LOG_LIVE_MAX_ITEMS（20），舊設計在此筆數完全不收日誌


@pytest.fixture
def _fake_judge(monkeypatch):
    """把「撈 item / 跑 LLM / 落庫歸因」換成假的，只留日誌管線真跑。"""
    items = [{"item_id": f"rev_{i}", "rec_oid": f"rev_{i}"} for i in range(_BATCH)]
    monkeypatch.setattr(db, "get_items_by_ids", lambda ids, source=None: list(items))
    monkeypatch.setattr(prejudge, "to_findings", lambda norm, model, versions=None: [])
    monkeypatch.setattr(db, "replace_source_findings", lambda *a, **kw: None)
    monkeypatch.setattr(prejudge_batch, "_reload_judge_rules", lambda: None)
    return [it["item_id"] for it in items]


def test_batch_over_live_gate_still_persists_per_item_logs(temp_db, _fake_judge):
    """25 筆批量：每則評論一列日誌 + 一列 job 級事件，且記憶體緩衝已清空。"""
    job_id = "pj_test_batch_log"
    prejudge_batch._run(job_id, _fake_judge, eff={}, model="stub", source="reviews")

    rows = db.get_run_log(job_id)
    assert rows is not None, "批量初判必須留下執行日誌"
    assert len(rows["items"]) == _BATCH, "每則評論都該有自己的日誌列"
    assert all(it["count"] > 0 for it in rows["items"])

    # job 級事件（任務啟動）獨立成列：整批視角讀得到，且不混進任何評論
    assert any(e["message"].startswith("初判任務啟動") for e in rows["entries"])

    # 記憶體：SSE 佇列不建（超過 live 閘門），逐筆緩衝已被 take 清乾淨
    assert run_log.read(job_id)[2] is False
    assert run_log.take(job_id, _fake_judge[0]) == []


def test_single_item_log_is_addressable_by_source_id(temp_db, _fake_judge):
    """帶 source_id 只回該則評論（附 job 級脈絡）——前端抽屜的實際讀法。"""
    job_id = "pj_test_batch_log2"
    prejudge_batch._run(job_id, _fake_judge, eff={}, model="stub", source="reviews")

    one = db.get_run_log(job_id, "rev_7")
    assert one is not None
    stamped = [e for e in one["entries"] if e.get("item_id")]
    assert stamped and {e["item_id"] for e in stamped} == {"rev_7"}, "不得混入其他評論的條目"
    assert one["truncated"] is False
