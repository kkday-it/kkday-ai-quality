"""依人工評判案例，用旗艦模型產出售後根因 Prompt 的**定點補丁**（不重寫全文）。

為什麼是補丁不是整篇重寫：目標 Prompt 約 2–3 萬字，內含數百筆金標調出來的實測校準層與判例庫。
歷史上有一次讓模型「對齊官方表順手瘦身」的改版砍掉了校準錨點，結構欄準確率當場掉 8.7 分
（見 memory `hw-root-cause-prompt-tuning` 的 v_hw12 回歸教訓）。整篇重寫的 diff 長到沒人審得動，
而補丁清單可以逐條看、逐條勾——沒被指名的段落連碰都不會碰到。

正確性的關鍵在 `anchor` 驗證：模型必須逐字複製它要取代的原文片段，本模組逐條核對該片段在全文中
**恰好出現一次**。出現 0 次＝模型沒逐字複製（常見於它自作主張改了標點），出現多次＝片段太短、
套用會改到不該改的地方——兩者都不給套用，但仍回傳給前端顯示，因為「模型想改哪裡」本身有診斷價值。

改寫用的 system prompt 放 `prompts/conversations/reviser.md`（Prompt-as-Source 慣例，dev 熱掛載存檔即生效）；
刻意不做模組級快取，理由同 `prompt_debug_versions`。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Literal

from app.core import db
from app.core import settings as app_settings
from app.core.judge_config import pricing
from app.core.paths import CONVERSATION_PROMPTS_DIR
from app.judge import prompt_debug
from app.judge.llm import client

_REVISER_PROMPT_FILE = CONVERSATION_PROMPTS_DIR / "reviser.md"

_SCHEMA_NAME = "prompt_patch_set"

# 補丁條數上限：超過通常代表模型在做重構而非修 bug（system prompt 亦有同一條紀律，此處是硬閘）
MAX_PATCHES = 6

# anchor 命中狀態：唯一命中才可套用；另外兩種只顯示不套用（理由見模組 docstring）
MatchStatus = Literal["matched", "not_found", "ambiguous"]

PATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "diagnosis": {
            "type": "string",
            "description": "洞在哪：這批案例暴露出哪一條判準綁錯了軸（不可觀測的心理狀態／純字面觸發／缺前置條件）。",
        },
        "patches": {
            "type": "array",
            "maxItems": MAX_PATCHES,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "anchor": {
                        "type": "string",
                        "minLength": 8,
                        "description": "要被取代的原文片段，逐字複製自現行 Prompt，且須在全文中唯一。",
                    },
                    "replacement": {"type": "string", "description": "換成什麼。"},
                    "reason": {"type": "string", "description": "為什麼這樣改。"},
                    "risk": {
                        "type": "string",
                        "description": "這樣改後哪一類原本判對的案例可能被錯誤吸過來。",
                    },
                },
                "required": ["anchor", "replacement", "reason", "risk"],
            },
        },
        "changelog": {
            "type": "string",
            "description": "CHANGELOG 條目草稿（markdown），比照版本庫既有格式。",
        },
    },
    "required": ["diagnosis", "patches", "changelog"],
}


def reviser_system_prompt() -> str:
    """改寫助手的 system prompt 全文。

    Raises:
        FileNotFoundError: `prompts/conversations/reviser.md` 不存在。
    """
    return _REVISER_PROMPT_FILE.read_text(encoding="utf-8")


def _field_labels() -> dict[str, str]:
    """欄位鍵 → 中文標籤（餵給模型時用人看得懂的名稱，對齊調試台欄位卡）。"""
    return {field["key"]: field["label"] for field in prompt_debug.OUTPUT_FIELDS}


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, list):
        return "、".join(str(v) for v in value) if value else "（空陣列）"
    if value is None or value == "":
        return "（空）"
    return str(value)


def format_cases(cases: list[dict[str, Any]]) -> str:
    """把案例組成模型好讀的段落。

    只列「被標錯的欄」的前後對照——把 14 欄全列出來會讓真正的錯誤淹沒在一堆判對的欄位裡。
    全欄皆對的案例（corrections 為空）仍會列出並標為正例：它們的作用是提醒模型「這些別改壞」。

    Args:
        cases: `db.fetch_prompt_debug_reviews` 的回傳列。
    """
    labels = _field_labels()
    blocks: list[str] = []
    for i, case in enumerate(cases, 1):
        corrections: dict[str, Any] = case.get("corrections") or {}
        ai_output: dict[str, Any] = case.get("ai_output") or {}
        lines = [
            f"### 案例 {i}",
            "",
            "<conversation>",
            str(case.get("conversation", "")).strip(),
            "</conversation>",
            "",
        ]
        if corrections:
            lines.append("人工判定為**錯**的欄位（AI 判的 → 正解）：")
            lines.extend(
                f"- {labels.get(key, key)}（`{key}`）：{_format_value(ai_output.get(key))}"
                f" → **{_format_value(value)}**"
                for key, value in corrections.items()
            )
        else:
            lines.append("人工逐欄檢查後**全部判對**——這是正例，改判準時不要把它吸走。")
            lines.append(
                "AI 當時的判定："
                + "、".join(
                    f"{labels.get(k, k)}={_format_value(v)}"
                    for k, v in ai_output.items()
                    if k in ("L1", "L2", "L3")
                )
            )
        comment = str(case.get("comment") or "").strip()
        if comment:
            lines += ["", f"人工修改建議：{comment}"]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def build_user_prompt(system_prompt: str, cases: list[dict[str, Any]]) -> str:
    """組出送給改寫助手的 user message（現行 Prompt 全文 ＋ 案例）。"""
    return (
        "## 現行 Prompt 全文\n\n"
        "以下是要修改的 Prompt。補丁的 `anchor` 必須逐字複製自這段內容。\n\n"
        f"<current_prompt>\n{system_prompt}\n</current_prompt>\n\n"
        f"## 人工評判案例（共 {len(cases)} 則）\n\n"
        f"{format_cases(cases)}\n\n"
        "## 你的任務\n\n"
        "找出上述誤判的共同成因，產出最小幅度的定點補丁。"
    )


def match_status(system_prompt: str, anchor: str) -> MatchStatus:
    """判定某 anchor 在全文中的命中狀態。

    Args:
        system_prompt: 現行 Prompt 全文。
        anchor: 模型宣稱逐字複製自原文的片段。

    Returns:
        `matched`（恰好一次，可套用）／`not_found`（沒逐字複製）／`ambiguous`（多處命中，套用會誤傷）。
    """
    count = system_prompt.count(anchor)
    if count == 1:
        return "matched"
    return "not_found" if count == 0 else "ambiguous"


def annotate_patches(system_prompt: str, patches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """逐條補上 anchor 命中狀態與出現次數（前端據此決定哪幾條可勾選）。"""
    out: list[dict[str, Any]] = []
    for patch in patches:
        anchor = str(patch.get("anchor", ""))
        occurrences = system_prompt.count(anchor) if anchor else 0
        out.append(
            {
                "anchor": anchor,
                "replacement": str(patch.get("replacement", "")),
                "reason": str(patch.get("reason", "")),
                "risk": str(patch.get("risk", "")),
                "status": match_status(system_prompt, anchor) if anchor else "not_found",
                "occurrences": occurrences,
            }
        )
    return out


def apply_patches(system_prompt: str, patches: list[dict[str, Any]]) -> str:
    """把選定補丁套進全文，回新的全文。

    由後往前替換（依 anchor 在全文中的位置倒序）：先改靠後的段落，前面段落的位置才不會被前一次
    替換造成的長度變化推移。

    Args:
        system_prompt: 現行 Prompt 全文。
        patches: 要套用的補丁（各需 `anchor`/`replacement`）。

    Returns:
        套用後的新全文。

    Raises:
        ValueError: 任一補丁的 anchor 不是恰好命中一次（呼叫端應先過 `annotate_patches` 篩掉）。
    """
    located: list[tuple[int, str, str]] = []
    for patch in patches:
        anchor = str(patch.get("anchor", ""))
        status = match_status(system_prompt, anchor) if anchor else "not_found"
        if status != "matched":
            raise ValueError(
                f"補丁的原文片段{'找不到' if status == 'not_found' else '在全文中出現多次'}，無法套用：{anchor[:60]}…"
            )
        located.append((system_prompt.index(anchor), anchor, str(patch.get("replacement", ""))))

    revised = system_prompt
    for index, anchor, replacement in sorted(located, reverse=True):
        revised = revised[:index] + replacement + revised[index + len(anchor) :]
    return revised


def _usage_payload(model: str, usage: Any, latency_ms: int) -> dict[str, Any]:
    """本次改寫的 token 與費用（形狀對齊調試台 usage 幀，前端沿用同一張卡片）。"""
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
    prompt_details = getattr(usage, "prompt_tokens_details", None) if usage else None
    completion_details = getattr(usage, "completion_tokens_details", None) if usage else None
    cached_tokens = int(getattr(prompt_details, "cached_tokens", 0) or 0) if prompt_details else 0
    reasoning_tokens = (
        int(getattr(completion_details, "reasoning_tokens", 0) or 0) if completion_details else 0
    )
    return {
        "model": model,
        "prompt_tokens": prompt_tokens,
        "cached_tokens": cached_tokens,
        "completion_tokens": completion_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost_usd": pricing.cost_usd(model, prompt_tokens, completion_tokens, cached_tokens),
        "latency_ms": latency_ms,
        "usage_available": usage is not None,
        "estimated": True,
    }


def _record_usage_best_effort(cfg: dict[str, Any], payload: dict[str, Any], job_id: str) -> None:
    """改寫本身也是真金白銀的 LLM 呼叫，照樣落 llm_usage 讓「AI 消耗」看板算得到。"""
    if not payload["usage_available"]:
        return
    try:
        db.insert_llm_usage_row(
            {
                "stage": "prompt_revise",
                "model": cfg["model"],
                "prompt_tokens": payload["prompt_tokens"],
                "completion_tokens": payload["completion_tokens"],
                "reasoning_tokens": payload["reasoning_tokens"],
                "cached_tokens": payload["cached_tokens"],
                "cost_usd": payload["cost_usd"],
                "source": None,  # 非反饋來源驅動的呼叫；歸屬由 stage 表達
                "source_id": None,
                "job_id": job_id,
            }
        )
    except Exception:  # noqa: BLE001 - 計費紀錄不能阻斷改寫結果
        pass


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def stream_frames(
    system_prompt: str,
    cases: list[dict[str, Any]],
    effective: dict[str, Any],
):
    """呼叫旗艦模型產補丁，輸出前端可直接消費的 SSE frame。

    走串流而非一次性回應：輸入約 3 萬 token、高 reasoning 檔次下單次要跑一到數分鐘，
    純 POST 等待期間畫面全無動靜（也容易撞上中介層閒置逾時）。

    Yields:
        SSE 文字幀：`meta`（模型資訊）→ `delta`（逐 token 原始輸出）→ `result`
        （diagnosis／已標註命中狀態的 patches／changelog）→ `usage` → `done`；
        失敗時改吐 `error` ＋ `done`。
    """
    token = app_settings.resolve_provider_token(effective)
    if not token:
        raise ValueError("目前配置沒有可用 API token，請先在「配置 › LLM 模型連線」完成設定")

    cfg = {
        "token": token,
        "base_url": (effective.get("base_url") or "").strip(),
        "model": effective.get("model") or "",
        "temperature": effective.get("temperature"),
        "thinking": effective.get("thinking", "default"),
        "reasoning_effort": effective.get("reasoning_effort", "default"),
        "service_tier": None,
    }
    kwargs: dict[str, Any] = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": reviser_system_prompt()},
            {"role": "user", "content": build_user_prompt(system_prompt, cases)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": _SCHEMA_NAME, "strict": True, "schema": PATCH_SCHEMA},
        },
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if cfg["temperature"] is not None:
        kwargs["temperature"] = float(cfg["temperature"])
    kwargs.update(client._reasoning_kwargs(cfg))

    job_id = f"prompt_revise_{uuid.uuid4().hex}"
    yield _sse(
        "meta",
        {
            "job_id": job_id,
            "model": cfg["model"],
            "provider": app_settings.provider_id_for(cfg["base_url"]),
            "reasoning_effort": cfg["reasoning_effort"],
            "case_count": len(cases),
            "prompt_chars": len(system_prompt),
        },
    )

    started = time.monotonic()
    stream = None
    raw_parts: list[str] = []
    usage = None
    try:
        # 走共用降級階梯而非直呼：相容端點不支援 strict json_schema 時，直呼會直接吐 error frame；
        # 階梯會逐級降級並把每一階明示給使用者（與調試台同一套 warning 文案）。
        stream, warnings = prompt_debug._request_compat(cfg, kwargs, stage="prompt_revise")
        for warning in warnings:
            yield _sse("warning", {"message": warning})
        for chunk in stream:
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                usage = chunk_usage
            for choice in getattr(chunk, "choices", []) or []:
                delta = getattr(getattr(choice, "delta", None), "content", None)
                if delta:
                    raw_parts.append(delta)
                    yield _sse("delta", {"text": delta})

        raw = "".join(raw_parts)
        parsed = client._loads_lenient(raw)
        if parsed is None:
            yield _sse(
                "error", {"message": "改寫模型的輸出不是合法 JSON，請重試或降低 reasoning 檔次"}
            )
            yield _sse("done", {"job_id": job_id, "failed": True})
            return

        patches = annotate_patches(system_prompt, parsed.get("patches") or [])
        yield _sse(
            "result",
            {
                "raw": raw,
                "diagnosis": str(parsed.get("diagnosis", "")),
                "changelog": str(parsed.get("changelog", "")),
                "patches": patches,
                "applicable": sum(1 for p in patches if p["status"] == "matched"),
            },
        )
        usage_payload = _usage_payload(
            cfg["model"], usage, int((time.monotonic() - started) * 1000)
        )
        _record_usage_best_effort(cfg, usage_payload, job_id)
        yield _sse("usage", usage_payload)
        yield _sse("done", {"job_id": job_id})
    except GeneratorExit:
        raise
    except Exception as exc:  # noqa: BLE001 - 轉為串流錯誤事件，避免前端只看到連線中斷
        yield _sse("error", {"message": str(exc).splitlines()[0][:500]})
        yield _sse("done", {"job_id": job_id, "failed": True})
    finally:
        if stream is not None and hasattr(stream, "close"):
            stream.close()
