# kkday-ai-product-quality（AI 法官）專案規則

## 開發核心原則（最高優先 · 動手前必過）

**1. 成熟現成方案優先於自研，不重新發明輪子。** 任何需求動手前，先找「已被驗證的現成解」——既有 codebase、已裝套件（Arco / VueUse / lodash-es / ECharts），以及**網上成熟開源方案**（社群廣泛使用、活躍維護、star/下載量高）。既有 ~80% 符合 → 擴充它；非平凡功能（如 JSON diff 編輯器、虛擬列表、日期處理、表單驗證、快照/版本控制）→ **優先選成熟 library，禁從零手刻**。細則見下方「工具函式優先序」「復用優先」+ 全域 `rules/reuse-and-decoupling.md`。

**2. 開發前先盤點現成方案，禁閉門造車。** 動手第一步是「這個需求有沒有現成的成熟解？」——查順序：
1. codebase 既有 composable / util / component
2. 專案已裝套件
3. 框架 / 語言原生 API
4. **網上成熟開源套件**（先 web search 評估：社群採用度、維護活躍度、bundle 體積、授權；技術棧鐵律已指定者不另尋）
5. 上述都無 → 才自研

新增依賴前一句話說明「為何既有不夠 + 為何選此套件」；自研前一句話說明「為何沒有現成方案可用」。**寧可花時間找對的輪子，不要急著手刻一個半成品。**

**3. 結構清晰，按職責拆分，單檔不過載。** 不把多種邏輯堆一個檔：
- **公共純函式** → `utils/`（無副作用、可測）｜**響應式邏輯** → `composables/`（hook）｜**共享狀態** → `store`（Pinia）
- **可複用 UI** → `components/`（元件薄，只管渲染 + 互動，業務邏輯下沉 composable）
- **公共配置 / 常數** → `constants/` 或前後端共用 `config/defaults.json`（單一真相源，禁前後端各寫一份）｜**型別** → `packages/types`
- **大檔拆子檔**：一個 `.vue` / `.ts` 只擔一個職責；script 邏輯偏多 → 抽 composable / util；函式 > 50 行或元件塞多職責 → 拆分（呼應 `rules/reuse-and-decoupling.md`）
- **資料夾有 barrel `index.ts`**：對外從資料夾根 import，內部 cross-import 用相對路徑

> 三原則衝突時以「簡單優先 + 不過早抽象」收斂：相同邏輯出現第 3 次才抽（Rule of Three），勿為假設性未來預先抽象。**「找成熟輪子」適用於非平凡功能（複雜元件 / 演算法 / 標準解已存在者）；一兩行能解的小工具，直接寫純函式即可，不為它硬找套件。**

## 註釋規範（強制）

代碼盡量補上**完整註釋**：說明「為什麼 / 何時用 / 非顯而易見的決策」，而非複述代碼字面。

- **TS / JS** → **JSDoc 形式**：公開函式 / composable / util、複雜邏輯、公開 `interface` / `type`、回傳語義不明者**必加**。格式：一句話用途（繁中）+ `@param`（每個）/ `@returns` / `@throws {ErrorType}`（會拋錯時）/ `@example`；TS 型別已宣告時省略型別括號 `{Type}`。自說明的 getter / 單行 arrow 可省。細則見全域 `rules/typescript.md`。
- **Python** → **Google-style docstring**：module / 公開 function / class **必加** `"""..."""`——一句話用途 + `Args:` / `Returns:` / `Raises:`（會拋時）。型別走 type hint，docstring 不重複型別、專注語義與動機。複雜邏輯行內 `#` 註解說明「為何這樣做」。
- **共通**：禁止用註釋掩蓋壞代碼（該重構就重構）；TODO / FIXME 須附原因或追蹤票號；中文註釋，技術術語 / API 名保留英文。

> 本專案既有代碼註釋密度高（如 `client.py` ping / `pipeline.py` 各閘門皆有「為何」註解）——新增 / 修改代碼比照既有密度，勿降低。

## 技術棧鐵律（強制，禁止偏離）

前端一律使用以下既有輪子，**禁止**自行引入功能重疊的第三方套件：

| 用途 | 指定方案 | 文件 |
|---|---|---|
| UI 元件庫 | **Arco Design Vue**（`@arco-design/web-vue`） | https://arco.design/vue/docs/start |
| 圖表 | **ECharts**（`echarts` + `vue-echarts`） | https://echarts.apache.org/ |
| 狀態管理 | Pinia | — |
| 路由 | vue-router | — |
| 樣式 | **Tailwind utility-first**（Vite 既有設定，`preflight: false`） | https://tailwindcss.com/docs/installation/using-vite |
| 響應式工具 / composable | **VueUse**（`@vueuse/core`） | https://vueuse.org/ |
| 純函式工具 | **lodash-es**（named import，tree-shakeable） | https://lodash.com/docs |

注意：Arco 採 **Vue 版**（`@arco-design/web-vue`），非 React 版。查 API、找元件範例一律以 Vue 文件為準，禁止照搬 `arco.design/react/*` 的寫法。

## 樣式鐵律（Tailwind utility-first）

**禁止手寫 scoped CSS class** 來表達可用 utility 完成的樣式（間距 / 排版 / flex / 顏色 / 字級）。優先級：

1. **Tailwind utility class**（`flex`、`gap-2`、`pt-5`、`text-sm`…）直接寫在模板元素上 — 預設首選
2. **Arco 元件的 style prop**：要調 Arco 內部結構（header / body）時，用元件 prop（如 `:header-style` / `:body-style` / `:wrapper-style`），**不要** `:deep()` 改內部 class
3. **`:deep()` + scoped CSS**：僅限 utility 與 prop 都無法觸及的情境（複雜選擇器、偽元素、第三方深層 DOM），且須註解說明為何 utility 不可行
4. **`style.css` 全域**：僅放 design token / reset / 跨頁共用基底，禁止塞頁面級樣式

> `preflight: false`（已關 Tailwind reset，避免破壞 Arco）。新增 utility 直接用，無需額外設定。

## 工具函式優先序（不造輪子）

寫任何 helper / 響應式邏輯前，按此順序找現成方案（呼應全域 `rules/reuse-and-decoupling.md`）：

1. **codebase 既有** composable / util（`features/*/utils`、`features/*/composables`）
2. **VueUse**（`@vueuse/core`）：`useDebounceFn` / `useLocalStorage` / `useElementSize` / `useEventListener` / `watchDebounced` … 凡響應式 / 生命週期 / DOM 互動，先查 VueUse 有無對應，**不要**自寫 `addEventListener` + `onUnmounted`、自寫 debounce
3. **lodash-es**：`debounce` / `cloneDeep` / `groupBy` / `uniqBy` / `orderBy` … 純資料轉換用 named import（`import { groupBy } from 'lodash-es'`，禁 `import _ from 'lodash'` 全量引入）
4. **才考慮**自寫

> 響應式情境優先 VueUse（自動解包 + 自動清理）；純資料轉換用 lodash-es。兩者重疊時（如 debounce），響應式回呼用 `useDebounceFn`，純函式用 lodash-es `debounce`。

## 復用優先

新增任何共用邏輯 / UI 前，先搜尋 codebase 既有 composable / util / component / store，找到即擴充，不平行造第二套（細則見全域 `rules/reuse-and-decoupling.md`）。

- 需要彈窗 / 抽屜 → 用 `a-modal` / `a-drawer`，不自寫
- 需要表單 / 表格 / 提示 → 用 Arco 對應元件（`a-form` / `a-table` / `Message`）
- 需要圖表 → 用 `vue-echarts` 的 `<v-chart>`，不引入其他圖表庫

## 專案結構

- monorepo：`frontend/apps/console`（主控台）、`frontend/packages/*`（共用 types 等）
- 路由入口：`frontend/apps/console/src/router/index.ts`
- 全域殼層（header / tabs / 設定抽屜）：`frontend/apps/console/src/App.vue`
