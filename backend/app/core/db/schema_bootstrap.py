"""容器啟動時的 DB schema 對齊：判斷當前庫的狀態，決定 stamp／upgrade 策略。

**為何獨立成模組而非寫在 `docker-entrypoint.sh` 裡**：這段判斷是有分支、有失敗模式的邏輯
（squash 相容、未蓋章的既有庫、認不得的版本），寫成 bash heredoc 既無法單元測試，也讓
entrypoint 膨脹。抽出後 `resolve_startup_mode()` 可直接被 `tests/test_schema_bootstrap.py` 覆蓋。

**單一路徑原則**：一律 `alembic upgrade head`，不再有「空庫 create_all+stamp／既有庫 upgrade」
的雙軌。舊雙軌的理由是當時 migration 鏈含「假設 create_all 既有表」的破壞性 DDL、空庫跑鏈會
crash；baseline v2（94e60400715b）改為凍結式顯式 DDL 後此前提消失。雙軌本身正是 schema 漂移的
溫床——兩條路徑各自造 schema，而空庫從不跑鏈，導致鏈斷很久都沒人發現（見
`tests/test_schema_parity.py`）。
"""

from __future__ import annotations

import logging
import sys

_log = logging.getLogger(__name__)

# squash 造成的「舊 revision → 新 baseline」對照。
#
# ⚠️ **刻意不與 `datapack.LEGACY_COMPATIBLE_HEADS` 共用**：兩者語義不同，共用會出錯（實際踩過）。
#   - datapack 的表回答「當年匯出的資料包，欄位形狀還相容嗎」——即使目標 revision 後來又被
#     squash 掉，那筆對照對資料相容性判斷仍然有意義。
#   - 本表回答「DB 蓋章在 X，該重新蓋成哪一支」——目標**必須是 script 目錄裡現存的 revision**，
#     否則 stamp 會直接拋 Can't locate revision（把環境從 crash-loop 換成另一種 crash-loop）。
#   由 `tests/test_schema_bootstrap.py` 的 targets-exist 測試守住這條不變式。
#
# 只登記 squash 前的**最終 head**，不登記中間版本：停在中間 revision 的環境 schema 其實落後，
# 對應到新 baseline 會被謊稱為最新、永久漏掉後續 DDL，那種狀態應由人介入。
SQUASHED_REVISIONS: dict[str, str] = {
    # 2026-08-04 squash：4ac23d6d20b4 起 15 支併為 baseline v2
    "a1d7e3f92b64": "94e60400715b",
}

# 判定結果（供 entrypoint 與測試共用的字面值）
MODE_FRESH = "fresh"  # 全新空庫 → 直接 upgrade
MODE_OK = "ok"  # 版本認得 → 直接 upgrade
MODE_ADOPT = "adopt"  # 有業務表卻無版本紀錄 → stamp head，不重跑建表 DDL
MODE_STAMP_PREFIX = "stamp:"  # 版本已不在 script 目錄（squash）→ 先 stamp 到對照版本
MODE_ABORT_PREFIX = "abort:"  # 認不得且未登記 → 拒絕自動處理


def _alembic_config():
    """alembic.ini 的 Config（script_location 由 ini 提供；URL 走 env.py 的 tables.resolve_url）。"""
    from pathlib import Path

    from alembic.config import Config

    backend_root = Path(__file__).resolve().parents[3]  # app/core/db/x.py → backend/
    return Config(str(backend_root / "alembic.ini"))


def resolve_startup_mode() -> str:
    """判斷當前 DB 的 schema 狀態，回傳啟動策略字串。

    Returns:
        `fresh` / `ok` / `adopt` / `stamp:<revision>` / `abort:<revision>` 其中之一。
    """
    from alembic.script import ScriptDirectory
    from sqlalchemy import inspect, text

    from app.core.db import tables as T

    engine = T.get_engine()
    insp = inspect(engine)

    current: str | None = None
    if insp.has_table("alembic_version"):
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).first()
            current = row[0] if row else None

    if current is None:
        # 無版本紀錄：再看業務表在不在，區分「全新空庫」與「建過但沒蓋章」。
        # 後者若直接 upgrade 會對既存表下 CREATE TABLE 撞 DuplicateTable。
        has_business_table = any(insp.has_table(name) for name in T.metadata.tables)
        return MODE_ADOPT if has_business_table else MODE_FRESH

    script = ScriptDirectory.from_config(_alembic_config())
    try:
        script.get_revision(current)
    except Exception:  # noqa: BLE001  revision 不在 script 目錄（squash 後的既有環境）
        target = SQUASHED_REVISIONS.get(current)
        return f"{MODE_STAMP_PREFIX}{target}" if target else f"{MODE_ABORT_PREFIX}{current}"
    return MODE_OK


def align_schema() -> None:
    """依判定結果把 DB schema 對齊到 head；無法安全處理時拋錯（entrypoint 據此中止啟動）。

    Raises:
        RuntimeError: DB 版本認不得且未登記於 `LEGACY_COMPATIBLE_HEADS`（可能是降級殘留、
            人為改動，或停在 squash 前的中間版本）。自動 stamp 會謊稱已是最新並永久漏掉 DDL。
    """
    from alembic import command

    cfg = _alembic_config()
    mode = resolve_startup_mode()

    if mode == MODE_FRESH:
        print("▶ 全新空庫：alembic upgrade head…")
    elif mode == MODE_OK:
        print("▶ 既有庫：alembic upgrade head（增量套用 migration）…")
    elif mode == MODE_ADOPT:
        print("▶ 既有表但無版本紀錄（歷史 create_all 遺留）：stamp head，不重跑建表 DDL")
        command.stamp(cfg, "head")
    elif mode.startswith(MODE_STAMP_PREFIX):
        target = mode[len(MODE_STAMP_PREFIX) :]
        print(f"▶ squash 相容：DB 版本已不在 script 目錄 → 先 stamp 至 {target} 再增量升級")
        # purge=True 必要：alembic 的 stamp 預設會去解析「當前版本」以計算路徑，而這裡的
        # 當前版本正是那個已被 squash 刪除的 revision → 直接拋 Can't locate revision。
        # purge 先清空 alembic_version 再蓋章，等同「宣告這個庫就是 target」，不做路徑推導。
        command.stamp(cfg, target, purge=True)
    elif mode.startswith(MODE_ABORT_PREFIX):
        current = mode[len(MODE_ABORT_PREFIX) :]
        raise RuntimeError(
            f"alembic_version={current} 既不在 script 目錄、也未登記於 LEGACY_COMPATIBLE_HEADS。"
            "可能是降級殘留、人為改動，或停在 squash 前的中間版本（schema 實際落後）。"
            "自動 stamp 會謊稱已是最新並永久漏掉 DDL，故拒絕處理——請人工確認後手動 stamp。"
        )
    else:  # pragma: no cover  防禦：resolve_startup_mode 只會回上述五種
        raise RuntimeError(f"無法判斷 DB schema 狀態（策略判定輸出：{mode!r}）")

    command.upgrade(cfg, "head")


def main() -> int:
    """CLI 入口（`python -m app.core.db.schema_bootstrap`）；失敗回非零供 entrypoint 中止。"""
    try:
        align_schema()
    except Exception as exc:  # noqa: BLE001  啟動閘門：任何失敗都要擋下服務、印清楚原因
        print(f"✗ DB schema 對齊失敗：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
