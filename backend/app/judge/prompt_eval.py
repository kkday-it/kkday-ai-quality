"""單支 Prompt 評測核心（後端 SSOT）：抽樣 + 跑單支 prompt + 指標——供 UI「測試」端點與 CLI 共用。

定位：Prompt-as-Source 調適閉環的量測核心。給定一支 prompt（polarity / C-1~C-6）與 N，抽 N 則現行
判決為參照、只跑該支 prompt（不跑其他六支、不動 production 判決管線），出該支指標。指標計算為純函式
（與 LLM/DB I/O 解耦→可單元測）；抽樣 + 跑 LLM 為 I/O 段。CLI（scripts/tools/eval_prompt_single.py）
與 UI 端點（api/routers/v1/judgment.prompt_eval）皆 import 本模組，避免判準邏輯平行兩份。

指標：
    域 prompt：primary 一致率 / 棄權正確率 / 命中率 / 多報率。
    極性 prompt：polarity 一致率 + sentiment 一致率。

診斷理由 overlay（B0）：`_diagnostic_schema`/`_diagnostic_system` 在**評測期**動態於 7 支 prompt 的
schema/system 上附加 reason/abstain_reason 欄位（不改 md 檔本身、production 判決路徑零影響）。
`domain_verdicts()` 對六域各自單獨呼叫診斷版 schema，回「六域裁決」：命中的域帶 l2_code+理由，
棄權的域帶棄權理由——無論匹配與否都有交代，供調適者定位「邊界寫糊」或「例句缺」。
"""

from __future__ import annotations

import copy

from sqlalchemy import text

from app.core import db
from app.core.db import tables as T
from app.judge import prompt_source
from app.judge.llm import client

_SOURCE = "product_reviews"

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
    """極性 prompt schema 動態加 reason（必填一句話理由）。"""
    out = copy.deepcopy(schema)
    out["properties"]["reason"] = {"type": "string"}
    out["required"] = [*out["required"], "reason"]
    return out


def domain_verdicts(
    item: dict, text_: str, model: str, polarity: str
) -> tuple[list[dict], list[dict]]:
    """六域並行診斷歸因：回 (gated attrs 含 reason, verdicts 六域逐支交代)。

    每域獨立呼叫該域 prompt（動態附診斷 schema/system，不動 production `_attrs_pack`），命中則附
    reason，棄權則附 abstain_reason——無論匹配與否六個域都有交代。合流閘門委派 `prejudge._gate_attrs`
    （與 production 同一套同域去重/信心閘門/排序規則，避免評測與生產兩份實作 drift）。

    Args:
        item: 判決輸入 item dict（供 `_finalize_attr_l2` 的證據封頂讀 order_oid 等）。
        text_: 評論文字。
        model: 判決模型。
        polarity: 已判定的整體傾向（negative/neutral，用於填 {POLARITY} 槽）。

    Returns:
        (gated_attrs, verdicts)：gated_attrs 為過閘門後的最終歸因（含 reason）；
        verdicts 為 `[{domain, domain_label, matched, attributions, abstain_reason}]`（六域皆有）。
    """
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context

    from app.judge import prejudge

    valid = prejudge._l2_label_map()
    effort = prejudge._attr_effort()
    pids = prompt_source.DOMAIN_PROMPT_IDS

    def _one(pid: str) -> dict:
        p = prompt_source.load(pid)
        schema = _diagnostic_domain_schema(p["schema"])
        system = p["system"] + _DIAGNOSTIC_DOMAIN_NOTE
        user = prejudge._render_pack_user(p["user_template"], text_, polarity)
        out = prejudge._call(system, user, "attribute", model, schema=schema, effort=effort)
        domain = prompt_source._domain_of(pid)
        dm = prompt_source._domain_meta(domain)
        raw_attrs = [a for a in (out.get("attributions") or []) if isinstance(a, dict)]
        finalized: list[dict] = []
        for raw in raw_attrs[:3]:
            f = prejudge._finalize_attr_l2(item, text_, raw, valid)
            f["reason"] = str(raw.get("reason", ""))[:300]
            finalized.append(f)
        return {
            "domain": domain,
            "domain_label": dm.get("label", domain),
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


# ─────────────────────────── prompt 對照 ───────────────────────────
def prompt_id_of(arg: str) -> str:
    """--prompt / API 值（polarity / C-1..C-6）→ prompt_source 的 prompt_id。缺對照拋 ValueError。"""
    if arg == "polarity":
        return prompt_source.POLARITY_ID
    pid = prompt_source.prompt_id_for_rule(f"prompt_{arg}")  # C-3 → prompt_C-3 → 03_C-3_supplier
    if not pid:
        raise ValueError(f"未知 prompt：{arg}（須為 polarity 或 C-1..C-6）")
    return pid


def domain_of(arg: str) -> str:
    """C-N → 對應歸因分類域機器值（自 prompt_source 檔名尾綴派生，如 C-3→supplier）；未知回空字串。"""
    return prompt_source._domain_of(prompt_id_of(arg))


# ─────────────────────────── 取文字 ───────────────────────────
def _review_texts(ids: list[str]) -> dict[str, str]:
    """rec_oid → 評論文字（title + desc，對齊判決輸入）。"""
    if not ids:
        return {}
    with T.get_engine().connect() as c:
        rows = c.execute(
            text(
                "SELECT rec_oid::text AS sid, coalesce(rec_title,'') AS t, coalesce(rec_desc,'') AS d "
                "FROM product_reviews WHERE rec_oid::text = ANY(:ids)"
            ),
            {"ids": ids},
        ).all()
    return {sid: (f"{t}\n{d}".strip()) for sid, t, d in rows}


# ─────────────────────────── 抽樣（production 現行判決為參照）───────────────────────────
def sample_domain(dom_machine: str, n: int) -> list[dict]:
    """域參照集：judgments 判過本域（含 primary）與判過他域（棄權分母）各半，md5 穩定排序。

    回統一參照記錄：{id, text, polarity, ref_l2s, ref_primary}。dom_machine＝l1_code 機器值。
    """
    half = n // 2
    with T.get_engine().connect() as c:
        pos = c.execute(
            text(
                "SELECT source_id, min(polarity) AS polarity FROM judgments "
                "WHERE source=:s AND l1_code=:d AND polarity IN ('negative','neutral') "
                "GROUP BY source_id ORDER BY md5(source_id) LIMIT :k"
            ),
            {"s": _SOURCE, "d": dom_machine, "k": n - half},
        ).all()
        neg = c.execute(
            text(
                "SELECT j.source_id, min(j.polarity) AS polarity FROM judgments j "
                "WHERE j.source=:s AND j.polarity IN ('negative','neutral') "
                "AND j.l1_code IS NOT NULL AND j.l1_code <> '' AND j.l1_code <> :d "
                "AND NOT EXISTS (SELECT 1 FROM judgments x WHERE x.source=:s "
                "  AND x.source_id=j.source_id AND x.l1_code=:d) "
                "GROUP BY j.source_id ORDER BY md5(j.source_id) LIMIT :k"
            ),
            {"s": _SOURCE, "d": dom_machine, "k": half},
        ).all()
        prod = c.execute(
            text(
                "SELECT source_id, l2_code, coalesce(is_primary,false) AS is_primary "
                "FROM judgments WHERE source=:s AND l1_code=:d AND source_id = ANY(:ids)"
            ),
            {"s": _SOURCE, "d": dom_machine, "ids": [r[0] for r in pos] + [r[0] for r in neg]},
        ).all()
    by_rec: dict[str, dict] = {}
    for sid, pol in list(pos) + list(neg):
        by_rec[sid] = {"id": sid, "polarity": pol, "ref_l2s": [], "ref_primary": None}
    for sid, l2, is_primary in prod:
        by_rec[sid]["ref_l2s"].append(l2)
        if is_primary:
            by_rec[sid]["ref_primary"] = l2
    texts = _review_texts(list(by_rec))
    return [dict(v, text=texts.get(k, "")) for k, v in by_rec.items() if texts.get(k, "").strip()]


def sample_polarity(n: int) -> list[dict]:
    """極性參照集：三態各 n/3（md5 穩定），帶 production polarity/sentiment 真值。"""
    per = max(1, n // 3)
    out: list[dict] = []
    with T.get_engine().connect() as c:
        for pol in ("negative", "neutral", "positive"):
            rows = c.execute(
                text(
                    "SELECT source_id, min(polarity) AS polarity, min(sentiment_score) AS sentiment "
                    "FROM judgments WHERE source=:s AND polarity=:p AND sentiment_score IS NOT NULL "
                    "GROUP BY source_id ORDER BY md5(source_id) LIMIT :k"
                ),
                {"s": _SOURCE, "p": pol, "k": per},
            ).all()
            out += [{"id": sid, "polarity": p, "sentiment": s} for sid, p, s in rows]
    texts = _review_texts([r["id"] for r in out])
    return [dict(r, text=texts.get(r["id"], "")) for r in out if texts.get(r["id"], "").strip()]


# ─────────────────────────── 指標（純函式，與 I/O 解耦→可單元測）───────────────────────────
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
    """逐筆 {polarity, sentiment, pack_polarity, pack_sentiment} → 極性指標（純函式）。"""
    n = len(records)
    pol_ok = sum(1 for r in records if r["pack_polarity"] == r["polarity"])
    sent_ok = sum(1 for r in records if r["pack_sentiment"] == r["sentiment"])
    return {
        "n": n,
        "polarity_match_rate": round(pol_ok / n, 3) if n else None,
        "sentiment_match_rate": round(sent_ok / n, 3) if n else None,
    }


# ─────────────────────────── 跑單支（I/O：呼叫 LLM 產 records → 純函式算指標）───────────────────────────
def _run_domain(pid: str, dom_code: str, samples: list[dict], *, diagnostic: bool = True) -> dict:
    """跑單域 prompt → records → 指標 + 分歧清單。

    diagnostic=True（預設）：schema/system 動態附診斷欄位（見模組頂 B0 overlay），mismatches 每案
    附 `reason`（命中取首條歸因理由、棄權取 abstain_reason）——批量分歧的原因分佈直接可讀。
    """
    p = prompt_source.load(pid)
    schema = _diagnostic_domain_schema(p["schema"]) if diagnostic else p["schema"]
    system = p["system"] + _DIAGNOSTIC_DOMAIN_NOTE if diagnostic else p["system"]
    records: list[dict] = []
    for s in samples:
        user = (
            p["user_template"]
            .replace("{POLARITY}", s["polarity"])
            .replace("{TEXT}", s["text"][:2000])
        )
        resp = client.chat_json(system, user, stage=f"eval_{dom_code}", schema=schema)
        attrs = sorted(
            resp.get("attributions") or [], key=lambda a: -float(a.get("confidence") or 0)
        )
        records.append(
            {
                "id": s["id"],
                "ref_l2s": s["ref_l2s"],
                "ref_primary": s["ref_primary"],
                "pack_l2s": [a.get("l2_code") for a in attrs],
                "pack_reasons": [str(a.get("reason", "")) for a in attrs],
                "abstain_reason": str(resp.get("abstain_reason", "")),
                "text": s["text"][:120],
            }
        )
    m = compute_domain_metrics(records)
    mismatches = [
        {
            "id": r["id"],
            "ref": r["ref_l2s"],
            "ref_primary": r["ref_primary"],
            "pack": r["pack_l2s"],
            "text": r["text"],
            **(
                {"reason": r["pack_reasons"][0] if r["pack_reasons"] else r["abstain_reason"]}
                if diagnostic
                else {}
            ),
        }
        for r in records
        if (bool(r["pack_l2s"]) != bool(r["ref_l2s"]))
        or (r["ref_primary"] and not (r["pack_l2s"] and r["pack_l2s"][0] == r["ref_primary"]))
    ]
    return {"prompt": dom_code, **m, "mismatches": mismatches}


def _run_polarity(pid: str, samples: list[dict], *, diagnostic: bool = True) -> dict:
    """跑極性 prompt → records → 指標 + 分歧清單。diagnostic=True：mismatches 每案附一句話 reason。"""
    p = prompt_source.load(pid)
    schema = _diagnostic_polarity_schema(p["schema"]) if diagnostic else p["schema"]
    system = p["system"] + _DIAGNOSTIC_POLARITY_NOTE if diagnostic else p["system"]
    records: list[dict] = []
    for s in samples:
        user = p["user_template"].replace("{TEXT}", s["text"][:2000])
        resp = client.chat_json(system, user, stage="eval_polarity", schema=schema)
        records.append(
            {
                "id": s["id"],
                "polarity": s["polarity"],
                "sentiment": s["sentiment"],
                "pack_polarity": resp.get("polarity"),
                "pack_sentiment": resp.get("sentiment"),
                "reason": str(resp.get("reason", "")),
                "text": s["text"][:120],
            }
        )
    m = compute_polarity_metrics(records)
    mismatches = [
        {
            "id": r["id"],
            "ref": r["polarity"],
            "pack": r["pack_polarity"],
            "text": r["text"],
            **({"reason": r["reason"]} if diagnostic else {}),
        }
        for r in records
        if r["pack_polarity"] != r["polarity"]
    ]
    return {"prompt": "polarity", **m, "mismatches": mismatches}


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
    from app.core import source_mapping as _srcmap
    from app.judge import prejudge

    items = db.get_items_by_ids([source_id], source)
    if not items:
        raise ValueError(f"找不到評論：{source}/{source_id}")
    # 正規化源欄→canonical content（判決主輸入）——比照 prejudge_batch._work_one,否則 _text_of 讀不到
    # product_reviews 的 rec_title/rec_desc（在 rec_* 欄,非 content/comment）→ 判空文字。
    canon = _srcmap.normalize_row(source, items[0]) if source in _srcmap.sources() else {}
    item = {
        **items[0],
        "source": source,
        "source_id": source_id,
        "content": canon.get("content") or "",
        "prod_oid": canon.get("prod_oid") or "",
        "order_oid": canon.get("order_oid") or "",
        "raw": items[0],  # 供 _evidence_cap 讀 order_oid
    }
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


def run_eval(prompt_arg: str, n: int, *, diagnostic: bool = True) -> dict:
    """單支 prompt 評測（production 參照）：抽樣 → 跑 → 指標。同步 I/O，供 UI「測試」與 CLI 共用。

    Args:
        prompt_arg: "polarity" 或 "C-1".."C-6"。
        n: 樣本數（UI 快測建議 ≤20；抽樣 md5 穩定、跨 run 可比）。
        diagnostic: 是否開診斷理由（mismatches 每案附 reason；預設開）。

    Returns:
        {prompt, source, model, n, <指標>, mismatches}。

    Raises:
        ValueError: 未知 prompt / 域；stub 模式（無 token）拒跑避免假結果。
    """
    if client.is_stub():
        raise ValueError("stub 模式（該配置無可用 LLM token），拒跑避免假結果")
    pid = prompt_id_of(prompt_arg)
    if prompt_arg == "polarity":
        result = _run_polarity(pid, sample_polarity(n), diagnostic=diagnostic)
    else:
        dom = domain_of(prompt_arg)
        if not dom:
            raise ValueError(f"未知域：{prompt_arg}")
        result = _run_domain(pid, prompt_arg, sample_domain(dom, n), diagnostic=diagnostic)
    result["source"] = "production"
    return result
