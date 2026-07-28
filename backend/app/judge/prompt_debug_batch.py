"""Prompt 調試台批量跑批：上傳 CSV/XLSX → 以當前 Prompt/契約逐條結構化裁決 → 斷點續跑。

定位＝把離線 lab 跑批（tmp 的 run_batch_v3 語義）搬進 app 原生執行：容器內看不到 host 的
tmp/ 與 .venv-promptlab，故不是包 subprocess，而是复用調試台既有機制重寫同語義批次：

- LLM 連線＝prompt_debug 功能區設定（DB 加密 token，等同腳本 `--api-key-source app`）；
- 輸出契約/schema/校驗＝prompt_debug 單一契約（與調試台同源，無版本切換）；
- Prompt＝呼叫端未給就取版本庫最新版（`prompt_debug_versions.resolve`），與調試台同一份；
- run 目錄＝`DATA_DIR/prompt_debug_batch/<run_id>/`（dev 掛 ./data，host 直接可取產物）；
- `raw_results.jsonl` 逐筆 flush＝斷點：resume 只補「未成功」筆、rerun 忽略斷點全部重打；
- manifest 鎖 輸入/Prompt/schema/model——SSOT 變了就拒絕續跑，防混用結果。

與正式初判（prejudge_batch）刻意分離：這裡不落 attributions / 不走判準 loader，只是調試工具；
job 進度走共用 JobStore（in-mem，重啟即清），但 run 目錄在磁碟上——server 重啟後列表仍可見、
可續跑（uvicorn --reload 的 dev 環境尤其常見）。
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import shutil
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core import db
from app.core.job_registry import JobStore
from app.core.paths import DATA_DIR
from app.judge import prompt_debug, prompt_debug_versions
from app.judge.llm import client

_log = logging.getLogger(__name__)

BATCH_DIR = DATA_DIR / "prompt_debug_batch"

# 併發/規模守門（調試工具 guardrail，非業務可調值）：workers 上限對齊 OpenAI 常規 org 併發水位；
# 行數上限防誤上傳全量大表（正式全量請走批次管線，調試台定位是百~數千條的 Prompt 驗證）。
_WORKERS_CAP = 32
_MAX_ROWS = 20_000
_MAX_FAILED_ITEMS = 200  # 失敗明細清單上限：系統性失敗只計數不細列，防快照撐爆
_RECENT_ITEMS = 8  # 快照內最近完成明細條數（前端「即時回報」用，全量明細在 jsonl）

_TERMINAL_STATUSES = ("done", "error", "cancelled", "interrupted")

_store: JobStore = JobStore()
# 每 run 一個協作式取消旗標（與快照同生命週期但非 JSON-safe，不進 JobStore；同 prejudge_batch 慣例）
_cancels: dict[str, threading.Event] = {}
_cancels_lock = threading.Lock()


# ── 輸入解析（CSV/XLSX → 有效唯一行）─────────────────────────────────────────────


@dataclass(frozen=True)
class InputRow:
    """一條可送判的輸入及其源檔行號（1-based，含表頭前的雜訊行）。"""

    item_id: str
    conversation: str
    source_row: int


def _clean_cell(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _find_header_row(rows: list[list[Any]], id_column: str, text_column: str) -> int:
    """回傳 0-based 表頭索引：前 20 行內同時含 id/text 欄名者（兼容表頭前有分組雜訊行的匯出格式）。

    Raises:
        ValueError: 前 20 行找不到同時包含兩個關鍵欄名的表頭。
    """
    for idx, row in enumerate(rows[:20]):
        header = {_clean_cell(v) for v in row}
        if id_column in header and text_column in header:
            return idx
    raise ValueError(f"前 20 行找不到同時包含 {id_column!r} 和 {text_column!r} 的表頭")


def load_input_rows(
    path: Path, *, sheet: str, id_column: str, text_column: str
) -> tuple[list[InputRow], dict[str, int]]:
    """讀取 CSV/XLSX 並抽出有效唯一行（去空 id / 空對話 / 重複 id）。

    Args:
        path: 輸入檔（.csv 用 utf-8-sig 兼容 BOM；.xlsx/.xlsm 只讀載入）。
        sheet: XLSX 工作表名；空字串＝取第一個工作表；CSV 忽略。
        id_column: 唯一 ID 欄名。
        text_column: 送模型的完整對話欄名。

    Returns:
        (有效行清單, 解析統計 dict)——統計進 manifest/快照，讓 limit 語義可核對。

    Raises:
        ValueError: 副檔名不支援、工作表不存在、找不到表頭、關鍵欄名重複。
    """
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            raw_rows: list[list[Any]] = list(csv.reader(fh))
    elif suffix in {".xlsx", ".xlsm"}:
        import openpyxl  # 重庫 lazy import：僅 XLSX 輸入才載

        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            if sheet and sheet not in workbook.sheetnames:
                raise ValueError(f"工作表不存在：{sheet}；可選={workbook.sheetnames}")
            worksheet = workbook[sheet] if sheet else workbook[workbook.sheetnames[0]]
            raw_rows = [list(row) for row in worksheet.iter_rows(values_only=True)]
        finally:
            workbook.close()
    else:
        raise ValueError(f"只支援 .csv/.xlsx/.xlsm，實際輸入：{path.name}")

    header_idx = _find_header_row(raw_rows, id_column, text_column)
    header = [_clean_cell(v) for v in raw_rows[header_idx]]
    if header.count(id_column) != 1 or header.count(text_column) != 1:
        raise ValueError(
            f"關鍵欄位必須各出現一次：{id_column}×{header.count(id_column)}、"
            f"{text_column}×{header.count(text_column)}"
        )
    id_idx = header.index(id_column)
    text_idx = header.index(text_column)

    valid: list[InputRow] = []
    seen: set[str] = set()
    stats = {
        "header_row": header_idx + 1,
        "physical_rows": 0,
        "empty_ids": 0,
        "empty_conversations": 0,
        "duplicate_ids": 0,
    }
    for source_row, raw in enumerate(raw_rows[header_idx + 1 :], start=header_idx + 2):
        if not raw or not any(_clean_cell(v) for v in raw):
            continue
        stats["physical_rows"] += 1
        item_id = _clean_cell(raw[id_idx] if id_idx < len(raw) else "")
        conversation = _clean_cell(raw[text_idx] if text_idx < len(raw) else "")
        if not item_id:
            stats["empty_ids"] += 1
            continue
        if item_id in seen:
            stats["duplicate_ids"] += 1
            continue
        seen.add(item_id)
        if not conversation:
            stats["empty_conversations"] += 1
            continue
        valid.append(InputRow(item_id=item_id, conversation=conversation, source_row=source_row))
    stats["valid_rows"] = len(valid)
    return valid, stats


# ── run 目錄與產物 ───────────────────────────────────────────────────────────────


def _run_dir(run_id: str) -> Path:
    """run_id → run 目錄；拒絕路徑穿越（run_id 來自 URL path 參數）。"""
    if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id:
        raise ValueError(f"非法 run_id：{run_id!r}")
    return BATCH_DIR / run_id


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_hash(value: Any) -> str:
    return _sha256_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _write_json_atomic(path: Path, value: Any) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _csv_columns(id_column: str) -> list[str]:
    """結果 CSV 欄序＝id 欄 + 契約欄位卡順序（欄位卡是契約欄序 SSOT，勿另抄一份）。"""
    return [id_column] + [f["key"] for f in prompt_debug.OUTPUT_FIELDS]


def _csv_cell(value: Any) -> Any:
    """結構化值 → CSV 儲存格：bool 用 TRUE/FALSE、陣列頓號連接、null 留空（對齊裁判表口徑）。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, list):
        return "、".join(str(v) for v in value)
    return value


def _csv_row(item_id: str, parsed: dict, columns: list[str]) -> dict[str, Any]:
    """條件欄（cause/target）落表紀律：n/a 哨兵與不合法情境的值一律留空。

    JSON（preds/raw）保留原值可稽核；表格層對齊裁判表口徑（用戶要求：表中不得出現 n/a 與 null）。
    """
    is_oot = parsed.get("category") == "__OUT_OF_TAXONOMY__"
    allowed = {
        "likely_cause": not is_oot,
        # 認 theme_code 前綴、不比對全稱：全稱由 config SSOT 的 theme_code+theme_label 拼出
        # （目前為「[93]訂單申請修改」無空格），寫死全稱曾因多一個空格而讓 [93] 的 modify_target 全被清空
        "modify_target": str(parsed.get("theme") or "").startswith("[93]"),
    }
    row: dict[str, Any] = {columns[0]: item_id}
    for column in columns[1:]:
        value = parsed.get(column)
        # 無分類統一「其他」（20260727 拍板）：契約升版前先在落表層映射舊哨兵
        if column == "category" and value == "__OUT_OF_TAXONOMY__":
            value = "其他"
        if column == "likely_cause" and value == "unclear":
            value = "其他"
        if column in allowed and (not allowed[column] or str(value).strip().lower() == "n/a"):
            value = None
        row[column] = _csv_cell(value)
    return row


def _rebuild_results_csv(
    path: Path, selected: list[InputRow], result_by_id: dict[str, dict], columns: list[str]
) -> None:
    """原子重建結果 CSV：按輸入順序、只寫成功判定（含校驗未過者——校驗訊息在 jsonl，不擋交付）。"""
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for input_row in selected:
            record = result_by_id.get(input_row.item_id)
            if record and not record.get("error") and isinstance(record.get("parsed"), dict):
                writer.writerow(_csv_row(input_row.item_id, record["parsed"], columns))
    temp.replace(path)


def _load_completed(raw_file: Path, id_column: str) -> dict[str, dict]:
    """讀取斷點內的成功紀錄（同 id 多筆取最後）；壞行明確報錯，不靜默跳資料。

    Raises:
        ValueError: 斷點檔某行不是合法 JSON。
    """
    completed: dict[str, dict] = {}
    if not raw_file.exists():
        return completed
    with raw_file.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"斷點檔第 {line_no} 行不是合法 JSON：{raw_file.name}") from exc
            item_id = _clean_cell(record.get(id_column))
            if item_id and isinstance(record.get("parsed"), dict) and not record.get("error"):
                completed[item_id] = record
    return completed


def _jsonl_spend(raw_file: Path) -> tuple[float, int]:
    """run 目錄歷來實際 API 花費/token 總和（跨 attempt 累計；rerun 重打同樣累計）。

    費用口徑取 jsonl 逐筆紀錄而非上一份 summary——續跑 attempt 的快照若從 0 起算會把
    累計費用「歸零覆蓋」進 summary（實測踩過），逐筆加總才是 run 目錄真實燒錢數。
    """
    cost = 0.0
    tokens = 0
    if not raw_file.exists():
        return cost, tokens
    with raw_file.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            cost += float(record.get("cost_usd") or 0.0)
            tokens += int(record.get("input_tokens") or 0) + int(record.get("output_tokens") or 0)
    return round(cost, 6), tokens


# ── job 快照 ────────────────────────────────────────────────────────────────────


def _new_snapshot(manifest: dict, *, total: int, resumed: int, pending: int) -> dict:
    """初始進度快照（欄位對齊前端 PromptDebugBatchDrawer 消費端）。"""
    return {
        # 狀態機：running → done｜running → cancelling → cancelled｜error；
        # interrupted 僅出現在磁碟推導（server 重啟令 in-mem job 蒸發）
        "status": "running",
        "run_id": manifest["run_id"],
        "prompt_version": manifest.get("prompt_version", ""),
        "model": manifest["model"],
        "input_name": manifest["input_name"],
        "created_at": manifest["created_at"],
        "total": total,  # 本次選中目標（offset/limit 後）
        "resumed": resumed,  # 斷點復用的成功筆
        "pending": pending,  # 本次實際要請求的筆數
        "processed": 0,  # 本次已完成請求數（成功+失敗）
        "ok": resumed,  # 累計成功（含復用）
        "failed": 0,
        "invalid": 0,  # 成功但欄位校驗未過（詳情在 jsonl.validation_issues）
        "total_tokens": 0,
        "cost_usd": 0.0,
        "started_at": time.time(),
        "warnings": [],  # 相容端點降級等一次性警告
        "recent": [],  # 最近完成明細環（前端即時回報）
        "failed_items": [],
        "failed_items_truncated": False,
        "_created_at": time.time(),
    }


def _bump(run_id: str, record: dict, cost_usd: float, total_tokens: int) -> None:
    """單筆完成後累加進度（僅 collector 執行緒呼叫；mutate 保證與讀取端互斥）。"""

    def _apply(snap: dict) -> None:
        snap["processed"] += 1
        ok = bool(record.get("parsed")) and not record.get("error")
        snap["ok" if ok else "failed"] += 1
        if ok and record.get("validation_issues"):
            snap["invalid"] += 1
        snap["total_tokens"] += total_tokens
        snap["cost_usd"] = round(snap["cost_usd"] + cost_usd, 6)
        parsed = record.get("parsed") or {}
        recent = snap["recent"]
        recent.insert(
            0,
            {
                "item_id": record.get("item_id", ""),
                "ok": ok,
                "theme": parsed.get("theme"),
                "category": parsed.get("category"),
                "issues": len(record.get("validation_issues") or []),
                "latency_ms": record.get("latency_ms"),
                "error": record.get("error"),
            },
        )
        del recent[_RECENT_ITEMS:]
        if not ok:
            if len(snap["failed_items"]) < _MAX_FAILED_ITEMS:
                snap["failed_items"].append(
                    {"item_id": record.get("item_id", ""), "error": record.get("error") or ""}
                )
            else:
                snap["failed_items_truncated"] = True

    _store.mutate(run_id, _apply)


# ── 批次執行本體 ─────────────────────────────────────────────────────────────────


@dataclass
class _RunPlan:
    """一次執行（create 或 resume）解析完成後的全部执行素材。"""

    run_id: str
    run_dir: Path
    manifest: dict
    selected: list[InputRow]
    pending: list[InputRow]
    result_by_id: dict[str, dict]
    columns: list[str]
    cfg: dict
    system_prompt: str
    schema: dict
    schema_name: str
    validator: Any
    taxonomy: dict
    workers: int
    usage_rows: list[dict] = field(default_factory=list)


def _build_cfg(effective: dict) -> dict:
    """effective LLM dict → client 呼叫 cfg（形狀對齊 prompt_debug.stream_frames 的單次路徑）。

    Raises:
        ValueError: 配置解不出 API token 或未指定 model（router 已前置檢查，此處為第二道防線）。
    """
    from app.core import settings as app_settings

    token = app_settings.resolve_provider_token(effective)
    if not token:
        raise ValueError("目前配置沒有可用 API token，請先在「配置 › LLM 模型連線」完成設定")
    model = (effective.get("model") or "").strip()
    if not model:
        raise ValueError("本次跑批未指定 model")
    return {
        "token": token,
        "base_url": (effective.get("base_url") or "").strip(),
        "model": model,
        "temperature": effective.get("temperature"),
        "thinking": effective.get("thinking", "default"),
        "reasoning_effort": effective.get("reasoning_effort", "default"),
        "service_tier": None,
    }


def _messages(plan: _RunPlan, row: InputRow) -> list[dict[str, str]]:
    """單筆 messages（user 包裝與單次調試逐字一致，批量與單次 A/B 才可比）。"""
    return [
        {"role": "system", "content": plan.system_prompt},
        {"role": "user", "content": prompt_debug.user_prompt_for(row.conversation)},
    ]


def _base_kwargs(plan: _RunPlan, row: InputRow) -> dict[str, Any]:
    """組一筆請求的完整 kwargs（strict json_schema）。"""
    kwargs: dict[str, Any] = {
        "model": plan.cfg["model"],
        "messages": _messages(plan, row),
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": plan.schema_name, "strict": True, "schema": plan.schema},
        },
    }
    if plan.cfg["temperature"] is not None:
        kwargs["temperature"] = float(plan.cfg["temperature"])
    kwargs.update(client._reasoning_kwargs(plan.cfg))
    return kwargs


def _settle_request_shape(probe_kwargs: dict) -> dict:
    """探測呼叫完成後，抽出「已降級收斂」的請求形狀（response_format / reasoning_effort 等）。

    相容端點的參數降級（prompt_debug._request_compat）與 reasoning_effort 降級都是就地改寫
    kwargs——只在第一筆探測時走完整降級迴圈，其餘筆直接沿用收斂後形狀，免得每筆都吃一輪 400。
    """
    return {k: v for k, v in probe_kwargs.items() if k not in ("model", "messages")}


def _record_from_response(plan: _RunPlan, row: InputRow, resp: Any, latency_ms: int) -> dict:
    """單筆回應 → jsonl 紀錄（欄位形狀對齊 lab 腳本 raw_results.jsonl，方便沿用既有分析工具）。"""
    choices = getattr(resp, "choices", None) or []
    raw = (getattr(getattr(choices[0], "message", None), "content", None) or "") if choices else ""
    parsed = client._loads_lenient(raw)
    issues = (
        plan.validator(parsed, plan.taxonomy)
        if parsed is not None
        else ["AI 輸出不是合法 JSON object"]
    )
    usage = prompt_debug._usage_payload(plan.cfg["model"], getattr(resp, "usage", None), latency_ms)
    id_column = plan.manifest["id_column"]
    return {
        id_column: row.item_id,
        **({"item_id": row.item_id} if id_column != "item_id" else {}),
        "source_row": row.source_row,
        "parsed": parsed,
        "raw_output": raw or None,
        "model": plan.cfg["model"],
        "request_id": getattr(resp, "id", None),
        "status": "ok" if parsed is not None else "bad_output",
        "error": None if parsed is not None else "AI 輸出不是合法 JSON object",
        "validation_issues": issues,
        "input_tokens": usage["prompt_tokens"],
        "output_tokens": usage["completion_tokens"],
        "cached_tokens": usage["cached_tokens"],
        "reasoning_tokens": usage["reasoning_tokens"],
        "latency_ms": latency_ms,
        "cost_usd": usage["cost_usd"],
        "completed_at": datetime.now(UTC).isoformat(),
    }


def _error_record(plan: _RunPlan, row: InputRow, exc: BaseException, latency_ms: int) -> dict:
    id_column = plan.manifest["id_column"]
    return {
        id_column: row.item_id,
        **({"item_id": row.item_id} if id_column != "item_id" else {}),
        "source_row": row.source_row,
        "parsed": None,
        "raw_output": None,
        "model": plan.cfg["model"],
        "request_id": None,
        "status": "error",
        "error": f"{type(exc).__name__}: {str(exc).splitlines()[0][:500]}"
        if str(exc).strip()
        else type(exc).__name__,
        "validation_issues": [],
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
        "latency_ms": latency_ms,
        "cost_usd": 0.0,
        "completed_at": datetime.now(UTC).isoformat(),
    }


def _usage_row(plan: _RunPlan, row: InputRow, record: dict) -> dict:
    """單筆用量 → llm_usage 落庫列（stage 同單次調試，AI 消耗看板口徑一致；source 區分批量）。"""
    from app.core import settings as app_settings

    return {
        "stage": "prompt_debug",
        "model": plan.cfg["model"],
        "provider": app_settings.provider_id_for(plan.cfg["base_url"]),
        "prompt_tokens": record["input_tokens"],
        "completion_tokens": record["output_tokens"],
        "reasoning_tokens": record["reasoning_tokens"],
        "cached_tokens": record["cached_tokens"],
        "total_tokens": record["input_tokens"] + record["output_tokens"],
        "cost_usd": record["cost_usd"],
        "source": "prompt_debug_batch",
        "source_id": row.item_id,
        "job_id": plan.run_id,
    }


def _collect_one(
    plan: _RunPlan, row: InputRow, record: dict, raw_fh: Any, csv_fh: Any, csv_writer: Any
) -> None:
    """collector 收單筆結果：jsonl 逐筆 flush（斷點）→ 成功即追加 CSV → 進度快照累加。"""
    raw_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    raw_fh.flush()
    plan.result_by_id[row.item_id] = record
    if record.get("parsed") and not record.get("error"):
        csv_writer.writerow(_csv_row(row.item_id, record["parsed"], plan.columns))
        csv_fh.flush()
        plan.usage_rows.append(_usage_row(plan, row, record))
    elif record.get("input_tokens"):
        plan.usage_rows.append(_usage_row(plan, row, record))
    _bump(
        plan.run_id,
        record,
        record.get("cost_usd") or 0.0,
        (record.get("input_tokens") or 0) + (record.get("output_tokens") or 0),
    )


def _finalize(plan: _RunPlan, status: str) -> None:
    """收尾：重建有序 CSV、寫 preds.json / summary.json、flush llm_usage（皆 best-effort 不互相阻斷）。"""
    try:
        _rebuild_results_csv(
            plan.run_dir / "results.csv", plan.selected, plan.result_by_id, plan.columns
        )
        preds = {
            row.item_id: plan.result_by_id[row.item_id]["parsed"]
            for row in plan.selected
            if row.item_id in plan.result_by_id
            and isinstance(plan.result_by_id[row.item_id].get("parsed"), dict)
            and not plan.result_by_id[row.item_id].get("error")
        }
        _write_json_atomic(plan.run_dir / "preds.json", preds)
    except Exception:  # noqa: BLE001 - 產物重建失敗不影響 jsonl 斷點本體
        _log.exception("跑批產物收尾失敗 run=%s", plan.run_id)
    try:
        db.insert_llm_usage_rows(plan.usage_rows)
    except Exception:  # noqa: BLE001 - 計費紀錄 best-effort
        _log.debug("llm_usage flush 失敗 run=%s", plan.run_id)
    _store.set_fields(plan.run_id, status=status)
    summary = _public(_store.get(plan.run_id) or {})
    summary["finished_at"] = datetime.now(UTC).isoformat()
    try:
        _write_json_atomic(plan.run_dir / "summary.json", summary)
    except Exception:  # noqa: BLE001
        _log.exception("跑批 summary 落盤失敗 run=%s", plan.run_id)
    with _cancels_lock:
        _cancels.pop(plan.run_id, None)


def _run_batch(plan: _RunPlan) -> None:
    """背景執行整批：首筆探測（參數降級收斂 + fail-fast）→ ThreadPool 併發 → 逐筆落盤 → 收尾。"""
    cancel = _cancels.get(plan.run_id) or threading.Event()
    raw_file = plan.run_dir / "raw_results.jsonl"
    csv_file = plan.run_dir / "results.csv"
    try:
        # 先把「斷點復用」筆寫進 CSV（本次新完成的走 append），保證中斷時 CSV 也含復用部分
        _rebuild_results_csv(csv_file, plan.selected, plan.result_by_id, plan.columns)

        def call_one(row: InputRow, extra: dict[str, Any]) -> dict:
            started = time.monotonic()
            kwargs = {
                "model": plan.cfg["model"],
                "messages": _messages(plan, row),
                **{k: (dict(v) if isinstance(v, dict) else v) for k, v in extra.items()},
            }
            try:
                resp = client._complete_effort_safe(plan.cfg, kwargs, None, "prompt_debug_batch")
                return _record_from_response(
                    plan, row, resp, int((time.monotonic() - started) * 1000)
                )
            except Exception as exc:  # noqa: BLE001 - 單筆失敗隔離，不炸整批
                return _error_record(plan, row, exc, int((time.monotonic() - started) * 1000))

        with (
            raw_file.open("a", encoding="utf-8") as raw_fh,
            csv_file.open("a", encoding="utf-8", newline="") as csv_fh,
        ):
            csv_writer = csv.DictWriter(csv_fh, fieldnames=plan.columns, extrasaction="ignore")
            settled_extra: dict[str, Any] | None = None
            pending = list(plan.pending)

            if pending and not cancel.is_set():
                # 首筆探測：走完整相容降級迴圈（一次收斂 response_format / reasoning_effort），
                # 同時 fail-fast——配置級錯誤（壞 key / 壞 model）在第一筆就終止，不燒整批。
                probe_row = pending.pop(0)
                probe_kwargs = _base_kwargs(plan, probe_row)
                started = time.monotonic()
                resp, warnings = prompt_debug._request_compat(plan.cfg, probe_kwargs)
                if warnings:
                    _store.mutate(plan.run_id, lambda s: s["warnings"].extend(warnings))
                record = _record_from_response(
                    plan, probe_row, resp, int((time.monotonic() - started) * 1000)
                )
                _collect_one(plan, probe_row, record, raw_fh, csv_fh, csv_writer)
                settled_extra = _settle_request_shape(probe_kwargs)

            if pending and not cancel.is_set():
                extra = settled_extra if settled_extra is not None else {}
                with ThreadPoolExecutor(max_workers=plan.workers) as pool:
                    future_to_row: dict[Future, InputRow] = {
                        pool.submit(call_one, row, extra): row for row in pending
                    }
                    for future in as_completed(future_to_row):
                        row = future_to_row[future]
                        if future.cancelled():
                            continue
                        record = future.result()  # call_one 已把例外轉 error record，不會拋
                        _collect_one(plan, row, record, raw_fh, csv_fh, csv_writer)
                        if cancel.is_set():
                            for f in future_to_row:
                                f.cancel()  # 未起跑的取消；已在飛的 drain 完落盤

        _finalize(plan, "cancelled" if cancel.is_set() else "done")
    except Exception as exc:  # noqa: BLE001 - 整批級失敗（IO/首筆探測/初始化）→ 標 error 供前端停輪詢
        _log.exception("Prompt 調試跑批失敗 run=%s", plan.run_id)
        message = str(exc).splitlines()[0][:500] if str(exc).strip() else type(exc).__name__
        _store.set_fields(plan.run_id, error=message)
        _finalize(plan, "error")


# ── 對外 API（router 消費）──────────────────────────────────────────────────────


def _prepare_plan(
    run_dir: Path, manifest: dict, effective: dict, *, workers: int, rerun: bool
) -> _RunPlan:
    """由 run 目錄 + manifest 組出執行素材（create / resume 共用）。

    Raises:
        ValueError: 輸入解析失敗、offset/limit 選不出資料、或 schema 已與 manifest 不相容。
    """
    taxonomy = prompt_debug.load_taxonomy()
    schema = prompt_debug.output_schema(taxonomy)
    if _json_hash(schema) != manifest["schema_sha256"]:
        raise ValueError("分類 SSOT 已變動，輸出 schema 與本 run 斷點不相容；請開新跑批")
    system_prompt = (run_dir / "system_prompt.md").read_text(encoding="utf-8")

    rows, _stats = load_input_rows(
        run_dir / "input" / manifest["input_name"],
        sheet=manifest.get("sheet") or "",
        id_column=manifest["id_column"],
        text_column=manifest["text_column"],
    )
    selected = rows[manifest["offset"] :]
    if manifest["limit"]:
        selected = selected[: manifest["limit"]]
    if not selected:
        raise ValueError(
            f"沒有可跑資料：有效行={len(rows)}，offset={manifest['offset']}，limit={manifest['limit']}"
        )
    if len(selected) > _MAX_ROWS:
        raise ValueError(f"目標 {len(selected)} 條超過跑批上限 {_MAX_ROWS}；請用 limit 分段")

    completed = (
        {} if rerun else _load_completed(run_dir / "raw_results.jsonl", manifest["id_column"])
    )
    selected_ids = {row.item_id for row in selected}
    resumed_records = {k: v for k, v in completed.items() if k in selected_ids}
    pending = [row for row in selected if row.item_id not in resumed_records]

    return _RunPlan(
        run_id=manifest["run_id"],
        run_dir=run_dir,
        manifest=manifest,
        selected=selected,
        pending=pending,
        result_by_id=dict(resumed_records),
        columns=_csv_columns(manifest["id_column"]),
        cfg=_build_cfg(effective),
        system_prompt=system_prompt,
        schema=schema,
        schema_name=prompt_debug._SCHEMA_NAME,
        validator=prompt_debug.validate_result,
        taxonomy=taxonomy,
        workers=max(1, min(int(workers), _WORKERS_CAP)),
    )


def _public(snapshot: dict) -> dict:
    """快照對外視圖：剝除底線開頭的內部欄位（如 sweep 用 _created_at），API 契約只含業務欄。"""
    return {k: v for k, v in snapshot.items() if not k.startswith("_")}


def _launch(plan: _RunPlan, triggered_by: str) -> dict:
    """註冊快照 + 背景執行緒起跑；回傳初始快照。"""
    _store.sweep_terminal(24 * 3600, _TERMINAL_STATUSES)  # 終態快照保留一天供回看，防無限增長
    snapshot = _new_snapshot(
        plan.manifest,
        total=len(plan.selected),
        resumed=len(plan.result_by_id),
        pending=len(plan.pending),
    )
    # 費用/token 以 run 目錄累計起算（含先前 attempt），_bump 疊加本次新呼叫
    snapshot["cost_usd"], snapshot["total_tokens"] = _jsonl_spend(
        plan.run_dir / "raw_results.jsonl"
    )
    snapshot["triggered_by"] = triggered_by
    _store.put(plan.run_id, snapshot)
    with _cancels_lock:
        _cancels[plan.run_id] = threading.Event()
    threading.Thread(
        target=_run_batch, args=(plan,), name=f"pdbatch-{plan.run_id}", daemon=True
    ).start()
    return _public(_store.get(plan.run_id) or snapshot)


def create_and_start(
    *,
    input_name: str,
    input_bytes: bytes,
    sheet: str,
    id_column: str,
    text_column: str,
    offset: int,
    limit: int,
    workers: int,
    system_prompt: str,
    overrides: dict | None,
    effective: dict,
    triggered_by: str = "",
) -> dict:
    """建立 run 目錄（存輸入/Prompt/manifest）並啟動跑批；回傳初始進度快照。

    Args:
        input_name: 上傳檔原名（僅取 basename 存放）。
        input_bytes: 上傳檔內容。
        sheet: XLSX 工作表名（空＝第一個；CSV 忽略）。
        id_column/text_column: 關鍵欄名（預設 session_oid / conversation_full）。
        offset/limit: 有效唯一行的切片（limit 0＝全部）。
        workers: 併發請求數（上限 _WORKERS_CAP）。
        system_prompt: 本批固定使用的 system prompt（存檔為斷點依據）；
            空字串＝取 Prompt 版本庫最新版，跑批與調試台永遠同一份口徑。
        overrides: 本次 LLM 旋鈕覆寫原始 dict（進 manifest，續跑時重放）。
        effective: router 解析好的 effective LLM dict（含 token 來源，不落盤）。
        triggered_by: 觸發人（user email）。

    Returns:
        初始進度快照（含 run_id）。

    Raises:
        ValueError: 參數 / 輸入解析錯誤（router 轉 400）。
    """
    if offset < 0 or limit < 0:
        raise ValueError("offset / limit 不可小於 0（limit 0＝全部）")
    system_prompt, prompt_version = prompt_debug_versions.resolve(system_prompt)

    run_id = f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    run_dir = _run_dir(run_id)
    input_name = Path(input_name or "input.csv").name
    try:
        (run_dir / "input").mkdir(parents=True, exist_ok=False)
        input_path = run_dir / "input" / input_name
        input_path.write_bytes(input_bytes)
        (run_dir / "system_prompt.md").write_text(system_prompt, encoding="utf-8")

        taxonomy = prompt_debug.load_taxonomy()
        manifest = {
            "version": 1,
            "run_id": run_id,
            "created_at": datetime.now(UTC).isoformat(),
            "input_name": input_name,
            "input_sha256": _sha256_file(input_path),
            "sheet": sheet or None,
            "id_column": id_column or "session_oid",
            "text_column": text_column or "conversation_full",
            "offset": offset,
            "limit": limit,
            # 版本名為空＝送出前在頁面上臨時編輯過，此時只有 prompt_sha256 能追出實際用了什麼
            "prompt_version": prompt_version,
            "prompt_sha256": _sha256_text(system_prompt),
            "schema_sha256": _json_hash(prompt_debug.output_schema(taxonomy)),
            "model": (effective.get("model") or "").strip(),
            "overrides": overrides or {},
            "workers": workers,
            "triggered_by": triggered_by,
        }
        _write_json_atomic(run_dir / "manifest.json", manifest)
        plan = _prepare_plan(run_dir, manifest, effective, workers=workers, rerun=False)
    except Exception:
        shutil.rmtree(run_dir, ignore_errors=True)  # 建檔失敗不留半殘目錄
        raise
    return _launch(plan, triggered_by)


def resume_run(
    run_id: str,
    effective: dict,
    *,
    workers: int | None = None,
    rerun: bool = False,
    triggered_by: str = "",
) -> dict:
    """對既有 run 目錄續跑（只補未成功筆）或強制重跑（忽略斷點全部重打）。

    manifest 鎖定輸入/Prompt/schema/model：分類 SSOT 或功能區 model 變了會拒絕，防混用結果。

    Raises:
        ValueError: run 不存在、仍在執行中、或與 manifest 鎖不相容（router 轉 4xx）。
    """
    manifest = read_manifest(run_id)
    live = _store.get(run_id)
    if live and live["status"] in ("running", "cancelling"):
        raise ValueError("本 run 仍在執行中，不可重複啟動")
    model = (effective.get("model") or "").strip()
    if model != manifest["model"]:
        raise ValueError(
            f"model 已由 {manifest['model']} 變為 {model}，與本 run 斷點不相容；請開新跑批"
        )
    plan = _prepare_plan(
        _run_dir(run_id),
        manifest,
        effective,
        workers=workers or manifest.get("workers") or 8,
        rerun=rerun,
    )
    return _launch(plan, triggered_by)


def read_manifest(run_id: str) -> dict:
    """讀 run manifest（router 據此重放 overrides / 校驗存在性）。

    Raises:
        ValueError: run 目錄或 manifest 不存在。
    """
    path = _run_dir(run_id) / "manifest.json"
    if not path.is_file():
        raise ValueError(f"跑批 run 不存在：{run_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def get_run(run_id: str) -> dict | None:
    """單 run 進度：執行中回 in-mem 快照；否則回磁碟 summary（server 重啟後為 interrupted 推導）。"""
    live = _store.get(run_id)
    if live:
        return _public(live)
    try:
        manifest = read_manifest(run_id)
    except ValueError:
        return None
    return _disk_summary(_run_dir(run_id), manifest)


def _disk_summary(run_dir: Path, manifest: dict) -> dict:
    """無 in-mem 快照時由磁碟推導 run 狀態（正常收尾有 summary.json；否則視為 interrupted 可續跑）。"""
    summary_file = run_dir / "summary.json"
    if summary_file.is_file():
        try:
            return json.loads(summary_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    ok = failed = 0
    try:
        with (run_dir / "raw_results.jsonl").open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record.get("parsed"), dict) and not record.get("error"):
                    ok += 1
                else:
                    failed += 1
    except OSError:
        pass
    cost_usd, total_tokens = _jsonl_spend(run_dir / "raw_results.jsonl")
    return {
        "status": "interrupted",
        "run_id": manifest["run_id"],
        "prompt_version": manifest.get("prompt_version", ""),
        "model": manifest["model"],
        "input_name": manifest["input_name"],
        "created_at": manifest["created_at"],
        "total": 0,  # 中斷推導無選中總數（重啟後 in-mem 已失）；續跑會重算
        "resumed": 0,
        "pending": 0,
        "processed": ok + failed,
        "ok": ok,
        "failed": failed,
        "invalid": 0,
        "total_tokens": total_tokens,
        "cost_usd": cost_usd,
        "warnings": [],
        "recent": [],
        "failed_items": [],
        "failed_items_truncated": False,
    }


def list_runs() -> list[dict]:
    """全部 run 摘要（新→舊）：磁碟目錄為準、in-mem 快照 overlay 即時進度。"""
    if not BATCH_DIR.is_dir():
        return []
    rows: list[dict] = []
    for run_dir in sorted(BATCH_DIR.iterdir(), reverse=True):
        manifest_file = run_dir / "manifest.json"
        if not manifest_file.is_file():
            continue
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        snap = _store.get(manifest["run_id"]) or _disk_summary(run_dir, manifest)
        rows.append(
            {
                "run_id": manifest["run_id"],
                "created_at": manifest["created_at"],
                "input_name": manifest["input_name"],
                "prompt_version": manifest.get("prompt_version", ""),
                "model": manifest["model"],
                "offset": manifest["offset"],
                "limit": manifest["limit"],
                "workers": manifest.get("workers"),
                "status": snap["status"],
                "total": snap["total"],
                "resumed": snap.get("resumed", 0),
                "processed": snap["processed"],
                "ok": snap["ok"],
                "failed": snap["failed"],
                "invalid": snap.get("invalid", 0),
                "cost_usd": snap.get("cost_usd", 0.0),
                "has_csv": (run_dir / "results.csv").is_file(),
            }
        )
    return rows


def cancel_run(run_id: str) -> bool:
    """停止執行中 run：已完成筆保留（斷點），未跑筆可事後續跑。回 True＝成功送出停止。"""

    def _apply(snap: dict) -> bool:
        if snap["status"] not in ("running",):
            return False
        snap["status"] = "cancelling"
        return True

    if not _store.mutate(run_id, _apply):
        return False
    with _cancels_lock:
        event = _cancels.get(run_id)
    if event:
        event.set()
    return True


# 下載 kind → (run 目錄內檔名, media type)；input 保留原名故另行處理
_DOWNLOAD_KINDS = {
    "csv": ("results.csv", "text/csv"),
    "jsonl": ("raw_results.jsonl", "application/x-ndjson"),
    "preds": ("preds.json", "application/json"),
}


def download_path(run_id: str, kind: str) -> tuple[Path, str, str]:
    """下載檔定位：回 (實體路徑, 建議檔名, media type)。

    Raises:
        ValueError: run 不存在、kind 不支援或檔案尚未產生。
    """
    manifest = read_manifest(run_id)
    run_dir = _run_dir(run_id)
    if kind == "input":
        path = run_dir / "input" / manifest["input_name"]
        media = "application/octet-stream"
        name = manifest["input_name"]
    elif kind in _DOWNLOAD_KINDS:
        filename, media = _DOWNLOAD_KINDS[kind]
        path = run_dir / filename
        name = f"{run_id}_{filename}"
    else:
        raise ValueError(f"不支援的下載類型：{kind}")
    if not path.is_file():
        raise ValueError(f"檔案尚未產生：{kind}")
    return path, name, media


def mark_running_interrupted() -> list[str]:
    """graceful shutdown 收尾：把仍在跑的 run 標 interrupted（斷點已逐筆落盤，重啟後可續跑）。"""
    return _store.mark_interrupted(
        running_statuses=("running", "cancelling"), new_status="interrupted"
    )
