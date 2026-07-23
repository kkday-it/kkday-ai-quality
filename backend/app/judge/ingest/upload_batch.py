"""上傳匯入背景 job：逐工作表分塊處理 + 進度快照（前端輪詢畫進度條，每表分開）。

沿用 `prejudge_batch` 的 in-mem registry + 背景 thread 模式（共用機制層見
`core.job_registry.JobStore`）。上傳為 DB 寫入 I/O bound，單背景 thread 逐表逐塊
（每塊 _CHUNK 列）處理即可，不需 ThreadPool。每張工作表獨立一段進度，
前端據 `sheets[].processed / total` 畫各自進度條；job 重啟即清（單機夠用）。
"""

from __future__ import annotations

import logging
import threading
import uuid

from app.core import db
from app.core import source_mapping as srcmap
from app.core.job_registry import JobStore
from app.judge.ingest import entry

_log = logging.getLogger(__name__)

# rows 另存 _job_rows（量大，不回前端）；job 進度快照走共用 JobStore。
_store: JobStore = JobStore()
_job_rows: dict[str, list[dict]] = {}

_CHUNK = 1000  # 每塊列數：兼顧進度更新頻率與寫入效率


def _set_status(job_id: str, status: str) -> None:
    """設定 job 終態（thread-safe）。"""
    _store.set_fields(job_id, status=status)


def _bump_sheet(
    job_id: str, idx: int, *, add: dict | None = None, set_: dict | None = None
) -> None:
    """更新第 idx 張工作表進度（add=累加欄位 / set_=覆寫欄位；thread-safe）。"""

    def _apply(snap: dict) -> None:
        sh = snap["sheets"][idx]
        for k, v in (add or {}).items():
            sh[k] += v
        for k, v in (set_ or {}).items():
            sh[k] = v

    _store.mutate(job_id, _apply)


def _append_errors(job_id: str, idx: int, errs: list[str]) -> None:
    """把該表壞列原因累積進快照（最多 10 筆，供前端顯示排查）。"""
    if not errs:
        return

    def _apply(snap: dict) -> None:
        cur = snap["sheets"][idx]["errors"]
        cur.extend(errs[: max(0, 10 - len(cur))])

    _store.mutate(job_id, _apply)


# mixpanel $ / 大寫欄名 → 淨化為合法 SQL 欄名（對齊來源表定義；其餘來源不需）
_MIX_SANITIZE = {
    "$insert_id": "insert_id",
    "$distinct_id": "distinct_id",
    "$current_url": "current_url",
    "$os": "os",
    "Platform": "platform",
}


def _sanitize_row(source: str, row: dict) -> dict:
    """mixpanel_tracker：源列的 $ / 大寫 key 淨化為合法欄名；其餘來源原樣。"""
    if source != "mixpanel_tracker":
        return row
    return {_MIX_SANITIZE.get(k, k): v for k, v in row.items()}


def _process_source(job_id: str, idx: int, source: str, rows: list[dict]) -> int:
    """5 來源統一：分塊把原始源列（$ 淨化）直接 upsert 各自來源表；逐塊回報進度。回 inserted 總數。"""
    total = 0
    for start in range(0, len(rows), _CHUNK):
        chunk = [_sanitize_row(source, r) for r in rows[start : start + _CHUNK]]
        errs: list[str] = []
        inserted = db.insert_source_batch(source, chunk, errors=errs)
        total += inserted
        _bump_sheet(
            job_id,
            idx,
            add={"processed": len(chunk), "inserted": inserted, "failed": len(chunk) - inserted},
        )
        _append_errors(job_id, idx, errs)
    return total


def _run(job_id: str, filename: str, sheets_data: list[dict]) -> None:
    """背景：逐表處理，每表結束建 / 回填批次記錄 + 標記狀態；全部完成標 done。"""
    try:
        for idx, sd in enumerate(sheets_data):
            source, label, name, rows = sd["source"], sd["label"], sd["sheet_name"], sd["rows"]
            _bump_sheet(job_id, idx, set_={"status": "running"})
            try:
                # 5 來源統一：原始源列（$ 淨化）直接 upsert 各自來源表（衝突鍵＝特徵 id）
                inserted = _process_source(job_id, idx, source, rows)
                batch = db.create_batch(
                    source,
                    label,
                    f"{filename}::{name}",
                    len(rows),
                    inserted,
                    note=sd.get("note", ""),
                )
                _bump_sheet(job_id, idx, set_={"status": "done", "batch_id": batch["batch_id"]})
            except Exception:  # noqa: BLE001 — 單表失敗隔離，不阻斷其餘表
                _log.exception("上傳單表失敗 job=%s sheet=%s", job_id, name)
                _bump_sheet(job_id, idx, set_={"status": "error"})
            _store.mutate(
                job_id, lambda snap: snap.update({"done_sheets": snap["done_sheets"] + 1})
            )
        _set_status(job_id, "done")
    except Exception:  # noqa: BLE001
        _log.exception("上傳批量任務失敗 job=%s", job_id)
        _set_status(job_id, "error")
    finally:
        _job_rows.pop(job_id, None)


def start_upload_job(content: bytes, filename: str, selections: list[dict]) -> dict:
    """解析檔案 → 校驗勾選工作表 → 註冊 job 並背景啟動；立即回 {job_id, sheets}（不阻塞落庫）。

    Args:
        content: 上傳檔 bytes。
        filename: 原始檔名（CSV/xlsx 判別 + 批次命名）。
        selections: 勾選清單 [{"sheet_name", "source", "note"}]（來自 /validate 後；note＝用戶備註，隨批次保存）。

    Returns:
        {"job_id", "sheets": [{"sheet_name","source","label","total","valid","reason"}]}；
        校驗不過的表 valid=False 附 reason，不進背景處理。

    Raises:
        ValueError: 副檔名非 .csv/.xlsx/.xls（entry.read_sheets 拋）。
    """
    sheets = {sh["sheet_name"]: sh for sh in entry.read_sheets(content, filename)}
    sheets_meta: list[dict] = []  # 回前端（含無效表說明）
    sheets_data: list[dict] = []  # 進背景處理（僅有效表）
    for sel in selections:
        name = sel.get("sheet_name")
        source = sel.get("source") or ""
        sh = sheets.get(name)
        if sh is None:
            sheets_meta.append(
                {
                    "sheet_name": name,
                    "source": source,
                    "label": source,
                    "total": 0,
                    "valid": False,
                    "reason": "工作表不存在",
                }
            )
            continue
        missing = srcmap.validate_headers(source, sh["headers"])
        if missing:
            sheets_meta.append(
                {
                    "sheet_name": name,
                    "source": source,
                    "label": source,
                    "total": len(sh["rows"]),
                    "valid": False,
                    "reason": f"缺必備欄：{'、'.join(missing)}",
                }
            )
            continue
        label = srcmap.source_label(source)
        sheets_meta.append(
            {
                "sheet_name": name,
                "source": source,
                "label": label,
                "total": len(sh["rows"]),
                "valid": True,
                "reason": "",
            }
        )
        sheets_data.append(
            {
                "sheet_name": name,
                "source": source,
                "label": label,
                "rows": sh["rows"],
                "note": (sel.get("note") or "").strip(),
            }
        )

    job_id = f"up_{uuid.uuid4().hex[:12]}"
    _store.put(
        job_id,
        {
            "status": "running",  # running → done / error
            "total_sheets": len(sheets_data),
            "done_sheets": 0,
            "sheets": [
                {
                    "sheet_name": d["sheet_name"],
                    "source": d["source"],
                    "label": d["label"],
                    "total": len(d["rows"]),
                    "processed": 0,
                    "inserted": 0,
                    "failed": 0,
                    "status": "pending",
                    "batch_id": None,
                    "errors": [],
                }
                for d in sheets_data
            ],
            # 無效表也回報（不處理，供前端顯示原因）
            "invalid": [m for m in sheets_meta if not m["valid"]],
        },
    )
    if not sheets_data:  # 全無效 → 直接標終態，前端顯示 invalid 原因
        _set_status(job_id, "done")
    else:
        threading.Thread(
            target=_run, args=(job_id, filename, sheets_data), name=f"upload-{job_id}", daemon=True
        ).start()
    return {"job_id": job_id, "sheets": sheets_meta}


def get_job(job_id: str) -> dict | None:
    """取 job 進度快照複本（thread-safe，深複製；`JobStore.get` 對巢狀 sheets/invalid 亦完整深拷貝）；
    不存在回 None（端點轉 404）。"""
    return _store.get(job_id)


def mark_running_interrupted() -> list[str]:
    """graceful shutdown 收尾：把仍在 running 的上傳落庫 job 標 interrupted（語義同 export_jobs）。"""
    return _store.mark_interrupted(running_statuses=("running",), new_status="interrupted")
