"""環境變數 / 機密集中管理（Pydantic Settings）。

取代散落各處的 os.environ.get()：import 時讀 backend/.env（gitignore），型別化 +
缺漏即時可見。全後端統一從本模組的 env 單例取機密，禁止再裸讀 os.environ。

分工：
- 機密 / 環境相關（本檔）：JWT secret、LLM token fallback、KKday/Mixpanel 憑證 → backend/.env
- 非機密共用預設（另處）：QC DB host/port、LLM provider 目錄 → repo 根 config/global/qc_db.json、llm_model.json
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/.env：config.py = backend/app/core/config.py → parents[2] = backend/。
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """後端 env 機密 / 環境變數。欄位名小寫，自動對應同名大寫 env（case-insensitive）。"""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # .env 含未定義 key 不報錯
    )

    # ── 環境 ──
    app_env: str = (
        "development"  # development / staging / production；非 development 缺 JWT secret 拒啟動
    )
    # ── 認證 ──
    aiq_jwt_secret: str | None = None
    # user_settings 機密（provider_tokens/qc_passwords）at-rest 加密 passphrase；
    # 未設＝明文落庫（dev 相容），設定後新寫入即加密、舊列跑 scripts/tools/encrypt_user_secrets.py。
    aiq_secret_key: str | None = None
    jwt_ttl_days: int = 7  # JWT 有效期（天）；prod 可縮短
    min_password_length: int = 6  # 註冊密碼最短長度（安全政策，可依合規調整）
    # ── 資料層（app 操作庫；PostgreSQL only。dev 預設本機，prod 經 env DATABASE_URL 覆蓋）──
    database_url: str = "postgresql+psycopg2://localhost:5432/kkdb_ai_quality"
    # 連線池（跨環境可調；prejudge 併發 64 執行緒共享，預設 15 明顯不足 → 拉高。
    # pool_size + max_overflow = 單 process 連線上限，須 < PG max_connections(預設 100)，留餘裕給其他連線）。
    db_pool_size: int = 10  # 常駐連線數
    db_max_overflow: int = 20  # 尖峰可超額連線數（10+20=30 上限）
    db_pool_recycle: int = (
        1800  # 連線回收秒（避免 PG 端 idle 斷線後借到死連線；配 pool_pre_ping 雙保險）
    )
    # ── 服務 / 部署（可 env 覆蓋，免改碼）──
    cors_allow_origins: str = "http://localhost:5273"  # 逗號分隔多 origin；對齊 vite dev port 5273
    # kklog 服務識別鍵（Kibana log_type.keyword 查詢用；正式名稱與 DevOps 對齊 kkday-k8s-apps 慣例後 env 覆蓋）
    log_type: str = "aiq-backend"
    # 全庫資料包匯入開關（破壞性：清空並覆蓋整庫）。None＝依環境（development 開、其餘關）；
    # 顯式 true/false 覆蓋。防生產誤觸；上線收緊 admin 閘後仍建議留此環境級保險。
    aiq_allow_data_import: bool | None = None
    # 自助註冊開關。None＝依環境（development 開、其餘關）；顯式 true/false 覆蓋。
    # 防生產環境任何人自助建帳號即取得 qc 角色全權（含 datapack.import 全庫覆寫）；
    # prod 首次部署 bootstrap admin 時臨時設 true，建完帳號即移除（見 docker/README.md）。
    aiq_allow_self_register: bool | None = None
    # ── 初判歸因批量併發（I/O bound LLM；OpenAI 無併發硬上限、僅 RPM/TPM，gpt-5-mini Tier1 500K TPM 足以支撐）──
    prejudge_max_workers: int = 64  # ThreadPool 全域上限；多 job 疊加時由 Semaphore 收斂到此值
    llm_timeout: int = (
        300  # 單次 LLM 呼叫 timeout 秒（5 分鐘；容納 high/xhigh 推理較慢；env LLM_TIMEOUT 可覆蓋）
    )
    llm_timeout_flex: int = (
        900  # flex tier 單次呼叫 timeout 秒（官方建議 15 分鐘：flex 延遲變動大易逾時）
    )
    # LLM exact-match 結果快取（diskcache·data/llm_cache）：key=model+messages+response_format+effort 的
    # 雜湊——prompt 內嵌規則正文，規則一改 key 即變（失效粒度自動精準）；命中＝重用先前初判、零 token 零延遲。
    # 重新初判密集工作流（規則微調→全量重新初判）下未變更部分全免費；語義中性（同輸入同規則＝同初判），不影響準確度。
    llm_exact_cache: bool = True
    llm_cache_ttl_days: int = 30  # 快取條目存活天數（過期自動失效；目錄可整刪重生）
    qc_db_connect_timeout: int = 5  # QC DB 連線測試 timeout 秒
    # ── 訂單佐證取數服務帳號（R17 憑證抽象層的 env 注入點；SA/SD 核發 kkday_ai_quality_api_user
    # 後於部署層設定即自動優先於 per-user qc_configs，查詢邏輯零改動。現階段留空＝走 fallback）──
    evidence_db_host: str = ""
    evidence_db_port: int = 5432
    evidence_db_user: str = ""
    evidence_db_password: str = ""
    # ── LLM fallback（優先級低於 DB user_settings 面板設定）──
    openai_api_key: str = ""
    ai_judge_model: str = (
        "gpt-5-mini"  # fallback 預設＝最低可用模型（nano 已下架，對齊 llm_model.json defaultModel）
    )
    llm_max_retries: int = (
        5  # 單次 LLM 呼叫 429/5xx 最大重試次數（改值需重啟；client 依 token/base_url 快取）
    )


# 單例：import 時即載入 .env，全後端共用。
env = Settings()


def is_production() -> bool:
    """非 development 一律視為正式環境（含 staging）。

    收斂 auth/crypto/admin_import/prejudge 各處散落的 `env.app_env != "development"`
    字串比較——環境尺度只此一份，避免各處各寫一次語意漂移（如誤寫 == "production" 漏掉 staging）。
    """
    return env.app_env != "development"
