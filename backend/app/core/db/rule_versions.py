"""初判規則版本（RULE_CODES：bd_tag_vertical + source_mapping + prompt_*；append-only 快照）。

檔案＝默認 seed（git 版控、不可變）；DB＝live + 完整歷史；一 rule_code 僅一 active。
- bd_tag_vertical（BD 分工代碼→PM/Vertical，源自 BD 分工表 Google Sheet），seed 放 config/global。
  取代舊制 product_vertical（CATEGORY_xxx→Tour/Exp/Charter/Tix 分組，已於 2026-07-27 全棧退役）。
- source_mapping（上傳表頭校驗 + 欄位映射），seed 放 config/ai_judge，線上編輯即時生效於上傳校驗。
- prompt_polarity + prompt_C-1~6（初判 Prompt，Prompt-as-Source 架構）：初判 prompt 唯一真相源＝
  prompts/*.md，default seed 讀 md 包成 {"_meta":..., "text": md}（見 default_rule_content），
  存檔驗證/drift 護欄委派 app.judge.prompt_source。
註：judgment（顯示標籤 + 信心閾值 + prejudge 旋鈕 + 極性閘門 + 證據政策）為專案靜態設定檔
config/ai_judge/prejudge.json（+verdict.json）（`_shared.read_pipeline_config` 直讀檔案），不進 RULE_CODES、不 DB
版本化 / 不列規則頁。
判準文字由 prompt_C-1~6 的 System 承載、域結構由 app.judge.prompt_source.structure() 派生（非本表）。
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from app.core.db import tables as T
from app.core.paths import AI_JUDGE_DIR as _AI_JUDGE_DIR
from app.core.paths import GLOBAL_DIR as _GLOBAL_DIR

# 本表管理的 rule。初判 Prompt（prompt_*）已於 2026-07-28 遷出至檔案版本庫
# （`app.judge.prompt_versions`），不再列於此——`list_rule_meta` 以本元組過濾，留著會讓已遷出的
# 舊資料列在前端「幽靈重現」。API 層的合法 code 是本元組與 prompt code 的聯集（見 rules.py）。
RULE_CODES = (
    "bd_tag_vertical",
    "source_mapping",
)


def _rule_file(code: str) -> Path:
    """rule_code → 對應默認檔（bd_tag_vertical→config/global，source_mapping→config/ai_judge）。"""
    if (
        code == "bd_tag_vertical"
    ):  # 商品垂直分類屬全域配置，默認 seed 放 config/global（非歸因判準）
        return _GLOBAL_DIR / "bd_tag_vertical.json"
    if (
        code == "source_mapping"
    ):  # 上傳表頭校驗 + 來源欄位映射（上傳流程 SSOT），默認 seed = source_mapping.json
        return _AI_JUDGE_DIR / "source_mapping.json"
    raise ValueError(f"未知 rule code：{code}")


def default_rule_content(code: str) -> dict:
    """讀默認檔內容（恢復默認用）；檔不存在拋 FileNotFoundError。"""
    return json.loads(_rule_file(code).read_text(encoding="utf-8"))


def _jrv():  # 縮寫
    return T.judge_rule_versions


def list_rule_meta() -> list[dict]:
    """列現行 RULE_CODES 的 active 版 meta（rule_code/version/author/note/created_at/label），無 active 者略。

    label 取 `_meta.label`（各 rule content 的顯示名慣例）；
    缺值回 None 由前端 fallback 補（JSONB 路徑抽出，避免拉整份 content）。

    以 RULE_CODES 過濾（非撈全表 active）：非當前版本化的 rule_code 之歷史列仍留在 DB，若不過濾
    會在前端「幽靈重現」——已無管理端點卻仍顯示於清單，令人困惑。
    """
    j = _jrv()
    stmt = (
        select(
            j.c.rule_code,
            j.c.version,
            j.c.author,
            j.c.note,
            j.c.created_at,
            j.c.content["_meta"]["label"].astext.label("label"),
        )
        .where(j.c.is_active.is_(True), j.c.rule_code.in_(RULE_CODES))
        .order_by(j.c.rule_code)
    )
    with T.get_engine().connect() as c:
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_rule_active(code: str) -> dict | None:
    """取某 rule 的 active 版 content（dict）；無則 None。"""
    j = _jrv()
    stmt = select(j.c.content).where(j.c.rule_code == code, j.c.is_active.is_(True))
    with T.get_engine().connect() as c:
        row = c.execute(stmt).first()
    return row[0] if row else None


def get_rule_version(code: str, version: int) -> dict | None:
    """取某 rule 特定版本的 content（diff/恢復用）；無則 None。"""
    j = _jrv()
    stmt = select(j.c.content).where(j.c.rule_code == code, j.c.version == version)
    with T.get_engine().connect() as c:
        row = c.execute(stmt).first()
    return row[0] if row else None


def list_rule_history(code: str) -> list[dict]:
    """列某 rule 全版本（version/author/note/is_active/created_at），新到舊。"""
    j = _jrv()
    stmt = (
        select(j.c.version, j.c.author, j.c.note, j.c.is_active, j.c.created_at)
        .where(j.c.rule_code == code)
        .order_by(j.c.version.desc())
    )
    with T.get_engine().connect() as c:
        return [dict(r) for r in c.execute(stmt).mappings()]


def save_rule_version(code: str, content: dict, note: str = "", author: str = "") -> dict:
    """存新版本（version=max+1）並切為 active（交易內解除前一 active）。回 {rule_code, version}。"""
    j = _jrv()
    with T.get_engine().begin() as c:
        maxv = c.execute(select(func.max(j.c.version)).where(j.c.rule_code == code)).scalar()
        newv = (maxv or 0) + 1
        c.execute(
            sa_update(j)
            .where(j.c.rule_code == code, j.c.is_active.is_(True))
            .values(is_active=False)
        )
        c.execute(
            sa_insert(j).values(
                rule_code=code,
                version=newv,
                content=content,
                note=note,
                author=author,
                is_active=True,
            )
        )
    return {"rule_code": code, "version": newv}


def restore_rule_version(code: str, version: int, author: str = "") -> dict:
    """恢復某歷史版本（複製其 content 為新 active 版）。回 {rule_code, version}；版本不存在拋 ValueError。"""
    content = get_rule_version(code, version)
    if content is None:
        raise ValueError(f"version {version} not found for {code}")
    return save_rule_version(code, content, note=f"恢復自 v{version}", author=author)


def reset_rule_default(code: str, author: str = "") -> dict:
    """恢復默認（讀 config/ai_judge/ 檔內容存為新 active 版）。回 {rule_code, version}。"""
    return save_rule_version(code, default_rule_content(code), note="恢復默認", author=author)


def reset_all_rule_defaults(author: str = "") -> dict:
    """恢復 RuleManager「全部恢復默認」涵蓋的規則為檔案默認，各存為新 active 版（覆蓋當前、
    保留歷史）；不論觸發時當前開著哪一頁，範圍恆一致（使用者 2026-07-24 拍板：全域單一動作）。

    **排除**：bd_tag_vertical（設定抽屜獨立管理）；7 支初判 prompt（2026-07-28 起改存檔案版本庫，
    檔案本身即生效版，「用磁碟檔覆蓋熱編版」這個二元對照已不存在——退回舊內容改用恢復歷史版本）。
    實際涵蓋範圍因此只剩 source_mapping。
    缺默認檔的 code 跳過不中斷，回報於 skipped。

    Returns:
        {reset: [{rule_code, version}, ...], skipped: [code, ...]}（依 RULE_CODES 順序）。
    """
    done: list[dict] = []
    skipped: list[str] = []
    _EXCLUDED = {"bd_tag_vertical"}  # 設定抽屜獨立管理，非「全部恢復默認」範圍
    for code in RULE_CODES:
        if code in _EXCLUDED:
            continue
        try:
            done.append(reset_rule_default(code, author=author))
        except FileNotFoundError:
            skipped.append(code)  # 該 rule 無默認檔 → 跳過
    return {"reset": done, "skipped": skipped}


def seed_rules_from_files() -> dict:
    """初次播種：無任何 DB 版的 rule_code 以默認檔建 version 1 active。回各 code 處理結果。"""
    j = _jrv()
    out: dict[str, str] = {}
    with T.get_engine().connect() as c:
        existing = {r[0] for r in c.execute(select(j.c.rule_code).distinct()).all()}
    for code in RULE_CODES:
        if code in existing:
            out[code] = "skip(existed)"
            continue
        try:
            save_rule_version(
                code, default_rule_content(code), note="seed from file", author="system"
            )
            out[code] = "seeded"
        except FileNotFoundError:
            out[code] = "skip(no file)"
    return out
