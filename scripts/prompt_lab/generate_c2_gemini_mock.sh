#!/usr/bin/env bash
# 使用 Gemini 3.5 Flash 批量生成 C2 Mock 数据。
#
# 默认 1 轮：114 次 API 调用，目标 260 条。
# 多轮会重复相同覆盖矩阵，但每轮独立调用并在最终合并时加 round 前缀、跨轮去重：
#   CONFIRM_COST=1 ROUNDS=5 bash scripts/prompt_lab/generate_c2_gemini_mock.sh
#
# 可配置：
#   ROUNDS=1                       生成轮数；每轮目标 260 条
#   WORKERS=4                      并发调用数
#   MODEL=gemini-3.5-flash         Generator 模型
#   OUT_DIR=tmp/prompt_lab/...     输出目录
#   PYTHON=.venv-promptlab/bin/python
#   DRY_RUN=1                      只显示规模，不调用 API

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

PYTHON="${PYTHON:-.venv-promptlab/bin/python}"
MODEL="${MODEL:-gemini-3.5-flash}"
ROUNDS="${ROUNDS:-1}"
WORKERS="${WORKERS:-4}"
OUT_DIR="${OUT_DIR:-tmp/prompt_lab/c2-gemini35-generated}"
DRY_RUN="${DRY_RUN:-0}"

if [[ ! "$ROUNDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "⛔ ROUNDS 必须是正整数，当前：$ROUNDS" >&2
  exit 2
fi
if [[ ! "$WORKERS" =~ ^[1-9][0-9]*$ ]]; then
  echo "⛔ WORKERS 必须是正整数，当前：$WORKERS" >&2
  exit 2
fi
if [[ ! -x "$PYTHON" ]]; then
  echo "⛔ 找不到可执行 Python：$PYTHON" >&2
  exit 2
fi

CALLS_PER_ROUND=114
CASES_PER_ROUND=260
TOTAL_CALLS=$((CALLS_PER_ROUND * ROUNDS))
TOTAL_CASES=$((CASES_PER_ROUND * ROUNDS))

echo "C2 Gemini Mock 批量生成"
echo "模型：$MODEL"
echo "轮数：${ROUNDS}｜预计 API 调用：${TOTAL_CALLS}｜目标样本：${TOTAL_CASES}"
echo "输出：$OUT_DIR"

if [[ "$DRY_RUN" == "1" ]]; then
  "$PYTHON" scripts/prompt_lab/generate_cases.py \
    --plan evals/prompt_lab/plans/c2_layer1_plan.json \
    --provider gemini --model "$MODEL" \
    --out "$OUT_DIR/dry-run-layer1.jsonl" --dry-run
  "$PYTHON" scripts/prompt_lab/generate_cases.py \
    --plan evals/prompt_lab/plans/c2_layer2_plan.json \
    --provider gemini --model "$MODEL" \
    --out "$OUT_DIR/dry-run-layer2.jsonl" --dry-run
  echo "✅ dry-run 完成，未调用 API"
  exit 0
fi

if [[ "${CONFIRM_COST:-0}" != "1" ]]; then
  echo "⛔ 即将调用 Gemini API $TOTAL_CALLS 次。确认费用后以 CONFIRM_COST=1 重新执行。" >&2
  exit 2
fi

"$PYTHON" - <<'PY'
import os
import sys

sys.path.insert(0, "scripts/prompt_lab")
import common

common.load_env()
if not os.environ.get("GEMINI_API_KEY"):
    raise SystemExit("⛔ 未在环境变量或 evals/prompt_lab/.env 配置 GEMINI_API_KEY")
print("✅ GEMINI_API_KEY 已配置")
PY

mkdir -p "$OUT_DIR"

for ((round = 1; round <= ROUNDS; round++)); do
  round_tag="$(printf 'r%02d' "$round")"
  round_dir="$OUT_DIR/$round_tag"
  mkdir -p "$round_dir"

  echo "▶ $round_tag / Layer 1（54 calls → 110 cases）"
  "$PYTHON" scripts/prompt_lab/generate_cases.py \
    --plan evals/prompt_lab/plans/c2_layer1_plan.json \
    --provider gemini --model "$MODEL" \
    --out "$round_dir/layer1.jsonl" \
    --workers "$WORKERS" --resume --all --confirm-cost

  echo "▶ $round_tag / Layer 2（60 calls → 150 cases）"
  "$PYTHON" scripts/prompt_lab/generate_cases.py \
    --plan evals/prompt_lab/plans/c2_layer2_plan.json \
    --provider gemini --model "$MODEL" \
    --out "$round_dir/layer2.jsonl" \
    --workers "$WORKERS" --resume --all --confirm-cost
done

"$PYTHON" - "$OUT_DIR" "$ROUNDS" "$MODEL" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "scripts/prompt_lab")
import common
from schemas import CandidateCase, normalize_for_dedup

out_dir = Path(sys.argv[1])
rounds = int(sys.argv[2])
model = sys.argv[3]
merged: list[dict] = []
seen_text: set[str] = set()
duplicate_texts = 0

for round_no in range(1, rounds + 1):
    round_tag = f"r{round_no:02d}"
    round_dir = out_dir / round_tag
    layer1 = common.read_jsonl(round_dir / "layer1.jsonl")
    layer2 = common.read_jsonl(round_dir / "layer2.jsonl")
    if len(layer1) != 110 or len(layer2) != 150:
        raise SystemExit(
            f"⛔ {round_tag} 数量不足：Layer1={len(layer1)}/110，"
            f"Layer2={len(layer2)}/150；请重新执行同一脚本，--resume 会补跑未完成生成格。"
        )
    for raw in layer1 + layer2:
        raw = dict(raw)
        old_case_id = raw["case_id"]
        raw["case_id"] = f"{round_tag}-{old_case_id}"
        if raw.get("contrast_pair_id"):
            raw["contrast_pair_id"] = f"{round_tag}-{raw['contrast_pair_id']}"
        case = CandidateCase(**raw)
        if case.generator_model != model:
            raise SystemExit(
                f"⛔ {case.case_id} generator_model={case.generator_model!r}，预期 {model!r}"
            )
        normalized = normalize_for_dedup(case.text)
        if normalized in seen_text:
            duplicate_texts += 1
            continue
        seen_text.add(normalized)
        merged.append(case.model_dump())

common.write_jsonl(out_dir / "c2-gemini35-all-candidates.jsonl", merged)
manifest = {
    "model": model,
    "rounds": rounds,
    "api_calls_planned": 114 * rounds,
    "cases_requested": 260 * rounds,
    "unique_cases_written": len(merged),
    "cross_file_duplicates_dropped": duplicate_texts,
    "output": str(out_dir / "c2-gemini35-all-candidates.jsonl"),
}
(out_dir / "generation_manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(manifest, ensure_ascii=False, indent=2))
PY

echo "✅ 全部完成：$OUT_DIR/c2-gemini35-all-candidates.jsonl"
