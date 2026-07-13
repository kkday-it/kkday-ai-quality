"""單支 Prompt 評測核心（後端 SSOT）：抽樣 + 跑單支 prompt + 指標——供 UI「測試」端點與 CLI 共用。

定位：Prompt-as-Source 調適閉環的量測核心。給定一支 prompt（polarity / C-1~C-6）與 N，抽 N 則現行
判決為參照、只跑該支 prompt（不跑其他六支、不動 production 判決管線），出該支指標。指標計算為純函式
（與 LLM/DB I/O 解耦→可單元測）；抽樣 + 跑 LLM 為 I/O 段。CLI（scripts/tools/eval_prompt_single.py）
與 UI 端點（api/routers/v1/judgment.prompt_eval）皆 import 本模組，避免判準邏輯平行兩份。

指標：
    域 prompt：primary 一致率 / 棄權正確率 / 命中率 / 多報率。
    極性 prompt：polarity 一致率 + sentiment 一致率。
"""

from __future__ import annotations

from sqlalchemy import text

from app.core import db
from app.core.db import tables as T
from app.judge import prompt_source
from app.judge.llm import client

_SOURCE = "product_reviews"


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
    """C-N → 對應歸因分類域機器值（自樹 tree[0].domain）；未知回空字串。"""
    content = db.get_rule_active(arg) or db.default_rule_content(arg)
    tree = (content or {}).get("tree") or []
    return tree[0].get("domain", "") if tree else ""


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
def _run_domain(pid: str, dom_code: str, samples: list[dict]) -> dict:
    """跑單域 prompt → records → 指標 + 分歧清單。"""
    p = prompt_source.load(pid)
    records: list[dict] = []
    for s in samples:
        user = (
            p["user_template"]
            .replace("{POLARITY}", s["polarity"])
            .replace("{TEXT}", s["text"][:2000])
        )
        resp = client.chat_json(p["system"], user, stage=f"eval_{dom_code}", schema=p["schema"])
        attrs = sorted(
            resp.get("attributions") or [], key=lambda a: -float(a.get("confidence") or 0)
        )
        records.append(
            {
                "id": s["id"],
                "ref_l2s": s["ref_l2s"],
                "ref_primary": s["ref_primary"],
                "pack_l2s": [a.get("l2_code") for a in attrs],
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
        }
        for r in records
        if (bool(r["pack_l2s"]) != bool(r["ref_l2s"]))
        or (r["ref_primary"] and not (r["pack_l2s"] and r["pack_l2s"][0] == r["ref_primary"]))
    ]
    return {"prompt": dom_code, **m, "mismatches": mismatches}


def _run_polarity(pid: str, samples: list[dict]) -> dict:
    """跑極性 prompt → records → 指標 + 分歧清單。"""
    p = prompt_source.load(pid)
    records: list[dict] = []
    for s in samples:
        user = p["user_template"].replace("{TEXT}", s["text"][:2000])
        resp = client.chat_json(p["system"], user, stage="eval_polarity", schema=p["schema"])
        records.append(
            {
                "id": s["id"],
                "polarity": s["polarity"],
                "sentiment": s["sentiment"],
                "pack_polarity": resp.get("polarity"),
                "pack_sentiment": resp.get("sentiment"),
                "text": s["text"][:120],
            }
        )
    m = compute_polarity_metrics(records)
    mismatches = [
        {"id": r["id"], "ref": r["polarity"], "pack": r["pack_polarity"], "text": r["text"]}
        for r in records
        if r["pack_polarity"] != r["polarity"]
    ]
    return {"prompt": "polarity", **m, "mismatches": mismatches}


def run_eval(prompt_arg: str, n: int) -> dict:
    """單支 prompt 評測（production 參照）：抽樣 → 跑 → 指標。同步 I/O，供 UI「測試」與 CLI 共用。

    Args:
        prompt_arg: "polarity" 或 "C-1".."C-6"。
        n: 樣本數（UI 快測建議 ≤20；抽樣 md5 穩定、跨 run 可比）。

    Returns:
        {prompt, source, model, n, <指標>, mismatches}。

    Raises:
        ValueError: 未知 prompt / 域；stub 模式（無 token）拒跑避免假結果。
    """
    if client.is_stub():
        raise ValueError("stub 模式（該配置無可用 LLM token），拒跑避免假結果")
    pid = prompt_id_of(prompt_arg)
    if prompt_arg == "polarity":
        result = _run_polarity(pid, sample_polarity(n))
    else:
        dom = domain_of(prompt_arg)
        if not dom:
            raise ValueError(f"未知域：{prompt_arg}")
        result = _run_domain(pid, prompt_arg, sample_domain(dom, n))
    result["source"] = "production"
    return result
