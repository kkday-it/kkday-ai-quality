"""補齊 DDL 改名時新增欄位的 COMMENT（serial PK + 審計欄）

`f3d92a7c48be` 為 118 欄補了註解，但它跑在改名 migration `a8e5c31d0f62` **之前**——後者新增的
9 個 serial PK 欄與 17 個審計欄當時還不存在，因此沒有註解。本支補齊，使規範第 ④ 條
（每個欄位都有 COMMENT）真正 100%。

（改名本身不會弄丟註解：PG 的 comment 綁在 attnum 上，`RENAME COLUMN` 後仍在。
被刪掉的 4 個舊流水號欄 `id` 連同其註解一起消失，屬預期。）

Revision ID: b6f04a2e7d31
Revises: a8e5c31d0f62
Create Date: 2026-08-04

"""

from collections.abc import Sequence

from alembic import op

revision: str = "b6f04a2e7d31"
down_revision: str | Sequence[str] | None = "a8e5c31d0f62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMMENTS: tuple[str, ...] = (
    "COMMENT ON COLUMN attribution_tbl.attribution_oid IS '歸因流水號主鍵（serial）'",
    "COMMENT ON COLUMN attribution_event_lst.attribution_event_oid IS '歸因事件流水號主鍵（serial）'",
    "COMMENT ON COLUMN llm_usage_lst.llm_usage_oid IS '用量流水號主鍵（serial）'",
    "COMMENT ON COLUMN prejudge_run_tbl.prejudge_run_oid IS '初判批次流水號主鍵（serial）'",
    "COMMENT ON COLUMN prompt_debug_review_tbl.prompt_debug_review_oid IS '評判案例流水號主鍵（serial）'",
    "COMMENT ON COLUMN judge_rule_version_lst.judge_rule_version_oid IS '規則版本流水號主鍵（serial）'",
    "COMMENT ON COLUMN upload_batch_tbl.upload_batch_oid IS '上傳批次流水號主鍵（serial）'",
    "COMMENT ON COLUMN setting_master.setting_oid IS '設定流水號主鍵（serial）'",
    "COMMENT ON COLUMN evidence_snapshot_tbl.evidence_snapshot_oid IS '佐證快照流水號主鍵（serial）'",
    "COMMENT ON COLUMN attribution_tbl.create_user IS '建立者（user email 或 system:* 標記）'",
    "COMMENT ON COLUMN attribution_tbl.modify_user IS '最後修改者（user email 或 system:* 標記）'",
    "COMMENT ON COLUMN attribution_tbl.modify_date IS '最後修改時間'",
    "COMMENT ON COLUMN llm_usage_lst.create_user IS '建立者（user email 或 system:* 標記）'",
    "COMMENT ON COLUMN prejudge_run_tbl.modify_user IS '最後修改者（user email 或 system:* 標記）'",
    "COMMENT ON COLUMN prejudge_run_tbl.modify_date IS '最後修改時間'",
    "COMMENT ON COLUMN prompt_debug_review_tbl.modify_user IS '最後修改者（user email 或 system:* 標記）'",
    "COMMENT ON COLUMN prompt_debug_review_tbl.modify_date IS '最後修改時間'",
    "COMMENT ON COLUMN upload_batch_tbl.create_user IS '建立者（user email 或 system:* 標記）'",
    "COMMENT ON COLUMN upload_batch_tbl.modify_user IS '最後修改者（user email 或 system:* 標記）'",
    "COMMENT ON COLUMN upload_batch_tbl.modify_date IS '最後修改時間'",
    "COMMENT ON COLUMN setting_master.create_user IS '建立者（user email 或 system:* 標記）'",
    "COMMENT ON COLUMN setting_master.create_date IS '建立時間'",
    "COMMENT ON COLUMN setting_master.modify_user IS '最後修改者（user email 或 system:* 標記）'",
    "COMMENT ON COLUMN evidence_snapshot_tbl.create_user IS '建立者（user email 或 system:* 標記）'",
    "COMMENT ON COLUMN evidence_snapshot_tbl.modify_user IS '最後修改者（user email 或 system:* 標記）'",
    "COMMENT ON COLUMN evidence_snapshot_tbl.modify_date IS '最後修改時間'",
)


def upgrade() -> None:
    """補上 serial PK 欄與審計欄的註解。"""
    for stmt in _COMMENTS:
        op.execute(stmt)


def downgrade() -> None:
    """移除本支加上的註解。"""
    for stmt in _COMMENTS:
        op.execute(f"{stmt.split(' IS ')[0]} IS NULL")
