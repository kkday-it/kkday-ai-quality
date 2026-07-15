#!/usr/bin/env bash
# C3～C6 通用 Gemini Mock 批处理：多轮、resume、跨轮去重与 manifest。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

PYTHON="${PYTHON:-.venv-promptlab/bin/python}"
DOMAIN="${DOMAIN:-}"
MODEL="${MODEL:-gemini-3.5-flash}"
ROUNDS="${ROUNDS:-1}"
WORKERS="${WORKERS:-4}"
DRY_RUN="${DRY_RUN:-0}"

case "$DOMAIN" in
  C-3) SLUG=c3; L1=130; L2=246; CALLS=156 ;;
  C-4) SLUG=c4; L1=90;  L2=108; CALLS=75 ;;
  C-5) SLUG=c5; L1=90;  L2=108; CALLS=75 ;;
  C-6) SLUG=c6; L1=120; L2=198; CALLS=129 ;;
  *) echo "⛔ DOMAIN 必须是 C-3/C-4/C-5/C-6" >&2; exit 2 ;;
esac

OUT_DIR="${OUT_DIR:-tmp/prompt_lab/${SLUG}-gemini35-5rounds}"
[[ "$ROUNDS" =~ ^[1-9][0-9]*$ ]] || { echo "⛔ ROUNDS 必须是正整数" >&2; exit 2; }
[[ "$WORKERS" =~ ^[1-9][0-9]*$ ]] || { echo "⛔ WORKERS 必须是正整数" >&2; exit 2; }
[[ -x "$PYTHON" ]] || { echo "⛔ 找不到 Python：$PYTHON" >&2; exit 2; }

echo "${DOMAIN} Gemini Mock｜model=${MODEL}｜rounds=${ROUNDS}｜planned_calls=$((CALLS * ROUNDS))｜target_rows=$(((L1 + L2) * ROUNDS))"
echo "输出：$OUT_DIR"

if [[ "$DRY_RUN" == "1" ]]; then
  "$PYTHON" scripts/prompt_lab/generate_cases.py --plan "evals/prompt_lab/plans/${SLUG}_layer1_plan.json" --provider gemini --model "$MODEL" --out "$OUT_DIR/dry-layer1.jsonl" --dry-run
  "$PYTHON" scripts/prompt_lab/generate_cases.py --plan "evals/prompt_lab/plans/${SLUG}_layer2_plan.json" --provider gemini --model "$MODEL" --out "$OUT_DIR/dry-layer2.jsonl" --dry-run
  echo "✅ dry-run 完成（零 API）"
  exit 0
fi

[[ "${CONFIRM_COST:-0}" == "1" ]] || { echo "⛔ 真跑需 CONFIRM_COST=1" >&2; exit 2; }
"$PYTHON" - <<'PY'
import os, sys
sys.path.insert(0, "scripts/prompt_lab")
import common
common.load_env()
if not os.environ.get("GEMINI_API_KEY"):
    raise SystemExit("⛔ 缺少 GEMINI_API_KEY")
print("✅ GEMINI_API_KEY 已配置")
PY

mkdir -p "$OUT_DIR"
: > "$OUT_DIR/generation.log"
ROUND_CONTEXTS=(
  "城市票券与短程活动；台湾繁体为主；短评与口语；地点覆盖东亚"
  "长途交通与多日行程；简体及中英混合；较长叙述；地点覆盖东南亚"
  "亲子、长者与无障碍场景；繁简混合；克制语气；地点覆盖日韩"
  "户外、水上与季节活动；中英夹杂；情绪化及反问；地点覆盖欧美"
  "餐饮、住宿、展馆与特殊体验；多语言噪声；中性混合及对抗表达"
)

run_plan() {
  local plan="$1" out="$2" expected="$3" context="$4"
  local attempt count
  for attempt in 1 2 3; do
    PROMPT_LAB_ROUND_CONTEXT="$context" "$PYTHON" scripts/prompt_lab/generate_cases.py \
      --plan "$plan" --provider gemini --model "$MODEL" --out "$out" \
      --workers "$WORKERS" --resume --all --confirm-cost 2>&1 | tee -a "$OUT_DIR/generation.log"
    count="$($PYTHON - "$out" <<'PY'
import sys
sys.path.insert(0, "scripts/prompt_lab")
import common
print(len(common.read_jsonl(sys.argv[1])))
PY
)"
    [[ "$count" == "$expected" ]] && return 0
    echo "⚠️ ${out} 数量 ${count}/${expected}，resume 重试 ${attempt}/3" | tee -a "$OUT_DIR/generation.log"
  done
  echo "⛔ $out 连续重试后仍不足 $expected" >&2
  return 1
}

for ((round=1; round<=ROUNDS; round++)); do
  tag="$(printf 'r%02d' "$round")"
  dir="$OUT_DIR/$tag"
  mkdir -p "$dir"
  context="${ROUND_CONTEXTS[$(((round - 1) % 5))]}；轮次=${tag}，禁止复用前轮措辞"
  echo "▶ ${tag} Layer1" | tee -a "$OUT_DIR/generation.log"
  run_plan "evals/prompt_lab/plans/${SLUG}_layer1_plan.json" "$dir/layer1.jsonl" "$L1" "$context"
  echo "▶ ${tag} Layer2" | tee -a "$OUT_DIR/generation.log"
  run_plan "evals/prompt_lab/plans/${SLUG}_layer2_plan.json" "$dir/layer2.jsonl" "$L2" "$context"
done

"$PYTHON" - "$OUT_DIR" "$ROUNDS" "$MODEL" "$DOMAIN" "$SLUG" "$L1" "$L2" "$CALLS" <<'PY'
from __future__ import annotations
import json, subprocess, sys
from collections import Counter
from datetime import datetime
from pathlib import Path
sys.path.insert(0, "scripts/prompt_lab")
import common
from schemas import CandidateCase, normalize_for_dedup

out_dir, rounds, model, domain, slug, l1n, l2n, calls = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5], int(sys.argv[6]), int(sys.argv[7]), int(sys.argv[8])
merged, seen, dedupe = [], set(), []
for rn in range(1, rounds + 1):
    tag = f"r{rn:02d}"
    rows = common.read_jsonl(out_dir / tag / "layer1.jsonl") + common.read_jsonl(out_dir / tag / "layer2.jsonl")
    if len(rows) != l1n + l2n:
        raise SystemExit(f"⛔ {tag} 行数={len(rows)}，预期 {l1n+l2n}")
    for raw in rows:
        raw = dict(raw)
        raw["case_id"] = f"{tag}-{raw['case_id']}"
        raw["generation_plan_id"] = f"{tag}-{raw['generation_plan_id']}"
        if raw.get("contrast_pair_id"):
            raw["contrast_pair_id"] = f"{tag}-{raw['contrast_pair_id']}"
        case = CandidateCase(**raw)
        norm = normalize_for_dedup(case.text)
        if norm in seen:
            dedupe.append({"case_id": case.case_id, "normalized_text": norm})
            continue
        seen.add(norm)
        merged.append(case.model_dump())

all_path = out_dir / f"{slug}-all-candidates.jsonl"
common.write_jsonl(all_path, merged)
common.write_jsonl(out_dir / "dedupe-records.jsonl", dedupe)
slice_counts = {
    "domain": domain,
    "total": len(merged),
    "expected_domain": Counter(x["expected_domain"] for x in merged),
    "case_family": Counter(x["case_family"] for x in merged),
    "expected_l2": Counter(code for x in merged for code in x["expected_l2_codes"]),
    "boundary": Counter(x.get("boundary_with") or "" for x in merged),
}
(out_dir / "slice-counts.json").write_text(json.dumps(slice_counts, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
git_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
manifest = {
    "created_at": datetime.now().astimezone().isoformat(), "domain": domain,
    "provider": "gemini", "model": model, "rounds": rounds,
    "generator_prompt_path": f"evals/prompt_lab/prompts/generators/{slug}_generator.md",
    "generator_prompt_sha256": common.sha256_file(f"evals/prompt_lab/prompts/generators/{slug}_generator.md"),
    "plan_sha256": {
        "layer1": common.sha256_file(f"evals/prompt_lab/plans/{slug}_layer1_plan.json"),
        "layer2": common.sha256_file(f"evals/prompt_lab/plans/{slug}_layer2_plan.json"),
    },
    "api_calls_planned": calls * rounds, "cases_requested": (l1n + l2n) * rounds,
    "unique_cases_written": len(merged), "duplicates_dropped": len(dedupe),
    "duplicate_rate": round(len(dedupe) / ((l1n + l2n) * rounds), 6),
    "output": str(all_path), "output_sha256": common.sha256_file(all_path),
    "git_commit": git_commit, "workspace_dirty": dirty,
    "resume": True, "dedupe_normalization": "Unicode NFKC + collapsed whitespace",
}
(out_dir / "generation-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
common.write_jsonl(out_dir / "failures.jsonl", [])
print(json.dumps(manifest, ensure_ascii=False, indent=2))
if len(merged) < int((l1n + l2n) * rounds * 0.98):
    raise SystemExit("⛔ 去重后不足目标 98%，必须定向 top-up")
PY

echo "✅ 完成：$OUT_DIR/${SLUG}-all-candidates.jsonl"
