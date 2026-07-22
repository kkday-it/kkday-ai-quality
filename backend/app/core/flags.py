"""OpenFeature 標準旗標介面（初判閾值）+ judge_rule_versions 薄 provider。

面向 **OpenFeature 標準避免供應商鎖定**（計畫核心工程原則）：初判閾值（confidence_tiers：auto_accept
/ jury_low / jury_high）讀取一律走 OpenFeature client（`threshold()`→`get_float_value`），backing
provider（JudgeConfigProvider）解析到 prejudge 初判配置的 confidence_tiers（靜態設定檔，讀取
走 `db._shared.read_pipeline_config` 單一入口）。Phase 7 換 Flagsmith 只需 `api.set_provider(新 provider)`，
呼叫端零改。

閾值以 module 級 cache 持有（初判熱路徑高頻讀，避免每 finding 打 DB）；規則管理存檔後由
rules._reload_judge_cache 呼叫 `reload()` 清 cache 即時生效（對齊 ai_judge/prejudge）。
"""

from __future__ import annotations

from openfeature import api
from openfeature.flag_evaluation import FlagResolutionDetails, Reason
from openfeature.provider import AbstractProvider
from openfeature.provider.metadata import Metadata

# 旗標命名：judge.<tier>（auto_accept / jury_low / jury_high）。
_FLAG_PREFIX = "judge."
# provider 無值時的內建預設（對齊 prejudge.json/verdict.json confidence_tiers）。
_DEFAULT_TIERS: dict[str, float] = {"auto_accept": 0.8, "jury_low": 0.5, "jury_high": 0.7}

# 閾值 cache（prejudge.json confidence_tiers；lazy，reload() 清空重載）。
_tiers_cache: dict | None = None


def _tiers() -> dict:
    """取 prejudge confidence_tiers（靜態設定檔，缺則內建預設）；module 級快取。"""
    global _tiers_cache
    if _tiers_cache is None:
        from app.core.db import _shared  # lazy：避免 core → core.db import 期成環

        cfg = _shared.read_pipeline_config()
        _tiers_cache = cfg.get("confidence_tiers") or dict(_DEFAULT_TIERS)
    return _tiers_cache


class JudgeConfigProvider(AbstractProvider):
    """薄 OpenFeature provider：judge.<tier> 旗標 → prejudge confidence_tiers（靜態設定檔）。

    僅 float（初判閾值）為本 provider 職責；其餘型別回 default（DEFAULT reason），不越權。
    """

    def get_metadata(self) -> Metadata:
        return Metadata(name="judge-config")

    def resolve_float_details(self, flag_key, default_value, evaluation_context=None):
        """judge.<tier> → confidence_tiers[tier]（缺 / 非數值 → default）。"""
        name = flag_key[len(_FLAG_PREFIX) :] if flag_key.startswith(_FLAG_PREFIX) else flag_key
        v = _tiers().get(name)
        if isinstance(v, (int, float)):
            return FlagResolutionDetails(value=float(v), reason=Reason.STATIC)
        return FlagResolutionDetails(value=float(default_value), reason=Reason.DEFAULT)

    def resolve_boolean_details(self, flag_key, default_value, evaluation_context=None):
        return FlagResolutionDetails(value=default_value, reason=Reason.DEFAULT)

    def resolve_string_details(self, flag_key, default_value, evaluation_context=None):
        return FlagResolutionDetails(value=default_value, reason=Reason.DEFAULT)

    def resolve_integer_details(self, flag_key, default_value, evaluation_context=None):
        return FlagResolutionDetails(value=default_value, reason=Reason.DEFAULT)

    def resolve_object_details(self, flag_key, default_value, evaluation_context=None):
        return FlagResolutionDetails(value=default_value, reason=Reason.DEFAULT)


_registered = False


def _client():
    """取 OpenFeature client（首次呼叫註冊 JudgeConfigProvider）。"""
    global _registered
    if not _registered:
        api.set_provider(JudgeConfigProvider())
        _registered = True
    return api.get_client()


def threshold(name: str, default: float) -> float:
    """取初判閾值（走 OpenFeature 標準介面；provider 讀 prejudge 靜態配置，缺則 default）。

    Args:
        name: tier 名（auto_accept / jury_low / jury_high）。
        default: provider 無值時回退。
    """
    return _client().get_float_value(f"{_FLAG_PREFIX}{name}", default)


def reload() -> None:
    """清閾值 cache（規則管理存檔後呼叫，使新閾值即時反映於初判）。"""
    global _tiers_cache
    _tiers_cache = None
