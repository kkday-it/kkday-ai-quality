"""判決引擎 DSPy 鷹架（Phase 3 旗艦）——把 polarity/L1-L3 分類改用 DSPy Signature 承載、可用 true_label
編譯自優化（BootstrapFewShot→MIPROv2）；**保留 evidence-cap/abstain 業務語義（手刻，reuse prejudge）**。

⚠️ 現況（誠實）：這是**可建但未能完整驗證的鷹架**——
- **編譯需標註**：`compile_and_persist` 以 judgments.true_label 為 trainset；label 不足時優雅 skip（同 calibration）。
- **執行需 LLM key**：`DspyJudge` 呼叫 dspy.LM；無 key → `configure_lm` 回 False，judge_findings 交呼叫端決定回退。
- **完全並行·不接主路徑**：不改 prejudge.to_findings；有 label+key 且驗證後才由呼叫端切換（strangler）。
mock 可測部分：Signature 結構、metric exact-match、DspyJudge 組合邏輯（注入假 predict）、compile 優雅 skip。

依賴：dspy（`.[dspy]` extra，lazy——本模組 import 即需 dspy，故僅在 DSPy 路徑載入）。判準/業務語義一律
reuse `prejudge`（禁在此重寫），DSPy 僅替換「LLM 分類呼叫」這一層。
"""

from __future__ import annotations

import logging
from typing import Any

import dspy

from app.core import ai_judge
from app.core.paths import REPO_ROOT
from app.core.schema import TicketFinding
from app.judge import prejudge

_log = logging.getLogger(__name__)

# 編譯最少標註樣本（少於此 trainset 過薄、優化無意義 → skip）。
_MIN_TRAIN = 50
# 持久化編譯後程式（派生產物；data/ 已 gitignore）。
_COMPILED_FILE = REPO_ROOT / "data" / "dspy" / "attribution_compiled.json"


# ── Signatures（承載判決任務；判準指引留給 catalog / 編譯優化，非硬寫 prompt）──────────────
class PolarityJudge(dspy.Signature):
    """判斷 KKday 旅遊商品進線評論的整體情緒傾向；只判傾向、不做任何歸因。"""

    review: str = dspy.InputField(desc="評論 / 進線原文")
    polarity: str = dspy.OutputField(desc="整體傾向：positive / negative / neutral 之一")


class AttributionJudge(dspy.Signature):
    """把負向評論嚴格且精準歸到 L3 判準目錄中最貼切的一條 code，並給信心與原文逐字佐證；無法明確歸類回空。"""

    review: str = dspy.InputField(desc="負向評論原文")
    catalog: str = dspy.InputField(desc="L3 判準目錄（code + 定義）")
    l3_code: str = dspy.OutputField(desc="最貼切的 L3 code；無法明確歸類則回空字串（寧缺勿濫）")
    confidence: float = dspy.OutputField(desc="信心 0-1")
    evidence_quote: str = dspy.OutputField(desc="支持該歸因的原文逐字片段")


class DspyJudge(dspy.Module):
    """一條進線 → TicketFinding 清單：DSPy 做極性閘門 + 歸因分類，業務語義 reuse prejudge（手刻）。

    DSPy 僅負責「LLM 分類」；evidence-cap / abstain / L3 白名單淨化 / 分層 / 階段派生一律走
    `prejudge._finalize_attr` + `_attributed_finding` + `_non_issue_finding`（不重寫）。
    """

    def __init__(self) -> None:
        super().__init__()
        self.polarity = dspy.Predict(PolarityJudge)
        self.attribute = dspy.Predict(AttributionJudge)

    def forward(self, item: dict[str, Any]) -> list[TicketFinding]:
        text = prejudge._text_of(item)
        pol = str(self.polarity(review=text).polarity or "").strip()
        if pol not in ("positive", "negative", "neutral"):
            pol = "neutral"  # 非法輸出兜底中立（傾向只有三態）
        if pol != "negative":
            return [prejudge._non_issue_finding(item, pol, "dspy")]

        domains = ai_judge.selectable_domains()
        candidate_codes = frozenset(
            n["code"] for n in ai_judge.l3_nodes_for_domains([d["code"] for d in domains])
        )
        out = self.attribute(review=text, catalog=prejudge._l3_catalog(domains))
        attr = prejudge._finalize_attr(
            item,
            text,
            {
                "l3_code": out.l3_code,
                "confidence": out.confidence,
                "evidence_quote": out.evidence_quote,
            },
            candidate_codes,
        )
        if not attr[
            "l1_domain_code"
        ]:  # 全 abstain（無域）→ 負向未歸因 pending_data（同 to_findings）
            f = prejudge._non_issue_finding(item, "negative", "dspy")
            f.judgment_stage = "pending_data"
            f.confidence_tier = "needs_review"
            f.needs_review = True
            f.evidence_quote = text[:200]
            return [f]
        return [prejudge._attributed_finding(item, attr, "dspy", enhanced=False)]


def attribution_metric(example: dspy.Example, pred: Any, trace: Any = None) -> bool:
    """編譯 metric：模型判出的 L1 域是否命中人工真值 true_label（exact-match）。

    pred 為 DspyJudge 輸出（TicketFinding 清單）；取首個非空 L1 域 code（l1_domain_code）與 example.l1_code 比對。
    """
    gold = getattr(example, "l1_code", "") or ""
    findings = pred if isinstance(pred, list) else [pred]
    got = ""
    for f in findings:
        code = getattr(f, "l1_domain_code", "")
        if code:
            got = code
            break
    return bool(gold) and got == gold


def load_trainset() -> list[dspy.Example] | None:
    """撈 judgments 標註列 → dspy.Example(review, l1_code=true_label)；DB 不可達 / 無標註回 None。

    只取有 true_label 的列作 trainset；review 文字由來源表還原（現以 summary/evidence 佔位，真流程接原文）。
    """
    try:
        from sqlalchemy import select

        from app.core.db import tables as T

        jg = T.judgments
        stmt = select(jg.c.summary, jg.c.evidence, jg.c.true_label).where(
            jg.c.true_label.isnot(None), jg.c.true_label != ""
        )
        with T.get_engine().connect() as c:
            rows = c.execute(stmt).all()
    except Exception:  # noqa: BLE001  DB 未就緒 / 表缺 → 交呼叫端 skip
        return None

    # summary 現為語系 map（JSONB）→ 取 zh-tw 文字當訓練用 review；優先 evidence 原文。
    def _txt(r) -> str:
        return r.evidence or (r.summary or {}).get("zh-tw") or ""

    return [
        dspy.Example(review=_txt(r), l1_code=r.true_label).with_inputs("review")
        for r in rows
        if _txt(r)
    ]


def configure_lm() -> bool:
    """以當前 settings（base_url/token/model）配置 dspy.LM（openai-compatible）；無 token 回 False。

    對齊 client._resolve 的 provider 解析；DSPy 底層走 litellm，故 model 以 `openai/<name>` + api_base 路由。
    """
    from app.core import settings as _settings
    from app.core.config import env

    cfg = _settings.current()
    base_url = (cfg.get("base_url") or "").strip()
    provider = _settings.provider_id_for(base_url)
    token = (cfg.get("provider_tokens") or {}).get(provider) or env.openai_api_key
    if not token:
        return False
    model = cfg.get("model") or env.ai_judge_model
    kwargs: dict = {"api_key": token}
    if base_url:
        kwargs["api_base"] = base_url
    dspy.configure(lm=dspy.LM(f"openai/{model}", **kwargs))
    return True


def compile_and_persist(method: str = "bootstrap") -> dict[str, Any]:
    """離線編譯 DspyJudge（以 true_label trainset 自優化）→ 存檔。優雅降級（不拋）。

    Args:
        method: 'bootstrap'（BootstrapFewShot，少樣本起步）| 'mipro'（MIPROv2，樣本足後深優化）。

    Returns:
        {status: compiled|skipped, reason?, method, n}。無 label / 無 LM key / dspy 不可用 → skipped。
    """
    train = load_trainset()
    if train is None:
        return {"status": "skipped", "reason": "db_unavailable"}
    if len(train) < _MIN_TRAIN:
        return {
            "status": "skipped",
            "reason": "insufficient_labels",
            "n": len(train),
            "min": _MIN_TRAIN,
        }
    if not configure_lm():
        return {"status": "skipped", "reason": "no_llm_key", "n": len(train)}
    try:
        if method == "mipro":
            from dspy.teleprompt import MIPROv2

            opt = MIPROv2(metric=attribution_metric, auto="light")
            compiled = opt.compile(DspyJudge(), trainset=train)
        else:
            from dspy.teleprompt import BootstrapFewShot

            opt = BootstrapFewShot(
                metric=attribution_metric, max_bootstrapped_demos=4, max_labeled_demos=16
            )
            compiled = opt.compile(DspyJudge(), trainset=train)
    except Exception as e:  # noqa: BLE001  編譯失敗（LLM 錯 / 樣本病態）優雅回報
        return {
            "status": "skipped",
            "reason": f"compile_error: {str(e).splitlines()[0][:160]}",
            "n": len(train),
        }
    if _COMPILED_FILE is not None:
        _COMPILED_FILE.parent.mkdir(parents=True, exist_ok=True)
        compiled.save(str(_COMPILED_FILE))
    return {"status": "compiled", "method": method, "n": len(train)}
