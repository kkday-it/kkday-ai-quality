"""③ OpenAI Batch API 封裝：離線大批量判決省成本（−50%，24h 非同步）。

適用邊界（誠實）：Batch API 是**無狀態單次請求批**，適合「單階段、全量、無分支」的大批量呼叫
（如 polarity 全量預篩、或用固定 prompt 對上萬條歷史資料重判某一步）。**不直接適用**於初判歸因的
多階段管線（polarity→attribute 有「負向才 attribute」的條件分支，無法在一個 batch request 內表達）。
故本模組提供 Batch 端點封裝供「單階段大批量」場景用；即時互動判決仍走 prejudge_batch 的 ThreadPool。

省成本定位：Batch −50% 與 prompt caching −50% 是不同機制、場景互補——caching 適即時（靜態法典前綴
重用），Batch 適離線（可等 24h 的大批量）。build_* 為純函式可離線單元測；submit/poll/fetch 需 token。
"""
from __future__ import annotations

import json
from typing import Any


def build_batch_line(
    custom_id: str, model: str, system: str, user: str, *, schema: dict | None = None
) -> dict:
    """組一行 OpenAI Batch JSONL request（POST /v1/chat/completions）。純函式·可離線測。

    Args:
        custom_id: 該筆唯一識別（回下載結果時對回原 item，如 source_id）。
        model/system/user: chat 參數。
        schema: 傳入時走 Structured Outputs（json_schema strict），與即時判決同格式。

    Returns:
        Batch JSONL 單行 dict（{custom_id, method, url, body}）。
    """
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if schema:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "out", "schema": schema, "strict": True},
        }
    return {"custom_id": custom_id, "method": "POST", "url": "/v1/chat/completions", "body": body}


def build_batch_jsonl(lines: list[dict]) -> str:
    """多行 request → JSONL 字串（每行一 JSON·OpenAI Batch 上傳格式）。純函式。"""
    return "\n".join(json.dumps(line, ensure_ascii=False) for line in lines)


def submit_batch(jsonl: str, *, base_url: str, token: str) -> str:
    """上傳 JSONL + 建立 batch job（−50%·completion_window=24h）；回 batch_id。需 token（真呼叫）。

    Raises:
        RuntimeError: openai SDK 未裝 / API 失敗。
    """
    import io

    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - 需真環境
        raise RuntimeError(f"openai SDK 未安裝：{exc}") from exc
    client = OpenAI(base_url=base_url, api_key=token)
    up = client.files.create(file=io.BytesIO(jsonl.encode("utf-8")), purpose="batch")
    batch = client.batches.create(
        input_file_id=up.id, endpoint="/v1/chat/completions", completion_window="24h"
    )
    return batch.id


def poll_batch(batch_id: str, *, base_url: str, token: str) -> dict:
    """查 batch 狀態；回 {status, output_file_id, request_counts}。需 token。"""
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=token)
    b = client.batches.retrieve(batch_id)
    return {
        "status": b.status,  # validating/in_progress/completed/failed/…
        "output_file_id": getattr(b, "output_file_id", None),
        "request_counts": getattr(b, "request_counts", None),
    }


def fetch_results(output_file_id: str, *, base_url: str, token: str) -> list[dict]:
    """下載 batch 結果 JSONL → [{custom_id, body/…}]（呼叫端按 custom_id 對回 item 落庫）。需 token。"""
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=token)
    content = client.files.content(output_file_id).text
    return [json.loads(line) for line in content.splitlines() if line.strip()]
