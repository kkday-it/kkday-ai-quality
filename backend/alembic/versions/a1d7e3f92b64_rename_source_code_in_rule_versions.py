"""版本化規則內容同步改名：product_reviews → reviews

`source_mapping` 與 `bd_tag_vertical` 是 DB 版本化規則（judge_rule_versions.content JSONB），
實際生效的是 DB 裡的 active 版本，config/ai_judge/*.json 只是「恢復默認」的檔案來源。
因此前一支表改名 migration 只改了表與 source 欄值，DB 規則內容仍是舊 key，
導致 source='reviews' 查不到 field_map、canonical 欄位（OID / 出發日 / 語系）全空。

歷史版本一併改：舊版留著舊 key 沒有意義（表已不存在），使用者一旦從歷史還原就會壞。

⚠️ 此類「JSONB 內容形狀/語意變更」不會被 alembic schema_version 比對攔住
（欄位型別沒變），是 datapack 匯入的已知盲區——舊資料包匯入後需重跑一次本遷移或恢復默認。

Revision ID: a1d7e3f92b64
Revises: f4c9a2e81b57
Create Date: 2026-08-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a1d7e3f92b64"
down_revision: str | Sequence[str] | None = "f4c9a2e81b57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """所有版本（含歷史）的 content 內 product_reviews 字串改為 reviews。

    以文字替換而非 jsonb key 操作：舊 key 除了 sources 底下的來源 key，也出現在
    _meta / _comment 等說明文字中，一次替換涵蓋全部位置。
    """
    op.execute(
        """
        UPDATE judge_rule_versions
        SET content = REPLACE(content::text, 'product_reviews', 'reviews')::jsonb
        WHERE content::text LIKE '%product_reviews%'
        """
    )


def downgrade() -> None:
    """還原為舊 key（upgrade 後 content 內已無 product_reviews，反向替換不會疊加前綴）。"""
    op.execute(
        """
        UPDATE judge_rule_versions
        SET content = REPLACE(content::text, 'reviews', 'product_reviews')::jsonb
        WHERE content::text LIKE '%reviews%'
        """
    )
