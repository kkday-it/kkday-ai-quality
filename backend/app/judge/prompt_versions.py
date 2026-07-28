"""初判 Prompt 檔案版本庫：一支 prompt 一個資料夾、一版一檔、`ACTIVE` 檔指定生效版本。

形態（2026-07-28 起，取代 `judge_rule_versions` 表的 `prompt_*` 列）：

    prompts/01_C-1_content/
        ACTIVE              ← 單行純文字＝當前生效的版本名
        v20260724041913.md  ← 一版一檔全文快照，immutable
        v20260728153000.md

**為什麼 active 用獨立指標檔、不用「檔名字典序最大」**：售後調試台
（`prompt_debug_versions`）採字典序，2026-07-28 當天就因此連續三次靜默互蓋——平行編輯
各自以舊版為基線存檔，時間戳大的那支勝出、另一支的改動無聲失效。顯式指標讓「哪一版生效」
與「誰最後存檔」解耦，也讓切回舊版成為一個明確動作而非碰運氣。

**⚠️ 但指標檔本身擋不住互蓋，真正擋住的是 `expected_base_version` 樂觀鎖。**
別誤以為「改用檔案／進 git 就會自然產生衝突」——實測那批調試台版本檔一直都被 git 追蹤，
同分支循序 commit 屬 fast-forward，git 從不抗議，即使語意上蓋掉別人剛存的內容。並發安全
完全來自 `save_version()` 寫入前的基線比對，移掉它防呆就靜默失效了。

寫入一律 temp→`os.replace` 原子落盤，且 `ACTIVE` **最後**才寫：中途失敗頂多留下一個沒被
指向的孤兒版本檔（無害），不會出現「ACTIVE 指向一個還沒寫完的檔」。
"""

from __future__ import annotations

import fcntl
import os
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.paths import PROMPTS_DIR

# 版本檔名：v + 14 位時間戳（定長，故字典序＝時序，僅用於排序展示，不用於決定 active）
_VERSION_RE = re.compile(r"^v\d{14}$")
_STAMP_FORMAT = "v%Y%m%d%H%M%S"
# prompt_id 白名單格式（擋路徑穿越）：兩位序號 + 底線 + 英數/底線/連字號
_PROMPT_ID_RE = re.compile(r"^\d{2}_[A-Za-z0-9_-]+$")

_ACTIVE_FILE = "ACTIVE"
_LOCK_FILE = ".lock"

# 檔名時間戳走 UTC：版本號會直接顯示在 RuleManager 上（前端 versionLabel 取 created_at 的數字
# 前 14 位），而 DB 時代那些號碼就是 UTC——改成台北時間會讓遷移前後同一版的號碼平移 8 小時，
# 使用者記憶中的版本號全部對不上。跨時區也只有一種讀法，不必問「這是誰的下午三點」。
# （售後調試台 prompt_debug_versions 用台北時間，那是獨立的另一條線、無歷史號碼包袱。）
_STAMP_TZ = UTC

# 每支 prompt 一把 in-process 鎖。prod 是單副本單 worker（deploy/base/backend-deployment.yaml
# 的 replicas:1 + Dockerfile 的 --workers 1），故不需要分散式鎖；跨進程（CLI 腳本與 API 同時寫）
# 由下方的 flock 兜底。
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

_FRONTMATTER_OPEN = "<!--"
_FRONTMATTER_CLOSE = "-->"
_VERSION_TAG = "prompt-version"


class ConflictError(Exception):
    """存檔基線與當前 active 版本不符（有人在你編輯期間存過新版）。

    呼叫端應轉 HTTP 409 並要求使用者重新載入——**不可以**自動覆蓋，那正是要防的 lost update。
    """


class VersionNotFoundError(Exception):
    """指定的版本檔不存在。"""


def check_prompt_id(prompt_id: str) -> None:
    """擋掉路徑穿越與非法 prompt_id（`../` 之類進不了目錄名）。

    Raises:
        ValueError: prompt_id 不符合白名單格式。
    """
    if not _PROMPT_ID_RE.match(prompt_id):
        raise ValueError(f"非法 prompt_id：{prompt_id!r}")


def _check_version(version: str) -> None:
    """驗證版本名格式（同時擋路徑穿越）。

    Raises:
        ValueError: 版本名不是 `v` + 14 位數字。
    """
    if not _VERSION_RE.match(version):
        raise ValueError(f"非法版本名：{version!r}")


def prompt_dir(prompt_id: str) -> Path:
    """某支 prompt 的版本資料夾路徑（不保證存在）。"""
    check_prompt_id(prompt_id)
    return PROMPTS_DIR / prompt_id


def _lock_for(prompt_id: str) -> threading.Lock:
    """取得該 prompt 專屬的 in-process 鎖（首次取用時建立）。"""
    with _locks_guard:
        return _locks.setdefault(prompt_id, threading.Lock())


def atomic_write(path: Path, text: str) -> None:
    """temp 檔寫完再 rename 就位——避免讀者看到寫到一半的內容（版本檔與草稿檔共用）。

    temp 檔刻意放同一個目錄：`os.replace` 只在同一檔案系統上保證原子性。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp{os.getpid()}"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def split_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    """把版本檔拆成 metadata 與 prompt 本文。

    metadata 用 HTML 註解夾帶而非 YAML frontmatter：`---` 開頭會被 markdown 編輯器渲染成
    水平線，而這些檔案是人要直接讀的。

    Args:
        raw: 版本檔全文。

    Returns:
        `(metadata, body)`；沒有 frontmatter 時 metadata 為空 dict、body 即原文。
    """
    if not raw.startswith(_FRONTMATTER_OPEN):
        return {}, raw
    end = raw.find(_FRONTMATTER_CLOSE, len(_FRONTMATTER_OPEN))
    if end == -1:
        return {}, raw
    block = raw[len(_FRONTMATTER_OPEN) : end]
    meta: dict[str, str] = {}
    for line in block.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    return meta, raw[end + len(_FRONTMATTER_CLOSE) :].lstrip("\n")


def render_frontmatter(tag: str, meta: dict[str, str], body: str) -> str:
    """把 metadata + 本文組成帶 frontmatter 的檔案全文（版本檔與草稿檔共用）。

    Args:
        tag: 區塊標記（如 `prompt-version` / `prompt-draft`），供人閱讀時辨識檔案性質。
        meta: key-value metadata；值為空的鍵會被略過。
        body: 檔案本文。
    """
    lines = "\n".join(f"{k}: {v}" for k, v in meta.items() if v)
    return f"{_FRONTMATTER_OPEN} {tag}\n{lines}\n{_FRONTMATTER_CLOSE}\n\n{body.rstrip()}\n"


def list_versions(prompt_id: str) -> list[str]:
    """該 prompt 的所有版本名，新→舊；資料夾不存在或無版本檔回空陣列。

    排序用檔名（時間戳定長，字典序即時序）——**僅供展示**，不代表哪一版生效。
    """
    directory = prompt_dir(prompt_id)
    if not directory.is_dir():
        return []
    return sorted(
        (p.stem for p in directory.glob("v*.md") if _VERSION_RE.match(p.stem)), reverse=True
    )


def active_version(prompt_id: str) -> str | None:
    """當前生效的版本名；尚未初始化（無 `ACTIVE` 檔）回 None。

    Raises:
        ValueError: `ACTIVE` 內容不是合法版本名（人手改壞了）。
    """
    path = prompt_dir(prompt_id) / _ACTIVE_FILE
    if not path.is_file():
        return None
    version = path.read_text(encoding="utf-8").strip()
    _check_version(version)
    return version


def read_version(prompt_id: str, version: str) -> str:
    """讀指定版本的 prompt 本文（已剝除 frontmatter）。

    Raises:
        ValueError: 版本名格式非法。
        VersionNotFoundError: 該版本檔不存在。
    """
    _check_version(version)
    path = prompt_dir(prompt_id) / f"{version}.md"
    if not path.is_file():
        raise VersionNotFoundError(f"{prompt_id} 無此版本：{version}")
    return split_frontmatter(path.read_text(encoding="utf-8"))[1]


def version_meta(prompt_id: str, version: str) -> dict[str, str]:
    """讀指定版本的 metadata（author / created_at / note / parent）。

    Raises:
        VersionNotFoundError: 該版本檔不存在。
    """
    _check_version(version)
    path = prompt_dir(prompt_id) / f"{version}.md"
    if not path.is_file():
        raise VersionNotFoundError(f"{prompt_id} 無此版本：{version}")
    return split_frontmatter(path.read_text(encoding="utf-8"))[0]


def active_text(prompt_id: str) -> str:
    """當前生效版本的 prompt 本文。

    Raises:
        VersionNotFoundError: 尚未初始化（無 `ACTIVE`）或 `ACTIVE` 指向不存在的版本。
            **刻意 fail-loud 不做 fallback**：靜默退回別的版本＝線上判準悄悄變成另一套。
    """
    version = active_version(prompt_id)
    if version is None:
        raise VersionNotFoundError(f"{prompt_id} 尚未初始化（缺 {_ACTIVE_FILE}）")
    return read_version(prompt_id, version)


def list_history(prompt_id: str) -> list[dict[str, Any]]:
    """版本歷史（新→舊），每筆帶 metadata 與 `is_active` 旗標，供歷史面板直接消費。"""
    current = active_version(prompt_id)
    history: list[dict[str, Any]] = []
    for version in list_versions(prompt_id):
        meta = version_meta(prompt_id, version)
        history.append(
            {
                "version": version,
                "author": meta.get("author", ""),
                "note": meta.get("note", ""),
                "created_at": meta.get("created_at", ""),
                "is_active": version == current,
            }
        )
    return history


def _next_stamp(directory: Path) -> str:
    """產生不撞檔的版本名（同一秒內連存兩次就往後挪秒，保住檔名唯一）。"""
    stamp = datetime.now(_STAMP_TZ)
    while (directory / f"{stamp.strftime(_STAMP_FORMAT)}.md").exists():
        stamp = stamp.replace(microsecond=0) + timedelta(seconds=1)
    return stamp.strftime(_STAMP_FORMAT)


def save_version(
    prompt_id: str,
    text: str,
    *,
    expected_base_version: str | None,
    author: str = "",
    note: str = "",
) -> dict[str, Any]:
    """寫出新版本並切為 active。

    Args:
        prompt_id: 要存的 prompt。
        text: prompt 全文（四節格式，不含 frontmatter）。
        expected_base_version: 呼叫端編輯時看到的 active 版本名；與當前 active 不符即拒絕。
            傳 None 代表「這支還沒有任何版本」（首次初始化），此時當前 active 必須也是 None。
        author: 存檔者（供歷史顯示）。
        note: 本次改動說明。

    Returns:
        `{"version": 新版本名, "created": 是否真的建了新檔}`；內容與 active 版逐字相同時
        不建檔（避免無意義的版本堆積），回既有版本名並標 `created=False`。

    Raises:
        ValueError: prompt_id 非法或 text 為空白。
        ConflictError: `expected_base_version` 與當前 active 不符——有人在編輯期間存過新版。
    """
    body = text.strip()
    if not body:
        raise ValueError("Prompt 內容不可為空")

    directory = prompt_dir(prompt_id)
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / _LOCK_FILE
    lock_path.touch(exist_ok=True)

    with _lock_for(prompt_id), open(lock_path, "r+", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            current = active_version(prompt_id)
            # 樂觀鎖：這一行是整個模組防 lost update 的全部依據
            if expected_base_version != current:
                raise ConflictError(
                    f"基線已過期：你編輯時的版本是 {expected_base_version or '（無）'}，"
                    f"但目前生效版本已是 {current or '（無）'}，請重新載入後再存。"
                )

            if current is not None and read_version(prompt_id, current).strip() == body:
                return {"version": current, "created": False}

            version = _next_stamp(directory)
            atomic_write(
                directory / f"{version}.md",
                render_frontmatter(
                    _VERSION_TAG,
                    {
                        "version": version,
                        "parent": current or "",
                        "author": author,
                        "created_at": datetime.now(_STAMP_TZ).isoformat(timespec="seconds"),
                        "note": note,
                    },
                    body,
                ),
            )
            # ACTIVE 最後寫：中途失敗只會留下沒被指向的孤兒版本檔，不會指向半成品
            atomic_write(directory / _ACTIVE_FILE, f"{version}\n")
            return {"version": version, "created": True}
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


def set_active(
    prompt_id: str, version: str, *, expected_base_version: str | None
) -> dict[str, Any]:
    """把 active 指標切到既有的某一版（＝「恢復歷史版本」）。

    只動指標、不複製出一份新版本檔——「何時從哪版切到哪版」由 `ACTIVE` 檔的 git 歷史承載，
    比多存一份內容重複的版本檔乾淨。

    Args:
        prompt_id: 目標 prompt。
        version: 要切過去的版本名。
        expected_base_version: 呼叫端看到的當前 active；不符即拒絕（同 `save_version`）。

    Returns:
        `{"version": 切換後的版本名}`。

    Raises:
        VersionNotFoundError: 該版本檔不存在。
        ConflictError: 基線已過期。
    """
    _check_version(version)
    directory = prompt_dir(prompt_id)
    if not (directory / f"{version}.md").is_file():
        raise VersionNotFoundError(f"{prompt_id} 無此版本：{version}")

    lock_path = directory / _LOCK_FILE
    lock_path.touch(exist_ok=True)
    with _lock_for(prompt_id), open(lock_path, "r+", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            current = active_version(prompt_id)
            if expected_base_version != current:
                raise ConflictError(
                    f"基線已過期：你操作時的版本是 {expected_base_version or '（無）'}，"
                    f"但目前生效版本已是 {current or '（無）'}，請重新載入後再試。"
                )
            atomic_write(directory / _ACTIVE_FILE, f"{version}\n")
            return {"version": version}
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
