"""一次性遷移：judge_rule_versions 的 7 支 prompt_* → prompts/<id>/v<時間戳>.md 檔案版本庫。

用法（容器內跑，需 DB 連線）：
    python scripts/tools/migrate_prompt_db_to_files.py            # dry-run，寫到暫存目錄後驗證
    python scripts/tools/migrate_prompt_db_to_files.py --apply    # 正式寫入 prompts/

⚠️ 一次性腳本：遷移完成並確認後即應刪除（專案核心原則 4「退役即徹底」/ 原則 5「臨時內容不入
版本庫」）。留著只會讓後人以為還需要跑。

版本檔名取自 DB 的 `created_at`（UTC，與前端 versionLabel 現行顯示一致）而非版本序號——檔案版本庫的識別就是時間戳，
硬塞回 v1/v2 只會多一層對照表。原本的整數版本號記進 frontmatter 的 `db_version` 供追溯。
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, timedelta
from pathlib import Path

# 從 repo 內執行時補上 backend 到 import path；容器內 cwd 已是 /app/backend 故容錯即可
_here = Path(__file__).resolve()
for _candidate in (*(p / "backend" for p in _here.parents), Path("/app/backend")):
    if (_candidate / "app" / "__init__.py").is_file():
        sys.path.insert(0, str(_candidate))
        break

from sqlalchemy import select  # noqa: E402

from app.core.db import tables as T  # noqa: E402
from app.judge import prompt_source, prompt_versions  # noqa: E402

STAMP = "v%Y%m%d%H%M%S"


def _rows(rule_code: str) -> list[dict]:
    """該 rule_code 的全部版本（舊→新）；直接查表因為 list_rule_history 不回 content。"""
    j = T.judge_rule_versions
    stmt = (
        select(j.c.version, j.c.content, j.c.note, j.c.author, j.c.created_at, j.c.is_active)
        .where(j.c.rule_code == rule_code)
        .order_by(j.c.version)
    )
    with T.get_engine().connect() as c:
        return [dict(r) for r in c.execute(stmt).mappings()]


def _stamp_for(created_at, taken: set[str]) -> str:
    """由 created_at 產生不撞名的版本名（同秒多版就往後挪秒）。"""
    moment = created_at.astimezone(UTC)
    name = moment.strftime(STAMP)
    while name in taken:
        moment += timedelta(seconds=1)
        name = moment.strftime(STAMP)
    taken.add(name)
    return name


def migrate_one(rule_code: str, target_root: Path, *, verbose: bool) -> dict:
    """把一支 prompt 的全部 DB 版本落成檔案；回統計。"""
    prompt_id = prompt_source.prompt_id_for_rule(rule_code)
    assert prompt_id, rule_code
    directory = target_root / prompt_id
    directory.mkdir(parents=True, exist_ok=True)

    rows = _rows(rule_code)
    taken: set[str] = set()
    active_name: str | None = None
    invalid: list[str] = []

    for row in rows:
        text = (row["content"] or {}).get("text") or ""
        if not text.strip():
            invalid.append(f"v{row['version']}（content.text 為空，略過）")
            continue
        name = _stamp_for(row["created_at"], taken)
        # 舊版本可能因當年格式不同而驗不過（如早期無 ## Taxonomy）——記錄但不中止，
        # 歷史瀏覽不要求每版都能重跑，只要求內容不失真
        try:
            prompt_source.validate(text, prompt_id)
        except Exception as exc:  # noqa: BLE001
            invalid.append(f"{name}（原 v{row['version']}）：{str(exc)[:60]}")
        prompt_versions.atomic_write(
            directory / f"{name}.md",
            prompt_versions.render_frontmatter(
                "prompt-version",
                {
                    "version": name,
                    "db_version": str(row["version"]),
                    "author": row["author"] or "",
                    "created_at": row["created_at"].astimezone(UTC).isoformat(timespec="seconds"),
                    "note": (row["note"] or "").replace("\n", " ")[:200],
                },
                text,
            ),
        )
        if row["is_active"]:
            active_name = name

    if active_name is None:
        raise RuntimeError(f"{rule_code} 沒有 is_active 版本，無法決定 ACTIVE")
    prompt_versions.atomic_write(directory / "ACTIVE", f"{active_name}\n")

    if verbose and invalid:
        for line in invalid:
            print(f"    ⚠️  {line}")
    return {"rule_code": rule_code, "versions": len(taken), "active": active_name,
            "invalid": len(invalid)}


def migrate_drafts(target_root: Path) -> int:
    """把 prompt_drafts 表的殘留草稿落成檔案；回搬移筆數。"""
    from app.core import db

    moved = 0
    for row in db.list_prompt_drafts():
        draft = db.get_prompt_draft(row["rule_code"])
        prompt_id = prompt_source.prompt_id_for_rule(row["rule_code"])
        if not draft or not prompt_id:
            continue
        text = (draft.get("content") or {}).get("text") or ""
        if not text.strip():
            continue
        path = target_root / "drafts" / f"{prompt_id}.md"
        prompt_versions.atomic_write(
            path,
            prompt_versions.render_frontmatter(
                "prompt-draft",
                {
                    # DB 的 base_version 是整數；檔案版本庫用版本名，這裡留空讓前端把它當
                    # 「基線不明」處理（一份草稿而已，重存一次即可對齊）
                    "base_version": "",
                    "updated_by": draft.get("updated_by") or "",
                    "updated_at": str(draft.get("updated_at") or ""),
                },
                text,
            ),
        )
        moved += 1
    return moved


def verify(target_root: Path) -> list[str]:
    """核對每支 prompt 的 ACTIVE 版內容逐字等於 DB 當前 active——防遷移腳本悄悄改變判準。"""
    from app.core import db

    problems: list[str] = []
    for rule_code in prompt_source.PROMPT_RULE_CODES:
        prompt_id = prompt_source.prompt_id_for_rule(rule_code)
        expected = ((db.get_rule_active(rule_code) or {}).get("text") or "").strip()
        active = (target_root / prompt_id / "ACTIVE").read_text(encoding="utf-8").strip()
        got = prompt_versions.split_frontmatter(
            (target_root / prompt_id / f"{active}.md").read_text(encoding="utf-8")
        )[1].strip()
        if got != expected:
            problems.append(f"{rule_code}：ACTIVE 版內容與 DB active 不一致")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="正式寫入 prompts/（預設 dry-run）")
    parser.add_argument("--scratch", default="/tmp/prompt_migration", help="dry-run 輸出目錄")
    args = parser.parse_args()

    from app.core.paths import PROMPTS_DIR

    target = PROMPTS_DIR if args.apply else Path(args.scratch)
    print(f"{'正式寫入' if args.apply else 'DRY-RUN'} → {target}\n")

    total = 0
    for rule_code in prompt_source.PROMPT_RULE_CODES:
        stat = migrate_one(rule_code, target, verbose=True)
        total += stat["versions"]
        flag = f"，{stat['invalid']} 版驗證未過" if stat["invalid"] else ""
        print(f"  {stat['rule_code']:<16} {stat['versions']:>3} 版  ACTIVE={stat['active']}{flag}")

    drafts = migrate_drafts(target)
    print(f"\n  草稿 {drafts} 份")
    print(f"  合計 {total} 個版本檔")

    problems = verify(target)
    if problems:
        print("\n❌ 內容核對未過：")
        for line in problems:
            print(f"  {line}")
        return 1
    print("\n✅ 內容核對通過：7 支 ACTIVE 版逐字等於 DB active")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
