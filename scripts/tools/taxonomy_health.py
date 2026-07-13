"""歸因分類體系健檢（多模型交叉診斷）——不依賴人工真值，用多模型判決的一致性與分歧結構反推分類體系品質。

核心邏輯（誠實定位）：全庫幾無人工真值（true_label），無法算 accuracy。改用多個獨立模型
（gpt-5-mini / ByteDance seed / Gemini / Claude，皆判同一批商品評論、快照存 judgment_history）
的**一致率高低與分歧集中處**反推分類體系本身的合理性與覆蓋率：
  一致率高 = 該處類別邊界清晰（體系合理）；分歧集中在特定類別對 = 那對邊界模糊（體系缺陷）。

診斷維度：
  A 整體合理性：L1/L2 成對集合 F1 + 多模型完全一致率 vs 隨機基準
  B 邊界模糊：純 L1 爭議（各判單一域卻不同）的類別對熱點
  C 粒度失衡：L2 使用分佈（零/極低觸發、過度集中）
  D 層級效度：L1 一致時 L2 是否也一致（按域）
  E 覆蓋率：外部獨立標籤 free_tag 映射殘差（盲區候選）
  F 結構觀察：跨域粒度不一致 + L3 深度限制

⚠️ 侷限（報告內明確聲明）：這是「一致性/合理性」非「準確度」；樣本為負向歸因子集（正向/中立
不歸因）；L3 層因判決深度=l2 不判故不評；多模型皆封閉式判（被餵當前樹），能診斷「現有類好不好用」
但無法完整回答「缺什麼類」——後者需開放式歸因（見 taxonomy_blindspot 探測）。

用法（容器內）：
    docker exec kkday-ai-quality-backend python /app/scripts/tools/taxonomy_health.py \
        --out /app/data/reports/taxonomy_health.md
    # 改規則後重跑對比：同指令即可，報告覆蓋。
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations

# 預設納入對比的模型（judgment_history 現有的四模型；gpt-5-mini 為生產主判決）
_DEFAULT_MODELS = [
    "gpt-5-mini",
    "seed-2-0-lite-260428",
    "gemini-2.5-flash-lite",
    "claude-fable-5",
]
_RANDOM_L1_BASE = 1 / 6  # L1 六域隨機一致基準（脈絡化完全一致率）


def _l1set(snap: dict) -> frozenset:
    """一則評論某模型快照 → L1 域 code 集合（多歸因即多元素）。"""
    return frozenset(
        c for a in snap["attributions"] if (c := (a.get("l1") or {}).get("code"))
    )


def _l2set(snap: dict) -> frozenset:
    """一則評論某模型快照 → L2 面向 code 集合。"""
    return frozenset(
        c for a in snap["attributions"] if (c := (a.get("l2") or {}).get("code"))
    )


def _f1(a: frozenset, b: frozenset) -> float | None:
    """兩集合的對稱 F1（2×交集/(|A|+|B|)）；任一空回 None（不可比，不計入平均）。"""
    if not a or not b:
        return None
    return 2 * len(a & b) / (len(a) + len(b))


def _label_maps():
    """從 ai_judge cascade_tree 取 {L1 code→label} 與 {L2 code→label}（報告顯示中文域/面向名）。"""
    from app.core.judge_config import ai_judge

    tree = ai_judge.cascade_tree()
    l1 = {n["value"]: n["label"] for n in tree}
    l2 = {}
    for n in tree:
        for c in n.get("children") or []:
            l2[c["value"]] = c["label"]
    return l1, l2


def _pairwise_f1(models, snaps, common, setfn) -> list[tuple[str, str, float, int]]:
    """各模型對的加權平均集合 F1（越高越一致）。回 [(模型A, 模型B, 平均F1, 可比則數)]。"""
    out = []
    for x, y in combinations(models, 2):
        vals = [
            v
            for sid in common
            if (v := _f1(setfn(snaps[x][sid]), setfn(snaps[y][sid]))) is not None
        ]
        out.append((x, y, sum(vals) / len(vals) if vals else 0.0, len(vals)))
    return out


def _consensus_rate(models, snaps, common) -> tuple[int, int]:
    """多模型「單一 L1 域完全一致」率：僅各模型皆判恰好一個域時可比，回 (一致數, 可比數)。"""
    same = comp = 0
    for sid in common:
        doms = [_l1set(snaps[m][sid]) for m in models]
        singles = [next(iter(s)) for s in doms if len(s) == 1]
        if len(singles) == len(models):  # 全部皆判單一域
            comp += 1
            if len(set(singles)) == 1:
                same += 1
    return same, comp


def _boundary_disputes(models, snaps, common, l1lab) -> tuple[int, Counter]:
    """純 L1 邊界爭議：各模型皆判單一域但不全同（排除評論真跨域的多歸因並存）。

    回 (爭議則數, Counter{(域A中文, 域B中文): 次數})——熱點對＝分類體系最該補判準界線處。
    """
    conf: Counter = Counter()
    n = 0
    for sid in common:
        singles = [
            next(iter(s)) for m in models if len(s := _l1set(snaps[m][sid])) == 1
        ]
        if len(singles) == len(models) and len(set(singles)) > 1:
            n += 1
            for a, b in combinations(sorted(set(singles)), 2):
                conf[(l1lab.get(a, a), l1lab.get(b, b))] += 1
    return n, conf


def _l2_granularity(models, snaps, common, l2lab):
    """L2 使用分佈（各 L2 被任一模型觸發的評論數）→ 零觸發/極低/過度集中診斷。"""
    hit: Counter = Counter()
    for sid in common:
        used = set()
        for m in models:
            used |= _l2set(snaps[m][sid])
        for c in used:
            hit[c] += 1
    allc = sorted(l2lab)
    zero = [l2lab[c] for c in allc if hit[c] == 0]
    low = sorted(
        [(l2lab[c], hit[c]) for c in allc if 0 < hit[c] <= 3], key=lambda x: x[1]
    )
    top = [(l2lab[c], n) for c, n in hit.most_common(5)]
    return zero, low, top


def _hierarchy_validity(models, snaps, common, l1lab):
    """層級效度：L1 全同單一域時 L2 是否也一致（按域）→ 該域 L2 劃分清晰度。回 {域: (L1一致數, L2也一致數)}。"""
    by: dict = defaultdict(lambda: [0, 0])
    for sid in common:
        doms = [_l1set(snaps[m][sid]) for m in models]
        if all(len(d) == 1 for d in doms) and len({next(iter(d)) for d in doms}) == 1:
            dom = next(iter(doms[0]))
            by[dom][0] += 1
            l2s = [_l2set(snaps[m][sid]) for m in models]
            if all(l2s[0] == x for x in l2s):
                by[dom][1] += 1
    return {l1lab.get(d, d): tuple(v) for d, v in by.items()}


def _free_tag_coverage():
    """外部 free_tag（獨立標籤體系）映射殘差＝覆蓋盲區候選。回 (全覆蓋率, 問題面向覆蓋率, 盲區Top)。"""
    import json

    from sqlalchemy import select

    from app.core.db import tables as T
    from app.core.paths import AI_JUDGE_DIR

    mapped = set(
        json.loads((AI_JUDGE_DIR / "free_tag_mapping.json").read_text())["mapping"]
    )
    pr = T.product_reviews
    freq: Counter = Counter()
    negfreq: Counter = Counter()
    with T.get_engine().connect() as c:
        rows = c.execute(
            select(pr.c.free_tag).where(pr.c.free_tag.isnot(None), pr.c.free_tag != "")
        ).all()
    for (ft,) in rows:
        try:
            arr = json.loads(ft)
        except (ValueError, TypeError):
            continue
        if not isinstance(arr, list):
            continue
        seen = set()
        for t in arr:
            name = t.get("tag_name") if isinstance(t, dict) else None
            if not name or name in seen:
                continue
            seen.add(name)
            freq[name] += 1
            v = t.get("tag_value")
            try:
                if v is not None and float(v) <= 2:
                    negfreq[name] += 1
            except (ValueError, TypeError):
                pass
    tot = sum(freq.values())
    cov = sum(f for n, f in freq.items() if n in mapped)
    negtot = sum(negfreq.values())
    negcov = sum(f for n, f in negfreq.items() if n in mapped)
    blind = sorted(
        [(n, negfreq[n], freq[n]) for n in freq if n not in mapped and negfreq.get(n, 0) > 0],
        key=lambda x: -x[1],
    )[:12]
    return (
        (cov, tot, cov / tot if tot else 0),
        (negcov, negtot, negcov / negtot if negtot else 0),
        blind,
    )


def build_report(models: list[str], source: str) -> str:
    """跑全診斷並組出 markdown 報告字串。"""
    from app.core import db

    snaps = {m: db.latest_snapshots(source, m) for m in models}
    present = [m for m in models if snaps[m]]  # 只納入有快照的模型
    common = sorted(set.intersection(*[set(snaps[m]) for m in present]))
    l1lab, l2lab = _label_maps()

    f1_l1 = _pairwise_f1(present, snaps, common, _l1set)
    f1_l2 = _pairwise_f1(present, snaps, common, _l2set)
    same, comp = _consensus_rate(present, snaps, common)
    ndisp, conf = _boundary_disputes(present, snaps, common, l1lab)
    zero, low, top = _l2_granularity(present, snaps, common, l2lab)
    hv = _hierarchy_validity(present, snaps, common, l1lab)
    (cov, tot, covr), (negcov, negtot, negr), blind = _free_tag_coverage()

    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    L = []
    L.append("# 歸因分類體系健檢報告（多模型交叉診斷）")
    L.append(f"\n> 產出時間：{ts}　·　來源：{source}　·　對比模型：{len(present)} 個")
    L.append(
        "\n## 方法論與侷限（先讀）\n"
        "- **這不是準確度**：全庫幾無人工真值，無法算 accuracy。本報告用多個獨立模型判決的"
        "**一致率與分歧結構**反推分類體系品質——一致高＝邊界清晰、體系合理；分歧集中＝該處邊界模糊。\n"
        f"- **樣本**：{len(present)} 模型交集 {len(common)} 則；一致性指標基於其中**有負向歸因**的子集"
        "（正向/中立不歸因、需雙方都判負向才可比），趨勢可信、個別小類需謹慎。\n"
        "- **L3 層不評**：當前判決深度=l2，根本不判 L3。\n"
        "- **封閉式侷限**：所有模型都被餵當前樹去選，故能診斷「現有類好不好用」，"
        "但**無法完整回答『缺什麼類』**——真正覆蓋盲區需開放式歸因（另見盲區探測）。"
    )

    L.append("\n## 對比模型覆蓋")
    L.append("\n| 模型 | 覆蓋評論數 |\n|---|---|")
    for m in present:
        L.append(f"| {m} | {len(snaps[m])} |")

    L.append("\n## 一、整體合理性")
    L.append(
        f"\n- **多模型單一 L1 完全一致率：{same}/{comp} = {same / comp:.1%}**"
        f"（隨機基準 {_RANDOM_L1_BASE:.1%}）"
        + ("　→ 遠高於隨機，L1 骨架模型可穩定區分、設計合理 ✅" if comp and same / comp > 0.5 else "")
    )
    L.append("\n**L1 成對集合 F1**（域級歸因可複現度）\n\n| 模型對 | F1 | 可比則數 |\n|---|---|---|")
    for x, y, v, n in f1_l1:
        L.append(f"| {x} × {y} | {v:.3f} | {n} |")
    L.append("\n**L2 成對集合 F1**（面向級，通常低於 L1＝粒度越細越難一致）\n\n| 模型對 | F1 | 可比則數 |\n|---|---|---|")
    for x, y, v, n in f1_l2:
        L.append(f"| {x} × {y} | {v:.3f} | {n} |")

    L.append("\n## 二、邊界模糊點（最該補判準界線）")
    L.append(f"\n排除「評論真跨域（多歸因並存）」後，**{ndisp} 則純邊界爭議**（各判單一域卻不同）集中於：")
    L.append("\n| 爭議類別對 | 則數 |\n|---|---|")
    for (a, b), n in conf.most_common(8):
        L.append(f"| {a} ⇄ {b} | {n} |")
    L.append("\n> 熱點對＝多模型都難分的邊界，分類體系最該補「判準界線範例」或考慮合併之處。")

    L.append("\n## 三、層級效度（各域 L2 劃分清晰度）")
    L.append("\nL1 判一致時 L2 是否也一致——低＝該域 L2 判準模糊該檢討。\n\n| L1 域 | L2 一致率 | 樣本 |\n|---|---|---|")
    for dom, (t, s) in sorted(hv.items(), key=lambda x: -x[1][0]):
        if t >= 5:
            flag = " ⚠️" if s / t < 0.8 else " ✅"
            L.append(f"| {dom} | {s / t:.0%}{flag} | {t} 則 |")

    L.append("\n## 四、粒度失衡")
    L.append(f"\n- **零觸發 L2**（{len(zero)} 類）：{'、'.join(zero) or '無'}　→ 可能適用其他來源或過度細分，需確認")
    L.append(f"- **極低觸發 1-3 則**（{len(low)} 類）：{'、'.join(f'{n}({c})' for n, c in low) or '無'}")
    L.append(f"- **過度集中 Top5**：{'、'.join(f'{n}({c})' for n, c in top)}　→ 高頻類可能太粗、內部該再分")

    L.append("\n## 五、覆蓋率（外部獨立標籤 free_tag 驗證）")
    L.append(
        f"\n用外部評論系統的 free_tag（**完全獨立於當前分類**的面向標籤）反查：\n\n"
        f"| 口徑 | 覆蓋率 |\n|---|---|\n"
        f"| 全部面向（加權） | **{covr:.1%}**（{cov}/{tot}）|\n"
        f"| 僅問題面向（低分，加權） | **{negr:.1%}**（{negcov}/{negtot}）|"
    )
    L.append("\n**盲區候選**（外部標了問題面向、當前樹未映射；量小，需人工確認是真缺類或 mapping 沒配全）\n\n| 面向 | 低分則數 | 總則數 |\n|---|---|---|")
    for n, nf, f in blind:
        L.append(f"| {n} | {nf} | {f} |")

    L.append("\n## 六、結構觀察")
    L.append(
        "\n- **跨域粒度不一致**：C-1 商品內容域細到「頁面欄位」級（60 個 L3：商品主圖/集合地點…），"
        "其他域只到「問題類型」級（內容期待落差/天候與自然因素）。同一棵樹兩種粒度標準，屬體系設計的不對稱。\n"
        "- **L3 深度**：當前判決深度=l2，L3 節點全未觸發——非分類缺陷，是判決設定；"
        "若要評 L3 品質需先開 l3 深判。"
    )
    L.append(f"\n---\n\n*本報告由 `scripts/tools/taxonomy_health.py` 產出（純 DB 查詢，零 LLM 成本）；改規則後重跑同指令即可對比。*")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", nargs="*", default=_DEFAULT_MODELS)
    ap.add_argument("--source", default="product_reviews")
    ap.add_argument("--out", default="/app/data/reports/taxonomy_health.md")
    args = ap.parse_args()

    report = build_report(args.models, args.source)
    from pathlib import Path

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"✅ 報告已寫入 {out}（{len(report)} 字）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
