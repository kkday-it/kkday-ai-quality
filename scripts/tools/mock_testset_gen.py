#!/usr/bin/env python3
"""Mock 測試集生成器（零 LLM，確定性生成）——供 V1 Prompt 穩定性驗證計畫使用。

讀取 config/ai_judge/rule_C-1.json ~ rule_C-6.json 六域歸因分類樹，為每個 L1 域
（content 商品內容 / product_quality 商品品質 / supplier 供應商履約 /
redemption 平台與系統 / service 客服營運 / customer 理解期待）各生成正樣本 100 條
＋負樣本 100 條（負樣本＝屬其他域、易與本域混淆的樣本，仍標明其真實 gold 域）。

樣本來源（皆取材自既有規則配置，不憑空杜撰文案）：
- 正樣本：該域所有葉節點的 positive_cases（既有客訴語感例句）＋該域各層級節點的
  positive_cases（主題級描述，補量用）＋散落在「任一域」negative_cases 中以
  『引號』標註且「→ 歸 <本域 code>」的真實例句（他域眼中「看似 X 實為本域」的案例）。
- 負樣本：本域 negative_cases 中「看似本域、實為他域」的例句（同一句話，對本域是
  負樣本、對他域是正樣本——雙重利用）；不足 100 則以其他域的正樣本池補足並標明其
  真實 gold 域。

C-1（商品內容）例外：其葉節點 positive_cases/negative_cases 是「頁面文案範例」
（好文案 vs 壞文案），並非客訴語句，故 C-1 改以每個葉節點的 canon 語義（canon 本身
即「嫌 XXX」的客訴原型描述）改寫成第一人稱抱怨核心片語作為種子。

所有擴增（同義詞替換 / 語氣變換 / 句式重組 / 跨類干擾 / 異常輸入模擬）皆透過
`random.Random` 搭配 `zlib.crc32` 衍生的確定性子種子產生（不依賴 Python 內建
`hash()`，避免 PYTHONHASHSEED 隨機化導致跨次執行不可重現），同一 --seed 保證輸出
逐位元組相同，全程不呼叫任何 LLM。

用法：
    cd backend && .venv/bin/python ../scripts/tools/mock_testset_gen.py \
        --out ../tmp/mock_testset/testset_v1.jsonl [--seed 42]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import zlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# ── 路徑與規則檔常數 ──────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RULE_DIR = _REPO_ROOT / "config" / "ai_judge"
_DEFAULT_OUT = _REPO_ROOT / "tmp" / "mock_testset" / "testset_v1.jsonl"

# 六域代碼固定順序（對應 rule_C-1.json ~ rule_C-6.json，非任意排序）
DOMAIN_CODES: list[str] = ["C-1", "C-2", "C-3", "C-4", "C-5", "C-6"]

# 每域正／負樣本目標數量（Slack 定案：正/負各 100）
SAMPLES_PER_BUCKET = 100

# 中文域名 → rule code 別名表：negative_cases 常以中文 label 而非 code 標註跨域目標，
# 各檔措辭略有出入（如「客人理解」/「理解期待」皆指 C-6），故建別名表統一解析。
_LABEL_ALIASES: dict[str, str] = {
    "商品內容": "C-1",
    "商品品質": "C-2",
    "供應商履約": "C-3",
    "平台與系統": "C-4",
    "平台功能": "C-4",
    "客服營運": "C-5",
    "客人理解": "C-6",
    "理解期待": "C-6",
}

# 跨域 hint 解析用正則（優先序：代碼 > 中文域名 arrow > 括號附註，見 _resolve_target_code）
_CODE_ARROW_RE = re.compile(r"歸\s*(C-\d+(?:-\d+)*)")
_LABEL_ARROW_RE = re.compile(r"歸\s*([^\s，。、（）(]+)")
_PAREN_RE = re.compile(r"（屬於?([^）]+)）")
_QUOTE_RE = re.compile(r"『([^』]+)』")
# C-1 canon 專用：去除排除子句（「，非...」／「；非...」／「、非...」），只留客訴核心語意
_CANON_EXCLUDE_RE = re.compile(r"[，；、](非|→)")

# 第三人稱 → 第一人稱簡易替換（確定性字面替換，非 NLP，僅拉近客訴語感）
_PERSON_REPLACEMENTS = [("旅客", "我"), ("顧客", "我"), ("客人", "我"), ("使用者", "我")]

# 句式重組 carrier（抽象種子轉完整第一人稱抱怨句用；「主詞+情境+抱怨」骨架）
_CARRIER_TEMPLATES = [
    "這次體驗下來，{core}。",
    "老實說，{core}，讓我蠻困擾的。",
    "整個過程中，{core}。",
    "說真的，{core}，覺得不太OK。",
    "印象最深的是，{core}。",
    "以這次來說，{core}，有點無言。",
]

# 語氣強度詞（fuzzy 變體：語氣變換用）
_TONE_WORDS = ["超爛", "很差", "不太行", "有點失望", "蠻扯的", "說不過去"]
_FUZZY_TEMPLATES = [
    "說真的{tone}，{core}",
    "{core}，真的{tone}",
    "老實講有點{tone}，{core}",
]

# 同義詞替換表（synonym 變體用；key 命中即替換第一個出現處，避免過度改寫）
_SYNONYM_MAP: dict[str, list[str]] = {
    "很差": ["超差", "很糟", "不太好", "有夠糟"],
    "沒有": ["完全沒", "根本沒", "壓根沒"],
    "覺得": ["感覺", "認為"],
    "問題": ["狀況", "毛病"],
    "客服": ["客服人員", "客服團隊"],
    "導遊": ["領隊", "隨行導遊"],
    "司機": ["駕駛", "師傅"],
    "退款": ["退費", "退錢"],
    "沒寫": ["沒有寫", "完全沒提"],
    "不清楚": ["講不明白", "說不明白"],
}
# 無關鍵詞可替換時的保底尾句（確保 synonym 變體仍與原句有可辨識差異）
_SYNONYM_FALLBACK_TAILS = ["，真的很無言", "，讓人很傻眼", "，有夠誇張", "，很不能接受"]

# 異常輸入模擬用符號／emoji（abnormal 變體：模擬雜訊字元/emoji/錯字）
_NOISE_TOKENS = ["😡", "😭", "🙄", "💢", "!!!", "???", "...", "－－", "囧"]

# 跨類干擾用短句（cross_noise 變體：夾帶「他域字眼」但主抱怨仍屬本域）
# 每域一句與該域主題相關、但非抱怨核心的讓步子句，取自他域主題製造干擾
_CROSS_NOISE_PHRASES: dict[str, list[str]] = {
    "C-1": ["雖然頁面介紹寫得還算清楚", "商品資訊部分倒是還好"],
    "C-2": ["雖然餐點口味算OK", "設施品質基本上沒問題"],
    "C-3": ["雖然導遊態度還不錯", "供應商配合度算可以"],
    "C-4": ["雖然開通過程算順利", "系統操作沒有卡關"],
    "C-5": ["雖然客服回覆速度還算快", "客服態度也還可以"],
    "C-6": ["雖然可能是我自己期待比較高", "也許是我自己會錯意"],
}

# variant_type 目標配比（100 條內的抽樣比例；四捨五入誤差併入 common 修正）
_VARIANT_RATIO: dict[str, float] = {
    "common": 0.30,
    "fuzzy": 0.20,
    "synonym": 0.20,
    "cross_noise": 0.20,
    "abnormal": 0.10,
}


@dataclass
class Seed:
    """單一候選抱怨句種子（尚未擴增為最終樣本文字）。

    Attributes:
        text: 種子文字。concrete=True 時已是完整第一人稱風格客訴句；
            concrete=False 時僅為主題片語，渲染時需先經句式重組（_carrier_wrap）。
        concrete: 是否已是可直接使用的完整句子。
        source_leaf: 來源節點 code（供輸出 seed_node 欄位追溯）。
    """

    text: str
    concrete: bool
    source_leaf: str


# ── 規則樹讀取與走訪 ──────────────────────────────────────────────────────


def _load_rules() -> dict[str, dict]:
    """讀取六域規則檔，回傳 {rule_code: 規則 JSON}。"""
    rules: dict[str, dict] = {}
    for code in DOMAIN_CODES:
        path = _RULE_DIR / f"rule_{code}.json"
        with path.open(encoding="utf-8") as f:
            rules[code] = json.load(f)
    return rules


def _walk(node: dict):
    """深度優先走訪規則樹節點（generator）。"""
    yield node
    for child in node.get("children") or []:
        yield from _walk(child)


def _code_to_domain(rules: dict[str, dict]) -> dict[str, tuple[str, str]]:
    """rule code → (機器域值, 中文域名)，如 'C-1' → ('content', '商品內容')。"""
    out = {}
    for code, rule in rules.items():
        top = rule["tree"][0]
        out[code] = (top["domain"], top["label"])
    return out


# ── 跨域 hint 解析 ────────────────────────────────────────────────────────


def _resolve_target_code(text: str) -> tuple[str, str] | None:
    """解析單條 negative_case 文字中的「跨域指向」，回傳 (核心描述文字, 目標 rule code)。

    優先序：① 代碼（如「→ 歸 C-3-1-1」）② 中文域名 arrow（如「→歸供應商履約」）
    ③ 括號附註（如「（屬供應商履約）」）。三者皆未命中（如純敘述、無指向目標的
    negative_case）回傳 None——呼叫端應視為無跨域訊號略過，非例外狀況。

    Args:
        text: 單條 negative_case 字串。

    Returns:
        (核心描述文字, 目標 rule code) 或 None。
    """
    m = _CODE_ARROW_RE.search(text)
    if m:
        prefix = "-".join(m.group(1).split("-")[:2])
        if prefix in DOMAIN_CODES:
            core = text.split("→", 1)[0].strip("，。、 ")
            return core, prefix

    m = _LABEL_ARROW_RE.search(text)
    if m:
        label = m.group(1)
        for alias, code in _LABEL_ALIASES.items():
            if alias in label:
                core = text.split("→", 1)[0].strip("，。、 ")
                return core, code

    m = _PAREN_RE.search(text)
    if m:
        label = m.group(1)
        for alias, code in _LABEL_ALIASES.items():
            if alias in label:
                core = _PAREN_RE.sub("", text).strip("，。、 ")
                return core, code

    return None


def _canon_core(canon: str) -> str:
    """C-1 專用：把 canon 欄位（如「嫌名稱誇大/促銷/與實際不符，非...」）轉為抱怨核心片語。

    去除排除子句（「，非...」／「；非...」／「、非...」，含後續 →code 指向）與開頭
    「嫌」字，保留「使用者在嫌什麼」的語意核心，供 _carrier_wrap 句式重組使用。
    """
    core = _CANON_EXCLUDE_RE.split(canon)[0]
    return core[1:] if core.startswith("嫌") else core


def _collect_seeds(
    rules: dict[str, dict],
) -> tuple[dict[str, list[Seed]], dict[str, list[tuple[Seed, str]]]]:
    """走訪六域規則樹，彙整每域「正樣本種子池」與「負樣本 hint 池（真實歸屬他域）」。

    Args:
        rules: `_load_rules()` 回傳的六域規則 JSON。

    Returns:
        (pos_seeds, neg_hints)：
        - pos_seeds: {rule_code: [Seed,...]}——text 真正歸屬該域者。
        - neg_hints: {rule_code: [(Seed, gold_code), ...]}——埋在該域 negative_cases
          裡、經解析後發現「實為他域」的例句（gold_code 為其真實歸屬域）。
    """
    pos_seeds: dict[str, list[Seed]] = {c: [] for c in DOMAIN_CODES}
    neg_hints: dict[str, list[tuple[Seed, str]]] = {c: [] for c in DOMAIN_CODES}

    for code, rule in rules.items():
        top = rule["tree"][0]
        for node in _walk(top):
            is_leaf = not node.get("children")

            if code == "C-1":
                # C-1 例外：葉節點 pos/neg 為頁面文案範例，改以 canon 語義改寫種子
                if is_leaf and node.get("canon"):
                    core = _canon_core(node["canon"])
                    if core:
                        pos_seeds[code].append(Seed(text=core, concrete=False, source_leaf=node["code"]))
            elif is_leaf:
                for p in node.get("positive_cases") or []:
                    pos_seeds[code].append(Seed(text=p, concrete=True, source_leaf=node["code"]))

            # 非葉節點（含頂層 L1）的 positive_cases 為主題級描述，各域皆可補量使用
            if not is_leaf:
                for p in node.get("positive_cases") or []:
                    pos_seeds[code].append(Seed(text=p, concrete=False, source_leaf=node["code"]))

            # 跨域 hint：解析 negative_cases（任一層級）找「看似本域、實為他域」例句
            for n in node.get("negative_cases") or []:
                resolved = _resolve_target_code(n)
                if not resolved:
                    continue
                core, target_code = resolved
                quote_m = _QUOTE_RE.search(n)
                concrete = bool(quote_m)
                seed_text = quote_m.group(1) if quote_m else core
                if not seed_text:
                    continue
                seed = Seed(text=seed_text, concrete=concrete, source_leaf=node["code"])
                pos_seeds[target_code].append(seed)
                if target_code != code:
                    neg_hints[code].append((seed, target_code))

    return pos_seeds, neg_hints


# ── 確定性亂數與擴增 ──────────────────────────────────────────────────────


def _sub_seed(base_seed: int, *parts: str) -> int:
    """由 base seed + 任意字串組合出穩定子種子（crc32，避免 Python `hash()` 隨機化）。

    Python 內建 `hash(str)` 受 PYTHONHASHSEED 影響，同一輸入跨進程可能不同值；
    crc32 對相同輸入永遠回傳相同整數，確保 --seed 相同時逐位元組可重現。
    """
    key = f"{base_seed}:" + ":".join(parts)
    return zlib.crc32(key.encode("utf-8"))


def _build_variant_cycle(n: int, rng: random.Random) -> list[str]:
    """依 _VARIANT_RATIO 配比產生長度 n 的 variant_type 序列（已洗牌，非成塊排列）。"""
    counts = {k: round(v * n) for k, v in _VARIANT_RATIO.items()}
    diff = n - sum(counts.values())  # 四捨五入可能與 n 有 ±誤差，併入 common 修正
    counts["common"] += diff
    cycle = [vt for vt, cnt in counts.items() for _ in range(cnt)]
    rng.shuffle(cycle)
    return cycle


def _normalize_person(text: str) -> str:
    """第三人稱指涉詞 → 第一人稱（確定性字面替換）。"""
    for a, b in _PERSON_REPLACEMENTS:
        text = text.replace(a, b)
    return text


def _carrier_wrap(core: str, rng: random.Random) -> str:
    """句式重組：把抽象核心片語套入「主詞+情境+抱怨」carrier 模板組成完整句子。"""
    template = rng.choice(_CARRIER_TEMPLATES)
    return template.format(core=core)


def _strip_terminal_punct(text: str) -> str:
    """去除句尾標點（。！？」』），供還需接續文字的組合模板使用，避免「。，」重複標點。"""
    return text.rstrip("。！？」』")


def _apply_synonym(text: str, rng: random.Random) -> str:
    """同義詞替換：命中表內關鍵詞即替換第一個出現處；未命中則附保底尾句製造差異。"""
    for key, alts in _SYNONYM_MAP.items():
        if key in text:
            return text.replace(key, rng.choice(alts), 1)
    return _strip_terminal_punct(text) + rng.choice(_SYNONYM_FALLBACK_TAILS)


def _apply_fuzzy(core: str, rng: random.Random) -> str:
    """語氣變換：插入強度詞（超爛/很差/不太行/有點失望…）模擬不同抱怨強度。"""
    tone = rng.choice(_TONE_WORDS)
    core = _strip_terminal_punct(core)
    template = rng.choice(_FUZZY_TEMPLATES)
    return template.format(tone=tone, core=core)


def _apply_cross_noise(core: str, own_domain: str, rng: random.Random) -> str:
    """跨類干擾：夾帶「他域字眼」的讓步子句，但保留主抱怨仍屬本域（own_domain）。"""
    other_domains = [c for c in DOMAIN_CODES if c != own_domain]
    other = rng.choice(other_domains)
    phrase = rng.choice(_CROSS_NOISE_PHRASES[other])
    return f"{phrase}，但{core}"


def _apply_abnormal(text: str, rng: random.Random) -> str:
    """異常輸入模擬：隨機插入雜訊字元/emoji、模擬打字重複，測試判官對髒輸入的穩定度。"""
    segments = text.split("，")
    noisy: list[str] = []
    for seg in segments:
        noisy.append(seg)
        if rng.random() < 0.5:
            noisy.append(rng.choice(_NOISE_TOKENS))
    text = "，".join(noisy)
    if text and rng.random() < 0.5:
        idx = rng.randrange(len(text))
        text = text[:idx] + text[idx] + text[idx:]  # 模擬重複打字，如「真真的的」
    return text


def _render(seed: Seed, variant_type: str, gold_code: str, rng: random.Random) -> str:
    """依 variant_type 對種子做確定性擴增，回傳最終樣本文字。

    Args:
        seed: 候選種子。
        variant_type: common|fuzzy|synonym|cross_noise|abnormal。
        gold_code: 該種子的真實歸屬 rule code（cross_noise 用於排除自身域）。
        rng: 確定性亂數來源。
    """
    core = _normalize_person(seed.text)
    if not seed.concrete:
        core = _carrier_wrap(core, rng)  # 抽象片語先句式重組成完整句子

    if variant_type == "common":
        return core
    if variant_type == "synonym":
        return _apply_synonym(core, rng)
    if variant_type == "fuzzy":
        return _apply_fuzzy(core, rng)
    if variant_type == "cross_noise":
        return _apply_cross_noise(core, gold_code, rng)
    if variant_type == "abnormal":
        return _apply_abnormal(core, rng)
    return core  # 防禦性 fallback（理論上不會走到，_VARIANT_RATIO 已窮舉所有型態）


# ── bucket 組裝 ───────────────────────────────────────────────────────────


def _build_bucket(
    seeds_with_domain: list[tuple[Seed, str]],
    own_domain: str,
    role: str,
    base_seed: int,
    code_to_domain: dict[str, tuple[str, str]],
) -> list[dict]:
    """依種子池擴增為固定 SAMPLES_PER_BUCKET 筆樣本（不足則循環重用種子＋不同變體）。

    Args:
        seeds_with_domain: [(seed, 該 seed 的真實 gold rule code), ...]。
        own_domain: 本次生成所屬的 bucket 域（僅用於輸出欄位標註與統計，不影響 gold）。
        role: "positive" 或 "negative"（bucket 角色，寫入輸出供除錯/統計用）。
        base_seed: 頂層 --seed，供衍生本 bucket 專屬的確定性子亂數流。
        code_to_domain: rule code → (機器域值, 中文域名)。

    Returns:
        SAMPLES_PER_BUCKET 筆樣本 dict 清單。

    Raises:
        ValueError: 種子池為空（規則配置可能有缺漏，非預期狀況，不靜默略過）。
    """
    if not seeds_with_domain:
        raise ValueError(f"{own_domain} {role} 種子池為空，規則配置可能有缺漏")

    cycle_rng = random.Random(_sub_seed(base_seed, own_domain, role, "cycle"))
    variant_cycle = _build_variant_cycle(SAMPLES_PER_BUCKET, cycle_rng)

    rows = []
    for i in range(SAMPLES_PER_BUCKET):
        seed, gold_code = seeds_with_domain[i % len(seeds_with_domain)]
        variant_type = variant_cycle[i]
        gold_machine, gold_label = code_to_domain[gold_code]
        render_rng = random.Random(_sub_seed(base_seed, own_domain, role, str(i)))
        text = _render(seed, variant_type, gold_code, render_rng)
        rows.append(
            {
                "id": f"{own_domain}-{role[:3]}-{i + 1:03d}",
                "text": text,
                "gold_l1": gold_machine,
                "gold_l1_label": gold_label,
                "variant_type": variant_type,
                "seed_node": seed.source_leaf,
                "bucket_domain": own_domain,
                "bucket_role": role,
            }
        )
    return rows


def generate_testset(seed: int) -> tuple[list[dict], dict[str, tuple[str, str]]]:
    """產生完整 mock 測試集（6 域 × 200 筆 = 1200 筆）。

    Args:
        seed: 確定性亂數種子，相同 seed 保證輸出逐位元組相同。

    Returns:
        (rows, code_to_domain)：rows 為完整樣本清單；code_to_domain 供 stdout 統計使用。
    """
    rules = _load_rules()
    code_to_domain = _code_to_domain(rules)
    pos_seeds, neg_hints = _collect_seeds(rules)

    rows: list[dict] = []
    for domain in DOMAIN_CODES:
        pos_pairs = [(s, domain) for s in pos_seeds[domain]]
        rows += _build_bucket(pos_pairs, domain, "positive", seed, code_to_domain)

        neg_pairs = list(neg_hints[domain])
        if len(neg_pairs) < SAMPLES_PER_BUCKET:
            # 本域內埋藏的跨域 hint 不足 100 則，從其他域正樣本池補足（維持域間平衡取材）
            others = [c for c in DOMAIN_CODES if c != domain]
            pool = [(s, other) for other in others for s in pos_seeds[other]]
            fill_rng = random.Random(_sub_seed(seed, domain, "fill"))
            fill_rng.shuffle(pool)
            j = 0
            while len(neg_pairs) < SAMPLES_PER_BUCKET and pool:
                neg_pairs.append(pool[j % len(pool)])
                j += 1
        rows += _build_bucket(neg_pairs, domain, "negative", seed, code_to_domain)

    return rows, code_to_domain


# ── CLI ──────────────────────────────────────────────────────────────────


def _print_stats(rows: list[dict], code_to_domain: dict[str, tuple[str, str]]) -> None:
    """列印每域正/負樣本數與 variant_type 分佈（驗收用）。"""
    variant_by_domain: dict[str, Counter] = {d: Counter() for d in DOMAIN_CODES}
    role_by_domain: dict[str, Counter] = {d: Counter() for d in DOMAIN_CODES}
    for r in rows:
        d = r["bucket_domain"]
        variant_by_domain[d][r["variant_type"]] += 1
        role_by_domain[d][r["bucket_role"]] += 1

    print(f"共產生 {len(rows)} 筆樣本（{len(DOMAIN_CODES)} 域 × 正 {SAMPLES_PER_BUCKET} / 負 {SAMPLES_PER_BUCKET}）")
    for d in DOMAIN_CODES:
        machine, label = code_to_domain[d]
        pos_n = role_by_domain[d]["positive"]
        neg_n = role_by_domain[d]["negative"]
        variant_str = "、".join(f"{k}={v}" for k, v in sorted(variant_by_domain[d].items()))
        print(f"  {d} {label}({machine})：正 {pos_n} / 負 {neg_n}｜型態分佈 {variant_str}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Mock 測試集生成器（零 LLM，確定性生成）")
    ap.add_argument("--out", default=str(_DEFAULT_OUT), help="輸出 JSONL 路徑")
    ap.add_argument("--seed", type=int, default=42, help="確定性亂數種子（同 seed 保證輸出逐位元組相同）")
    args = ap.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows, code_to_domain = generate_testset(args.seed)
    with out_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    _print_stats(rows, code_to_domain)
    print(f"✅ 測試集已寫入 {out_path}")


if __name__ == "__main__":
    main()
