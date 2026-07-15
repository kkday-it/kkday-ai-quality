# scripts/ — 開發腳本（按職責分層）

**開發腳本收攏於此，按職責分子夾**：`dev/`（日常工作流：doctor/format/lint/test/seed + dump-seed/fetch-seed）、
`ops/`（生產維運：backup-db/restore-db）、`audit/`（分析審計：accuracy_audit）、`tools/`（產生器/批次：prejudge_reviews/translate_summaries/multi_model_eval/persist_multimodel_history/taxonomy_health/eval_prompt_single/encrypt_user_secrets/dump_datapack）。
**例外：`start.sh` / `stop.sh` 放 repo 根**（與 docker-compose 同級·onboarding 入口，clone 後一眼可見免翻子夾）。
新腳本依職責放對應子夾；要跑什麼先看本表。與 backend 套件耦合的腳本（需 venv + import `app.*`）實體仍在
`backend/`（`run.sh` / `seed_mock.py` / `smoke_test.py`），這裡用**薄 wrapper** 委派，使「怎麼做 X」永遠在一處可查。

| 腳本 | 用途 | 等價手動指令 |
|---|---|---|
| `./start.sh`（repo 根） | 一鍵啟動（**純 Docker**）：偵測+啟動 Docker → 全服務背景起（PG+後端+前端，hot reload）→ 等就緒 → **自動開前端網頁**（Swagger 只印 URL）；停止用 `./stop.sh` | `fetch-seed`（選）＋ `docker compose -f docker-compose.dev.yml up -d` |
| `./stop.sh`（repo 根） | 停止所有服務（**只停止·資料一律保留**；清庫刻意不提供，須顯式 `down -v`） | `docker compose -f docker-compose.dev.yml down` |
| `./scripts/dev/dump-seed.sh` | 產全庫 seed（pg_dump plain+gzip → `docker/seed/seed.sql.gz`；`--sha` 印 checksum） | `pg_dump --clean --if-exists -Fp kkdb_ai_quality \| gzip` |
| `./scripts/ops/backup-db.sh` | 生產庫備份（容器化 pg_dump+gzip → `backups/db/`；`--keep N` 保留份數預設 7；crontab 排程範例見檔頭） | `docker compose exec -T db pg_dump --clean ... \| gzip` |
| `./scripts/ops/restore-db.sh <file>` | 還原備份（**破壞性**·type-to-confirm；還原後 restart backend 自動補 migration） | `gunzip -c file \| docker compose exec -T db psql` |
| `./scripts/dev/fetch-seed.sh` | 取得 seed（`SEED_URL` 下載/本地/LFS/`--sample`）；`--restore-if-empty` 空庫時還原 | `gunzip -c docker/seed/seed.sql.gz \| psql kkdb_ai_quality` |
| `./scripts/tools/dump_datapack.py` | 導出全庫**資料包 zip**（ndjson+manifest，供前台安全匯入；`--include-sensitive`/`--tables`/`--out`） | `cd backend && .venv/bin/python ../scripts/tools/dump_datapack.py` |
| `./scripts/dev/seed.sh` | 重置 mock 判決資料（20 筆全場景） | `cd backend && .venv/bin/python seed_mock.py` |
| `./scripts/dev/test.sh` | 後端 smoke test（零 key stub） | `cd backend && ./run.sh test` |
| `./scripts/dev/lint.sh` | Lint 前後端（ruff + eslint） | `cd backend && .venv/bin/ruff check .` ＋ `cd frontend && pnpm lint` |
| `./scripts/dev/format.sh` | 格式化前後端（Prettier + ruff format，鏈式/長行自動換行） | `cd frontend && pnpm format` ＋ `cd backend && .venv/bin/ruff format .` |
| `./scripts/tools/prejudge_reviews.sh` | 批量預判歸因（product_reviews → L3 + 信心度，config/ai_judge 驅動） | `cd backend && .venv/bin/python prejudge_reviews.py` |
| `./scripts/tools/translate_summaries.py` | 一鍵批量轉譯既有判決摘要為繁中（DB 直接改；只轉非中文為主者·需 active LLM·stub 拒跑；`--dry-run`/`--limit N` 試跑） | `cd backend && .venv/bin/python ../scripts/tools/translate_summaries.py` |
| `./scripts/tools/multi_model_eval.py` | 多模型準確度評測（唯讀不寫 judgments）：`--build-set` 建評測集（有外部 free_tag 且已判的 product_reviews）／`--run --config-id <id>` 以指定 LLM 配置逐則 `to_findings` 收集 sentiment/polarity/歸因 | `cd backend && .venv/bin/python ../scripts/tools/multi_model_eval.py --help` |
| `./scripts/tools/persist_multimodel_history.py` | 把多模型評測結果（`tmp/multi_model/*_v4.json`）灌進 `judgment_history`（kind='judgment' 每評論每模型一筆快照，**只寫歷史不碰 judgments 活表**→列表恆 gpt canonical）：供判決歷史 modal 看各模型、導出 `compare_models` 並排對比；去重天然（model+params+digest）、label 由 BD/Gemini 自帶＋ai_judge fallback 補 | `docker exec kkday-ai-quality-backend python /app/scripts/tools/persist_multimodel_history.py --dry-run` |
| `./scripts/tools/prompt_stability_eval.py` | V1 Prompt 穩定性驗證：對 mock 測試集重複判決 N 次（唯讀，同 multi_model_eval 配方），算各域 P/R/F1＋重複一致性（完全一致率/pairwise agreement）＋variant_type 分組準確率＋混淆對 Top10；`--dry-run` 免呼叫 LLM 驗證管線。⚠️測試集生成器 `mock_testset_gen.py`（取材自已退役的 `rule_C-*.json` 正反例）已隨 JSON 樹於 2026-07-13 一併退役；既有 `tmp/mock_testset/testset_v1.jsonl` 仍可續用（B3 `prompt_testcases` 邊界測試集機制已於 2026-07-14 退役） | `cd backend && .venv/bin/python ../scripts/tools/prompt_stability_eval.py --testset ../tmp/mock_testset/testset_v1.jsonl --user you@kkday.com --config-id <id>` |
| `scripts/prompt_lab/`（模組）| **C1–C6 單域 Prompt Mock 評測實驗室**（隔離·OpenAI Responses/Gemini API·**不碰生產 prejudge/DB/前端**）：`build_*_plans`/`build_manifest` → `generate_cases` → `audit_cases` → `build_dataset` → `evaluate_prompt`（+`metrics`/`report`）→ `compare_runs`，即「生成→獨立審核→人工複核→凍結 Dev/Holdout→跑 baseline→逐條除錯→換候選 Prompt→diff」。另附 `run_c1_debug_workbook.py`：對**人工除錯工作簿**（`C1_判官除錯資料表.xlsx` 三 Sheet）用指定模型跑 C-1 判官，把裁決寫回新欄 `AI審核器·建議_V1_RESULT`（+`AI判官·面向/信心/證據_V1` companion），支援 `--per-sheet-limit`／resume／OpenAI+Gemini provider 路由。用獨立 venv `.venv-promptlab`。完整說明見 [`evals/prompt_lab/README.md`](../evals/prompt_lab/README.md) | `.venv-promptlab/bin/python scripts/prompt_lab/evaluate_prompt.py --help`；測試 `.venv-promptlab/bin/python -m pytest backend/tests/prompt_lab` |
| `./scripts/tools/taxonomy_health.py` | **歸因分類體系健檢**（多模型交叉診斷·零 LLM 成本）：不依賴人工真值，用 judgment_history 現成多模型判決快照的一致率/分歧結構反推分類體系品質——L1/L2 成對集合 F1＋完全一致率 vs 隨機基準（合理性）、純 L1 邊界爭議混淆對（邊界模糊）、L2 使用分佈（粒度失衡）、層級效度（各域 L2 清晰度）；產 markdown 報告，改規則後重跑對比。⚠️封閉式侷限：能診斷「現有類好不好用」，「缺什麼類」需開放式歸因另探 | `docker exec kkday-ai-quality-backend python /app/scripts/tools/taxonomy_health.py --out /tmp/taxonomy_health.md`（容器需先 `docker cp` 腳本，scripts/ 非掛載）|
| `./scripts/tools/eval_prompt_single.py` | **單支 Prompt 評測 harness**（調適閉環驗證端，Prompt-as-Source）：對 7 支 `prompts/*.md` 逐支獨立驗證——prompt 由 `prompt_source.load()` 即時解析（DB active 優先→檔案 fallback，天然不過期），對 N 則已判評論（md5 穩定排序＝跨 run 可比）與 production judgments 比對。域 prompt 指標：primary 一致率/棄權正確率/命中率/多報率；polarity：極性＋sentiment 一致率。token 走 user_settings（stub 拒跑）、關 exact-cache 讀。調適閉環：規則配置頁「初判 Prompt」編輯對應域 md → `--prompt C-3 --n 20` 驗證 → 達標後存檔 | `docker exec kkday-ai-quality-backend python /app/scripts/tools/eval_prompt_single.py --prompt C-3 --n 20 --user alvin.bian@kkday.com --out /app/tmp/eval_C-3.json` |
| `./scripts/tools/gen_taxonomy_doc.py` | **類別定義文檔生成器**（C1 交付物，單向 prompts→文檔）：從 7 支 `prompts/*.md` 解析六域定義/邊界（✅屬本域／❌常見誤判／⛔明確禁止）/L2 面向表，生成 `docs/類別定義_V0.1.md`；純讀 prompt_source + domains.json，免 LLM 呼叫 | `docker exec kkday-ai-quality-backend python /app/scripts/tools/gen_taxonomy_doc.py --out /app/docs/類別定義_V0.1.md` |
| `./scripts/tools/encrypt_user_secrets.py` | user_settings 機密 at-rest 加密遷移（明文→Fernet 密文；冪等；`--dry-run` 試跑、`--decrypt` 回滾）；需 backend/.env 已設 `AIQ_SECRET_KEY` | `cd backend && .venv/bin/python ../scripts/tools/encrypt_user_secrets.py` |

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
