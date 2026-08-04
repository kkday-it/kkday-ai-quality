"""靜態掃描：tables.py 定義的每張表，migration 歷史中至少有一次可辨識的建表/改名操作。

秒級、不需 DB 連線——目的是在合併前直接抓到下一次「judge_rule_versions 式」事故：
新表加進 tables.py 卻忘了補對應 migration，只能靠 create_all 生出，既有庫走
`alembic upgrade head` 永遠拿不到它（見 e2f4a8c91d37_create_judge_rule_versions_table.py
的背景說明）。

已知侷限：這是**字面掃描**，只抓得到「完全沒寫 migration」，抓不到「寫了但與 tables.py
不一致」；動態組字串（f-string RENAME）或迴圈驅動的建表也無法可靠辨識，只能靠
squash baseline 的共現佐證兜底。真正的結構比對在 `test_schema_parity.py`——實際建兩個空庫、
分別跑 alembic 鏈與 `metadata.create_all`，再逐欄逐索引比對。那支是本檔的嚴格超集，
待其脫離 xfail（重構計畫 Phase 1 重建 baseline）後，本檔即可整支退役。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.db import tables as T

_VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _table_appears_in_migration_history(table_name: str, combined_source: str) -> bool:
    """字面比對：op.create_table("x") / T.x.create( / rename_table(..., "x") / RENAME TO x / CREATE TABLE x。"""
    t = re.escape(table_name)
    pattern = re.compile(
        r'op\.create_table\(\s*["\']' + t + r'["\']'
        r"|T\." + t + r"\.create\("
        r'|rename_table\([^)]*["\']' + t + r'["\']'
        r'|RENAME TO\s+"?' + t + r'"?\b'
        r'|CREATE TABLE(?:\s+IF NOT EXISTS)?\s+"?' + t + r'"?\b',
        re.IGNORECASE,
    )
    return bool(pattern.search(combined_source))


_CREATE_ALL_PATTERN = re.compile(r"metadata\.create_all\(")


def _table_covered_by_baseline(table_name: str, file_texts: list[str]) -> bool:
    """squash baseline（如 4ac23d6d20b4）以 `T.metadata.create_all()` 一次建齊多表，個別表名
    不會以 op.create_table 字面出現。改以「同檔含 create_all 呼叫 + 表名確實出現在檔案內容
    （baseline docstring 逐一列出建立哪些表）」佐證涵蓋——只認同一檔案內的共現，不放寬到
    「combined_source 隨便哪裡出現過表名」，避免未來新表只改 tables.py 忘補 migration 時
    被誤判為已涵蓋（新表名不會出現在舊 baseline 檔案裡）。"""
    t = re.escape(table_name)
    name_pattern = re.compile(r"\b" + t + r"\b")
    return any(
        _CREATE_ALL_PATTERN.search(text) and name_pattern.search(text) for text in file_texts
    )


def test_every_table_has_a_traceable_migration() -> None:
    """tables.py 每張表，migration 歷史中須有字面命中或 squash baseline 佐證，兩者皆無即視為孤兒表。

    ⚠️ 本測試是**字面掃描**，只抓得到「完全沒寫 migration」，抓不到「寫了但與 tables.py 不一致」。
    真正的結構比對在 `test_schema_parity.py`（實際建庫跑完兩條路徑再逐欄比對）——那支目前因
    baseline 的既有缺陷標為 xfail，待重構計畫 Phase 1 轉綠後，本檔即可整支退役。
    """
    files = list(_VERSIONS_DIR.glob("*.py"))
    file_texts = [f.read_text() for f in files]
    combined_source = "".join(file_texts)

    orphans: list[str] = []
    for table_name in T.metadata.tables:
        if _table_appears_in_migration_history(table_name, combined_source):
            continue
        if _table_covered_by_baseline(table_name, file_texts):
            continue
        orphans.append(table_name)

    assert not orphans, (
        f"以下表在 alembic/versions/ 找不到任何建表/改名痕跡，只能靠 create_all 生出——"
        f"既有庫走 alembic upgrade head 永遠拿不到它們，這正是 judge_rule_versions 踩過的坑："
        f"{orphans}。請補一支 create migration（參考 e2f4a8c91d37 的寫法：raw SQL + "
        f"CREATE TABLE IF NOT EXISTS 冪等）。"
    )
