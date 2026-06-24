// 處理狀態顯示常數：label / color / 篩選選項。

/** 處理狀態 → 中文 label。 */
export const STATUS_LABEL: Record<string, string> = {
  new: '待處理',
  confirmed: '已確認',
  dismissed: '已忽略',
  fixed: '已修',
  data_missing: '缺資料',
};

/** 處理狀態 → Arco tag 顏色。 */
export const STATUS_COLOR: Record<string, string> = {
  confirmed: 'green',
  fixed: 'cyan',
  dismissed: 'gray',
  data_missing: 'red',
  new: 'arcoblue',
};

/** 狀態篩選下拉選項（由 STATUS_LABEL 衍生，避免重複維護）。 */
export const STATUS_OPTS = Object.entries(STATUS_LABEL).map(([k, l]) => ({ k, l }));
