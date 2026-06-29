"""用本地 inquiries（merged 進線 CSV 灌入）跑判決鏈 → 真 findings（取代 seed_mock）。

流程：inquiries → NormalizedTicket（parse 對話）→ classify→adequacy→arbiter→diagnose → TicketFinding。
- stub 模式（無 LLM key）：啟發式分類（零 key 走通 pipeline）
- 真判：「設定」面板填 api_token 後重跑此腳本即 LLM 判決

執行：cd backend && .venv/bin/python -m judge_intake
"""

from __future__ import annotations

from app.core.db import _conn, init_db, insert_finding
from app.core.roster import rebuild_pkg_quality, rebuild_prod_quality
from app.judge import pipeline
from app.judge.ingest import conversations
from app.judge.llm import client


def _log(msg: str) -> None:
    print(msg, flush=True)


def main() -> None:
    init_db()
    tickets = conversations.fetch_conversations(source="db")
    mode = "stub（啟發式·無 key）" if client.is_stub() else "LLM 真判"
    _log(f"開始判決 {len(tickets)} 筆進線（{mode}）…")

    # 先清舊 judgments（重跑冪等）；逐筆判決並即時寫入（partial progress 可見、可中斷續用）
    with _conn() as c:
        c.execute("DELETE FROM judgments")
    n = 0
    for i, t in enumerate(tickets, 1):
        try:
            f = pipeline.diagnose_ticket(t, prod_source="db")
            insert_finding(f)
            n += 1
        except Exception as e:  # noqa: BLE001 — LLM 輸出不可預測，單筆失敗不毀整批
            _log(f"⚠️ ticket {t.ticket_id} 判決失敗，略過：{e}")
        if i % 5 == 0 or i == len(tickets):
            _log(f"  進度 {i}/{len(tickets)}（已寫入 {n}）")

    rebuild_prod_quality()
    rebuild_pkg_quality()
    _log(f"✅ 完成：進線 {len(tickets)} → findings {n}（{mode}）")


if __name__ == "__main__":
    main()
