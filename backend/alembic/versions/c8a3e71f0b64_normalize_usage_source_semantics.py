"""llm_usage_lst：把「呼叫者」從 feedback_source_code 移回 stage

`feedback_source_code` 的語義是**反饋來源**（reviews / conversations / freshdesk_tickets /
app_feedback / mixpanel_tracker）。但實測 58,537 列中有 5,647 列（9.65%）存的是**功能區**
名稱——`prompt_debug_batch` / `prompt_debug` / `prompt_revise`。那些呼叫根本不隸屬任何反饋
來源（調試台是拿任意文本試 Prompt），欄名與內容對不上。

⚠️ 這個名不符實是 `a8e5c31d0f62` 造成的：該欄原本叫 `source`，語義本來就寬（「這次呼叫是
誰驅動的」），改名成 `feedback_source_code` 才把它收窄成「反饋來源」，於是既有資料變成不合。
**是改名讓資料變髒，不是資料本來就髒**——所以正解是讓資料回到正確的欄，而不是把欄名改回去
（成本 dashboard 的「來源分佈」維度需要的正是窄語義）。

**歸屬改由 `stage` 承擔**：那正是它的職責（欄註解寫的就是「呼叫階段：polarity / C-1~C-6 /
prompt_debug / prompt_revise…」）。三列的 stage 更新為原 source 值即可——
- `prompt_debug_batch`：原 stage 是 `prompt_debug`，**source 才帶著 batch/單次的區分**，
  直接抹掉會損失資訊，故先把 stage 升級成更精確的 `prompt_debug_batch`
- `prompt_debug` / `prompt_revise`：stage 與 source 本來就相同，等值改寫

寫入端同輪修正（三處），本支只處理歷史列。

Revision ID: c8a3e71f0b64
Revises: b7d24e0a3f19
Create Date: 2026-08-04

"""

from collections.abc import Sequence

from alembic import op

revision: str = "c8a3e71f0b64"
down_revision: str | Sequence[str] | None = "b7d24e0a3f19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FEATURE_AREAS = "('prompt_debug_batch', 'prompt_debug', 'prompt_revise')"

_UPGRADE: tuple[str, ...] = (
    # stage 收下歸屬（原 source 值一律比原 stage 同等或更精確），source 清空
    f"UPDATE llm_usage_lst SET stage = feedback_source_code, feedback_source_code = NULL "
    f"WHERE feedback_source_code IN {_FEATURE_AREAS}",
    "COMMENT ON COLUMN llm_usage_lst.stage IS "
    "'呼叫階段／呼叫者：polarity / C-1~C-6 / attribute / pack_* / prompt_debug / "
    "prompt_debug_batch / prompt_revise…（非反饋來源驅動的呼叫，歸屬由此欄表達）'",
    "COMMENT ON COLUMN llm_usage_lst.feedback_source_code IS "
    "'反饋來源 code（reviews / conversations / freshdesk_tickets / app_feedback / "
    "mixpanel_tracker）；非反饋來源驅動的呼叫（調試台、AI 改寫）為空'",
    # ── 順帶把兩個「看起來冗餘但刻意保留」的欄，理由寫進註解 ──
    # 逐欄稽核曾建議刪 l1_label（與 l1_code 雙射）。實測 l1 是 7↔7↔7、l2 是 32↔32↔32，
    # 兩者對稱，沒有只刪其一的依據；而兩個 label 都是**判決當下的分類名快照**——分類體系
    # 改寫措辭時，讀取時推導會讓 6,242 列歷史歸因的顯示文字被回溯改寫。故兩個都留。
    "COMMENT ON COLUMN attribution_tbl.l1_label IS "
    "'L1 域名稱（判決當下的快照，非讀取時推導——分類體系改寫措辭時不回溯改變歷史歸因的顯示）'",
    "COMMENT ON COLUMN attribution_tbl.l2_label IS "
    "'L2 面向名稱（判決當下的快照，理由同 l1_label）'",
    "COMMENT ON COLUMN evidence_snapshot_tbl.package_module_setting IS "
    "'方案模組設定 list[{prod_module_type, prod_module_setting}]（來源 ors_prod_module_setting）'",
)


def upgrade() -> None:
    """歷史列歸屬改掛 stage；補上三個欄的語義註解。"""
    for stmt in _UPGRADE:
        op.execute(stmt)


def downgrade() -> None:
    """把歸屬搬回 feedback_source_code（`prompt_debug_batch` 的 stage 一併還原）。"""
    op.execute(
        f"UPDATE llm_usage_lst SET feedback_source_code = stage WHERE stage IN {_FEATURE_AREAS}"
    )
    op.execute("UPDATE llm_usage_lst SET stage = 'prompt_debug' WHERE stage = 'prompt_debug_batch'")
