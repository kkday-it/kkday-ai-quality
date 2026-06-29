"""LLM 模型配置持久化（per-user，存 DB user_settings 表）。

前端「設定」面板管理：provider / model / base_url / api_token / temperature /
thinking / reasoning_effort / provider_models（各供應商自訂 model 清單）。
api_token 絕不明文回前端（masked()）；raw() 供「眼睛顯示全文」。空/遮罩值 save 不覆蓋既有 token。

每個 user 一份設定。judge 路徑（llm client）不傳 user_id，而是透過 contextvar `current()`
取「當前 request 端點注入的 user 設定」——避免污染 pipeline/classify/adequacy 的函式簽名。
"""

from __future__ import annotations

import contextvars
import json
from pathlib import Path

from app.core import db

# 跨語言共用的「非機密」預設值單一真相源（與前端 @config/defaults.json 同一檔）。
# parents[3]：settings.py = backend/app/core/settings.py → repo 根。
_DEFAULTS_PATH = Path(__file__).resolve().parents[3] / "config" / "defaults.json"
_SHARED: dict = json.loads(_DEFAULTS_PATH.read_text(encoding="utf-8"))
# QC DB 連線預設（host/port/name/schema）；main.py 連線測試的 port fallback 亦取此。
QC_DB_DEFAULTS: dict = _SHARED["qc_db"]
# LLM model 下拉的最低版本門檻（僅 gpt-* 受限）；/api/settings/models 動態清單過濾用。
LLM_MODEL_MIN_VERSION: str = _SHARED["llm"].get("modelMinVersion", "5.4")
# LLM 供應商目錄（id/base_url/defaultModels）；model 下拉清單 SSOT，list_models() 讀此（不打 /v1/models）。
LLM_PROVIDERS: list = _SHARED["llm"].get("providers", [])

# 機密欄位：回前端一律遮罩，空/遮罩值 save 不覆蓋既有（避免遮罩值回寫清空真值）
_SECRET_KEYS = ("api_token", "qc_db_password")

_DEFAULT: dict = {
    "provider": "openai",  # openai | gemini | azure | custom
    "model": "gpt-5.4-mini",  # 對齊 config/defaults.json defaultModels（gpt-5-mini 不在清單且被 modelMinVersion 過濾→靜默打不通）
    "base_url": "",  # 空＝OpenAI 預設端點
    "api_token": "",
    "temperature": None,  # None＝用 API 預設（gpt-5 系列鎖定不送）
    "thinking": "default",  # default | on | off
    "reasoning_effort": "default",  # default | none | low | medium | high | xhigh
    "provider_models": {},  # 各供應商自訂 model 清單（per-user 累積）
    # ── 資料來源：QC DB（PostgreSQL）連線配置（前端「資料來源配置」面板管理）──
    # host/name 留空＝要求顯式設定才連線（前端表單以 config/defaults.json 預填）；
    # schema 預設取共用檔，與前端一致。
    "qc_db_host": "",
    "qc_db_port": None,  # 空＝連線時回退 QC_DB_DEFAULTS["port"]（_try_qc_db_connect）
    "qc_db_name": "",
    "qc_db_schema": QC_DB_DEFAULTS["schema"],
    "qc_db_user": "",
    "qc_db_password": "",  # 機密：比照 api_token 遮罩、不入 git
}

# 當前 request 生效的 user 設定（端點 Depends 注入）；judge 路徑經 current() 讀取
_current: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "current_settings", default=None
)


def load_settings(user_id: str) -> dict:
    """讀某 user 完整設定（含明文 token）；未存過回 _DEFAULT 複本。"""
    data = db.load_user_settings(user_id)
    return {**_DEFAULT, **data} if data else dict(_DEFAULT)


def save_settings(user_id: str, patch: dict) -> dict:
    """合併寫入某 user。空或遮罩(*** / …)的 api_token 不覆蓋既有，回 masked()。"""
    cur = load_settings(user_id)
    for k, v in patch.items():
        if k not in _DEFAULT:
            continue
        if k in _SECRET_KEYS and (not v or "***" in str(v) or "…" in str(v)):
            continue
        cur[k] = v
    db.save_user_settings(user_id, cur)
    return masked(user_id)


def masked(user_id: str) -> dict:
    """回傳給前端：機密欄位（api_token / qc_db_password）遮罩，附 has_token / has_qc_db_password 旗標。"""
    cur = load_settings(user_id)
    # has_* 旗標需在遮罩前由真值計算
    cur["has_token"] = bool(cur.get("api_token"))
    cur["has_qc_db_password"] = bool(cur.get("qc_db_password"))
    for sk in _SECRET_KEYS:
        tok = cur.get(sk, "") or ""
        cur[sk] = (tok[:7] + "…" + tok[-4:]) if len(tok) > 12 else ("***" if tok else "")
    return cur


def raw(user_id: str) -> dict:
    """完整未遮罩配置（含明文 api_token / qc_db_password）——供設定面板「眼睛顯示全文」。

    ⚠️ 明文回傳機密欄位：僅應在受信任的本地 / 內網環境暴露此端點。
    """
    cur = load_settings(user_id)
    cur["has_token"] = bool(cur.get("api_token"))
    cur["has_qc_db_password"] = bool(cur.get("qc_db_password"))
    return cur


def set_current(settings: dict) -> None:
    """端點注入當前 request 的 user 設定，供 judge 路徑（llm client）讀取。"""
    _current.set(settings)


def current() -> dict:
    """judge 路徑取當前生效設定；未注入時回 _DEFAULT 複本（→ stub 模式）。"""
    s = _current.get()
    return s if s is not None else dict(_DEFAULT)
