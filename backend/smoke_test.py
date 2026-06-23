"""Smoke test：評論線端到端驗證（零 key，stub 模式）。

run: .venv/bin/python smoke_test.py  或  ./run.sh test
驗證：health · 單個錄入 · CSV 錄入 · 判決(150665 纜車案例) · findings 查詢。
"""

from __future__ import annotations

import sys

from fastapi.testclient import TestClient

from app.api.main import app


def main() -> int:
    c = TestClient(app)
    fails: list[str] = []

    # 1. health
    if c.get("/health").json() != {"status": "ok"}:
        fails.append("health")

    # 2. 單個錄入
    if c.post("/api/inbound", json={"prod_oid": "150665", "comment": "smoke 單個", "rating": 1}).status_code != 200:
        fails.append("inbound(single)")

    # 3. CSV 批量錄入
    csv = "prod_oid,rating,comment\n150665,1,smoke CSV 纜車\n".encode("utf-8")
    r = c.post(
        "/api/inbound/upload",
        files={"file": ("smoke.csv", csv, "text/csv")},
    )
    if r.status_code != 200 or r.json().get("inserted", 0) < 1:
        fails.append("inbound/upload(csv)")

    # 4. 判決（150665，纜車案例應判 content_unclear）
    d = c.post("/api/diagnose", json={"prod_oid": "150665"}).json()
    if d.get("count") != 6:
        fails.append(f"diagnose count={d.get('count')}≠6")
    nav = next((f for f in d.get("findings", []) if "纜車" in f.get("problem_summary", "")), None)
    if not nav or nav.get("verdict") != "content_unclear":
        fails.append("纜車案例 verdict≠content_unclear")

    # 5. findings 查詢
    if len(c.get("/api/findings?prod_oid=150665").json()) < 6:
        fails.append("findings query")

    if fails:
        print("❌ smoke test 失敗：" + ", ".join(fails))
        return 1
    print("✅ smoke test 通過：health · inbound(單個/CSV) · diagnose(6筆,纜車=content_unclear) · findings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
