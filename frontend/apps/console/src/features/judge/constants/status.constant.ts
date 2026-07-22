// 處理狀態顯示常數：label / color。
// label SSOT＝repo 根 config/ai_judge/verdict.json status_labels（前後端同讀；與 TIER/STAGE 同慣例）。
import verdictCfg from '@config/ai_judge/verdict.json';

/** 處理狀態 → 中文 label。auto_confirmed＝G1 系統自動確認（高信心免人工）；fixed 已撤除。 */
export const STATUS_LABEL: Record<string, string> = verdictCfg.status_labels;

/** 處理狀態 → Arco tag 顏色（純前端展示色）。auto_confirmed 用 lime（與人工 confirmed 綠區隔：系統自動 vs 人工）。 */
export const STATUS_COLOR: Record<string, string> = {
  new: 'arcoblue',
  auto_confirmed: 'lime',
  confirmed: 'green',
  dismissed: 'gray',
};
