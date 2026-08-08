"""權限 business-key 具名常數（be2 風格 `module.sub-function.action`）+ 全集。

命名對齊 be2 中央 Auth SVC 的 permission-string（如 `order.booking-method.query`），日後接
be2 時 key 命名即整合面之一。**禁在 router 散寫字面字串**——一律引本模組常數（改 key 一處到底）。
哪些 email 具備哪些 key 由 `config/global/permissions.json` 定義（default ∪ grants[email]，
無角色中間層，SSOT），本檔只定義「有哪些 key」。
"""

from __future__ import annotations

# ── 判準法典管理（破壞性：改初判規範 / 覆蓋整庫，只在 grants）──
JUDGE_RULE_MANAGE = "judge-rule.version.manage"  # 發布 / 恢復版本 / 恢復默認初判規則
DATA_DATAPACK_IMPORT = "data.datapack.import"  # 全庫資料包匯入（truncate-then-load·最高破壞性）
ATTRIBUTION_DIMENSION_MANAGE = (
    "attribution.dimension.manage"  # 維護判決值域主檔（改 label 影響全庫判決顯示）
)

# ── 日常質檢作業（入 default，登入即可用）──
DATA_DATAPACK_EXPORT = "data.datapack.export"  # 全庫資料包導出
DATA_SOURCE_UPLOAD = "data.source.upload"  # 上傳 5 來源資料落庫
PROBLEM_LIST_EXPORT = "problem.list.export"  # 導出問題列表 xlsx
PREJUDGE_RUN = "prejudge.run"  # 啟動/暫停/恢復/停止批量初判歸因（消耗 LLM 額度）
ATTRIBUTION_CORRECTION_MANAGE = (
    "attribution.correction.manage"  # 人工糾正歸因（改/增/標記誤判/還原）
)
ATTRIBUTION_REVIEW = "attribution.review"  # 複審確認 AI 判對（待複審的出口）

# ── 設定管理（LLM 連線 + QC DB 連線；敏感項只在 grants）──
# 模型配置庫（llm_model_configs）刻意**不設 key**：它只決定「用哪個 model、思考多深」，不涉及
# 新端點或機密外洩面（要打哪裡、拿什麼 token 打，仍由受 SETTINGS_LLM_CONFIG_MANAGE 保護的
# llm_connections/llm_tokens 決定），屬登入即可用的日常操作。
SETTINGS_LLM_CONFIG_MANAGE = "settings.llm-config.manage"  # 改 LLM 連線（供應商 base_url/token）
SETTINGS_QC_CONFIG_MANAGE = "settings.qc-config.manage"  # 改 QC DB 連線
SETTINGS_SECRET_READ = "settings.secret.read"  # 看明文機密（/api/settings/raw）

# 全量 key：grants "*" 展開用 + local provider 過濾 config 打錯的未知 key。
ALL_KEYS: frozenset[str] = frozenset(
    {
        JUDGE_RULE_MANAGE,
        DATA_DATAPACK_IMPORT,
        ATTRIBUTION_DIMENSION_MANAGE,
        DATA_DATAPACK_EXPORT,
        DATA_SOURCE_UPLOAD,
        PROBLEM_LIST_EXPORT,
        PREJUDGE_RUN,
        ATTRIBUTION_CORRECTION_MANAGE,
        ATTRIBUTION_REVIEW,
        SETTINGS_LLM_CONFIG_MANAGE,
        SETTINGS_QC_CONFIG_MANAGE,
        SETTINGS_SECRET_READ,
    }
)
