---
paths:
  - "**/*.vue"
  - "**/*.css"
  - "**/*.scss"
---

# 前端 Vue / 樣式規則（編輯 .vue / .css / .scss 時載入）

## 樣式鐵律（Tailwind utility-first）

禁止手寫 scoped CSS class 來表達可用 utility 完成的樣式（間距 / 排版 / flex / 顏色 / 字級）。優先級：

1. **Tailwind utility class**（`flex`、`gap-2`、`pt-5`、`text-sm`…）直接寫在模板元素上 — 預設首選
2. **Arco 元件的 style prop**：要調 Arco 內部結構（header / body）時，用元件 prop（如 `:header-style` / `:body-style` / `:wrapper-style`），**不要** `:deep()` 改內部 class
3. **`:deep()` + scoped CSS**：僅限 utility 與 prop 都無法觸及的情境（複雜選擇器、偽元素、第三方深層 DOM），且須註解說明為何 utility 不可行
4. **`style.css` 全域**：僅放 design token / reset / 跨頁共用基底，禁止塞頁面級樣式

> `preflight: false`（已關 Tailwind reset，避免破壞 Arco）。新增 utility 直接用，無需額外設定。

## 多控制項橫列佈局（篩選列 / 工具列 · 強制用 Arco Grid）

篩選列 / 工具列等**會換行的多控制項橫排**（多個 `a-select` / `a-input` / 按鈕並排且可能超出一行），一律用 **Arco Grid（`a-row` / `a-col`）**，禁止手抄 `flex flex-wrap gap-x` 拼版：

- **根用 `<a-row :gutter="[橫, 縱]" align="center" wrap>`**：`gutter` 傳**陣列**同時給欄距與**換行行距**——這是關鍵。手寫 `flex flex-wrap gap-2` 的 `gap` 只作用於單一 flex 容器內部；一旦拆成**兩個相鄰 `<div>`**（如「篩選維度列」+「精確查詢列」），兩 div 之間**無縱向間距 → 視覺黏疊**。Grid 的 `gutter` 縱向值天然消除此問題。
- **每個控制項包一個 `<a-col :flex="...">`**：固定基寬用 `flex="160px"`，內容自適寬按鈕用 `flex="none"`，撐開占位（把右側計數/重置推到最右）用 `flex="auto"`（`flex` prop Arco 2.10.0+）。
- **控制項本身 `class="w-full"` 撐滿該欄**，寬度交給 `a-col`，**不再**在 `a-select` / `a-input` 上寫 `style="width: XXXpx"`。
- **多段橫列**（篩選維度 + 精確查詢分兩排）：拆成多個 `a-row`，段間距用 Tailwind `class="mb-2"`（Grid 不管跨 row 間距）。
- **條件渲染的欄位**：`v-if` / `v-for` 掛在 `a-col` 上（欄不存在時不佔 gutter 空位）。

> 範例見 `features/judge/pages/AttributionList.vue` 的 `#toolbar`。輕量單行、不會換行的少量控制項（2~3 個）可續用 `flex gap-2`，不強制 Grid。

## UI 元件復用（不自寫）

新增任何共用 UI 前，先搜尋 codebase 既有 component，找到即擴充，不平行造第二套。

- 需要彈窗 / 抽屜 → 用 `a-modal` / `a-drawer`，不自寫
- 需要表單 / 表格 / 提示 → 用 Arco 對應元件（`a-form` / `a-table` / `Message`）
- 需要圖表 → 用 `vue-echarts` 的 `<v-chart>`，不引入其他圖表庫
- **元件薄**：只管渲染 + 互動，業務邏輯下沉 composable / util；function > 50 行或元件塞多職責 → 拆分

## 表格（全局公共元件 · 強制）

任何列表表格（頁面 / 抽屜 / 彈窗內皆同）一律用全局公共元件 `TableLayout`（`@/components`），禁止各處手抄 flex 樣板 / TABLE_DEFAULTS / 散寫 pagination 物件：

- **內建表格模式（首選）**：傳 `data` 即啟用，內部渲染 a-table 並自動打底 `TABLE_DEFAULTS` + 滿高滾動 + 分頁 preset；columns / row-key / expandable / row-selection / 事件與 #columns / #expand-row / 自訂 cell slots 全透傳：
  ```vue
  <TableLayout title="…" :data="rows" :columns="COLS" :loading="loading" :error="error"
    server :total="total" v-model:page="page" v-model:page-size="pageSize" @change="load">
    <template #toolbar>…篩選列…</template>
    <template #review="{ record }">…</template>
  </TableLayout>
  ```
- **分頁**：`pagination` prop 傳 `'standard'`（預設）/ `'with-all'`（含「全部」，**僅限總量可控小表**，萬級大表禁用）/ `false` / 自訂物件；伺服器分頁加 `server`，元件自組 current/pageSize/total 與換頁 handlers（換 pageSize 自動回第 1 頁）
- **三態**：`loading`（a-table 內建 spin）/ `error`（表上方 alert 不遮資料）/ `emptyText`（內建 empty 文案）
- **高度前置**：頁面根 `h-full`（AppShell 已 flex 撐高）；抽屜 / 彈窗傳 `full-height`
- **純佈局模式**（不傳 `data`）：非表格內容（卡片列表等）走預設插槽自排；a-table 自帶 `class="min-h-0 flex-1"` + `:scroll="{ y: '100%' }"`
- **例外**：`pagination=false` 的輕量小表（設定面板 / 彈窗內對照表）可直接 a-table，不強制套卡片
- 常數 SSOT：`@/constants/table.constant`（`TABLE_DEFAULTS` / `ALL_PAGINATION` / `PAGINATION_WITH_ALL` / `PAGE_SIZE_ALL`）

## 按鈕與操作區（視覺區分主次 · 強制）

同一操作區（toolbar / 卡片動作列 / 彈窗 footer）並排多顆按鈕時，**禁止整排同色同樣式**（全 default 或全同型），須以 Arco `type` / `status` 依語義區分主次，讓使用者一眼分辨主行為與破壞性操作：

| 語義 | Arco 樣式 | 例 |
|---|---|---|
| **主行為**（該區唯一最重要、確認/提交） | `type="primary"` | 儲存、確認、送出 |
| **次要行為**（並列可選動作） | `type="outline"` | 導出、匯入、複製 |
| **破壞性/需謹慎**（重置、刪除、清空） | `type="outline" status="warning"`（刪除用 `status="danger"`） | 恢復默認、刪除 |
| **純檢視/輕量**（開彈窗看、切換） | `type="text"` | 歷史、詳情 |

- 主行為**每區至多一顆** primary；其餘不得搶佔主色。
- 有明確語義的動作**配對應 icon**（導出→`icon-download`、新增→`icon-plus`、刷新→`icon-refresh`），icon 從 `@arco-design/web-vue/es/icon` 具名 import。
- 破壞性操作除變色外，仍須二次確認（`a-modal` / `Modal.confirm`），顏色不替代確認。

## 懶加載 / Code-splitting（預設機制）

首屏不需要的一律延遲載入，縮小初始 bundle（呼應 06 quality-targets：單路由 JS < 200KB gzip）：

- **路由頁元件**：一律 `component: () => import('...')`（route-level splitting）；禁在 route 檔頂靜態 `import 頁面元件`
- **重型第三方庫**（jsoneditor / jspdf / html2canvas 等）：使用點動態載入——元件內 `await import('lib')`（掛載 / 觸發時），型別走 `import type`（編譯期擦除，不進 bundle）；禁 module 頂 import 把大庫壓進頁面 chunk
- **點擊才開的重元件**（modal / 抽屜）：`defineAsyncComponent(() => import('...'))`，不開不載
- **大型共用 vendor**（echarts / arco / vue 全家桶）：於 `vite.config` `build.rollupOptions.output.manualChunks` 拆獨立 chunk（利瀏覽器快取）
- **例外（別 lazy）**：首屏立即渲染必需者（App 殼層 / 登入核心）、體積極小的元件——lazy 反增請求數與閃爍。判準：**首屏就要？是→靜態；否→lazy**
