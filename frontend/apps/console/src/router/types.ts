// vue-router RouteMeta 型別擴充：集中宣告本專案用到的 meta 欄位（單一真相）。
import 'vue-router';

declare module 'vue-router' {
  interface RouteMeta {
    /** 公開頁（免登入即可進，如 /login） */
    public?: boolean;
    /** 路由顯示文字（頂部視圖 tab 用）；有此欄位的子路由才會出現在 FeatureTabs */
    text?: string;
  }
}
