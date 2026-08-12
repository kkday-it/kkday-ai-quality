#!/usr/bin/env python3
"""覆蓋歸屬回歸鎖 — 對已定案的跨域場景跑完整六域，斷言主歸因落在拍板的那個域。

定位：`eval_prompt_single.py`（單支指標）與 `eval_equivalence.py`（管線等價性）都**測不到
跨域覆蓋**——前者的參照集是「本域正例／他域負例各半」，棄權率與多報率在多數域已觸頂觸底；
後者比的是管線前後差異、不驗歸屬正確性。本工具補這個缺口。

它同時擋兩種失敗，而且兩者在聚合指標上都看不出來：
  - **無人接**：某域說「不屬本項」而沒有別的域認領 → 六域皆棄權、落 non_issue、零報錯
  - **收錯／雙收**：改半邊覆蓋斷裂最典型的後果（只表現為多報率微升，落在噪音帶內）

⚠️ 主歸因＝`is_primary` 那條，不是「恰好一個域觸發」——設計上就允許多域觸發、由合流層
（`prejudge._gate_attrs`）依信心收斂。次要域會一併印出供人檢視。

用法（scripts/ 已 bind mount，免 docker cp）：
    docker compose -f docker-compose.dev.yml exec -T backend \
        python /app/scripts/tools/verify_coverage.py [--repeats 3]

⚠️ 真打 LLM（每個場景 7 次呼叫），不進 pre-commit；改完 prompt 且**發完 DB 版**後手動跑。
未發版會測到舊版且不會報錯（`prompt_source.load()` 是 DB active 優先）。
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.core import settings as app_settings
from app.judge import prejudge
from app.judge.llm import client

# (場景, 拍板歸屬域, 合成反饋)。新增覆蓋定案時在此登記，該定案才有回歸保護。
# 拍板歸屬域填 `_ABSTAIN`（"—"）＝**預期六域皆棄權**，用來鎖三詞制第三式「不構成問題點」的
# 裁定。少了這個值域，工具只鎖得住「該收的有沒有收」，鎖不住「不該收的有沒有被亂收」——
# 而後者正是放寬覆蓋時最容易壞掉的一半。
_ABSTAIN = "—"
CASES: list[tuple[str, str, str]] = [
    ("#1 頁面寫有X現場沒有", "supplier",
     "網頁明明寫含迎賓飲料，到現場店員說根本沒有這個東西，白期待一場。"),
    ("#2 公共區域衛生", "quality",
     "休息站的廁所超級髒，地上都是水也沒人整理，很不舒服。"),
    ("#3 降規格", "supplier",
     "明明付的是 SUV 等級的錢，現場來的卻是一台一般小轎車，感覺被降規格。"),
    ("#4 保證成團", "content",
     "頁面明明標了「保證成團」，結果出發前還是以人數不足為由把行程取消了。"),
    ("#5a 絕對份量少", "quality",
     "這餐的份量真的太少了，同桌大家都沒吃飽就收走了。"),
    ("#6 改期未生效", "platform",
     "在網站上按了改期申請，過了一個禮拜訂單頁面上還是原本的日期，也沒有任何通知。"),
    ("#7 重複扣款", "platform",
     "同一筆訂單被重複扣款兩次，但訂單頁只顯示一筆，錢就這樣被扣走。"),
    ("#8 客服入口打不開", "platform",
     "App 裡的線上客服點進去一直轉圈圈，根本連不上任何人。"),
    ("贈品破損", "quality",
     "隨行程附贈的紀念明信片印刷起泡、邊角整個破損，收到很傻眼。"),
    ("租借配件壞", "quality",
     "租借的語音導覽機按鍵壞掉，按了完全沒反應，整趟等於沒導覽。"),
    # 2026-08-12 定案：C-1 的資訊載體由「商品頁」放寬為「商品資訊」（＋隨訂購確認／憑證交付的
    # 使用說明）。成因是三方全棄權的靜默漏判——C-4 三個 facet 都要求「終局卡死」、收不了
    # 「過程反覆失敗但最終走通」，而 C-1-5 原本限定「指涉對象必須是商品頁的敘述本身」。
    ("#9 隨貨說明矛盾", "content",
     "隨憑證附的紙本說明和 QR code 裡的內容不一致，照著設定失敗好幾次，最後才找到正確的版本。"),
    # 反向守備：C-1 放寬後不得把「憑證本體缺可核銷要素」一併收走（那是 C-4-2）。
    # 只加正向案例會讓「改過頭」與「改對了」在報表上長得一模一樣。
    ("#9b 憑證缺兌換碼", "platform",
     "拿到的憑證上完全沒有任何兌換碼或 QR，就像一張普通的資訊說明單，根本沒辦法核銷。"),
    # 2026-08-12 定案：C-4 三個 facet 原本都以「終局卡死」為門檻（尚未成功用上／換票卡住／
    # 平台壞了），收不了「過程反覆失敗但最終走通」。新增第三型態＝流程摩擦。
    ("#10 流程繞路最終完成", "platform",
     "兌換路徑繞得莫名其妙，換了三個地方、跑了兩趟才終於把票換到手。"),
    # 反向守備兩條：C-4 放寬後**不得**把這兩類一併收走，否則等於推翻既有邊界。
    ("#10b 流程順但東西差", "quality",
     "兌換過程一路順暢沒卡到，但換到的餐券內容很寒酸，東西也不新鮮。"),
    ("#10c 說明沒寫非流程繞", "content",
     "頁面完全沒說要先線上預約才能入場，我到現場才知道有這個步驟。"),
    # 輕語氣變體：只給流程一句負向評價、未敘述失敗次數。原本 (d) 要求「原文須寫出過程」，
    # 真實案例 3336305 就卡在這道門檻上（3/3 棄權），故放寬並在此鎖住。
    ("#10d 流程複雜輕語氣", "platform",
     "這張券要打開的途徑不太方便，路徑實在太複雜了。"),
    # 2026-08-12 定案（真實案例 3358488）：行前該給的資訊拖到最後一刻／要催才給 → C-3-4。
    # 原本 C-3-4 的列舉只有「成團未告知」「憑證未如期發出」，無此語彙；且另有一條
    # ❌「已應變、只是告知較慢」會被誤讀成涵蓋本型，已一併收窄回風險應變語境。
    ("#11 行前資訊要催才給", "supplier",
     "接送業者硬是拖到班機抵達前四小時才肯給司機資訊，我一再要求才拿到，態度還很差。"),
    # 反向守備：風險應變情境下「已應變且已告知、只是公告慢一點」＝無失職事實，六域皆應棄權。
    # C-3-6 對同型情境的裁定就是第三式（「商家有先通知也協助改期…不構成問題點」），
    # C-3-4 原本卻寫成第二式「不屬本域、棄權」，兩者矛盾；已一併改齊。
    ("#11b 應變已做只是公告慢", _ABSTAIN,
     "颱風當天供應商有安排替代行程也照常出團，只是公告晚了一個多小時才發出來。"),
]


def _primary_domain(text: str, model: str, tag: str) -> tuple[str | None, str, list[str]]:
    """跑一次完整六域，回 (主歸因域, 主歸因描述, 次要域清單)。"""
    item = {"content": text, "source": "reviews", "source_id": f"probe_{tag}", "raw": {}}
    findings = [
        f if isinstance(f, dict) else f.model_dump()
        for f in prejudge.to_findings(item, model=model)
    ]
    attrs = [f for f in findings if f.get("l1_domain_code")]
    if not attrs:
        return None, "—", []
    prim = next((f for f in attrs if f.get("is_primary")), attrs[0])
    dom = prim["l1_domain_code"]
    others = sorted({f["l1_domain_code"] for f in attrs} - {dom})
    return dom, f"{prim['l2_code']}@{prim.get('confidence') or 0:.2f}", others


def main() -> None:
    """CLI：跑全部場景，任一場景主歸因不符即以非零碼退出。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=1, help="每場景重跑次數，取多數決（LLM 非決定性）")
    ap.add_argument("--area", default="prejudge")
    args = ap.parse_args()

    app_settings.set_current(app_settings.effective_llm_dict(app_settings.load_settings(), area=args.area))
    if client.is_stub():
        raise SystemExit("❌ stub 模式（無可用 LLM token），拒跑避免假結果。")
    client.set_llm_cache_read(False)  # 量測真實行為
    client.set_usage_context({"job_id": "verify_coverage"})

    bad = 0
    for name, want, text in CASES:
        votes = [_primary_domain(text, app_settings.current().get("model"), name) for _ in range(args.repeats)]
        dom = Counter(v[0] for v in votes).most_common(1)[0][0]
        detail, others = next((d, o) for g, d, o in votes if g == dom)
        got = dom or _ABSTAIN  # 六域皆棄權時 _primary_domain 回 None，統一成 _ABSTAIN 才比得了
        if got == want:
            okmark = "✅"
        elif want == _ABSTAIN:
            okmark = f"❌ 不該收卻收了 {dom}"
        else:
            okmark = "❌ 無人接" if dom is None else f"❌ 主歸因為 {dom}"
        bad += got != want
        tail = f"  次要域 {others}" if others else ""
        spread = "" if args.repeats == 1 else f"  ({Counter(v[0] for v in votes).most_common()})"
        print(f"  {okmark:<18} {name:<18} 期望 {want:<9} 主歸因 {detail}{tail}{spread}", flush=True)

    print(f"\n  通過 {len(CASES) - bad}/{len(CASES)}")
    if bad:
        raise SystemExit(f"❌ {bad} 個場景的主歸因與定案不符——覆蓋斷裂復發或被改半邊。")


if __name__ == "__main__":
    main()
