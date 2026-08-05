"""wire 契約快照：凍結 DB 層各讀取函式回傳給 API 的 key 形狀與時間欄型別。

**存在的理由**：多個讀取函式目前以 `select(表)` 全欄直出（`prejudge_runs.list_prejudge_runs`、
`attribution_history.list_attribution_history`、`ingest.list_batches`、
`prompt_debug_reviews.fetch_prompt_debug_reviews`），DB 加一個欄就等於改一次 API 契約 —— 新增欄會
**自動**流到前端，而型別檢查與既有測試都攔不住。本檔把當下的形狀凍結成常數，使
「DB schema 演進」與「wire 契約變更」必須是兩個顯式動作。

**怎麼用**：改動 DB 欄或序列化邏輯後本檔若變紅，代表 wire 契約真的動了 —— 若是預期內的
契約變更，同輪更新下方常數並確認前端消費端；若非預期，那就是無意間外洩了內部欄位。

時間欄一律斷言為 `str`（ISO）：專案慣例是 datetime 欄在出 API 前轉 ISO 字串
（見 `prejudge_runs._serialize` / `attribution_history._history_row`），日後時間欄改型別時
這幾條斷言會逐一指出所有需要跟著改的序列化點。

**別名欄護欄（本檔後半段）**：DDL 規範對齊後多個欄的 DB 名與 Python key 刻意不同
（`Column("version_number", key="version")`）。`key=` 只在**組查詢**時生效，讀結果時
SQLAlchemy 的 result mapping 一律用 **DB 欄名** —— 投影沒 `.label(c.key)` 就會把規範名漏給前端。
這類漏法是靜默的（HTTP 200、前端欄位變 undefined），故本檔用兩層護欄堵：
① 每張有別名欄的表（**自 metadata 派生**，非手寫清單）都必須登記直出探針並逐一斷言；
② 反向掃全部 GET 端點的回傳樣本，出現任何 DB 規範名即紅燈。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

import pytest
from fastapi.testclient import TestClient

from app.core import db
from app.core.db import tables as T
from app.core.db._shared import attribution_dto

# ── 凍結的 wire 形狀（dotted path；巢狀 dict 展開，list 不展開）──────────────────
# 值＝該 key 允許的 Python 型別名；None 表示不約束型別（僅約束 key 存在）。

_PREJUDGE_RUN_WIRE = {
    "job_id": "str",
    "kind": "str",
    "rejudge": None,
    "source": None,
    "model": None,
    "params": "dict",  # JSONB 發起參數快照（動態 key，視為葉節點）
    "status": "str",
    "total": None,
    "processed": None,
    "ok": None,
    "failed": None,
    "total_tokens": None,
    "cost_usd": None,
    "triggered_by": None,
    "started_at": "str",  # datetime → ISO 字串
    "finished_at": None,  # 執行中為 None，終態為 ISO 字串
}

_ATTRIBUTION_HISTORY_WIRE = {
    "id": "int",
    "source": "str",
    "source_id": "str",
    "kind": "str",
    "model": None,
    "params": None,
    "attributions": None,
    # result_digest 已於 2026-08-04 移出 wire（內部去重鍵，前端零消費）
    "job_id": None,
    "triggered_by": None,
    "author": None,
    "content": None,
    "created_at": "str",
}

_BATCH_WIRE = {
    "batch_id": "str",
    "name": "str",
    "source": "str",
    "original_name": "str",
    "row_count": "int",
    "uploaded_at": "str",
    "note": "str",
}

# 規則版本（`/api/judge-rules` 與 `/{code}/history`）—— 對應前端 `judgeRules.api.ts` 的
# RuleMeta / RuleVersionMeta，兩個歷史面板（RuleHistoryPanel / PromptHistoryPanel）共用。
# 時間欄在此為 HTTP 層樣本，故已是 FastAPI 序列化後的 ISO 字串。
_RULE_META_WIRE = {
    "rule_code": "str",
    "version": "int",
    "author": None,
    "note": None,
    "created_at": "str",
    "label": None,  # content._meta.label，缺值由前端 fallback
}

_RULE_HISTORY_WIRE = {
    "version": "int",
    "author": None,
    "note": None,
    "is_active": "bool",
    "created_at": "str",
}

# 歸因 DTO（`_shared.attribution_dto`）—— 前端 Attribution interface 的對應面。
# 這是全專案唯一有結構性巢狀的 wire 形狀，故需指定可展開的欄（其餘端點皆為扁平）。
_ATTRIBUTION_DTO_STRUCTURAL = frozenset({"l1", "l2", "confidence", "content"})

_ATTRIBUTION_DTO_WIRE = {
    # 身分鍵：原為 finding_id（(來源,評論,L1,L2) 四欄的字串編碼），2026-08-04 退場——
    # 它 100% 冗餘且實測 91% 的值與 feedback_source_code 互相矛盾。改由 serial PK 承擔。
    "attribution_oid": None,
    "polarity": None,
    "sentiment_score": None,
    "stage": None,
    "l1.code": None,
    "l1.label": None,
    "l2.code": None,
    "l2.label": None,
    "confidence.value": None,
    "confidence.raw": None,
    "confidence.tier": None,
    "content.summary": None,
    "content.summary_langs": "dict",  # 語系→摘要 map（動態 key，視為葉節點）
    "content.evidence": None,
    "content.action": None,
    "owner": None,
    "model": None,
    "is_primary": None,
    "is_auto_accepted": None,
}

# attribution_dto 的輸入樣本（typed 欄 mapping）——契約測試與別名欄探針共用同一份，避免兩處漂移。
_ATTRIBUTION_ROW_SAMPLE = {
    "polarity": "negative",
    "sentiment_score": 2,
    "prejudge_stage": "judged",
    "l1_code": "content",
    "l1_label": "商品內容",
    "l2_code": "C-1-1",
    "l2_label": "描述不符",
    "conf_value": 0.9,
    "conf_raw": 0.85,
    "conf_tier": "auto_accept",
    "summary": {"zh-tw": "摘要"},
    "evidence": "原文",
    "action": "建議",
    "model": "stub",
    "is_primary": True,
    "is_auto_accepted": False,
}


def _shape(
    obj: Mapping, structural: frozenset[str] = frozenset(), prefix: str = ""
) -> dict[str, str | None]:
    """mapping → {dotted key: 型別名}；只對 `structural` 列名的欄展開巢狀。

    **為何要顯式列出可展開的欄**：wire 上的 dict 有兩種，混談會讓契約失去意義 ——
    ① 結構性巢狀（`l1`/`confidence`/`content`）：key 集合固定，是契約的一部分，要展開比對；
    ② JSONB 資料 payload（`params`/`ai_output`/`versions`/`summary_langs`）：key 由業務資料
    決定、隨夾具而變，展開只會讓契約隨測試資料浮動（實測：空 dict 展開後整個 key 直接消失）。
    後者一律視為葉節點，只鎖「這個 key 存在且是 dict」。

    值為 None 的欄位型別記為 None（測試端據此放寬型別斷言，因為 nullable 欄在夾具中
    可能就是空的）。
    """
    out: dict[str, str | None] = {}
    for k, v in obj.items():
        key = f"{prefix}{k}"
        if isinstance(v, Mapping) and key in structural:
            out.update(_shape(v, structural, prefix=f"{key}."))
        else:
            out[key] = None if v is None else type(v).__name__
    return out


def _assert_wire(
    actual: Mapping,
    expected: dict[str, str | None],
    label: str,
    structural: frozenset[str] = frozenset(),
) -> None:
    """比對實際 wire 形狀與凍結契約：key 集合須完全相同，且有約束的欄型別須相符。"""
    shape = _shape(actual, structural)
    missing = sorted(set(expected) - set(shape))
    extra = sorted(set(shape) - set(expected))
    assert not missing and not extra, (
        f"{label} 的 wire 契約已變動 —— 少了：{missing}；多了：{extra}。"
        f"若為預期內的契約變更，請同輪更新 test_wire_contract.py 的常數並確認前端消費端。"
    )
    # 型別只在「契約有指定 且 實際值非 None」時比對（nullable 欄夾具可能為空）
    bad = {
        k: (want, shape[k])
        for k, want in expected.items()
        if want is not None and shape[k] is not None and shape[k] != want
    }
    assert not bad, f"{label} 的 wire 欄型別已變動（期望 vs 實際）：{bad}"


# ── 夾具 ────────────────────────────────────────────────────────────────────


@pytest.fixture
def seeded(temp_db):
    """為每個契約端點各落一列最小夾具（欄位值不重要，形狀才重要）。"""
    # 規則版本用磁碟默認 seed 而非隨手捏的 dict：prompt_* 的 content 會被 prompt_source 解析
    # （缺 `## System` 區塊直接拋），捏假值等於毒到同 session 其他測試的 prompt 快取。
    db.save_rule_version(
        "prompt_C-1",
        db.default_rule_content("prompt_C-1"),
        note="契約夾具",
        author="wire@kkday.com",
    )
    db.insert_prejudge_run(
        {
            "job_id": "pj_wire0001",
            "kind": "batch",
            "rejudge": False,
            "source": "reviews",
            "model": "stub",
            "params": {"scope": "all"},
            "status": "running",
            "total": 1,
            "triggered_by": "wire@kkday.com",
        }
    )
    db.add_history_note("reviews", "R-wire-1", author="wire@kkday.com", content="契約夾具")
    db.create_batch(
        source="reviews",
        source_label="評論",
        original_name="wire.xlsx",
        row_count=1,
        note="契約夾具",
    )


@pytest.fixture
def client(seeded):
    """TestClient（綁 `seeded` 的隔離測試庫）：別名欄探針一律打真實端點，測的是 wire 本身。"""
    import app.api.main as m

    return TestClient(m.app)


# ── 契約測試 ────────────────────────────────────────────────────────────────


def test_prejudge_run_list_and_detail_wire(seeded):
    """`/api/v1/prejudge/runs` 與 `/runs/{job_id}`：run 欄位直出 + 詳情多一個 stages。"""
    items = db.list_prejudge_runs()["items"]
    assert items, "夾具未落庫"
    _assert_wire(items[0], _PREJUDGE_RUN_WIRE, "list_prejudge_runs.items[]")

    detail = db.prejudge_run_detail("pj_wire0001")
    assert detail is not None
    _assert_wire(detail, {**_PREJUDGE_RUN_WIRE, "stages": None}, "prejudge_run_detail")
    # log 必須被剔除：它是可觀的快照（實測既有資料平均約 70 KB/列），不該進列表/詳情回傳
    assert "log" not in detail


def test_attribution_history_wire(seeded):
    """`/api/attribution-history` 與 notes POST：兩者共用同一個序列化函式，形狀須一致。"""
    events = db.list_attribution_history("reviews", "R-wire-1")
    assert events
    _assert_wire(events[0], _ATTRIBUTION_HISTORY_WIRE, "list_attribution_history[]")

    created = db.add_history_note("reviews", "R-wire-2", author="w@kkday.com", content="x")
    _assert_wire(created, _ATTRIBUTION_HISTORY_WIRE, "add_history_note")


def test_batches_wire(seeded):
    """`/api/batches`：list 與 create 兩處各自硬編一份 key，形狀必須相同（現況是重複定義）。"""
    rows = db.list_batches()
    assert rows
    _assert_wire(rows[0], _BATCH_WIRE, "list_batches[]")

    created = db.create_batch(
        source="reviews",
        source_label="評論",
        original_name="wire2.xlsx",
        row_count=2,
    )
    _assert_wire(created, _BATCH_WIRE, "create_batch")


def test_judge_rule_meta_and_history_wire(client):
    """`/api/judge-rules` 與 `/{code}/history`：規則頁與 Prompt 頁的版本歷史面板共用此形狀。

    走 HTTP（而非 db 層函式）：本次事故正是「db 層回 DB 規範名、FastAPI 原樣往外送」，
    只測 db 層看不出前端拿到什麼。
    """
    metas = client.get("/api/judge-rules").json()
    assert metas, "夾具未落庫"
    _assert_wire(metas[0], _RULE_META_WIRE, "GET /api/judge-rules[]")

    history = client.get("/api/judge-rules/prompt_C-1/history").json()
    assert history, "夾具未落庫"
    _assert_wire(history[0], _RULE_HISTORY_WIRE, "GET /api/judge-rules/{code}/history[]")


def test_attribution_dto_wire():
    """`attribution_dto` 是 DB 欄 → 前端 Attribution interface 的緩衝層，形狀即 wire 契約。

    純函式，不需要 DB。
    """
    dto = attribution_dto(_ATTRIBUTION_ROW_SAMPLE)
    _assert_wire(
        dto, _ATTRIBUTION_DTO_WIRE, "attribution_dto", structural=_ATTRIBUTION_DTO_STRUCTURAL
    )


# ── 別名欄表的 wire 護欄（表清單自 metadata 派生，杜絕手寫漏表）────────────────────


def _aliased_columns() -> dict[str, dict[str, str]]:
    """metadata → `{表名: {DB 規範名: Python/wire key}}`，只收 `key != name` 的欄。

    **刻意從 metadata 派生而非手寫清單**：手寫必然漏 —— 本檔原本只覆蓋 8 張別名欄表裡的 4 張，
    漏掉的 `judge_rule_version_lst` 正好就是實際外洩規範名（version_number/create_user/
    create_date）給前端的那張。派生後新增別名欄的表會讓 `test_every_aliased_table_has_wire_probe`
    直接變紅，逼著補探針。
    """
    return {
        t.name: aliased
        for t in T.metadata.sorted_tables
        if (aliased := {c.name: c.key for c in t.columns if c.key != c.name})
    }


def _keys_deep(obj) -> set[str]:
    """遞迴收集 JSON 樣本中出現過的所有 dict key（含巢狀 dict 與 list 元素）。"""
    if isinstance(obj, Mapping):
        return set(obj) | {k for v in obj.values() for k in _keys_deep(v)}
    if isinstance(obj, (list, tuple)):
        return {k for v in obj for k in _keys_deep(v)}
    return set()


# ── 各別名欄表的「直出到 wire」探針 ───────────────────────────────────────────
# 探針一律打真實端點（測 wire 而非 DB 層函式）；回傳該表會流到前端的樣本 list。


def _probe_attribution(client: TestClient) -> list:
    """`attribution_tbl`：經 `attribution_dto` 出 wire（問題列表 items[].attributions[]）。"""
    return [client.get("/api/problems").json(), attribution_dto(_ATTRIBUTION_ROW_SAMPLE)]


def _probe_attribution_event(client: TestClient) -> list:
    """`attribution_event_lst`：歸因歷史時間軸（select_wire 全欄直出）。"""
    return [client.get("/api/attribution-history?source=reviews&source_id=R-wire-1").json()]


def _probe_judge_rule_version(client: TestClient) -> list:
    """`judge_rule_version_lst`：規則清單 meta ＋版本歷史（規則頁與 Prompt 頁的歷史面板共用）。"""
    return [
        client.get("/api/judge-rules").json(),
        client.get("/api/judge-rules/prompt_C-1/history").json(),
    ]


def _probe_prejudge_run(client: TestClient) -> list:
    """`prejudge_run_tbl`：初判批次清單與詳情（select_wire 全欄直出）。"""
    return [
        client.get("/api/v1/prejudge/runs").json(),
        client.get("/api/v1/prejudge/runs/pj_wire0001").json(),
    ]


def _probe_upload_batch(client: TestClient) -> list:
    """`upload_batch_tbl`：上傳批次清單（select_wire 全欄直出）。"""
    return [client.get("/api/batches").json()]


def _probe_evidence_snapshot(client: TestClient) -> list:
    """`evidence_snapshot_tbl`：訂單佐證快取列 → `/api/evidence/{order_oid}` 的 data。

    直接走快取讀寫（而非打端點）：端點需要 QC DB 憑證，測試環境解不出憑證會直接降級回
    `data=None`，探到的是空殼、驗不到欄名。快取列才是真正流到 wire 的那份 dict。
    """
    from datetime import datetime, timedelta, timezone

    from app.core.db import qc_evidence

    now = datetime.now(timezone.utc)
    fields = dict.fromkeys(qc_evidence._snapshot_value_columns())
    qc_evidence._cache_set(1, fields, fetched_at=now, expires_at=now + timedelta(hours=1))
    return [qc_evidence._cache_get(1)]


def _probe_llm_usage(client: TestClient) -> list:
    """`llm_usage_lst`：AI 消耗 dashboard（純聚合，欄位不直出）。"""
    return [client.get("/api/llm-usage/overview").json()]


def _probe_setting(client: TestClient) -> list:
    """`setting_master`：設定端點（只取 `setting_value` JSONB 的內容，欄位不直出）。"""
    return [client.get("/api/settings").json(), client.get("/api/settings/raw").json()]


# 表名 → (探針, marker)。marker＝「該表的別名欄 Python key 之一」，用來證明探針真的碰到了這張表
# 的資料（否則探到空列表也會 vacuously 通過）；None 表示該表**無欄位直出端點**（只出聚合值或
# JSONB 內容），探針仍照掃一遍，防日後有人改成 `select(表)` 全欄直出而無人察覺。
_ALIASED_TABLE_PROBES: dict[str, tuple[Callable[[TestClient], list], str | None]] = {
    "attribution_tbl": (_probe_attribution, "action"),
    "attribution_event_lst": (_probe_attribution_event, "attributions"),
    "evidence_snapshot_tbl": (_probe_evidence_snapshot, "fetched_at"),
    "judge_rule_version_lst": (_probe_judge_rule_version, "version"),
    "llm_usage_lst": (_probe_llm_usage, None),
    "prejudge_run_tbl": (_probe_prejudge_run, "kind"),
    "setting_master": (_probe_setting, None),
    "upload_batch_tbl": (_probe_upload_batch, "name"),
}


def test_every_aliased_table_has_wire_probe():
    """每張含別名欄的表都必須登記探針 —— 新增別名欄的表時本測試先紅，逼著補上覆蓋。"""
    derived = set(_aliased_columns())
    missing = sorted(derived - set(_ALIASED_TABLE_PROBES))
    stale = sorted(set(_ALIASED_TABLE_PROBES) - derived)
    assert not missing, (
        f"這些表有 `key != name` 的別名欄卻沒有 wire 探針：{missing}。"
        f"請在 _ALIASED_TABLE_PROBES 補上探針（找出會把該表直出到 wire 的端點）。"
    )
    assert not stale, f"這些表已無別名欄，探針該退場：{stale}"


@pytest.mark.parametrize("table_name", sorted(_aliased_columns()))
def test_aliased_table_wire_uses_python_keys(client, table_name):
    """別名欄表流到 wire 的樣本，key 必須是 Python/wire 名而非 DB 規範名。"""
    probe, marker = _ALIASED_TABLE_PROBES[table_name]
    aliased = _aliased_columns()[table_name]
    keys = _keys_deep(probe(client))
    leaked = sorted(set(aliased) & keys)
    assert not leaked, (
        f"{table_name} 把 DB 規範名漏到 wire：{leaked}（應為 "
        f"{[aliased[n] for n in leaked]}）。投影缺 `.label(c.key)` —— 改用 "
        f"`_shared.select_wire()`，自訂欄位投影則逐欄 `.label()`。"
    )
    if marker is not None:
        assert marker in keys, (
            f"{table_name} 的探針沒探到該表的資料（找不到 marker `{marker}`），"
            f"斷言等於空跑；請補夾具或修正探針。"
        )


# ── 反向護欄：全端點掃描 ─────────────────────────────────────────────────────

# 掃描時跳過的端點：SSE 串流會一直掛著、下載端點回二進位，都不是 JSON wire。
_SCAN_SKIP_MARKERS = ("stream", "/download", "/files/")

# 路徑參數 → 掃描用值（對齊 `seeded` 夾具）。含未列參數的路徑一律跳過。
_SCAN_PATH_PARAMS = {"code": "prompt_C-1", "job_id": "pj_wire0001", "version": "1"}


def _forbidden_db_names() -> set[str]:
    """不得出現在任何 wire 回傳上的 DB 規範名（自 metadata 派生）。

    兩處排除：
    - `*_oid`：serial PK 的 DB 名即 wire 名（`attribution_oid`），本來就該出現在 wire 上。
    - 在**別的表**是正常欄名者（`create_user` / `create_date` / `modify_date`）：稽核四欄與
      來源鏡像表的原生欄名撞名，列入會誤報。這些欄的漏名由上方 per-table 探針負責兜。
    """
    aliased = {n for cols in _aliased_columns().values() for n in cols if not n.endswith("_oid")}
    genuine = {c.name for t in T.metadata.sorted_tables for c in t.columns if c.key == c.name}
    return aliased - genuine


def test_no_db_canonical_name_on_any_get_endpoint(client):
    """反向護欄：掃所有註冊的 GET 端點樣本，出現任何 DB 規範名即紅燈。

    per-table 探針靠人登記端點、仍可能登漏；本測試不需要知道哪個端點碰哪張表，
    只認「規範名不該出現在 wire 上」這條不變式，是最後一道網。
    """
    forbidden = _forbidden_db_names()
    assert forbidden, "派生不到任何 DB 規範名，護欄等於沒開"

    scanned: list[str] = []
    leaks: dict[str, list[str]] = {}
    for path, ops in client.app.openapi()["paths"].items():
        if "get" not in ops or any(m in path for m in _SCAN_SKIP_MARKERS):
            continue
        params = [seg[1:-1] for seg in path.split("/") if seg.startswith("{")]
        if any(p not in _SCAN_PATH_PARAMS for p in params):
            continue
        url = path
        for p in params:
            url = url.replace(f"{{{p}}}", _SCAN_PATH_PARAMS[p])
        scanned.append(path)
        r = client.get(url)
        if r.status_code != 200:
            continue
        if hit := sorted(forbidden & _keys_deep(r.json())):
            leaks[path] = hit

    # 帶路徑參數的端點必須進得了掃描範圍——本次事故（規則版本歷史）正是這種端點，
    # 護欄若只掃無參數路徑就會跟事故擦身而過。
    assert "/api/judge-rules/{code}/history" in scanned, "掃描漏了帶路徑參數的端點"
    assert not leaks, (
        f"以下端點把 DB 規範名漏到 wire：{leaks}。"
        f"改用 `_shared.select_wire()`，自訂欄位投影則逐欄 `.label()`。"
    )
