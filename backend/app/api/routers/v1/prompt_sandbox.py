"""歸因列表「Prompt 測試」沙盒端點：ungated 對多筆並行測試 prompt 子集，結果落獨立歷史。

自 v1/prejudge.py 拆出（2026-07-23，原檔混三領域違反一 router 一領域慣例）；
PrejudgeIn/LlmOverridesIn/_resolve_target_ids 重用 prejudge.py 的共用契約與標的解析
（同 prompt_eval 重用 prejudge._gate_attrs 的既有慣例，不另立第三個共用模組）。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.core import auth, db
from app.core import settings as app_settings
from app.core.permissions import permission_keys, require_permission

from .prejudge import PrejudgeIn, _resolve_target_ids

router = APIRouter(prefix="/prejudge", tags=["prompt-sandbox"])


class PromptSandboxIn(PrejudgeIn):
    """Prompt 測試沙盒啟動請求：對 item_ids（或依條件解析出的目標集合）逐筆跑 prompt_ids 子集
    （不受正式歸因閘門限制）。

    繼承 `PrejudgeIn` 全部目標選取欄位（item_ids 顯式優先；否則 scope="all" 依 stages 目標選取，
    可 within_ids 交集勾選範圍，語義與初判分類「依條件批量選取」完全一致，零改動重用
    `_resolve_target_ids`）。scope 由前端依觸發入口顯式帶入：single（單列按鈕）/ selection（工具列
    對勾選多筆，item_ids 顯式）/ all（工具列「依條件批量」）——不由 len(item_ids) 反推，即使選取
    剛好 1 筆走 selection 入口，語意仍是「選取批次」而非單列。
    """

    source: str  # 覆寫父類 Optional：沙盒必須指定來源
    prompt_ids: list[str]
    scope: str = "single"
    # 版本選擇功能：{rule_code: 指定歷史版本號}（前端 PromptVersionPickerGroup／
    # usePromptVersionPicker）；不帶時全 7 支沿用 DB active。
    versions: dict[str, int] | None = None
    # 草稿測試功能：{rule_code: 草稿 md 全文}（內容快照隨請求帶入，與 DB 草稿演進脫鉤）；
    # 送測前逐條 prompt_source.validate 強驗 fail-fast（草稿存檔寬鬆、送測強驗）。
    drafts: dict[str, str] | None = None
    # 雙跑對比模式（僅 drafts 非空時有效）：每筆 item 跑 baseline（僅 versions）與
    # draft（versions+drafts）各一遍——token 成本 ×2，由前端明示後帶入。
    compare: bool = False


@router.post("/prompt-sandbox")
async def start_prompt_sandbox(
    body: PromptSandboxIn,
    user: dict = Depends(require_permission(permission_keys.PREJUDGE_RUN)),
) -> dict:
    """啟動 Prompt 測試沙盒背景 job → 立即回 {job_id}（前端輪詢 `/prompt-sandbox/status`）。

    本端點 ungated（勾了域 prompt 即跑，不受正向評論擋六域的正式閘門限制）、可對多筆並行
    （含依條件批量選取），結果落獨立的 `prompt_sandbox_runs` 歷史（與 attributions/attribution_history
    完全分離），且捕捉完整 LLM log 供事後回看（見 `prompt_sandbox.py`）。
    """
    from app.judge import prompt_sandbox

    item_ids = _resolve_target_ids(body)
    overrides = body.overrides.model_dump(exclude_unset=True) if body.overrides else None
    eff = app_settings.effective_llm_dict(
        app_settings.load_settings(), area="sandbox", overrides=overrides
    )
    try:
        job_id = await asyncio.to_thread(
            prompt_sandbox.start,
            body.source,
            item_ids,
            body.prompt_ids,
            eff,
            scope=body.scope,
            triggered_by=user.get("email", ""),
            versions=body.versions,
            drafts=body.drafts,
            compare=body.compare,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"job_id": job_id}


@router.post("/prompt-sandbox/count")
def prompt_sandbox_count(body: PromptSandboxIn, _: dict = Depends(auth.get_current_user)) -> dict:
    """預覽 Prompt 測試沙盒「將測試 N 筆」（與 `/prompt-sandbox` 同一套標的解析；不派工、不消耗 token）。"""
    return {"total": len(_resolve_target_ids(body))}


@router.get("/prompt-sandbox/status")
def prompt_sandbox_status(job_id: str, user: dict = Depends(auth.get_current_user)) -> dict:
    """沙盒測試 job 進度輪詢 → {status: running/done/error, total, done, run_id}。"""
    from app.judge import prompt_sandbox

    snap = prompt_sandbox.get_job(job_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="找不到此測試任務")
    return snap


@router.get("/prompt-sandbox/runs")
def list_prompt_sandbox_runs(
    limit: int = 20, offset: int = 0, user: dict = Depends(auth.get_current_user)
) -> dict:
    """沙盒測試歷史列表（created_at 降冪分頁）→ {total, items}——與正式初判歷史完全分離。

    items 不含 results/log（體積可觀，只列摘要）；詳情走 `/prompt-sandbox/runs/{run_id}`。
    """
    return db.list_sandbox_runs(limit=limit, offset=offset)


# 註：須定義於 `/runs/{run_id}` 之前，否則 "compare" 會被當成 run_id 段攔截。
@router.get("/prompt-sandbox/runs/compare")
def compare_prompt_sandbox_runs(
    a: str, b: str, user: dict = Depends(auth.get_current_user)
) -> dict:
    """兩筆沙盒測試 run 的結果對比（run-vs-run）→ {a, b, items, metrics}。

    按 source_id 對齊兩筆 run 的逐筆結果：單邊獨有的 item 仍列於 items（另一側為 null）；
    初判失敗的 item 保留 error 標記列出（草稿造成部分 item 初判失敗正是對比最該凸顯的訊號，
    不可靜默消失），但任一側帶 error 即不計入 metrics。雙跑對比 run 取其 draft 變體
    （該 run 實際受測的新配置），單跑 run 取原結果。metrics 委派
    `prompt_eval.sandbox_pair_metrics`（與雙跑 run 摘要同一套口徑）。
    """
    from app.judge import prompt_eval

    ra, rb = db.sandbox_run_detail(a), db.sandbox_run_detail(b)
    if ra is None or rb is None:
        raise HTTPException(status_code=404, detail="找不到此測試紀錄")

    def _meta(row: dict) -> dict:
        keep = ("run_id", "source", "scope", "item_count", "model", "prompt_ids",
                "versions", "compare", "created_at")  # fmt: skip
        return {k: row.get(k) for k in keep}

    def _normalize(r: dict) -> dict:
        # 雙跑 item → 取 draft 變體並補回 item 級 source_id/text，形狀同單跑結果；
        # error item（{source_id, error}）原樣保留，供對比視圖顯示失敗而非消失。
        if r.get("compare"):
            return {"source_id": r.get("source_id", ""), "text": r.get("text", ""),
                    **(r.get("draft") or {})}  # fmt: skip
        return r

    def _index(row: dict) -> dict[str, dict]:
        return {
            r.get("source_id", ""): _normalize(r)
            for r in (row.get("results") or [])
            if isinstance(r, dict)
        }

    ia, ib = _index(ra), _index(rb)
    items = [
        {"source_id": sid, "a": ia.get(sid), "b": ib.get(sid)} for sid in sorted(set(ia) | set(ib))
    ]
    pairs = [
        (it["a"], it["b"])
        for it in items
        if it["a"] and it["b"] and not it["a"].get("error") and not it["b"].get("error")
    ]
    return {
        "a": _meta(ra),
        "b": _meta(rb),
        "items": items,
        "metrics": prompt_eval.sandbox_pair_metrics(pairs) if pairs else None,
    }


@router.get("/prompt-sandbox/runs/{run_id}")
def get_prompt_sandbox_run(run_id: str, user: dict = Depends(auth.get_current_user)) -> dict:
    """單一沙盒測試 run 完整詳情（含逐筆 results + 完整 LLM log 快照，供事後回看）。

    雙跑對比 run（compare=true）另附 metrics（baseline vs draft 等價性聚合，讀取時動態算
    ——口徑演進不受落庫快照凍結）。
    """
    row = db.sandbox_run_detail(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="找不到此測試紀錄")
    if row.get("compare"):
        from app.judge import prompt_eval

        pairs = [
            (r["baseline"], r["draft"])
            for r in (row.get("results") or [])
            if isinstance(r, dict) and r.get("compare")
        ]
        row["metrics"] = prompt_eval.sandbox_pair_metrics(pairs) if pairs else None
    return row
