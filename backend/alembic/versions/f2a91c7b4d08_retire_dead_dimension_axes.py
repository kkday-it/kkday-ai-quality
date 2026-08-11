"""清退三個零消費者的值域軸（責任方／嚴重度／建議行動）

`attribution_dimension_master` 原本承載四軸，但實際只有 `note_type` 有消費者
（備註時間線：db/notes.py、api/routers/findings.py、db/attribution_history.py）。

另外三軸是為「判決」功能預備的值域，而判決至今仍是佔位、沒有任何讀寫路徑：
    responsible_party — schema.py 同名欄位是**初判 vector** 的欄，由 root_cause_domain 推導，
                        與本表無關（見 db/_shared.py 的「語義重疊、來源不同」註解）
    severity          — schema.py 的 Severity 恆為預設 "P3"，註解明載「本期不判斷」
    verdict_action    — 全 codebase 僅出現於本表註解與前端 tab 定義
依專案原則「退役即徹底、要用再補到位」，不留半殘骨架。

只刪列、**不動表結構**——note_type 仍在用，且表以 dimension_code 判別、保留多軸能力。

⚠️ downgrade 不還原資料：這三軸是未使用的提案值，種子檔已一併移除，
還原也只是把死資料塞回去；日後真要做判決功能時，值域應重新依業務定案填。

Revision ID: f2a91c7b4d08
Revises: c4b81f0d3e57
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "f2a91c7b4d08"
down_revision = "c4b81f0d3e57"
branch_labels = None
depends_on = None

_DEAD_AXES = ("responsible_party", "severity", "verdict_action")


def upgrade() -> None:
    """刪除三軸的所有值域列（冪等：已刪過再跑影響 0 列），並同步欄註解。"""
    op.execute(
        "DELETE FROM attribution_dimension_master WHERE dimension_code IN "
        f"({', '.join(repr(a) for a in _DEAD_AXES)})"
    )
    # 註解也是 schema 的一部分——test_schema_parity 會比對 alembic 鏈與 tables.py，
    # 只改 tables.py 不改這裡會紅（該護欄由 1c463dd 補上，正是為了防這種漂移）。
    op.alter_column(
        "attribution_dimension_master",
        "dimension_code",
        existing_type=sa.Text(),
        existing_nullable=False,
        comment="值域維度：note_type（備註的互動類型）",
        existing_comment="值域維度：responsible_party（責任方）/ severity（嚴重度）/ verdict_action（建議行動）",
    )
    op.create_table_comment(
        "attribution_dimension_master",
        "值域主檔（以 dimension_code 判別的判別式單表，保留多軸能力；目前僅 note_type 備註互動類型一軸）。判別式單表是既有慣例（judge_rule_version_lst 用 rule_code 判別），欄形相同的值域共用一表，避免每加一軸就多一套 migration／API／畫面。檔案 config/ai_judge/attribution_dimension.json 為默認 seed，本表存 live",
        existing_comment="判決歸因值域主檔（責任方／嚴重度／建議行動三軸共用一表，以 dimension_code 判別）。三者欄形完全相同，拆三張表＝三套 migration／API／畫面；判別式單表是既有慣例（judge_rule_version_lst 用 rule_code 判別）。檔案 config/ai_judge/attribution_dimension.json 為默認 seed，本表存 live",
    )


def downgrade() -> None:
    """只還原欄註解；資料不還原——刪除的是未被任何程式讀取的提案值。"""
    op.alter_column(
        "attribution_dimension_master",
        "dimension_code",
        existing_type=sa.Text(),
        existing_nullable=False,
        comment="值域維度：responsible_party（責任方）/ severity（嚴重度）/ verdict_action（建議行動）",
        existing_comment="值域維度：note_type（備註的互動類型）",
    )
    op.create_table_comment(
        "attribution_dimension_master",
        "判決歸因值域主檔（責任方／嚴重度／建議行動三軸共用一表，以 dimension_code 判別）。三者欄形完全相同，拆三張表＝三套 migration／API／畫面；判別式單表是既有慣例（judge_rule_version_lst 用 rule_code 判別）。檔案 config/ai_judge/attribution_dimension.json 為默認 seed，本表存 live",
        existing_comment="值域主檔（以 dimension_code 判別的判別式單表，保留多軸能力；目前僅 note_type 備註互動類型一軸）。判別式單表是既有慣例（judge_rule_version_lst 用 rule_code 判別），欄形相同的值域共用一表，避免每加一軸就多一套 migration／API／畫面。檔案 config/ai_judge/attribution_dimension.json 為默認 seed，本表存 live",
    )
