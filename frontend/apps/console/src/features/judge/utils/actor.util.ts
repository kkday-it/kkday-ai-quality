/**
 * 稽核身分（create_user / triggered_by / author）的顯示轉換。
 *
 * 後端在 SSO 接入前一律記 `system`（沒有經過驗證的身分就不假裝有人，見 app/core/auth.py
 * SYSTEM_USER）。顯示層**原樣顯示 `system` 不做中文化**——全站（歸因歷史 / 初判紀錄 /
 * Prompt 版本）統一同一個字面值，與 DB 值、API 回傳值三者一致，查問題時不必在
 * 「系統」與 `system` 之間對照。be2 SSO 接入後身分是真實 email，同樣原樣顯示。
 *
 * 本函式唯一的職責是把「空值」正規化為 `system`：舊資料的 NULL／空字串在語義上就是系統操作。
 */

/** 後端的系統身分標記（與 app/core/auth.py 的 SYSTEM_USER 對齊）。 */
export const SYSTEM_ACTOR = 'system';

/**
 * 身分字串 → 顯示文字。
 *
 * @param actor 後端回的身分值（`system` 或未來 SSO 的真實 email；空值視同系統）。
 * @returns 原值；空值回 `system`。
 */
export const formatActor = (actor?: string | null): string => actor?.trim() || SYSTEM_ACTOR;
