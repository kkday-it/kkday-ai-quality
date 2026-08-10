"""7 支初判 prompt 的結構與措辭護欄（repo 檔案層）。

與 ``prompt_source.validate()`` 的分工：
- ``validate()``＝**存檔閘門**，只擋 ``_HARD_LINT_RULES``（現況已零違規者），因為 RuleManager
  熱編路徑不經過 repo，那是唯一擋得住線上改壞的地方。
- 本檔＝**repo 檔案層**，跑全部規則。對尚有存量違規的規則採「遞減閂鎖」：斷言違規數
  **不得超過**當前基線，只能往下不能往上。存量清零後把該規則移進 ``_HARD_LINT_RULES``，
  本檔的閂鎖同步改成 ``== 0``。

為何需要遞減閂鎖而非直接設硬閘門：L1a／L1e 現有 92 處存量違規，直接硬擋會讓六支
prompt 全部存不了檔。清零工程分多批進行，期間需要一道「不會更糟」的保證。
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
_RATCHET: dict[str, int] = {
    "L1e": 6,  # 禁詞：「取最核心、最直接的」×3、「且…已合理/正常/成功」×3（Phase 3 清）
}
# L1a 原有 86 處存量，2026-08-10 三詞制改寫後清零 → 已升級為 _HARD_LINT_RULES（存檔閘門直接擋）。

# ── 六域逐字共用的區塊（改一支就要六支一起改，否則判準分裂）──
_SHARED_BLOCKS = ("judgment_rules", "abstain_rules", "critical_rules")

# ── 例證配額 ──
# ❌**域外棄權**反例數才是健康指標，不是 ❌ 總數：六域全平行、每域只判自己，facet 唯一的
# 防誤收工具就是域外反例；域內指路（→ C-x-y）只是在同一個域裡搬椅子。
_MIN_POSITIVE = 2
_MIN_NEGATIVE_TOTAL = 3
_MIN_NEGATIVE_OUTBOUND = 2


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
    """六域共用區塊必須逐位元組相同——只改一支＝判準分裂，且不會報錯。"""
    seen: dict[str, list[str]] = {}
    for pid in ps.DOMAIN_PROMPT_IDS:
        m = re.search(rf"<{block}>(.*?)</{block}>", _system_of(pid), re.S)
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
            if not re.search(rf"{domain}-\d", t):
                outbound += 1
    return pos, neg, outbound


@pytest.mark.parametrize("prompt_id", ps.DOMAIN_PROMPT_IDS)
def test_facet_example_quota(prompt_id: str) -> None:
    """每個 facet 的例證配額。

    ⚠️ 目前為 **xfail 觀測模式**：C-5-2／C-5-3 的域外反例數為 0，Phase 6 補齊後拿掉 xfail。
    """
    domain = ps.rule_code_for_prompt(prompt_id).removeprefix("prompt_")
    bad: list[str] = []
    for code, body in _facets_of(_system_of(prompt_id)).items():
        pos, neg, out = _quota_of(body, domain)
        if pos < _MIN_POSITIVE:
            bad.append(f"{code}: ✅{pos} < {_MIN_POSITIVE}")
        if neg < _MIN_NEGATIVE_TOTAL:
            bad.append(f"{code}: ❌總{neg} < {_MIN_NEGATIVE_TOTAL}")
        if out < _MIN_NEGATIVE_OUTBOUND:
            bad.append(f"{code}: ❌域外{out} < {_MIN_NEGATIVE_OUTBOUND}（域內指路不算防誤收）")
    if bad:
        pytest.xfail(f"{prompt_id} 例證配額待補（Phase 6）：" + "；".join(bad))


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
    """polarity 骨架。

    ⚠️ 目前為 **xfail 觀測模式**：兩輪六域結構統一工程（bcfde20／5fa28e5）都跳過了這支，
    Phase 3 補齊後拿掉 xfail。
    """
    system = _system_of(ps.POLARITY_ID)
    want = ("judge_identity", "critical_rules", "decision_process", "output_format", "limitations")
    if missing := [t for t in want if f"<{t}>" not in system]:
        pytest.xfail(f"polarity 缺區塊（Phase 3）：{missing}")


# ─────────────────────────── 交付面 ───────────────────────────
def test_repo_files_pass_validate() -> None:
    """7 支 repo 檔案都通過存檔閘門——否則它們在 RuleManager 裡改一個字就存不回去。"""
    for pid in ps.PROMPT_IDS:
        ps.validate(_text(pid), pid)


def test_prompts_dir_has_exactly_the_declared_ids() -> None:
    """`prompts/` 的 md 檔與 `_PROMPT_RULE` 宣告一致——多一支少一支都會讓 structure() 靜默漏域。"""
    on_disk = {p.stem for p in Path(PROMPTS_DIR).glob("[0-9][0-9]_*.md")}
    assert on_disk == set(ps.PROMPT_IDS), f"檔案 {sorted(on_disk)} vs 宣告 {sorted(ps.PROMPT_IDS)}"
