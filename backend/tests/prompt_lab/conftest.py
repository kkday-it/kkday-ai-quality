"""pytest 設定：把 scripts/prompt_lab 加入 sys.path，並提供 repo 路徑 fixture。

Prompt Lab 為獨立模組（不 import backend.app），測試以 sibling import 方式載入其模組。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LAB_DIR = REPO_ROOT / "scripts" / "prompt_lab"
sys.path.insert(0, str(LAB_DIR))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def prompts_dir() -> Path:
    return REPO_ROOT / "evals" / "prompt_lab" / "prompts"


@pytest.fixture(scope="session")
def plans_dir() -> Path:
    return REPO_ROOT / "evals" / "prompt_lab" / "plans"


@pytest.fixture()
def c1_prompt_path(prompts_dir: Path) -> Path:
    return prompts_dir / "judges" / "01_C-1_content.md"
