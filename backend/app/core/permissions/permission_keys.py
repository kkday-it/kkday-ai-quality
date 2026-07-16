"""權限 business-key 具名常數（be2 風格 `module.sub-function.action`）+ 全集。

命名對齊 be2 中央 Auth SVC 的 permission-string（如 `order.booking-method.query`），日後接
be2 時 key 命名即整合面之一。**禁在 router 散寫字面字串**——一律引本模組常數（改 key 一處到底）。
角色→哪些 key 由 `config/global/role_permissions.json` 定義（SSOT），本檔只定義「有哪些 key」。
"""

from __future__ import annotations

# ── 判準法典管理（admin 級·破壞性：改判決規範 / 覆蓋整庫）──
JUDGE_RULE_MANAGE = "judge-rule.version.manage"  # 發布 / 恢復版本 / 恢復默認判決規則
DATA_DATAPACK_IMPORT = "data.datapack.import"  # 全庫資料包匯入（truncate-then-load·最高破壞性）

# ── 日常質檢作業（qc 級·qc + admin 皆可）──
DATA_DATAPACK_EXPORT = "data.datapack.export"  # 全庫資料包導出
DATA_SOURCE_UPLOAD = "data.source.upload"  # 上傳 5 來源資料落庫
FINDING_REVIEW_UPDATE = "finding.review.update"  # 歸因人工覆核（確認 / 忽略 / 撤銷）
PROBLEM_LIST_EXPORT = "problem.list.export"  # 導出問題列表 xlsx
JUDGMENT_PREJUDGE_RUN = "judgment.prejudge.run"  # 啟動/暫停/恢復/停止批量初判歸因（消耗 LLM 額度）

# 全量 key：admin 慣例值 '*' 展開用 + local provider 過濾 config 打錯的未知 key。
ALL_KEYS: frozenset[str] = frozenset(
    {
        JUDGE_RULE_MANAGE,
        DATA_DATAPACK_IMPORT,
        DATA_DATAPACK_EXPORT,
        DATA_SOURCE_UPLOAD,
        FINDING_REVIEW_UPDATE,
        PROBLEM_LIST_EXPORT,
        JUDGMENT_PREJUDGE_RUN,
    }
)
