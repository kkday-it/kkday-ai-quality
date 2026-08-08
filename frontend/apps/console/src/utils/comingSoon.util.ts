import { Modal } from '@arco-design/web-vue';

/**
 * 「即將推出」佔位提示——尚未實作的功能入口統一走這裡。
 *
 * 三條配套規則（本專案原本沒有這個慣例，這裡一次立起來）：
 *
 * 1. **佔位按鈕不 disable，維持正常視覺權重**。disabled 的按鈕點不下去就不會有任何回饋，
 *    使用者只會看到一顆死按鈕，分不出是壞了還是還沒做。要能點、點了要解釋。
 * 2. **用 `Modal.info` 不違反 Drawer-first 鐵律**。那條鐵律禁的是**內容型**彈出層（承載表單／
 *    明細／流程）；純文案 + 單一 OK 的訊息型 Modal 與 `Message`／`Notification` 同族。
 * 3. **文案禁用刪節號**（「即將推出」而非「即將推出…」），對齊 2026-07-30 拍板的動作標籤規則。
 *
 * @param feature 功能名稱（會嵌進標題與內文）。
 * @param detail 補充說明；建議寫「現在可以先做什麼」，而不只是「還沒做」。
 */
export const notifyComingSoon = (feature: string, detail?: string): void => {
  Modal.info({
    title: '即將推出',
    content: detail ?? `「${feature}」功能正在開發中，流程節點已就緒，稍後開放。`,
    okText: '知道了',
    hideCancel: true,
  });
};
