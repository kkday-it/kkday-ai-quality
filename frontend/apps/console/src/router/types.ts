// vue-router RouteMeta 型別擴充：集中宣告本專案用到的 meta 欄位（單一真相）。
import 'vue-router';

declare module 'vue-router' {
  interface RouteMeta {
    /** 公開頁（免登入即可進，如 /login） */
    public?: boolean;
    /** 路由顯示文字（頂部視圖 tab 用）；有此欄位的子路由才會出現在 FeatureTabs */
    text?: string;
    /**
     * 進此路由所需的 business-key 權限（全具備才放行，缺任一導回首頁）。
     * 由可替換權限框架的 router 守衛強制（見 router/index.ts）；現無路由宣告，框架就緒待用。
     */
    permissions?: string[];
  }
}
