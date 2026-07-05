"""backfill judgment auto_confirm config

Revision ID: b3e9c2a10f47
Revises: 85a7dea69f9d
Create Date: 2026-07-05

"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3e9c2a10f47"
down_revision: Union[str, Sequence[str], None] = "85a7dea69f9d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# G1 自動確認旋鈕預設（對齊 config/ai_judge/judgment.json；migration 為時點快照，內嵌不讀檔避免路徑漂移）。
_AUTO_CONFIRM_DEFAULT = {
    "_comment": (
        "G1 自動確認路由：auto_accept+judged 的判決自動採信、不進人工佇列（status=auto_confirmed）；"
        "audit_sample_rate 比例抽樣回 new 交人工複核（防自動化偏誤——LLM 高召回低精確）。"
        "QC 免改碼調（本檔已納 RULE_CODES 熱重載）。"
    ),
    "enabled": True,
    "audit_sample_rate": 0.05,
}
_NOTE = "backfill auto_confirm（G1 旋鈕，供規則頁編輯）"


def upgrade() -> None:
    """把 auto_confirm 旋鈕併入 judgment active 版本，使 QC 能於規則頁「判決配置」編輯 audit_sample_rate。

    judgment 早於 G1 即 seed 進 DB → active content 缺 auto_confirm；新增判決配置編輯器後若不補，
    QC 開啟看不到該旋鈕（G1 靠 _auto_confirm_cfg 硬編 default 兜底，無法由 UI 調）。此處以 JSONB 合併補上，
    存為新 active 版（version+1）、舊版轉非 active（append-only，保留歷史）。冪等：active 版已含則不動；
    judgment 未 seed（全新庫由 seed_rules_from_files 帶新 judgment.json，已含 auto_confirm）則跳過。
    """
    bind = op.get_bind()
    row = bind.execute(
        sa.text(
            "SELECT version, content FROM judge_rule_versions "
            "WHERE rule_code='judgment' AND is_active ORDER BY version DESC LIMIT 1"
        )
    ).fetchone()
    if row is None:
        return  # 未 seed judgment（新庫 seed 時 judgment.json 已含 auto_confirm）→ 無需 backfill
    version, content = row[0], row[1]
    if isinstance(content, str):  # JSONB 通常回 dict；字串型驅動時解析
        content = json.loads(content)
    if "auto_confirm" in content:
        return  # 冪等：已含旋鈕不重複補
    merged = {**content, "auto_confirm": _AUTO_CONFIRM_DEFAULT}
    bind.execute(
        sa.text("UPDATE judge_rule_versions SET is_active=false WHERE rule_code='judgment' AND is_active")
    )
    bind.execute(
        sa.text(
            "INSERT INTO judge_rule_versions (rule_code, version, content, note, author, is_active) "
            "VALUES ('judgment', :v, CAST(:c AS jsonb), :note, 'system', true)"
        ),
        {"v": version + 1, "c": json.dumps(merged, ensure_ascii=False), "note": _NOTE},
    )


def downgrade() -> None:
    """回滾 backfill：刪本遷移新增的 auto_confirm 版，前一版重新 active（僅認本遷移的 note 標記，不誤刪 QC 後續編輯）。"""
    bind = op.get_bind()
    deleted = bind.execute(
        sa.text("DELETE FROM judge_rule_versions WHERE rule_code='judgment' AND note=:note"),
        {"note": _NOTE},
    ).rowcount
    if deleted:
        bind.execute(
            sa.text(
                "UPDATE judge_rule_versions SET is_active=true WHERE rule_code='judgment' "
                "AND version=(SELECT MAX(version) FROM judge_rule_versions WHERE rule_code='judgment')"
            )
        )
