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
