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

GLOBAL_DIR: Path = CONFIG_DIR / "global"      # 前後端共用非機密（model 清單 / QC 預設 / 定價）
AI_JUDGE_DIR: Path = CONFIG_DIR / "ai_judge"  # 判決領域規則樹（rule_C-* / source_mapping / domains）
TAXONOMY_DIR: Path = CONFIG_DIR / "taxonomy"  # 軸A 歸因分類（domains 回退來源）

# constants/＝固定共用參照常數（enum / 代碼字典，非業務可調），按維度分子資料夾；前端以 @constants alias 同讀。
# 與 config/（業務可調）分家：constants 由工程師維護、變動低頻。可用 AIQ_CONSTANTS_DIR 覆蓋（Docker 掛載）。
CONSTANTS_DIR: Path = Path(os.getenv("AIQ_CONSTANTS_DIR") or (REPO_ROOT / "constants")).resolve()
LABELS_DIR: Path = CONSTANTS_DIR / "labels"   # 代碼→文案字典（lang / traveller_type，源自 kkday-member-ci）
