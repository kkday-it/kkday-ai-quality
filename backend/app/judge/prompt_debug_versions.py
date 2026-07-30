"""售後根因調試台 Prompt 版本庫：草稿（drafts）／正式版（releases）雙軌。

形態（2026-07-30 起，取代「無指針、檔名字典序最大者即口徑」的舊設計）：

- **草稿區**＝`prompts/conversations/root_cause_drafts/YYYY-MM-DD-HHMMSS.md`，一版一檔全文快照
  （分類庫已內嵌、含實測校準層，非模板渲染）。調試台「存為新草稿」寫出；人手直接丟檔同樣
  即時生效（dev 熱掛載）。**跑批不讀草稿**，單次調試要用得明確選。
- **正式版區**＝`prompts/conversations/versions/<自訂名>.md` ＋ `index.json`（active 指針 + meta）。
  只能由既有草稿升版產生（`promote()`）。**調試台與跑批的預設口徑，且跑批唯一可讀的來源。**

為什麼要引入指針：舊設計下任何存檔立即成為線上口徑，實戰四次出事（見 `root_cause_drafts/
CHANGELOG.md`）——兩支平行版本以同一基線分叉時時間戳大的靜默吞掉另一支的 13 處改動；已退役的
欄位被舊基線帶回 10 處，使線上口徑一度「叫模型填 schema 裡不存在的欄位」；編輯器緩衝較舊把
修正覆蓋回舊寫法。根因都是缺少草稿隔離。

為什麼不重蹈上一代「雙口徑」覆轍（`v2.md`/`v3.md` + 前端契約 radio，曾分岔 320 行）：
① **輸出契約始終單一**，本模組只管「哪份 Prompt 文字」，不碰 schema／校驗器；
② **單一口徑來源**：頁面的軌別選擇器同時決定編輯器載入、單次測試與跑批，三者不可能各讀一份
   （2026-07-30 修正：原設計靠「跑批硬拒草稿」當防線，但草稿:正式版比例懸殊下那等於跑批不可用；
   真正的防線是「默認值一致」，故改為兩軌都能跑、由 manifest `prompt_kind` 顯式標明跑的是哪一軌）；
③ 草稿被使用時由 UI 醒目標示，不讓它成為第二套默認值。

`index.json` 定位＝**optional enrichment**：檔案系統仍是唯一真相源，index 只補檔名表達不了的
（active 指向、備註、作者）。index 缺項就顯示空白，**絕不因缺 meta 而讓版本讀不到**——這是
「人手丟檔即生效」這個核心體驗不被破壞的關鍵。index 整檔遺失時 active 走 fallback（見
`active_release()`），寫入一律原子（tmp + `os.replace`），避免每請求重讀時撞上半寫入狀態。

刻意不做模組級快取：dev 熱掛載 prompts/，存檔即生效是這條路徑的核心體驗，而每次請求重讀的
成本遠低於快取失效帶來的困惑（單檔約 105KB）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.core.paths import ROOT_CAUSE_DRAFTS_DIR, ROOT_CAUSE_RELEASES_DIR

_log = logging.getLogger(__name__)


class NoActiveReleaseError(FileNotFoundError):
    """正式版區一支都沒有，因此無線上口徑可用。

    刻意繼承 `FileNotFoundError`：既有 `except FileNotFoundError` 的呼叫端（如 `list_releases`、
    `resolve`）行為完全不變，只是多了一個可被精準辨識的子型別。

    為什麼要獨立型別而非沿用裸 `FileNotFoundError`：本專案有另一種語義相反的 FileNotFoundError
    ——`prompt_source.py` 的引擎 fail-loud（prompt 檔與 DB 版雙缺＝伺服器設定壞了，該回 500）。
    若在 app 層掛「FileNotFoundError → 404」的全域處理器，會把那個真故障誤報成 404。故只讓
    本型別對應 404（見 `api/main.py` 的例外處理器）。
    """


DRAFTS_DIR: Path = ROOT_CAUSE_DRAFTS_DIR
RELEASES_DIR: Path = ROOT_CAUSE_RELEASES_DIR
INDEX_FILE: Path = RELEASES_DIR / "index.json"

_STAMP_FORMAT = "%Y-%m-%d-%H%M%S"
# 草稿名＝定長時間戳（字典序即時序）。這個正則同時兼任路徑穿越守門。
_DRAFT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6}$")
# 正式版名＝人取的自訂名。與草稿分開守門：自訂名不符時間戳格式，但仍必須擋掉 `/` 與 `..`。
# 首字元強制英數——若只寫 `[A-Za-z0-9._-]+`，`..` 會整串通過（`.` 在字元集內），
# 雖然 `versions/...md` 實際不穿越目錄，但讓 `..`／`.hidden` 這類名稱過關本身就是缺陷。
_RELEASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# 檔名時間戳走台北時間：這串是人在 `ls` 與頁面上直接讀的（團隊在台北），用 UTC 會與本機時鐘
# 差 8 小時、對不上「我剛存的那版」。落庫/manifest 的時間戳仍一律 UTC，兩者用途不同不衝突。
_STAMP_TZ = ZoneInfo("Asia/Taipei")

_EMPTY_INDEX: dict[str, Any] = {"schema": 1, "active_release": None, "releases": {}, "drafts": {}}


# ── index.json（active 指針 + meta）─────────────────────────────────────────────


def _read_index() -> dict[str, Any]:
    """讀 index.json；不存在或壞掉都回空骨架（fail-soft）。

    刻意不拋錯：index 是 enrichment 而非真相源，它壞掉時版本檔本身仍該讀得到，
    只是少了 active 指向與備註——後者由 `active_release()` 的 fallback 兜住。
    """
    if not INDEX_FILE.is_file():
        return dict(_EMPTY_INDEX)
    try:
        data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _log.warning("index.json 讀取失敗，改走 fallback：%s", exc)
        return dict(_EMPTY_INDEX)
    if not isinstance(data, dict):
        _log.warning("index.json 不是 object，改走 fallback")
        return dict(_EMPTY_INDEX)
    return {**_EMPTY_INDEX, **data}


def _write_index(data: dict[str, Any]) -> None:
    """原子寫 index.json（tmp + os.replace）。

    非原子寫會讓「每請求重讀指針」撞上半寫入狀態——本 repo 有過同型事故：跑批期間編輯
    config SSOT，讀到 `JSONDecodeError: Unterminated string`，9 筆判決沒有 result 幀。
    """
    RELEASES_DIR.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, INDEX_FILE)


# ── 草稿區 ─────────────────────────────────────────────────────────────────────


def list_drafts() -> list[str]:
    """草稿名（不含副檔名），新→舊排序；目錄不存在或無草稿回空陣列。

    `glob` 非遞迴＋正則過濾：`CHANGELOG.md` 等非時間戳檔名一律不參與解析，供人寫校準歷史。
    """
    if not DRAFTS_DIR.is_dir():
        return []
    stems = [p.stem for p in DRAFTS_DIR.glob("*.md") if _DRAFT_RE.match(p.stem)]
    return sorted(stems, reverse=True)


def latest_draft() -> str:
    """最新草稿名。**注意：這不是線上口徑**——口徑看 `active_release()`。

    Raises:
        FileNotFoundError: 草稿區沒有任何 `YYYY-MM-DD-HHMMSS.md`。
    """
    drafts = list_drafts()
    if not drafts:
        raise FileNotFoundError(f"{DRAFTS_DIR} 內找不到任何草稿（需 YYYY-MM-DD-HHMMSS.md）")
    return drafts[0]


def read_draft(version: str) -> str:
    """讀指定草稿全文；version 必須是合法時間戳名（同時擋掉路徑穿越）。

    Raises:
        ValueError: version 不是合法草稿名。
        FileNotFoundError: 該草稿不存在。
    """
    if not _DRAFT_RE.match(version):
        raise ValueError(f"非法草稿名：{version!r}")
    return (DRAFTS_DIR / f"{version}.md").read_text(encoding="utf-8")


def draft_meta() -> list[dict[str, Any]]:
    """草稿清單 + index 補的 meta（新→舊）；缺 meta 的欄位留空字串，不影響列出。"""
    index = _read_index()
    metas = index.get("drafts") or {}
    out: list[dict[str, Any]] = []
    for name in list_drafts():
        m = metas.get(name) or {}
        out.append(
            {
                "version": name,
                "note": m.get("note", ""),
                "author": m.get("author", ""),
                "saved_at": m.get("saved_at", ""),
            }
        )
    return out


def save_draft(text: str, *, note: str = "", author: str = "") -> dict[str, object]:
    """把編輯後的全文存成新草稿。**不改變線上口徑**（要上線得再走 `promote()`）。

    與最新草稿逐字相同時不建檔（避免無意義堆積），回既有最新草稿並標 `created=False`。

    Args:
        text: system prompt 全文。
        note: 備註（存進 index 的 drafts meta，可空）。
        author: 存檔者（同上）。

    Returns:
        `{"version": 草稿名, "created": 是否真的建了新檔}`。

    Raises:
        ValueError: text 為空白。
    """
    body = text.strip()
    if not body:
        raise ValueError("Prompt 內容不可為空")

    existing = list_drafts()
    if existing and read_draft(existing[0]).strip() == body:
        return {"version": existing[0], "created": False}

    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(_STAMP_TZ)
    path = DRAFTS_DIR / f"{stamp.strftime(_STAMP_FORMAT)}.md"
    # 同一秒內連存兩次：往後挪秒直到不撞檔，保住「檔名唯一且字典序＝時序」
    while path.exists():
        stamp = stamp.replace(microsecond=0) + timedelta(seconds=1)
        path = DRAFTS_DIR / f"{stamp.strftime(_STAMP_FORMAT)}.md"
    path.write_text(f"{body}\n", encoding="utf-8")

    index = _read_index()
    index.setdefault("drafts", {})[path.stem] = {
        "note": note,
        "author": author,
        "saved_at": datetime.now(_STAMP_TZ).isoformat(timespec="seconds"),
    }
    _write_index(index)
    return {"version": path.stem, "created": True}


# ── 正式版區 ───────────────────────────────────────────────────────────────────


def _release_files() -> list[str]:
    """正式版區實際存在的 `.md` 檔名（不含副檔名），依 mtime 新→舊。"""
    if not RELEASES_DIR.is_dir():
        return []
    files = [p for p in RELEASES_DIR.glob("*.md") if _RELEASE_RE.match(p.stem)]
    return [p.stem for p in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)]


def active_release() -> str:
    """當前線上口徑的正式版名。

    以 index.json 的 `active_release` 為準；index 缺失/失效時 fail-soft 走 fallback：
    正式版區只有一支就是它，多支取 mtime 最新者（並告警）。

    Raises:
        NoActiveReleaseError: 正式版區沒有任何 `.md`。
    """
    index = _read_index()
    name = index.get("active_release")
    if isinstance(name, str) and (RELEASES_DIR / f"{name}.md").is_file():
        return name

    files = _release_files()
    if not files:
        raise NoActiveReleaseError(f"{RELEASES_DIR} 內找不到任何正式版（需 <名稱>.md）")
    # 指針失效（index 有值但檔案不在）與指針缺失（index 沒這個 key）都會走到這裡：只要正式版區
    # 還有檔案就降級取用，不讓「指針壞掉」升級成「整個功能不可用」——index 是 enrichment 不是真相源。
    if isinstance(name, str) and name:
        _log.warning(
            "index.json 的 active_release=%r 對應檔案不存在，降級改用：%s（共 %d 支正式版）",
            name,
            files[0],
            len(files),
        )
    elif len(files) > 1:
        _log.warning("index.json 未指定 active_release，改取 mtime 最新的正式版：%s", files[0])
    return files[0]


def read_release(name: str) -> str:
    """讀指定正式版全文；name 走自訂名白名單（擋 `/`、`..` 等路徑穿越）。

    Raises:
        ValueError: name 不是合法正式版名。
        FileNotFoundError: 該正式版不存在。
    """
    if not _RELEASE_RE.match(name):
        raise ValueError(f"非法正式版名：{name!r}")
    return (RELEASES_DIR / f"{name}.md").read_text(encoding="utf-8")


def list_releases() -> list[dict[str, Any]]:
    """正式版清單 + meta + `is_active` 標記（mtime 新→舊）；缺 meta 的欄位留空字串。"""
    index = _read_index()
    metas = index.get("releases") or {}
    try:
        active = active_release()
    except FileNotFoundError:
        active = ""
    out: list[dict[str, Any]] = []
    for name in _release_files():
        m = metas.get(name) or {}
        out.append(
            {
                "name": name,
                "source_draft": m.get("source_draft", ""),
                "note": m.get("note", ""),
                "author": m.get("author", ""),
                "promoted_at": m.get("promoted_at", ""),
                "is_active": name == active,
            }
        )
    return out


def active_prompt() -> str:
    """當前線上口徑的 system prompt 全文（調試台預設值與跑批唯一來源）。"""
    return read_release(active_release())


def promote(draft: str, name: str, *, note: str = "", author: str = "") -> dict[str, object]:
    """把某個草稿升為正式版，並讓它成為 active。

    來源刻意限定為**已存檔的草稿**而非「編輯器當前內容」：升版是上線動作，要升的必須是
    已經在草稿區、可被 diff 與回查的那一份（初判線的草稿採納同此紀律）。

    Args:
        draft: 來源草稿名（時間戳）。
        name: 正式版名稱（自訂，如 `release-v2`）。
        note: 備註（建議必填，供日後回顧「這版為何上線」）。
        author: 升版者。

    Returns:
        `{"name": 正式版名, "source_draft": 來源草稿, "previous_active": 升版前的 active 或 ""}`。

    Raises:
        ValueError: 草稿名或正式版名不合法、或該名稱已存在。
        FileNotFoundError: 來源草稿不存在。
    """
    if not _DRAFT_RE.match(draft):
        raise ValueError(f"非法草稿名：{draft!r}")
    if not _RELEASE_RE.match(name):
        raise ValueError(f"非法正式版名：{name!r}（僅允許英數與 . _ -，長度 1–64）")

    src = DRAFTS_DIR / f"{draft}.md"
    if not src.is_file():
        raise FileNotFoundError(f"來源草稿不存在：{src}")

    dst = RELEASES_DIR / f"{name}.md"
    if dst.exists():
        raise ValueError(f"正式版名稱已存在：{name}（正式版不覆寫，請換名）")

    index = _read_index()
    previous = index.get("active_release") or ""

    RELEASES_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)

    import hashlib

    index["active_release"] = name
    index.setdefault("releases", {})[name] = {
        "source_draft": draft,
        "note": note,
        "author": author,
        "promoted_at": datetime.now(_STAMP_TZ).isoformat(timespec="seconds"),
        "sha256": hashlib.sha256(dst.read_bytes()).hexdigest(),
    }
    _write_index(index)
    return {"name": name, "source_draft": draft, "previous_active": previous}


def set_active_release(name: str, *, author: str = "") -> dict[str, object]:
    """把 active 指標改指向某個**既有**正式版（回退／切換上線版本）。

    與 `promote()` 的分工：promote 是「把草稿變成新的正式版」（複製檔案 + 新增版本紀錄），
    本函式只動指標——**不複製檔案、不新增版本紀錄**。升錯版時若只有 promote 可用，就只能再升
    一版（版本號無謂膨脹、且製造一份內容重複的檔案），閉環缺的就是這一塊。

    Args:
        name: 目標正式版名（必須已存在）。
        author: 操作者（記進 index 的 `active_changed_by`，供回查誰切的）。

    Returns:
        `{"name": 目標版, "previous_active": 切換前的 active 或 ""}`。

    Raises:
        ValueError: 名稱不合法。
        FileNotFoundError: 該正式版不存在（不允許把指標指向不存在的檔案——那正是 P0 事故的形狀）。
    """
    if not _RELEASE_RE.match(name):
        raise ValueError(f"非法正式版名：{name!r}")
    if not (RELEASES_DIR / f"{name}.md").is_file():
        raise FileNotFoundError(f"正式版不存在：{name}")

    index = _read_index()
    previous = index.get("active_release") or ""
    if previous == name:
        return {"name": name, "previous_active": previous}

    index["active_release"] = name
    index["active_changed_at"] = datetime.now(_STAMP_TZ).isoformat(timespec="seconds")
    index["active_changed_by"] = author
    _write_index(index)
    _log.info("active_release 由 %r 切換為 %r（操作者 %s）", previous, name, author or "-")
    return {"name": name, "previous_active": previous}


# ── 呼叫端共用解析 ─────────────────────────────────────────────────────────────


def resolve(text: str, *, allow_draft: bool = False) -> tuple[str, str, str]:
    """把「呼叫端給的 Prompt」收斂成本次實際要用的全文、版本名與種類。

    單次調試與批量跑批共用同一條解析，避免兩邊對「沒給 Prompt 時該用哪份」各有一套答案；
    種類欄位讓呼叫端（與前端）不必自己比字串判斷「這是不是線上版」——舊版前後端各有一套
    比對演算法（一邊 strip 一邊不 strip），是靜默 drift 的來源。

    Args:
        text: 呼叫端送來的全文；空白＝沒指定，取當前正式版。
        allow_draft: 允許把內容認成草稿（單次調試 True；跑批 False）。

    Returns:
        `(全文, 版本名, 種類)`；種類 ∈ `{"release", "draft", ""}`，`""`＝頁面臨時編輯過、
        不對應任何存檔版本（此時仍可靠 manifest 的 `prompt_sha256` 追出實際用了什麼）。

    Raises:
        FileNotFoundError: 沒給 text 且正式版區是空的。
    """
    body = text.strip()
    if not body:
        name = active_release()
        return read_release(name), name, "release"

    try:
        active = active_release()
        if read_release(active).strip() == body:
            return body, active, "release"
    except FileNotFoundError:
        pass  # 正式版區空著：仍允許用臨時內容跑，不阻斷

    if allow_draft:
        for d in list_drafts():
            if read_draft(d).strip() == body:
                return body, d, "draft"

    return body, "", ""
