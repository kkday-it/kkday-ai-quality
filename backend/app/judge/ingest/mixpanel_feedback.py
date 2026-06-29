"""mixpanel_feedback adapter — 第 6 進線管道（訂單明細頁行中關懷 feedback 埋點）。

與其他 SQL 來源不同：走 Mixpanel Raw Data Export API（官方 mixpanel-utils），逐筆事件落 CSV，
接既有 loader（entry.py 的 CSV 流程）。輸出檔名對齊命名規範 `mixpanel_tracker-<YYYYMMDDHHmm>.csv`。

注意：輸出為**逐筆原始事件**（每列一個 event，攤平所有埋點屬性），**不做聚合統計**。
聚合（count / 占比 / 關聯 prod_oid·order_oid 與其他來源）一律在本地分析階段做。

3 個事件（Impression 埋點尚未上線，export 會是空）：
  Click_OrderDetailPg_FeedbackBtn / Click_OrderDetailPg_FeedbackDetailBtn / Impression_OrderDetailPg_Feedback

認證走 env（Service Account；舊 Project Secret 2027-03-03 停用）：
  MIXPANEL_SA_USERNAME / MIXPANEL_SA_PASSWORD / MIXPANEL_PROJECT_ID / MIXPANEL_TOKEN
  （選用）MIXPANEL_OUT_DIR：匯出目錄，預設 <repo 根>/data/mixpanel_tracker

用法：
  pip install mixpanel-utils
  python -m app.judge.ingest.mixpanel_feedback --from 2026-01-01 --to 2026-06-30
"""

from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path

from app.core.config import env

# 預設拉取的 3 個 feedback 事件（Impression 未上線會回空）
DEFAULT_EVENTS: list[str] = [
    "Click_OrderDetailPg_FeedbackBtn",
    "Click_OrderDetailPg_FeedbackDetailBtn",
    "Impression_OrderDetailPg_Feedback",
]

# <repo 根>/data/mixpanel_tracker（檔案在 backend/app/judge/ingest/ → parents[4]=repo 根）
# 原始進線匯出統一收在根層 data/（與 backend/frontend 並列），各來源一個子資料夾
# （mixpanel_tracker / ai_review_summary / app_feedback / freshdesk_tickets / conversations / product_reviews）
_DEFAULT_OUT = Path(__file__).resolve().parents[4] / "data" / "mixpanel_tracker"


def _month_ranges(from_date: str, to_date: str) -> list[tuple[str, str]]:
    """把 [from, to] 切成逐月區間，避開 Raw Export API rate limit（60/hr、3/s）一次拉太大。"""
    start = date.fromisoformat(from_date)
    end = date.fromisoformat(to_date)
    out: list[tuple[str, str]] = []
    cur = start
    while cur <= end:
        nxt = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
        seg_end = min(end, date.fromordinal(nxt.toordinal() - 1))  # 當月最後一天
        out.append((cur.isoformat(), seg_end.isoformat()))
        cur = nxt
    return out


def _flatten(ev: dict) -> dict:
    """Mixpanel raw 事件 {event, properties:{...}} → 攤平單列；巢狀（List/dict）轉 JSON 字串。"""
    props = ev.get("properties", {}) or {}
    row: dict[str, str] = {"event": ev.get("event", "")}
    for k, v in props.items():
        if isinstance(v, (list, dict)):
            row[k] = json.dumps(v, ensure_ascii=False)
        else:
            row[k] = "" if v is None else str(v)
    return row


def _dedup_key(row: dict) -> tuple:
    """冪等鍵：優先 $insert_id；否則 event + distinct_id + time（+ order_mid 加固）。raw export 不去重，必須自己擋。"""
    if row.get("$insert_id"):
        return ("iid", row["$insert_id"])
    return (
        "k",
        row.get("event", ""),
        row.get("distinct_id", ""),
        row.get("time", ""),
        row.get("order_mid", ""),
    )


def export_mixpanel_feedback(
    from_date: str,
    to_date: str,
    out_dir: Path | None = None,
    events: list[str] | None = None,
) -> Path:
    """拉 [from, to] 全部 feedback 事件（逐月分批）→ mixpanel_tracker-<YYYYMMDDHHmm>.csv，回傳路徑。

    $insert_id 去重、巢狀欄攤平。需 Service Account env 憑證。"to_date" 含當日（inclusive）。
    """
    try:
        from mixpanel_utils import MixpanelUtils
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("缺套件：pip install mixpanel-utils") from e

    # 憑證統一經 config.env（import 時已載 backend/.env），不再裸讀 os.environ。
    creds = {
        "MIXPANEL_SA_USERNAME": env.mixpanel_sa_username,
        "MIXPANEL_SA_PASSWORD": env.mixpanel_sa_password,
        "MIXPANEL_PROJECT_ID": env.mixpanel_project_id,
        "MIXPANEL_TOKEN": env.mixpanel_token,
    }
    missing = [k for k, v in creds.items() if not v]
    if missing:
        raise RuntimeError(f"缺 env 憑證（Service Account）：{', '.join(missing)}")

    mp = MixpanelUtils(
        service_account_username=creds["MIXPANEL_SA_USERNAME"],
        service_account_password=creds["MIXPANEL_SA_PASSWORD"],
        project_id=int(creds["MIXPANEL_PROJECT_ID"]),
        token=creds["MIXPANEL_TOKEN"],
    )

    ev_json = json.dumps(events or DEFAULT_EVENTS, ensure_ascii=False)
    seen: set[tuple] = set()
    rows: list[dict] = []
    for seg_from, seg_to in _month_ranges(from_date, to_date):
        # query_export：in-memory 回逐筆事件 dict（免落一堆中繼檔）
        for ev in mp.query_export({"from_date": seg_from, "to_date": seg_to, "event": ev_json}):
            row = _flatten(ev)
            key = _dedup_key(row)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)

    if out_dir is None:
        env_dir = env.mixpanel_out_dir
        out_dir = Path(env_dir) if env_dir else _DEFAULT_OUT
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d%H%M")
    path = out_dir / f"mixpanel_tracker-{ts}.csv"

    # union 所有出現過的屬性當表頭（event 固定排首）
    cols: list[str] = ["event"]
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="拉 Mixpanel feedback 事件 → mixpanel_tracker-<ts>.csv"
    )
    ap.add_argument("--from", dest="from_date", required=True, help="起始日 YYYY-MM-DD")
    ap.add_argument("--to", dest="to_date", required=True, help="結束日 YYYY-MM-DD（含當日）")
    ap.add_argument(
        "--out-dir",
        dest="out_dir",
        default=None,
        help="匯出目錄（預設 <repo 根>/data/mixpanel_tracker 或 MIXPANEL_OUT_DIR）",
    )
    a = ap.parse_args()
    out = export_mixpanel_feedback(a.from_date, a.to_date, Path(a.out_dir) if a.out_dir else None)
    print(f"WROTE {out} ({out.stat().st_size} bytes)")
