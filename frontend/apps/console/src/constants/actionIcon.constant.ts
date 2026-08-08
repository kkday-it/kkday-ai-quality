/**
 * 動作 → icon 的全站對照表（SSOT）。
 *
 * **同一個動詞在全站永遠是同一個圖示**——「儲存」在設定面板和 Prompt 編輯器不該是兩個不同的圖示。
 * 沒有這張表的話，每個人寫元件時各自挑一個看起來像的，半年後同一個動作會有三種圖示，
 * 而使用者是靠圖示形狀在掃描介面的。
 *
 * ## 什麼時候該加 icon（判準，不是「全部都加」）
 *
 * | 情境 | 加不加 | 為什麼 |
 * |---|---|---|
 * | 工具列動作、卡片動作列 | **加** | 一排並列的動作，圖示讓人不必逐字讀就分得出來 |
 * | 表格 per-row 操作 | **加** | 同一組按鈕每列重複，圖示是掃描時的定位點 |
 * | 破壞性 / 不可逆動作 | **加** | 多一層視覺確認，降低誤點 |
 * | 抽屜／彈窗 footer 的「取消 / 確定」 | **不加** | 位置（右下）與 primary 樣式已經表達語義，加圖示只是視覺重量 |
 * | 分頁器、tab、radio-group 型切換 | **不加** | 那是導覽不是動作 |
 * | 同一個容器內超過 6 顆按鈕 | **重新設計** | 不是靠圖示救，是該收斂或分組 |
 *
 * ## 用法
 *
 * ```vue
 * import { IconRobot } from '@arco-design/web-vue/es/icon';
 * <a-button><template #icon><icon-robot /></template>初判分類</a-button>
 * ```
 *
 * 本表是「查表用」的文件而非執行期常數——Arco 的 icon 要靜態 import 才能 tree-shake，
 * 動態查表反而會把 500+ 個圖示全打進 bundle。
 */

/** 動作語義 → Arco icon 元件名。新增動作時**先查這張表有沒有同義的**，有就沿用，別另挑一個。 */
export const ACTION_ICONS = {
  // ── 判定與執行 ──────────────────────────────────────────────────────────
  /** AI 產出的判定：初判分類 / 判決歸因（批量與單列同一個——是同一動作的兩種範圍）。 */
  aiJudge: 'IconRobot',
  /** 執行 / 開始跑：開始裁決、開始回歸、跑批。 */
  run: 'IconPlayArrow',
  /** 暫停（可續跑）。 */
  pause: 'IconPauseCircle',
  /** 停止（不可續）。 */
  stop: 'IconRecordStop',
  /** 重跑既有的一批：重跑、重新初判本批失敗筆。與 `run`（首次執行）分開——重跑帶「再來一次」語義。 */
  rerun: 'IconSync',
  /** 測試 / 探測：測試連線、探測模型能力。 */
  test: 'IconExperiment',
  /** AI 輔助產出（非判定）：AI 定點改寫、產出補丁。與 `aiJudge` 分開——那是「判定」，這是「生成建議」。 */
  aiAssist: 'IconBulb',
  /** 套用已產出的結果：套用勾選補丁、確認匯入。 */
  apply: 'IconCheck',

  // ── 檢視 ───────────────────────────────────────────────────────────────
  /** 資料本身的詳情：反饋詳情。 */
  detail: 'IconFile',
  /** 時間軸 / 歷次紀錄：初判歷史、判決歷史、人工歷史、執行紀錄、版本歷史。 */
  history: 'IconHistory',
  /** 開啟一份清單：版本列表。 */
  list: 'IconList',
  /** 原始日誌 / 逐行輸出：查看 LLM 日誌。 */
  log: 'IconCode',
  /** 放大 / 全螢幕看內容。 */
  zoom: 'IconZoomIn',
  /** 兩份內容對比：版本對比、補丁 diff。 */
  diff: 'IconSwap',
  /** 外部連結（會離開本站）：開啟來源儀表板。**用 icon 取代文案裡的 ↗**，不要兩個都放。 */
  external: 'IconLaunch',
  /** 一般「查看」：查看原因、查看待審建議。比 `detail`（資料本身的完整內容）輕。 */
  view: 'IconEye',
  /** 回上一步 / 回到上層視角：回上一步調整補丁、回到單筆視角。 */
  back: 'IconArrowLeft',

  // ── 編輯 ───────────────────────────────────────────────────────────────
  /** 改既有值：人工糾正、編輯選項。 */
  edit: 'IconEdit',
  /** 新增一筆：新增選項、新增遺漏歸因、存為新草稿。 */
  create: 'IconPlus',
  /** 存檔（覆蓋現值）。 */
  save: 'IconSave',
  /** 複製到剪貼簿。 */
  copy: 'IconCopy',
  /** 送出一則內容（不是表單確認）：送出備註。 */
  send: 'IconSend',
  /** 收藏成可重用的素材：存為案例。 */
  collect: 'IconBookmark',
  /** 開啟設定頁／面板：管理 LLM 設定。 */
  settings: 'IconSettings',

  // ── 狀態變更 ───────────────────────────────────────────────────────────
  /** 確認正確 / 標對：複審通過。 */
  approve: 'IconCheckCircle',
  /** 標記為錯 / 駁回 / 標記誤判。 */
  reject: 'IconCloseCircle',
  /** 還原 / 撤銷到先前狀態：還原歸因、恢復此版本、恢復默認。 */
  restore: 'IconUndo',
  /** 升級 / 發布：升為正式版。 */
  promote: 'IconToTop',
  /** 把某一版設為當前生效：設為使用中。 */
  activate: 'IconCheckCircle',
  /** 刪除（真的刪掉，非 tombstone）。 */
  remove: 'IconDelete',

  // ── 資料進出 ───────────────────────────────────────────────────────────
  /** 匯出 / 下載檔案。 */
  export: 'IconDownload',
  /** 匯入 / 上傳檔案。 */
  import: 'IconUpload',
  /** 重新載入當前資料（不改變任何值）。 */
  reload: 'IconRefresh',

  // ── 選取與重置（篩選列、批次操作列）────────────────────────────────────
  //
  // 這三個是**不同的動作**，不要合成一個「clear」：選取是加、清除是減、重置是回到起點。
  // 曾經只定義一個 `clear: 'IconClose'`——但 `IconClose` 在使用者眼裡是「關閉」不是「清除」，
  // 而且無法區分「清掉已選的項目」與「把篩選條件退回預設」。
  /** 批次選取：選取分頁、全選。 */
  selectBatch: 'IconSelectAll',
  /** 清除既有的選取或標記：清除選擇、清除標記、清空輸入框。 */
  clearSelection: 'IconEraser',
  /** 退回初始狀態：重置篩選。與 `restore`（撤銷某個具體動作）刻意用不同圖示——
   *  「重置」是回到預設值，「還原」是把某一筆改回去，兩者的後果範圍不同。 */
  reset: 'IconRotateLeft',
} as const;

export type ActionIconKey = keyof typeof ACTION_ICONS;
