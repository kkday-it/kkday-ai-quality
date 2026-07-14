"""判決規則管理端點（RULE_CODES：product_vertical + source_mapping + prompt_* 的 live + 版本化；
judgment 已移出＝專案靜態設定檔，不經此管理）。

檔案＝默認 seed（git 版控）；DB judge_rule_versions＝live + 完整歷史。存檔前依 code 型別驗 content
（product_vertical/source_mapping 各自結構驗、prompt_* 委派 prompt_source.validate 驗 md 三節 +
drift 護欄），不過回 422——DB 永不存非法規則。存檔後 _reload_judge_cache 熱重載對應 loader。
全端點 JWT 守衛。

註：判準走 prompt_C-1~6（不經此端點管理 schema 樹）；global_rule（極性閘門 + 證據政策）走
judgment.json 靜態設定檔，亦不經本端點。
"""

from __future__ import annotations

import json

import jsonschema
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import auth, db
from app.core.permissions import permission_keys, require_permission

router = APIRouter(prefix="/api/judge-rules", tags=["judge-rules"])

_VALID_CODES = set(db.RULE_CODES)


def _reload_judge_cache() -> None:
    """規則寫入後重載 judge loader 快取，使判決／候選分類「菜單」+ judgment 配置（極性閘門/證據
    政策/信心閾值/顯示 label/prejudge 旋鈕）即時反映新規則（對齊 config.py；reload 失敗不阻斷已
    成功的寫入）。"""
    try:
        from app.core import ai_judge, flags, source_mapping
        from app.core.db import _shared
        from app.judge import prejudge, prompt_source

        ai_judge.reload()
        _shared.reload_judgment_cfg()  # 顯示 label + 信心閾值（attribution/export 就地生效）
        prejudge.reload()  # 極性閘門/證據政策/prejudge 旋鈕快取
        flags.reload()  # OpenFeature 判決閾值 cache（auto_accept/jury_*）
        source_mapping.reload()  # 上傳表頭校驗 + 欄位映射（/inbound/validate 即時採新版）
        prompt_source.reload()  # 初判 Prompt md 解析快取（判決引擎即時採新版 prompt）
    except Exception:  # noqa: BLE001  reload 失敗不應吞掉寫入成功事實
        pass


class SaveIn(BaseModel):
    """存檔請求：完整 rule/schema content + 編輯備註。"""

    content: dict
    note: str = ""


def _check_code(code: str) -> None:
    if code not in _VALID_CODES:
        raise HTTPException(status_code=404, detail=f"未知 rule code：{code}")


def _validate(code: str, content: dict) -> None:
    """存檔前驗證：prompt_* 委派 prompt_source、product_vertical 用輕量結構驗、source_mapping
    用自身 schema。不過拋 422。
    """
    if code.startswith("prompt_"):
        # 初判 Prompt（Prompt-as-Source）：content={"_meta":..., "text": md 全文}，非 L1-L3 歸因樹。
        # 委派 prompt_source.validate：三節可解析 + Schema 合法 + {TEXT}/{POLARITY} + 自洽 drift 護欄
        # （facet_catalog 解析出的面向 codes == Schema l2_code enum，且域須在 domains.json 註冊）。
        from app.judge import prompt_source

        text = content.get("text")
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(status_code=422, detail="prompt content 需含 text（md 全文字串）")
        prompt_id = prompt_source.prompt_id_for_rule(code)
        try:
            prompt_source.validate(text, prompt_id)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from None
        return
    if code == "product_vertical":
        groups = content.get("groups")
        if not isinstance(groups, dict):
            raise HTTPException(
                status_code=422,
                detail="product_vertical 需含 groups: {分組名: [CATEGORY 代碼,...]}",
            )
        for name, codes in groups.items():
            if not isinstance(codes, list) or not all(isinstance(c, str) for c in codes):
                raise HTTPException(status_code=422, detail=f"分組「{name}」的代碼須為字串清單")
        order = content.get("group_order")  # 選填：分組顯示順序（jsonb 不保 key 序，故顯式存）
        if order is not None and (
            not isinstance(order, list) or not all(isinstance(g, str) for g in order)
        ):
            raise HTTPException(status_code=422, detail="group_order 須為分組名字串清單")
        return
    if code == "source_mapping":
        # 上傳表頭校驗 + 欄位映射：驗自身 schema（source_mapping.schema.json），非 L1-L3 歸因樹。
        from app.core.paths import AI_JUDGE_DIR

        try:
            sschema = json.loads(
                (AI_JUDGE_DIR / "source_mapping.schema.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return  # 無 schema 檔 → 跳過結構驗（後端仍為最終閘）
        serrs = sorted(
            jsonschema.Draft202012Validator(sschema).iter_errors(content),
            key=lambda e: list(e.path),
        )
        if serrs:
            smsgs = [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in serrs[:8]]
            raise HTTPException(status_code=422, detail={"errors": smsgs, "count": len(serrs)})
        # 指紋唯一性：required_headers 同時是自動辨識指紋，兩來源指紋完全相同會使辨識歧義 → 擋。
        fps = [tuple(sorted(m.get("required_headers", []))) for m in content["sources"].values()]
        if len(fps) != len(set(fps)):
            raise HTTPException(
                status_code=422,
                detail="兩個來源的 required_headers 完全相同——自動辨識將無法區分，請至少保留一個獨有欄",
            )
        return
    if code == "judgment":
        # 判決配置（顯示 label / 信心閾值 / prejudge 旋鈕）非 L1-L3 歸因樹 → 不套歸因 schema；輕量結構驗。
        tiers = content.get("confidence_tiers")
        if not isinstance(tiers, dict) or not all(
            isinstance(tiers.get(k), (int, float)) for k in ("auto_accept", "jury_low", "jury_high")
        ):
            raise HTTPException(
                status_code=422,
                detail="judgment 需含 confidence_tiers（auto_accept / jury_low / jury_high 皆為數值）",
            )
        # auto_confirm（G1 自動確認路由）必驗：QC 若在 JSON 誤刪整塊，下游 _auto_confirm_cfg 會靜默退回
        # 預設 enabled=True，等於「刪一個鍵就重開自動確認、跳過人工複核」——業務行為靜默改變，故存檔前擋。
        ac = content.get("auto_confirm")
        rate = ac.get("audit_sample_rate") if isinstance(ac, dict) else None
        if (
            not isinstance(ac, dict)
            or not isinstance(ac.get("enabled"), bool)
            or not isinstance(rate, (int, float))
            or not (0 <= rate <= 1)
        ):
            raise HTTPException(
                status_code=422,
                detail="judgment 需含 auto_confirm（enabled 為 true/false·audit_sample_rate 為 0~1 數值）——防誤刪靜默重開自動確認",
            )
        return


@router.get("")
def list_rules(user: dict = Depends(auth.get_current_user)) -> list[dict]:
    """列所有判決規則的 active 版 meta（rule_code/version/author/note/created_at）。"""
    return db.list_rule_meta()


# 註：須定義於 `/{code}` GET 之前（雖然路徑段數不同不會衝突，仍比照 reset-default-all 慣例前置）。
@router.get("/product-vertical/resolved")
def get_product_vertical_resolved(user: dict = Depends(auth.get_current_user)) -> dict:
    """取當前生效的商品垂直分類定義（{"groups": {name: [codes]}, "group_order": [name,...]}）；
    讀 product_vertical active 版本，缺版本回空 groups。

    供歸因列表商品垂直分類篩選下拉：顯示分組名、送分組名，CATEGORY 代碼由後端展開。
    group_order＝分組顯示順序（顯式排序欄；jsonb 不保 object key 序，前端以此排列選項）。
    """
    from app.core import product_vertical

    return {"groups": product_vertical.all_groups(), "group_order": product_vertical.group_order()}


# 註：須定義於 `/{code}` GET 之前，否則 "export" 會被當成 code 段被 get_rule 攔截。
@router.post("/export")
def export_rules_xlsx(user: dict = Depends(auth.get_current_user)) -> dict:
    """啟動判決規則導出背景 job → {job_id, filename}（立即回，背景組檔）。

    導出 6 支域 prompt 的面向結構（prompt_source.structure()，每域一分頁，L2 面向代碼/名稱）＋
    global 判決總規範（DB active 版本）；供品控 / PM 離線核對域/面向涵蓋範圍（完整判準文字在各域
    prompt System，請至規則配置頁「初判 Prompt」查看）。改背景 job：與問題列表導出共用 /api/exports
    進度串流 / 停止 / 取檔。
    """
    from app.core import export_jobs, rule_export

    filename = "judge_rules.xlsx"
    job_id = export_jobs.start_export(rule_export.build_rules_workbook_bytes, filename)
    return {"job_id": job_id, "filename": filename}


@router.get("/{code}")
def get_rule(
    code: str, version: int | None = None, user: dict = Depends(auth.get_current_user)
) -> dict:
    """取某 rule 的 active content（或 ?version=N 取特定版）。"""
    _check_code(code)
    content = (
        db.get_rule_version(code, version) if version is not None else db.get_rule_active(code)
    )
    if content is None:
        raise HTTPException(status_code=404, detail="無此版本（或尚未 seed）")
    return {"rule_code": code, "version": version, "content": content}


@router.get("/{code}/history")
def get_history(code: str, user: dict = Depends(auth.get_current_user)) -> list[dict]:
    """某 rule 全版本清單（新到舊）。"""
    _check_code(code)
    return db.list_rule_history(code)


@router.get("/{code}/versions/{version}")
def get_version(code: str, version: int, user: dict = Depends(auth.get_current_user)) -> dict:
    """取特定版本完整 content（diff/恢復用）。"""
    _check_code(code)
    content = db.get_rule_version(code, version)
    if content is None:
        raise HTTPException(status_code=404, detail="無此版本")
    return {"rule_code": code, "version": version, "content": content}


# 註：須定義於 `/{code}` POST 之前，否則會被 save_rule 的 code path 攔截。
@router.post("/reset-default-all")
def reset_default_all(
    user: dict = Depends(require_permission(permission_keys.JUDGE_RULE_MANAGE)),
) -> dict:
    """恢復所有歸因分類（C-N，排除 schema）為檔案默認，各新增一個版本覆蓋當前。

    缺默認檔的 code 由 db 層跳過（回傳 skipped），不視為錯誤。
    """
    res = db.reset_all_rule_defaults(author=user.get("email") or user.get("user_id", ""))
    _reload_judge_cache()
    return res


@router.post("/{code}")
def save_rule(
    code: str,
    body: SaveIn,
    user: dict = Depends(require_permission(permission_keys.JUDGE_RULE_MANAGE)),
) -> dict:
    """存檔（先 jsonschema 驗證 → 新版 active）。"""
    _check_code(code)
    _validate(code, body.content)
    res = db.save_rule_version(
        code, body.content, note=body.note, author=user.get("email") or user.get("user_id", "")
    )
    _reload_judge_cache()
    return res


@router.post("/{code}/restore/{version}")
def restore_rule(
    code: str,
    version: int,
    user: dict = Depends(require_permission(permission_keys.JUDGE_RULE_MANAGE)),
) -> dict:
    """恢復某歷史版本（複製為新 active 版）。"""
    _check_code(code)
    try:
        res = db.restore_rule_version(
            code, version, author=user.get("email") or user.get("user_id", "")
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    _reload_judge_cache()
    return res


@router.post("/{code}/reset-default")
def reset_default(
    code: str,
    user: dict = Depends(require_permission(permission_keys.JUDGE_RULE_MANAGE)),
) -> dict:
    """恢復默認（讀 config/ai_judge/ 檔內容存為新 active 版）。"""
    _check_code(code)
    try:
        res = db.reset_rule_default(code, author=user.get("email") or user.get("user_id", ""))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="默認檔不存在") from None
    _reload_judge_cache()
    return res
