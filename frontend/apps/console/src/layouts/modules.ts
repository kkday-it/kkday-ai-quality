// 頂層功能模組註冊表：topbar 下拉切換的單一真相。
// 每個模組綁定 home 路由 + 路徑前綴 + 視圖 tab；新增模組只需在此追加一筆，殼層自動處理導航與 tab 高亮。
import { JUDGE_TABS } from '@/features/judge/routes';
import { OVERVIEW_TABS } from '@/features/overview/routes';

export interface AppModule {
  /** 模組唯一鍵 */
  value: string;
  /** 下拉顯示標籤（含 emoji） */
  label: string;
  /** 切換後導向的首頁路由 */
  home: string;
  /** 用於依當前路由反查 active 模組的路徑前綴 */
  prefix: string;
  /** 該模組的視圖 tab（空集合＝單頁模組，殼層隱藏 tab 列） */
  tabs: ReadonlyArray<{ key: string; label: string }>;
}

/** 縱覽置首（整個 AI 質檢的鳥瞰），AI 法官為其下一環。 */
export const MODULES: ReadonlyArray<AppModule> = [
  { value: 'overview', label: '📊 質檢概覽', home: '/overview', prefix: '/overview', tabs: OVERVIEW_TABS },
  { value: 'ai-judge', label: '⚖️ AI 法官', home: '/judge', prefix: '/judge', tabs: JUDGE_TABS },
];

/**
 * 依當前路由路徑反查所屬模組（前綴匹配）；無匹配時退回第一個模組。
 * @param path 當前 route.path
 */
export function moduleByPath(path: string): AppModule {
  return MODULES.find((m) => path.startsWith(m.prefix)) ?? MODULES[0];
}
