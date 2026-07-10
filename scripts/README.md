# scripts/ — 開發腳本（按職責分層）

**開發腳本收攏於此，按職責分子夾**：`dev/`（日常工作流：doctor/format/lint/test/seed + dump-seed/fetch-seed）、
`audit/`（分析審計：accuracy_audit/rule_audit）、`tools/`（產生器/批次：gen_taxonomy_xlsx/prejudge_reviews/translate_summaries/boundary_ab_eval+report/multi_model_eval+report/encrypt_user_secrets/dump_datapack）、
`refeed/`（rule 反哺飛輪：rule_refeed）。
**例外：`start.sh` / `stop.sh` 放 repo 根**（與 docker-compose 同級·onboarding 入口，clone 後一眼可見免翻子夾）。
新腳本依職責放對應子夾；要跑什麼先看本表。與 backend 套件耦合的腳本（需 venv + import `app.*`）實體仍在
`backend/`（`run.sh` / `seed_mock.py` / `smoke_test.py`），這裡用**薄 wrapper** 委派，使「怎麼做 X」永遠在一處可查。

| 腳本 | 用途 | 等價手動指令 |
|---|---|---|
| `./start.sh`（repo 根） | 一鍵啟動（**純 Docker**）：偵測+啟動 Docker → 全服務背景起（PG+後端+前端，hot reload）→ 等就緒 → **自動開前端網頁**（Swagger 只印 URL）；停止用 `./stop.sh` | `fetch-seed`（選）＋ `docker compose -f docker-compose.dev.yml up -d` |
| `./stop.sh`（repo 根） | 停止所有服務（**只停止·資料一律保留**；清庫刻意不提供，須顯式 `down -v`） | `docker compose -f docker-compose.dev.yml down` |
| `./scripts/dev/dump-seed.sh` | 產全庫 seed（pg_dump plain+gzip → `docker/seed/seed.sql.gz`；`--sha` 印 checksum） | `pg_dump --clean --if-exists -Fp kkdb_ai_quality \| gzip` |
| `./scripts/dev/fetch-seed.sh` | 取得 seed（`SEED_URL` 下載/本地/LFS/`--sample`）；`--restore-if-empty` 空庫時還原 | `gunzip -c docker/seed/seed.sql.gz \| psql kkdb_ai_quality` |
| `./scripts/tools/dump_datapack.py` | 導出全庫**資料包 zip**（ndjson+manifest，供前台安全匯入；`--include-sensitive`/`--tables`/`--out`） | `cd backend && .venv/bin/python ../scripts/tools/dump_datapack.py` |
| `./scripts/dev/seed.sh` | 重置 mock 判決資料（20 筆全場景） | `cd backend && .venv/bin/python seed_mock.py` |
| `./scripts/dev/test.sh` | 後端 smoke test（零 key stub） | `cd backend && ./run.sh test` |
| `./scripts/dev/lint.sh` | Lint 前後端（ruff + eslint） | `cd backend && .venv/bin/ruff check .` ＋ `cd frontend && pnpm lint` |
| `./scripts/dev/format.sh` | 格式化前後端（Prettier + ruff format，鏈式/長行自動換行） | `cd frontend && pnpm format` ＋ `cd backend && .venv/bin/ruff format .` |
| `./scripts/tools/prejudge_reviews.sh` | 批量預判歸因（product_reviews → L3 + 信心度，config/ai_judge 驅動） | `cd backend && .venv/bin/python prejudge_reviews.py` |
| `./scripts/tools/translate_summaries.py` | 一鍵批量轉譯既有判決摘要為繁中（DB 直接改；只轉非中文為主者·需 active LLM·stub 拒跑；`--dry-run`/`--limit N` 試跑） | `cd backend && .venv/bin/python ../scripts/tools/translate_summaries.py` |
| `./scripts/tools/boundary_ab_eval.py` | 判準界線 A/B 離線評測（唯讀不寫 judgments）：`--build` 抽評測集／`--mode flat\|cascade` 重判（cascade 為本 process monkey-patch，不動線上）；`--stage-a-model` 覆寫 Stage A 模型 | `cd backend && .venv/bin/python ../scripts/tools/boundary_ab_eval.py --help` |
| `./scripts/tools/boundary_ab_report.py` | A/B 報告：before/flat/cascade 對 silver label 算 content 誤判率、primary 準確率（accuracy.analyze_supervised）與混淆對 | `cd backend && .venv/bin/python ../scripts/tools/boundary_ab_report.py --help` |
| `./scripts/tools/multi_model_eval.py` | 多模型準確度評測（唯讀不寫 judgments）：`--build-set` 建評測集（有外部 free_tag 且已判的 product_reviews）／`--run --config-id <id>` 以指定 LLM 配置逐則 `to_findings` 收集 sentiment/polarity/歸因 | `cd backend && .venv/bin/python ../scripts/tools/multi_model_eval.py --help` |
| `./scripts/tools/multi_model_report.py` | 多模型準確度報告：對外部 free_tag「問題面向(tag_value≤門檻)」映射到當前 L1/L2 為 ground truth，算情緒/ L1-L2 精確率/free_tag 召回，出 xlsx（統計+明細+長條圖）；復用 build_comparison_report 計分原語 | `cd backend && .venv/bin/python ../scripts/tools/multi_model_report.py --help` |
| `./scripts/tools/mock_testset_gen.py` | V1 Prompt 穩定性驗證：零 LLM 確定性生成 Mock 測試集（六域各正/負 100 則，取材自 config/ai_judge 既有正反例＋跨域『看似X實為Y』hint，含同義詞/語氣/句式重組/跨類干擾/異常輸入變體；`--seed` 保證可重現） | `cd backend && .venv/bin/python ../scripts/tools/mock_testset_gen.py --out ../tmp/mock_testset/testset_v1.jsonl` |
| `./scripts/tools/prompt_stability_eval.py` | V1 Prompt 穩定性驗證：對 mock 測試集重複判決 N 次（唯讀，同 multi_model_eval 配方），算各域 P/R/F1＋重複一致性（完全一致率/pairwise agreement）＋variant_type 分組準確率＋混淆對 Top10；`--dry-run` 免呼叫 LLM 驗證管線 | `cd backend && .venv/bin/python ../scripts/tools/prompt_stability_eval.py --testset ../tmp/mock_testset/testset_v1.jsonl --user you@kkday.com --config-id <id>` |
| `./scripts/tools/free_tag_coverage.py` | free tag 匯總表 → free_tag_mapping.json 加權覆蓋率驗收（顯式命中＋子字串兜底＋顯式無對應三口徑；驗收線 ≥95%） | `python3 scripts/tools/free_tag_coverage.py --csv "~/Downloads/free tag 匯總表.csv"` |
| `./scripts/tools/encrypt_user_secrets.py` | user_settings 機密 at-rest 加密遷移（明文→Fernet 密文；冪等；`--dry-run` 試跑、`--decrypt` 回滾）；需 backend/.env 已設 `AIQ_SECRET_KEY` | `cd backend && .venv/bin/python ../scripts/tools/encrypt_user_secrets.py` |
| `./scripts/refeed/rule_refeed.sh` | rule 反哺飛輪：印 ensemble 判錯的邊界候選（content↔supplier 優先）／`--apply <RULE> <NODE> "<canon>"` 精煉某 node canon 寫回 DB active 版並熱重載 | `cd backend && .venv/bin/python ../scripts/refeed/rule_refeed.py` |

## 統一格式化 / Lint 規則（ready-made，零調校）

| 範疇 | 工具 | 規則來源（現成預設，不用自己配） |
|---|---|---|
| 前端格式 | **Prettier** | `.prettierrc.json`（printWidth 100·單引號·trailing comma） |
| 前端 lint | **ESLint flat** | `frontend/eslint.config.js`＝JS推薦＋TS推薦＋Vue3推薦＋eslint-config-prettier（關格式衝突） |
| 後端格式＋lint | **ruff** | `backend/pyproject.toml [tool.ruff]`＝Black 風格 format＋E/F/I/B/UP（E501/B008 已關：formatter 管行長、FastAPI Depends 是正確寫法） |
| 型別 | **vue-tsc** | tsconfig strict |
| 跨編輯器 | **.editorconfig** | UTF-8/LF/縮排（py=4 其餘=2） |

**一次性前置**（裝工具）：`cd frontend && pnpm install`（Prettier/ESLint）＋ `cd backend && .venv/bin/pip install -e ".[dev]"`（ruff）。裝完 `./scripts/dev/format.sh`／`./scripts/dev/lint.sh` 即可用。

埠：後端 8100（Swagger `/docs`）｜前端 5273。需 `pnpm`（`brew install pnpm`）；首次自動建 venv + 裝依賴。

> 為何不全搬進 scripts/：`backend/run.sh` 用 `cd "$(dirname "$0")"` 假定自己在 `backend/`，`seed_mock.py`/`smoke_test.py` 需 backend venv 與 `app.*` import → 搬出會破壞路徑/import。原則：**腳本跟著它跑的東西放，跨域編排才進 scripts/**。
