// 資料上傳來源（菜單）。售前售後進線為第一管道，預設選中。
export const SOURCES = [
  { value: 'presale_postsale', label: '售前售後進線', hint: 'FreshDesk 工單 + 訂單訊息/chatbot（SQL 匯出）' },
  { value: 'review', label: '商品評論', hint: '商品差評 CSV/Excel' },
  { value: 'ticket', label: '工單', hint: '其他工單來源' },
  { value: 'manual', label: '其他 / 通用', hint: '含 prod_oid + comment 欄位的通用檔' },
];
