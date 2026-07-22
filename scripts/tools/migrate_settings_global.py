"""去帳戶隔離遷移：合併現有多 user 的 user_settings row → 單一 __global__ row。

背景（2026-07-22）：配置改全項目共享後，`settings.load_settings/save_settings` 一律讀寫
`GLOBAL_SETTINGS_KEY="__global__"`。歷史上每個 user 各有一 row，需合併成一份。

合併策略（保守·可乾跑）：
- 挑「配置最完整」的來源 row 作基底（有 llm_configs 且有 active 者優先，其次 llm_configs 最多者）。
- 其餘 row 的 production QC 連線（qc_configs env=production + qc_passwords）補進基底（去隔離後
  佐證憑證也全局化，確保任一 user 曾配過的 production QC 不丟）。
- 寫入 __global__ row（若已存在則以合併結果覆蓋）。**不刪原 user row**（保留可回退；確認無誤後
  可另跑清理）。

用法（容器內）：
    docker cp scripts/tools/migrate_settings_global.py kkday-ai-quality-backend:/tmp/
    docker exec -w /app/backend kkday-ai-quality-backend python /tmp/migrate_settings_global.py --dry-run
    docker exec -w /app/backend kkday-ai-quality-backend python /tmp/migrate_settings_global.py --apply
"""

from __future__ import annotations

import argparse
import json
import sys


def _score(data: dict) -> tuple[int, int]:
    """配置完整度評分：（有 active_llm_config_id, llm_configs 數量）——越大越完整。"""
    return (1 if data.get("active_llm_config_id") else 0, len(data.get("llm_configs") or []))


def run(dry_run: bool) -> int:
    """執行合併；dry_run 只印計畫不寫入。回 exit code。"""
    from sqlalchemy import text

    from app.core import settings as app_settings
    from app.core.db.tables import get_engine

    with get_engine().connect() as c:
        rows = [
            (r[0], json.loads(r[1]) if r[1] else {})
            for r in c.execute(text("SELECT user_id, data FROM user_settings"))
        ]
    if not rows:
        print("無任何 user_settings row，無需遷移。")
        return 0

    existing_global = next((d for uid, d in rows if uid == app_settings.GLOBAL_SETTINGS_KEY), None)
    source_rows = [(uid, d) for uid, d in rows if uid != app_settings.GLOBAL_SETTINGS_KEY]
    print(f"現有 row：{len(rows)}（其中 __global__ {'存在' if existing_global else '不存在'}）")

    # 基底：已有 __global__ 用之；否則挑最完整的來源
    if existing_global is not None:
        base_uid, base = "__global__", dict(existing_global)
    else:
        base_uid, base = max(source_rows, key=lambda kv: _score(kv[1]))
        base = dict(base)
    print(
        f"基底來源：{base_uid}（llm_configs={len(base.get('llm_configs') or [])} "
        f"active={bool(base.get('active_llm_config_id'))}）"
    )

    # 補其餘 row 的 production QC 連線（避免佐證憑證丟失）
    merged_qc = {c.get("id"): c for c in (base.get("qc_configs") or [])}
    merged_pw = dict(base.get("qc_passwords") or {})
    added = 0
    for uid, d in source_rows:
        if uid == base_uid:
            continue
        for cfg in d.get("qc_configs") or []:
            if cfg.get("env") == "production" and cfg.get("id") not in merged_qc:
                merged_qc[cfg["id"]] = cfg
                pw = (d.get("qc_passwords") or {}).get(cfg["id"])
                if pw:
                    merged_pw[cfg["id"]] = pw
                added += 1
    if added:
        base["qc_configs"] = list(merged_qc.values())
        base["qc_passwords"] = merged_pw
    print(f"補入其他 row 的 production QC 連線：{added} 條")

    if dry_run:
        print(
            "\n[DRY-RUN] 未寫入。計畫：以上基底 + 補入 QC → 寫 __global__ row（原 user row 保留）。"
        )
        return 0

    # 寫入：走 settings.save_settings（會加密機密、_sanitize）——但它讀 __global__ 當基底，
    # 故先直寫 raw row 再讓後續 load 正常化。這裡直接落庫（機密已是明文，用 _persist 加密）。
    app_settings._persist(base)  # noqa: SLF001 —— 遷移腳本刻意用內部落庫（含加密）
    print("✅ 已寫入 __global__ row（機密加密落庫）。原 user row 保留供回退。")
    # 驗證回讀
    reloaded = app_settings.load_settings()
    print(
        f"回讀驗證：llm_configs={len(reloaded.get('llm_configs') or [])} "
        f"qc_configs={len(reloaded.get('qc_configs') or [])}"
    )
    return 0


def main() -> int:
    """CLI 入口。"""
    ap = argparse.ArgumentParser(description="去帳戶隔離：合併 user_settings → __global__")
    ap.add_argument("--apply", action="store_true", help="實際寫入（缺省為 dry-run）")
    ap.add_argument("--dry-run", action="store_true", help="只印計畫不寫入")
    args = ap.parse_args()
    return run(dry_run=not args.apply)


if __name__ == "__main__":
    sys.exit(main())
