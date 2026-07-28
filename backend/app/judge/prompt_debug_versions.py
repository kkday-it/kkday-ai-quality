"""售後根因調試台 Prompt 版本庫：時間戳一版一檔，永遠以最新版為唯一口徑。

形態（2026-07-27 起，取代舊 `vN.md` append-only ＋ 前端契約切換的做法）：

- 目錄＝`prompts/debug/after_sales_root_cause/`，一版一檔 `YYYY-MM-DD-HHMMSS.md`＝該版 system prompt
  全文快照（分類庫已內嵌、含實測校準層，非模板渲染）。
- 「最新版」＝檔名字典序最大者（時間戳定長，字典序即時序）。調試台與批量跑批**都只讀這一份**，
  不提供版本切換——線上只有一套口徑，才不會出現「頁面調的是 A、跑批跑的是 B」。
- 新版由調試台「存為新版本」寫出（`save()`）；人手直接把檔案丟進目錄同樣即時生效（dev 熱掛載）。
- `CHANGELOG.md` 等非時間戳檔名一律不參與版本解析，供人寫校準歷史。

刻意不做模組級快取：dev 熱掛載 prompts/，存檔即生效是這條路徑的核心體驗，
而單檔僅約 60KB、每次請求重讀的成本遠低於快取失效帶來的困惑。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core.paths import PROMPTS_DIR

PROMPT_DIR: Path = PROMPTS_DIR / "debug" / "after_sales_root_cause"

_STAMP_FORMAT = "%Y-%m-%d-%H%M%S"
_VERSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6}$")
# 檔名時間戳走台北時間：這串是人在 `ls` 與頁面上直接讀的（團隊在台北），用 UTC 會與本機時鐘
# 差 8 小時、對不上「我剛存的那版」。落庫/manifest 的時間戳仍一律 UTC，兩者用途不同不衝突。
_STAMP_TZ = ZoneInfo("Asia/Taipei")


def list_versions() -> list[str]:
    """目錄內所有版本名（不含副檔名），新→舊排序；目錄不存在或無版本檔回空陣列。"""
    if not PROMPT_DIR.is_dir():
        return []
    stems = [p.stem for p in PROMPT_DIR.glob("*.md") if _VERSION_RE.match(p.stem)]
    return sorted(stems, reverse=True)


def latest_version() -> str:
    """最新版本名。

    Raises:
        FileNotFoundError: 目錄內沒有任何 `YYYY-MM-DD-HHMMSS.md` 版本檔。
    """
    versions = list_versions()
    if not versions:
        raise FileNotFoundError(
            f"{PROMPT_DIR} 內找不到任何 Prompt 版本檔（需 YYYY-MM-DD-HHMMSS.md）"
        )
    return versions[0]


def read_version(version: str) -> str:
    """讀指定版本全文；version 必須是合法時間戳名（同時擋掉路徑穿越）。

    Raises:
        ValueError: version 不是合法版本名。
        FileNotFoundError: 該版本檔不存在。
    """
    if not _VERSION_RE.match(version):
        raise ValueError(f"非法 Prompt 版本名：{version!r}")
    return (PROMPT_DIR / f"{version}.md").read_text(encoding="utf-8")


def latest_prompt() -> str:
    """最新版 system prompt 全文（調試台預設值與跑批缺省口徑）。"""
    return read_version(latest_version())


def resolve(text: str) -> tuple[str, str]:
    """把「呼叫端給的 Prompt」收斂成本次實際要用的全文與版本標記。

    單次調試與批量跑批共用同一條解析，避免兩邊對「沒給 Prompt 時該用哪份」各有一套答案。

    Args:
        text: 呼叫端送來的全文；空白＝沒指定，取最新版。

    Returns:
        `(全文, 版本名)`。版本名為空字串＝頁面臨時編輯過、不對應任何存檔版本
        （此時仍可靠 manifest 的 prompt_sha256 追出實際用了什麼）。

    Raises:
        FileNotFoundError: 沒給 text 且版本庫是空的。
    """
    body = text.strip()
    if not body:
        version = latest_version()
        return read_version(version), version

    existing = list_versions()
    if existing and read_version(existing[0]).strip() == body:
        return body, existing[0]
    return body, ""


def save(text: str) -> dict[str, object]:
    """把頁面編輯後的全文存成新版本檔。

    與最新版逐字相同時不建檔（避免無意義的版本堆積），回既有最新版並標 `created=False`。

    Args:
        text: system prompt 全文。

    Returns:
        `{"version": 版本名, "created": 是否真的建了新檔}`。

    Raises:
        ValueError: text 為空白。
    """
    body = text.strip()
    if not body:
        raise ValueError("Prompt 內容不可為空")

    existing = list_versions()
    if existing and read_version(existing[0]).strip() == body:
        return {"version": existing[0], "created": False}

    PROMPT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(_STAMP_TZ)
    path = PROMPT_DIR / f"{stamp.strftime(_STAMP_FORMAT)}.md"
    # 同一秒內連存兩次：往後挪秒直到不撞檔，保住「檔名唯一且字典序＝時序」
    while path.exists():
        stamp = stamp.replace(microsecond=0) + timedelta(seconds=1)
        path = PROMPT_DIR / f"{stamp.strftime(_STAMP_FORMAT)}.md"
    path.write_text(f"{body}\n", encoding="utf-8")
    return {"version": path.stem, "created": True}
