# scripts/ — 開發腳本單一入口

**本資料夾是所有開發腳本的唯一入口**：新腳本一律放這裡；要跑什麼先看本表。
與 backend 套件耦合的腳本（需 venv + import `app.*`）實體仍在 `backend/`（`run.sh` / `seed_mock.py` / `smoke_test.py`），這裡用**薄 wrapper** 委派，使「怎麼做 X」永遠在一處可查。

| 腳本 | 用途 | 等價手動指令 |
|---|---|---|
| `./scripts/dev.sh` | 一鍵起前後端（Ctrl-C 一起停） | `backend/run.sh` ＋ `cd frontend && pnpm dev` |
| `./scripts/seed.sh` | 重置 mock 判決資料（20 筆全場景） | `cd backend && .venv/bin/python seed_mock.py` |
| `./scripts/test.sh` | 後端 smoke test（零 key stub） | `cd backend && ./run.sh test` |
| `./scripts/lint.sh` | Lint 前後端（ruff + eslint） | `cd backend && .venv/bin/ruff check .` ＋ `cd frontend && pnpm lint` |
| `./scripts/format.sh` | 格式化前後端（Prettier + ruff format，鏈式/長行自動換行） | `cd frontend && pnpm format` ＋ `cd backend && .venv/bin/ruff format .` |
| `./scripts/prejudge_reviews.sh` | 批量預判歸因（product_reviews → L3 + 信心度，config/ai_judge 驅動） | `cd backend && .venv/bin/python prejudge_reviews.py` |

## 統一格式化 / Lint 規則（ready-made，零調校）

| 範疇 | 工具 | 規則來源（現成預設，不用自己配） |
|---|---|---|
| 前端格式 | **Prettier** | `.prettierrc.json`（printWidth 100·單引號·trailing comma） |
| 前端 lint | **ESLint flat** | `frontend/eslint.config.js`＝JS推薦＋TS推薦＋Vue3推薦＋eslint-config-prettier（關格式衝突） |
| 後端格式＋lint | **ruff** | `backend/pyproject.toml [tool.ruff]`＝Black 風格 format＋E/F/I/B/UP（E501/B008 已關：formatter 管行長、FastAPI Depends 是正確寫法） |
| 型別 | **vue-tsc** | tsconfig strict |
| 跨編輯器 | **.editorconfig** | UTF-8/LF/縮排（py=4 其餘=2） |

**一次性前置**（裝工具）：`cd frontend && pnpm install`（Prettier/ESLint）＋ `cd backend && .venv/bin/pip install -e ".[dev]"`（ruff）。裝完 `./scripts/format.sh`／`./scripts/lint.sh` 即可用。

埠：後端 8100（Swagger `/docs`）｜前端 5273。需 `pnpm`（`brew install pnpm`）；首次自動建 venv + 裝依賴。

> 為何不全搬進 scripts/：`backend/run.sh` 用 `cd "$(dirname "$0")"` 假定自己在 `backend/`，`seed_mock.py`/`smoke_test.py` 需 backend venv 與 `app.*` import → 搬出會破壞路徑/import。原則：**腳本跟著它跑的東西放，跨域編排才進 scripts/**。
