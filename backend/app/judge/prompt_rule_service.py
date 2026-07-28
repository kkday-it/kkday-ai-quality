"""初判 Prompt 的「rule 端點」服務層：把 `rule_code` 世界接到檔案版本庫。

存在理由：`/api/judge-rules` 這組端點同時服務兩類 rule——7 支初判 prompt（2026-07-28 起改存
檔案版本庫，見 `prompt_versions`）與 `bd_tag_vertical` / `source_mapping`（仍在
`judge_rule_versions` 表）。若讓 router 逐一端點自己做「prompt_id 轉換 + 檔案層呼叫 + content
包裝」，12 個端點會各寫一份，遲早漂移。本模組把 prompt 這一側收成與 `app.core.db` 同形狀的
介面，router 只需判斷「是不是 prompt code」然後二選一。

回傳形狀刻意對齊既有 DB 層（`content` 為 `{"_meta": {...}, "text": md 全文}`），前端契約不變。
差別只有一處：**版本識別由整數改成版本名字串**（`v20260724041913`）——這是檔案版本庫的天然
識別，硬塞回整數只會多一層對照表。
"""

from __future__ import annotations

from typing import Any

from app.judge import prompt_drafts_file, prompt_source, prompt_versions
from app.judge.prompt_versions import ConflictError, VersionNotFoundError

__all__ = [
    "ConflictError",
    "VersionNotFoundError",
    "delete_draft",
    "get_active_content",
    "get_draft",
    "get_version_content",
    "is_prompt_code",
    "list_drafts",
    "list_meta",
    "list_history",
    "restore",
    "save",
    "upsert_draft",
]


def is_prompt_code(rule_code: str) -> bool:
    """該 rule_code 是否為 7 支初判 prompt 之一（決定 router 走檔案層還是 DB 層）。"""
    return rule_code in prompt_source.PROMPT_RULE_CODES


def _pid(rule_code: str) -> str:
    """rule_code → prompt_id。

    Raises:
        ValueError: 非 prompt rule_code。
    """
    prompt_id = prompt_source.prompt_id_for_rule(rule_code)
    if prompt_id is None:
        raise ValueError(f"非 prompt rule_code：{rule_code}")
    return prompt_id


def _wrap(prompt_id: str, text: str) -> dict[str, Any]:
    """把 md 全文包成前端既有的 content 形狀（`_meta.label` 供左選單顯示）。"""
    return {
        "_meta": {"label": prompt_source.prompt_label(prompt_id), "kind": "prompt"},
        "text": text,
    }


def get_active_content(rule_code: str) -> dict[str, Any] | None:
    """當前生效版本的 content；版本庫尚未初始化回 None（比照 `db.get_rule_active`）。"""
    prompt_id = _pid(rule_code)
    try:
        return _wrap(prompt_id, prompt_versions.active_text(prompt_id))
    except VersionNotFoundError:
        return None


def get_version_content(rule_code: str, version: str) -> dict[str, Any] | None:
    """指定版本的 content；無此版本回 None（比照 `db.get_rule_version`）。"""
    prompt_id = _pid(rule_code)
    try:
        return _wrap(prompt_id, prompt_versions.read_version(prompt_id, version))
    except (VersionNotFoundError, ValueError):
        return None


def list_meta() -> list[dict[str, Any]]:
    """7 支 prompt 的當前版本 meta，形狀對齊 `db.list_rule_meta`（供左選單）。

    尚未初始化的 prompt 一律略過（與 DB 層「無 active 者略」同語意）。
    """
    rows: list[dict[str, Any]] = []
    for rule_code in prompt_source.PROMPT_RULE_CODES:
        prompt_id = _pid(rule_code)
        version = prompt_versions.active_version(prompt_id)
        if version is None:
            continue
        meta = prompt_versions.version_meta(prompt_id, version)
        rows.append(
            {
                "rule_code": rule_code,
                "version": version,
                "author": meta.get("author", ""),
                "note": meta.get("note", ""),
                "created_at": meta.get("created_at", ""),
                "label": prompt_source.prompt_label(prompt_id),
            }
        )
    return rows


def list_history(rule_code: str) -> list[dict[str, Any]]:
    """版本歷史（新→舊），形狀對齊 `db.list_rule_history`。"""
    return prompt_versions.list_history(_pid(rule_code))


def save(
    rule_code: str,
    text: str,
    *,
    expected_base_version: str | None,
    note: str = "",
    author: str = "",
) -> dict[str, Any]:
    """存為新版並切為生效。

    Raises:
        ConflictError: 基線已過期（有人在編輯期間存過新版）——router 應轉 409。
        ValueError: 內容為空或 rule_code 非 prompt。
    """
    return prompt_versions.save_version(
        _pid(rule_code),
        text,
        expected_base_version=expected_base_version,
        author=author,
        note=note,
    )


def restore(rule_code: str, version: str, *, expected_base_version: str | None) -> dict[str, Any]:
    """把生效指標切回既有的某一版（＝「恢復歷史版本」）。

    Raises:
        ConflictError: 基線已過期。
        VersionNotFoundError: 該版本不存在。
    """
    return prompt_versions.set_active(
        _pid(rule_code), version, expected_base_version=expected_base_version
    )


# ── 草稿（形狀對齊 db.prompt_drafts，rule_code ↔ prompt_id 由本層轉換）──


def get_draft(rule_code: str) -> dict[str, Any] | None:
    """取草稿；無草稿回 None。content 包成前端既有形狀。"""
    prompt_id = _pid(rule_code)
    draft = prompt_drafts_file.get_draft(prompt_id)
    if draft is None:
        return None
    return {
        "content": _wrap(prompt_id, draft["text"]),
        "base_version": draft["base_version"],
        "updated_by": draft["updated_by"],
        "updated_at": draft["updated_at"],
    }


def list_drafts() -> list[dict[str, Any]]:
    """列所有存在草稿的 prompt（不含內文），形狀對齊 `db.list_prompt_drafts`。"""
    rows: list[dict[str, Any]] = []
    for row in prompt_drafts_file.list_drafts():
        rule_code = prompt_source.rule_code_for_prompt(row["prompt_id"])
        if rule_code is None:  # 目錄裡混進非 prompt 檔名，略過而非炸掉整個清單
            continue
        rows.append(
            {
                "rule_code": rule_code,
                "base_version": row["base_version"],
                "updated_by": row["updated_by"],
                "updated_at": row["updated_at"],
            }
        )
    return rows


def upsert_draft(
    rule_code: str, text: str, base_version: str, updated_by: str = ""
) -> dict[str, Any]:
    """寫入／覆蓋草稿（last-write-wins，不套樂觀鎖——草稿是未定案的個人狀態）。"""
    return prompt_drafts_file.upsert_draft(_pid(rule_code), text, base_version, updated_by)


def delete_draft(rule_code: str) -> bool:
    """刪除草稿；回是否確實刪到。"""
    return prompt_drafts_file.delete_draft(_pid(rule_code))
