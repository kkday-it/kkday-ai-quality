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

> **按鈕群組換行已全域修好**：Arco 的 `a-radio-group type="button"`（`.arco-radio-group-button`）與 `a-button-group`（`.arco-btn-group`）皆為 `display:inline-flex` 且無 `flex-wrap`，選項/按鈕一多會橫向溢出容器而非換行；已在 `src/style.css` 統一補上 `max-width:100%; flex-wrap:wrap`（Arco 無對應 prop，全域一次修正）。**新元件不需要、也不要再各自加 `:deep()` 覆寫**——那會與全域規則重複。
4. **`style.css` 全域**：僅放 design token / reset / 跨頁共用基底，禁止塞頁面級樣式

> `preflight: false`（已關 Tailwind reset，避免破壞 Arco）。新增 utility 直接用，無需額外設定。

## 頁面殼層三層結構：parent tabs / sub tabs / 篩選操作區（強制 · 2026-08-07 使用者拍板）

頁面頂部的控制項一律**按語義分三層**擺放，各層各佔一條橫帶；**禁止把「切換看哪份資料」的導航控件塞進篩選列**。

| 層 | 放什麼 | 掛載點 | 控件形態 |
|---|---|---|---|
| **① parent tabs**（切換頁面） | 不同功能頁 | `FeatureTabs`（`MODULES[].tabs`，由各 feature `*_TABS` 從路由 `meta.text` 衍生） | `a-tabs type="line"`，key＝路由路徑 |
| **② sub tabs**（切換看哪份資料） | 同頁內的資料源 / 檢視，換了它**整頁資料重載** | `<Teleport to="#page-subtabs">`（AppShell 主 tab 列正下方） | `a-radio-group type="button" size="small"` 分段按鈕 |
| **③ 篩選 / 操作區** | **收斂當前這份資料**的條件（日期 / 分類 / 模型）＋對它的動作（重新整理 / 導出） | `<Teleport to="#page-toolbar">`（子 tab 列正下方） | select / picker / button（排版見下節 Arco Grid） |

**判準（拿不準放哪層時問這兩句）**

1. 「換了它之後，我還在看**同一份**資料嗎？」→ **不是** → ② sub tabs（導航）
2. 「換了它之後，我看到的是同一份資料的**子集**嗎？」→ **是** → ③ 篩選
3. 兩者皆非的**顯示選項**（趨勢粒度、圖表型態、排序方式）→ **貼著它所影響的那張圖 / 那塊區域放**，不進篩選列——放進去會讓人誤以為它會篩掉資料。

**同語義跨頁一致（呼應下方「同語義控件跨頁一致」）**：同一語義的 sub tabs 在不同頁面必須用**同一種控件**與**同一份選項 SSOT**。實例：歸因概覽的「檢視」與歸因列表的「反饋來源」都吃 `SOURCES`（`config/global/sources.json`），一律走 `#page-subtabs` + 分段按鈕，禁止一頁做 tabs、另一頁做下拉。

**掛載點契約**：`#page-subtabs` / `#page-toolbar` 皆帶 `empty:hidden`，無頁面注入時收合為 0 高，不影響其餘頁面；橫帶樣式（bg / border / px-5 / py-2）集中於 `AppShell.vue`，頁面 Teleport 內只放內容、不各自補 padding（避免各頁對齊值 drift）。

> **反例（2026-08-07 實際踩過）**：歸因概覽曾把 6 顆檢視切換與 6 個篩選/操作控件擠在同一條 `#page-toolbar`，實測需 **1572px** 才排得下——MacBook Pro 14"（1512）就開始擠壓。更隱蔽的是**壞法**：`a-select` 的固定 `style="width:200px"` 沒鎖 `flex-shrink:0`，超寬時是「下拉框悄悄變窄」而非溢出，**沒有橫向捲軸提示**，寬螢幕開發者永遠看不到。拆成三層後子 tab 列 532px、篩選列 1042px，兩段都不再擠壓。

## 多控制項橫列佈局（篩選列 / 工具列 · 強制用 Arco Grid）

篩選列 / 工具列等**會換行的多控制項橫排**（多個 `a-select` / `a-input` / 按鈕並排且可能超出一行），一律用 **Arco Grid（`a-row` / `a-col`）**，禁止手抄 `flex flex-wrap gap-x` 拼版：

- **根用 `<a-row :gutter="[橫, 縱]" align="center" wrap>`**：`gutter` 傳**陣列**同時給欄距與**換行行距**——這是關鍵。手寫 `flex flex-wrap gap-2` 的 `gap` 只作用於單一 flex 容器內部；一旦拆成**兩個相鄰 `<div>`**（如「篩選維度列」+「精確查詢列」），兩 div 之間**無縱向間距 → 視覺黏疊**。Grid 的 `gutter` 縱向值天然消除此問題。
- **每個控制項包一個 `<a-col :flex="...">`**：固定基寬用 `flex="160px"`，內容自適寬按鈕用 `flex="none"`，撐開占位（把右側計數/重置推到最右）用 `flex="auto"`（`flex` prop Arco 2.10.0+）。
- **控制項本身 `class="w-full"` 撐滿該欄**，寬度交給 `a-col`，**不再**在 `a-select` / `a-input` 上寫 `style="width: XXXpx"`。
- **多段橫列**（篩選維度 + 精確查詢分兩排）：拆成多個 `a-row`，段間距用 Tailwind `class="mb-2"`（Grid 不管跨 row 間距）。
- **條件渲染的欄位**：`v-if` / `v-for` 掛在 `a-col` 上（欄不存在時不佔 gutter 空位）。

> 範例見 `features/judge/pages/AttributionList.vue` 的 `#toolbar`。輕量單行、不會換行的少量控制項（2~3 個）可續用 `flex gap-2`，不強制 Grid。

## UI 元件復用（Arco 優先 · 不自寫）

任何 UI 需求（元件 / 互動方法 / 樣式），查找順序固定：**① codebase 既有共用元件 → ② Arco Design Vue 內建元件/方法/樣式 → ③ 專案已裝的其他套件（`vue-echarts`…）→ ④ 才自寫**。前三層任一能滿足，禁止跳過去自己刻。

- **元件**：彈窗/抽屜 → `a-modal`/`a-drawer`；表單/表格/樹/級聯/上傳 → `a-form`/`a-table`/`a-tree`/`a-cascader`/`a-upload`；提示/回饋 → `Message`/`Notification`/`a-alert`；導覽 → `a-anchor`/`a-tabs`/`a-breadcrumb`/`a-steps`；資料展示 → `a-descriptions`/`a-statistic`/`a-timeline`/`a-collapse`/`a-empty`/`a-skeleton`——動手寫一個「看起來很基礎」的 UI 片段（loading 骨架、空狀態、麵包屑、步驟條…）前，先查 Arco 是否已有對應元件，十之八九有。
- **方法 / API**：確認對話走 `Modal.confirm`/`a-popconfirm`（不自寫確認彈窗）；全域訊息走 `Message`/`Notification`（不自己疊 toast）；表單驗證走 `a-form` 的 `rules`（不手寫 validate 邏輯）；圖示一律 `@arco-design/web-vue/es/icon` 具名 import（不外找 icon 套件、不用 emoji/SVG 拼湊）。
- **樣式 / 語義**：顏色、狀態、尺寸優先用 Arco 的 `type`/`status`/`size`/`color` prop 或 **Arco 內建 CSS 變數**（`var(--color-text-1)`~`var(--color-text-4)`、`rgb(var(--primary-6))`、`rgb(var(--danger-6))` 等，定義於 `@arco-design/web-vue/dist/arco.css`），不要為了微調樣式另外手刻一套視覺規範；Arco prop 不夠精細才退到 `:deep()`（見上方樣式鐵律優先級）。

  > ⛔ **永遠不要在本專案使用 KKday 消費者前台 Design System 的 `var(--kk-*)` token**（如 `--kk-color-text-3`、`--kk-color-text-danger`）。本專案（內部後台 console）技術棧鐵律為 Arco Design Vue，**從未安裝、也不會安裝**該套消費者前台 DS（給 kkday.com 商品頁用，見 `~/.claude/skills/kkday-design-system/`）。`--kk-*` 在本專案是未定義的自訂屬性，`color` 屬性套用未定義變數會在 computed-value time 判定為 invalid，靜默退回繼承色而不報錯——外觀「看起來正常」但顏色語意沒有生效，非常隱蔽（實例：2026-07-23 `DataImportPanel.vue` 4 處誤用被修正）。上一段刻意避免再用「DS token」稱呼 Arco 自己的 CSS 變數，就是為了不再與這裡的 `--kk-*` 撞名混淆。
- **圖表** → `vue-echarts` 的 `<v-chart>`，不引入其他圖表庫（Arco 本身無重量級圖表元件，此為既定例外）。
- **判斷「Arco 沒有」前先查文件**：以 [arco.design/vue/component](https://arco.design/vue/component) 為準（禁照搬 React 版寫法），拿不準就先搜再下結論，不要單憑印象斷定「Arco 沒有這個」就直接自寫。
- **元件薄**：只管渲染 + 互動，業務邏輯下沉 composable / util；function > 50 行或元件塞多職責 → 拆分。

## 開發元件前：復用檢查優先於動手寫（強制 · 不需使用者提醒）

寫任何新元件 / 方法 / 樣式 / 邏輯前，**先查是否已有可復用的**，順序固定：**① 同 feature 內既有 component/composable/util → ② `@/components`／`@/composables`／`@/utils` 跨 feature 共用層 → ③ Arco 內建元件/方法/樣式（見上方「UI 元件復用」）→ ④ 才自寫**。任一層已有 ~80% 符合的既有實現，優先擴充其 props/slot 去覆蓋新場景，不另起爐灶——查找方式：`Grep` 元件名關鍵字 / feature 的 `components/index.ts`、`composables/index.ts` barrel 掃一眼既有清單，或用 codebase-memory `search_graph` 語意搜（見全域 agent-orchestration 規則）。

**公共邏輯預設放最外層（`src/components`／`src/composables`／`src/utils`），不是 `features/*`**：新寫一個元件 / composable / util 時，先問「這段邏輯本身耦合特定業務嗎」——不耦合（純排版、純資料轉換、純外觀邏輯，props/參數都是通用型別而非業務型別）就直接放最外層共用目錄，**不要因為目前只有一個 feature 在用就先放進該 feature 的資料夾、之後才「升級」搬出去**。判準同下方佈局元件抽離準則的內容耦合判斷：拿去給完全不相干的 feature 用，需不需要改這段程式碼本身——不需要 → 最外層；需要（改動涉及該 feature 特有的欄位/流程/術語）→ 才留在 `features/<feature>/` 底下。這條優先於「等第 2 次出現才抽」的漸進準則：**寫的當下就能判斷不耦合業務，直接放最外層，不必等出現第二個消費端**。

## 佈局性質元件主動拆公共元件（強制 · 不需使用者提醒）

開發過程中若寫出的區塊屬於**佈局性質**（跟具體業務資料無關，只管排版/導覽/容器結構——如「左側錨點導航 + 右側內容區」「固定 header + 可捲動 body」「多欄並排卡片」「窄直排收合軌 + 可收合面板」等），且該區塊已有跡象會被第二處消費（同檔內複用一次以上、或明顯是其他頁面/抽屜也會需要的通用結構），**當場主動拆成獨立元件放共用層，不必等使用者提出**：

- **判準＝Rule of Three 提前版**：**佈局結構第 2 次出現**（不用等到第 3 次）即拆——佈局元件比一般邏輯更容易被跨頁復用，且越晚拆、消費端寫死的樣式/資料耦合越深，重構成本越高。第 1 次出現時若已能預見「這結構明顯會被別處用到」（如抽屜的收合面板、確認彈窗的左選單），可以在第 1 次就直接拆，不必機械等到第 2 次才動手。
- 拆出的元件只管**排版與容器結構**，資料/業務邏輯留在呼叫端用 props 注入（呼應「元件薄」）；純樣式/純資料轉換的輔助函式一併下沉共用 `utils`，不要讓拆出的佈局元件裡還混著呼叫端專屬的格式化邏輯。
- **v-show 優先於 v-if**：可收合/可切換顯示的佈局元件（側欄、面板、tab 內容），若 slot/內容內有元件依賴掛載時機初始化預設值（如版本選擇器的預設勾選、composable 的 onMounted 副作用），一律用 `v-show` 保留掛載，不用 `v-if` 忽掛忽卸——避免「收合時看似正常、展開才觸發初始化」的隱性時序 bug（實例：`CollapsibleSidePanel.vue`）。
- **放置位置判準＝元件內容是否耦合業務，不是看目前消費端剛好都在哪個 feature**：元件本身不含任何業務邏輯（純排版/容器結構，props 全是外觀/開關類）→ 一律放 `@/components`，即使當下兩個消費端剛好都在同一個 feature 內也一樣；元件內容本身就耦合某 feature 的業務語意（如初判分類、規則版本）才留在該 feature 的 `components/`（同 barrel 慣例）。判斷時問自己：「把這個元件搬去給完全不相干的 feature 用，需要改元件本身一行 code 嗎？」不需要 → 放 `@/components`。
- 命名反映「佈局角色」而非「當下業務場景」（如 `LlmCallTimeline`、`CollapsibleSidePanel` 而非 `PolarityLogPane`、`JudgmentSettingsRail`），避免改名或內容耦合業務字眼，讓下一個消費端一看名字就懂能不能用。
- 完成後**同時檢查既有同類佈局是否已重複散落多處**，能收斂就順手收斂（不強制大規模 codemod，但當次任務觸碰到的範圍內要收）。

> Canonical 用例：`@/components/CollapsibleSidePanel.vue`（初判確認抽屜與 Prompt 測試抽屜的「左側窄直排收合軌＋可收合面板」共用元件，2026-07-16 於第 2 次出現時抽出；兩個消費端當下都在 judge feature 內，但元件本身零業務耦合，仍放跨 feature 共用層而非 `features/judge/components/`——這正是本條「判準看內容不看消費端」的實例）；`StickyTabs.vue`（tabs 固定捲動）；`TableLayout.vue`（表格三態）。

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

> ⚠️ **此表不適用於表格 per-row 操作欄**（隨列數重複出現的動作按鈕，如 `TableLayout` 的 `#actions` slot）：該情境按鈕組每一列都會重複一次，用色塊分級在多列並排時反而變成視覺噪音（一整欄藍橙灰綠上下重複），一律**統一用 `type="text"`**、`flex flex-wrap` 橫向鋪開、一行放不下自動換行，不逐顆垂直堆疊佔列高（實例：2026-07-23 `AttributionList` 的列操作欄從 primary/dashed/outline/text 四樣式改為統一 text）。toolbar / 卡片動作列 / 彈窗 footer（該區只出現一次，不隨列表重複）才適用下表的主次分色。
>
> ⚠️ **例外：窄容器（抽屜 / 彈窗）內的表格，操作欄改「固定窄寬 + 按鈕直排」**（`:width="~96"` + `flex flex-col items-start`，2026-08-07 使用者拍板）。上一段「橫向鋪開不直排」的理由是「直排會佔列高」——那條理由**只在頁面級寬表成立**：頁面表每列通常只有一行內容，直排會憑空拉高整列。抽屜內的表格已按「多欄收斂成描述區塊」改造過（見下方該節），每列本來就是 3~4 行高的描述區塊，按鈕直排正好落在同一段既有垂直空間內、**列高零增加**；反之橫排會讓操作欄吃掉整表 1/3 寬度，而窄容器裡左側描述區塊才是更需要寬度的一方。判準：**這一列本來就有多行高嗎？** 是 → 直排並鎖窄寬；否（單行列）→ 維持橫向 wrap。`type="text"` 統一色的部分兩者相同，不因直排而改。實例：`PromptDebugBatchDrawer.vue` 跑批記錄表（同時是本節與「多欄收斂成描述區塊」的 canonical 用例）。

| 語義 | Arco 樣式 | 例 |
|---|---|---|
| **主行為**（該區唯一最重要、確認/提交） | `type="primary"` | 儲存、確認、送出 |
| **次要行為**（並列可選動作） | `type="outline"` | 導出、匯入、複製 |
| **試驗性 / dry-run**（模擬執行、不落庫） | `type="dashed"` | 測試、測試 Prompt |
| **破壞性/需謹慎**（重置、刪除、清空） | `type="outline" status="warning"`（刪除用 `status="danger"`） | 恢復默認、刪除 |
| **純檢視/輕量**（開抽屜看、切換） | `type="text"` | 歷史、詳情 |

- 主行為**每區至多一顆** primary；其餘不得搶佔主色。
- **相鄰按鈕禁止「同 type 且同 status」**；同層級多顆 text 檢視鈕以不同 icon 區分。
- ~~列操作欄範本：初判分類 `primary` → 測試 `dashed` → 查看詳情 `outline` → 歸因歷史 `text`+icon~~（2026-07-23 已改為 per-row 一律 `text`，見上方例外說明；此行僅留存歷史對照，不再是範本）。
- 有明確語義的動作**配對應 icon**（導出→`icon-download`、新增→`icon-plus`、刷新→`icon-refresh`），icon 從 `@arco-design/web-vue/es/icon` 具名 import。
- 破壞性操作除變色外，仍須二次確認（`Modal.confirm` / `a-popconfirm`），顏色不替代確認。
- **同類按鈕聚合為 `a-button-group`**：同一操作區內若有 2 顆以上屬於「同一組核心操作」（如某功能的分類/歷史/導出三顆，語意上是一組流程而非各自獨立的動作），一律包 `<a-button-group>` 讓它們貼齊顯示成一個視覺群組，不要讓語意相關的按鈕之間留有等寬 gap、看起來跟其他無關按鈕一樣鬆散排列；`a-button-group` 只管版位貼齊，**組內每顆按鈕仍各自帶自己的 `type`/`status`**（不因為進了 group 就統一樣式），繼續遵守上表的主次區分——用**顏色**分主次，而非用「有無邊框」分。**併不併入群組看「流程歸屬」而非「按鈕型別」**：只要屬於同一條流程的一環就併入（如 dry-run 測試雖是試驗性動作，但仍是初判流程的一環，就該進群組）；唯有與 group 語意**完全無關**的另一類動作才維持在 group 外、不強行併入。範本見 `features/judge/pages/AttributionList.vue`（初判分類 `primary`(藍) → 初判 Prompt 測試 `primary status="warning"`(橙) → 初判歷史 `secondary`(灰) → 導出列表 `primary status="success"`(綠) 四顆全填滿併一組，順序＝三顆「初判*」相鄰成族、導出殿後）。
  - ⛔ **`a-button-group` 內禁用 `type="text"`**：Arco 的群組相連感靠相鄰按鈕的**邊框/底色**合併呈現（見 [arco.design/vue/component/button#button-group](https://arco.design/vue/component/button)），`text` 按鈕無邊框無底色，夾在群組中會「浮空」、讓整組看起來鬆散不相連（實例：2026-07-23 初判歷史原為 `text`，群組看不出是一組，改 `secondary` 後才相連）。上表原本歸 `text` 的「純檢視/輕量」動作，**一旦進了 button-group，一律升級為有底色/邊框的型別**（檢視類用 `secondary` 灰底最貼近原本輕量語義，或 `outline`），靠顏色與 icon 區分，而非拿掉邊框。group 外的獨立檢視按鈕才續用 `text`。
  - **偏好全填滿分色**：群組內按鈕優先**全部用有底色的填滿型別**（`primary` 藍 / `secondary` 灰 / `primary status="success"` 綠 / `status="warning"` 橙…），靠色相分主次，視覺上最像一條相連的分段控制列（實例：初判分類藍實心／初判歷史灰實心／導出列表綠實心）。用 `status` 色為群組視覺區分屬刻意的視覺選擇、可接受，但要對得上語義延伸：`success`(綠)→導出/產出、`warning`(橙)→試驗/dry-run/需謹慎的非正式動作（如 Prompt 測試）。仍守「主行為 primary(藍) 至多一顆」與相鄰按鈕不同色；**`danger`(紅) 嚴格保留給真破壞性動作**（刪除/清空），一律不得挪作純裝飾配色。

## 按鈕 / 彈窗文案禁用刪節號（強制 · 2026-07-30 使用者拍板）

按鈕文字、彈窗 / 抽屜標題、`Modal.confirm` 的 `title`/`okText` 等**動作標籤**一律用完整文案，禁止用「XXX…」表達「這裡還有更多」（如「升為正式版…」「導入資料包…」）——完整文案不必讓使用者猜點下去會發生什麼。

```
❌ <a-button>升為正式版…</a-button>
✅ <a-button>升為正式版</a-button>
```

- **適用範圍**：一切**動作標籤**——按鈕、`a-menu-item`、`a-dropdown-item`、彈窗/抽屜 `title`、`okText`/`cancelText`。
- **不適用（不要誤改）**：**進行中狀態**的刪節號是不同語義（「載入中…」「校驗中…」「儲存中…」），表達的是「非同步操作尚未結束」而非「文字被截斷」，維持既有寫法，不必刪掉。
- 判準：**這個刪節號是「動作標籤本身」還是「當下的進行式狀態」**——前者禁，後者留。

## 抽屜 / 彈窗標題命名（強制 · 2026-08-07 全專案盤點後立規）

四條規則，逐條都有實際踩過的案例：

1. **開啟它的按鈕文案 ＝ 它的標題**。使用者點「反饋詳情」開出來的抽屜就該叫「反饋詳情」，
   不叫「歸因詳情」；點「人工糾正」開出來的不叫「人工糾正歸因」。
   兩處措辭不一時，使用者會懷疑自己點錯了。
2. **標題只放名稱，補充說明放內文**（`a-alert` / hint 段）。禁止用括號在標題裡塞解釋：
   ❌「版本列表（草稿 / 正式版）」「初判歷史（每次批量 / 選取 / 單筆重新初判的 LLM 使用紀錄）」
   ✅「版本列表 · 草稿與正式版」「初判執行紀錄」。
3. **副標分隔符一律 `·`**（中點，前後各一空格）。曾經三種並存：`·`、`—`（RuleHistoryDrawer）、
   `×`（PromptReviseDrawer）。
4. **禁止兩個抽屜同名**——尤其層級不同的時候。實例：run 級的「每次 job 的 LLM 使用紀錄」與
   反饋級的「某則反饋的初判事件時間軸」一度都叫「初判歷史」，前者已改為「初判執行紀錄」。
   命名衝突不會有任何工具報錯，只會讓使用者以為自己開錯了視窗。

> 既有例外：`SettingsDrawer` 的標題與 tab 皆帶 emoji（`⚙️ 配置` / `🤖 LLM 設定`…），
> 是該抽屜自成一套的視覺錨點，內部一致故不強改；**新抽屜不要跟進這個寫法**。

## 按鈕 icon（強制 · 2026-08-07 使用者拍板）

**動作 → icon 的對照表是 SSOT**：`src/constants/actionIcon.constant.ts`。同一個動詞在全站永遠是
同一個圖示——「儲存」在設定面板和 Prompt 編輯器不該長得不一樣。沒有這張表，每個人寫元件時各自
挑一個看起來像的，半年後同一個動作會有三種圖示，而使用者是靠圖示形狀在掃描介面的。

**新增按鈕時先查表**：有同義動作就沿用，沒有才新增一條（並補進該檔）。

### 什麼時候該加（判準不是「全部都加」）

| 情境 | 加不加 | 為什麼 |
|---|---|---|
| 工具列動作、卡片動作列 | **加** | 一排並列的動作，圖示讓人不必逐字讀就分得出來 |
| **篩選列的選取／清除／重置** | **加** | 同上——它們是動作不是篩選條件本身 |
| 表格 per-row 操作 | **加** | 同一組按鈕每列重複，圖示是掃描時的定位點 |
| 破壞性 / 不可逆動作 | **加** | 多一層視覺確認，降低誤點 |
| 抽屜／彈窗 footer 的「取消 / 確定」 | **不加** | 位置（右下）與 primary 樣式已表達語義，加圖示只是視覺重量 |
| 分頁器、tab、radio-group 型切換 | **不加** | 那是導覽不是動作 |
| 同一容器超過 6 顆按鈕 | **重新設計** | 不是靠圖示救，是該收斂或分組 |

⚠️ **「選取 / 清除 / 重置」是三個不同動作，不要合成一個 `clear`**：選取是加、清除是減、重置是
回到起點。對照表原本只有一條 `clear: 'IconClose'`，但 `IconClose` 在使用者眼裡是「關閉」不是
「清除」，而且分不出「清掉已選的項目」與「把篩選條件退回預設」。現為
`selectBatch`(IconSelectAll) / `clearSelection`(IconEraser) / `reset`(IconRotateLeft) 三條。

### 分組共用同一個 icon

同一分組的動作**共用同一個圖示**，讓「這兩顆是同一類」不必讀文字就看得出來。
實例（歸因列表操作欄）：`初判分類` 與 `判決歸因` 同為 `IconRobot`（AI 產出的判定）、
三個「歷史」同為 `IconHistory`。**批量與單列的同一動作也必須同 icon**——工具列的「初判分類」
與列內的「初判分類」是同一件事的兩種範圍，用不同圖示會讓人以為是兩件事。

### 加 icon 之後要重量欄寬

icon 會吃掉 ~20px/顆。加完之後**用瀏覽器量，不要估**——量法見下一節。

## 表格欄寬完整規範（2026-08-07 實測定案）

### 1. 心智模型：`width` 是比例權重，不是像素

**Arco 2.58 的 `TableColumnData` 只有 `width`，沒有 `minWidth`**，所以「給下限、其餘自適應」寫不出來。

容器比欄寬總和大時，**Arco 對每一欄套用同一個放大倍率**：

```
拉伸率 = 表格容器可用寬 / SCROLL_X          （SCROLL_X = 各欄宣告寬總和 + 選取/展開欄）
某欄渲染寬 = 該欄宣告寬 × 拉伸率
```

實測佐證（歸因列表，同一時刻）：

| 容器 | 拉伸率 | 序號 40 | 反饋內容 340 | 關聯資料 300 | 判決歸因 260 |
|---|---|---|---|---|---|
| 1365px | 1.17 | 47 | 397 | 351 | 304 |
| 1973px | 1.67 | 67 | 567 | 500 | 433 |

**推論**：宣告寬決定的是「占幾份」，不是「有多寬」。改一欄的 width 會同時改變**所有**欄的渲染寬。

### 2. 依欄型分流

| 欄型 | 怎麼給 width | 為什麼 |
|---|---|---|
| **內容欄**（可截斷／換行的文字） | **抓比例即可，不用精算** | 多拿到的寬度是有用的——顯示更多字。width 等同 `flex-grow` 權重 |
| **動作欄**（按鈕列） | **見下方第 3 點** | 內容寬度固定，多拿到的寬度**全是死留白**——按鈕不會因為欄變寬而變好用 |
| **`fixed: 'left'/'right'`** | **必須明確給** | Arco 靠它算 sticky 偏移量，不給會錯位 |

**動作欄與內容欄的差別是本規範的核心**：同一個拉伸率對兩者的意義相反。內容欄拿到 67% 額外寬度是賺到，動作欄拿到 67% 就是 67% 的空白。

### 3. 動作欄：讓它換行，然後 width 就不再是下限問題

**強制：同一列會有兩顆以上按鈕並排的動作欄，容器一律加 `flex-wrap: wrap`。**

判準是「**同時**渲染幾顆」不是「程式碼裡寫了幾顆」——以下三種不算多按鈕、不需要 wrap：
互斥 `v-if / v-else-if` 鏈（每列只出現一顆，如 `PromptVersionDrawer`）、
外層已是 `flex-col`（本來就一顆一行，如 `PromptDebugBatchDrawer`）、
單一動作欄（如三個歷史面板的 `#op`）。

```css
.act-group {
  display: flex;
  flex-wrap: wrap;   /* ← 這一行消滅「靜默裁切」這個失敗模式 */
  gap: 4px;
}
```

加了之後模型整個變乾淨：

- **不可能被裁切**——不夠寬就換行，不會把字切掉
- 於是 **width 不再是「裁切下限」，而是「並排 vs 堆疊的切換點」**
- 可以放心把它調小：**寬螢幕維持並排、窄螢幕自動堆疊**，兩端都對

實測（歸因列表操作欄，7 顆按鈕分 4 組）：

| 宣告寬 | 2048px 下渲染 | 形態 | 1280px 下渲染 | 形態 | 裁切 |
|---|---|---|---|---|---|
| 180（無 wrap） | 301 | 並排四行 | 180 | 並排四行 | 無，但留白 152px |
| **112（有 wrap）** | **198** | 並排四行 | **121** | 堆疊七行 | 無 |

**列高不是換行的代價**：整列高度由**最高的那一欄**決定（歸因列表實測 295~397px，由「關聯資料」欄撐開），而操作欄並排時 79px、堆疊七行也只有 151px——遠低於列高，多幾行完全不影響版面。動手前先量整列高度，不要憑直覺假設「換行會變高」。

> ⚠️ **本節推翻了 2026-08-07 稍早的「寧可留白也不要靜默裁切」**。那條規則在**沒有 `flex-wrap`** 的前提下是對的（當時只能靠保守下限硬撐，欄寬因此被一路推到 180、寬螢幕留白 152px）。加了換行之後前提消失，不要再照舊規則加寬。

### 4. 量法（強制照做，不要估）

**⚠️ 必須在窄視窗量（≤1280px）。** 2026-08-07 踩過兩次：

- 在 1365px 量到「還有 19px 餘裕」→ 誤判不必加寬。那是拉伸率 1.185 的數字，窄視窗會裁切
- 審計時用手算 `(96−32)−(24+36+16)` 推導溢出 → 方向對但數字不可信，實量才發現真正裁切的是另外兩欄

列表有 `:scroll="{ x: SCROLL_X }"`，容器窄於 SCROLL_X 時各欄退回宣告寬（拉伸率→1.0），**這才是最壞情況**。寬螢幕開發者永遠看不到。

```js
// 貼在 Chrome DevTools console；先把視窗縮到 1280×800
const tr = document.querySelector('.arco-table-body tbody tr');
const last = tr.lastElementChild;
const cell = last.querySelector('.arco-table-cell');
const cs = getComputedStyle(cell);
const usable = cell.getBoundingClientRect().width
  - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
// 內容固有需求：解除換行後量 max-content
const inner = cell.firstElementChild;
const prev = inner.style.cssText;
inner.style.width = 'max-content'; inner.style.flexWrap = 'nowrap';
const needed = Math.ceil(inner.getBoundingClientRect().width);
inner.style.cssText = prev;
({ 渲染寬: Math.round(last.getBoundingClientRect().width), usable: Math.round(usable),
   needed, 會換行: needed > usable, 整列高: Math.round(tr.getBoundingClientRect().height) });
```

**判讀**：有 `flex-wrap` 時 `needed > usable` 只代表「會換行」不代表壞掉；沒有 `flex-wrap` 時它代表**被裁切**，必須加寬。

### 5. 算式與註解義務

動作欄的 width **必須在該欄定義處留下算式註解**，含三個數字：單一按鈕寬、一組並排寬、Arco 儲存格左右內距（見下一節，恆為 32px）。canonical 範例見 `features/judge/constants/source-schema.constant.ts` 的操作欄。

```
一組並排需 148px（icon+4字 ×2 + gap 4×2 + 分隔 0）
宣告 112 → 2048px 渲染 198 → 可用 165 ≥ 148 → 並排
宣告 112 → 1280px 渲染 121 → 可用  88 <  148 → 堆疊（不裁切）
```

**改了按鈕文案、加了 icon、或動了內距，一律重量重寫這段註解**——這三者都會改變 `needed`。2026-08-07 有五個抽屜的操作欄加了 icon 卻沒重量，其中兩個真的被裁切（`PromptVersionDrawer` 切 5px、`PromptDebugBatchDrawer` 切 1px）。

### 6. 反例清單（出現即改）

- ❌ 為了「看起來貼合寬螢幕」把動作欄調到剛好——窄視窗必裁切
- ❌ 沒有 `flex-wrap` 卻用「保守加寬」硬撐——留白會隨螢幕變寬等比放大
- ❌ 只在自己的螢幕上目視確認——寬螢幕看不到裁切，窄螢幕看不到留白
- ❌ 手算推導欄寬結論——一律實量
- ❌ 用 `:deep()` 改單一欄的 cell padding 去擠出寬度——見下一節，內距不客製化；要寬度就改 `width`

## 表格儲存格內距：一律用 Arco 預設，不客製化（2026-08-07 使用者拍板）

**`.arco-table-cell` 的 `5px 16px` 原樣不動，不加任何全域覆寫、不做各欄特例。**
算動作欄下限時左右共吃掉 32px。

上下左右**刻意不對稱**，那是資料表的設計意圖（垂直緊湊＝一屏多看幾列；水平寬鬆＝欄與欄視覺分離），
不是待修的瑕疵。

- **禁止各欄／各表自行收窄或加寬 cell padding**。曾有一條
  `:deep(.arco-table-td:last-child .arco-table-cell)` 把最後一欄收成 8px 換取 16px 欄寬——省下的
  寬度不值得換來「欄與欄內距不一致」，已刪。要更多欄寬就把 `width` 開大。
- **一致性靠「不做特例」達成，不靠全域 override**。曾短暫改成全域 12px 全向，代價是必須連 Arco
  展開行子表那條「等於 cell padding 的負 margin」一起覆寫——那是 Arco 的 private implementation
  detail，升版就要重驗。回歸預設後零維護。
- **儲存格內容 wrapper 不要加 `py-*`**：cell padding 就是 cell padding，在 slot 根節點再疊一層
  等於各處手抄同一個語義。嫌太擠就調 `TABLE_DEFAULTS.size`，不要逐欄補。
  （塊與塊之間的分隔留白配 `border` 是另一回事，那不是儲存格內距，可以留。）
- **密度只認 `TABLE_DEFAULTS`**，不要在個別表寫 `size` 字面值。

## 使用者可見文案的來源用字（強制 · 2026-08-07 使用者拍板）

**跨來源的泛稱一律用「反饋」**，不要用某個來源的專屬名詞代稱全體。系統有 5 個反饋來源
（`reviews` 商品評論、`conversations` 客服進線…），日後還可能整體融合——UI 文案若寫死成
「評論」，每接一個新來源就要全面重寫一次，而且看 conversations 資料的人會看到「本則評論」。

| 情境 | 用字 |
|---|---|
| 跨來源泛稱（KPI、備註、時間軸、狀態說明、單筆代稱） | **反饋**（「這則反饋」「已初判反饋」「反饋詳情」） |
| 明確指某一個來源的身分 | **保留該來源的專屬名詞**（`SOURCE_LIST_SCHEMAS` 的 `idNoun`／`contentLabel`：reviews→「評論」、conversations→「進線」） |
| 外部系統的既有專有名詞 | 原樣保留（「外部評論」＝外部評論系統的融合資料，不是泛稱） |

判準一句話：**這句話換一個來源看還成不成立？** 成立就用「反饋」，只對某一來源成立才用專屬名詞。

實例（2026-08-07 修）：概覽 KPI「已初判進線」→「已初判反饋」（那是全來源合計）；
備註 placeholder「輸入評論級備註（記錄本則評論的處理脈絡）」→「記錄這則反饋的處理脈絡」。

## 彈窗 vs 抽屜（Drawer-first · 強制）

**除「確認窗口」外，一切彈出層一律用 `a-drawer`（右側滑出），禁止新增內容型 `a-modal`**；需求觸碰到既有內容型 modal 時順帶替換為 drawer。

| 場景 | 用什麼 |
|---|---|
| 二次確認（刪除/覆蓋/送出：純文案＋確定/取消，至多附一個備註輸入欄） | `Modal.confirm` / `a-popconfirm` / 輕量 `a-modal` |
| 表單 / 參數配置（新增、編輯、初判目標、導出設定） | `a-drawer` |
| 詳情 / 歷史 / 時間軸 / 測試面板 / 預覽 | `a-drawer` |

- 一律右側滑出（不指定 `placement`、不混向）；寬度依內容：640 單欄詳情 / 680–760 輕表單 / 820–900 並排對比、時間軸 / 1040 多欄配置表單
- **內部滾動高度撐滿（強制）**：抽屜主內容為單一長列表 / 時間軸 / 表格時，`:body-style="{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }"` 打通高度鏈，滾動區塊 `flex-1 min-h-0 overflow-auto`（表格走 TableLayout 傳 `full-height`）。**禁止 `max-h-[NNNpx]` 寫死滾動高**——那是 modal 時代殘留，抽屜滿高後會變成「上面一小塊滾動、下面大片留白」。例外：多段文檔流內容（表單＋說明＋子表混排）維持 drawer body 預設整體捲動即可。
- 純檢視 `:footer="false"`；有提交動作沿用 `ok-text` / `cancel-text` / `:ok-loading` / `@ok`——drawer 與 modal 同名同義 API，替換可直接平移
- 重內容加 `unmount-on-close`（配 `defineAsyncComponent` 點開才載，見下方懶加載）
- 不可中斷流程（匯入中）：`:mask-closable="false"` + `:closable` 動態控制
- 元件檔名以 `*Drawer.vue` 結尾；禁止 drawer 內容元件命名 `*Modal`

## Tabs 切換展示（固定 Tab · 內容捲動 · 強制用公共元件）

任何用 tabs 做切換展示的場景（多路 LLM 調用、多分頁資料檢視等），**tab 列必須恆常可見固定，只有內容區塊捲動**——內容過長時使用者仍要能一眼看到全部 tab、隨時點擊切換，禁止 tab 列隨內容一起被捲走（使用者要滾回頂部才找得到切換入口）。

**一律用公共元件 `StickyTabs`（`@/components`）取代裸 `a-tabs`，禁止各處手抄 `:deep()` CSS：**

```vue
<script setup lang="ts">
import { StickyTabs } from '@/components';
</script>

<template>
  <StickyTabs v-model:active-key="activeTab" type="card-gutter" size="small" :lazy-load="true">
    <a-tab-pane key="foo" title="Foo">…</a-tab-pane>
  </StickyTabs>
</template>
```

- **透明轉發**：`StickyTabs` 不宣告 props，`v-model:active-key` / `type` / `size` / `:lazy-load` 等一切 `a-tabs` 原生 API 直接生效，`<a-tab-pane>` 寫法零改動——與裸 `a-tabs` 替換零學習成本。
- **消費端前提**：根元素需在有實際高度的容器內（drawer 走 `:body-style` 撐滿、頁面走 `h-full`）才能 `flex:1` 撐滿並讓內容區正確捲動。
- **消費端不得再套 `overflow-auto` 包住整個 `<StickyTabs>`**——捲動容器已下沉到元件內部，外層若疊加 `overflow-auto` 會產生雙層捲軸；消費端改用 `overflow-hidden` 讓內部機制接管。
- **串流新增條目要自動捲到底**：用 `ref` 拿 `StickyTabs` 實例，呼叫其 `scrollActiveToBottom()`（`:lazy-load="true"` 下同時只有 active pane 掛載，元件內部自動抓對容器，消費端不需得知 Arco 內部 class 名稱）。
- **內容旁要掛一份跟捲動同步的側欄導航**（如左側掛錨點導航）：用同一 `ref` 呼叫 `getScrollEl()` 取得 `.arco-tabs-content` 這個唯一捲動容器本身，餵給 `a-anchor` 的 `:scroll-container`；side-nav 元素本身放在 `<StickyTabs>` **外面**（同一個 flex row 的相鄰兄弟），不要塞進 tab-pane 內部——這樣捲動範圍天然限定在 tab 列下方的內容區，不含 tab 列與側欄自身，也不必額外包一層外層捲動容器。

> Canonical 用例：`features/judge/components/PrejudgeLogTabs.vue`（7 路 LLM 調用 tab，`polarity`/`C-1`~`C-6`，含左側掛錨點導航 + `getScrollEl()` 用法；`PrejudgeLogView.vue` 是它的外層 wrapper，捲動機制不在該檔）。`StickyTabs` 內部實作（`:deep()` 覆寫 `.arco-tabs`/`.arco-tabs-nav`/`.arco-tabs-content`，水平 overflow 維持 hidden 不動 Arco 原生 clip 機制）見 `components/StickyTabs.vue` 本身，除非要擴充該元件本身，否則消費端不需要、也不應該知道這些內部細節。

## 異步加載三態一致：骨架 + 進度反饋（canonical · 強制）

任何**異步加載**的 UI（fetch/SSE/await 後才有內容）一律給「載入中反饋」，禁止只有空白或裸 spinner 乾等；且**同類場景用同一元件**，不各處手抄骨架/進度：

| 異步場景 | 唯一元件 | 載入中反饋 |
|---|---|---|
| **列表表格**（頁/抽屜/彈窗內） | `TableLayout`（`@/components`） | 內建 loading（a-table spin）+ error（表上 alert 不遮資料）+ emptyText 三態 |
| **有確定進度的 job**（批次初判/導出，知道 processed/total） | `ExportProgressBar`（`@/components`）或 `a-progress` | 真實百分比進度條 + 文案（見既有 useExportJob/usePrejudgeJob 的 SSE 驅動） |
| **非表格區塊/詳情/卡片**（單請求，進度不可知，如訂單佐證/圖表容器/側欄詳情） | **`AsyncSection`（`@/components`）** | **頂部不確定進度條（indeterminate 動態感）+ `a-skeleton` 骨架占位 + error/empty 三態** |
| **按鈕觸發的動作**（送出/儲存/測試） | `a-button :loading` | 按鈕自身 loading 態（不需骨架） |

- **強制**：凡 `ref(false)` 的 loading 狀態驅動一塊**內容區塊**的顯示，該區塊一律包 `AsyncSection`（或上表對應元件），不得只寫 `v-if="loading"` 顯示一行「載入中…」或什麼都不顯示直接閃現。骨架讓使用者預期內容形狀、進度條給「正在載入」的實時感——兩者都要，尤其加載 >1s 的請求（如佐證取數 3-6s）。
- **Canonical 用例＝訂單佐證區塊**（`features/judge/components/AttributionDetailDrawer.vue` 的 `AsyncSection`）：`:loading`/`:error`/`:empty`/`:empty-text`/`:skeleton-rows`，成功內容走預設插槽。任何新的異步區塊照此，禁止再手寫 `a-skeleton`+`a-alert`+`a-empty` 三段 v-if（那正是 AsyncSection 收斂掉的重複）。
- **值域/三態語義同源**：`AsyncSection` 的 loading/error/empty 對應 composable 回傳的三態（如 `useOrderEvidence` 的 `loading`/`error`/`result`）——composable 也一律回這組三態，不各自發明狀態欄位。
- **選型判準**：能算百分比 → 進度條（ExportProgressBar/a-progress）；算不出百分比的單請求 → AsyncSection（骨架+不確定條）；表格 → TableLayout；按鈕 → :loading。拿不準時預設 AsyncSection。

## 窄容器內的表格：多欄收斂成「描述區塊」（強制）

抽屜／彈窗等**寬度受限**的容器內，表格**禁止靠 `:scroll="{ x: NNN }"` 硬撐出橫向捲動**來塞下
一堆窄欄。一律把語義同群的欄位收斂成**一個描述區塊欄**，用「左小標籤 ＋ 右內容」的緊湊列堆疊：

```vue
<a-table-column title="本批" :width="300">
  <template #cell="{ record }">
    <div class="flex flex-col gap-1 py-0.5">
      <div class="text-xs font-medium">{{ 主要值 }}</div>
      <div class="flex gap-1.5 text-[11px] leading-[1.6]">
        <span class="shrink-0 text-[#86909c]">標籤</span>
        <span class="min-w-0 truncate text-[#4e5969]">{{ 值 }}</span>
      </div>
    </div>
  </template>
</a-table-column>
```

- **判準**：抽屜內表格欄數 >4，或最小寬度超過容器寬度 → 收斂。頁面級寬表不受此限。
- **語義分群**：同一群 = 值的來源與變動時機一致（例：跑批記錄的「本批」＝輸入／範圍／Prompt 版本，
  同群組必然相同；「執行」＝模型／結果／狀態／耗時，逐 run 不同）。
- **合併儲存格（`span-method`）配套**：收斂後合併的欄要跟著減少。三個窄欄各自合併時，
  鄰欄若有 N 列會拉出一大塊空白（實例：2026-07-31 跑批記錄多模型群組列）。
- **長值一律 `min-w-0 truncate` + `:title`**：`truncate` 在 flex 子元素上必須配 `min-w-0` 才生效，
  漏了就會把父容器撐爆——那正是橫向捲動的來源之一。
- **收斂後操作欄跟著鎖窄寬 + 按鈕直排**：列已是多行高的描述區塊，直排零成本，橫排卻要吃掉
  1/3 表寬——見上方「按鈕與操作區」的窄容器例外。
- Canonical 用例：`RecordContextPanel.vue` 的 `compact` 版型（歸因列表「關聯資料」欄）、
  `PromptDebugBatchDrawer.vue` 的跑批記錄表。

## 進度條百分比（全站統一 · 強制）

**任何 `a-progress` 顯示文字一律走 `fmtPercent()`（`@/utils`），格式固定 `xx.xx%`（兩位小數）。**

```vue
<a-progress :percent="pct">
  <template #text="{ percent }">{{ fmtPercent(percent) }}</template>
</a-progress>
```

- **為什麼不能用 Arco 預設**：`a-progress` 不帶 `#text` 時會把浮點原樣印出來——實際踩過
  `98.63013698630137%`（2026-07-31 跑批進度條），一長串數字在窄容器裡還會把版面撐爆換行。
- **禁止各處手抄 `(percent * 100).toFixed(2)`**：同一個顯示口徑手抄必 drift（改造前
  `DataImportPanel` / `DataUpload` 抄了兩份，其餘四處根本沒抄 → 同一個 app 兩種格式）。
  一律 import `@/utils` 的 `fmtPercent`。
- **`:show-text="false"` 的純視覺條**（如列表內的細條）不需要，本規則只管有顯示文字的。
- **未知不等於 0%**：`fmtPercent` 對 `null`/`undefined`/非有限數回 `—`。分母未知時
  （如 server 重啟後由磁碟推導的 run，`total` 為 `null`）**不要畫一條 0% 的假進度條**，
  改顯示說明文字——0% 會讓使用者以為「跑了但一筆都沒成功」。

## 同語義控件跨頁一致（canonical 對齊 · 強制）

同一語義的設定/表單控件，**全站只准一種元件形態**；已有 canonical 實作的語義，新頁面必須對齊其元件選型與交互語義（含禁用/鎖定條件與值域 SSOT），禁止另選元件重做一套：

| 語義 | 唯一元件形態 | 禁止 |
|---|---|---|
| 布林開關（開/關、啟用/停用、鎖定） | `a-switch`（必要時 `checked-value`/`unchecked-value` 帶語義值） | `a-select` 下拉「開啟/關閉」、checkbox 模擬開關 |
| 小集合互斥檔位（≤6 個枚舉，如 reasoning effort） | `a-radio-group type="button" size="small"`（分段按鈕） | select 下拉（掃視成本高、與 canonical 不一致） |
| 大集合單選（模型清單、連線清單） | `a-select`（`:options` 或 a-option） | 自刻下拉 |
| 數值微調（temperature 類） | `a-switch`（啟用自訂）＋ `a-slider`＋當前值顯示；有鎖定條件時 switch disabled + 鎖定說明文字 | 裸 input number、無啟用開關的常駐 slider |

- **Canonical 用例＝LLM 旋鈕**（`@/components/LlmKnobs.vue`，A schema 2026-07-22 起為唯一實作，各功能區與設定面板皆呼叫此元件，禁止另寫第二套）：Thinking＝`a-switch on/off`、Reasoning effort＝radio-group 分段、Temperature＝switch＋slider＋`tempLocked`。值域與鎖定規則不再寫死（不是 `provider === 'openai'` 判斷），改由 `features/settings/constants` 的 `capabilitiesFor(model, provider)` 依所屬供應商（`config/global/llm_model.json` `providers[].supportsThinking`/`reasoningEffortOptions`/`temperatureLockedWhenThinking`/`lockedTemperatureValue`，個別 model 可被 `modelCapabilities` 覆寫）動態決定，與後端 `settings.model_capabilities_for()` 同一份資料源。⚠️ **2026-07-31 起 `LlmKnobs` 只出現在一個地方**：設定 › LLM 設定 的模型配置編輯器（`features/settings/components/LlmModelConfigList.vue`）。旋鈕已收斂成**全域具名模型配置**，各功能區（初判分類 `prejudge`/Prompt 調試台 `prompt_debug`/AI 定點改寫 `prompt_revise`，SSOT＝`config/global/llm_model.json` `areas`）頁面上只留一個 `@/components/LlmConfigSelect.vue` 下拉選配置，**不得**再內嵌整組旋鈕；v-model 綁定一律走 `useLlmAreaConfig(area)`（回傳 `configId`/`configs`/`config`/`overrides`，其中 `overrides` 形狀與改造前一字不差，故後端契約零改動）。配對的供應商 radio 元件 `LlmConfigPicker.vue` 已**整檔退役**（唯一職責是選供應商，配置自帶 provider 後無此需求）。「哪個功能區用哪一筆配置」＝設定表 `setting_master` 內設定 JSON 的 `llm_area_configs` 鍵，與配置**內容**同為**團隊共用單一份**（2026-07-31 拍板；曾短暫改存 localStorage 求個人隔離，但瀏覽器儲存跨不了人與裝置，同事拿不到你調好的安排，故收回 DB）。**下拉沒有儲存按鈕——選了就落庫**（`useLlmAreaConfig` 的 `configId` setter，樂觀更新＋失敗回滾＋Message 提示）；也因為每個使用點都自動同步，設定面板**不得**再開一個集中綁定區塊（那是同一件事的第二個入口）。**「測試連線」亦收斂到設定面板**：`useLlmConfigTest` 唯一消費端＝`LlmModelConfigList.vue` 的配置編輯區（綠色 `primary status="success"`，測當前展開那筆的**草稿值**，未存也能先驗），功能區頁面**不得**再各擺一顆——旋鈕已不在那裡，該在編配置的地方驗證。⚠️ 與連線卡（`LlmConnectionCard`）的「測試連線」是**兩層不同的測試**，勿合併：連線卡測「base_url + token 通不通」，配置列測「這組 model + 旋鈕跑不跑得動」。任何頁面出現同語義需求一律複用上述元件，不得自帶第二套值域或另選元件（2026-07-22 Prompt 調試台曾用 select 下拉重做旋鈕被退回對齊，即本條由來——注意那是「重做旋鈕」被退回，不是「用 select 選配置」）。
- **值域/選項 SSOT 同源**：對齊 canonical 時連值域一起復用（import 同一 constants），禁止在新頁面手抄枚舉陣列——手抄必 drift。
- **第 2 次出現即評估抽共用元件**（呼應上方佈局拆分準則）：同一組控件組合出現在第 2 個頁面時，優先抽成共用元件（props 注入差異）而非各自複製模板。

## 控件輔助/狀態說明文字位置（強制）

控件旁的輔助說明／狀態文字（鎖定原因、目前狀態、disabled 原因等 `text-xs text-[#86909c]` 灰字）一律放在**控件橫列下方另起一行**，禁止塞進 `a-space :wrap="false"` 內緊貼控件同一列——窄容器（設定抽屜／彈窗）下同排文字會被截斷或推擠版面（實例：`LlmKnobs.vue` Reasoning effort 的 disabled 說明「Thinking 關閉：不支援完全關閉的模型自動降為最低檔」曾在抽屜內被截斷,2026-07-23 修正)。

- **結構**：外層包 `<div class="flex flex-col gap-1">`，第一行放控件本體（原橫排 `a-space`／單一控件不動），第二行放說明 `<span class="text-xs text-[#86909c]">`
- 說明文字**不加 `whitespace-nowrap`**，讓其可正常換行，避免超出容器寬度
- Canonical 用例：`@/components/LlmKnobs.vue`（Temperature 狀態文字、思考模式狀態文字、Reasoning effort disabled 說明皆遵此結構）

## 懶加載 / Code-splitting（預設機制）

首屏不需要的一律延遲載入，縮小初始 bundle（呼應 06 quality-targets：單路由 JS < 200KB gzip）：

- **路由頁元件**：一律 `component: () => import('...')`（route-level splitting）；禁在 route 檔頂靜態 `import 頁面元件`
- **重型第三方庫**（jsoneditor / jspdf / html2canvas 等）：使用點動態載入——元件內 `await import('lib')`（掛載 / 觸發時），型別走 `import type`（編譯期擦除，不進 bundle）；禁 module 頂 import 把大庫壓進頁面 chunk
- **點擊才開的重元件**（modal / 抽屜）：`defineAsyncComponent(() => import('...'))`，不開不載
- **大型共用 vendor**（echarts / arco / vue 全家桶）：於 `vite.config` `build.rollupOptions.output.manualChunks` 拆獨立 chunk（利瀏覽器快取）
- **例外（別 lazy）**：首屏立即渲染必需者（App 殼層 / 登入核心）、體積極小的元件——lazy 反增請求數與閃爍。判準：**首屏就要？是→靜態；否→lazy**
