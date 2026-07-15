"""Mock 樣本生成 CLI（PRD §7 / §15）——按 plan 的 domain 逐格生成 CandidateCase。

Generator 只收「單格規格」（非 judge prompt），每格產 target_count 條；權威 expected_* 由
plan cell 決定（plan＝標籤 SSOT），LLM 只產文本 + 逐字證據 + 理由。去重＝NFKC+合併空白 exact hash。

用法見 PRD §15；支援 --dry-run（零 API）--limit --plan-id --workers --resume --model --all --confirm-cost。
"""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from domains import load_domain  # noqa: E402
from openai_gateway import Gateway  # noqa: E402
from gemini_gateway import GeminiGateway, provider_for_model  # noqa: E402
from prompt_parser import parse_gen_prompt_file  # noqa: E402
from schemas import (  # noqa: E402
    GENERATOR_OUTPUT_SCHEMA,
    CandidateCase,
    GeneratorOutput,
    Plan,
    PlanCell,
    normalize_for_dedup,
    verbatim_grounded,
)

_PROMPT_ROOT = (
    Path(__file__).resolve().parents[2] / "evals/prompt_lab/prompts/generators"
)
_GEN_PROMPTS = {
    "C-1": _PROMPT_ROOT / "c1_generator.md",
    "C-2": _PROMPT_ROOT / "c2_generator.md",
    "C-3": _PROMPT_ROOT / "c3_generator.md",
    "C-4": _PROMPT_ROOT / "c4_generator.md",
    "C-5": _PROMPT_ROOT / "c5_generator.md",
    "C-6": _PROMPT_ROOT / "c6_generator.md",
}

_BOUNDARY_SEMANTICS = {
    "C-1": "页面描述本身错误、缺漏或与实际规格不一致",
    "C-1-2": "页面行程时长、步骤、景点或交通流程没写清",
    "C-1-3": "页面未揭露必付费用或价格规则",
    "C-1-5": "页面未写清启用、兑换、凭证或证件规则",
    "C-1-6": "页面未揭露年龄、体能、天候或风险限制",
    "C-1-7": "页面未揭露退改政策或服务承诺",
    "C-2": "已经取得的交付物存在具体客观品质瑕疵",
    "C-2-1": "已经成功启用后才出现网速慢、断线或无讯号",
    "C-2-2": "餐点或用餐区存在具体品质／卫生问题",
    "C-2-3": "车辆设备、车况或舒适设备有具体瑕疵，驾驶行为正常",
    "C-2-5": "活动设施或器材老旧、故障或不洁但无现场管理失职",
    "C-3-1": "当地现场人员的态度、沟通或专业服务问题",
    "C-3-2": "司机接送迟到、找不到或危险驾驶",
    "C-3-3": "现场带团节奏明确偏离表定",
    "C-3-4": "供应商一般约定未履行或应给未给",
    "C-3-6": "供应商对已知风险告知、备案、处置或善后失当",
    "C-4-2": "凭证已经取得但扫码、核销、入场或资格验证被系统卡住",
    "C-4-3": "App／网页按钮、页面、订单状态或功能异常",
    "C-5": "售后客服、退款或订单处理失当",
    "C-5-2": "取消退款结果被拒、金额错误或到账延宕",
    "C-5-3": "售后客服回复慢、推诿、态度差或答非所问",
    "C-6": "商品与执行正常，只有旅客主观、自身或外力因素",
    "C-6-1": "旅客自己迟到、身体不适、改变计划或操作错误",
    "C-6-2": "无隐藏费用或具体瑕疵，只有主观觉得不值",
    "C-6-3": "执行依表且无瑕疵，只是内容或节奏不如个人想象",
    "C-6-4": "天候或自然条件造成影响，供应商已合理应变",
    "C-6-5": "罢工、第三方运输、场馆关闭等外部事件，供应商已合理应变",
    "C-6-6": "信息本来写清，旅客明确自承没读、会错意或选错",
    "no_issue": "完全正常且只有礼貌称赞，没有任何问题点",
}


def generator_prompt_path(domain: str, override: str = "") -> Path:
    """依 plan domain 自動選 Generator prompt；可用 CLI 显式覆盖。"""
    if override:
        return Path(override)
    try:
        return _GEN_PROMPTS[domain]
    except KeyError as e:
        raise ValueError(f"尚未配置 {domain} Generator prompt") from e


def build_spec(cell: PlanCell) -> str:
    """把 plan cell 轉為給 LLM 的可讀規格字串（不洩漏評測用語）。"""
    lines = [
        f"格 ID：{cell.cell_id}",
        f"層級：Layer {cell.layer}｜表達變體：{cell.expression_variant}｜難度：{cell.difficulty}",
        f"整體傾向：{cell.input_polarity}｜語言優先：zh-tw｜需產數量：{cell.target_count} 條",
    ]
    round_context = os.environ.get("PROMPT_LAB_ROUND_CONTEXT", "").strip()
    if round_context:
        lines.append(f"本轮多样性要求：{round_context}")
    cfg = load_domain(cell.domain_under_test) if cell.domain_under_test in {"C-3", "C-4", "C-5", "C-6"} else None
    responsibility = cfg.get("responsibility_contract", "") if cfg else ""
    if cell.case_family in {"contrast_pair", "domain_pair"}:
        if cfg:
            a_contract = responsibility
            b_contract = _BOUNDARY_SEMANTICS.get(cell.boundary_with or "", f"责任明确属于 {cell.boundary_with}")
        else:
            a_contract = "页面资讯有问题" if cell.domain_under_test == "C-1" else "交付物本身有可观察的客观品质瑕疵"
            b_contract = "页面已写清" if cell.domain_under_test == "C-1" else "交付物本身没有独立成立的客观品质瑕疵"
        lines += [
            "類型：跨域最小對照——請產出恰好 1 對共 2 條：",
            f"  A 側（pair_side=A）：{a_contract}（命中面向語義：{cell.coverage_note}）。",
            f"  B 側（pair_side=B）：{b_contract}，真實責任在他域 {cell.boundary_with}。",
            f"  唯一可改變的責任事實（contrast_key）：{cell.contrast_theme}",
            "  两侧必须使用同一商品、地点、人物、时间、句式、语气、问题数量与严重度，只替换一处决定责任的事实；不得给 B 侧增加第二个问题或改成别的边界域。",
            "  最小改写硬规则：先写一份共同底稿，再复制成两侧，只替换一个主语＋一个决定性动作／状态；禁止在任一侧新增‘又、还、并且、同时’串联的第二项失职。若某边界有多种表现，只能任选一种。",
            f"  B 侧只能体现：{b_contract}；必须排除任何独立成立的 {cell.domain_under_test} 问题。A 侧附逐字 evidence，B 侧 evidence 回空。",
        ]
    elif cell.case_family == "l2_pair":
        a, b = cell.target_l2_codes
        lines += [
            "类型：本域 L2 最小对照——请产出恰好 1 对共 2 条，两侧都属于受测责任域：",
            f"  A 侧（pair_side=A）：唯一应落到 {a}。",
            f"  B 侧（pair_side=B）：唯一应落到 {b}。",
            f"  决定性语义：{cell.coverage_note}",
            f"  唯一可改变的事实：{cell.contrast_theme}",
            "  两侧都必须附各自文本中的逐字 evidence；商品、人物、地点、语气与问题数量保持一致。",
        ]
    elif cell.expected_domain == "true":
        if cfg:
            must = f"文本必须自足地写出决定性责任事实：{responsibility}"
        else:
            must = "文本明确指称『页面/介绍/说明』的资讯写错、缺漏、模糊或夸大，且改页面即可避免" if cell.domain_under_test == "C-1" else "文本明确写出已经取得或使用的交付物存在可观察品质状态，且修复、清洁、维护或更换交付物即可解决；不能只写普通、不值或不喜欢"
        lines += [
            f"標籤：正例（命中面向語義：{cell.coverage_note}）。",
            f"必須：{must}；附至少 1 條逐字 evidence_quote。",
        ]
    elif cell.expected_domain == "false":
        forbidden = f"{cell.domain_under_test} 独立问题" if cfg else ("页面资讯问题" if cell.domain_under_test == "C-1" else "交付物客观品质瑕疵")
        lines += [
            f"標籤：負例，真實責任明確在他域 {cell.boundary_with}（情境語義：{cell.coverage_note}）。",
            f"必須：文本明確給出該他域責任事實，且不得暗含任何獨立成立的{forbidden}；evidence_quotes 回空。",
        ]
    else:  # uncertain
        ambiguity = f"{cell.domain_under_test} 责任还是近邻域责任" if cfg else ("页面写错、现场偏离还是旅客没看" if cell.domain_under_test == "C-1" else "交付物品质、系统流程、人员执行还是主观期待")
        lines += [
            f"標籤：不確定/證據不足（情境語義：{cell.coverage_note}）。",
            f"必須：文本讓人無法只憑文字判斷是{ambiguity}；evidence_quotes 回空。",
            "关键限制：正文不得写出足以独立证明受测域或任何其他域已经失职的明确事实；只写观察到的模糊结果/感受，并明确列出至少两个互斥且都合理的原因。",
            "不得用明确错误提示、明确未履约、明确页面缺漏、明确旅客自认失误或明确外力事件来假装 uncertain；一旦这些决定性事实出现，标签就已经可判。",
        ]
        if cell.domain_under_test == "C-4":
            lines.append("本域 uncertain：只能写‘最后没享受到/没弄明白’，并明确说明当时没有看到或记下错误提示、没有确认是否真正进入开通/绑定/核销步骤、也没有得到任何原因结论；不得写‘过程不顺/没能刷过/核销不了/绑定失败/启用失败/查无订单/验证失败/一直转圈/按钮灰掉’等可直接证明系统流程卡关的事实。")
        elif cell.domain_under_test == "C-5":
            lines.append("本域 uncertain：只能写旅客想确认、修改、取消或退款但没有弄清接下来怎么做；不得写已成功提交申请、客服已经承诺、订单持续处理中、退款超过期限、重复扣款或明确无人回复，因为这些会直接证明本域问题。必须同时保留‘政策可能不适用/旅客可能没完成步骤/系统或客服可能未处理’三种可能。")
        elif cell.domain_under_test == "C-6":
            lines.append("本域 uncertain：只有‘不值/失望/计划受影响’等感受，无法确认是个人期待/误读，还是页面、品质、履约、系统、客服问题；不得写任何一方的明确失职事实。")
        elif cell.domain_under_test == "C-3":
            lines.append("本域 uncertain：只能写现场体验不顺但没有确认人员做了什么、是否偏离约定或是否存在外部原因；不得写迟到分钟数、冷淡态度、危险行为、明确未提供或明确未应变等独立履约事实。")
        lines.append("推荐自然结构：先写模糊结果或感受，再明确承认当时没有查清关键事实，最后列出互斥原因；不要替任何一个原因补充可验证细节。")
    if cell.adversarial_techniques:
        lines += [
            f"對抗技術（{cell.target_count} 條各用一種，輪替覆蓋）：{', '.join(cell.adversarial_techniques)}",
            f"對抗樣本仍須保有一個真實成立的 {cell.domain_under_test} 問題；prompt_injection 僅作待判文本，不得寫成給系統的指令生效內容。",
        ]
    return "\n".join(lines)


def _mk_case(
    cell: PlanCell,
    co,
    *,
    case_id: str,
    expected_domain: str,
    plan_id: str,
    gen_model: str,
    req_id: str | None,
    contrast_pair_id: str | None,
    expected_l2_codes: list[str] | None = None,
) -> CandidateCase | None:
    """由 LLM 單條輸出 + cell 權威欄位組 CandidateCase；驗證失敗回 None。"""
    is_true = expected_domain == "true"
    l2 = list(expected_l2_codes if expected_l2_codes is not None else cell.target_l2_codes) if is_true else []
    # evidence：只保留逐字落地的；非 true 一律清空
    quotes = (
        [q for q in (co.evidence_quotes or []) if verbatim_grounded(q, co.text)]
        if is_true
        else []
    )
    try:
        return CandidateCase(
            case_id=case_id,
            domain_under_test=cell.domain_under_test,
            layer=cell.layer,
            text=co.text,
            input_polarity=cell.input_polarity,
            expected_domain=expected_domain,  # type: ignore[arg-type]
            expected_l2_codes=l2,
            forbidden_l2_codes=[],
            expected_evidence_quotes=quotes,
            case_family=cell.case_family,
            expression_variant=cell.expression_variant,
            difficulty=cell.difficulty,
            language=co.language or "zh-tw",
            boundary_with=cell.boundary_with,
            contrast_pair_id=contrast_pair_id,
            contrast_key=cell.contrast_theme if contrast_pair_id else None,
            label_reason=co.label_reason,
            generator_model=gen_model,
            generator_request_id=req_id or "",
            generation_plan_id=cell.cell_id,
            origin="ai_generated",
            status="candidate",
        )
    except Exception as e:  # noqa: BLE001  驗證失敗（如 true 側無證據仍可，但 L2 不合法等）→ 記錄跳過
        print(
            f"  ⚠️ {case_id} 建構失敗：{str(e).splitlines()[-1][:80]}", file=sys.stderr
        )
        return None


def process_cell(
    gw: Gateway, gen_prompt, cell: PlanCell, model: str
) -> tuple[list[CandidateCase], str | None]:
    """處理單格：呼叫 gateway → 解析 GeneratorOutput → 組 CandidateCase 清單。回 (cases, error)。"""
    spec = build_spec(cell)
    res = gw.structured(
        system=gen_prompt.system,
        user=gen_prompt.render_user(spec),
        json_schema=GENERATOR_OUTPUT_SCHEMA,
        schema_name=f"{cell.domain_under_test.lower().replace('-', '')}_generator_output",
        model=model,
        meta={
            "cell_id": cell.cell_id,
            "plan_id": cell.cell_id,
            "prompt_sha256": gen_prompt.sha256,
        },
    )
    if not res.ok:
        return [], res.error
    try:
        out = GeneratorOutput(**res.parsed)
    except Exception as e:  # noqa: BLE001
        return [], f"schema_invalid:{str(e).splitlines()[-1][:60]}"
    slots = common.slot_case_ids(cell.cell_id, cell.case_family, cell.target_count)
    cases: list[CandidateCase] = []
    if cell.case_family in {"contrast_pair", "domain_pair", "l2_pair"}:
        pair_id = f"pair-{cell.cell_id}"
        by_side = {co.pair_side: co for co in out.cases if co.pair_side in ("A", "B")}
        # 若 LLM 未標 side，退回依序 A,B
        a = by_side.get("A") or (out.cases[0] if out.cases else None)
        b = by_side.get("B") or (out.cases[1] if len(out.cases) > 1 else None)
        a_domain = "true"
        b_domain = "true" if cell.case_family == "l2_pair" else "false"
        a_l2 = [cell.target_l2_codes[0]]
        b_l2 = [cell.target_l2_codes[1]] if cell.case_family == "l2_pair" else []
        if a:
            c = _mk_case(
                cell,
                a,
                case_id=f"{cell.cell_id}-a",
                expected_domain=a_domain,
                plan_id=cell.cell_id,
                gen_model=res.model,
                req_id=res.request_id,
                contrast_pair_id=pair_id,
                expected_l2_codes=a_l2,
            )
            if c:
                cases.append(c)
        if b:
            c = _mk_case(
                cell,
                b,
                case_id=f"{cell.cell_id}-b",
                expected_domain=b_domain,
                plan_id=cell.cell_id,
                gen_model=res.model,
                req_id=res.request_id,
                contrast_pair_id=pair_id,
                expected_l2_codes=b_l2,
            )
            if c:
                cases.append(c)
    else:
        for slot, co in zip(slots, out.cases[: cell.target_count]):
            c = _mk_case(
                cell,
                co,
                case_id=slot,
                expected_domain=cell.expected_domain,
                plan_id=cell.cell_id,
                gen_model=res.model,
                req_id=res.request_id,
                contrast_pair_id=None,
            )
            if c:
                cases.append(c)
    return cases, None


def main(argv: list[str] | None = None, *, gateway: Gateway | None = None) -> int:
    """CLI 入口。gateway 可注入（fake client 測試用）；None 則依 env 建真 gateway。"""
    common.load_env()  # 先載入 evals/prompt_lab/.env（真實 env 優先），再讓下方 default 讀 env
    ap = argparse.ArgumentParser(description="按 plan domain 生成 Mock 樣本")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default=os.environ.get("PROMPT_LAB_GENERATOR_MODEL", ""))
    ap.add_argument(
        "--provider",
        choices=("auto", "openai", "gemini"),
        default=os.environ.get("PROMPT_LAB_GENERATOR_PROVIDER", "auto"),
        help="Generator 供应商；auto 会把 gemini-* 模型路由到 Gemini API",
    )
    ap.add_argument(
        "--generator-prompt",
        default="",
        help="覆盖 plan domain 的 Generator prompt；默认自动选择 c1/c2_generator.md",
    )
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument(
        "--limit", type=int, default=5, help="預設成本上限（格數）；超過需 --all"
    )
    ap.add_argument("--plan-id", default="", help="只跑指定 cell_id（子串比對）")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument(
        "--dry-run", action="store_true", help="零 API：只印待處理格數與規格範例"
    )
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--confirm-cost", action="store_true")
    args = ap.parse_args(argv)

    judge_model = os.environ.get("PROMPT_LAB_JUDGE_MODEL", "")
    if args.model and judge_model and args.model == judge_model:
        print(
            "⚠️  Generator 与 Judge 使用同一 model snapshot；可做管线 smoke，"
            "但会产生同模型闭环偏差。正式生成请用 --model 指定独立 Generator 模型。",
            file=sys.stderr,
        )

    plan = Plan(**_load_json(args.plan))
    prompt_path = generator_prompt_path(plan.domain_under_test, args.generator_prompt)
    gen_prompt = parse_gen_prompt_file(prompt_path)
    cells = [c for c in plan.cells if (not args.plan_id or args.plan_id in c.cell_id)]

    existing = (
        {c["case_id"]: c for c in common.read_jsonl(args.out)} if args.resume else {}
    )
    if existing:
        existing_domains = {
            CandidateCase(**record).domain_under_test for record in existing.values()
        }
        if existing_domains != {plan.domain_under_test}:
            raise ValueError(
                f"resume 输出 domain {sorted(existing_domains)} 与 plan {plan.domain_under_test} 不一致"
            )
    todo: list[PlanCell] = []
    for cell in cells:
        slots = common.slot_case_ids(cell.cell_id, cell.case_family, cell.target_count)
        if args.resume and all(s in existing for s in slots):
            continue
        todo.append(cell)

    print(
        f"計畫 {plan.plan_id}（{plan.domain_under_test}｜{prompt_path.name}）：{len(cells)} 格；待處理 {len(todo)} 格（resume 已跳過 {len(cells) - len(todo)}）"
    )
    if args.dry_run:
        print(f"🔎 dry-run：將發出 {len(todo)} 次生成呼叫（零 API）。範例規格：")
        if todo:
            print("---\n" + build_spec(todo[0]) + "\n---")
        return 0

    if not args.model:
        print("⛔ 需 --model 或環境變數 PROMPT_LAB_GENERATOR_MODEL", file=sys.stderr)
        return 2
    allowed = common.confirm_cost_or_exit(
        len(todo), all_flag=args.all, confirm_cost=args.confirm_cost, limit=args.limit
    )
    todo = todo[:allowed]

    provider = provider_for_model(args.model, args.provider)
    gw = gateway or (GeminiGateway() if provider == "gemini" else Gateway())
    if not gw.has_key:
        key_name = "GEMINI_API_KEY" if provider == "gemini" else "OPENAI_API_KEY"
        print(f"⛔ 無 {key_name}（且未注入 client），無法真打", file=sys.stderr)
        return 2
    print(f"模型路由：provider={provider}｜model={args.model}")

    merged = dict(existing)
    seen_text = {normalize_for_dedup(c["text"]) for c in existing.values()}
    dups = 0
    fails: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {
            ex.submit(process_cell, gw, gen_prompt, cell, args.model): cell
            for cell in todo
        }
        for fut in futs:
            cell = futs[fut]
            cases, err = fut.result()
            if err:
                fails.append(f"{cell.cell_id}:{err}")
            for c in cases:
                nt = normalize_for_dedup(c.text)
                if nt in seen_text:
                    dups += 1
                    continue
                seen_text.add(nt)
                merged[c.case_id] = c.model_dump()
            # 每格完成即原子 checkpoint；进程中断后 --resume 不会重烧已完成格。
            common.write_jsonl(args.out, [merged[k] for k in sorted(merged)])
            common.write_jsonl(
                Path(args.out).with_suffix(Path(args.out).suffix + ".failures.jsonl"),
                [{"error": item} for item in fails],
            )

    common.write_jsonl(args.out, [merged[k] for k in sorted(merged)])
    print(
        f"✅ 產出 {len(merged)} 條（新增去重後）；重複丟棄 {dups}；失敗格 {len(fails)}"
    )
    if fails:
        print("失敗格：" + "; ".join(fails[:10]), file=sys.stderr)
    return 0


def _load_json(path: str) -> dict:
    import json

    return json.loads(Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
