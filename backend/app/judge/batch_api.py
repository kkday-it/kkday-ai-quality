"""③ OpenAI Batch API 封裝：組 Batch JSONL request 行的純函式。

適用邊界（誠實）：Batch API 是**無狀態單次請求批**，適合「單階段、全量、無分支」的大批量呼叫
（如 polarity 全量預篩、或用固定 prompt 對上萬條歷史資料重判某一步）。**不直接適用**於初判歸因的
多階段管線（polarity→attribute 有「負向才 attribute」的條件分支，無法在一個 batch request 內表達）。

省成本定位：Batch −50% 與 prompt caching −50% 是不同機制、場景互補——caching 適即時（靜態法典前綴
重用），Batch 適離線（可等 24h 的大批量）。本模組 build_* 為純函式，可離線單元測。
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
