---
paths:
  - "**/*.ts"
  - "**/*.tsx"
  - "**/*.vue"
---

# TypeScript / JS 規則（編輯 .ts / .tsx / .vue 時載入）

## 註釋規範（JSDoc · 強制）

公開函式 / composable / util、複雜邏輯、公開 `interface` / `type`、回傳語義不明者**必加** JSDoc：

- 格式：一句話用途（繁中）+ `@param`（每個）/ `@returns` / `@throws {ErrorType}`（會拋錯時）/ `@example`
- TS 型別已宣告時**省略**型別括號 `{Type}`；自說明的 getter / 單行 arrow 可省
- 說明「為什麼 / 何時用 / 非顯而易見的決策」，而非複述代碼字面
- 禁止用註釋掩蓋壞代碼（該重構就重構）；TODO / FIXME 須附原因或追蹤票號；中文註釋，技術術語 / API 名保留英文

## 工具函式優先序（不造輪子）

寫任何 helper / 響應式邏輯前，按此順序找現成方案：

1. **codebase 既有** composable / util（`features/*/utils`、`features/*/composables`）
2. **VueUse**（`@vueuse/core`）：`useDebounceFn` / `useLocalStorage` / `useElementSize` / `useEventListener` / `watchDebounced` … 凡響應式 / 生命週期 / DOM 互動，先查 VueUse，**不要**自寫 `addEventListener` + `onUnmounted`、自寫 debounce
3. **lodash-es**：`debounce` / `cloneDeep` / `groupBy` / `uniqBy` / `orderBy` … 純資料轉換用 named import（`import { groupBy } from 'lodash-es'`，禁 `import _ from 'lodash'` 全量引入）
4. **才考慮**自寫

> 響應式回呼用 `useDebounceFn`（自動解包 + 自動清理）；純函式用 lodash-es `debounce`。

## Barrel exports

資料夾有 barrel `index.ts`：對外從資料夾根 import，內部 cross-import 用相對路徑。
