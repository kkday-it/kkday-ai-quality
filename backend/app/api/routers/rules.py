"""初判規則管理端點（RULE_CODES：bd_tag_vertical + source_mapping + prompt_* 的 live + 版本化；
judgment 已移出＝專案靜態設定檔，不經此管理）。

檔案＝默認 seed（git 版控）；DB judge_rule_versions＝live + 完整歷史。存檔前依 code 型別驗 content
（bd_tag_vertical/source_mapping 各自結構驗、prompt_* 委派 prompt_source.validate 驗 md 三節 +
drift 護欄），不過回 422——DB 永不存非法規則。存檔後 _reload_judge_cache 熱重載對應 loader。
全端點 JWT 守衛。

註：判準走 prompt_C-1~6（不經此端點管理 schema 樹）；global_rule（極性閘門 + 證據政策）走
prejudge.json/verdict.json 靜態設定檔，亦不經本端點。
"""

from __future__ import annotations

import json

import jsonschema
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import auth, db
from app.core.permissions import permission_keys, require_permission
from app.judge import prompt_rule_service as prs
from app.judge import prompt_source

router = APIRouter(prefix="/api/judge-rules", tags=["judge-rules"])

# 合法 rule code＝DB 表管理的（bd_tag_vertical / source_mapping）∪ 檔案版本庫管理的 7 支 prompt。
# 兩套儲存各有自己的 code 清單，API 層是唯一需要看到聯集的地方。
_VALID_CODES = set(db.RULE_CODES) | set(prompt_source.PROMPT_RULE_CODES)


def _reload_judge_cache() -> None:
    """規則寫入後重載 judge loader 快取，使初判／候選分類「菜單」+ judgment 配置（極性閘門/證據
    政策/信心閾值/顯示 label/prejudge 旋鈕）即時反映新規則（對齊 config.py；reload 失敗不阻斷已
    成功的寫入）。"""
    try:
        from app.core import ai_judge, flags, source_mapping
        from app.core.db import _shared
        from app.judge import prejudge, prompt_source

        ai_judge.reload()
        _shared.reload_pipeline_cfg()  # 顯示 label + 信心閾值（attribution/export 就地生效）
        prejudge.reload()  # 極性閘門/證據政策/prejudge 旋鈕快取
        flags.reload()  # OpenFeature 初判閾值 cache（auto_accept/jury_*）
        source_mapping.reload()  # 上傳表頭校驗 + 欄位映射（/inbound/validate 即時採新版）
        prompt_source.reload()  # 初判 Prompt md 解析快取（初判引擎即時採新版 prompt）
    except Exception:  # noqa: BLE001  reload 失敗不應吞掉寫入成功事實
        pass


def _db_version(version: str) -> int:
    """非 prompt 的 rule 版本仍是整數；路由統一收字串後在此轉換。"""
    try:
        return int(version)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"版本號需為整數：{version}") from None


class SaveIn(BaseModel):
    """存檔請求：完整 rule/schema content + 編輯備註。

    `expected_base_version` 僅 prompt_* 使用：呼叫端編輯時看到的生效版本名，與當前不符即 409。
    這是防 lost update 的唯一依據（見 `app.judge.prompt_versions` 模組 docstring）——舊的 DB
    路徑沒有這道檢查，是既有的潛在缺陷，改檔案版本庫時一併補上。
    """

    content: dict
    note: str = ""
    expected_base_version: str | None = None


class DraftIn(BaseModel):
    """草稿寫入請求：完整 content（{_meta, text}）＋分叉基準版本（stale 偵測用）。"""

    content: dict
    base_version: str


class ValidateIn(BaseModel):
    """dry-run 驗證請求：prompt md 全文（不落庫）。"""

    text: str


def _check_code(code: str) -> None:
    if code not in _VALID_CODES:
        raise HTTPException(status_code=404, detail=f"未知 rule code：{code}")


def _check_prompt_code(code: str) -> None:
    """草稿／dry-run 驗證端點僅服務初判 Prompt（prompt_*）——其餘 rule 無草稿概念。"""
    if code not in _VALID_CODES or not code.startswith("prompt_"):
        raise HTTPException(status_code=404, detail=f"非初判 Prompt rule code：{code}")


def _validate(code: str, content: dict) -> None:
    """存檔前驗證：prompt_* 委派 prompt_source、bd_tag_vertical 用輕量結構驗、source_mapping
    用自身 schema。不過拋 422。
    """
    if code.startswith("prompt_"):
        # 初判 Prompt（Prompt-as-Source）：content={"_meta":..., "text": md 全文}，非 L1-L3 歸因樹。
        # 委派 prompt_source.validate：三節可解析 + Schema 合法 + {TEXT}/{POLARITY} + 自洽 drift 護欄
        # （域 prompt 的 `## Taxonomy` 可解析、至少一 facet；enum 由 taxonomy 派生）。
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
    if code == "bd_tag_vertical":
        items = content.get("items")
        if not isinstance(items, dict):
            raise HTTPException(
                status_code=422,
                detail="bd_tag_vertical 需含 items: {bd_tag代碼: {note, pm, vertical}}",
            )
        for bd_code, v in items.items():
            if not isinstance(v, dict):
                raise HTTPException(status_code=422, detail=f"代碼「{bd_code}」的值須為物件")
            for key in ("pm", "vertical"):
                if not isinstance(v.get(key), str) or not v[key]:
                    raise HTTPException(
                        status_code=422, detail=f"代碼「{bd_code}」缺少必填欄位 {key}（字串）"
                    )
            if "note" in v and not isinstance(v.get("note"), str):
                raise HTTPException(status_code=422, detail=f"代碼「{bd_code}」的 note 須為字串")
        # pms/verticals：獨立可配置選項池（選填；缺省時 loader 各自安全回退，見 bd_tag_vertical.py）
        for pool_key in ("pms", "verticals"):
            pool = content.get(pool_key)
            if pool is not None and (
                not isinstance(pool, list) or not all(isinstance(x, str) and x for x in pool)
            ):
                raise HTTPException(status_code=422, detail=f"{pool_key} 須為非空字串清單")
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
        # 初判配置（顯示 label / 信心閾值 / prejudge 旋鈕）非 L1-L3 歸因樹 → 不套歸因 schema；輕量結構驗。
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
    """列所有初判規則的當前版 meta（rule_code/version/author/note/created_at）。

    兩路合併：7 支初判 prompt 走檔案版本庫，其餘 rule_code 仍在 judge_rule_versions 表。
    """
    return sorted(prs.list_meta() + db.list_rule_meta(), key=lambda r: r["rule_code"])


# 註：須定義於 `/{code}` GET 之前（雖然路徑段數不同不會衝突，仍比照 reset-default-all 慣例前置）。
@router.get("/bd-tag-vertical/resolved")
def get_bd_tag_vertical_resolved(user: dict = Depends(auth.get_current_user)) -> dict:
    """取當前生效的商品垂直分類定義（{"verticals": [name,...], "items": {code: {note,pm,vertical}}}）；
    讀 bd_tag_vertical active 版本，缺版本回空。

    供歸因列表商品垂直分類篩選下拉：verticals 為去重排序後的 Vertical 名稱清單，bd_tag 代碼展開
    由後端 _shared._vertical_codes 處理，前端只需顯示/送出 Vertical 名稱。items 供設定頁表格編輯器
    對照顯示（bd_tag 代碼 → note/PM，避免前端再拉一次原始 rule content）。
    """
    from app.core import bd_tag_vertical

    return {"verticals": bd_tag_vertical.all_verticals(), "items": bd_tag_vertical.all_items()}


# 註：須定義於 `/{code}` GET 之前，否則 "drafts" 會被當成 code 段被 get_rule 攔截。
@router.get("/drafts")
def list_drafts(user: dict = Depends(auth.get_current_user)) -> list[dict]:
    """列所有存在草稿的 prompt（rule_code/base_version/updated_by/updated_at，不含 content）——
    供沙盒版本選擇器一次拉取草稿存在狀態，免逐 code 輪詢。"""
    return prs.list_drafts()


# 註：須定義於 `/{code}` GET 之前，否則 "export" 會被當成 code 段被 get_rule 攔截。
@router.post("/export")
def export_prompts_zip(user: dict = Depends(auth.get_current_user)) -> dict:
    """啟動初判 prompt 包導出背景 job → {job_id, filename}（立即回，背景組檔）。

    Prompt-as-Source 架構下初判 prompt 唯一真相源＝prompts/*.md，本導出直接打包該目錄
    （7 支 prompt md ＋ 引擎契約 README ＋ 基線 BASELINE）為 zip，供離線交付 / 版本留存 / 手動 diff。
    以磁碟現行檔為準（見 rule_export.build_prompts_zip_bytes）。改背景 job：與問題列表導出共用
    /api/exports 進度串流 / 停止 / 取檔。
    """
    from app.core import export_jobs, rule_export

    filename = "judge_prompts.zip"
    job_id = export_jobs.start_export(rule_export.build_prompts_zip_bytes, filename)
    return {"job_id": job_id, "filename": filename}


@router.get("/{code}")
def get_rule(
    code: str, version: str | None = None, user: dict = Depends(auth.get_current_user)
) -> dict:
    """取某 rule 的當前 content（或 ?version=... 取特定版）。"""
    _check_code(code)
    if prs.is_prompt_code(code):
        content = (
            prs.get_version_content(code, version)
            if version is not None
            else prs.get_active_content(code)
        )
    else:
        content = (
            db.get_rule_version(code, _db_version(version))
            if version is not None
            else db.get_rule_active(code)
        )
    if content is None:
        raise HTTPException(status_code=404, detail="無此版本（或尚未 seed）")
    return {"rule_code": code, "version": version, "content": content}


@router.get("/{code}/history")
def get_history(code: str, user: dict = Depends(auth.get_current_user)) -> list[dict]:
    """某 rule 全版本清單（新到舊）。"""
    _check_code(code)
    return prs.list_history(code) if prs.is_prompt_code(code) else db.list_rule_history(code)


@router.get("/{code}/versions/{version}")
def get_version(code: str, version: str, user: dict = Depends(auth.get_current_user)) -> dict:
    """取特定版本完整 content（diff/恢復用）。"""
    _check_code(code)
    content = (
        prs.get_version_content(code, version)
        if prs.is_prompt_code(code)
        else db.get_rule_version(code, _db_version(version))
    )
    if content is None:
        raise HTTPException(status_code=404, detail="無此版本")
    return {"rule_code": code, "version": version, "content": content}


# 註：須定義於 `/{code}` POST 之前，否則會被 save_rule 的 code path 攔截。
@router.post("/reset-default-all")
def reset_default_all(
    user: dict = Depends(require_permission(permission_keys.JUDGE_RULE_MANAGE)),
) -> dict:
    """恢復 RuleManager「全部恢復默認」涵蓋的規則（source_mapping + 7 支初判 Prompt，排除
    bd_tag_vertical）為檔案默認，各新增一個版本覆蓋當前；範圍為全域單一動作，不依觸發頁面而變。

    缺默認檔的 code 由 db 層跳過（回傳 skipped），不視為錯誤。
    """
    res = db.reset_all_rule_defaults(author=user.get("email", ""))
    _reload_judge_cache()
    return res


@router.post("/{code}")
def save_rule(
    code: str,
    body: SaveIn,
    user: dict = Depends(require_permission(permission_keys.JUDGE_RULE_MANAGE)),
) -> dict:
    """存檔（先 jsonschema 驗證 → 新版生效）。

    prompt_* 走檔案版本庫並強制基線比對：`expected_base_version` 與當前生效版不符回 409，
    要求使用者重新載入——**不可以**靜默覆蓋，那正是要防的 lost update。
    """
    _check_code(code)
    _validate(code, body.content)
    if prs.is_prompt_code(code):
        text = body.content.get("text")
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(status_code=422, detail="content 需含 text（md 全文字串）")
        try:
            res = prs.save(
                code,
                text,
                expected_base_version=body.expected_base_version,
                note=body.note,
                author=user.get("email", ""),
            )
        except prs.ConflictError as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
    else:
        res = db.save_rule_version(code, body.content, note=body.note, author=user.get("email", ""))
    _reload_judge_cache()
    return res


@router.get("/{code}/draft")
def get_draft(code: str, user: dict = Depends(auth.get_current_user)) -> dict:
    """取某 prompt 的草稿；無草稿回 draft: null（200，前端免把「尚無草稿」當錯誤處理）。"""
    _check_prompt_code(code)
    return {"rule_code": code, "draft": prs.get_draft(code)}


@router.put("/{code}/draft")
def put_draft(
    code: str,
    body: DraftIn,
    user: dict = Depends(require_permission(permission_keys.JUDGE_RULE_MANAGE)),
) -> dict:
    """寫入/覆蓋草稿（last-write-wins）。刻意寬鬆只驗 text 非空——草稿允許存半成品，
    送測（prompt-sandbox drafts）與入庫（save_rule）才走 prompt_source.validate 強驗。
    草稿不影響初判管線，故不需 _reload_judge_cache。"""
    _check_prompt_code(code)
    text = body.content.get("text")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=422, detail="草稿 content 需含 text（md 全文字串）")
    prs.upsert_draft(code, text, body.base_version, updated_by=user.get("email", ""))
    return {"rule_code": code, "saved": True}


@router.delete("/{code}/draft")
def delete_draft(
    code: str,
    user: dict = Depends(require_permission(permission_keys.JUDGE_RULE_MANAGE)),
) -> dict:
    """刪除草稿（入庫採納後清理／手動捨棄）。deleted=false 表原本就無草稿（冪等，不視為錯誤）。"""
    _check_prompt_code(code)
    return {"rule_code": code, "deleted": prs.delete_draft(code)}


# 註：須定義於 `/{code}` POST 之後仍可正確匹配（雙段路徑不與單段衝突）；比照 draft 端點聚集於此。
@router.post("/{code}/validate")
def validate_prompt_text(
    code: str, body: ValidateIn, user: dict = Depends(auth.get_current_user)
) -> dict:
    """dry-run 驗證 prompt md 全文（不落庫）：三節可解析 + Schema 合法 + {TEXT}/{POLARITY} 佔位符
    + 域 Taxonomy 檢查。供草稿編輯器「驗證」鈕與沙盒送測前 fail-fast 共用；驗證失敗以 200 回
    {valid:false, error}——「內容不合法」是本端點的正常業務結果，非 HTTP 層錯誤。"""
    _check_prompt_code(code)
    from app.judge import prompt_source

    prompt_id = prompt_source.prompt_id_for_rule(code)
    try:
        prompt_source.validate(body.text, prompt_id)
    except ValueError as e:
        return {"valid": False, "error": str(e)}
    return {"valid": True}


@router.post("/{code}/restore/{version}")
def restore_rule(
    code: str,
    version: str,
    expected_base_version: str | None = None,
    user: dict = Depends(require_permission(permission_keys.JUDGE_RULE_MANAGE)),
) -> dict:
    """恢復某歷史版本。

    prompt_* 是「把生效指標切回該版」（不複製新檔——切換歷史由 ACTIVE 檔的 git log 承載）；
    其餘 rule 維持 DB 的「複製為新 active 版」語意。
    """
    _check_code(code)
    try:
        if prs.is_prompt_code(code):
            res = prs.restore(code, version, expected_base_version=expected_base_version)
        else:
            res = db.restore_rule_version(code, _db_version(version), author=user.get("email", ""))
    except prs.ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except (ValueError, prs.VersionNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    _reload_judge_cache()
    return res


@router.post("/{code}/reset-default")
def reset_default(
    code: str,
    user: dict = Depends(require_permission(permission_keys.JUDGE_RULE_MANAGE)),
) -> dict:
    """恢復默認（讀 config/ai_judge/ 檔內容存為新 active 版）。

    prompt_* 不再適用：舊語意是「用磁碟檔覆蓋 DB 熱編版」，而檔案版本庫裡檔案本身就是生效版，
    這個二元對照已不存在。要退回舊內容請用「恢復歷史版本」（restore）。
    """
    _check_code(code)
    if prs.is_prompt_code(code):
        raise HTTPException(
            status_code=422,
            detail="初判 Prompt 沒有「恢復默認」：所有版本都在版本庫裡，請改用「恢復歷史版本」。",
        )
    try:
        res = db.reset_rule_default(code, author=user.get("email", ""))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="默認檔不存在") from None
    _reload_judge_cache()
    return res
