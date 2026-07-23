"""靜態掃描：tables.py 定義的每張表，migration 歷史中至少有一次可辨識的建表/改名操作。

秒級、不需 DB 連線——目的是在合併前直接抓到下一次「judge_rule_versions 式」事故：
新表加進 tables.py 卻忘了補對應 migration，只能靠 create_all 生出，既有庫走
`alembic upgrade head` 永遠拿不到它（見 e2f4a8c91d37_create_judge_rule_versions_table.py
的背景說明）。

已知侷限：本 repo 部分表歷經動態改名/拆分（如 2c8ed24edb24 用 f-string 組
`ALTER TABLE {old} RENAME TO {new}`、648f09878b62 用 tuple/迴圈驅動的拆表），
表名並非字面出現在原始碼中，靜態 regex 無法可靠辨識。這些表改走人工核實過的
白名單（`_KNOWN_DYNAMIC_LINEAGE`），而非硬做一個 AST/資料流分析器——為了
少數幾張表的動態改名寫一整套解析引擎，不符合簡單優先原則。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.db import tables as T

_VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"

# 人工核實過：這些表的建立/改名軌跡藏在動態組字串（f-string RENAME）或 tuple/迴圈驅動
# 的拆表邏輯中，字面 regex 無法辨識，但確實有 migration 歷史可查——見各自標註的檔案。
_KNOWN_DYNAMIC_LINEAGE: dict[str, str] = {
    "attributions": "2c8ed24edb24_two_stage_rename_attributions_prejudge_.py（f-string RENAME）",
    "prejudge_runs": "2c8ed24edb24_two_stage_rename_attributions_prejudge_.py（f-string RENAME）",
    "attribution_history": "2c8ed24edb24_two_stage_rename_attributions_prejudge_.py（f-string RENAME，序列改名）",
    "conversations": "648f09878b62_split_5_source_tables_judgments_source_.py（tuple/迴圈驅動拆表）",
    "freshdesk_tickets": "648f09878b62_split_5_source_tables_judgments_source_.py（tuple/迴圈驅動拆表）",
    "app_feedback": "648f09878b62_split_5_source_tables_judgments_source_.py（tuple/迴圈驅動拆表）",
    "mixpanel_tracker": "648f09878b62_split_5_source_tables_judgments_source_.py（tuple/迴圈驅動拆表）",
}


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


def test_every_table_has_a_traceable_migration() -> None:
    """tables.py 每張表，migration 歷史中須有字面命中或列名白名單，兩者皆無即視為孤兒表。"""
    combined_source = "".join(f.read_text() for f in _VERSIONS_DIR.glob("*.py"))

    orphans: list[str] = []
    for table_name in T.metadata.tables:
        if table_name in _KNOWN_DYNAMIC_LINEAGE:
            continue
        if not _table_appears_in_migration_history(table_name, combined_source):
            orphans.append(table_name)

    assert not orphans, (
        f"以下表在 alembic/versions/ 找不到任何建表/改名痕跡，只能靠 create_all 生出——"
        f"既有庫走 alembic upgrade head 永遠拿不到它們，這正是 judge_rule_versions 踩過的坑："
        f"{orphans}。請補一支 create migration（參考 e2f4a8c91d37 的寫法：raw SQL + "
        f"CREATE TABLE IF NOT EXISTS 冪等），或若此表確有動態改名歷史，"
        f"人工核實後登記進本檔的 _KNOWN_DYNAMIC_LINEAGE。"
    )
