"""Prompt 評測共用：初判輸入 item 組裝 + 純函式指標。

`_build_eval_item` 依 source/source_id 組出與 production 初判同形的 item，供離線腳本
（`scripts/tools/eval_equivalence.py` 等價性閘門、`train_domain_router.py` 域路由訓練）
走 `prejudge.to_findings` 重跑。

`compute_*_metrics` 為純函式（與 LLM/DB I/O 解耦，可單元測），是指標口徑的 SSOT，
供 CLI（`scripts/tools/eval_prompt_single.py`、`eval_equivalence.py`）獨立引用。
"""

from __future__ import annotations

from app.core import db


def _build_eval_item(source: str, source_id: str) -> dict:
    """單筆初判輸入 item 組裝：取原始資料 → normalize_row → canonical 欄位補齊。

    比照 `prejudge_batch._work_one`——否則 `_text_of` 讀不到 reviews 的
    rec_title/rec_desc（在 rec_* 欄，非 content/comment）→ 判空文字。
    消費端＝離線腳本（`eval_equivalence.py` 等價性閘門、`train_domain_router.py` 域路由訓練）。

    Args:
        source: 來源 code（如 reviews）。
        source_id: 該來源業務 id（reviews→rec_oid）。

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


def compute_equivalence_metrics(records: list[dict]) -> dict:
    """管線改動前後等價性指標（純函式；scripts/tools/eval_equivalence.py 的 SSOT）。

    每筆 record＝{"a": run_a 摘要, "b": run_b 摘要}，摘要形狀（eval_equivalence 序列化產出）：
        {polarity, sentiment, n_findings, facets: [[l1,l2]…], primary: [l1,l2]|None}

    指標（升級計畫 P0 等價閘門五項）：
    - polarity_agree / sentiment_agree：整體傾向 / 情緒分逐筆一致率。
    - count_equal：findings 數量一致率（附平均絕對差 count_mae——「初判數量不變」的直接量測）。
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
