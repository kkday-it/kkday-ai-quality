// 內容維度常數：8 大維度直接復用 @aiq/types 契約，避免與前後端 schema 脫節。
import { DIMENSIONS } from '@aiq/types';

/** 8 大內容維度（heatmap 縱軸 / 篩選用），來源為 @aiq/types 單一真相。 */
export const DIMS = DIMENSIONS;
