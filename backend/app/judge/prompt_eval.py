"""Prompt 評測共用核心：診斷理由 overlay + 沙盒 dry-run 分類 + 純函式指標。

`domain_verdicts()`（六域並行診斷，動態附 reason/abstain_reason schema，B0 overlay，不改 md
本身、production 判決路徑零影響）供 `sandbox_classify`（Prompt 測試沙盒，使用者可任意勾選
prompt 子集、不受正式歸因閘門限制）呼叫。

`compute_domain_metrics`/`compute_polarity_metrics` 為純函式（與 LLM/DB I/O 解耦，可單元測），
供 CLI（`scripts/tools/eval_prompt_single.py`）獨立引用計算指標。
"""

from __future__ import annotations

import copy

from app.core import db
from app.judge import prompt_source

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
    """域 prompt schema 動態加診斷欄位：attributions[].reason（必填）+ 頂層 abstain_reason（必填）。

    冪等：md ## Schema 已存在同名欄位時不重複加（現行 md 不含 abstain_reason——2026-07-16 已自
    production Schema 移除，本 overlay 為沙盒唯一來源；冪等護欄保留防未來 md 變動）
    （避免 required 重複項）；pin 舊版（無此欄）時仍由此補上。
    """
    out = copy.deepcopy(schema)
    item = out["properties"]["attributions"]["items"]
    if "reason" not in item["properties"]:
        item["properties"]["reason"] = {"type": "string"}
        item["required"] = [*item["required"], "reason"]
    if "abstain_reason" not in out["properties"]:
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
    item: dict,
    text_: str,
    model: str,
    polarity: str,
    *,
    pids: list[str] | None = None,
    versions: dict[str, int] | None = None,
    drafts: dict[str, str] | None = None,
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
        versions: {rule_code: 指定歷史版本號}（版本選擇功能）；貫穿至 `prompt_source.load`。預設
            None＝沿用 DB active，對既有呼叫端零副作用。
        drafts: {rule_code: 草稿 md 全文}（草稿測試功能）；貫穿至 `prompt_source.load`，
            同 rule_code 時草稿優先於 versions。

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
        p = prompt_source.load(pid, versions=versions, drafts=drafts)
        schema = _diagnostic_domain_schema(p["schema"])
        system = p["system"] + _DIAGNOSTIC_DOMAIN_NOTE
        user = prejudge._render_pack_user(p["user_template"], text_, polarity)
        domain = prompt_source._domain_of(pid)
        out = prejudge._call(
            system,
            user,
            f"attribute:{domain}",
            model,
            schema=schema,
            effort=effort,
            label=domain,
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


def _sandbox_polarity(
    text_: str,
    model: str,
    *,
    versions: dict[str, int] | None = None,
    drafts: dict[str, str] | None = None,
) -> tuple[str, int, str]:
    """沙盒極性診斷：跑 00_polarity（附診斷 reason 欄），回 (polarity, sentiment, reason)。

    schema/system 疊法同 `_diagnostic_polarity_schema`/`_DIAGNOSTIC_POLARITY_NOTE`，但單筆、且用
    `prejudge._call` 走 model override（沙盒可能指定非目前 config 的模型），而非 `client.chat_json`
    直呼。

    Args:
        versions: {rule_code: 指定歷史版本號}，貫穿至 `prompt_source.load`；預設 None 零副作用。
        drafts: {rule_code: 草稿 md 全文}，貫穿至 `prompt_source.load`（草稿優先於 versions）。
    """
    from app.judge import prejudge

    p = prompt_source.load(prompt_source.POLARITY_ID, versions=versions, drafts=drafts)
    schema = _diagnostic_polarity_schema(p["schema"])
    system = p["system"] + _DIAGNOSTIC_POLARITY_NOTE
    out = prejudge._call(
        system,
        prejudge._render_pack_user(p["user_template"], text_, ""),  # polarity 無 {POLARITY} 槽
        "polarity",
        model,
        schema=schema,
        effort=prejudge._polarity_effort(),
        label="polarity",
    )
    pol = str(out.get("polarity", "")).strip().lower()
    pol = pol if pol in ("positive", "negative", "neutral") else "neutral"
    sentiment = prejudge._clamp_sentiment(out.get("sentiment"), pol)
    return pol, sentiment, str(out.get("reason", ""))[:300]


def sandbox_classify(
    item: dict,
    prompt_ids: list[str],
    model: str,
    *,
    versions: dict[str, int] | None = None,
    drafts: dict[str, str] | None = None,
) -> dict:
    """Prompt 測試沙盒：對單筆 item 跑「使用者勾選的」prompt 子集，ungated（不受正式歸因閘門限制）。

    供歸因列表「Prompt 測試」沙盒——使用者可任意勾選 7 支 prompt 中的子集，即使勾了域 prompt 但
    整體是正向評論，也照跑（測試目的不受生產策略約束）。域診斷委派 `domain_verdicts`（`pids` 子集），
    stage 已帶域名標籤（見該函式 docstring）。

    Args:
        item: `_build_sandbox_item` 組裝的判決輸入 item dict。
        prompt_ids: 使用者勾選的 prompt id 子集（`polarity` / `C-1`..`C-6`，`prompt_id_of` 值域）。
        model: 判決模型。
        versions: {rule_code: 指定歷史版本號}（版本選擇功能；`prompt_sandbox._one` 貫穿呼叫）。
            預設 None＝沿用 active，對既有呼叫端零副作用。
        drafts: {rule_code: 草稿 md 全文}（草稿測試功能；同 rule_code 時草稿優先於 versions）。
            極性有草稿且勾選域時，域 prompt 的 {POLARITY} 槽用「草稿極性」的結果——變體內部自洽。

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
        polarity, sentiment, reason = _sandbox_polarity(
            text_, model, versions=versions, drafts=drafts
        )
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
        _, verdicts = domain_verdicts(
            item,
            text_,
            model,
            polarity or "neutral",
            pids=domain_pids,
            versions=versions,
            drafts=drafts,
        )
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
    rec_title/rec_desc（在 rec_* 欄，非 content/comment）→ 判空文字。供沙盒測試
    （`prompt_sandbox.py`）呼叫。

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
        "title": canon.get("title") or "",  # 標題（rec_title/subject；_text_of 前置一行）
        "prod_oid": canon.get("prod_oid") or "",
        "order_oid": canon.get("order_oid") or "",
        "raw": items[0],  # 供 _evidence_cap 讀 order_oid
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


def sandbox_result_summary(res: dict) -> dict:
    """單筆沙盒結果（`sandbox_classify` 輸出或雙跑變體）→ 等價性摘要（純函式）。

    形狀對齊 `compute_equivalence_metrics` 的 record 摘要：{polarity, sentiment, n_findings,
    facets: [[l1,l2]…], primary: [l1,l2]|None}——primary 取全域最高 confidence 的歸因。
    """
    facets: list[list[str]] = []
    primary: list[str] | None = None
    best = -1.0
    for p in res.get("prompts") or []:
        for a in p.get("attributions") or []:
            l1 = str(a.get("l1_domain_code") or "")
            l2 = str(a.get("l2_code") or "")
            facets.append([l1, l2])
            conf = float(a.get("confidence") or 0)
            if conf > best:
                best, primary = conf, [l1, l2]
    return {
        "polarity": res.get("polarity"),
        "sentiment": res.get("sentiment_score"),
        "n_findings": len(facets),
        "facets": facets,
        "primary": primary,
    }


def sandbox_pair_metrics(pairs: list[tuple[dict, dict]]) -> dict:
    """逐筆 (a, b) 沙盒結果對 → 等價性聚合指標（純函式）。

    供兩處共用：雙跑對比 run 的 baseline/draft 摘要、測試歷史 run-vs-run 對比端點。
    委派 `compute_equivalence_metrics`（同一套口徑，避免兩份實作 drift）。
    """
    records = [{"a": sandbox_result_summary(a), "b": sandbox_result_summary(b)} for a, b in pairs]
    return compute_equivalence_metrics(records)


def compute_equivalence_metrics(records: list[dict]) -> dict:
    """管線改動前後等價性指標（純函式；scripts/tools/eval_equivalence.py 的 SSOT）。

    每筆 record＝{"a": run_a 摘要, "b": run_b 摘要}，摘要形狀（eval_equivalence 序列化產出）：
        {polarity, sentiment, n_findings, facets: [[l1,l2]…], primary: [l1,l2]|None}

    指標（升級計畫 P0 等價閘門五項）：
    - polarity_agree / sentiment_agree：整體傾向 / 情緒分逐筆一致率。
    - count_equal：findings 數量一致率（附平均絕對差 count_mae——「判決數量不變」的直接量測）。
    - facet_jaccard_mean：(l1,l2) 集合 Jaccard 均值（兩邊皆空＝1.0）。
    - primary_agree：主歸因 (l1,l2) 一致率（兩邊皆無主歸因＝一致）。
    用法：同管線雙跑 → 噪音地板；改動 vs 基線 → 各指標 ≥ 地板 − 1pp 才過閘。
    """

    def _facets(s: dict) -> set[tuple[str, str]]:
        return {tuple(f) for f in (s.get("facets") or [])}

    n = len(records)
    pol = sent = cnt = prim = 0
    mae = 0.0
    jac = 0.0
    for r in records:
        a, b = r["a"], r["b"]
        pol += a.get("polarity") == b.get("polarity")
        sent += a.get("sentiment") == b.get("sentiment")
        na, nb = int(a.get("n_findings") or 0), int(b.get("n_findings") or 0)
        cnt += na == nb
        mae += abs(na - nb)
        fa, fb = _facets(a), _facets(b)
        jac += 1.0 if not fa and not fb else len(fa & fb) / len(fa | fb)
        pa, pb = a.get("primary"), b.get("primary")
        prim += (tuple(pa) if pa else None) == (tuple(pb) if pb else None)

    def _rate(v: float) -> float | None:
        return round(v / n, 4) if n else None

    return {
        "n": n,
        "polarity_agree": _rate(pol),
        "sentiment_agree": _rate(sent),
        "count_equal": _rate(cnt),
        "count_mae": round(mae / n, 4) if n else None,
        "facet_jaccard_mean": _rate(jac),
        "primary_agree": _rate(prim),
    }
