"""AI 使用紀錄（llm_usage）：寫入 + 多維度聚合正確性（合成列，免 LLM）。"""

from app.core import db


def _row(
    stage="attribute",
    model="gpt-5-nano",
    prompt=100,
    completion=40,
    cached=20,
    cost=0.01,
    source="product_reviews",
):
    return {
        "stage": stage,
        "model": model,
        "provider": "openai",
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_tokens": cached,
        "total_tokens": prompt + completion,
        "cost_usd": cost,
        "source": source,
        "source_id": "R1",
        "job_id": "job1",
    }


def test_insert_and_overview_aggregation(temp_db) -> None:
    """bulk 寫入 3 列 → overview KPI/分組聚合數字正確。"""
    db.insert_llm_usage_rows(
        [
            _row(stage="polarity", model="gpt-5-nano", prompt=100, completion=10, cost=0.01),
            _row(stage="attribute", model="gpt-5-nano", prompt=200, completion=50, cost=0.03),
            _row(
                stage="attribute",
                model="gpt-5-mini",
                prompt=300,
                completion=100,
                cost=0.10,
                source="conversations",
            ),
        ]
    )
    ov = db.llm_usage_overview()

    assert ov["kpi"]["calls"] == 3
    assert ov["kpi"]["tokens"] == (110 + 250 + 400)  # total_tokens 加總
    assert round(ov["kpi"]["cost"], 2) == 0.14

    by_model = {r["key"]: r for r in ov["by_model"]}
    assert by_model["gpt-5-nano"]["calls"] == 2 and round(by_model["gpt-5-nano"]["cost"], 2) == 0.04
    assert by_model["gpt-5-mini"]["calls"] == 1

    by_stage = {r["key"]: r["calls"] for r in ov["by_stage"]}
    assert by_stage == {"polarity": 1, "attribute": 2}

    by_source = {r["key"]: r["calls"] for r in ov["by_source"]}
    assert by_source == {"product_reviews": 2, "conversations": 1}


def test_insert_single_row(temp_db) -> None:
    """單筆即時寫入（ad-hoc true_label 呼叫用）→ overview 計入。"""
    db.insert_llm_usage_row(_row(stage="true_label", model="gpt-5-nano"))
    ov = db.llm_usage_overview()
    assert ov["kpi"]["calls"] == 1
    assert ov["by_stage"][0]["key"] == "true_label"


def test_empty_bulk_insert_noop(temp_db) -> None:
    """空清單 bulk 寫入回 0、overview 全零不炸。"""
    assert db.insert_llm_usage_rows([]) == 0
    ov = db.llm_usage_overview()
    assert ov["kpi"] == {"cost": 0.0, "tokens": 0, "calls": 0, "cached": 0}
    assert ov["trend"] == [] and ov["by_model"] == []


def test_cost_usd_flex_tier_half_price() -> None:
    """flex tier 計價＝標準 ×0.5（input/output/cached 全含）；未帶 tier 不打折。"""
    from app.core.judge_config import pricing

    std = pricing.cost_usd("gpt-5-mini", 1_000_000, 100_000, 200_000)
    flex = pricing.cost_usd("gpt-5-mini", 1_000_000, 100_000, 200_000, service_tier="flex")
    assert std > 0
    assert flex == round(std * 0.5, 6)
    assert pricing.cost_usd("gpt-5-mini", 100, 10, 0, service_tier=None) == pricing.cost_usd(
        "gpt-5-mini", 100, 10, 0
    )
