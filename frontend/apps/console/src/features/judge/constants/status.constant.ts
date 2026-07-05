// 處理狀態顯示常數：label / color / 篩選選項。

/** 處理狀態 → 中文 label。auto_confirmed＝G1 系統自動確認（高信心免人工）。 */
export const STATUS_LABEL: Record<string, string> = {
  new: '待處理',
  auto_confirmed: '自動確認',
  confirmed: '已確認',
  dismissed: '已忽略',
  fixed: '已修',
};

/** 處理狀態 → Arco tag 顏色。auto_confirmed 用 lime（與人工 confirmed 綠區隔：系統自動 vs 人工）。 */
export const STATUS_COLOR: Record<string, string> = {
  new: 'arcoblue',
  auto_confirmed: 'lime',
  confirmed: 'green',
  fixed: 'cyan',
  dismissed: 'gray',
};

/** 狀態篩選下拉選項（由 STATUS_LABEL 衍生，避免重複維護）。 */
export const STATUS_OPTS = Object.entries(STATUS_LABEL).map(([k, l]) => ({ k, l }));
