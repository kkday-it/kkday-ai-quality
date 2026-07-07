/**
 * 把一組 DOM 區塊 rasterize 後組成多頁 PDF 報告。
 *
 * 設計重點：
 *   - **規避 CJK 字型問題**：jsPDF 內建字型不含中日韓字，`pdf.text('中文')` 會缺字；
 *     故所有文字（含報告頭）一律以 html2canvas 轉成圖再 addImage，無須嵌入大型字型檔。
 *   - **重套件 lazy import**：jspdf / html2canvas 僅在使用者按下導出時動態載入，不進主 bundle。
 *   - 通用：吃任意 `HTMLElement[]`，故 Analytics 等其他含圖表頁亦可複用。
 */

/** 報告頭 meta。 */
export type ReportMeta = {
  /** 報告標題。 */
  title: string;
  /** 產生時間（呼叫端格式化好的字串）。 */
  generatedAt: string;
  /** 當前套用的篩選描述；空陣列＝未套用。 */
  filters: string[];
  /** 附加摘要行（如「共 N 筆 · 篩後 M」）。 */
  summary?: string;
};

/**
 * 建立離屏報告頭 DOM 節點（CJK 文字以圖呈現，避免 jsPDF 缺字）。
 * 動態值一律以 textContent 注入（非 innerHTML），杜絕 XSS——即便未來 filters 含商品名等外部資料亦安全。
 * @param meta 報告頭資訊
 */
function buildHeaderEl(meta: ReportMeta): HTMLElement {
  const el = document.createElement('div');
  el.style.cssText =
    'width:1100px;padding:20px 24px;background:#fff;box-sizing:border-box;' +
    'font-family:-apple-system,"PingFang TC","Microsoft JhengHei",sans-serif;color:#1d2129;';

  /** 建一行 div：字級 / 顏色 / 上距，文字以 textContent 安全注入。 */
  const line = (text: string, css: string): HTMLDivElement => {
    const d = document.createElement('div');
    d.style.cssText = css;
    d.textContent = text;
    return d;
  };

  el.appendChild(line(meta.title, 'font-size:22px;font-weight:700;'));
  el.appendChild(line(`產生時間：${meta.generatedAt}`, 'margin-top:6px;font-size:12px;color:#86909c;'));
  if (meta.summary) el.appendChild(line(meta.summary, 'margin-top:2px;font-size:12px;color:#86909c;'));
  const filters = meta.filters.length ? meta.filters.join('　·　') : '全部（未套用篩選）';
  el.appendChild(line(`當前篩選：${filters}`, 'margin-top:8px;font-size:12px;color:#4e5969;line-height:1.6;'));
  return el;
}

/** PDF 導出進度 / 取消掛鉤（前端逐區塊回報；PDF 為客戶端生成，無後端 job）。 */
export type PdfExportHooks = {
  /** 每完成一區塊回報進度（done, total）；total＝blocks.length + 1（含報告頭）。 */
  onProgress?: (done: number, total: number) => void;
  /** 每區塊 rasterize 前輪詢；回 true 即中止（不存檔），exportBlocksToPdf 回 false。 */
  shouldCancel?: () => boolean;
};

/**
 * 將指定 DOM 區塊依序導出為單一多頁 PDF（A4 直式）。
 * @param blocks 依視覺順序排列的可導出區塊（各面板卡片 / KPI 區）
 * @param meta 報告頭資訊
 * @param fileName 下載檔名（含 .pdf）
 * @param hooks 進度回報 / 取消輪詢（可選；供實時進度條與停止按鈕，與後端導出 job 體驗一致）
 * @returns 完成存檔回 true；中途被 shouldCancel 中止回 false（未存檔）
 * @throws {Error} html2canvas / jsPDF 載入或渲染失敗時拋出，由呼叫端提示
 */
export async function exportBlocksToPdf(
  blocks: HTMLElement[],
  meta: ReportMeta,
  fileName: string,
  hooks: PdfExportHooks = {},
): Promise<boolean> {
  const [{ jsPDF }, { default: html2canvas }] = await Promise.all([import('jspdf'), import('html2canvas')]);
  // compress: 對 PDF 內容流做 zlib 壓縮（再省一截）。
  const pdf = new jsPDF({ unit: 'pt', format: 'a4', compress: true });
  const pageW = pdf.internal.pageSize.getWidth();
  const pageH = pdf.internal.pageSize.getHeight();
  const margin = 28;
  const contentW = pageW - margin * 2;
  let y = margin;

  /**
   * 把單一 DOM 節點轉圖、依剩餘高度自動換頁後置入 PDF。
   * 影像以 **JPEG(q0.92)** 而非 PNG 嵌入：圖表多色漸層的 PNG 體積極大（整份可達數十 MB），
   * 改 JPEG 通常省 10–20 倍且白底圖表文字仍清晰；scale 1.6 兼顧清晰度與體積。
   */
  const place = async (el: HTMLElement): Promise<void> => {
    const canvas = await html2canvas(el, { scale: 1.6, backgroundColor: '#ffffff', useCORS: true, logging: false });
    let w = contentW;
    let h = (canvas.height / canvas.width) * w;
    const maxH = pageH - margin * 2;
    // 單塊比整頁還高 → 等比縮到單頁高（letterbox），保證不溢出
    if (h > maxH) {
      h = maxH;
      w = (canvas.width / canvas.height) * h;
    }
    if (y + h > pageH - margin) {
      pdf.addPage();
      y = margin;
    }
    pdf.addImage(canvas.toDataURL('image/jpeg', 0.92), 'JPEG', margin, y, w, h);
    y += h + 14;
  };

  // 報告頭（離屏節點 → 圖 → 用完移除）
  const header = buildHeaderEl(meta);
  header.style.position = 'fixed';
  header.style.left = '-99999px';
  header.style.top = '0';
  document.body.appendChild(header);
  // 進度總量＝報告頭 + 各區塊；每 rasterize 一塊前先輪詢取消，取消則不存檔、回 false。
  const total = blocks.length + 1;
  let done = 0;
  const report = () => hooks.onProgress?.(done, total);
  report(); // 起始 0/total（進度條由「準備中」轉可見）
  try {
    if (hooks.shouldCancel?.()) return false;
    await place(header);
    done += 1;
    report();
    for (const el of blocks) {
      if (hooks.shouldCancel?.()) return false;
      await place(el);
      done += 1;
      report();
    }
  } finally {
    document.body.removeChild(header);
  }

  pdf.save(fileName);
  return true;
}
