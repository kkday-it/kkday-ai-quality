"""production 訂單佐證唯讀查詢層（判決歸因的「下單當時商品快照」取數入口）。

判決歸因（C-1~C-6）需要下單當時的頁面文字/退改政策作佐證（欄位映射 SSOT＝Confluence 2195652717）。
本模組是唯一的 production 取數入口，設計要點：

- **憑證抽象層** `resolve_credentials()`：env 服務帳號優先（`AIQ_EVIDENCE_DB_*`，SA/SD 核發後
  於部署層注入即自動切換）→ fallback「觸發 job 的 user」的 active production QC 連線
  （user_settings.qc_configs + 解密 qc_passwords）。job 啟動時一次性快照憑證，不中途重查。
- **allow-list 投影**：SQL SELECT 層面只取判決消費欄位；個資欄位（contact_email/contact_tel/
  member_uuid…）永不出現在投影 SQL——非取回再過濾（PII 防線，配 tests 斷言鎖定）。
- **JSONB 伺服器端投影**：`ors_prod_setting` 全塊 avg ~446KB/6.2s → 投影後 ~94KB/0.5s
  （2026-07-21 production 實測 6.5x/4.2x）；單語系 description_module + 實買 pkg 條目。
- **併發治理**：獨立 `BoundedSemaphore`（pool_size，遠低於 LLM 併發 64）+ 每次借出重設
  statement_timeout（session-scoped，池化連線必重設）+ 斷線丟棄重連單次重試；
  timeout（QueryCanceled）不重試直接降級——共享 snapshot 庫，自我限速優先。
- **失敗永不拋出**：`get_evidence()` 統一吞錯轉 `EvidenceResult.status`——佐證失敗＝降級判決，
  不得讓佐證問題拖垮判決批次（與判決管線的單筆 fail-loud 原則刻意相反）。

⚠️ 過渡管道聲明：現階段連 QC 共用 snapshot（postgresql-snapshot.kkday.com，共用帳號）純為
可行性驗證；終態＝SA/SD（Confluence VM/2165145662）專用 replica + 服務帳號。
快取/single-flight/熔斷 為後續層（同模組擴充）；審計落庫待 evidence 欄位 migration 就緒後接 DB。
"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.core import paths

_log = logging.getLogger("aiq.evidence")

# ── 配置（config/ai_judge/evidence.json；lazy + 模組級快取，比照 ai_judge loader 慣例）──────
_cfg_lock = threading.Lock()
_cfg_cache: dict | None = None


def _cfg() -> dict:
    """讀 evidence.json（首次存取才載入；`reload()` 清快取）。"""
    global _cfg_cache
    if _cfg_cache is None:
        with _cfg_lock:
            if _cfg_cache is None:
                _cfg_cache = json.loads(
                    (paths.AI_JUDGE_DIR / "evidence.json").read_text(encoding="utf-8")
                )
    return _cfg_cache


def reload() -> None:
    """清配置快取（編輯 evidence.json 後測試/腳本用）。"""
    global _cfg_cache
    with _cfg_lock:
        _cfg_cache = None


# ── 憑證（contextvar：批次啟動時 set 一次，ThreadPool worker 經 copy_context 繼承）──────────
_current_creds: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "qc_evidence_creds", default=None
)


def set_current(creds: dict | None) -> None:
    """注入當前執行流的佐證 DB 憑證（`resolve_credentials()` 產物；None＝本批無佐證）。"""
    _current_creds.set(creds)


def current() -> dict | None:
    """取當前執行流的佐證 DB 憑證；未注入回 None（get_evidence 據此降級）。"""
    return _current_creds.get()


def resolve_credentials(s: dict) -> dict | None:
    """解析佐證 DB 憑證：env 服務帳號優先，fallback user 的 active production QC 連線。

    Args:
        s: `app.core.settings.load_settings()` 回傳的完整 user 設定（含明文 qc_passwords）。

    Returns:
        連線參數 dict（host/port/user/password/dbname/schema/…），不可解析回 None——
        呼叫端不擋批次啟動，None＝本批全走無佐證降級。密碼在此一次性快照，
        批次執行中 user 改設定不影響進行中的 job（防半批新舊憑證不一致）。

    選取順序：env 服務帳號 → active config（若 env=production）→ 第一個有密碼的
    production config。佐證**只准連 production**，故不要求 user 把 active 切到
    production（active 服務的是資料瀏覽/上傳工作流，與佐證取數是不同關注點）。
    """
    from app.core.config import env

    db_cfg = _cfg().get("db") or {}
    base = {
        "dbname": db_cfg.get("dbname", "kkdb"),
        "schema": db_cfg.get("schema", "public"),
    }
    # ① env 服務帳號（終態路徑：SA/SD 核發後部署層注入即生效）
    if env.evidence_db_host and env.evidence_db_user and env.evidence_db_password:
        return {
            **base,
            "host": env.evidence_db_host,
            "port": env.evidence_db_port,
            "user": env.evidence_db_user,
            "password": env.evidence_db_password,
        }
    # ② fallback：production env 的 QC config（active 優先，否則首個有密碼者）
    configs = [c for c in (s.get("qc_configs") or []) if c.get("env") == "production"]
    aid = s.get("active_qc_config_id")
    configs.sort(key=lambda c: c.get("id") != aid)  # active 排最前（stable sort）
    passwords = s.get("qc_passwords") or {}
    for cfg in configs:
        pw = passwords.get(cfg.get("id")) or ""
        if cfg.get("host") and cfg.get("user") and pw:
            return {
                **base,
                "host": cfg["host"],
                "port": cfg.get("port") or 5432,
                "user": cfg["user"],
                "password": pw,
            }
    return None


# ── 連線池 + Semaphore + 借出治理 ─────────────────────────────────────────────────────────
_sem_lock = threading.Lock()
_sem: threading.BoundedSemaphore | None = None
_pools_lock = threading.Lock()
_pools: dict[tuple, Any] = {}  # key=(host,port,dbname,user) → ThreadedConnectionPool


def _get_sem() -> threading.BoundedSemaphore:
    """全域併發閘（lazy 以 config pool_size 建；獨立於 LLM 的 prejudge_max_workers）。"""
    global _sem
    if _sem is None:
        with _sem_lock:
            if _sem is None:
                _sem = threading.BoundedSemaphore(int((_cfg().get("db") or {}).get("pool_size", 3)))
    return _sem


def _get_pool(creds: dict):
    """取（或 lazy 建）對應憑證的 psycopg2 連線池；maxconn=pool_size（不比 semaphore 閘門寬）。"""
    from psycopg2.pool import ThreadedConnectionPool  # 重依賴 lazy import

    key = (creds["host"], creds["port"], creds["dbname"], creds["user"])
    pool = _pools.get(key)
    if pool is None:
        with _pools_lock:
            pool = _pools.get(key)
            if pool is None:
                db_cfg = _cfg().get("db") or {}
                pool = ThreadedConnectionPool(
                    minconn=1,
                    maxconn=int(db_cfg.get("pool_size", 3)),
                    host=creds["host"],
                    port=creds["port"],
                    dbname=creds["dbname"],
                    user=creds["user"],
                    password=creds["password"],
                    connect_timeout=int(db_cfg.get("connect_timeout_s", 5)),
                )
                _pools[key] = pool
    return pool


@contextmanager
def _borrow(creds: dict):
    """借出連線（semaphore 內）：每次重設 statement_timeout；壞連線丟棄不回池。

    SET 是 session-scoped，池化連線可能被 reset 或殘留舊值——每次借出必重設。
    """
    with _get_sem():
        pool = _get_pool(creds)
        conn = pool.getconn()
        try:
            conn.autocommit = True
            timeout_ms = int(float((_cfg().get("db") or {}).get("statement_timeout_s", 12)) * 1000)
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = %s", (timeout_ms,))
            yield conn
        except Exception:
            pool.putconn(conn, close=True)  # 狀態不明的連線一律丟棄，防污染池
            raise
        else:
            pool.putconn(conn)


def _query(creds: dict, sql: str, params: dict, *, many: bool = False):
    """點查執行 + 斷線單次重試。

    斷線類（OperationalError/InterfaceError）→ 丟棄連線重連重試 1 次；
    statement_timeout（QueryCanceled，OperationalError 子類）→ **不重試**直接拋——
    共享庫超時代表當下負載高，重打只會加壓（D4 自我限速）。

    Raises:
        psycopg2 例外：重試耗盡或不可重試類，由 get_evidence 統一轉 status。
    """
    import psycopg2
    from psycopg2 import errors as pg_errors

    last: Exception | None = None
    for _attempt in range(2):
        try:
            with _borrow(creds) as conn, conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall() if many else cur.fetchone()
        except pg_errors.QueryCanceled:
            raise  # timeout：不重試（見 docstring）
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            last = e
            continue
    assert last is not None
    raise last


# ── allow-list 投影 SQL（PII 防線：個資欄位永不出現；tests/test_qc_evidence.py 斷言鎖定）────
# order_tbl：判決消費欄位僅此 5 欄（Confluence §五/§六：order_status/price_pay/lang_code + 時序）。
_SQL_ORDER_TBL = """
SELECT order_mid, order_status, price_pay, lang_code, crt_dt
FROM order_tbl WHERE order_oid = %(oid)s
"""
# order_lst：版本鎖定鍵（prod_version）+ 使用日/時區（C-5/C-6 硬需求）+ 名稱快照。
# 多品項訂單取首列（ORDER BY 定序保冪等）；佐證聚焦主商品，逐品項展開待真實需求出現再擴充。
_SQL_ORDER_LST = """
SELECT prod_oid, prod_version, prod_level2_oid, item_oid, supplier_oid,
       lst_dt_go, timezone, prod_level2_name, prod_desc
FROM order_lst WHERE order_oid = %(oid)s
ORDER BY order_lst_oid LIMIT 1
"""
# ors_prod_lang：頁面呈現文字快照——只投影 item_summary/package_summary（判決引用的規格與方案文案）。
_SQL_PROD_LANG = """
SELECT jsonb_build_object(
  'item_summary',    prod_lang->'item_summary',
  'package_summary', prod_lang->'package_summary'
)
FROM ors_prod_lang
WHERE prod_oid = %(prod_oid)s AND prod_version = %(ver)s AND lang_code = %(lang)s
"""
# ors_prod_setting：結構化設定——單語系 description_module + 實買 pkg 條目（D3 投影，實測 6.5x/4.2x）。
# sale_time_result 可能為 null（實測樣本即缺）——jsonb_build_object 對 null 值容錯，消費端防禦。
_SQL_PROD_SETTING = """
SELECT jsonb_build_object(
  'timezone',           prod_setting->'product_summary'->'timezone',
  'category',           prod_setting->'product_summary'->'category',
  'product_name',       prod_setting->'product_summary'->'product_name'->%(lang)s,
  'sale_time_result',   prod_setting->'product_summary'->'sale_time_result',
  'description_module', prod_setting->'product_summary'->'description_module'->%(lang)s,
  'item_summary',       prod_setting->'item_summary',
  'package_summary',    (
    SELECT jsonb_agg(pkg) FROM jsonb_array_elements(prod_setting->'package_summary') pkg
    WHERE (pkg->>'pkg_oid')::bigint = %(pkg_oid)s
  )
)
FROM ors_prod_setting
WHERE prod_oid = %(prod_oid)s AND prod_version = %(ver)s
"""
# ors_pkg_basic：結構化退改政策 + 行程時長（C-5 權威來源）。
_SQL_PKG_BASIC = """
SELECT cancel_policy_client, tour_duration
FROM ors_pkg_basic
WHERE prod_oid = %(prod_oid)s AND prod_version = %(ver)s AND pkg_oid = %(pkg_oid)s
"""
# ors_prod_module_setting：套餐模組層設定（第 7 授權表；一 pkg 多 module_type 列）。
_SQL_MODULE_SETTING = """
SELECT prod_module_type, prod_module_setting
FROM ors_prod_module_setting
WHERE prod_oid = %(prod_oid)s AND prod_version = %(ver)s
  AND pkg_oid = %(pkg_oid)s AND lang_code = %(lang)s
"""
# supplier：僅名稱與處理單位歸屬（legal_name 等已定案為 noise，不取）。
_SQL_SUPPLIER = """
SELECT supplier_name, order_handler, msg_handler
FROM supplier WHERE supplier_oid = %(sup)s
"""

# 個資欄位關鍵字（deny 斷言用：任何投影 SQL / 組裝結果的 key 不得含這些字樣；allow-list 為主防線，
# 此清單供 tests 與 runtime 雙重把關——schema 未來加欄也不會被誤投影，但仍鎖定防人為誤加）。
PII_KEYWORDS: tuple[str, ...] = (
    "contact_email",
    "contact_tel",
    "contact_firstname",
    "contact_lastname",
    "tel_country",
    "member_uuid",
    "crt_uuid",
    "passport",
    "cus_email",
    "cus_tel",
    "access_token",
)

# 模組內全部投影 SQL（tests 掃描入口；新增點查必須登記於此，否則 PII 斷言測試會漏掃）。
ALL_PROJECTION_SQL: tuple[str, ...] = (
    _SQL_ORDER_TBL,
    _SQL_ORDER_LST,
    _SQL_PROD_LANG,
    _SQL_PROD_SETTING,
    _SQL_PKG_BASIC,
    _SQL_MODULE_SETTING,
    _SQL_SUPPLIER,
)


def _jsonable(v: Any) -> Any:
    """DB 值 → JSON 可序列化（datetime→ISO、Decimal→float；jsonb 已是 dict/list 原樣）。"""
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def assert_no_pii_keys(data: Any) -> None:
    """遞迴斷言組裝結果的 key 不含個資關鍵字（第二道防線；違反即拋 ValueError）。

    只掃 key 不掃 value：商品文案 value 含「email」字樣屬正常內容，不可誤殺。

    Raises:
        ValueError: 發現含 PII 關鍵字的 key（代表投影 SQL 被誤改，屬程式缺陷須 fail-loud）。
    """
    if isinstance(data, dict):
        for k, v in data.items():
            kl = str(k).lower()
            for kw in PII_KEYWORDS:
                if kw in kl:
                    raise ValueError(f"PII key leaked into evidence payload: {k}")
            assert_no_pii_keys(v)
    elif isinstance(data, list):
        for v in data:
            assert_no_pii_keys(v)


# ── 對外結果型別與唯一入口 ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class EvidenceResult:
    """佐證查詢結果。

    status：fetched（本次實查成功）/ cache_hit（快取命中，後續層回填）/ no_order_oid /
    not_found（訂單不存在或關聯鏈斷）/ degraded_unavailable（未配置/連線失敗/timeout）/
    error（非預期錯誤，已記 log）。
    """

    status: str
    data: dict | None = None


def get_evidence(order_oid: str | int | None) -> EvidenceResult:
    """判決歸因唯一取數入口：allow-list 投影點查 production，統一吞錯降級（不對外拋）。

    Args:
        order_oid: 訂單 oid；空值回 no_order_oid。

    Returns:
        EvidenceResult；status != "fetched" 時 data 為 None，呼叫端以空佐證降級判決。
    """
    import psycopg2
    from psycopg2 import errors as pg_errors
    from psycopg2 import pool as pg_pool

    if not _cfg().get("enabled", True):
        return EvidenceResult("degraded_unavailable")
    creds = current()
    if creds is None:
        return EvidenceResult("degraded_unavailable")
    oid = str(order_oid or "").strip()
    if not oid:
        return EvidenceResult("no_order_oid")

    t0 = time.time()
    try:
        ot = _query(creds, _SQL_ORDER_TBL, {"oid": oid})
        if ot is None:
            _audit(oid, "not_found", t0)
            return EvidenceResult("not_found")
        order_mid, order_status, price_pay, lang_code, crt_dt = ot

        ol = _query(creds, _SQL_ORDER_LST, {"oid": oid})
        if ol is None:
            _audit(oid, "not_found", t0)
            return EvidenceResult("not_found")
        (
            prod_oid,
            prod_version,
            pkg_oid,
            item_oid,
            supplier_oid,
            lst_dt_go,
            tz,
            pkg_name,
            prod_desc,
        ) = ol

        lang = (lang_code or "").strip() or str(
            (_cfg().get("summary") or {}).get("lang_fallback", "zh-tw")
        )
        common = {"prod_oid": prod_oid, "ver": prod_version, "pkg_oid": pkg_oid, "lang": lang}

        pl = _query(creds, _SQL_PROD_LANG, common)
        ps = _query(creds, _SQL_PROD_SETTING, common)
        pb = _query(creds, _SQL_PKG_BASIC, common)
        ms = _query(creds, _SQL_MODULE_SETTING, common, many=True)
        sup = _query(creds, _SQL_SUPPLIER, {"sup": supplier_oid})

        data = {
            "order": {
                "order_oid": int(oid),
                "order_mid": order_mid,
                "order_status": order_status,
                "price_pay": _jsonable(price_pay),
                "lang_code": lang_code,
                "crt_dt": _jsonable(crt_dt),
                "prod_oid": prod_oid,
                "prod_version": prod_version,
                "pkg_oid": pkg_oid,
                "item_oid": item_oid,
                "supplier_oid": supplier_oid,
                "lst_dt_go": _jsonable(lst_dt_go),
                "timezone": tz,
                "package_name": pkg_name,
                "prod_desc": prod_desc,
            },
            "product_lang": pl[0] if pl else None,
            "product_setting": ps[0] if ps else None,
            "pkg_basic": ({"cancel_policy_client": pb[0], "tour_duration": pb[1]} if pb else None),
            "module_setting": ({str(r[0]): r[1] for r in ms} if ms else None),
            "supplier": (
                {"supplier_name": sup[0], "order_handler": sup[1], "msg_handler": sup[2]}
                if sup
                else None
            ),
            "meta": {
                "lang": lang,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "source": "qc-snapshot",  # 過渡管道標記；服務帳號接線後改 replica 標記
            },
        }
        assert_no_pii_keys(data)  # 第二道防線：投影被誤改時 fail-loud（轉 error 降級）
        _audit(oid, "fetched", t0)
        return EvidenceResult("fetched", data)
    except pg_errors.QueryCanceled:
        _audit(oid, "timeout", t0)
        return EvidenceResult("degraded_unavailable")
    except (psycopg2.OperationalError, psycopg2.InterfaceError, pg_pool.PoolError):
        _audit(oid, "conn_fail", t0)
        return EvidenceResult("degraded_unavailable")
    except Exception:
        _log.exception("evidence fetch unexpected error order_oid=%s", oid)
        _audit(oid, "error", t0)
        return EvidenceResult("error")


def _audit(order_oid: str, outcome: str, t0: float) -> None:
    """應用層審計（R8：共用 DB 帳號在 DB 側無法區分呼叫者，只能靠 app 記）。

    現階段落 logger；evidence 欄位 migration 就緒後改仿 llm_usage 落庫（含 job_id/triggered_by）。
    """
    _log.info(
        "evidence_audit order_oid=%s outcome=%s elapsed_ms=%d",
        order_oid,
        outcome,
        int((time.time() - t0) * 1000),
    )
