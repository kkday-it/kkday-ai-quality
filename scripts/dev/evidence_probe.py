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


def main() -> int:
    """CLI 入口。"""
    ap = argparse.ArgumentParser(description="訂單佐證取數驗證")
    ap.add_argument("--single", metavar="ORDER_OID", help="單筆全鏈路查詢與逐欄核對")
    ap.add_argument("--user-id", default=None, help="指定憑證來源 user_id（缺省掃描）")
    args = ap.parse_args()
    if args.single:
        return run_single(args.single, args.user_id)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
