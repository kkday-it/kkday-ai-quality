"""7 支初判 prompt 的結構與措辭護欄（repo 檔案層）。

與 ``prompt_source.validate()`` 的分工：
- ``validate()``＝**存檔閘門**，只擋 ``_HARD_LINT_RULES``（現況已零違規者），因為 RuleManager
  熱編路徑不經過 repo，那是唯一擋得住線上改壞的地方。
- 本檔＝**repo 檔案層**，跑全部規則。對尚有存量違規的規則採「遞減閂鎖」：斷言違規數
  **不得超過**當前基線，只能往下不能往上。存量清零後把該規則移進 ``_HARD_LINT_RULES``，
  本檔的閂鎖同步改成 ``== 0``。

為何需要遞減閂鎖而非直接設硬閘門：新規則上線時常有存量違規（L1a 曾有 86 處、L1e 6 處），
直接硬擋會讓六支 prompt 全部存不了檔。清零工程分多批進行，期間需要一道「不會更糟」的保證。
（2026-08-10：兩者均已清零並升級為硬規則，`_RATCHET` 現為空。）
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from app.core.paths import PROMPTS_DIR
from app.judge import prompt_source as ps

# ── 遞減閂鎖基線（2026-08-10 實測）──
# 每批清理後**必須同步調降**這裡的數字；只准降不准升。降到 0 就把規則移進
# prompt_source._HARD_LINT_RULES 並把本表對應項刪掉（改由 test_no_hard_lint_violations 守）。
# 2026-08-10 現況：**空的**——所有規則的存量都已清零並升級進 `_HARD_LINT_RULES`
# （L1a 原 86 處由三詞制改寫清零；L1e 原 6 處由刪「取最核心」3 行 + 正向前提改負向測試清零）。
# 機制保留：日後新增一條「立意正確但有存量違規」的規則時，先掛這裡當遞減閂鎖，清零後再升級。
_RATCHET: dict[str, int] = {}

# ── 六域逐字共用的區塊（改一支就要六支一起改，否則判準分裂）──
# output_format / limitations 早已 6/6 逐位元組相同，只是一直沒被鎖（2026-08-11 補上）。
_SHARED_BLOCKS = (
    "judgment_rules",
    "abstain_rules",
    "critical_rules",
    "output_format",
    "limitations",
)

# ── decision_process 骨架：第 1／2／4／5 步六域逐字相同，只有第 3 步是域專屬 ──
# 有意讓某域的固定步分歧時，在此登記 {prompt_id: {步號索引}} 並寫理由；空 dict＝零例外。
# ⚠️ 比照 _RATCHET 的雙向斷言：登記了卻其實沒分歧也要紅，否則過期 entry 會靜默解鎖。
_ALLOWED_STEP_DIVERGENCE: dict[str, set[int]] = {}
_FIXED_STEP_IDX = (0, 1, 3, 4)  # 0-based；index 2＝第 3 步

# ── 例證配額 ──
# ❌**域外棄權**反例數才是健康指標，不是 ❌ 總數：六域全平行、每域只判自己，facet 唯一的
# 防誤收工具就是域外反例；域內指路（→ C-x-y）只是在同一個域裡搬椅子。
_MIN_POSITIVE = 2
_MIN_NEGATIVE_TOTAL = 3
_MIN_NEGATIVE_OUTBOUND = 2
# ❌/✅ 上限：反例遠多於正例的 facet 會過度保守而漏收（原本只是撰寫規範裡的建議值，
# 2026-08-10 實測 C-2-3 到 3.00、C-6-2 到 2.67 都沒紅燈，改為硬斷言）。
_MAX_NEG_POS_RATIO = 2.5


def _text(prompt_id: str) -> str:
    """讀 repo 檔案（**不走 prompt_source.load()**——那是 DB active 優先，會測到熱編版）。"""
    return (PROMPTS_DIR / f"{prompt_id}.md").read_text(encoding="utf-8")


def _system_of(prompt_id: str) -> str:
    """取 ``## System`` fence 內容＝實際送進模型的那段。"""
    return ps.parse_md(_text(prompt_id))["system"]


def _facets_of(system: str) -> dict[str, str]:
    """把 ``<facet_catalog>`` 拆成 {facet code: 該 facet 全文}。"""
    blocks = re.split(r"^■ ", system, flags=re.M)[1:]
    return {b.split()[0]: b for b in blocks if b.split()}


# ─────────────────────────── L1／PSCHEMA：措辭與 schema 形狀 ───────────────────────────
@pytest.mark.parametrize("prompt_id", ps.PROMPT_IDS)
def test_no_hard_lint_violations(prompt_id: str) -> None:
    """硬規則零違規——這些同時是 validate() 的存檔閘門，紅了代表線上也存不了檔。"""
    bad = [m for r, m in ps.lint_prompt(_text(prompt_id), prompt_id) if r in ps._HARD_LINT_RULES]
    assert not bad, f"{prompt_id} 硬規則違規：\n" + "\n".join(bad)


@pytest.mark.parametrize("rule,baseline", sorted(_RATCHET.items()))
def test_soft_lint_ratchet(rule: str, baseline: int) -> None:
    """軟規則遞減閂鎖：違規數只能往下，且清理後必須同步調降基線。

    兩道斷言缺一不可——只有上界的話，清到 0 之後基線還掛在 86，閂鎖就等於失效了。
    """
    n = sum(1 for pid in ps.PROMPT_IDS for r, _ in ps.lint_prompt(_text(pid), pid) if r == rule)
    assert n <= baseline, (
        f"{rule} 違規數自 {baseline} 升到 {n}——新增了不合規措辭。"
        f"改寫方式見 .claude/rules/prompt-authoring.md 的三詞制。"
    )
    assert baseline - n <= 5, (
        f"{rule} 基線 {baseline} 但實測僅 {n}——請把 _RATCHET['{rule}'] 調到 {n}"
        + ("；已清零，可移進 prompt_source._HARD_LINT_RULES" if n == 0 else "")
    )


# ─────────────────────────── L4：跨檔一致性 ───────────────────────────
@pytest.mark.parametrize("block", _SHARED_BLOCKS)
def test_shared_blocks_identical_across_domains(block: str) -> None:
    """六域共用區塊必須逐位元組相同——只改一支＝判準分裂，且不會報錯。

    ⚠️ 區塊標籤必須錨定行首：judgment_rules 內有一句「見 <abstain_rules>」的行內交叉引用，
    不錨定的話 regex 會從那個提及開始匹配、跨進別的區塊，造成連鎖誤報（2026-08-10 實際踩到）。
    """
    seen: dict[str, list[str]] = {}
    for pid in ps.DOMAIN_PROMPT_IDS:
        m = re.search(rf"^<{block}>$(.*?)^</{block}>$", _system_of(pid), re.S | re.M)
        assert m, f"{pid} 缺 <{block}> 區塊"
        seen.setdefault(hashlib.md5(m.group(1).encode()).hexdigest()[:8], []).append(pid)
    assert len(seen) == 1, f"<{block}> 六域不一致：{ {h: v for h, v in seen.items()} }"


def test_polarity_placeholder_hint_identical_across_domains() -> None:
    """六域 User 模板對 {POLARITY} 的括號說明須逐字一致——半套編輯會讓域間對 neutral 的理解分裂。"""
    hints = {
        m.group(1)
        for pid in ps.DOMAIN_PROMPT_IDS
        if (m := re.search(r"整體傾向：\{POLARITY\}（([^）]*)）", _text(pid)))
    }
    assert len(hints) == 1, f"{{POLARITY}} 括號說明不一致：{sorted(hints)}"


def test_schema_section_identical_across_domains() -> None:
    """六域 ``## Schema`` 逐位元組相同（機器契約；l2_code enum 由 Taxonomy 於 load 時派生注入）。"""
    seen = {
        hashlib.md5(
            re.search(r"## Schema\s*```json(.*?)```", _text(pid), re.S).group(1).encode()
        ).hexdigest()[:8]
        for pid in ps.DOMAIN_PROMPT_IDS
    }
    assert len(seen) == 1, f"六域 Schema 不一致：{sorted(seen)}"


# ─────────────────────────── L3：例證配額 ───────────────────────────
def _quota_of(facet_body: str, domain: str) -> tuple[int, int, int]:
    """回 (✅ 數, ❌ 總數, ❌ 域外棄權數)。域外＝該 ❌ 行不含本域 code（不是指路同域 facet）。"""
    pos = neg = outbound = 0
    for ln in facet_body.splitlines():
        t = ln.strip()
        if t.startswith("✅"):
            pos += 1
        elif t.startswith("❌"):
            neg += 1
            # ⚠️ 域外＝結論逐字為「不屬本域、棄權」（三詞制第二式）。
            # 2026-08-10 踩過：原本只判「不含本域 code」，於是第三式「不構成問題點、不歸因」
            # 也被計入——C-3-7 靠兩條**逐字重複**的第三式湊過 ≥2，實際零防誤收能力，
            # 而它正是嚴重度最高（應觸發供應商管理/法務關注）的 facet。
            if "不屬本域、棄權" in t:
                outbound += 1
    return pos, neg, outbound


@pytest.mark.parametrize("prompt_id", ps.DOMAIN_PROMPT_IDS)
def test_facet_example_quota(prompt_id: str) -> None:
    """每個 facet 的例證配額（2026-08-10 起全數達標，硬斷言）。"""
    domain = ps.rule_code_for_prompt(prompt_id).removeprefix("prompt_")
    bad: list[str] = []
    for code, body in _facets_of(_system_of(prompt_id)).items():
        pos, neg, out = _quota_of(body, domain)
        if pos < _MIN_POSITIVE:
            bad.append(f"{code}: ✅{pos} < {_MIN_POSITIVE}")
        if neg < _MIN_NEGATIVE_TOTAL:
            bad.append(f"{code}: ❌總{neg} < {_MIN_NEGATIVE_TOTAL}")
        if out < _MIN_NEGATIVE_OUTBOUND:
            bad.append(
                f"{code}: ❌域外棄權{out} < {_MIN_NEGATIVE_OUTBOUND}（域內指路與「不構成問題點」不算防誤收）"
            )
        if neg > pos * _MAX_NEG_POS_RATIO:
            bad.append(
                f"{code}: ❌/✅={neg / max(1, pos):.2f} > {_MAX_NEG_POS_RATIO}（反例壓過正例，facet 會過度保守）"
            )
    assert not bad, f"{prompt_id} 例證配額不足：" + "；".join(bad)


@pytest.mark.parametrize("prompt_id", ps.DOMAIN_PROMPT_IDS)
def test_no_duplicate_examples(prompt_id: str) -> None:
    """同一 facet 內禁止逐字重複的例證行。

    重複行不提供任何額外判別力，卻會把配額硬斷言灌滿——2026-08-10 的 C-3-7
    就是靠一組重複的 ❌ 通過 ``_MIN_NEGATIVE_OUTBOUND``。比對只取引號內的例句本體，
    因為「同一例句、換句話說結論」同樣是湊數。
    """
    dup: list[str] = []
    for code, body in _facets_of(_system_of(prompt_id)).items():
        seen: dict[str, str] = {}
        for ln in body.splitlines():
            t = ln.strip()
            if not (t.startswith("✅") or t.startswith("❌")):
                continue
            m = re.search(r"「(.+?)」", t)
            key = m.group(1) if m else t.lstrip("✅❌")
            if key in seen:
                dup.append(f"{code}: 例句「{key[:28]}…」重複出現（{seen[key][:1]} 與 {t[:1]}）")
            else:
                seen[key] = t
    assert not dup, f"{prompt_id} 例證重複：" + "；".join(dup)


# ─────────────────────────── 骨架 ───────────────────────────
_DOMAIN_REQUIRED_TAGS = (
    "judge_identity",
    "attribution_domain",
    "attribution_principles",
    "critical_rules",
    "domain_boundary",
    "facet_catalog",
    "decision_process",
    "judgment_rules",
    "abstain_rules",
    "output_format",
    "limitations",
)


@pytest.mark.parametrize("prompt_id", ps.DOMAIN_PROMPT_IDS)
def test_domain_skeleton_complete(prompt_id: str) -> None:
    """六域必備區塊齊全——攔「加規則時塞進當下看到的區塊」造成的骨架分裂。"""
    system = _system_of(prompt_id)
    missing = [t for t in _DOMAIN_REQUIRED_TAGS if f"<{t}>" not in system]
    assert not missing, f"{prompt_id} 缺區塊：{missing}"


def test_polarity_skeleton() -> None:
    """polarity 骨架——兩輪六域結構統一工程（bcfde20／5fa28e5）都跳過了這支，2026-08-10 補齊。"""
    system = _system_of(ps.POLARITY_ID)
    want = (
        "judge_identity",
        "critical_rules",
        "polarity_boundary",
        "sentiment_scale",
        "decision_process",
        "output_format",
        "limitations",
    )
    assert not (missing := [t for t in want if f"<{t}>" not in system]), (
        f"polarity 缺區塊：{missing}"
    )
    # <input_format> 已整塊退役（防注入句併入 critical_rules）——不留 tombstone
    assert "<input_format>" not in system, "<input_format> 已退役，不應復活"


# ─────────────────────────── 交付面 ───────────────────────────
def test_repo_files_pass_validate() -> None:
    """7 支 repo 檔案都通過存檔閘門——否則它們在 RuleManager 裡改一個字就存不回去。"""
    for pid in ps.PROMPT_IDS:
        ps.validate(_text(pid), pid)


def test_prompts_dir_has_exactly_the_declared_ids() -> None:
    """`prompts/` 的 md 檔與 `_PROMPT_RULE` 宣告一致——多一支少一支都會讓 structure() 靜默漏域。"""
    on_disk = {p.stem for p in Path(PROMPTS_DIR).glob("[0-9][0-9]_*.md")}
    assert on_disk == set(ps.PROMPT_IDS), f"檔案 {sorted(on_disk)} vs 宣告 {sorted(ps.PROMPT_IDS)}"


# ─────────────────────── 骨架跨檔一致性（2026-08-11） ───────────────────────
# ⚠️ 這批測試守的是「六域骨架不漂移」，**不是**判準品質，也**偵測不到根因 A**
#    （新規則寫進 L1/L3 卻沒下放到第 3 步）——那種情況下第 3 步與自己上一版逐字相同、
#    與其餘五域仍相異，以下所有斷言都會全綠。別把「骨架有測試」讀成「執行層安全」。
# ⚠️ 另一個邊界：validate() 只拿得到單支檔，跨檔一致性做不成存檔閘門，
#    因此 RuleManager 熱編路徑仍可把骨架改裂，只有 CI 跑 repo 檔才會紅。


def _steps_of(prompt_id: str) -> list[str]:
    """取 <decision_process> 的編號步驟；順帶驗形狀（恰 5 步、依序 1.~5.）。"""
    m = re.search(
        r"^<decision_process>$(.*?)^</decision_process>$", _system_of(prompt_id), re.S | re.M
    )
    assert m, f"{prompt_id} 缺 <decision_process>"
    steps = [
        ln.strip() for ln in m.group(1).strip().splitlines() if re.match(r"^\d\.\s", ln.strip())
    ]
    assert len(steps) == 5, f"{prompt_id} decision_process 應為 5 步，實得 {len(steps)}"
    for i, s in enumerate(steps, 1):
        assert s.startswith(f"{i}. "), f"{prompt_id} 第 {i} 步編號不符：{s[:30]}"
    return steps


def test_decision_process_fixed_steps_identical() -> None:
    """第 1／2／4／5 步六域必須逐字相同（第 3 步例外，見下一個測試）。"""
    steps = {pid: _steps_of(pid) for pid in ps.DOMAIN_PROMPT_IDS}
    for idx in _FIXED_STEP_IDX:
        seen: dict[str, list[str]] = {}
        for pid, ss in steps.items():
            if idx in _ALLOWED_STEP_DIVERGENCE.get(pid, set()):
                continue
            seen.setdefault(ss[idx], []).append(pid)
        assert len(seen) == 1, (
            f"decision_process 第 {idx + 1} 步在六域間分裂成 {len(seen)} 種："
            + "；".join(f"{v} → 「{k[:46]}…」" for k, v in seen.items())
        )
    # 雙向斷言：登記了例外卻其實沒分歧 → 紅（防過期 entry 靜默解鎖）
    for pid, idxs in _ALLOWED_STEP_DIVERGENCE.items():
        for idx in idxs:
            others = {steps[o][idx] for o in ps.DOMAIN_PROMPT_IDS if o != pid}
            assert steps[pid][idx] not in others, (
                f"_ALLOWED_STEP_DIVERGENCE[{pid}] 登記了第 {idx + 1} 步，但它與其他域相同——過期例外請移除"
            )


def test_decision_process_step3_is_domain_specific() -> None:
    """第 3 步必須六域兩兩相異，且不得被清成一句廢話。

    這條才是真正抓得到東西的斷言。實際會發生的不是「有人單獨改了第 4 步」，而是
    **複製既有檔當模板**（開新域或大改寫）時，第 3 步留著來源域的問句——那是該域執行層
    完全失效，但輸出仍 schema 合法、仍非空、零報錯，目前沒有任何其他機制抓得到。
    """
    third = {pid: _steps_of(pid)[2] for pid in ps.DOMAIN_PROMPT_IDS}
    dup: dict[str, list[str]] = {}
    for pid, s in third.items():
        dup.setdefault(s, []).append(pid)
    clones = {k: v for k, v in dup.items() if len(v) > 1}
    assert not clones, "decision_process 第 3 步被複製貼上：" + "；".join(
        f"{v} 共用「{k[:56]}…」" for k, v in clones.items()
    )
    short = [f"{pid}({len(s)}字)" for pid, s in third.items() if len(s) < 40]
    assert not short, f"第 3 步過短、恐已被清成廢話：{short}"


def test_domain_boundary_header_and_footer() -> None:
    """boundary 的 ⚠️ 標頭與收束句六域逐字相同，且收束句必須是區塊最後一個非空行。

    收束句用「是最後一行」而非「有出現」來斷言是刻意的：用 contains 的話，有人在收束句
    **之後**追加一條新裁定會全綠，但那條裁定在閱讀順序上已從「規則」降格成「附註」。
    """
    heads: dict[str, list[str]] = {}
    feet: dict[str, list[str]] = {}
    for pid in ps.DOMAIN_PROMPT_IDS:
        m = re.search(r"^<domain_boundary>$(.*?)^</domain_boundary>$", _system_of(pid), re.S | re.M)
        assert m, f"{pid} 缺 <domain_boundary>"
        lines = [ln.strip() for ln in m.group(1).strip().splitlines() if ln.strip()]
        h = [ln for ln in lines if ln.startswith("⚠️ 易混淆邊界裁定")]
        assert len(h) == 1, f"{pid} 的 ⚠️ 標頭應恰一行，實得 {len(h)}"
        heads.setdefault(h[0], []).append(pid)
        feet.setdefault(lines[-1], []).append(pid)
        assert any(ln.startswith("- ") for ln in lines), f"{pid} boundary 標頭後無任何裁定條目"
    assert len(heads) == 1, "boundary ⚠️ 標頭分裂：" + "；".join(f"{v}" for v in heads.values())
    assert len(feet) == 1, "boundary 收束句分裂（或有人在收束句之後追加了條目）：" + "；".join(
        f"{v} → 「{k[:46]}…」" for k, v in feet.items()
    )


def test_domain_block_order_identical() -> None:
    """六域的區塊出現順序必須一致（C-1 專屬的 evidence_gate 除外）。"""
    orders: dict[tuple[str, ...], list[str]] = {}
    for pid in ps.DOMAIN_PROMPT_IDS:
        tags = tuple(
            t for t in re.findall(r"^<([a-z_]+)>$", _system_of(pid), re.M) if t != "evidence_gate"
        )
        orders.setdefault(tags, []).append(pid)
    assert len(orders) == 1, "六域區塊順序不一致：" + "；".join(
        f"{v} → {k}" for k, v in orders.items()
    )
