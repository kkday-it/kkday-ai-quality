// 表格共用配置已提升至全局 `@/constants/table.constant`（TableLayout 內建打底需引用，
// components 層不得反向依賴 feature 層）。此檔僅保留 re-export 維持既有 import 路徑相容。
export * from '@/constants/table.constant';
