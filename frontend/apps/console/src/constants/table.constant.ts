// a-table 全局共用配置：表格最佳實踐默認 + 分頁 preset（標準 / 含「全部」）。
// 供全局公共元件 TableLayout 內建打底；各表直接 v-bind 亦可。原居 features/judge/constants，
// 因 TableLayout（components 層）需引用而提升至全局（依賴方向：components → constants）。

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

/** 「全部」選項的 sentinel pageSize：選「全部」時以此值作單次撈取上限（等同不分頁）。 */
export const PAGE_SIZE_ALL = 100000;

/**
 * 帶「全部」選項的分頁設定（僅限總量可控的小表使用；萬級大表勿掛，避免一次撈爆）。
 * Arco `pageSizeOptions` 只吃 number[]、label 固定「N / Page」，故改由 `pageSizeProps.options`
 * （透傳 page-size Select 的 props，mergeProps 於內建 options 之後可整組覆蓋）自訂繁中
 * label 與「全部」sentinel。伺服器端分頁再以展開覆蓋 current / pageSize / total。
 */
export const PAGINATION_WITH_ALL = {
  ...ALL_PAGINATION,
  pageSizeProps: {
    options: [
      ...PAGE_SIZE_OPTIONS.map((v) => ({ value: v, label: `${v} 條/頁` })),
      { value: PAGE_SIZE_ALL, label: '全部' },
    ],
  },
};
