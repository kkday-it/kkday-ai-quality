"""跨模組共用的路徑定位器（SSOT）。

repo 根的 config/ 為前後端共用的非機密配置目錄（前端以 @config alias 同讀同一批 JSON）。
此處集中把 repo 根算「一次」，其餘模組一律 `from app.core.paths import CONFIG_DIR / GLOBAL_DIR / ...`，
禁止各檔自行 `Path(__file__).resolve().parents[N]` 數層數——那會讓「定位邏輯」散落多份，
某檔一旦搬動目錄深度，它的 parents[N] 就靜默指錯（且各檔 N 已不一致：core/*=3、api/routers/*=4）。
"""

from __future__ import annotations

import os
from pathlib import Path

# paths.py = backend/app/core/paths.py → parents[3] = repo 根（全專案唯一一處數層數）
REPO_ROOT: Path = Path(__file__).resolve().parents[3]

# 部署（如 Docker 把 config/ 掛到別處）可用 AIQ_CONFIG_DIR 覆蓋；未設則用 repo 根 config/。
# 「跨環境會變的路徑」走 env，符合專案三層 config 決策樹。
CONFIG_DIR: Path = Path(os.getenv("AIQ_CONFIG_DIR") or (REPO_ROOT / "config")).resolve()

GLOBAL_DIR: Path = CONFIG_DIR / "global"  # 前後端共用非機密（model 清單 / QC 預設 / 定價）
AI_JUDGE_DIR: Path = (
    CONFIG_DIR / "ai_judge"
)  # 初判/判決領域配置（prejudge / verdict / source_mapping；判準文字本體在 prompts）

# repo 根 data/：runtime 派生產物（報表），整目錄 gitignore、可整刪重生。
# 曾散落各檔自拼 REPO_ROOT/"data"/...（accuracy），收斂至此 SSOT；env 覆蓋同 CONFIG_DIR 邏輯。
DATA_DIR: Path = Path(os.getenv("AIQ_DATA_DIR") or (REPO_ROOT / "data")).resolve()
REPORTS_DIR: Path = DATA_DIR / "reports"  # 準確度 / 規則品質報表（judge/accuracy、scripts/audit）
LLM_CACHE_DIR: Path = (
    DATA_DIR / "llm_cache"
)  # LLM exact-match 結果快取（judge/llm/client.py；可整刪重生）

# repo 根 constants/：前後端共用「固定參照」字典（前端 @constants alias 同讀；後端按需讀取）。
CONSTANTS_DIR: Path = REPO_ROOT / "constants"

# repo 根 prompts/：判決 Prompt 唯一真相源（Prompt-as-Source 執行期 SSOT，與 config/、constants/
# 同級頂層目錄，非人閱文檔故不置於 docs/）。容器：dev 掛 ./prompts:/app/prompts、prod Dockerfile
# COPY prompts；env 覆蓋同 CONFIG_DIR 邏輯（跨環境會變的路徑走 env）。
# 底下按「判決任務」分兩線，兩線互不相通（無共用 loader、無共用落庫）：
#   - 根目錄扁平 9 支 md（`00_polarity` + `01_C-1`~`06_C-6` + BASELINE + README）＝初判歸因，
#     跨全 5 反饋來源；由 prompt_source 讀（DB active 版優先、缺版時 fallback 讀檔）。
#   - `conversations/`＝售後根因調試台（僅 IM session），時間戳一版一檔純檔案版本庫，見下方常數。
PROMPTS_DIR: Path = Path(os.getenv("AIQ_PROMPTS_DIR") or (REPO_ROOT / "prompts")).resolve()

# 售後根因調試台的 prompt 根：草稿區在 `root_cause_drafts/`、正式版區在 `versions/`、
# AI 改寫用 system prompt 是 `reviser.md`。
# 收成常數而非讓各消費端各自拼 `PROMPTS_DIR / "conversations"`——2026-07-30 搬動目錄時正是因為
# 路徑散在兩個模組硬拼、無收斂點，才讓調試台整條路徑靜默斷掉（版本庫解不出來、reviser 讀不到）。
CONVERSATION_PROMPTS_DIR: Path = PROMPTS_DIR / "conversations"

# 草稿／正式版雙軌：草稿＝時間戳一版一檔的實驗區；正式版＝自訂命名 + `index.json` 指針的線上口徑。
# 兩區分開常數，讓「當前口徑」與「最新草稿」在 code 層就無法混用。
# ⚠️ 2026-07-30 起**跑批兩軌都能讀**（調試台是草稿工作台，草稿:正式版懸殊下硬拒等於跑批不可用），
# 由 manifest 的 `prompt_kind` 顯式記錄本批跑的是哪一軌，不再靠「限制能讀什麼」當防線。
ROOT_CAUSE_DRAFTS_DIR: Path = CONVERSATION_PROMPTS_DIR / "root_cause_drafts"
ROOT_CAUSE_RELEASES_DIR: Path = CONVERSATION_PROMPTS_DIR / "versions"
