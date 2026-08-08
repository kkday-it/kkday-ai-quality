"""資料層存取（SQLAlchemy Core · PostgreSQL）— db package barrel。

原單一 db.py（~1400 行）按職責拆為子模組（settings_store / rule_versions / ingest / findings /
problems / prejudge_targets / attribution / export + _shared 共用）；此 barrel re-export 全部公開函式，
使既有 `from app.core import db` + `db.list_problems(...)` 等呼叫端零改動。
schema：`init_db()` 用 metadata.create_all（dev/測試）；prod schema 演進交 Alembic（見 alembic/）。
"""

from app.core.db.attribution import (
    ai_judge_overview_stats,
    attribution_breakdown,
    attribution_overview,
)
from app.core.db.attribution_history import (
    latest_snapshots,
    list_attribution_history,
    list_prejudge_models,
)
from app.core.db.corrections import (
    CorrectionError,
    confirm_attribution,
    correct_attribution,
    create_attribution,
    delete_attribution,
    editable_fields,
    list_record_attributions,
    restore_attribution,
    swap_attribution_slots,
)
from app.core.db.dimensions import (
    list_dimensions,
    reorder_dimension,
    save_dimension_item,
    seed_dimensions_from_file,
)
from app.core.db.export import export_problems_xlsx
from app.core.db.findings import (
    replace_source_findings,
)
from app.core.db.ingest import (
    create_batch,
    get_items_by_ids,
    init_db,
    insert_source_batch,
    list_batches,
)
from app.core.db.llm_usage import (
    insert_llm_usage_row,
    insert_llm_usage_rows,
    llm_usage_overview,
)
from app.core.db.notes import (
    NoteError,
    active_note_types,
    add_note,
    list_notes,
    note_counts,
)
from app.core.db.prejudge_runs import (
    any_judged,
    finish_prejudge_run,
    get_run_log,
    insert_prejudge_run,
    list_prejudge_runs,
    prejudge_run_detail,
    save_run_log_item,
    update_prejudge_run_status,
)
from app.core.db.prejudge_targets import prejudge_target_ids
from app.core.db.problems import list_problems
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
from app.core.db.settings_store import (
    load_settings_row,
    save_settings_row,
)
from app.core.db.suggestions import (
    list_pending_suggestions,
    pending_counts,
    resolve_suggestions,
)

__all__ = [
    "RULE_CODES",
    "ai_judge_overview_stats",
    "attribution_breakdown",
    "attribution_overview",
    "create_batch",
    "default_rule_content",
    "export_problems_xlsx",
    "get_items_by_ids",
    "get_rule_active",
    "get_rule_version",
    "init_db",
    "insert_source_batch",
    "list_batches",
    "list_problems",
    "list_rule_history",
    "list_rule_meta",
    "load_settings_row",
    "prejudge_target_ids",
    "replace_source_findings",
    "reset_all_rule_defaults",
    "reset_rule_default",
    "restore_rule_version",
    "save_rule_version",
    "save_settings_row",
    "seed_rules_from_files",
    "latest_snapshots",
    "list_attribution_history",
    "list_prejudge_models",
    "insert_llm_usage_row",
    "insert_llm_usage_rows",
    "llm_usage_overview",
    "any_judged",
    "finish_prejudge_run",
    "list_pending_suggestions",
    "pending_counts",
    "resolve_suggestions",
    "list_dimensions",
    "reorder_dimension",
    "save_dimension_item",
    "seed_dimensions_from_file",
    "CorrectionError",
    "confirm_attribution",
    "correct_attribution",
    "create_attribution",
    "delete_attribution",
    "editable_fields",
    "list_record_attributions",
    "swap_attribution_slots",
    "NoteError",
    "active_note_types",
    "add_note",
    "list_notes",
    "note_counts",
    "restore_attribution",
    "get_run_log",
    "insert_prejudge_run",
    "prejudge_run_detail",
    "list_prejudge_runs",
    "save_run_log_item",
    "update_prejudge_run_status",
]
