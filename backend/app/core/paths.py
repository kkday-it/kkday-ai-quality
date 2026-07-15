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
)  # 判決領域規則配置（judgment / source_mapping / domains；判準文字本體在 prompts）

# repo 根 data/：runtime 派生產物（報表），整目錄 gitignore、可整刪重生。
# 曾散落各檔自拼 REPO_ROOT/"data"/...（accuracy），收斂至此 SSOT；env 覆蓋同 CONFIG_DIR 邏輯。
DATA_DIR: Path = Path(os.getenv("AIQ_DATA_DIR") or (REPO_ROOT / "data")).resolve()
REPORTS_DIR: Path = DATA_DIR / "reports"  # 準確度 / 規則品質報表（judge/accuracy、scripts/audit）
LLM_CACHE_DIR: Path = (
    DATA_DIR / "llm_cache"
)  # LLM exact-match 結果快取（judge/llm/client.py；可整刪重生）

# repo 根 constants/：前後端共用「固定參照」字典（前端 @constants alias 同讀；後端按需讀取）。
CONSTANTS_DIR: Path = REPO_ROOT / "constants"

# repo 根 prompts/：「判決 Prompt 唯一真相源」（7 支 md，Prompt-as-Source 執行期 SSOT，與 config/、
# constants/ 同級頂層目錄，非人閱文檔故不置於 docs/）。prompt_source 於 DB 無 active 版時的檔案
# fallback 讀此。容器：dev 掛 ./prompts:/app/prompts、prod Dockerfile COPY prompts；env 覆蓋同
# CONFIG_DIR 邏輯（跨環境會變的路徑走 env）。
PROMPTS_DIR: Path = Path(os.getenv("AIQ_PROMPTS_DIR") or (REPO_ROOT / "prompts")).resolve()
