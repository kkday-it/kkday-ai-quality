"""機密 at-rest 加密（Fernet 對稱加密；key 來自 backend/.env 的 AIQ_SECRET_KEY）。

user_settings 的 llm_tokens / qc_passwords 落庫前加密、讀出後解密——邊界統一在
app/core/settings.py 的 load/save，其餘模組（judge 路徑、端點）永遠只見明文，零改動。

設計：
- 密文帶 ``enc:v1:`` 前綴，與明文舊資料可區分 → 加密冪等（已加密不重複加密）、
  舊明文列可直通讀取（漸進遷移，migrate 腳本見 scripts/tools/encrypt_user_secrets.py）。
- AIQ_SECRET_KEY 為任意字串 passphrase，SHA-256 derive 成 Fernet 金鑰；未設定時
  明文直通並 warn 一次（dev 可回滾：回滾前先跑遷移腳本 --decrypt 把密文轉回明文）。
- 解密失敗（key 遺失/換過）回空字串並記 error，避免把 ``enc:...`` 垃圾值當 token 送出。
"""

from __future__ import annotations

import base64
import hashlib
import logging
from functools import lru_cache

from app.core.config import env, is_production

log = logging.getLogger(__name__)

# 密文前綴（含版本號，未來換演算法可升 v2 並存解讀）
ENC_PREFIX = "enc:v1:"

# 啟動即檢查：非 development 環境缺 AIQ_SECRET_KEY → 拒絕啟動。
# 未設 key 時 encrypt_secret 明文直通（見下），正式環境將令 provider token / QC 密碼明文落庫。
# 本模組於啟動時由 settings.py（→ settings_router）import → 啟動即觸發，與 auth.py 的 JWT 閘對稱。
if not (env.aiq_secret_key or "").strip() and is_production():
    raise RuntimeError(
        f"APP_ENV={env.app_env} 為正式環境，必須設定 AIQ_SECRET_KEY；"
        "拒絕以明文儲存 user_settings 機密（provider token / QC 密碼）。"
        '生成：python -c "import secrets; print(secrets.token_urlsafe(32))"'
    )


@lru_cache(maxsize=1)
def _fernet():
    """由 AIQ_SECRET_KEY derive Fernet；未設定回 None（明文直通模式）。

    lru_cache：Fernet 建構含 key 驗證，process 內只需一次；測試改 key 後
    需 ``_fernet.cache_clear()``。
    """
    secret = (env.aiq_secret_key or "").strip()
    if not secret:
        log.warning(
            "AIQ_SECRET_KEY 未設定，user_settings 機密將以明文落庫"
            "（設定後跑 scripts/tools/encrypt_user_secrets.py 補加密既有列）"
        )
        return None
    from cryptography.fernet import Fernet  # lazy：未啟用加密時不強制依賴

    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def is_encrypted(value: object) -> bool:
    """是否為本模組產出的密文（enc:v1: 前綴）。"""
    return isinstance(value, str) and value.startswith(ENC_PREFIX)


def encrypt_secret(value: str) -> str:
    """明文機密 → 密文（帶前綴）。空值 / 已加密 / 未設 key → 原樣返回（冪等 + 可回滾）。"""
    if not value or is_encrypted(value):
        return value
    f = _fernet()
    if f is None:
        return value
    return ENC_PREFIX + f.encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    """密文 → 明文。明文舊資料原樣返回；key 缺失或不符時回空字串（記 error，不外洩密文）。"""
    if not is_encrypted(value):
        return value
    f = _fernet()
    if f is None:
        log.error("讀到加密機密但 AIQ_SECRET_KEY 未設定，無法解密（回空值）")
        return ""
    from cryptography.fernet import InvalidToken

    try:
        return f.decrypt(value[len(ENC_PREFIX) :].encode("ascii")).decode("utf-8")
    except InvalidToken:
        log.error("機密解密失敗（AIQ_SECRET_KEY 與加密時不符？），回空值")
        return ""
