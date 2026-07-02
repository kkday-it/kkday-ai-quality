// 代碼 → 顯示文案映射（純前端 UI 顯示；來源為 kkday-member-ci，非自創文案）。
// 展開行「導覽語言」「旅客類型」用；缺項回退顯示原始代碼。

/**
 * 導覽語言 lang_code → 顯示文案。
 * 來源：kkday-member-ci `application/config/lang.php` 的 `guide_lang_map`；
 * 本地補 zh-cn / zh-hk（該 map 未含，但商品評論資料會出現）。
 */
export const LANG_LABELS: Record<string, string> = {
  'zh-tw': '中文',
  'zh-cn': '简体中文',
  'zh-hk': '繁體中文（香港）',
  ct: '廣東話',
  en: 'English',
  ja: '日本語',
  ko: '한국어',
  th: 'ไทย',
  vi: 'Tiếng Việt',
  bi: 'Bahasa Indonesia',
  de: 'Deutsch',
  es: 'Español',
  fr: 'Français',
  it: 'Italiano',
  pt: 'Português',
  ru: 'русский',
  ar: 'العربية',
};

/**
 * 旅客類型 traveller_type 代碼 → 繁中文案。
 * 來源：kkday-member-ci `src/KKday/B2CWeb/Constants/TravellerType.php`（6 端交叉驗證一致），僅 01~05。
 */
export const TRAVELLER_TYPE_LABELS: Record<string, string> = {
  '01': '情侶',
  '02': '家人',
  '03': '好友',
  '04': '單人旅遊',
  '05': '商務旅客',
};
