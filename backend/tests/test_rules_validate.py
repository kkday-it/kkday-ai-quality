"""判決規則存檔前驗證（rules._validate）——鎖 judgment 分支的結構驗證，防誤刪靜默改變行為。

judgment 分支（連同 product_vertical/source_mapping/prompt_* 分支）皆各自 return，不觸 DB，
故純函式可測、免 DB。judgment 已移出 RULE_CODES（`_check_code` 會先擋 404），此處直呼
`_validate` 繞過該檢查以鎖純驗證邏輯本身。
"""

import json

import pytest
from fastapi import HTTPException

from app.api.routers.rules import _validate
from app.core.paths import AI_JUDGE_DIR


@pytest.fixture
def judgment_seed() -> dict:
    """檔案默認 judgment（config/ai_judge/judgment.json）——合法基準，逐項破壞驗其被擋。"""
    return json.loads((AI_JUDGE_DIR / "judgment.json").read_text(encoding="utf-8"))


def test_valid_judgment_passes(judgment_seed) -> None:
    """完整 judgment.json（含 confidence_tiers + auto_confirm）通過驗證不拋。"""
    _validate("judgment", judgment_seed)  # 不應拋


def test_missing_confidence_tiers_rejected(judgment_seed) -> None:
    """缺 confidence_tiers → 422（信心閾值為判決分層核心，不可缺）。"""
    bad = {k: v for k, v in judgment_seed.items() if k != "confidence_tiers"}
    with pytest.raises(HTTPException) as e:
        _validate("judgment", bad)
    assert e.value.status_code == 422


def test_missing_auto_confirm_rejected(judgment_seed) -> None:
    """缺 auto_confirm → 422：防 QC 誤刪後下游 _auto_confirm_cfg 靜默退回 enabled=True 重開自動確認。"""
    bad = {k: v for k, v in judgment_seed.items() if k != "auto_confirm"}
    with pytest.raises(HTTPException) as e:
        _validate("judgment", bad)
    assert e.value.status_code == 422


def test_auto_confirm_enabled_must_be_bool(judgment_seed) -> None:
    """auto_confirm.enabled 非 bool（如字串 'true'）→ 422。"""
    bad = {**judgment_seed, "auto_confirm": {"enabled": "true", "audit_sample_rate": 0.05}}
    with pytest.raises(HTTPException) as e:
        _validate("judgment", bad)
    assert e.value.status_code == 422


@pytest.mark.parametrize("rate", [-0.1, 1.5, "0.05", None])
def test_audit_sample_rate_out_of_range_or_non_number_rejected(judgment_seed, rate) -> None:
    """audit_sample_rate 越界（<0 / >1）或非數值 → 422（抽樣比例必為 0~1）。"""
    bad = {**judgment_seed, "auto_confirm": {"enabled": True, "audit_sample_rate": rate}}
    with pytest.raises(HTTPException) as e:
        _validate("judgment", bad)
    assert e.value.status_code == 422


def test_audit_sample_rate_boundaries_pass(judgment_seed) -> None:
    """audit_sample_rate 邊界 0 與 1 皆合法（全不抽樣 / 全抽樣）。"""
    for rate in (0, 1, 0.5):
        _validate(
            "judgment",
            {**judgment_seed, "auto_confirm": {"enabled": False, "audit_sample_rate": rate}},
        )
