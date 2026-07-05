"""資料層存取（SQLAlchemy Core · PostgreSQL）— db package barrel。

原單一 db.py（~1400 行）按職責拆為子模組（users / rule_versions / ingest / findings / problems /
prejudge_targets / attribution / export + _shared 共用）；此 barrel re-export 全部公開函式，
使既有 `from app.core import db` + `db.list_problems(...)` 等呼叫端零改動。
schema：`init_db()` 用 metadata.create_all（dev/測試）；prod schema 演進交 Alembic（見 alembic/）。
"""

from app.core.db.attribution import attribution_breakdown, attribution_overview
from app.core.db.export import export_problems_xlsx
from app.core.db.findings import (
    get_finding,
    insert_finding,
    list_findings,
    list_products,
    replace_source_findings,
    update_finding_status,
    update_finding_true_label,
)
from app.core.db.ingest import (
    create_batch,
    get_items_by_ids,
    init_db,
    insert_source_batch,
    list_batches,
    update_batch_inserted,
)
from app.core.db.prejudge_targets import prejudge_target_ids
from app.core.db.problems import list_l1_domains, list_problems
from app.core.db.rule_versions import (
    RULE_CODES,
    default_rule_content,
    get_rule_active,
    get_rule_version,
    list_rule_history,
    list_rule_meta,
    reset_all_rule_defaults,
    reset_rule_default,
    restore_rule_version,
    save_rule_version,
    seed_rules_from_files,
)
from app.core.db.users import (
    DuplicateEmailError,
    create_user,
    get_user_by_email,
    get_user_by_id,
    load_user_settings,
    save_user_settings,
)

__all__ = [
    "RULE_CODES",
    "DuplicateEmailError",
    "attribution_breakdown",
    "attribution_overview",
    "create_batch",
    "create_user",
    "default_rule_content",
    "export_problems_xlsx",
    "get_items_by_ids",
    "get_rule_active",
    "get_rule_version",
    "get_user_by_email",
    "get_user_by_id",
    "init_db",
    "insert_finding",
    "insert_source_batch",
    "list_batches",
    "list_findings",
    "list_problems",
    "list_l1_domains",
    "list_products",
    "list_rule_history",
    "list_rule_meta",
    "load_user_settings",
    "prejudge_target_ids",
    "replace_source_findings",
    "reset_all_rule_defaults",
    "reset_rule_default",
    "restore_rule_version",
    "save_rule_version",
    "save_user_settings",
    "seed_rules_from_files",
    "get_finding",
    "update_batch_inserted",
    "update_finding_status",
    "update_finding_true_label",
]
