"""上傳匯入背景 job：逐工作表分塊處理 + 進度快照（前端輪詢畫進度條，每表分開）。

沿用 `prejudge_batch` 的 in-mem registry + 背景 thread 模式。上傳為 DB 寫入 I/O bound，
單背景 thread 逐表逐塊（每塊 _CHUNK 列）處理即可，不需 ThreadPool。每張工作表獨立一段進度，
前端據 `sheets[].processed / total` 畫各自進度條；job 重啟即清（單機夠用）。
"""

from __future__ import annotations

import logging
import threading
import uuid

from app.core import db
from app.core import source_mapping as srcmap
from app.judge.ingest import entry
from app.judge.ingest import product_reviews as product_reviews_ingest

_log = logging.getLogger(__name__)

# in-mem job 進度快照（job_id → snapshot dict）；rows 另存 _job_rows（量大，不回前端）。
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_job_rows: dict[str, list[dict]] = {}

_CHUNK = 1000  # 每塊列數：兼顧進度更新頻率與寫入效率


def _set_status(job_id: str, status: str) -> None:
    """設定 job 終態（thread-safe）。"""
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = status


def _bump_sheet(job_id: str, idx: int, *, add: dict | None = None, set_: dict | None = None) -> None:
    """更新第 idx 張工作表進度（add=累加欄位 / set_=覆寫欄位；thread-safe）。"""
    with _jobs_lock:
        snap = _jobs.get(job_id)
        if snap is None:
            return
        sh = snap["sheets"][idx]
        for k, v in (add or {}).items():
            sh[k] += v
        for k, v in (set_ or {}).items():
            sh[k] = v


def _append_errors(job_id: str, idx: int, errs: list[str]) -> None:
    """把該表壞列原因累積進快照（最多 10 筆，供前端顯示排查）。"""
    if not errs:
        return
    with _jobs_lock:
        snap = _jobs.get(job_id)
        if snap is None:
            return
        cur = snap["sheets"][idx]["errors"]
        cur.extend(errs[: max(0, 10 - len(cur))])


def _process_product_reviews(job_id: str, idx: int, rows: list[dict]) -> int:
    """product_reviews：分塊 transform + upsert 專表；逐塊回報進度。回 inserted 總數。"""
    total = 0
    for start in range(0, len(rows), _CHUNK):
        chunk = rows[start : start + _CHUNK]
        errs: list[str] = []
        pr_rows = []
        for row in chunk:
            try:
                pr_rows.append(
                    product_reviews_ingest.row_to_product_review(
                        srcmap.normalize_row("product_reviews", row), row
                    )
                )
            except Exception as ex:  # noqa: BLE001 — 單列轉換失敗只跳過該列並記因
                errs.append(f"transform: {type(ex).__name__}: {str(ex)[:120]}")
        inserted = db.insert_product_reviews_batch(pr_rows, errors=errs)
        total += inserted
        _bump_sheet(
            job_id, idx,
            add={"processed": len(chunk), "inserted": inserted, "failed": len(chunk) - inserted},
        )
        _append_errors(job_id, idx, errs)
    return total


def _process_generic(job_id: str, idx: int, source: str, rows: list[dict], batch_id: str) -> int:
    """其餘來源：分塊 item_from_canonical + insert_inbound_batch（intake_items）；逐塊回報。回 inserted 總數。"""
    total = 0
    for start in range(0, len(rows), _CHUNK):
        chunk = rows[start : start + _CHUNK]
        items = []
        failed = 0
        errs: list[str] = []
        for row in chunk:
            try:
                it = entry.item_from_canonical(srcmap.normalize_row(source, row), row)
                it.batch_id = batch_id
                items.append(it)
            except Exception as ex:  # noqa: BLE001
                failed += 1
                if len(errs) < 5:
                    errs.append(f"transform: {type(ex).__name__}: {str(ex)[:120]}")
        inserted = db.insert_inbound_batch(items)
        total += inserted
        _bump_sheet(
            job_id, idx,
            add={"processed": len(chunk), "inserted": inserted, "failed": failed + (len(items) - inserted)},
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
                if source == "product_reviews":
                    inserted = _process_product_reviews(job_id, idx, rows)
                    batch = db.create_batch(source, label, f"{filename}::{name}", len(rows), inserted)
                else:
                    # generic 需先有 batch_id 才能標記各列所屬批次；處理後回填實際筆數
                    batch = db.create_batch(source, label, f"{filename}::{name}", len(rows), 0)
                    inserted = _process_generic(job_id, idx, source, rows, batch["batch_id"])
                    db.update_batch_inserted(batch["batch_id"], inserted)
                _bump_sheet(job_id, idx, set_={"status": "done", "batch_id": batch["batch_id"]})
            except Exception:  # noqa: BLE001 — 單表失敗隔離，不阻斷其餘表
                _log.exception("上傳單表失敗 job=%s sheet=%s", job_id, name)
                _bump_sheet(job_id, idx, set_={"status": "error"})
            with _jobs_lock:
                snap = _jobs.get(job_id)
                if snap:
                    snap["done_sheets"] += 1
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
        selections: 勾選清單 [{"sheet_name", "source"}]（來自 /validate 後）。

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
            sheets_meta.append({"sheet_name": name, "source": source, "label": source,
                                 "total": 0, "valid": False, "reason": "工作表不存在"})
            continue
        missing = srcmap.validate_headers(source, sh["headers"])
        if missing:
            sheets_meta.append({"sheet_name": name, "source": source, "label": source,
                                "total": len(sh["rows"]), "valid": False,
                                "reason": f"缺必備欄：{'、'.join(missing)}"})
            continue
        label = srcmap.source_label(source)
        sheets_meta.append({"sheet_name": name, "source": source, "label": label,
                            "total": len(sh["rows"]), "valid": True, "reason": ""})
        sheets_data.append({"sheet_name": name, "source": source, "label": label, "rows": sh["rows"]})

    job_id = f"up_{uuid.uuid4().hex[:12]}"
    with _jobs_lock:
        _jobs[job_id] = {
            "status": "running",  # running → done / error
            "total_sheets": len(sheets_data),
            "done_sheets": 0,
            "sheets": [
                {"sheet_name": d["sheet_name"], "source": d["source"], "label": d["label"],
                 "total": len(d["rows"]), "processed": 0, "inserted": 0, "failed": 0,
                 "status": "pending", "batch_id": None, "errors": []}
                for d in sheets_data
            ],
            # 無效表也回報（不處理，供前端顯示原因）
            "invalid": [m for m in sheets_meta if not m["valid"]],
        }
    if not sheets_data:  # 全無效 → 直接標終態，前端顯示 invalid 原因
        _set_status(job_id, "done")
    else:
        threading.Thread(
            target=_run, args=(job_id, filename, sheets_data), name=f"upload-{job_id}", daemon=True
        ).start()
    return {"job_id": job_id, "sheets": sheets_meta}


def get_job(job_id: str) -> dict | None:
    """取 job 進度快照複本（thread-safe，深複製 sheets）；不存在回 None（端點轉 404）。"""
    with _jobs_lock:
        snap = _jobs.get(job_id)
        if snap is None:
            return None
        return {
            "status": snap["status"],
            "total_sheets": snap["total_sheets"],
            "done_sheets": snap["done_sheets"],
            "sheets": [dict(s) for s in snap["sheets"]],
            "invalid": list(snap.get("invalid", [])),
        }
