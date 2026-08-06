"""拿人工評判案例庫回歸驗證候選 Prompt：改完之後，舊案例修好了幾條、又改壞了幾條。

沒有這一步，AI 改寫就是在裸奔——你只知道「被餵進去的那條修好了」，不知道有沒有順手弄壞另外
二十條。CHANGELOG 裡每一版都做了這件事，而且真的攔下過過度矯正（2026-07-27-225310 那版初稿把
「商品規格」寫進判準，把配備詢問案吸走，靠回歸才發現）。

三種逐欄結果，判準來自案例本身而非 AI 舊輸出：

- `fixed` / `still_wrong`：`corrections` 裡的欄（人說「改完要變成這樣」）→ 新輸出對上正解＝修好
- `held` / `broken`：`confirmed` 裡的欄（人說「這欄本來就對」）→ 新輸出與當時一致＝守住，變了＝改壞
- 兩者都沒出現的欄＝人沒看過，**不計分**。拿 AI 舊判當標準答案等於把當時的錯誤當正解，
  分數會憑空虛高。

實作走輕量 in-mem job（小規模調適用途，不需要正式跑批那套斷點/自適應併發），
不用 `prompt_debug_batch`——那條是檔案上傳導向、有 run 目錄與續跑，回歸是 DB 來源、通常 <50 條、
比對邏輯完全不同。行程重啟即清空，回歸本來就是「跑完當場看」的動作。
"""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from typing import Any

from app.core import settings as app_settings
from app.core.job_registry import JobStore
from app.judge import prompt_debug
from app.judge.llm import client

_log = logging.getLogger(__name__)

_store: JobStore = JobStore()

# 案例間併發：回歸通常 10–50 條，固定小併發即可（不做自適應治理）
_MAX_WORKERS = 4

# 單次回歸的案例數上限：超過就該走正式跑批而不是這條「當場看結果」的路徑
MAX_CASES = 100


def _fields_to_check(case: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """回 `(要修好的欄→正解, 要守住的欄)`；兩者皆空＝這則案例沒有可計分的欄。"""
    corrections = dict(case.get("corrections") or {})
    confirmed = [k for k in (case.get("confirmed") or []) if k not in corrections]
    return corrections, confirmed


def compare_case(case: dict[str, Any], new_output: dict[str, Any]) -> dict[str, Any]:
    """把一則案例的新舊輸出逐欄比對成回歸結果。

    Args:
        case: `db.fetch_prompt_debug_reviews` 的一列（需含 ai_output/corrections/confirmed）。
        new_output: 用候選 Prompt 重跑後的判定。

    Returns:
        `{fixed, still_wrong, broken, held}` 四個欄位明細清單，各項為
        `{"field", "expected", "actual"}`（`held` 只有 field）。
    """
    corrections, confirmed = _fields_to_check(case)
    ai_output = case.get("ai_output") or {}

    fixed, still_wrong, broken, held = [], [], [], []
    for field, expected in corrections.items():
        actual = new_output.get(field)
        target = fixed if actual == expected else still_wrong
        target.append({"field": field, "expected": expected, "actual": actual})
    for field in confirmed:
        before = ai_output.get(field)
        actual = new_output.get(field)
        if actual == before:
            held.append({"field": field})
        else:
            broken.append({"field": field, "expected": before, "actual": actual})
    return {"fixed": fixed, "still_wrong": still_wrong, "broken": broken, "held": held}


def _new_snapshot(job_id: str, total: int, model: str, prompt_chars: int) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "status": "running",  # running → done | error
        "total": total,
        "processed": 0,
        "model": model,
        "prompt_chars": prompt_chars,
        # 逐案例結果（含四類欄位明細）；順序照送進來的案例順序
        "cases": [],
        # 欄位級累計，讓人一眼看出這次改寫的淨效果
        "fixed": 0,
        "still_wrong": 0,
        "broken": 0,
        "held": 0,
        "failed": 0,
        "cost_usd": 0.0,
        "total_tokens": 0,
        "error": "",
    }


def _judge_once(conversation: str, system_prompt: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """用候選 Prompt 跑一條對話，回 `{parsed, usage_tokens, cost_usd}`。

    Raises:
        RuntimeError: 模型輸出不是合法 JSON object。
    """
    from app.core.judge_config import pricing

    kwargs: dict[str, Any] = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt_debug.user_prompt_for(conversation)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "after_sales_root_cause",
                "strict": True,
                "schema": prompt_debug.output_schema(),
            },
        },
    }
    if cfg["temperature"] is not None:
        kwargs["temperature"] = float(cfg["temperature"])
    kwargs.update(client._reasoning_kwargs(cfg))

    # 走共用降級階梯而非直呼：相容端點（如 Ark 新模型）不支援 strict json_schema 時，
    # 直呼會讓每個 case 硬 400 全紅；階梯會逐級降級並由下方 _loads_lenient + 校驗兜底。
    response, _warnings = prompt_debug._request_compat(cfg, kwargs, stage="prompt_regression")
    raw = response.choices[0].message.content or ""
    parsed = client._loads_lenient(raw)
    if parsed is None:
        raise RuntimeError("模型輸出不是合法 JSON object")

    usage = getattr(response, "usage", None)
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
    details = getattr(usage, "prompt_tokens_details", None) if usage else None
    cached = int(getattr(details, "cached_tokens", 0) or 0) if details else 0
    return {
        "parsed": parsed,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost_usd": pricing.cost_usd(cfg["model"], prompt_tokens, completion_tokens, cached),
    }


def _run(job_id: str, cases: list[dict[str, Any]], system_prompt: str, cfg: dict[str, Any]) -> None:
    """背景執行：逐案例重跑並比對，過程即時寫回快照供前端輪詢。"""

    def one(case: dict[str, Any]) -> dict[str, Any]:
        row: dict[str, Any] = {
            "review_id": case["id"],
            "preview": str(case.get("conversation", ""))[:80],
            "ok": False,
            "error": "",
            "fixed": [],
            "still_wrong": [],
            "broken": [],
            "held": [],
            "total_tokens": 0,
            "cost_usd": 0.0,
        }
        try:
            result = _judge_once(str(case.get("conversation", "")), system_prompt, cfg)
            row.update(compare_case(case, result["parsed"]))
            row["ok"] = True
            row["total_tokens"] = result["total_tokens"]
            row["cost_usd"] = result["cost_usd"]
        except Exception as exc:  # noqa: BLE001 - 單條失敗不該讓整批回歸死掉
            row["error"] = str(exc).splitlines()[0][:300]
            _log.warning("回歸單條失敗 review_id=%s: %s", case.get("id"), row["error"])
        return row

    def absorb(snap: dict[str, Any], row: dict[str, Any]) -> None:
        snap["cases"].append(row)
        snap["processed"] += 1
        snap["total_tokens"] += row["total_tokens"]
        snap["cost_usd"] = round(snap["cost_usd"] + row["cost_usd"], 6)
        if row["ok"]:
            for key in ("fixed", "still_wrong", "broken", "held"):
                snap[key] += len(row[key])
        else:
            snap["failed"] += 1

    try:
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            # 每筆各自 copy_context()：同一個 Context 物件不能被兩條 thread 同時 enter
            # （會 RuntimeError: cannot enter context ... is already entered）。
            futures = [
                pool.submit(ctx.run, one, case)
                for ctx, case in ((copy_context(), c) for c in cases)
            ]
            for future in as_completed(futures):
                row = future.result()
                _store.mutate(job_id, lambda snap, r=row: absorb(snap, r))
        # 逐案例明細照 review_id 排序，免得每次跑完順序都隨完成時間亂跳
        _store.mutate(
            job_id,
            lambda snap: (
                snap["cases"].sort(key=lambda r: r["review_id"]),
                snap.update({"status": "done"}),
            ),
        )
    except Exception as exc:  # noqa: BLE001 - 整批級失敗轉為 error 狀態，前端輪詢看得到
        # 先綁成一般區域變數：`except ... as exc` 的名稱在區塊結束時會被刪除，直接寫進 lambda
        # 閉包等於埋一顆會在未來重構（例如改成延後執行）時才炸開的雷。
        message = str(exc)[:300]
        _log.exception("回歸整批失敗 job_id=%s", job_id)
        _store.mutate(job_id, lambda snap: snap.update({"status": "error", "error": message}))


def start(
    cases: list[dict[str, Any]], system_prompt: str, effective: dict[str, Any]
) -> dict[str, Any]:
    """啟動回歸重跑（背景執行），立刻回初始快照（含 job_id）。

    Args:
        cases: 要重跑的案例（`db.fetch_prompt_debug_reviews` 的回傳）。
        system_prompt: 候選 Prompt 全文（可以是還沒存版的草稿）。
        effective: 已解析的 LLM 配置（含 token）。

    Returns:
        初始進度快照。

    Raises:
        ValueError: 沒有案例、超過 `MAX_CASES`、或配置解不出 API token。
    """
    if not cases:
        raise ValueError("沒有可回歸的案例")
    if len(cases) > MAX_CASES:
        raise ValueError(f"單次回歸上限 {MAX_CASES} 條，選了 {len(cases)} 條")

    token = app_settings.resolve_provider_token(effective)
    if not token:
        # stub 假結果會讓人以為改寫沒問題，比直接失敗更糟——寧可直接失敗
        raise ValueError("目前配置沒有可用 API token，拒絕以假結果執行回歸")

    cfg = {
        "token": token,
        "base_url": (effective.get("base_url") or "").strip(),
        "model": effective.get("model") or "",
        "temperature": effective.get("temperature"),
        "thinking": effective.get("thinking", "default"),
        "reasoning_effort": effective.get("reasoning_effort", "default"),
        "service_tier": None,
    }

    job_id = f"prompt_regression_{uuid.uuid4().hex}"
    _store.put(job_id, _new_snapshot(job_id, len(cases), cfg["model"], len(system_prompt)))
    threading.Thread(target=_run, args=(job_id, cases, system_prompt, cfg), daemon=True).start()
    return _store.get(job_id) or {}


def get_job(job_id: str) -> dict[str, Any] | None:
    """回進度快照（深拷貝）；job 不存在（或行程重啟後）回 None。"""
    return _store.get(job_id)


def mark_running_interrupted() -> list[str]:
    """graceful shutdown 收尾：把仍在跑的 job 標 interrupted。

    ⚠️ 本模組的 job **沒有斷點**（結果只在記憶體，不像跑批會逐筆落盤），所以 interrupted 是終態、
    不能續跑——但仍必須標記：否則行程重啟後快照永遠停在 `running`，前端輪詢等不到終態。
    """
    return _store.mark_interrupted(running_statuses=("running",), new_status="interrupted")
