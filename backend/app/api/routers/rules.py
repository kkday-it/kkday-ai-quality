"""判決規則管理端點（config/ai_judge/ 的 7 rule + schema 的 live + 版本化）。

檔案＝默認 seed（git 版控）；DB judge_rule_versions＝live + 完整歷史。存檔前以 active schema
驗 content（jsonschema），不過回 422——DB 永不存非法規則。全端點 JWT 守衛。
"""

from __future__ import annotations

import jsonschema
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import auth, db

router = APIRouter(prefix="/api/judge-rules", tags=["judge-rules"])

_VALID_CODES = set(db.RULE_CODES)


class SaveIn(BaseModel):
    """存檔請求：完整 rule/schema content + 編輯備註。"""

    content: dict
    note: str = ""


def _check_code(code: str) -> None:
    if code not in _VALID_CODES:
        raise HTTPException(status_code=404, detail=f"未知 rule code：{code}")


def _validate_category_groups(content: dict) -> None:
    """category_groups 專屬結構驗證：{"groups": {str: [str, ...]}}；不合法拋 422。

    商品分類分組非歸因分類（不對應 L1/L2/L3 schema 語意），套用該 schema 會誤判 422，
    故獨立輕量結構檢查（不引入額外 schema 檔，符合本次範圍）。
    """
    groups = content.get("groups")
    if not isinstance(groups, dict):
        raise HTTPException(status_code=422, detail="category_groups 內容須含 'groups' dict")
    for name, codes in groups.items():
        if not isinstance(name, str):
            raise HTTPException(status_code=422, detail=f"分組名須為字串：{name!r}")
        if not isinstance(codes, list) or not all(isinstance(c, str) for c in codes):
            raise HTTPException(status_code=422, detail=f"分組「{name}」的代碼須為字串清單")


def _validate(code: str, content: dict) -> None:
    """存檔前驗證：schema 自身用 metaschema、category_groups 用專屬輕量結構檢查、其餘用 active schema。

    不過拋 422。
    """
    if code == "schema":
        try:
            jsonschema.Draft202012Validator.check_schema(content)
        except jsonschema.exceptions.SchemaError as e:
            raise HTTPException(status_code=422, detail=f"schema 不合法：{e.message}") from None
        return
    if code == "category_groups":
        _validate_category_groups(content)
        return
    schema = db.get_rule_active("schema") or db.default_rule_content("schema")
    errs = sorted(
        jsonschema.Draft202012Validator(schema).iter_errors(content),
        key=lambda e: list(e.path),
    )
    if errs:
        msgs = [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in errs[:8]]
        raise HTTPException(
            status_code=422, detail={"errors": msgs, "count": len(errs)}
        )


@router.get("")
def list_rules(user: dict = Depends(auth.get_current_user)) -> list[dict]:
    """列所有判決規則的 active 版 meta（rule_code/version/author/note/created_at）。"""
    return db.list_rule_meta()


# 註：須定義於 `/{code}` GET 之前（雖然路徑段數不同不會衝突，仍比照 reset-default-all 慣例前置）。
@router.get("/category-groups/resolved")
def get_category_groups_resolved(user: dict = Depends(auth.get_current_user)) -> dict:
    """取當前生效的商品分類分組定義（{"groups": {name: [codes]}}）；未設定則回空 groups。"""
    return db.get_rule_active("category_groups") or {"groups": {}}


@router.get("/{code}")
def get_rule(
    code: str, version: int | None = None, user: dict = Depends(auth.get_current_user)
) -> dict:
    """取某 rule 的 active content（或 ?version=N 取特定版）。"""
    _check_code(code)
    content = db.get_rule_version(code, version) if version else db.get_rule_active(code)
    if content is None:
        raise HTTPException(status_code=404, detail="無此版本（或尚未 seed）")
    return {"rule_code": code, "version": version, "content": content}


@router.get("/{code}/history")
def get_history(code: str, user: dict = Depends(auth.get_current_user)) -> list[dict]:
    """某 rule 全版本清單（新到舊）。"""
    _check_code(code)
    return db.list_rule_history(code)


@router.get("/{code}/versions/{version}")
def get_version(
    code: str, version: int, user: dict = Depends(auth.get_current_user)
) -> dict:
    """取特定版本完整 content（diff/恢復用）。"""
    _check_code(code)
    content = db.get_rule_version(code, version)
    if content is None:
        raise HTTPException(status_code=404, detail="無此版本")
    return {"rule_code": code, "version": version, "content": content}


# 註：須定義於 `/{code}` POST 之前，否則會被 save_rule 的 code path 攔截。
@router.post("/reset-default-all")
def reset_default_all(user: dict = Depends(auth.get_current_user)) -> dict:
    """恢復所有歸因分類（C-N，排除 schema）為檔案默認，各新增一個版本覆蓋當前。

    缺默認檔的 code 由 db 層跳過（回傳 skipped），不視為錯誤。
    """
    return db.reset_all_rule_defaults(author=user.get("user_id", ""))


@router.post("/{code}")
def save_rule(
    code: str, body: SaveIn, user: dict = Depends(auth.get_current_user)
) -> dict:
    """存檔（先 jsonschema 驗證 → 新版 active）。"""
    _check_code(code)
    _validate(code, body.content)
    return db.save_rule_version(code, body.content, note=body.note, author=user.get("user_id", ""))


@router.post("/{code}/restore/{version}")
def restore_rule(
    code: str, version: int, user: dict = Depends(auth.get_current_user)
) -> dict:
    """恢復某歷史版本（複製為新 active 版）。"""
    _check_code(code)
    try:
        return db.restore_rule_version(code, version, author=user.get("user_id", ""))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None


@router.post("/{code}/reset-default")
def reset_default(code: str, user: dict = Depends(auth.get_current_user)) -> dict:
    """恢復默認（讀 config/ai_judge/ 檔內容存為新 active 版）。"""
    _check_code(code)
    try:
        return db.reset_rule_default(code, author=user.get("user_id", ""))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="默認檔不存在") from None
