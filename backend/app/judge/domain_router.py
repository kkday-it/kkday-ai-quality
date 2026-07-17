"""Embedding 域路由（候選域剪枝）：學習式路由取代六域全 fan-out——零 prompt 改動、零關鍵詞。

背景：實測（2026-07-16，1,579 負/中立案）平均每案僅命中 0.87 個域，六域全跑約 85% 呼叫空轉。
本模組以系統自己的歷史初判為訓練資料（scripts/tools/train_domain_router.py 離線訓練），
runtime 以 text → OpenAI embedding → 每域 LogisticRegression 機率 → 高召回閾值選候選域，
只 fan-out 候選域（prejudge._attrs_pack pids 子集）。

三層安全防線（「歸類數量/準確性不變」的工程保證）：
1. 高召回閾值 + always_on：訓練端 per-domain holdout recall ≥ 99.5% 才給閾值；弱域列 always_on 不剪。
2. 兜底補跑：候選域閘門後零歸因 → prejudge._resolve_attrs_multi 自動補跑其餘域（最壞＝現行全跑）。
3. 影子雙跑：shadow_rate 抽樣強制全跑（production 輸出零風險）＋虛擬比對路由預測，
   漏域寫 attribution_history kind='router_shadow'（零 migration）供持續量測召回。

fail-open 鐵律：權重檔缺失 / embedding 失敗 / 非 OpenAI provider / 任何例外 → 回 None（不剪枝、
全 6 域），路由永不成為初判可用性的單點故障。

域詞彙表鐵律：thresholds / always_on / 權重檔 domains / probs 一律用**域機器值**
（content / quality / supplier / platform / service / customer——與 attributions.l1_code、
`prompt_source._domain_of` 同一詞彙表）；「C-1..C-6」碼僅用於 prompt 檔名與日誌標籤，
不得混入路由比對（審查實證：兩套詞彙表互比會令 shadow 漏域判定恆假陽性）。

Config（prejudge.json/verdict.json prejudge.domain_router，熱重載）：
    {enabled, thresholds: {content|supplier|…: float}, always_on: [域機器值…],
     shadow_rate: float, embedding_model, weights_path}
權重檔（data/router/weights.json，train_domain_router.py 產出）：
    {version, embedding_model, dim, domains: {content: {coef: […], intercept, threshold}…}}
"""

from __future__ import annotations

import json
import logging
import math
import random
import threading
from dataclasses import dataclass
from pathlib import Path

from app.judge.llm import client

_log = logging.getLogger(__name__)

# 權重檔 lazy 快取（mtime 失效：重新訓練覆蓋檔案即熱生效，無需重啟）
_weights_cache: dict | None = None
_weights_mtime: float | None = None
_WEIGHTS_LOCK = threading.Lock()


@dataclass(frozen=True)
class RouterDecision:
    """一次路由判定結果。

    pids: 候選域 prompt id 清單（prejudge._attrs_pack 的 pids 子集）；None＝不剪枝（路由關閉/不可用）。
    shadow: 本筆被抽為影子樣本——呼叫端須全跑六域（production 零風險），事後 report_shadow 虛擬比對。
    probs: 各域機率（域碼→float；shadow 比對/診斷用；不可用時 None）。
    """

    pids: list[str] | None
    shadow: bool
    probs: dict[str, float] | None = None


def _router_cfg() -> dict:
    """讀 prejudge.json/verdict.json prejudge.domain_router 區塊（熱重載跟隨 prejudge 配置快取）。"""
    from app.judge import prejudge

    return prejudge._prejudge_cfg().get("domain_router") or {}


def _weights_path() -> Path:
    """權重檔路徑（config 可覆寫；預設 data/router/weights.json）。"""
    from app.core.paths import DATA_DIR

    p = _router_cfg().get("weights_path")
    return Path(p) if p else DATA_DIR / "router" / "weights.json"


def _load_weights() -> dict | None:
    """載入權重檔（mtime 快取；缺檔/壞檔回 None＝fail-open）。"""
    global _weights_cache, _weights_mtime
    path = _weights_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    with _WEIGHTS_LOCK:
        if _weights_cache is not None and _weights_mtime == mtime:
            return _weights_cache
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data.get("domains"), dict) or not data["domains"]:
                raise ValueError("weights.json 缺 domains")
            _weights_cache, _weights_mtime = data, mtime
            return data
        except (OSError, ValueError, TypeError) as e:
            _log.warning("域路由權重檔載入失敗（fail-open 全域跑）：%s", e)
            return None


def _dom_pid_map() -> dict[str, str]:
    """域機器值（content/supplier…）→ prompt id（01_C-1_content…）。

    key 用 `prompt_source._domain_of`（檔名尾綴）——與 attributions.l1_code / gated attrs 的
    `l1_domain_code` 同一詞彙表，report_shadow 的漏域比對才成立（勿改回 C-x 碼）。
    """
    from app.judge import prompt_source

    return {
        prompt_source._domain_of(pid): pid
        for pid in prompt_source.DOMAIN_PROMPT_IDS
        if prompt_source._domain_of(pid)
    }


def _sigmoid(z: float) -> float:
    # 夾 z 防 math.exp 溢位（|z|>500 時 sigmoid 已飽和至 0/1）
    z = max(-500.0, min(500.0, z))
    return 1.0 / (1.0 + math.exp(-z))


def _score(vec: list[float], weights: dict) -> dict[str, float]:
    """embedding 向量 → 各域機率（純 python 內積；1536 維 × 6 域為微秒級，runtime 零 numpy 依賴）。"""
    probs: dict[str, float] = {}
    for dom, w in weights["domains"].items():
        coef = w.get("coef") or []
        if len(coef) != len(vec):
            raise ValueError(f"域 {dom} 權重維度 {len(coef)} ≠ embedding 維度 {len(vec)}")
        z = float(w.get("intercept", 0.0)) + sum(c * v for c, v in zip(coef, vec, strict=True))
        probs[dom] = _sigmoid(z)
    return probs


def _candidates(probs: dict[str, float], weights: dict) -> list[str]:
    """機率 → 候選域碼：per-domain 閾值（config 覆寫 > 權重檔內建）∪ always_on；空手保底 argmax。"""
    cfg = _router_cfg()
    cfg_thresholds = cfg.get("thresholds") or {}
    always_on = {str(d) for d in (cfg.get("always_on") or [])}
    picked: set[str] = set()
    for dom, p in probs.items():
        thr = cfg_thresholds.get(dom, weights["domains"].get(dom, {}).get("threshold"))
        if thr is None:  # 該域無閾值（訓練端判定不可剪）→ 視同 always_on
            picked.add(dom)
        elif p >= float(thr):
            picked.add(dom)
    if not picked:  # 保底：閾值全未命中仍保 top-1 預測域（v1 保守版不做「預測零域跳過歸因」）
        # 先保底再聯集 always_on——always_on 是風險配置（如供應商域恆跑），不頂替模型的最佳猜測。
        picked.add(max(probs, key=lambda d: probs[d]))
    picked |= always_on & set(probs)
    return sorted(picked)


def decide(text: str, polarity: str) -> RouterDecision:
    """路由判定入口（prejudge.to_findings 呼叫）。

    行為矩陣（enabled × shadow_rate）：
    - 關閉且 shadow_rate=0 → (None, shadow=False)：現行行為，零 embedding 開銷。
    - 關閉且 shadow_rate>0 → 抽中樣本算路由但 shadow=True（全跑＋虛擬比對＝純觀察期）；未抽中同上。
    - 開啟 → 全部算路由；抽中 shadow 樣本仍全跑＋比對（上線後持續監測），其餘依候選剪枝。

    任何失敗（無權重/embedding 掛/維度不符）→ (None, False)：fail-open 不剪枝。

    Args:
        text: 初判主輸入文字（prejudge._text_of 產出）。
        polarity: 整體傾向（目前不參與特徵，保留參數供 v2 分傾向閾值）。

    Returns:
        RouterDecision（pids=None 代表全 6 域）。
    """
    cfg = _router_cfg()
    enabled = bool(cfg.get("enabled", False))
    shadow_rate = float(cfg.get("shadow_rate", 0.0) or 0.0)
    shadow = shadow_rate > 0 and random.random() < shadow_rate
    if not enabled and not shadow:
        return RouterDecision(pids=None, shadow=False)
    try:
        weights = _load_weights()
        if weights is None:
            return RouterDecision(pids=None, shadow=False)
        vec = client.embed_one(
            text, model=str(cfg.get("embedding_model") or weights.get("embedding_model") or "")
        )
        if not vec:
            return RouterDecision(pids=None, shadow=False)
        probs = _score(vec, weights)
        pid_map = _dom_pid_map()
        # pid 檔名帶序號（01_..~06_..）→ 字典序＝fan-out canonical 序（勿依域機器值字母序）
        pids = sorted(pid_map[d] for d in _candidates(probs, weights) if d in pid_map)
        if not pids:
            return RouterDecision(pids=None, shadow=False)
        return RouterDecision(pids=pids, shadow=shadow, probs=probs)
    except Exception:  # noqa: BLE001  路由是省錢輔助，任何故障都不得阻斷初判（fail-open）
        _log.warning("域路由判定失敗（fail-open 全域跑）", exc_info=True)
        return RouterDecision(pids=None, shadow=False)


def _shadow_missed(
    decision: RouterDecision, gated_attrs: list[dict]
) -> tuple[set[str], set[str], list[str]]:
    """影子比對核心（純函式，供單測鎖詞彙表一致性）：回 (候選域, 命中域, 漏域)。

    兩側**同一詞彙表**＝域機器值：候選域由 pids 經 `_dom_pid_map`（`_domain_of` key）反查、
    命中域取 gated attrs 的 `l1_domain_code`——審查實證曾因一側用 C-x 碼令 missed 恆假陽性，
    修正後由 test_shadow_missed_same_vocabulary 回歸鎖定。
    """
    pid_map = _dom_pid_map()
    cand_doms = {d for d, pid in pid_map.items() if decision.pids and pid in decision.pids}
    hit_doms = {str(a.get("l1_domain_code") or "") for a in gated_attrs} - {""}
    return cand_doms, hit_doms, sorted(hit_doms - cand_doms)


def report_shadow(
    decision: RouterDecision,
    gated_attrs: list[dict],
    *,
    source: str,
    source_id: str,
) -> None:
    """影子比對：全跑產出的採信歸因 vs 路由候選——漏域即路由假陰性，落 attribution_history 留痕。

    kind='router_shadow'（Text 欄新增邏輯值零 migration，比照 kind='failure' 模式）；params 記
    {candidates, missed, probs}。best-effort：寫入失敗僅 log 不拋，絕不阻斷初判。
    """
    try:
        cand_doms, hit_doms, missed = _shadow_missed(decision, gated_attrs)
        if missed:
            _log.warning(
                "域路由影子漏域 source=%s id=%s missed=%s candidates=%s",
                source,
                source_id,
                missed,
                sorted(cand_doms),
            )
        from sqlalchemy import insert as sa_insert

        from app.core.db import tables as T

        with T.get_engine().begin() as c:
            c.execute(
                sa_insert(T.attribution_history).values(
                    source=source,
                    source_id=source_id,
                    kind="router_shadow",
                    params={
                        "candidates": sorted(cand_doms),
                        "hit": sorted(hit_doms),
                        "missed": missed,
                        "probs": {d: round(p, 4) for d, p in (decision.probs or {}).items()},
                    },
                    job_id="",
                    triggered_by="",
                )
            )
    except Exception:  # noqa: BLE001  影子留痕是量測輔助，寫不進去也不能拖垮初判
        _log.debug("router_shadow 落庫失敗 source=%s id=%s", source, source_id, exc_info=True)


def reload() -> None:
    """清權重快取（重新訓練後可手動觸發；一般靠 mtime 自動失效）。"""
    global _weights_cache, _weights_mtime
    with _WEIGHTS_LOCK:
        _weights_cache = None
        _weights_mtime = None
