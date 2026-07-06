"""多 model 聯合判決（ensemble）：低信心案例跨廠投票合併（純邏輯，不呼叫 LLM）。

confidence-gated：只有主判決信心低於 auto_accept 閾值的 finding 才觸發 ensemble（高信心直接採信、
省 token；見 `should_ensemble`）。各 voter 的實跑由呼叫端各自跑 `prejudge._resolve_attrs_multi` 後把
結果傳入本模組合併——故本模組為純函式、可獨立單元測，與 LLM/DB 解耦。

投票單位＝L1 歸因域（判決為多歸因、action/owner 為域級）。合併輸出：聯合判決（每保留域一條）+ 一致度
（agreement，label-free 準確率代理）+ 分歧旗標（disputed，交 arbiter 仲裁）+ 攤平票（model_votes 落庫）。
"""
from __future__ import annotations

from collections import Counter

# 多數決成立門檻：一致度 ≥ 2/3 視為多數共識（3 voter 中至少 2 票），低於此為分歧需仲裁。
_MAJORITY = 2.0 / 3.0


def should_ensemble(confidence: float, auto_accept_threshold: float) -> bool:
    """是否需觸發 ensemble：主判決信心低於 auto_accept 閾值才需跨廠複判（高信心直接採信省 token）。"""
    return confidence < auto_accept_threshold


def flatten_votes(voter_results: list[dict]) -> list[dict]:
    """各 voter 的多歸因 attr 清單 → 攤平 model_votes（每條一列，供落庫 judgments.model_votes）。"""
    votes: list[dict] = []
    for vr in voter_results:
        model = vr.get("model", "")
        for a in vr.get("attrs", []):
            votes.append(
                {
                    "model": model,
                    "l1_code": a.get("l1_domain_code", ""),
                    "l2_code": a.get("l2_code", ""),
                    "l3_code": a.get("l3_code", ""),
                    "conf": round(float(a.get("confidence", 0.0)), 4),
                }
            )
    return votes


def merge_votes(voter_results: list[dict], *, min_agreement: float = 0.5) -> dict:
    """跨 voter 多歸因投票合併 → 聯合判決 + 一致度 + 分歧旗標 + 攤平票。

    以 L1 歸因域為投票單位。每個 L1 域一致度＝投它的 voter 比例；保留一致度 ≥ min_agreement（過半）的域；
    域內 L3 取眾數（平手取信心最高者），conf 取投該域 voter 的平均信心（**不與一致度混算**，語義乾淨：
    conf＝模型平均信心、agreement＝一致度，兩者分開供 gate/arbiter 各自判斷）。
    disputed＝存在被投但未達多數共識（< 2/3）的情形 → 交 arbiter 仲裁。

    Args:
        voter_results: [{"model": str, "attrs": [_finalize_attr 產的 attr dict]}]，含主判決在內。
        min_agreement: 域保留門檻（預設過半 0.5）。

    Returns:
        {"merged": [attr dict], "agreement": float, "disputed": bool, "model_votes": [...]}。
        merged 依 conf 降冪；agreement＝各保留域一致度最大值（主域）；voter_results 為空回空結果。
    """
    n = len(voter_results)
    if n == 0:
        return {"merged": [], "agreement": 0.0, "disputed": False, "model_votes": []}

    # 各 voter 對每個 L1 域取信心最高的 attr（對齊 _resolve_attrs_multi 的域級去重語義）
    per_domain: dict[str, dict[str, dict]] = {}  # domain -> {model: attr}
    for vr in voter_results:
        model = vr.get("model", "")
        best: dict[str, dict] = {}
        for a in vr.get("attrs", []):
            dom = a.get("l1_domain_code", "")
            if not dom:
                continue
            if dom not in best or a.get("confidence", 0.0) > best[dom].get("confidence", 0.0):
                best[dom] = a
        for dom, a in best.items():
            per_domain.setdefault(dom, {})[model] = a

    merged: list[dict] = []
    agreements: list[float] = []
    disputed = False
    for voters in per_domain.values():
        agr = len(voters) / n
        # 非全數同意即為分歧訊號；未達多數共識（< 2/3）標記需仲裁
        if agr < _MAJORITY:
            disputed = True
        if agr < min_agreement:
            continue  # 少數域丟棄（不成聯合判決）
        attrs = list(voters.values())
        # L3 眾數（空字串＝L3 abstain 亦算一票）；平手取信心最高者的整條 attr 當代表
        top_l3 = Counter(a.get("l3_code", "") for a in attrs).most_common(1)[0][0]
        rep = max(
            (a for a in attrs if a.get("l3_code", "") == top_l3),
            key=lambda a: a.get("confidence", 0.0),
        )
        avg_conf = sum(a.get("confidence", 0.0) for a in attrs) / len(attrs)
        merged.append({**rep, "confidence": round(avg_conf, 4)})
        agreements.append(agr)

    merged.sort(key=lambda a: a.get("confidence", 0.0), reverse=True)
    overall = round(max(agreements), 4) if agreements else 0.0
    return {
        "merged": merged,
        "agreement": overall,
        "disputed": disputed,
        "model_votes": flatten_votes(voter_results),
    }
