"""環境變數 / 機密集中管理（Pydantic Settings）。

取代散落各處的 os.environ.get()：import 時讀 backend/.env（gitignore），型別化 +
缺漏即時可見。全後端統一從本模組的 env 單例取機密，禁止再裸讀 os.environ。

分工：
- 機密 / 環境相關（本檔）：JWT secret、LLM token fallback、KKday/Mixpanel 憑證 → backend/.env
- 非機密共用預設（另處）：QC DB host/port、LLM provider 目錄 → repo 根 config/global/default_qc.json、default_llm.json
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

    # ── 認證 ──
    aipq_jwt_secret: str | None = None
    jwt_ttl_days: int = 7  # JWT 有效期（天）；prod 可縮短
    # ── 資料層（app 操作庫；PostgreSQL only。dev 預設本機，prod 經 env DATABASE_URL 覆蓋）──
    database_url: str = "postgresql+psycopg2://localhost:5432/kkdb_ai_quality"
    # ── 服務 / 部署（可 env 覆蓋，免改碼）──
    cors_allow_origins: str = "http://localhost:5273"  # 逗號分隔多 origin；對齊 vite dev port 5273
    http_timeout: int = 30  # 外部 API（B2C / 評論）httpx timeout 秒
    # ── 初判歸因批量併發（I/O bound LLM；OpenAI 無併發硬上限、僅 RPM/TPM，gpt-5-mini Tier1 500K TPM 足以支撐）──
    prejudge_max_workers: int = 64  # ThreadPool 全域上限；多 job 疊加時由 Semaphore 收斂到此值
    llm_timeout: int = 60  # 單次 LLM 呼叫 timeout 秒（漏斗每筆 2 call，逾時即失敗交人審）
    qc_db_connect_timeout: int = 5  # QC DB 連線測試 timeout 秒
    bigquery_project_id: str = "kkday-data-dap"  # BQ live 抽取 project
    # ── LLM fallback（優先級低於 DB user_settings 面板設定）──
    openai_api_key: str = ""
    ai_judge_model: str = "gpt-5-nano"  # fallback 預設＝最省（對齊 default_llm.json defaultModel）；下拉見 defaultModels（minVersion 已降至 5）
    # ── KKday B2C API（datasource live 模式才需要）──
    kkday_b2c_token1: str = ""
    kkday_x_auth_token: str = ""
    # ── Mixpanel 進線 CLI（python -m app.judge.ingest.mixpanel_feedback）──
    mixpanel_sa_username: str | None = None
    mixpanel_sa_password: str | None = None
    mixpanel_project_id: str | None = None
    mixpanel_token: str | None = None
    mixpanel_out_dir: str | None = None


# 單例：import 時即載入 .env，全後端共用。
env = Settings()
