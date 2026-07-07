/**
 * 【過渡 re-export】exportBlocksToPdf 本體已下沉 `@/shared/charts/reportPdf`（overview 的
 * ChartModal 也要用，原 deep import 形成 overview→judge 反向依賴）。judge 內部消費端
 * （AttributionOverview 經 `../utils` barrel）不動；3B 收尾後評估改指 shared 並刪本檔。
 */
export * from '@/shared/charts/reportPdf';
