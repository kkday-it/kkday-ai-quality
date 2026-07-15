// 進線（conversations）顯示字典：對話角色 / 管道 / 處理方 / 行程階段 → 中文標籤與語義色。
// 代碼來自 SQL 匯出固定格式（[ROLE]: 前綴、conversation_type、msg_handler、trip_stage），
// 純前端顯示對照（固定參照、非業務可調），依 config-and-hardcode 決策樹留 feature constants。

/** 進線對話角色代碼（content 行首 `[ROLE]:` 前綴）→ 顯示標籤；未知代碼原樣顯示。 */
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

/** 進線管道 conversation_type → 顯示標籤。 */
export const INBOUND_CHANNEL_LABELS: Record<string, string> = {
  human: '人工客服',
  chatbot: '機器人',
  chatbot_to_human: '機器人轉人工',
};

/** 進線處理方 msg_handler → 顯示標籤（該 session 由誰對應）。 */
export const INBOUND_HANDLER_LABELS: Record<string, string> = {
  KKDAY: 'KKday 客服',
  SUPPLIER: '供應商',
};

/** 行程階段 trip_stage → 顯示標籤（售前/售後語義；未知值原樣顯示）。 */
export const INBOUND_TRIP_STAGE_LABELS: Record<string, string> = {
  'Pre-trip': '行前',
  'Pre-trip D-3': '行前 D-3',
  'D Day': '出發當日',
  'Post-trip': '行後',
};
