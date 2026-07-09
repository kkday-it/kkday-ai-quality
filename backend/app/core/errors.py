"""API 錯誤 code 統一入口：`raise_api_error(code, message)` → HTTPException(detail={code, message})。

前端據 code 對映 i18n key 翻譯（見前端 i18n/apiError.util.ts 的 errorCodeToI18nKey），無對映則回退
message（後端中文）。code 命名＝`DOMAIN.REASON`（如 `AUTH.EMAIL_EXISTS`），對齊前端
locales/zh-TW/errors.json 結構。漸進採用：touch-when-edit，未轉的端點續拋純字串 detail（前端仍相容）。
"""

from __future__ import annotations

from fastapi import HTTPException


def raise_api_error(code: str, message: str, status_code: int = 400) -> None:
    """拋出帶 code 的 API 錯誤（detail={code, message}）。

    Args:
        code: `DOMAIN.REASON` 形式錯誤碼（前端對映 i18n key）。
        message: 後端中文訊息，供前端無 i18n 對映時回退顯示。
        status_code: HTTP 狀態碼（預設 400）。

    Raises:
        HTTPException: detail 為 {code, message} 物件（前端 j() 依此契約解析）。
    """
    # from None：抑制隱式異常鏈（在 except 區塊內呼叫時不把內部例外掛為 __context__，日誌乾淨）。
    raise HTTPException(
        status_code=status_code, detail={"code": code, "message": message}
    ) from None
