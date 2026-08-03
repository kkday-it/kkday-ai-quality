"""product_reviews BD tag 欄改名：對齊 conversations 命名（bd_tag_cd＝代碼、bd_tag＝中文）

兩表原本命名相反（BQ 端兩份查詢的匯出命名各自演進不同步）：
- product_reviews：bd_tag＝代碼、bd_tag_note＝中文
- conversations  ：bd_tag_cd＝代碼、bd_tag＝中文

本次統一為 conversations 的命名。因兩欄語義互換，rename 必須依序執行
（先讓出 bd_tag 這個名字，再把 bd_tag_note 改進來），否則第二步撞名。
既有值隨欄位一起搬移，不需另做 UPDATE。

Revision ID: e2b6d4f18a03
Revises: d1f7a3c8e520
Create Date: 2026-08-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e2b6d4f18a03"
down_revision: str | Sequence[str] | None = "d1f7a3c8e520"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """bd_tag（代碼）→ bd_tag_cd；bd_tag_note（中文）→ bd_tag。順序不可對調。"""
    op.alter_column("product_reviews", "bd_tag", new_column_name="bd_tag_cd")
    op.alter_column("product_reviews", "bd_tag_note", new_column_name="bd_tag")


def downgrade() -> None:
    """還原為舊命名；同樣需依序（先讓出 bd_tag，再改回 bd_tag_note）。"""
    op.alter_column("product_reviews", "bd_tag", new_column_name="bd_tag_note")
    op.alter_column("product_reviews", "bd_tag_cd", new_column_name="bd_tag")
