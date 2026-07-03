#!/usr/bin/env python3
"""一次性回填：judgments.data 內單一歸因 → data.judges 單元素陣列（全 5 來源）。

多歸因改造 Design A-embed：全來源多歸因統一存 judgments.data.judges。既有 finding 的 data 只有
單值 l1/l2/l3 歸因，補一個等價的單元素 judges 陣列進 data，讓列表/前端多標籤對既有資料也一致渲染。
新判決由 prejudge.to_finding_multi 直接產出 data.judges，無需再跑本腳本。

只有「負向 + 有 L1 域」才成一條違規歸因（單元素）；正向/中性/無法歸類 → judges=[]（空陣列）。
已含非空 data.judges 者跳過（冪等，不覆蓋新判結果）。owner 留空待 backfill。

用法（後端環境）：
    python scripts/backfill_judgments_data_judges.py           # dry-run，只報數不寫
    python scripts/backfill_judgments_data_judges.py --apply    # 實際寫回 judgments.data
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _judge_from_finding(f: dict) -> dict:
    """單一 finding data dict → 單元素 ReviewJudge dict（欄位對映；即 primary）。"""
    l1 = f.get("l1_domain_code", "")
    return {
        "judge_id": l1 or "none",
        "l1_domain_code": l1,
        "l1_label": f.get("l1_label", ""),
        "l2_code": f.get("l2_code", ""),
        "l2_label": f.get("l2_label", ""),
        "l3_code": f.get("l3_code", ""),
        "l3_label": f.get("l3_label", ""),
        "confidence": f.get("confidence", 0.0),
        "raw_confidence": f.get("raw_confidence", 0.0),
        "confidence_tier": f.get("confidence_tier", ""),
        "judgment_stage": f.get("judgment_stage", ""),
        "recommended_action": f.get("recommended_action", ""),
        "owner": "",
        "evidence_quote": f.get("evidence_quote", ""),
        "problem_summary": f.get("problem_summary", ""),
        "is_primary": True,
        "is_enhanced": bool(f.get("is_enhanced", False)),
        "enhance_model": f.get("enhance_model", ""),
        "model_used": f.get("model_used", ""),
        "judged_at": f.get("judged_at", ""),
    }


def main() -> int:
    """掃所有 judgments，補 data.judges 單元素；已有非空者跳過；--apply 才寫回。"""
    apply = "--apply" in sys.argv
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    try:
        from sqlalchemy import select, update

        from app.core import tables as T
    except ImportError as exc:
        print(f"需在後端環境執行（缺 {exc.name}）", file=sys.stderr)
        return 1

    engine = T.get_engine()
    total = touched = attributed = skipped = 0
    with engine.begin() as c:
        rows = list(c.execute(select(T.judgments.c.finding_id, T.judgments.c.data)))
        total = len(rows)
        for fid, raw in rows:
            try:
                f = json.loads(raw) if raw else {}
            except (ValueError, TypeError):
                continue
            if f.get("judges"):  # 已有多歸因（新判結果）→ 冪等跳過
                skipped += 1
                continue
            pol = f.get("polarity") or ""
            l1 = f.get("l1_domain_code", "")
            f["judges"] = [_judge_from_finding(f)] if pol == "negative" and l1 else []
            if f["judges"]:
                attributed += 1
            touched += 1
            if apply:
                c.execute(
                    update(T.judgments).where(T.judgments.c.finding_id == fid).values(data=json.dumps(f, ensure_ascii=False))
                )

    verb = "已回填" if apply else "待回填（dry-run）"
    print(
        f"judgments 總列 {total}｜{verb} {touched}（含違規歸因單元素 {attributed}）｜已有 judges 跳過 {skipped}"
    )
    if not apply:
        print("→ 確認無誤後加 --apply 實際寫回")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
