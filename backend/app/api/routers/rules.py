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


def _validate(code: str, content: dict) -> None:
    """存檔前驗證：schema 自身用 metaschema、規則檔用 active schema。不過拋 422。"""
    if code == "schema":
        try:
            jsonschema.Draft202012Validator.check_schema(content)
        except jsonschema.exceptions.SchemaError as e:
            raise HTTPException(status_code=422, detail=f"schema 不合法：{e.message}") from None
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
