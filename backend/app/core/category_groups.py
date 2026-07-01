"""商品分類分組（Tour/Exp/Charter/Tix 對照表）：讀取 category_groups 判決規則的薄封裝。

分組定義走既有規則版本化機制（config/ai_judge/rule_category_groups.json 為 seed 檔，
DB judge_rule_versions 存 live + 歷史，見 db.RULE_CODES），QC 可於前端規則面板調整而不需
改碼發版——本模組只負責把 db.get_rule_active('category_groups') 的原始 dict 轉成
好用的 {group_name: [codes]} 形態，不重複儲存資料（單一真相源＝DB active 版）。

本模組 import `app.core.db`（單向）；db.py 不 import 本模組，故無循環依賴疑慮
（db.py 檔頭已註解其刻意不 import settings 以避免循環，這裡是相反方向的單向依賴，安全）。
"""

from __future__ import annotations

from app.core import db

_RULE_CODE = "category_groups"


def all_groups() -> dict[str, list[str]]:
    """取當前生效的商品分類分組定義 {group_name: [category_code, ...]}。

    Returns:
        分組 dict；尚未設定（無 active 版）時回空 dict，不拋錯（呼叫端安全兜底）。
    """
    content = db.get_rule_active(_RULE_CODE)
    if not content:
        return {}
    return content.get("groups", {}) or {}


def codes_for_group(group: str) -> list[str]:
    """取某分組（Tour/Exp/Charter/Tix）對應的商品分類代碼清單。

    Args:
        group: 分組名稱（如 'Tour'）。

    Returns:
        該分組的 CATEGORY_xxx 代碼清單；分組不存在回空 list。
    """
    return all_groups().get(group, [])
