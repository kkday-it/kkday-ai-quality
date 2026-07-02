// 代碼 → 顯示文案映射：SSOT 為 repo 根 constants/labels/*.json（前後端同源），此處 re-export 為具型別常數。
// 旅客類型展開行用；缺項由呼叫端回退顯示原始代碼。文案來源 kkday-member-ci，非自創。
import travellerTypeLabels from '@constants/labels/traveller_type.constant.json';

/** 旅客類型 traveller_type → 繁中文案（SSOT: constants/labels/traveller_type.constant.json；源自 member-ci TravellerType.php，僅 01~05）。 */
export const TRAVELLER_TYPE_LABELS: Record<string, string> = travellerTypeLabels;
