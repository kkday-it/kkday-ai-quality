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

## UI 元件復用（不自寫）

新增任何共用 UI 前，先搜尋 codebase 既有 component，找到即擴充，不平行造第二套。

- 需要彈窗 / 抽屜 → 用 `a-modal` / `a-drawer`，不自寫
- 需要表單 / 表格 / 提示 → 用 Arco 對應元件（`a-form` / `a-table` / `Message`）
- 需要圖表 → 用 `vue-echarts` 的 `<v-chart>`，不引入其他圖表庫
- **元件薄**：只管渲染 + 互動，業務邏輯下沉 composable / util；function > 50 行或元件塞多職責 → 拆分

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
