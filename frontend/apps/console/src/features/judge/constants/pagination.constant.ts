// a-table 共用配置：分頁「全部展示」+ 表格最佳實踐默認（列寬可調 / 緊湊 / hover / 框線），供各表 v-bind 套用。

/** 預設每頁筆數。 */
export const DEFAULT_PAGE_SIZE = 20;

/** 每頁筆數可選項。 */
export const PAGE_SIZE_OPTIONS = [10, 20, 50, 100];

/**
 * 「全部展示」標準分頁設定（showTotal / showJumper / showPageSize 全開 + pageSize + options）。
 * a-table 直接 `:pagination="ALL_PAGINATION"`；伺服器端分頁再以展開覆蓋 current/pageSize/total。
 */
export const ALL_PAGINATION = {
  showTotal: true,
  showJumper: true,
  showPageSize: true,
  pageSize: DEFAULT_PAGE_SIZE,
  pageSizeOptions: PAGE_SIZE_OPTIONS,
};

/**
 * a-table 最佳實踐默認（各表 `v-bind="TABLE_DEFAULTS"`，個別 props 可覆蓋）：
 * 拖曳調整列寬 + 緊湊密度 + 列 hover 高亮 + 儲存格框線。stripe 斑馬紋不入默認
 * （與歸因列表傾向背景色衝突），需要的表個別開。
 */
export const TABLE_DEFAULTS = {
  size: 'small' as const,
  columnResizable: true,
  hoverable: true,
  bordered: { cell: true },
};
