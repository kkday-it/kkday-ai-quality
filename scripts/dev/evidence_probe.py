"""訂單佐證取數驗證腳本（S1 單筆核對 / S2 批量壓測）。

用途：對 production snapshot 以 `qc_evidence` 正式投影路徑實測——單筆逐欄核對（--single）
或批量延遲分佈（--batch，S2 擴充）。憑證解析與查詢邏輯**完全走 qc_evidence 模組**（不自帶
平行實作），故本腳本同時是該模組的整合驗證。

執行（scripts/ 未掛載進容器，需 docker cp；比照 taxonomy_health.py 慣例）：
    docker cp scripts/dev/evidence_probe.py kkday-ai-quality-backend:/tmp/evidence_probe.py
    docker exec kkday-ai-quality-backend python /tmp/evidence_probe.py --single 47406070
    docker exec kkday-ai-quality-backend python /tmp/evidence_probe.py --single 47406070 --user-id <uuid>

憑證：--user-id 指定則用該 user 的 active production QC 連線；未指定則掃 user_settings
取第一個可解析出 production 憑證的 user（dev 便利；正式批次一律顯式指定觸發者）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time


def _resolve_creds(user_id: str | None) -> tuple[str, dict] | None:
    """取佐證憑證：指定 user 或掃全部 user_settings 取首個可解析者。

    Returns:
        (user_id, creds) 或 None（無任何 user 配好 production QC 連線）。
    """
    from sqlalchemy import text

    from app.core import settings as app_settings
    from app.core.db import qc_evidence
    from app.core.db.tables import get_engine

    if user_id:
        ids = [user_id]
    else:
        with get_engine().connect() as conn:
            ids = [r[0] for r in conn.execute(text("SELECT user_id FROM user_settings"))]
    for uid in ids:
        creds = qc_evidence.resolve_credentials(app_settings.load_settings(uid))
        if creds:
            return uid, creds
    return None


def run_single(order_oid: str, user_id: str | None) -> int:
    """單筆全鏈路查詢：印各表耗時、組裝結果摘要與逐欄核對清單。回 exit code。"""
    from app.core.db import qc_evidence

    resolved = _resolve_creds(user_id)
    if resolved is None:
        print("❌ 無可用 production QC 憑證（無 user 配置 env=production 的 active 連線）")
        return 2
    uid, creds = resolved
    print(f"憑證來源 user={uid} host={creds['host']} dbname={creds['dbname']}")
    qc_evidence.set_current(creds)

    t0 = time.time()
    result = qc_evidence.get_evidence(order_oid)
    elapsed = time.time() - t0
    print(f"status={result.status} elapsed={elapsed:.2f}s")
    if result.status != "fetched":
        return 1

    data = result.data or {}
    order = data.get("order") or {}
    print("── order（allow-list 欄位）──")
    for k, v in order.items():
        print(f"  {k} = {v}")
    for section in ("product_lang", "product_setting", "pkg_basic", "module_setting", "supplier"):
        v = data.get(section)
        size = len(json.dumps(v, ensure_ascii=False)) if v is not None else 0
        keys = list(v.keys()) if isinstance(v, dict) else v
        print(f"── {section}: {'null' if v is None else f'{size:,}B keys={keys}'}")
    # PII 防線複核（get_evidence 內已跑過一次；此處顯式重跑供人眼確認輸出）
    qc_evidence.assert_no_pii_keys(data)
    print("✅ PII key 掃描通過")
    print(f"meta = {data.get('meta')}")
    return 0


def _pick_target_order_oids(limit: int) -> list[str]:
    """從 app DB 取壓測標的：負向（優先）+ 中立補足的 Tour 垂直評論 order_oid（近期優先）。

    與歸因列表同源條件（attributions.is_primary × product_reviews），確保壓測母體＝
    真實判決會取佐證的訂單集合。
    """
    from sqlalchemy import text

    from app.core.db.tables import get_engine

    sql = text(
        """
        SELECT pr.order_oid, a.polarity
        FROM product_reviews pr
        JOIN attributions a
          ON a.source = 'product_reviews' AND a.source_id = pr.rec_oid AND a.is_primary = true
        WHERE a.polarity IN ('negative', 'neutral')
          AND pr.order_oid IS NOT NULL AND pr.order_oid <> ''
        ORDER BY (a.polarity = 'negative') DESC, pr.create_date DESC
        LIMIT :n
        """
    )
    with get_engine().connect() as conn:
        return [str(r[0]) for r in conn.execute(sql, {"n": limit})]


def run_batch(limit: int, concurrency: int, user_id: str | None) -> int:
    """批量壓測：N 筆訂單併發跑 get_evidence，輸出延遲分佈/覆蓋率報告（S2 閘門判定用）。"""
    import statistics
    from concurrent.futures import ThreadPoolExecutor

    from app.core import paths
    from app.core.db import qc_evidence

    resolved = _resolve_creds(user_id)
    if resolved is None:
        print("❌ 無可用 production QC 憑證")
        return 2
    uid, creds = resolved
    qc_evidence.set_current(creds)
    oids = _pick_target_order_oids(limit)
    print(f"標的 {len(oids)} 筆（憑證 user={uid}）併發={concurrency}")

    results: list[dict] = []

    def _one(oid: str) -> dict:
        # 每 worker thread 各自注入憑證（ThreadPool 不繼承主 thread contextvar）
        qc_evidence.set_current(creds)
        t0 = time.time()
        r = qc_evidence.get_evidence(oid)
        elapsed = time.time() - t0
        d = r.data or {}
        order = d.get("order") or {}
        return {
            "oid": oid,
            "status": r.status,
            "elapsed": elapsed,
            "prod_key": f"{order.get('prod_oid')}:{order.get('prod_version')}",
            "module_setting": d.get("module_setting") is not None,
            "bytes": len(json.dumps(d, ensure_ascii=False)) if r.data else 0,
        }

    t_wall = time.time()
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        for res in ex.map(_one, oids):
            results.append(res)
            n = len(results)
            if n % 10 == 0:
                print(f"  [{time.time() - t_wall:.0f}s] {n}/{len(oids)}")
    wall = time.time() - t_wall

    ok = [r for r in results if r["status"] == "fetched"]
    lat = sorted(r["elapsed"] for r in ok)
    q = lambda p: lat[min(len(lat) - 1, int(len(lat) * p))] if lat else 0  # noqa: E731
    status_counts: dict[str, int] = {}
    for r in results:
        status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1
    distinct_products = len({r["prod_key"] for r in ok})
    ms_rate = (sum(1 for r in ok if r["module_setting"]) / len(ok)) if ok else 0
    throughput = len(results) / wall if wall else 0
    proj_964 = 964 / throughput / 60 if throughput else float("inf")

    lines = [
        "# 訂單佐證 Phase A 壓測報告",
        f"- 時間：{datetime_now()}｜標的 {len(results)} 筆｜併發 {concurrency}｜wall {wall:.0f}s",
        f"- status 分佈：{status_counts}",
        f"- 延遲（成功筆）：p50 {q(0.5):.2f}s / p95 {q(0.95):.2f}s / max {lat[-1] if lat else 0:.2f}s / avg {statistics.mean(lat) if lat else 0:.2f}s",
        f"- 吞吐：{throughput:.2f} 筆/s → **外推 964 筆全量 ≈ {proj_964:.1f} 分鐘**（閘門：>30 分鐘升級兩階段管線）",
        f"- distinct (prod_oid,prod_version)：{distinct_products}/{len(ok)}（商品級快取可省比例）",
        f"- ors_prod_module_setting 命中率：{ms_rate:.0%}（null 代表該 pkg/lang 無模組設定列）",
        f"- payload bytes：avg {int(statistics.mean([r['bytes'] for r in ok])) if ok else 0:,} / max {max((r['bytes'] for r in ok), default=0):,}",
    ]
    report = "\n".join(lines)
    print("\n" + report)
    out = paths.REPORTS_DIR
    out.mkdir(parents=True, exist_ok=True)
    fp = out / f"evidence_probe_{int(time.time())}.md"
    fp.write_text(report + "\n", encoding="utf-8")
    print(f"\n報告已寫入 {fp}")
    return 0 if ok else 1


def datetime_now() -> str:
    """UTC ISO 時間字串（報告抬頭用）。"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main() -> int:
    """CLI 入口。"""
    ap = argparse.ArgumentParser(description="訂單佐證取數驗證")
    ap.add_argument("--single", metavar="ORDER_OID", help="單筆全鏈路查詢與逐欄核對")
    ap.add_argument("--batch", action="store_true", help="批量壓測（S2：延遲分佈 + 閘門判定）")
    ap.add_argument("--limit", type=int, default=100, help="批量壓測筆數（預設 100）")
    ap.add_argument("--concurrency", type=int, default=3, help="批量壓測併發（預設=pool_size 3）")
    ap.add_argument("--user-id", default=None, help="指定憑證來源 user_id（缺省掃描）")
    args = ap.parse_args()
    if args.single:
        return run_single(args.single, args.user_id)
    if args.batch:
        return run_batch(args.limit, args.concurrency, args.user_id)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
