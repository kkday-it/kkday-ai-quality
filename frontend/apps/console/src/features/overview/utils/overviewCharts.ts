/**
 * 【過渡 re-export】builders 本體已下沉 `@/shared/charts/builders`（被 judge/usage 共用，
 * 消除 feature 間反向依賴）。overview 內部消費端（EngineCard / NorthStarCard / chartRegistry）
 * 沿用 `../utils` 不動；待 AttributionList 拆分（3B）收尾後評估直接改指 shared 並刪本檔。
 */
export * from '@/shared/charts/builders';
