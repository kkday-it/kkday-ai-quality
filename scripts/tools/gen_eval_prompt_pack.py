"""評測交付 Prompt 包 V2 產生器 — 情緒傾向 ×1 + C-1~C-6 領域 ×6（共 7 個獨立 prompts）。

定位：把判決規則組裝成 7 支**獨立、自包含**的評測 prompt（1 支極性判官 + 每個歸因域各 1 支、
含該域完整 L1/L2 判準），供多模型評測（ByteDance / Gemini / Claude）或外部團隊直接使用。
與上一輪 tmp/multi_model/claude_rubric.json（六域合併一支大 prompt）的差異＝歸因側按域拆分，
單域判官只吃本域判準、不屬本域即棄權（回空 attributions）。

判準 SSOT＝DB judge_rule_versions active 版（經 app.core.judge_config.ai_judge / global_rule
讀取層，非 config/ai_judge seed 檔——seed 可能與線上編輯漂移）；故**必須在 backend 容器內執行**。
zero LLM 呼叫、零寫入，純 DB 讀取 + 字串組裝；production 判決管線（prejudge.py）零修改，
輸出 schema 為 prejudge._attr_schema_l2_multi 的**鏡射**（backend/tests/test_prompt_pack_schema.py
逐鍵比對護欄，漂移即測試紅）。

骨架層（模板驅動，2026-07-13 起）：7 支 prompt 的章節骨架（Anthropic system prompt 慣例：
巢狀 XML 分區/critical 粗體/NEVER 禁止式/If-then 邊界）各自獨立存於
config/ai_judge/prompt_templates/*.md（容器經 ./config 掛載即時生效）——調單支骨架不影響其他六支。
模板僅含骨架與指令文字（SSOT 鐵律：判準內容一律走 {{SLOT}} 生成期槽位注入）；
{TEXT}/{POLARITY} 單大括號＝執行期佔位符，與生成期雙大括號槽位刻意區分。

輸出佈局（--out 目錄）：
    manifest.json   生成時間 / git sha / 各 rule DB active 版本 / 各域字元數 / L2→L1 對照 / 政策快照
    bundle.json     機器可讀單檔（腳本消費）：polarity + domains 的 system/user_template/schema
    README.md       使用契約（跑法順序 + 合併規則 + 可比性 + 侷限）
    prompts/*.md    人工可讀版（可直接複製貼給第三方模型），與 bundle 同源渲染防漂移

用法（scripts/ 未掛載進容器，先 docker cp——比照 taxonomy_health.py 慣例）：
    docker cp scripts/tools/gen_eval_prompt_pack.py kkday-ai-quality-backend:/app/scripts/tools/
    docker exec kkday-ai-quality-backend python /app/scripts/tools/gen_eval_prompt_pack.py \
        --out /app/tmp/multi_model/prompt_pack_v2 --git-sha "$(git rev-parse --short HEAD)"
    docker cp kkday-ai-quality-backend:/app/tmp/multi_model/prompt_pack_v2 tmp/multi_model/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from app.core import db
from app.core.db._shared import read_judgment_config
from app.core.judge_config import ai_judge, global_rule
from app.core.paths import AI_JUDGE_DIR

PACK_VERSION = "v3"

TEMPLATES_DIR = AI_JUDGE_DIR / "prompt_templates"
_SLOT_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")
_SECTION_RE = re.compile(r"^===SYSTEM===\n(.*)\n===USER_TEMPLATE===\n(.*)$", re.S | re.M)


def _load_template(prompt_id: str) -> tuple[str, str, str]:
    """讀骨架模板檔 → (system 模板, user 模板, 內容 sha256 前 12 碼)。

    檔名＝prompt id（00_polarity / 0N_C-N_domain）；===SYSTEM===/===USER_TEMPLATE=== 分節，
    分節標記之前的 HTML 註解（slots 清單/SSOT 警語）不進 prompt。
    """
    path = TEMPLATES_DIR / f"{prompt_id}.md"
    text = path.read_text(encoding="utf-8")
    m = _SECTION_RE.search(text)
    if not m:
        raise ValueError(f"{path.name} 缺 ===SYSTEM===/===USER_TEMPLATE=== 分節標記")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return m.group(1).strip(), m.group(2).strip(), digest


_L3_CODE_RE = re.compile(r"(C-[1-9]-\d+)-\d+(?!\d)")


def _strip_l3_codes(text: str) -> str:
    """DB 判準 prose 中的 L3 代碼引用（C-X-Y-Z）截斷為 L2（C-X-Y）——包政策：prompt 只到 L2。

    L3 引用在 DB 規則中為未來深判保留，不動 SSOT；僅在注入評測 prompt 時淨化，
    避免第三方模型看到 schema enum 之外的代碼深度。
    """
    return _L3_CODE_RE.sub(r"\1", text)


def _render_template(name: str, template: str, slots: dict[str, str]) -> str:
    """{{SLOT}} 逐槽替換；渲染後殘留未填槽位即拋錯（fail-fast，防產出爛 prompt 外流）。"""
    slots = {k: _strip_l3_codes(str(v)) for k, v in slots.items()}
    out = _SLOT_RE.sub(lambda m: slots.get(m.group(1), m.group(0)), template)
    leftover = _SLOT_RE.findall(out)
    if leftover:
        raise ValueError(f"{name} 缺填佔位符：{sorted(set(leftover))}")
    return out


def _adapt_guidance_for_pack(text: str) -> str:
    """attribution_guidance（DB，為 production 單呼叫 prompt 所寫）→ 六域拆分包適配。

    僅做語境對齊的精準替換（章節名/棄權語義/去重複身分與輸出句），DB SSOT 不動；
    任一替換未命中＝DB 文字已改，保留原文並印警告（不擋生成）。
    """
    pairs = [
        # 語境對齊
        ("你是 KKday 旅遊商品客訴歸因判官。", ""),
        ("依下方『六域界線鐵則』與『問題分類目錄』", "依下方本域界線與面向目錄"),
        ("皆列於『六域界線鐵則』", "皆列於本域界線"),
        ("無法明確歸類時回空字串（寧缺勿濫）。輸出 JSON。", "無法明確歸類時棄權回空（寧缺勿濫）。"),
        ("——那是當前版本規則（QC 於規則配置頁編輯即時生效），據以判斷、勿自行臆造", "，據以判斷、勿自行臆造"),
        # 結構化重排：文字牆 → 三小節（歸因原則／責任軸流程／選碼紀律），語句零增刪
        ("並給信心。評論可能整體負向、也可能是", "並給信心。\n- 評論可能整體負向、也可能是"),
        ("混合傾向——混合評論只歸因其提到的具體問題點，被稱讚的面向不歸因；全文找不到具體問題點時回空（寧缺勿濫）。",
         "混合傾向——混合評論只歸因其提到的具體問題點，被稱讚的面向不歸因。\n- 全文找不到具體問題點時回空（寧缺勿濫）。"),
        ("判斷流程（責任軸）：①先定「問題該誰修」的責任站點——",
         "\n責任軸判斷流程：\n1. 先定「問題該誰修」的責任站點：\n　- "),
        ("頁面資訊寫錯/缺漏（→商品內容）、交付物本身差（→商品品質）、現場的人與執行差（→供應商履約）、下單→開通→兌換→使用的系統流程卡關（→平台與系統）、售後客服的人與政策差（→客服營運）、商品與執行皆正常而問題在旅客主觀/自身/外力（→理解期待）。",
         "頁面資訊寫錯/缺漏 → 商品內容\n　- 交付物本身差 → 商品品質\n　- 現場的人與執行差 → 供應商履約\n　- 下單→開通→兌換→使用的系統流程卡關 → 平台與系統\n　- 售後客服的人與政策差 → 客服營運\n　- 商品與執行皆正常，問題在旅客主觀/自身/外力 → 理解期待"),
        ("**非只看主題字眼**：評論提到時長/費用/停留/集合，要看是「頁面描述寫得不對」還是「實際發生的事」。",
         "\n2. **非只看主題字眼**：\n　- 評論提到時長/費用/停留/集合 → 分辨是「頁面描述寫得不對」還是「實際發生的事」。"),
        ("聽到『以為有／要另外付／沒說要』先問「頁面寫了嗎」——頁面未寫清 → 商品內容；頁面已寫清、客人主觀不滿 → 理解期待。",
         "\n　- 聽到『以為有／要另外付／沒說要』先問「頁面寫了嗎」：頁面未寫清 → 商品內容；頁面已寫清、客人主觀不滿 → 理解期待。"),
        ("行程節奏類：現場執行偏離表定（等人/臨時壓縮/提早收團）→ 供應商履約；依表執行但主觀嫌趕、嫌停留短、嫌拉車遠 → 理解期待。②再依目錄各面向定義選最貼切 code。",
         "\n　- 行程節奏類：現場執行偏離表定（等人/臨時壓縮/提早收團）→ 供應商履約；依表執行但主觀嫌趕、嫌停留短、嫌拉車遠 → 理解期待。\n3. 再依目錄各面向定義選最貼切 code。"),
        ("各域界線、正例、反例皆列於本域界線",
         "\n選碼紀律：\n- 各域界線、正例、反例皆列於本域界線"),
        ("勿自行臆造。多個問題時取最核心、最直接的抱怨點，勿被次要問題誤導。",
         "勿自行臆造。\n- 多個問題時取最核心、最直接的抱怨點，勿被次要問題誤導。"),
        ("勿被次要問題誤導。只能從目錄內選 code", "勿被次要問題誤導。\n- 只能從目錄內選 code"),
    ]
    for old, new in pairs:
        if old in text:
            text = text.replace(old, new)
        else:
            print(f"⚠️ guidance 適配未命中（DB 文字已變？）：{old[:30]}")
    text = "歸因原則：\n- " + text.strip()
    return text


def _domains() -> list[dict]:
    """六域清單 [{code: C-N, domain: 機器值, label}]（依 C-N 數字序；排除 intake_excluded 域）。"""
    out = []
    for d in ai_judge.selectable_domains():
        j = ai_judge.l1_judgment(d["code"])
        out.append({"code": j.get("code", ""), "domain": d["code"], "label": d["label"]})
    return sorted(out, key=lambda d: int(d["code"].split("-", 1)[1]))


def _gist(canon: str, cap: int = 60) -> str:
    """L1 canon → 一行 gist（首句，供六域總覽地圖定位用，非完整判準）。

    截斷保護：截後若有未閉合的全形括號則退到括號前；避免地圖行出現「（縮水/遲到；」殘尾。
    """
    first = (canon or "").strip().split("。", 1)[0][:cap]
    while first.count("（") > first.count("）"):
        first = first[: first.rfind("（")].rstrip("、；，")
    return first


def _domain_gist_map(domains: list[dict]) -> str:
    """六域「code+label+一行 gist」總覽地圖——讓單域判官知道自己在整體體系哪裡、其他問題歸誰。"""
    lines = []
    for d in domains:
        canon = ai_judge.l1_judgment(d["domain"]).get("canon", "")
        gist = _gist(canon)
        if gist.startswith(f"{d['label']}："):  # canon 自帶域名開頭 → 去重（如 C-4「平台與系統：」）
            gist = gist[len(d["label"]) + 1 :]
        lines.append(f"- {d['code']} {d['label']}：{gist}")
    return "\n".join(lines)


def _case_lines(node: dict, indent: str = "　") -> list[str]:
    """判準節點的 ✅屬/❌誤判/⛔不得 三段渲染（格式對齊 production _domain_boundaries 的域塊）。"""
    lines: list[str] = []
    pos = [p for p in (node.get("positive_cases") or []) if p]
    neg = [n for n in (node.get("negative_cases") or []) if n]
    forbid = [f for f in (node.get("forbid") or []) if f]
    # 逐條 bullet（原「；」串接成長行，可讀性與注意力皆差）
    if pos:
        lines.append(f"{indent}✅屬本項：")
        lines += [f"{indent}・{c}" for c in pos]
    if neg:
        lines.append(f"{indent}❌禁歸本項（常見誤判）：")
        lines += [f"{indent}・{c}" for c in neg]
    if forbid:
        lines.append(f"{indent}⛔不得：")
        lines += [f"{indent}・{c}" for c in forbid]
    return lines


def _l1_boundary_block(dom: dict) -> str:
    """單域 L1 界線塊：canon + ✅/❌/⛔ 完整（棄權訊號的主要來源——❌/⛔ 即「看似本域實為他域」）。"""
    j = ai_judge.l1_judgment(dom["domain"])
    lines = [f"【{dom['label']}（{dom['code']}）】{(j.get('canon') or '').strip()}"]
    lines += [
        ln.replace("屬本項", "屬本域").replace("禁歸本項", "禁歸本域") for ln in _case_lines(j)
    ]
    return "\n".join(lines)


def _l2_groups(domain: str) -> list[tuple[str, str, list[dict]]]:
    """該域葉節點按 l2_code 分組（保序）→ [(l2_code, l2_label, leaves)]。L2 葉自成一組（leaf 即 L2）。"""
    groups: list[tuple[str, str, list[dict]]] = []
    idx: dict[str, int] = {}
    for n in ai_judge.l3_nodes_for_domains([domain]):
        l2_code = n.get("l2_code") or n.get("code", "")
        if l2_code not in idx:
            idx[l2_code] = len(groups)
            groups.append((l2_code, n.get("l2_label", ""), []))
        groups[idx[l2_code]][2].append(n)
    return groups


def _lift_l3_examples(leaves: list[dict], max_n: int) -> list[str]:
    """L2 分支無自身正反例 → 從子 L3 葉各取 1 正 1 反上提（標來源細項），總數封頂 max_n。

    上提而非全量：C-1 全部 132/118 條例句都在 L3 葉，不上提則其 8 個 L2 只剩 canon 零例句、
    模型無從校準邊界；全量塞入則 prompt 爆量稀釋注意力。每葉 1 正 1 反 + 封頂為折衷。
    """
    lines: list[str] = []
    for leaf in leaves:
        tag = leaf.get("l3_label") or leaf.get("code", "")
        pos = [p for p in (leaf.get("positive_cases") or []) if p]
        neg = [n for n in (leaf.get("negative_cases") or []) if n]
        if pos and len(lines) < max_n:
            lines.append(f"　✅例（{tag}）：{pos[0]}")
        if neg and len(lines) < max_n:
            lines.append(f"　❌誤判例（{tag}）：{neg[0]}")
        if len(lines) >= max_n:
            break
    return lines


def _l2_block(l2_code: str, l2_label: str, leaves: list[dict], max_lift: int) -> str:
    """單一 L2 面向區塊：canon +（自身或上提的）例句界線。

    L2 葉（面向即葉，如 C-4/C-6 全域）：判準五欄全在葉上，直接渲染。
    L2 分支（其下有 L3，如 C-1）：canon 取分支判準（l2_judgment）；分支無自身正反例時
    從子 L3 葉上提例句（_lift_l3_examples）。
    """
    is_leaf = len(leaves) == 1 and leaves[0].get("code") == l2_code
    node = leaves[0] if is_leaf else ai_judge.l2_judgment(l2_code)
    canon = (node.get("canon") or "").strip()
    lines = [f"■ {l2_code} {l2_label}：{canon}"]
    case_lines = _case_lines(node)
    if not is_leaf and not case_lines:  # 分支未填自身例句 → 上提 L3 葉例句
        case_lines = _lift_l3_examples(leaves, max_lift)
    lines += case_lines
    return "\n".join(lines)


def _output_schema(l2_codes: list[str], max_n: int) -> dict:
    """單域多歸因輸出 schema（鏡射 prejudge._attr_schema_l2_multi；enum 限本域 L2 codes）。"""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["attributions"],
        "properties": {
            "attributions": {
                "type": "array",
                "maxItems": max_n,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["l2_code", "confidence", "summary", "evidence_quote"],
                    "properties": {
                        "l2_code": {"type": "string", "enum": sorted(l2_codes)},
                        "confidence": {"type": "number"},
                        "summary": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["lang", "text"],
                                "properties": {
                                    "lang": {"type": "string"},
                                    "text": {"type": "string"},
                                },
                            },
                        },
                        "evidence_quote": {"type": "string"},
                    },
                },
            }
        },
    }


def _polarity_prompt() -> dict:
    """情緒傾向 prompt：骨架＝00_polarity.md 模板；判準＝polarity_guidance（DB active）槽位注入。

    sentiment 區間規則在 production 由 code 端 _clamp_sentiment 夾值（負 1-2／中恆 3／正 4-5）；
    第三方模型跑此包時沒有 code-side 夾值，故由模板骨架明文外顯化，語義不新增。
    """
    sys_tpl, user_tpl, tpl_hash = _load_template("00_polarity")
    system = _render_template(
        "00_polarity", sys_tpl, {"POLARITY_GUIDANCE": global_rule.polarity_guidance()}
    )
    return {
        "id": "00_polarity",
        "title": "情緒傾向判官（Step 1）",
        "template_hash": tpl_hash,
        "system": system,
        "user_template": user_tpl,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["polarity", "sentiment"],
            "properties": {
                "polarity": {
                    "type": "string",
                    "enum": ["positive", "neutral", "negative"],
                },
                "sentiment": {"type": "integer", "minimum": 1, "maximum": 5},
            },
        },
    }


def _domain_prompt(dom: dict, domains: list[dict], max_lift: int, max_n: int) -> dict:
    """單域歸因 prompt：骨架＝0N_C-N_domain.md 模板；判準內容全數槽位注入（DB active）。

    棄權示範不另手寫例句——L1 界線塊的 ❌禁歸本域/⛔不得 即 DB 內策展的「看似本域實為他域」
    案例（SSOT），模板 <abstain_rules> 明文指回該清單，避免骨架內硬編判準內容形成第二真相源。
    """
    groups = _l2_groups(dom["domain"])
    l2_codes = [g[0] for g in groups]
    n = int(dom["code"].split("-", 1)[1])
    # 檔名別名（僅影響 prompt id/檔名，不動 DB domain 機器值）：
    # product_quality→quality（縮短）；redemption→platform（C-4 已升維「平台與系統」，舊機器值不再達意）
    _id_alias = {"product_quality": "quality", "redemption": "platform"}
    prompt_id = f"0{n}_{dom['code']}_{_id_alias.get(dom['domain'], dom['domain'])}"
    sys_tpl, user_tpl, tpl_hash = _load_template(prompt_id)
    system = _render_template(
        prompt_id,
        sys_tpl,
        {
            "DOMAIN_LABEL": dom["label"],
            "DOMAIN_CODE": dom["code"],
            "ATTRIBUTION_GUIDANCE": _adapt_guidance_for_pack(global_rule.attribution_guidance()),
            "DOMAIN_MAP": _domain_gist_map(domains),
            "L1_BOUNDARY": _l1_boundary_block(dom),
            "L2_CATALOG": "\n\n".join(
                _l2_block(c, lbl, leaves, max_lift) for c, lbl, leaves in groups
            ),
            "MAX_N": str(max_n),
        },
    )
    return {
        "id": prompt_id,
        "title": f"{dom['label']}（{dom['code']}）單域歸因判官（Step 2）",
        "code": dom["code"],
        "domain": dom["domain"],
        "label": dom["label"],
        "l2_codes": l2_codes,
        "template_hash": tpl_hash,
        "system": system,
        "user_template": user_tpl,
        "schema": _output_schema(l2_codes, max_n),
    }


def _render_md(p: dict, manifest: dict) -> str:
    """prompt dict → 乾淨 markdown（與 bundle.json 同源）。溯源資訊只留 manifest.json，不進 prompt 檔。"""
    del manifest  # 溯源移至 manifest.json（使用者要求 prompt 檔零註釋）
    return (
        f"# {p['title']}\n\n"
        f"## System\n\n```\n{p['system']}\n```\n\n"
        f"## User\n\n```\n{p['user_template']}\n```\n\n"
        f"## Schema\n\n```json\n{json.dumps(p['schema'], ensure_ascii=False, indent=2)}\n```\n"
    )


def _policy_snapshot(max_n: int) -> dict:
    """生成時的判決政策快照（README 合併規則與 manifest 溯源共用）。"""
    ev = global_rule.evidence_policy()
    return {
        "attribute_when": global_rule.polarity_gate().get("attribute_when", []),
        "attr_min_confidence": ev.get("attr_min_confidence"),
        "secondary_min_confidence": ev.get("secondary_min_confidence"),
        "require_quote_grounded": ev.get("require_quote_grounded", True),
        "max_attributions": max_n,
        "prejudge_depth": global_rule.prejudge_depth(),
    }


def _manifest(domains: list[dict], prompts: list[dict], git_sha: str, max_n: int) -> dict:
    """溯源 manifest：DB active 版本 / 各 prompt 字元數 / L2→L1 對照 / 政策快照。"""
    watch = {d["code"] for d in domains} | {"global_rule"}
    versions = {
        m["rule_code"]: {
            "version": m["version"],
            "author": m.get("author"),
            "created_at": str(m.get("created_at")),
        }
        for m in db.list_rule_meta()
        if m["rule_code"] in watch
    }
    l2_to_l1 = {}
    for d in domains:
        for c, lbl, _leaves in _l2_groups(d["domain"]):
            l2_to_l1[c] = {"l1_code": d["code"], "l1_label": d["label"], "l2_label": lbl}
    return {
        "pack_version": PACK_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha,
        "rule_versions": versions,
        "template_hashes": {p["id"]: p.get("template_hash", "") for p in prompts},
        "prompt_chars": {p["id"]: len(p["system"]) for p in prompts},
        "l2_to_l1": l2_to_l1,
        "policy": _policy_snapshot(max_n),
    }


def _render_readme(manifest: dict) -> str:
    """使用契約 README：溯源 / 跑法順序 / 合併規則（鏡射 production 語義）/ 可比性 / 侷限。"""
    pol = manifest["policy"]
    ver_lines = "\n".join(
        f"- {code}：v{v['version']}（{v['created_at'][:10]}）"
        for code, v in sorted(manifest["rule_versions"].items())
    )
    chars = "\n".join(f"- {pid}：{n:,} 字" for pid, n in sorted(manifest["prompt_chars"].items()))
    return f"""# 評測交付 Prompt 包 {PACK_VERSION}（情緒 ×1 + C-1~C-6 域 ×6）

生成：{manifest["generated_at"][:19]}Z · git {manifest["git_sha"] or "N/A"}
判準來源：**DB judge_rule_versions active 版**（非 config/ai_judge seed 檔）；規則異動後本包過期，需重新生成比對 manifest.json 版本號。

{ver_lines}

## 跑法順序（每則評論）

1. **Step 1 極性**：跑 `prompts/00_polarity.md`（user 模板 `{{TEXT}}`＝評論原文）→ 得 `polarity` + `sentiment`。
2. **閘門**：`polarity == "positive"` → 該評論判 non_issue，**不跑任何域 prompt**（對齊 production `attribute_when={pol["attribute_when"]}`）。
3. **Step 2 歸因**：`negative`/`neutral` → **六支域 prompt 各獨立跑一次**（同一份評論餵六次；user 模板帶 `{{POLARITY}}` + `{{TEXT}}`）。單域判官判「評論是否含本域問題」，不屬本域回空 `attributions`。

## 合併規則（六域結果 → 最終判決；鏡射 production 語義）

1. **證據落地**：`evidence_quote` 須為評論原文逐字子字串（去空白比對）；不落地者視為低信心（production 壓入人審帶）。
2. **信心閘門**：`confidence < {pol["attr_min_confidence"]}` 整條丟棄（殭屍歸因）。
3. **同域去重**：同一域多條時保留信心最高一條（單域 prompt maxItems={pol["max_attributions"]}，正常不觸發）。
4. **跨域排序**：全部存活條目按 confidence 降冪；第 1 條＝primary（`is_primary=true`）不受次要閘門限制；第 2 條起 `confidence < {pol["secondary_min_confidence"]}` 丟棄。
5. **上限**：合併後取前 {pol["max_attributions"]} 條（`max_attributions`）。

## 與 production 判決可比性

- 輸出對齊 `judgment_history` 快照形狀：`polarity` / `sentiment_score`（=sentiment）/ `l1`（由 `l2_code` 反查 `manifest.json` 的 `l2_to_l1`）/ `l2` / `l3` **恆空**（本包判到 L2 深度，對齊 production `prejudge_depth="{pol["prejudge_depth"]}"`）/ `confidence` / `evidence_quote` / `summary` / `is_primary`。
- 比對真值建議用 `judgments` 表現行判決或 free_tag 外部標籤，勿自我參照。

## 各 prompt 字元數

{chars}

## 與 production 管線的差異（編排層機制，不在 prompt 內）

- **低信心負反饋重問**（reroute_on_low_conf：首輪低信心面向排除後重問一次）——六域獨立跑已是全域掃描，本包不重放此環。
- **evidence grounding 壓信心**：production 由 code 端驗逐字落地並壓信心；本包由合併規則第 1 步等義承接。
- **confidence-gated ensemble**（跨廠投票）與 **G1 自動確認路由**：判決後編排，非 prompt 職責。

## 侷限

- 靜態文字快照：DB 規則異動不會自動反映，重跑產生器即可更新。
- 僅供離線評測比較，不回寫 production 任何資料。
- 六域獨立跑天然傾向多報（合併前最多 6×{pol["max_attributions"]} 條候選）——合併規則的雙信心閘門為必要步驟，勿省略。
"""


def write_pack(
    out_dir: Path,
    domains_filter: list[str],
    max_lift: int,
    formats: set[str],
    with_polarity: bool,
    git_sha: str,
) -> dict:
    """組裝並落盤整個 prompt 包；回傳 manifest（供 CLI 摘要輸出）。

    Args:
        out_dir: 輸出目錄（不存在自動建立）。
        domains_filter: 只產指定 C-N（空＝全六域）。
        max_lift: 每 L2 上提例句上限。
        formats: {"md", "json"} 子集。
        with_polarity: 是否含情緒 prompt。
        git_sha: 溯源用 git sha（容器內無 .git，由 host 端傳入）。
    """
    max_n = int(read_judgment_config().get("prejudge", {}).get("max_attributions", 2))
    domains = _domains()
    picked = [d for d in domains if not domains_filter or d["code"] in domains_filter]
    prompts: list[dict] = [_polarity_prompt()] if with_polarity else []
    prompts += [_domain_prompt(d, domains, max_lift, max_n) for d in picked]

    out_dir.mkdir(parents=True, exist_ok=True)
    old_manifest_path = out_dir / "manifest.json"
    if old_manifest_path.exists():  # 過期偵測：舊包規則版本 vs 當前 DB active，落差顯式輸出
        try:
            old_vers = json.loads(old_manifest_path.read_text(encoding="utf-8")).get(
                "rule_versions", {}
            )
            live = {m["rule_code"]: m["version"] for m in db.list_rule_meta()}
            drift = {
                c: (v.get("version"), live.get(c))
                for c, v in old_vers.items()
                if live.get(c) != v.get("version")
            }
            if drift:
                print(f"⚠️ 既有包已過期（規則版本落差，本次生成已更新）：{drift}")
        except (json.JSONDecodeError, OSError):
            pass  # 舊 manifest 壞損不阻斷重生成
    manifest = _manifest(picked, prompts, git_sha, max_n)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "README.md").write_text(_render_readme(manifest), encoding="utf-8")
    if "json" in formats:
        bundle = {
            "version": PACK_VERSION,
            "generated_at": manifest["generated_at"],
            "polarity": next((p for p in prompts if p["id"] == "00_polarity"), None),
            "domains": {p["code"]: p for p in prompts if "code" in p},
            "merge_policy": manifest["policy"],
        }
        (out_dir / "bundle.json").write_text(
            json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if "md" in formats:
        pdir = out_dir / "prompts"
        pdir.mkdir(exist_ok=True)
        for p in prompts:
            (pdir / f"{p['id']}.md").write_text(_render_md(p, manifest), encoding="utf-8")
    return manifest


def main() -> None:
    """CLI 入口：解析參數 → 產包 → 印各 prompt 字元數摘要。"""
    ap = argparse.ArgumentParser(description="評測交付 Prompt 包 V2 產生器（1 情緒 + 6 域）")
    ap.add_argument("--out", required=True, help="輸出目錄")
    ap.add_argument("--domains", default="", help="只產指定域（逗號分隔 C-N；預設全六域）")
    ap.add_argument("--max-lift-per-l2", type=int, default=8, help="每 L2 上提 L3 例句上限")
    ap.add_argument("--format", default="md,json", help="輸出格式：md,json 子集")
    ap.add_argument("--no-polarity", action="store_true", help="不產情緒 prompt")
    ap.add_argument("--git-sha", default="", help="溯源 git sha（host 端 git rev-parse 傳入）")
    args = ap.parse_args()

    manifest = write_pack(
        out_dir=Path(args.out),
        domains_filter=[c.strip() for c in args.domains.split(",") if c.strip()],
        max_lift=args.max_lift_per_l2,
        formats={f.strip() for f in args.format.split(",") if f.strip()},
        with_polarity=not args.no_polarity,
        git_sha=args.git_sha,
    )
    print(f"prompt_pack {PACK_VERSION} → {args.out}")
    for pid, n in sorted(manifest["prompt_chars"].items()):
        print(f"  {pid}: {n:,} chars")
    for code, v in sorted(manifest["rule_versions"].items()):
        print(f"  {code}: v{v['version']}")


if __name__ == "__main__":
    main()
