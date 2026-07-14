# kkday-ai-quality（AI 法官）專案規則

> 本檔為**常駐核心**（目標 < 80 行）。檔案類型專屬的細則走 `.claude/rules/*.md` 條件載入（見末尾索引），僅在編輯對應檔案時注入，不佔平時 context。

## 開發核心原則（最高優先 · 動手前必過）

**1. 成熟現成方案優先於自研，不重新發明輪子。** 需求動手前先找已驗證的現成解，查順序：① codebase 既有 composable / util / component → ② 專案已裝套件（Arco / VueUse / lodash-es / ECharts）→ ③ 框架 / 語言原生 API → ④ 網上成熟開源套件（先 web search 評估採用度 / 維護 / bundle 體積 / 授權；技術棧鐵律已指定者不另尋）→ ⑤ 才自研。既有 ~80% 符合則**擴充它**。非平凡功能（JSON diff / 虛擬列表 / 日期 / 表單驗證 / 快照）**禁從零手刻**。新增依賴前一句話說明「為何既有不夠 + 為何選此套件」；自研前一句話說明「為何無現成方案可用」。

**2. 結構清晰，按職責拆分，單檔不過載。** 純函式 → `utils/`（無副作用、可測）｜響應式邏輯 → `composables/`｜共享狀態 → Pinia `store`｜可複用 UI → `components/`（元件薄）｜公共常數 → `constants/` 或前後端共用 `config/`｜型別 → `packages/types`。函式 > 50 行或元件塞多職責 → 拆分。資料夾有 barrel `index.ts`：對外從根 import，內部用相對路徑。

**3. 簡單優先，不過早抽象。** 相同邏輯出現第 3 次才抽（Rule of Three），勿為假設性未來預先抽象。「找成熟輪子」適用於非平凡功能（複雜元件 / 演算法 / 標準解已存在者）；一兩行能解的小工具直接寫純函式，不硬找套件。

**4. 退役即徹底，不留痕跡。** 移除舊功能一律走**全棧清退**：代碼邏輯 / DB 欄（含 migration）/ 前端 / 配置 / 文件 / 註釋 / 測試各層一併清乾淨——不留 tombstone 註釋、不留「歷史相容」死欄、不留死 config key、不留 orphan import。後面若再需要，直接在對應層重新補到位，不預留半殘骨架。完整盤點 → 清退 → 驗證清單見 `.claude/rules/feature-retirement.md`（編輯 migration / tables.py 時自動載入；純代碼退役亦須主動遵循）。

## 技術棧鐵律（強制，禁止偏離）

前端一律用既有輪子，**禁止**自行引入功能重疊的第三方套件：

| 用途 | 指定方案 |
|---|---|
| UI 元件庫 | **Arco Design Vue**（`@arco-design/web-vue`，**非 React 版**）|
| 圖表 | **ECharts**（`echarts` + `vue-echarts`）|
| 狀態管理 / 路由 | Pinia / vue-router |
| 樣式 | **Tailwind utility-first**（Vite 既有設定，`preflight: false`）|
| 響應式工具 / composable | **VueUse**（`@vueuse/core`）|
| 純函式工具 | **lodash-es**（named import，tree-shakeable）|

> Arco 查 API、找範例一律以 **Vue 文件**（arco.design/vue）為準，禁照搬 `arco.design/react/*` 寫法。

## 環境鐵律（全容器化，強制）

**所有執行期依賴一律放進 Docker，禁止裝在本機。** 本機唯二前提：Homebrew + 容器引擎（start.sh 自動裝）。

| 新增什麼 | 放哪 |
|---|---|
| Python 套件 | `backend/requirements*.txt` + `backend/Dockerfile`（rebuild 生效）|
| npm 套件 | `frontend/package.json` + lockfile → `frontend/Dockerfile.dev`（node_modules 走容器內匿名 volume，**不用 host 的**）|
| 系統工具 / CLI（如 pg_dump、ffmpeg）| 對應 Dockerfile 的 apt/apk 層 |
| 新服務（cache / queue / 搜尋引擎…）| `docker-compose.dev.yml` 加 service（prod 版同步）|

- 禁止在腳本 / 文件 / 排錯指引中要求 host 安裝（`brew install X`、host 跑 `pip install` / `pnpm add` 後直接起服務）；套件管理命令只為更新 manifest / lockfile，執行環境一律容器
- 改依賴後 `docker compose -f docker-compose.dev.yml up -d --build`；前端依賴變更容器內會自動重建 node_modules（`CI=true` 已配）
- 驗證 / 排錯優先進容器（`compose exec`）或看容器 log，勿以 host 環境行為下結論

## 配置化 SSOT（核心 · 完整規範見 rule）

業務會調的值、跨環境會變的值、前後端共用的值，**一律不准寫死代碼**；同一語義只准一份真相源。速查去處：機密 → `backend/.env`；前後端共用非機密 → `config/global/*.json`；判決領域 → `config/ai_judge/*.json`；純前端 UI → feature `constants/`。**必要機密 / token / env var 一律接入 `./start.sh`（dev / prod）一鍵配置到位**：可自動生成者由 start.sh 冪等引導（`_ensure_secret`），外部核發者設計為可空啟動的選填 fallback——啟動不得要求手動預配置。完整決策樹 + 例外 + 自問清單見 `.claude/rules/config-and-hardcode.md`（編輯 config / 後端 / constants / start.sh / compose 時自動載入）。

## 專案結構

- monorepo：`frontend/apps/console`（主控台）、`frontend/packages/*`（共用 types）
- 路由入口：`frontend/apps/console/src/router/index.ts`｜全域殼層（header / tabs / 設定抽屜）：`frontend/apps/console/src/App.vue`
- 一鍵啟動：`./start.sh`（dev·純 Docker：偵測+啟動 Docker → `docker compose -f docker-compose.dev.yml up`，容器內起 PG + 後端:8100 + 前端:5273，hot reload）｜`./start.sh prod`（生產零配置：首次自動生成機密寫入 repo 根 `.env`（冪等）→ `up -d --build`，前端 :8080）；停止 `./stop.sh` / `./stop.sh prod`

## 條件載入規則（`.claude/rules/`，編輯對應檔案時自動注入）

| 規則檔 | 觸發路徑（glob）| 內容 |
|---|---|---|
| `frontend-vue.md` | `**/*.vue` `**/*.css` `**/*.scss` | 樣式鐵律（Tailwind 優先級）+ Arco 元件復用 + 表格全局公共元件（TableLayout/TABLE_DEFAULTS/分頁）+ 懶加載/code-splitting |
| `typescript.md` | `**/*.ts` `**/*.tsx` `**/*.vue` | JSDoc 註釋 + 工具函式優先序 + barrel |
| `python.md` | `**/*.py` | Google-style docstring + 註釋密度 + 重庫 lazy import |
| `config-and-hardcode.md` | `config/**` `backend/app/**` `**/*.constant.ts` `**/constants/**` `start.sh` `docker-compose*.yml` | 禁硬編碼配置化完整規範 + start.sh 一鍵啟動引導鐵律 |
| `docs-sync.md` | `**/*.py` `**/*.ts` `**/*.vue` `config/**` `constants/**` | 改邏輯/結構/契約 → 同步更新所有相關文檔（寫前先核實 code）|
| `feature-retirement.md` | `**/alembic/versions/**` `**/tables.py` `**/migrations/**` | 功能退場機制：盤點消費點 → 全棧清退（代碼/DB/前端/config/文件/註釋/測試零殘留）→ 驗證清單（呼應核心原則 4）|

> 冷啟動問「有什麼規則」時，只答本檔常駐部分；rules/ 條件規則不列為常駐。
