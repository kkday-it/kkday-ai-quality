"""初判 Prompt 草稿檔案層（每支 prompt 一份共享草稿，取代 `prompt_drafts` 表）。

草稿＝未入庫的編輯中內容：沙盒可直接送測（雙跑對比），驗證滿意後由呼叫端走
`prompt_versions.save_version` 入庫成新版並刪草稿。

存 `prompts/drafts/<prompt_id>.md`，**該目錄列入 .gitignore**：草稿是個人未定案的編輯狀態，
進 git 只會污染 PR diff。放 `prompts/` 底下而非 `data/`，是為了讓「prompt 相關的一切都在
prompts/ 這棵樹下」這件事成立，人找檔案時不必記兩個地方。

併發策略沿用 DB 版的 **last-write-wins**（`app.core.db.prompt_drafts` 原本就這樣宣告）：
草稿定義上就是尚未定案的個人編輯內容，不是要保護的正式狀態，故不套 `save_version` 那道
`expected_base_version` 樂觀鎖。`base_version` 仍然記著，供前端判斷「我這份草稿分叉自哪一版、
那一版是不是已經被別人推進了」。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.core.paths import PROMPTS_DIR
from app.judge.prompt_versions import (
    atomic_write,
    check_prompt_id,
    render_frontmatter,
    split_frontmatter,
)

DRAFTS_DIRNAME = "drafts"
_DRAFT_TAG = "prompt-draft"


def drafts_dir() -> Path:
    """草稿目錄路徑（不保證存在）。"""
    return PROMPTS_DIR / DRAFTS_DIRNAME


def _draft_path(prompt_id: str) -> Path:
    """某支 prompt 的草稿檔路徑；prompt_id 走版本庫同一套白名單驗證（擋路徑穿越）。"""
    check_prompt_id(prompt_id)
    return drafts_dir() / f"{prompt_id}.md"


def get_draft(prompt_id: str) -> dict | None:
    """取某支 prompt 的草稿；無草稿回 None。

    Returns:
        `{"text", "base_version", "updated_by", "updated_at"}`；`text` 為草稿全文
        （已剝 frontmatter）。呼叫端若需要 `_meta` 包裝請自行組，本層只管純文字。
    """
    path = _draft_path(prompt_id)
    if not path.is_file():
        return None
    meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
    return {
        "text": body,
        "base_version": meta.get("base_version", ""),
        "updated_by": meta.get("updated_by", ""),
        "updated_at": meta.get("updated_at", ""),
    }


def list_drafts() -> list[dict]:
    """列出所有存在草稿的 prompt（不含內文）——供前端 picker 一次拉取草稿狀態，免逐支輪詢。"""
    directory = drafts_dir()
    if not directory.is_dir():
        return []
    rows: list[dict] = []
    for path in sorted(directory.glob("*.md")):
        meta, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        rows.append(
            {
                "prompt_id": path.stem,
                "base_version": meta.get("base_version", ""),
                "updated_by": meta.get("updated_by", ""),
                "updated_at": meta.get("updated_at", ""),
            }
        )
    return rows


def upsert_draft(prompt_id: str, text: str, base_version: str, updated_by: str = "") -> dict:
    """寫入／覆蓋草稿（last-write-wins）。

    Args:
        prompt_id: 目標 prompt。
        text: 草稿全文。
        base_version: 這份草稿分叉自哪個版本（供前端判斷是否已落後於 active）。
        updated_by: 編輯者，供前端顯示編輯線索。

    Returns:
        寫入後的草稿內容（形狀同 `get_draft`）。

    Raises:
        ValueError: prompt_id 非法或 text 為空白。
    """
    body = text.strip()
    if not body:
        raise ValueError("草稿內容不可為空")
    updated_at = datetime.now(UTC).isoformat(timespec="seconds")
    atomic_write(
        _draft_path(prompt_id),
        render_frontmatter(
            _DRAFT_TAG,
            {
                "base_version": base_version,
                "updated_by": updated_by,
                "updated_at": updated_at,
            },
            body,
        ),
    )
    return {
        "text": body,
        "base_version": base_version,
        "updated_by": updated_by,
        "updated_at": updated_at,
    }


def delete_draft(prompt_id: str) -> bool:
    """刪除草稿（入庫採納後清理／手動捨棄）。回是否確實刪到檔案。"""
    path = _draft_path(prompt_id)
    if not path.is_file():
        return False
    path.unlink()
    return True
