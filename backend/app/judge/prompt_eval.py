"""Prompt 評測共用核心：診斷理由 overlay + 單筆/沙盒 dry-run 分類 + 純函式指標。

`domain_verdicts()`（六域並行診斷，動態附 reason/abstain_reason schema，B0 overlay，不改 md
本身、production 判決路徑零影響）為 `classify_one`（production 閘門單筆 dry-run，歸因列表「Prompt
測試」單列沿用其 ungated 版本前身）與 `sandbox_classify`（Prompt 測試沙盒，使用者可任意勾選
prompt 子集、不受正式歸因閘門限制）共用，避免評測/生產/沙盒三份平行實作。

`compute_domain_metrics`/`compute_polarity_metrics` 為純函式（與 LLM/DB I/O 解耦，可單元測），
供 CLI（`scripts/tools/eval_prompt_single.py`）獨立引用計算指標。
"""

from __future__ import annotations

import copy

from app.core import db
from app.judge import prompt_source
from app.judge.llm import client

# ─────────────────────────── 診斷理由 overlay（B0，code-side 動態注入，不改 md）───────────────────────────
_DIAGNOSTIC_DOMAIN_NOTE = (
    "\n\n<diagnostic_instructions>\n"
    "本次為調適診斷模式，額外要求：\n"
    "- 每條 attributions 補一句 reason：為何歸這條 l2_code（一句話中文）。\n"
    "- abstain_reason 必填：attributions 為空時，說明為何本域不收這則評論（如「問題屬其他域」"
    "「找不到具體問題點」）；attributions 非空時留空字串即可。\n"
    "</diagnostic_instructions>"
)
_DIAGNOSTIC_POLARITY_NOTE = (
    "\n\n<diagnostic_instructions>\n"
    "本次為調適診斷模式，額外要求：reason 補一句話中文，說明為何判此 polarity/sentiment。\n"
    "</diagnostic_instructions>"
)


def _diagnostic_domain_schema(schema: dict) -> dict:
    """域 prompt schema 動態加診斷欄位：attributions[].reason（必填）+ 頂層 abstain_reason（必填）。"""
    out = copy.deepcopy(schema)
    item = out["properties"]["attributions"]["items"]
    item["properties"]["reason"] = {"type": "string"}
    item["required"] = [*item["required"], "reason"]
    out["properties"]["abstain_reason"] = {"type": "string"}
    out["required"] = [*out["required"], "abstain_reason"]
    return out


def _diagnostic_polarity_schema(schema: dict) -> dict:
    """極性 schema：reason 必填；原 schema 不受影響。"""
    out = copy.deepcopy(schema)
    out["properties"]["reason"] = {"type": "string"}
    out["required"] = [*out["required"], "reason"]
    return out


def domain_verdicts(
    item: dict, text_: str, model: str, polarity: str, *, pids: list[str] | None = None
) -> tuple[list[dict], list[dict]]:
    """域並行診斷歸因：回 (gated attrs 含 reason, verdicts 逐域交代)。

    每域獨立呼叫該域 prompt（動態附診斷 schema/system，不動 production `_attrs_pack`），命中則附
    reason，棄權則附 abstain_reason——無論匹配與否每個域都有交代。合流閘門委派 `prejudge._gate_attrs`
    （與 production 同一套同(域,面向)去重/信心閘門/排序規則，避免評測與生產兩份實作 drift）。

    LLM 呼叫的 stage 帶域名標籤（`attribute:{domain}`，如 `attribute:C-1`）而非固定 `attribute`——
    stage 純屬 log/usage 記帳標籤、不參與判決 gating 或 exact-cache key（`client.chat_json` 的
    `cache_key` 只是 OpenAI `prompt_cache_key` 效能提示，見其 docstring），此改動對判決結果零影響，
    僅讓 llm_usage / run_log 能分域檢視（原固定字串令 7 泳道日誌分不出是哪一域）。

    Args:
        item: 判決輸入 item dict（供 `_finalize_attr_l2` 的證據封頂讀 order_oid 等）。
        text_: 評論文字。
        model: 判決模型。
        polarity: 已判定的整體傾向（negative/neutral，用於填 {POLARITY} 槽）。
        pids: 要跑的域 prompt 子集（預設全六域 `prompt_source.DOMAIN_PROMPT_IDS`，向後相容）。

    Returns:
        (gated_attrs, verdicts)：gated_attrs 為過閘門後的最終歸因（含 reason）；
        verdicts 為 `[{domain, domain_label, matched, attributions, abstain_reason}]`（含 pids 每域）。
    """
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context

    from app.judge import prejudge

    valid = prejudge._l2_label_map()
    effort = prejudge._attr_effort()
    pids = pids if pids is not None else prompt_source.DOMAIN_PROMPT_IDS

    def _one(pid: str) -> dict:
        p = prompt_source.load(pid)
        schema = _diagnostic_domain_schema(p["schema"])
        system = p["system"] + _DIAGNOSTIC_DOMAIN_NOTE
        user = prejudge._render_pack_user(p["user_template"], text_, polarity)
        domain = prompt_source._domain_of(pid)
        out = prejudge._call(
            system, user, f"attribute:{domain}", model, schema=schema, effort=effort
        )
        from app.core import ai_judge  # lazy：域中文名自 `## Taxonomy` 派生（ai_judge 快取）

        raw_attrs = [a for a in (out.get("attributions") or []) if isinstance(a, dict)]
        finalized: list[dict] = []
        for raw in raw_attrs[:3]:
            f = prejudge._finalize_attr_l2(item, text_, raw, valid)
            f["reason"] = str(raw.get("reason", ""))[:300]
            finalized.append(f)
        return {
            "domain": domain,
            "domain_label": ai_judge.domain_label(domain),
            "matched": bool(finalized),
            "attributions": finalized,
            "abstain_reason": "" if finalized else str(out.get("abstain_reason", ""))[:300],
        }

    ctxs = [copy_context() for _ in pids]
    with ThreadPoolExecutor(max_workers=len(pids)) as ex:
        futures = [ex.submit(ctx.run, _one, pid) for ctx, pid in zip(ctxs, pids, strict=True)]
        verdicts = [f.result() for f in futures]

    all_attrs = [a for v in verdicts for a in v["attributions"]]
    gated = prejudge._gate_attrs(all_attrs, prejudge._max_attributions())
    return gated, verdicts


def _sandbox_polarity(text_: str, model: str) -> tuple[str, int, str]:
    """沙盒極性診斷：跑 00_polarity（附診斷 reason 欄），回 (polarity, sentiment, reason)。

    schema/system 疊法同 `_diagnostic_polarity_schema`/`_DIAGNOSTIC_POLARITY_NOTE`，但單筆、且用
    `prejudge._call` 走 model override（沙盒可能指定非目前 config 的模型），而非 `client.chat_json`
    直呼。
    """
    from app.judge import prejudge

    p = prompt_source.load(prompt_source.POLARITY_ID)
    schema = _diagnostic_polarity_schema(p["schema"])
    system = p["system"] + _DIAGNOSTIC_POLARITY_NOTE
    out = prejudge._call(
        system,
        prejudge._render_pack_user(p["user_template"], text_, ""),  # polarity 無 {POLARITY} 槽
        "polarity",
        model,
        schema=schema,
        effort=prejudge._polarity_effort(),
    )
    pol = str(out.get("polarity", "")).strip().lower()
    pol = pol if pol in ("positive", "negative", "neutral") else "neutral"
    sentiment = prejudge._clamp_sentiment(out.get("sentiment"), pol)
    return pol, sentiment, str(out.get("reason", ""))[:300]


def sandbox_classify(item: dict, prompt_ids: list[str], model: str) -> dict:
    """Prompt 測試沙盒：對單筆 item 跑「使用者勾選的」prompt 子集，ungated（不受正式歸因閘門限制）。

    與 `classify_one` 的差異：`classify_one` 走 production `to_findings` 管線（受歸因閘門限制，
    正向評論不跑六域）；本函式供歸因列表「Prompt 測試」沙盒——使用者可任意勾選 7 支 prompt 中的
    子集，即使勾了域 prompt 但整體是正向評論，也照跑（測試目的不受生產策略約束）。域診斷委派
    `domain_verdicts`（`pids` 子集），stage 已帶域名標籤（見該函式 docstring）。

    Args:
        item: `_build_sandbox_item` 組裝的判決輸入 item dict。
        prompt_ids: 使用者勾選的 prompt id 子集（`polarity` / `C-1`..`C-6`，`prompt_id_of` 值域）。
        model: 判決模型。

    Returns:
        {source_id, text, polarity, sentiment_score, prompts}：`prompts` 為異質清單——勾了
        `polarity` 有一條 `{prompt_id:"polarity", matched, polarity, sentiment_score, reason}`；
        勾了域則各一條 `{prompt_id:"C-N", domain_label, matched, attributions, abstain_reason}`。
        只勾域未勾 polarity：仍會內部先跑一次極性（不落入 `prompts`）以填域 prompt 的 {POLARITY} 槽。
    """
    from app.judge import prejudge

    text_ = prejudge._text_of(item)
    want_polarity = "polarity" in prompt_ids
    domain_pids = [prompt_id_of(p) for p in prompt_ids if p != "polarity"]

    polarity = ""
    sentiment = 0
    prompts: list[dict] = []

    if want_polarity or domain_pids:
        polarity, sentiment, reason = _sandbox_polarity(text_, model)
        if want_polarity:
            prompts.append(
                {
                    "prompt_id": "polarity",
                    "matched": True,
                    "polarity": polarity,
                    "sentiment_score": sentiment,
                    "reason": reason,
                }
            )

    if domain_pids:
        _, verdicts = domain_verdicts(item, text_, model, polarity or "neutral", pids=domain_pids)
        for pid, v in zip(domain_pids, verdicts, strict=True):
            ext_id = (prompt_source.rule_code_for_prompt(pid) or "").removeprefix("prompt_")
            prompts.append(
                {
                    "prompt_id": ext_id,
                    "domain_label": v["domain_label"],
                    "matched": v["matched"],
                    "attributions": v["attributions"],
                    "abstain_reason": v["abstain_reason"],
                }
            )

    return {
        "source_id": item.get("source_id", ""),
        "text": text_,
        "polarity": polarity,
        "sentiment_score": sentiment,
        "prompts": prompts,
    }


# ─────────────────────────── prompt 對照 ───────────────────────────
def prompt_id_of(arg: str) -> str:
    """--prompt / API 值（polarity / C-1..C-6）→ prompt_source 的 prompt_id。缺對照拋 ValueError。"""
    if arg == "polarity":
        return prompt_source.POLARITY_ID
    pid = prompt_source.prompt_id_for_rule(f"prompt_{arg}")  # C-3 → prompt_C-3 → 03_C-3_supplier
    if not pid:
        raise ValueError(f"未知 prompt：{arg}（須為 polarity 或 C-1..C-6）")
    return pid


def _build_sandbox_item(source: str, source_id: str) -> dict:
    """單筆判決輸入 item 組裝：取原始資料 → normalize_row → canonical 欄位補齊。

    比照 `prejudge_batch._work_one`——否則 `_text_of` 讀不到 product_reviews 的
    rec_title/rec_desc（在 rec_* 欄，非 content/comment）→ 判空文字。供 `classify_one`
    與沙盒測試（`prompt_sandbox.py`）共用，避免重複組裝邏輯。

    Args:
        source: 來源 code（如 product_reviews）。
        source_id: 該來源業務 id（product_reviews→rec_oid）。

    Returns:
        item dict（含 content/prod_oid/order_oid/raw，供 `prejudge.to_findings`/`_text_of` 使用）。

    Raises:
        ValueError: 找不到該則評論。
    """
    from app.core import source_mapping as _srcmap

    items = db.get_items_by_ids([source_id], source)
    if not items:
        raise ValueError(f"找不到評論：{source}/{source_id}")
    canon = _srcmap.normalize_row(source, items[0]) if source in _srcmap.sources() else {}
    return {
        **items[0],
        "source": source,
        "source_id": source_id,
        "content": canon.get("content") or "",
        "prod_oid": canon.get("prod_oid") or "",
        "order_oid": canon.get("order_oid") or "",
        "raw": items[0],  # 供 _evidence_cap 讀 order_oid
    }


def classify_one(source: str, source_id: str, *, diagnostic: bool = True) -> dict:
    """單條評論 dry-run 分類（歸因列表「測試」用）：跑 prompts 判一則 → 結果,**不落庫**。

    engine=prompt_pack（live）;`to_findings` 本身非落庫（落庫是 db.replace_source_findings,不呼叫即不寫）
    → 天然 dry-run,可安全預覽「改 prompt 後這條會怎麼判」而不覆寫現有判決。

    diagnostic=True（預設）：極性落在 polarity_gate（negative/neutral）時，額外跑一輪六域診斷
    （`domain_verdicts`），回傳「六域裁決」——命中域帶 l2_code+理由、棄權域帶棄權理由，無論匹配與否
    六個域都有交代。⚠️ 這是與 `attributions`（production `to_findings` 產出）**獨立的第二輪 LLM 呼叫**
    （schema 多 reason 一欄），非同一次呼叫回填——`attributions` 與 `domain_verdicts` 命中結果理論上
    可能有微小差異（CoT 效應），但足供調適定位問題所在。

    Args:
        source: 來源 code（如 product_reviews）。
        source_id: 該來源業務 id（product_reviews→rec_oid）。
        diagnostic: 是否附六域診斷理由（預設開；停用可省一輪六域並行呼叫）。

    Returns:
        {polarity, sentiment_score, model, text, attributions:[{is_primary, l1_domain_code, l1_label,
         l2_code, l2_label, confidence, confidence_tier, judgment_stage, evidence_quote, summary}],
         domain_verdicts:[{domain, domain_label, matched, attributions, abstain_reason}]}。
        非問題（無歸因）→ attributions 空、僅 polarity；正向（non_issue，非 polarity_gate 內）→
        domain_verdicts 恆空（production 本就不跑六域，診斷沒有意義可交代）。

    Raises:
        ValueError: stub 模式（無 token）;或找不到該則評論。
    """
    if client.is_stub():
        raise ValueError("stub 模式（該配置無可用 LLM token），拒跑避免假結果")
    from app.core import settings as app_settings
    from app.judge import prejudge

    item = _build_sandbox_item(source, source_id)
    model = app_settings.current().get("model", "")
    findings = prejudge.to_findings(item, model=model)  # 非落庫
    polarity = findings[0].polarity if findings else ""
    sentiment = findings[0].sentiment_score if findings else 0
    attributions = [
        {
            "is_primary": f.is_primary,
            "l1_domain_code": f.l1_domain_code,
            "l1_label": f.l1_label,
            "l2_code": f.l2_code,
            "l2_label": f.l2_label,
            "confidence": round(f.confidence, 3),
            "confidence_tier": f.confidence_tier,
            "judgment_stage": f.judgment_stage,
            "evidence_quote": f.evidence_quote,
            "summary": f.summary,
        }
        for f in findings
        if f.l1_domain_code  # 只列真歸因（非問題 finding 的空域不列）
    ]
    text_ = prejudge._text_of(item)
    verdicts: list[dict] = []
    if diagnostic and polarity in prejudge._attribute_when():
        _, verdicts = domain_verdicts(item, text_, model, polarity)
    return {
        "polarity": polarity,
        "sentiment_score": sentiment,
        "model": model,
        "text": text_,
        "attributions": attributions,
        "domain_verdicts": verdicts,
    }


# ─────────────────────────── 指標（純函式，與 I/O 解耦→可單元測；CLI SSOT）───────────────────────────
def compute_domain_metrics(records: list[dict]) -> dict:
    """逐筆 {ref_l2s, ref_primary, pack_l2s} → 域指標（純函式，不觸 LLM/DB）。

    primary 一致率＝ref 有本域 primary 者 pack 最高信心 l2 同碼比例；棄權正確率＝ref 無本域歸因者
    pack 亦回空比例；命中率＝ref 有本域歸因者 pack 非空比例；多報率＝pack 條數 > ref 條數比例。
    """
    st = {
        "primary_total": 0,
        "primary_match": 0,
        "abstain_total": 0,
        "abstain_ok": 0,
        "hit_total": 0,
        "hit_ok": 0,
        "over_report": 0,
    }
    for r in records:
        ref_l2s, ref_primary, pack_l2s = r["ref_l2s"], r["ref_primary"], r["pack_l2s"]
        if ref_l2s:
            st["hit_total"] += 1
            if pack_l2s:
                st["hit_ok"] += 1
        else:
            st["abstain_total"] += 1
            if not pack_l2s:
                st["abstain_ok"] += 1
        if ref_primary:
            st["primary_total"] += 1
            if pack_l2s and pack_l2s[0] == ref_primary:
                st["primary_match"] += 1
        if len(pack_l2s) > len(ref_l2s):
            st["over_report"] += 1
    n = len(records)

    def _rate(a: int, b: int) -> float | None:
        return round(a / b, 3) if b else None

    return {
        "n": n,
        "primary_match_rate": _rate(st["primary_match"], st["primary_total"]),
        "abstain_correct_rate": _rate(st["abstain_ok"], st["abstain_total"]),
        "hit_rate": _rate(st["hit_ok"], st["hit_total"]),
        "over_report_rate": _rate(st["over_report"], n),
        "counts": st,
    }


def compute_polarity_metrics(records: list[dict]) -> dict:
    """逐筆 {polarity, sentiment, pack_polarity, pack_sentiment} → 極性指標（純函式）。

    sentiment 為 None 之筆不計入 sentiment_match_rate 分母（B3 mock 測試集只填 expected_polarity、
    無 sentiment 真值時，該欄自然回 None 而非誤導性的低分）。
    """
    n = len(records)
    pol_ok = sum(1 for r in records if r["pack_polarity"] == r["polarity"])
    sent_records = [r for r in records if r.get("sentiment") is not None]
    sent_ok = sum(1 for r in sent_records if r["pack_sentiment"] == r["sentiment"])
    return {
        "n": n,
        "polarity_match_rate": round(pol_ok / n, 3) if n else None,
        "sentiment_match_rate": round(sent_ok / len(sent_records), 3) if sent_records else None,
    }
