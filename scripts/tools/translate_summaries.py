#!/usr/bin/env python3
"""一鍵批量轉譯既有判決摘要為繁體中文（DB 直接修改）。

背景：summary 改繁中（Part1）前，既有已判摘要為逐字原文（外語）。本腳本把「非中文為主」的既有
judgments.summary 逐條交 LLM 翻成繁體中文，**直接 UPDATE**（evidence_quote 逐字佐證不動）。

- 冪等：只轉「非中文為主」者（含韓/日/泰/拉丁字元佔比高）；已中文為主者跳過，可重複執行。
- 需真 LLM：走 active LLM 配置（settings）；stub 模式（無 token）直接拒跑，避免寫入垃圾。
- 單執行緒：規模小（~數十至數百條），且避開 ThreadPool 不繼承 LLM 配置 contextvar 而誤走 stub 的坑。

用法（backend venv）：
    cd backend
    .venv/bin/python ../scripts/tools/translate_summaries.py --dry-run                      # 只看需轉幾條 + 樣本（免 token）
    .venv/bin/python ../scripts/tools/translate_summaries.py --user you@kkday.com           # 用你帳號的 LLM 設定實際轉譯寫 DB
    .venv/bin/python ../scripts/tools/translate_summaries.py --user you@kkday.com --limit 20 # 先試跑前 20 條
"""
import argparse
import os
import re
import sys

# 讓腳本能 import backend 的 app 套件（不論從何處執行）
_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import text, update  # noqa: E402

from app.core.db import tables as T  # noqa: E402
from app.judge.llm import client  # noqa: E402

# 非中文偵測：Han 以外的字母系統（拉丁/韓/日假名/泰/西里爾）佔比高 → 判為需轉譯。
_HAN = re.compile(r"[一-鿿]")
_FOREIGN = re.compile(r"[A-Za-z가-힣぀-ヿ฀-๿Ѐ-ӿ]")

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["zh"],
    "properties": {"zh": {"type": "string"}},
}
_SYS = (
    "你是翻譯員。把使用者提供的『反饋問題摘要』翻成**繁體中文**（台灣用語），"
    "只輸出翻譯、保持原意精簡、不加註解。若原文已是繁體中文則原樣輸出。"
    '輸出 JSON：{"zh":"繁體中文翻譯"}'
)


def _needs_translate(s: str) -> bool:
    """摘要是否需轉譯：外文字元數 > Han，或 Han 佔比 < 40%。"""
    h = len(_HAN.findall(s))
    f = len(_FOREIGN.findall(s))
    return bool(s) and (f > h or (h + f > 0 and h / (h + f) < 0.4))


def _translate(s: str) -> str:
    """單條摘要 → 繁體中文（LLM）；失敗回空字串。"""
    out = client.chat_json(_SYS, s, "translate_summary", schema=_SCHEMA)
    return str(out.get("zh", "")).strip()


def main() -> None:
    ap = argparse.ArgumentParser(description="批量轉譯既有判決摘要為繁體中文")
    ap.add_argument("--dry-run", action="store_true", help="只統計需轉數量 + 印樣本，不寫 DB")
    ap.add_argument("--limit", type=int, default=0, help="只處理前 N 條（試跑）")
    ap.add_argument("--user", help="以此 user（email）的 active LLM 設定跑；標準命令列無 request context 需指定")
    args = ap.parse_args()

    # 載入指定 user 的 active LLM 設定到 contextvar（腳本無 request context，否則走 stub）。
    if args.user:
        from app.core import db as _db
        from app.core import settings as app_settings

        u = _db.get_user_by_email(args.user)
        if not u:
            print(f"❌ 找不到 user：{args.user}")
            sys.exit(1)
        app_settings.set_current(app_settings.effective_llm_dict(app_settings.load_settings(u["user_id"])))

    with T.get_engine().connect() as c:
        rows = c.execute(
            text("SELECT finding_id, summary FROM judgments WHERE summary IS NOT NULL AND summary <> ''")
        ).all()
    todo = [(fid, s) for fid, s in rows if _needs_translate(s)]
    if args.limit:
        todo = todo[: args.limit]
    print(f"總摘要 {len(rows)} 條 · 需轉譯（非中文為主）{len(todo)} 條")

    if args.dry_run:  # 只偵測/統計，不呼 LLM → 免 token 亦可跑
        for fid, s in todo[:10]:
            print(f"  {fid}: {s[:40]}")
        print("（dry-run，未寫 DB）")
        return

    if client.is_stub():  # 實際轉譯才需 LLM；stub（無 token）拒跑避免寫入垃圾
        print("❌ 目前為 stub 模式（無 LLM token）。請加 --user <你的 email> 以載入該帳號已設定的 LLM 配置再跑。")
        sys.exit(1)

    ok = 0
    for i, (fid, s) in enumerate(todo, 1):
        try:
            zh = _translate(s)
            if zh:
                with T.get_engine().begin() as c:
                    c.execute(update(T.judgments).where(T.judgments.c.finding_id == fid).values(summary=zh))
                ok += 1
        except Exception as e:  # noqa: BLE001  單條失敗不中斷整批
            print(f"  ⚠️ 失敗 {fid}: {e}")
        if i % 10 == 0 or i == len(todo):
            print(f"  進度 {i}/{len(todo)}（成功 {ok}）")
    print(f"✅ 完成：{ok}/{len(todo)} 條轉譯並直接更新 DB")


if __name__ == "__main__":
    main()
