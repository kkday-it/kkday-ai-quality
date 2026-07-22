"""production 訂單佐證唯讀查詢層（判決歸因的「下單當時商品快照」取數入口）。

判決歸因（C-1~C-6）需要下單當時的頁面文字/退改政策作佐證（欄位映射 SSOT＝Confluence 2195652717）。
本模組是唯一的 production 取數入口，設計要點：

- **憑證抽象層** `resolve_credentials()`：env 服務帳號優先（`AIQ_EVIDENCE_DB_*`，SA/SD 核發後
  於部署層注入即自動切換）→ fallback 全項目共享的 production QC 連線
  （user_settings.qc_connections["production"] + 解密 qc_passwords["production"]）。
  job 啟動時一次性快照憑證，不中途重查。
- **allow-list 投影**：SQL SELECT 層面只取判決消費欄位；個資欄位（contact_email/contact_tel/
  member_uuid…）永不出現在投影 SQL——非取回再過濾（PII 防線，配 tests 斷言鎖定）。
- **JSONB 伺服器端投影**：`ors_prod_setting` 全塊 avg ~446KB/6.2s → 投影後 ~94KB/0.5s
  （2026-07-21 production 實測 6.5x/4.2x）；單語系 description_module + 實買 pkg 條目。
- **併發治理**：獨立 `BoundedSemaphore`（pool_size，遠低於 LLM 併發 64）+ 每次借出重設
  statement_timeout（session-scoped，池化連線必重設）+ 斷線丟棄重連單次重試；
  timeout（QueryCanceled）不重試直接降級——共享 snapshot 庫，自我限速優先。
- **失敗永不拋出**：`get_evidence()` 統一吞錯轉 `EvidenceResult.status`——佐證失敗＝降級判決，
  不得讓佐證問題拖垮判決批次（與判決管線的單筆 fail-loud 原則刻意相反）。
- **兩級快取落本地 PG**（`evidence_snapshot` 表；order 短 TTL/商品版本長 TTL）：真相源仍是
  production snapshot，此表純快取可整表清空重生；刻意不入 datapack（見 tables.py 註記）。

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
from collections.abc import Callable
from concurrent.futures import Future
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


def summary_cfg() -> dict:
    """摘要器旋鈕（summary 區塊複本；prejudge._summarize_evidence 消費）。"""
    return dict(_cfg().get("summary") or {})


def probe(creds: dict) -> bool:
    """輕量連線探測（job 啟動前，R9）：SELECT 1；任何失敗回 False（呼叫端整批降級）。

    不經快取/single-flight/熔斷——目的就是驗證「現在連得上嗎」本身。
    """
    try:
        with _borrow(creds) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return True
    except Exception:  # noqa: BLE001 —— 探測失敗原因一律記 log 後降級，不拋
        _log.warning("evidence DB probe failed", exc_info=True)
        return False


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


def _base_conn() -> dict:
    """佐證連線的固定欄位（dbname/schema，來自 evidence.json）。"""
    db_cfg = _cfg().get("db") or {}
    return {"dbname": db_cfg.get("dbname", "kkdb"), "schema": db_cfg.get("schema", "public")}


def _env_credentials() -> dict | None:
    """env 服務帳號憑證（終態路徑：SA/SD 核發後部署層注入 AIQ_EVIDENCE_DB_*；未設回 None）。"""
    from app.core.config import env

    if env.evidence_db_host and env.evidence_db_user and env.evidence_db_password:
        return {
            **_base_conn(),
            "host": env.evidence_db_host,
            "port": env.evidence_db_port,
            "user": env.evidence_db_user,
            "password": env.evidence_db_password,
        }
    return None


def _production_config_credentials(s: dict) -> dict | None:
    """全域設定的 production QC 連線憑證（無 host/user/password 任一則 None）。

    A schema 改造後（2026-07-22）QC 連線由 `qc_configs[]`+`active_qc_config_id` 收斂為
    `qc_connections`（keyed by env，每環境恰一條），故不再需要「掃多套挑 active/首個」。
    """
    conn = (s.get("qc_connections") or {}).get("production") or {}
    pw = (s.get("qc_passwords") or {}).get("production") or ""
    if conn.get("host") and conn.get("user") and pw:
        return {
            **_base_conn(),
            "host": conn["host"],
            "port": conn.get("port") or 5432,
            "user": conn["user"],
            "password": pw,
        }
    return None


def resolve_credentials(s: dict) -> dict | None:
    """解析某 user 的佐證 DB 憑證：env 服務帳號優先，fallback 該 user 的 production QC 連線。

    Args:
        s: `app.core.settings.load_settings()` 回傳的完整 user 設定（含明文 qc_passwords）。

    Returns:
        連線參數 dict（host/port/user/password/dbname/schema），不可解析回 None——
        呼叫端不擋批次啟動，None＝本批全走無佐證降級。密碼在此一次性快照，
        批次執行中 user 改設定不影響進行中的 job（防半批新舊憑證不一致）。
    """
    return _env_credentials() or _production_config_credentials(s)


def resolve_credentials_any(s: dict | None = None) -> dict | None:
    """佐證 DB 憑證：env 服務帳號 → 全項目共享配置的 production QC。

    去帳戶隔離後（2026-07-22）配置全項目單一份，不再需要「掃全庫找誰配了 production QC」——
    直讀全局配置即可。s 傳入時優先用（通常就是 `load_settings()` 全局那份，等價），None 則直讀全局。

    ⚠️ 過渡管道：現用 QC 共用 snapshot（唯讀共用帳號 kk02021）；終態＝env 服務帳號
    （`_env_credentials` 優先，SA/SD 核發後全局 QC config fallback 自然不觸發）。

    Returns:
        連線參數 dict 或 None（全項目無可用 production QC 憑證時）。
    """
    if s is not None:
        preferred = resolve_credentials(s)
        if preferred:
            return preferred
    from app.core import settings as app_settings

    return _env_credentials() or _production_config_credentials(app_settings.load_settings())


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

# PII key 防線的作用域：只掃「我們自組」的區塊（order/supplier/meta——欄位由 allow-list SQL
# 決定，出現 PII 關鍵字＝投影被誤改）。商品內容區塊（product_lang/product_setting/pkg_basic/
# module_setting）豁免——那是商品目錄 JSONB，key 由商品方自訂，`passportNo` 等字樣是「要求旅客
# 填護照」的 spec 欄位標籤（商品內容非旅客個資），Phase A 實測 44% 誤殺率，故不掃。
PII_GUARD_SECTIONS: tuple[str, ...] = ("order", "supplier", "meta")

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


# ── 兩級快取（本地 PG evidence_snapshot 表；使用者 2026-07-22 拍板由 diskcache 改落 PG）──────
# 真相源仍是 production snapshot——此表純快取（可整表清空重生）；TTL 懶清理：
# 讀到過期＝miss 並刪列；寫入時順手清全表過期列（走 expires 索引，量小成本可忽略）。
def _now_iso() -> str:
    """UTC ISO 時間字串（快取時戳/過期比較統一格式，lexical 可比較）。"""
    return datetime.now(timezone.utc).isoformat()


def _cache_get(key: str) -> Any:
    """PG 快取讀：命中且未過期回 payload；未命中/已過期回 None（過期列順手刪除）。"""
    from sqlalchemy import delete, select

    from app.core.db import tables as T

    t = T.evidence_snapshot
    with T.get_engine().begin() as c:
        row = c.execute(select(t.c.payload, t.c.expires_at).where(t.c.cache_key == key)).first()
        if row is None:
            return None
        if (row.expires_at or "") <= _now_iso():
            c.execute(delete(t).where(t.c.cache_key == key))
            return None
        return row.payload


def _cache_set(key: str, kind: str, val: Any, ttl_s: int) -> None:
    """PG 快取寫（upsert）＋清全表過期列；寫入失敗僅記 log 不拋（快取層不得拖垮取數）。"""
    from datetime import timedelta

    from sqlalchemy import delete
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.core.db import tables as T

    t = T.evidence_snapshot
    now = datetime.now(timezone.utc)
    try:
        with T.get_engine().begin() as c:
            c.execute(delete(t).where(t.c.expires_at <= now.isoformat()))
            stmt = pg_insert(t).values(
                cache_key=key,
                kind=kind,
                payload=val,
                fetched_at=now.isoformat(),
                expires_at=(now + timedelta(seconds=ttl_s)).isoformat(),
            )
            c.execute(
                stmt.on_conflict_do_update(
                    index_elements=[t.c.cache_key],
                    set_={
                        "kind": kind,
                        "payload": val,
                        "fetched_at": now.isoformat(),
                        "expires_at": (now + timedelta(seconds=ttl_s)).isoformat(),
                    },
                )
            )
    except Exception:  # noqa: BLE001 —— 快取寫入失敗降級為「無快取」，取數結果照常回傳
        _log.warning("evidence cache write failed key=%s", key, exc_info=True)


def _order_ttl_s() -> int:
    """order 級 TTL（短——order_status/price_refund 易變，拿舊值會判錯方向，R6）。"""
    return int(float((_cfg().get("cache") or {}).get("order_ttl_hours", 6)) * 3600)


def _product_ttl_s() -> int:
    """商品級 TTL（長——prod_version 鎖版理論不可變；未取得書面 immutable 保證前防禦性有限期，R5）。"""
    return int(float((_cfg().get("cache") or {}).get("product_ttl_days", 30)) * 86400)


# ── single-flight（in-process；單 worker 制度性保證，見 Dockerfile --workers 1 註記）──────
_inflight_lock = threading.Lock()
_inflight: dict[str, Future] = {}


def _singleflight(key: str, fn: Callable[[], Any]) -> Any:
    """同 key 併發請求合併為一次底層執行；follower 共享 leader 的結果或例外。

    批次 ThreadPool 多 worker 撞同一 order/product key 時，避免各自打一次 production
    （首輪 distinct 商品比率實測 88/99——重複 key 不多但單次成本 3~5s，值得合併）。
    """
    with _inflight_lock:
        fut = _inflight.get(key)
        leader = fut is None
        if leader:
            fut = Future()
            _inflight[key] = fut
    if not leader:
        return fut.result(timeout=float(_cfg().get("singleflight_wait_timeout_s", 20)))
    try:
        result = fn()
    except Exception as e:
        fut.set_exception(e)
        with _inflight_lock:
            _inflight.pop(key, None)
        raise
    fut.set_result(result)
    with _inflight_lock:
        _inflight.pop(key, None)
    return result


# ── 熔斷器（R13：維護窗口/掐線時整批快速降級，不讓 964 筆各撞 timeout 卡數小時）──────────
_breaker_lock = threading.Lock()
_breaker_fails = 0  # 連續失敗數（成功即清零）
_breaker_opened_at = 0.0  # 開啟時刻（0＝關閉）


class BreakerOpen(Exception):
    """熔斷開啟中——呼叫端直接降級，不觸 DB。"""


def _breaker_allow() -> None:
    """DB 存取前檢查：開啟且未過冷卻 → 拋 BreakerOpen；過冷卻放行一次探測（half-open）。

    Raises:
        BreakerOpen: 熔斷開啟且冷卻未到。
    """
    global _breaker_opened_at
    with _breaker_lock:
        if _breaker_opened_at <= 0:
            return
        cooldown = float((_cfg().get("db") or {}).get("breaker_cooldown_s", 60))
        if time.time() - _breaker_opened_at < cooldown:
            raise BreakerOpen
        _breaker_opened_at = 0.0  # half-open：放行本次探測；失敗會再度累積開啟


def _breaker_record(ok: bool) -> None:
    """記錄 DB 存取結果：連續失敗達閾值即開啟熔斷；成功清零。"""
    global _breaker_fails, _breaker_opened_at
    with _breaker_lock:
        if ok:
            _breaker_fails = 0
            return
        _breaker_fails += 1
        threshold = int((_cfg().get("db") or {}).get("breaker_threshold", 5))
        if _breaker_fails >= threshold and _breaker_opened_at <= 0:
            _breaker_opened_at = time.time()
            _log.warning("evidence breaker OPEN（連續 %d 次 DB 失敗）", _breaker_fails)


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


def _fetch_order_bundle(creds: dict, oid: str) -> dict | None:
    """order 級 DB 實查（order_tbl+order_lst 合併；cache miss 時經 single-flight 呼叫）。

    Returns:
        order dict；訂單不存在或關聯鏈斷回 None（呼叫端轉 not_found，不快取）。
    """
    _breaker_allow()
    ot = _query(creds, _SQL_ORDER_TBL, {"oid": oid})
    if ot is None:
        _breaker_record(True)  # 查詢成功、單純無此單——不是 infra 失敗
        return None
    order_mid, order_status, price_pay, lang_code, crt_dt = ot
    ol = _query(creds, _SQL_ORDER_LST, {"oid": oid})
    _breaker_record(True)
    if ol is None:
        return None
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
    return {
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
    }


def _fetch_product_bundle(creds: dict, order: dict, lang: str) -> dict:
    """商品版本級 DB 實查（4 表投影；同 (prod,ver,lang,pkg) 的多筆訂單共用快取）。"""
    _breaker_allow()
    common = {
        "prod_oid": order["prod_oid"],
        "ver": order["prod_version"],
        "pkg_oid": order["pkg_oid"],
        "lang": lang,
    }
    pl = _query(creds, _SQL_PROD_LANG, common)
    ps = _query(creds, _SQL_PROD_SETTING, common)
    pb = _query(creds, _SQL_PKG_BASIC, common)
    ms = _query(creds, _SQL_MODULE_SETTING, common, many=True)
    _breaker_record(True)
    return {
        "product_lang": pl[0] if pl else None,
        "product_setting": ps[0] if ps else None,
        "pkg_basic": ({"cancel_policy_client": pb[0], "tour_duration": pb[1]} if pb else None),
        "module_setting": ({str(r[0]): r[1] for r in ms} if ms else None),
    }


def _fetch_supplier_bundle(creds: dict, supplier_oid) -> dict | None:
    """supplier 級 DB 實查（名稱/處理單位；低變動，共用商品級 TTL）。"""
    _breaker_allow()
    sup = _query(creds, _SQL_SUPPLIER, {"sup": supplier_oid})
    _breaker_record(True)
    if sup is None:
        return None
    return {"supplier_name": sup[0], "order_handler": sup[1], "msg_handler": sup[2]}


def _cached(key: str, kind: str, ttl_s: int, fetch: Callable[[], Any]) -> tuple[Any, bool]:
    """快取優先讀取：命中直接回（不進 single-flight，減少鎖爭用）；miss 經 single-flight 實查。

    Returns:
        (value, cache_hit)。fetch 回 None（如查無此單）不落快取——避免暫態誤判長駐。
    """
    val = _cache_get(key)
    if val is not None:
        return val, True

    def _do() -> Any:
        v = fetch()
        if v is not None:
            _cache_set(key, kind, v, ttl_s)
        return v

    return _singleflight(key, _do), False


def get_evidence(order_oid: str | int | None) -> EvidenceResult:
    """判決歸因唯一取數入口：兩級快取 + single-flight + 熔斷 + allow-list 投影點查。

    Args:
        order_oid: 訂單 oid；空值回 no_order_oid。

    Returns:
        EvidenceResult；status ∈ {fetched, cache_hit} 時帶 data，其餘 data=None
        （呼叫端以空佐證降級判決，永不阻斷批次）。
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
        order, hit_order = _cached(
            f"order:{oid}", "order", _order_ttl_s(), lambda: _fetch_order_bundle(creds, oid)
        )
        if order is None:
            _audit(oid, "not_found", t0)
            return EvidenceResult("not_found")

        lang = (order.get("lang_code") or "").strip() or str(
            (_cfg().get("summary") or {}).get("lang_fallback", "zh-tw")
        )
        prod_key = f"prod:{order['prod_oid']}:{order['prod_version']}:{lang}:{order['pkg_oid']}"
        prod, hit_prod = _cached(
            prod_key, "product", _product_ttl_s(), lambda: _fetch_product_bundle(creds, order, lang)
        )
        sup, hit_sup = _cached(
            f"sup:{order['supplier_oid']}",
            "supplier",
            _product_ttl_s(),
            lambda: _fetch_supplier_bundle(creds, order["supplier_oid"]),
        )

        all_hit = hit_order and hit_prod and hit_sup
        data = {
            "order": order,
            **(prod or {}),
            "supplier": sup,
            "meta": {
                "lang": lang,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "source": "qc-snapshot",  # 過渡管道標記；服務帳號接線後改 replica 標記
                "cache": {"order": hit_order, "product": hit_prod, "supplier": hit_sup},
            },
        }
        # 第二道防線：只掃自組區塊（商品目錄內容豁免，見 PII_GUARD_SECTIONS 註解）
        assert_no_pii_keys({k: data.get(k) for k in PII_GUARD_SECTIONS})
        status = "cache_hit" if all_hit else "fetched"
        _audit(oid, status, t0)
        return EvidenceResult(status, data)
    except BreakerOpen:
        _audit(oid, "breaker_open", t0)
        return EvidenceResult("degraded_unavailable")
    except pg_errors.QueryCanceled:
        _breaker_record(False)
        _audit(oid, "timeout", t0)
        return EvidenceResult("degraded_unavailable")
    except (psycopg2.OperationalError, psycopg2.InterfaceError, pg_pool.PoolError):
        _breaker_record(False)
        _audit(oid, "conn_fail", t0)
        return EvidenceResult("degraded_unavailable")
    except TimeoutError:
        # single-flight follower 等 leader 逾時（leader 卡死/池耗盡）——降級不重試
        _audit(oid, "singleflight_timeout", t0)
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
