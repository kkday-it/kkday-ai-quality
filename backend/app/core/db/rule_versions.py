"""判決規則版本（RULE_CODES：product_vertical + source_mapping + prompt_*；append-only 快照）。

檔案＝默認 seed（git 版控、不可變）；DB＝live + 完整歷史；一 rule_code 僅一 active。
- product_vertical（Tour/Exp/Charter/Tix→CATEGORY 代碼），seed 放 config/global。
- source_mapping（上傳表頭校驗 + 欄位映射），seed 放 config/ai_judge，線上編輯即時生效於上傳校驗。
- prompt_polarity + prompt_C-1~6（初判 Prompt，Prompt-as-Source 架構）：判決 prompt 唯一真相源＝
  docs/prompts/*.md，default seed 讀 md 包成 {"_meta":..., "text": md}（見 default_rule_content），
  存檔驗證/drift 護欄委派 app.judge.prompt_source。
註：judgment（顯示標籤 + 信心閾值 + prejudge 旋鈕 + 極性閘門 + 證據政策）已於 2026-07-13 移出
RULE_CODES，降為專案靜態設定檔 config/ai_judge/judgment.json（_shared.read_judgment_config 直讀
檔案），不再 DB 版本化 / 不列規則頁。同日原 global_rule（極性閘門 polarity_gate + 證據政策
evidence_policy）併入 judgment.json 一併退出 RULE_CODES，減少判決 config 檔案數。
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

RULE_CODES = (
    "product_vertical",
    "source_mapping",
    # 初判 Prompt（Prompt-as-Source 架構）：判決 prompt 唯一真相源＝docs/prompts/*.md，
    # 經此機制 DB 版本化（線上熱編 + 歷史 + 恢復默認）。content={"_meta":..., "text": md 全文}，
    # 非 L1/L2/L3 歸因樹（default seed 讀 md 而非 JSON，見 default_rule_content）。
    "prompt_polarity",
    "prompt_C-1",
    "prompt_C-2",
    "prompt_C-3",
    "prompt_C-4",
    "prompt_C-5",
    "prompt_C-6",
)
# 註：judgment（判決顯示 label + 信心閾值 + prejudge 旋鈕）已於 2026-07-13 移出 RULE_CODES——
# 降為專案靜態設定檔 config/ai_judge/judgment.json（_shared.read_judgment_config 直讀檔案），不再
# DB 版本化 / 不列於規則配置頁。其值皆非 QC 逐條熱調的判準文本，過度工程化為可熱編規則反成干擾。


def _rule_file(code: str) -> Path:
    """rule_code → 對應默認檔（product_vertical→config/global，source_mapping→config/ai_judge）。"""
    if (
        code == "product_vertical"
    ):  # 商品垂直分類屬全域配置，默認 seed 放 config/global（非歸因判準）
        return _GLOBAL_DIR / "product_vertical.json"
    if (
        code == "source_mapping"
    ):  # 上傳表頭校驗 + 來源欄位映射（上傳流程 SSOT），默認 seed = source_mapping.json
        return _AI_JUDGE_DIR / "source_mapping.json"
    raise ValueError(f"未知 rule code：{code}")


def default_rule_content(code: str) -> dict:
    """讀默認檔內容（恢復默認用）；檔不存在拋 FileNotFoundError。

    prompt_*（初判 Prompt）默認 seed 非 JSON 檔，而是 docs/prompts/*.md 原文——委派
    prompt_source.default_prompt_content 讀 md 包成 {"_meta":..., "text": md} 版本化格式。
    """
    if code.startswith("prompt_"):
        from app.judge.prompt_source import default_prompt_content  # lazy：避免頂層循環依賴

        return default_prompt_content(code)
    return json.loads(_rule_file(code).read_text(encoding="utf-8"))


def _jrv():  # 縮寫
    return T.judge_rule_versions


def list_rule_meta() -> list[dict]:
    """列現行 RULE_CODES 的 active 版 meta（rule_code/version/author/note/created_at/label），無 active 者略。

    label 取 `_meta.label`（各 rule content 的顯示名慣例，含 prompt_* 的 default_prompt_content）；
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
    """恢復規則配置頁「整體配置」規則（source_mapping）為檔案默認，各存為新 active 版（覆蓋當前、保留歷史）。

    **排除**（見 RuleManager）：product_vertical（設定抽屜獨立管理）、prompt_*（初判 Prompt，各自獨立
    「恢復默認」；判準文本＝人工調適標的，bulk 不應連帶覆蓋 prompt 手改）。
    缺默認檔的 code 跳過不中斷，回報於 skipped。

    Returns:
        {reset: [{rule_code, version}, ...], skipped: [code, ...]}（依 RULE_CODES 順序）。
    """
    done: list[dict] = []
    skipped: list[str] = []
    # 各有獨立編輯入口·不納入 bulk reset-all；prompt_*（初判 Prompt）另有「初判 Prompt」分組
    # 各自恢復默認，且判準文本＝人工調適標的，bulk「恢復整體配置默認」不應連帶覆蓋 prompt 手改。
    _EXCLUDED = {
        "product_vertical",
        *(c for c in RULE_CODES if c.startswith("prompt_")),
    }
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
