"""config/taxonomy/*.json 線上查看 / 編輯端點（規則 tab 的 JSON 編輯器後端）。

判決規則已外部化為 config/taxonomy SSOT（純資料，前後端共讀）。本 router 讓內部調校台
可直接讀寫這些檔：寫入前 JSON 驗證 + 落地 .bak 備份，寫入後呼叫 taxonomy.reload()
使 judge 鏈即時反映新值（免重啟）。

安全：所有端點需登入；name 以白名單 + 路徑正規化雙重防穿越，且僅允許「既存」檔（不可新建任意檔）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import auth
from app.core.paths import CONFIG_DIR as _CONFIG  # repo 根 config/（統一定位，取代原 parents[4]）
from app.core.permissions import permission_keys, require_permission

router = APIRouter(prefix="/api/config", tags=["config"])

# SSOT 目錄：config/ai_judge（唯一 judge config 家）。備份統一落 config/.backups/（建議 gitignore）。
_DIRS: tuple[Path, ...] = (_CONFIG / "ai_judge",)
_BACKUP_DIR: Path = _CONFIG / ".backups"

# config/ai_judge 下的判決規則樹（rule_C-*.json / schema.json）由 /api/judge-rules 版本化管理，
# 不納入本 raw 編輯端點（避免與 DB 版本化雙寫衝突）。
_VERSIONED_NAMES = {"schema.json"}


def _is_versioned(p: Path) -> bool:
    """是否為 /api/judge-rules 管的版本化規則檔（rule_C-*.json / schema.json）。"""
    return p.name in _VERSIONED_NAMES or p.name.startswith("rule_C-")


def _editable_files() -> list[Path]:
    """config/{ai_judge,taxonomy}/ 下所有可編輯 *.json（遞迴；排除備份目錄與版本化規則檔）。"""
    out: list[Path] = []
    for base in _DIRS:
        out += [
            p for p in base.rglob("*.json") if _BACKUP_DIR not in p.parents and not _is_versioned(p)
        ]
    return sorted(out)


def _resolve(name: str) -> Path:
    """把相對 name 正規化為 config/{ai_judge,taxonomy}/ 內的既存 .json；越界 / 不存在 / 版本化檔 → 4xx。

    雙重防穿越：① resolve 後須仍位於某 SSOT 目錄內；② 須既存且非版本化規則檔。
    軸B ai_judge 優先解析，回退軸A taxonomy（與 loader 一致，呼叫端用 basename 即可）。
    """
    if not name.endswith(".json"):
        raise HTTPException(status_code=400, detail="僅允許 .json 設定檔")
    for base in _DIRS:
        target = (base / name).resolve()
        try:
            target.relative_to(base.resolve())
        except ValueError:
            continue  # 此 base 越界 → 試下一個
        if target.is_file() and not _is_versioned(target):
            return target
    raise HTTPException(status_code=404, detail=f"設定檔不存在或不可編輯：{name}")


class ConfigWriteIn(BaseModel):
    """PUT body：content 為已解析的 JSON 值（物件 / 陣列皆可）。"""

    content: Any


@router.get("/files")
def list_files(_: dict = Depends(auth.get_current_user)) -> list[dict]:
    """列出所有可編輯 config 檔（name 為相對 config/taxonomy/ 的 posix 路徑）。"""
    out: list[dict] = []
    for p in _editable_files():
        base = next(b for b in _DIRS if b in p.parents)  # 該檔所屬 SSOT 目錄
        name = p.relative_to(base).as_posix()  # basename（mappings/ 保留子路徑）
        out.append({"name": name, "bytes": p.stat().st_size})
    return out


@router.get("/files/{name:path}")
def read_file(name: str, _: dict = Depends(auth.get_current_user)) -> dict:
    """讀單一 config 檔：回傳解析後的 content + 原始 text。"""
    target = _resolve(name)
    text = target.read_text(encoding="utf-8")
    try:
        content = json.loads(text)
    except json.JSONDecodeError as e:
        # 磁碟上的檔已壞（理論上不該發生）；回 text 讓前端仍可檢視 / 修復
        raise HTTPException(status_code=422, detail=f"檔內容非合法 JSON：{e}") from e
    return {"name": name, "content": content, "text": text}


@router.put("/files/{name:path}")
def write_file(
    name: str,
    body: ConfigWriteIn,
    _: dict = Depends(require_permission(permission_keys.CONFIG_FILE_WRITE)),
) -> dict:
    """覆寫單一 config 檔：先備份 .backups/，再以 2-space/unicode 格式寫入，最後 reload taxonomy。

    Returns:
        { ok, name, bytes }；reloaded 表示 taxonomy 是否成功重載（失敗不影響寫入結果）。
    """
    target = _resolve(name)

    # 序列化（與既有檔格式一致：2 空格縮排、保留 unicode、結尾換行）
    try:
        serialized = json.dumps(body.content, ensure_ascii=False, indent=2) + "\n"
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"content 無法序列化為 JSON：{e}") from e

    # 備份當前磁碟版本（時間戳，避免覆蓋歷次備份）；config 本身有 git 版控，.bak 為working-copy 保險
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    flat = name.replace("/", "__")
    (_BACKUP_DIR / f"{flat}.{ts}.bak").write_text(
        target.read_text(encoding="utf-8"), encoding="utf-8"
    )

    target.write_text(serialized, encoding="utf-8")

    # 寫後即時重載 rule loader，使判準即時反映新值（reload 失敗不回滾寫入，回報 reloaded=false 供前端提示）
    reloaded = True
    try:
        from app.core import ai_judge

        ai_judge.reload()
    except Exception:  # noqa: BLE001  reload 失敗（如改壞結構）不應吞掉寫入成功事實
        reloaded = False

    return {
        "ok": True,
        "name": name,
        "bytes": len(serialized.encode("utf-8")),
        "reloaded": reloaded,
    }
