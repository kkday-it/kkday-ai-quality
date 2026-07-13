"""Prompt hash manifest 建構器（PRD §14 / §18 Phase 0）——記錄 7 份 baseline judge prompt
與 generator/auditor prompt 的 SHA-256，供追溯與「baseline 未被竄改」核對。

⚠️ 7 份 judge prompt 為使用者提供、原樣導入，baseline 跑完前**不得修改**（PRD §4/§17）。
執行：python scripts/prompt_lab/build_manifest.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "evals" / "prompt_lab" / "prompts"
JUDGES = [
    "00_polarity",
    "01_C-1_content",
    "01_C-1_content_v2",  # C-1 候選 v2（PRD-C1-PROMPT-V2）：修 §17.1/17.2/17.3，主攻棄權
    "02_C-2_quality",
    "03_C-3_supplier",
    "04_C-4_platform",
    "05_C-5_service",
    "06_C-6_customer",
]
GENERATORS = ["c1_generator", "c1_auditor"]


def build() -> dict:
    """組 manifest dict（judge + generator 各檔 sha256/bytes）。"""

    def entry(rel: str) -> dict:
        p = PROMPTS_DIR / rel
        return {
            "path": f"prompts/{rel}",
            "sha256": common.sha256_file(p),
            "bytes": p.stat().st_size,
        }

    return {
        "note": "baseline C-1 judge prompt 與同伴域 prompt 的 SHA-256；跑完 baseline 前不得修改（PRD §4/§17）。",
        "domain_under_test": "C-1",
        "judges": {name: entry(f"judges/{name}.md") for name in JUDGES},
        "generators": {name: entry(f"generators/{name}.md") for name in GENERATORS},
    }


def main() -> int:
    """產出 prompts_manifest.json。"""
    manifest = build()
    out = PROMPTS_DIR / "prompts_manifest.json"
    out.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"✅ manifest：{len(manifest['judges'])} judge + {len(manifest['generators'])} generator → {out}"
    )
    for name, e in manifest["judges"].items():
        print(f"   {name}: {e['sha256'][:16]}… ({e['bytes']}B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
