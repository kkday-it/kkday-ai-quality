// 進線（conversations）顯示字典：對話角色/段落 / 分桶 / 處理方 / 行程階段 → 中文標籤與語義色。
// 代碼來自新版 30 欄 SQL 匯出（conversation_full 內 [CHATBOT]/[真人] 段落標記 + ‖/⏎ 分隔、
// bucket、msg_handler_bucket、trip_stage 皆 BigQuery 端預算字面值），純前端顯示對照
// （固定參照、非業務可調），依 config-and-hardcode 決策樹留 feature constants。

/** 進線對話角色代碼（輪次行首 `[ROLE]:` 前綴）→ 顯示標籤；未知代碼原樣顯示。 */
export const DIALOGUE_ROLE_LABELS: Record<string, string> = {
  USER: '旅客',
  KKDAY: 'KKday 客服',
  SUP: '供應商',
  BOT: '機器人',
};

/** 進線對話角色 → Arco tag 色（旅客藍／客服綠／供應商橙／機器人灰，掃視即辨發話方）。 */
export const DIALOGUE_ROLE_COLORS: Record<string, string> = {
  USER: 'arcoblue',
  KKDAY: 'green',
  SUP: 'orange',
  BOT: 'gray',
};

/** 對話段落標記（`[CHATBOT]`＝機器人階段／`[真人]`＝真人客服階段，`‖` 分隔）→ 顯示標籤。 */
export const DIALOGUE_SEGMENT_LABELS: Record<string, string> = {
  chatbot: '機器人客服',
  human: '真人客服',
};

/** 進線分桶 bucket → 顯示標籤（該 session 整體處理路徑）。 */
export const BUCKET_LABELS: Record<string, string> = {
  transferred: '機器人轉真人',
  chatbot_only: '純機器人',
  human_supplier: '真人（供應商）',
  human_kkday: '真人（KKday）',
  human_other: '真人（其他）',
};

/** 進線分桶 → Arco tag 色。 */
export const BUCKET_COLORS: Record<string, string> = {
  transferred: 'orange',
  chatbot_only: 'gray',
  human_supplier: 'purple',
  human_kkday: 'green',
  human_other: 'arcoblue',
};

/** 進線處理方 msg_handler_bucket → 顯示標籤（該 session 由誰對應）。 */
export const MSG_HANDLER_BUCKET_LABELS: Record<string, string> = {
  KKDAY: 'KKday 客服',
  SUPPLIER: '供應商',
};

/** 行程階段 trip_stage → 顯示標籤（售前/售後語義；未知值原樣顯示）。 */
export const INBOUND_TRIP_STAGE_LABELS: Record<string, string> = {
  'Open Date': '未定日期',
  'Pre-trip': '行前',
  'Pre-trip Critical': '行前緊急',
  D0: '出發當日',
  'Post-trip': '行後',
};

/** 進線商品垂直分類 vertical 篩選選項（BigQuery 端預算字面值，已是可讀英文業務類別，不再翻譯；
 *  比照既有 product_vertical 分組選項原樣顯示的慣例）。 */
export const INBOUND_VERTICAL_OPTIONS: string[] = [
  'Tour',
  'Hotel',
  'Flight',
  'Experience',
  'Charter',
  'Tickets',
  'Trans',
  'Airport Transfer',
  'F&B',
  'MICE',
  'COMM',
  'Others',
];
