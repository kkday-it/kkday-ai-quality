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

## UI 元件復用（Arco 優先 · 不自寫）

任何 UI 需求（元件 / 互動方法 / 樣式），查找順序固定：**① codebase 既有共用元件 → ② Arco Design Vue 內建元件/方法/樣式 → ③ 專案已裝的其他套件（`vue-echarts`…）→ ④ 才自寫**。前三層任一能滿足，禁止跳過去自己刻。

- **元件**：彈窗/抽屜 → `a-modal`/`a-drawer`；表單/表格/樹/級聯/上傳 → `a-form`/`a-table`/`a-tree`/`a-cascader`/`a-upload`；提示/回饋 → `Message`/`Notification`/`a-alert`；導覽 → `a-anchor`/`a-tabs`/`a-breadcrumb`/`a-steps`；資料展示 → `a-descriptions`/`a-statistic`/`a-timeline`/`a-collapse`/`a-empty`/`a-skeleton`——動手寫一個「看起來很基礎」的 UI 片段（loading 骨架、空狀態、麵包屑、步驟條…）前，先查 Arco 是否已有對應元件，十之八九有。
- **方法 / API**：確認對話走 `Modal.confirm`/`a-popconfirm`（不自寫確認彈窗）；全域訊息走 `Message`/`Notification`（不自己疊 toast）；表單驗證走 `a-form` 的 `rules`（不手寫 validate 邏輯）；圖示一律 `@arco-design/web-vue/es/icon` 具名 import（不外找 icon 套件、不用 emoji/SVG 拼湊）。
- **樣式 / 語義**：顏色、狀態、尺寸優先用 Arco 的 `type`/`status`/`size`/`color` prop 或 DS token（`var(--color-xxx)` / `rgb(var(--primary-6))`），不要為了微調樣式另外手刻一套視覺規範；Arco prop 不夠精細才退到 `:deep()`（見上方樣式鐵律優先級）。
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

| 語義 | Arco 樣式 | 例 |
|---|---|---|
| **主行為**（該區唯一最重要、確認/提交） | `type="primary"` | 儲存、確認、送出 |
| **次要行為**（並列可選動作） | `type="outline"` | 導出、匯入、複製 |
| **試驗性 / dry-run**（模擬執行、不落庫） | `type="dashed"` | 測試、測試 Prompt |
| **破壞性/需謹慎**（重置、刪除、清空） | `type="outline" status="warning"`（刪除用 `status="danger"`） | 恢復默認、刪除 |
| **純檢視/輕量**（開抽屜看、切換） | `type="text"` | 歷史、詳情 |

- 主行為**每區至多一顆** primary；其餘不得搶佔主色。
- **相鄰按鈕禁止「同 type 且同 status」**；同層級多顆 text 檢視鈕以不同 icon 區分。
- 列操作欄範本（AttributionList）：初判分類 `primary` → 測試 `dashed` → 查看詳情 `outline` → 歸因歷史 `text`+icon——四鈕四樣式，掃一眼即分級。
- 有明確語義的動作**配對應 icon**（導出→`icon-download`、新增→`icon-plus`、刷新→`icon-refresh`），icon 從 `@arco-design/web-vue/es/icon` 具名 import。
- 破壞性操作除變色外，仍須二次確認（`Modal.confirm` / `a-popconfirm`），顏色不替代確認。

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

> Canonical 用例：`features/judge/components/PrejudgeLogView.vue`（7 路 LLM 調用 tab，`polarity`/`C-1`~`C-6`，含左側掛錨點導航 + `getScrollEl()` 用法）。`StickyTabs` 內部實作（`:deep()` 覆寫 `.arco-tabs`/`.arco-tabs-nav`/`.arco-tabs-content`，水平 overflow 維持 hidden 不動 Arco 原生 clip 機制）見 `components/StickyTabs.vue` 本身，除非要擴充該元件本身，否則消費端不需要、也不應該知道這些內部細節。

## 同語義控件跨頁一致（canonical 對齊 · 強制）

同一語義的設定/表單控件，**全站只准一種元件形態**；已有 canonical 實作的語義，新頁面必須對齊其元件選型與交互語義（含禁用/鎖定條件與值域 SSOT），禁止另選元件重做一套：

| 語義 | 唯一元件形態 | 禁止 |
|---|---|---|
| 布林開關（開/關、啟用/停用、鎖定） | `a-switch`（必要時 `checked-value`/`unchecked-value` 帶語義值） | `a-select` 下拉「開啟/關閉」、checkbox 模擬開關 |
| 小集合互斥檔位（≤6 個枚舉，如 reasoning effort） | `a-radio-group type="button" size="small"`（分段按鈕） | select 下拉（掃視成本高、與 canonical 不一致） |
| 大集合單選（模型清單、連線清單） | `a-select`（`:options` 或 a-option） | 自刻下拉 |
| 數值微調（temperature 類） | `a-switch`（啟用自訂）＋ `a-slider`＋當前值顯示；有鎖定條件時 switch disabled + 鎖定說明文字 | 裸 input number、無啟用開關的常駐 slider |

- **Canonical 用例＝LLM 旋鈕**（`features/settings/components/LlmConfigEditor.vue`）：Thinking＝`a-switch on/off`、Reasoning effort＝radio-group 分段（值域 SSOT＝`features/settings/constants` 的 `REASONING`，源頭 `config/global/llm_model.json`）、Temperature＝switch＋slider＋`tempLocked`（OpenAI thinking on 鎖 1）。任何頁面出現同語義旋鈕（如 Prompt 調試台）一律鏡射此組合與正規化邏輯（`thinking === 'on' ? 'on' : 'off'`、reasoning 兜底 `medium`），不得自帶第二套值域或另選元件（2026-07-22 Prompt 調試台曾用 select 下拉重做被退回對齊，即本條由來）。
- **值域/選項 SSOT 同源**：對齊 canonical 時連值域一起復用（import 同一 constants），禁止在新頁面手抄枚舉陣列——手抄必 drift。
- **第 2 次出現即評估抽共用元件**（呼應上方佈局拆分準則）：同一組控件組合出現在第 2 個頁面時，優先抽成共用元件（props 注入差異）而非各自複製模板。

## 懶加載 / Code-splitting（預設機制）

首屏不需要的一律延遲載入，縮小初始 bundle（呼應 06 quality-targets：單路由 JS < 200KB gzip）：

- **路由頁元件**：一律 `component: () => import('...')`（route-level splitting）；禁在 route 檔頂靜態 `import 頁面元件`
- **重型第三方庫**（jsoneditor / jspdf / html2canvas 等）：使用點動態載入——元件內 `await import('lib')`（掛載 / 觸發時），型別走 `import type`（編譯期擦除，不進 bundle）；禁 module 頂 import 把大庫壓進頁面 chunk
- **點擊才開的重元件**（modal / 抽屜）：`defineAsyncComponent(() => import('...'))`，不開不載
- **大型共用 vendor**（echarts / arco / vue 全家桶）：於 `vite.config` `build.rollupOptions.output.manualChunks` 拆獨立 chunk（利瀏覽器快取）
- **例外（別 lazy）**：首屏立即渲染必需者（App 殼層 / 登入核心）、體積極小的元件——lazy 反增請求數與閃爍。判準：**首屏就要？是→靜態；否→lazy**
