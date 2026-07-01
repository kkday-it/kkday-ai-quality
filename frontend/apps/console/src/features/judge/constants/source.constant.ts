// 5 反饋來源（對齊 config/ai_judge/source_mapping.json 的 key 與後端 source 值）。
// 上傳改全自動辨識（不再手選），本常數供批次列表 source→label 顯示對照。
export const SOURCES = [
  { value: 'conversations', label: '售前售後進線', hint: 'FreshDesk 工單 + 訂單訊息/chatbot（SQL 匯出）' },
  { value: 'freshdesk_tickets', label: '工單', hint: 'FreshDesk 工單' },
  { value: 'product_reviews', label: '商品評論', hint: '商品評論 CSV/Excel' },
  { value: 'app_feedback', label: 'App 回饋', hint: 'App 內回饋' },
  { value: 'mixpanel_tracker', label: '埋點', hint: 'Mixpanel 訂單頁關懷埋點' },
];

/** source code → 中文 label（批次列表 / 來源欄顯示）。 */
export const SOURCE_LABEL: Record<string, string> = Object.fromEntries(
  SOURCES.map((s) => [s.value, s.label]),
);
