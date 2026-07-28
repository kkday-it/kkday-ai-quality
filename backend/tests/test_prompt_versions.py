"""初判 Prompt 檔案版本庫：一版一檔、ACTIVE 指標、base_version 樂觀鎖。"""

from __future__ import annotations

import threading

import pytest

from app.judge import prompt_versions as pv

PID = "01_C-1_content"
BODY = "# 測試判官\n\n## System\n\n```\n你是判官。\n```\n"


@pytest.fixture(autouse=True)
def _tmp_prompts(monkeypatch, tmp_path):
    """把版本庫指向 tmp，避免測試污染 repo 內真正的 prompts/。"""
    monkeypatch.setattr(pv, "PROMPTS_DIR", tmp_path)
    # prompt_dir() 直接讀模組級 PROMPTS_DIR，故 patch 該名稱即可
    return tmp_path


def _save(text: str, base: str | None, **kw):
    return pv.save_version(PID, text, expected_base_version=base, **kw)


def test_first_save_initialises_active_pointer() -> None:
    result = _save(BODY, None, author="alvin", note="初版")
    assert result["created"] is True
    assert pv.active_version(PID) == result["version"]
    assert pv.active_text(PID).strip() == BODY.strip()


def test_frontmatter_is_stripped_from_body_but_readable_as_meta() -> None:
    """metadata 用 HTML 註解夾帶，讀本文時必須剝乾淨——否則會被當成 prompt 內容送進模型。"""
    version = _save(BODY, None, author="alvin", note="初版")["version"]
    assert "prompt-version" not in pv.read_version(PID, version)
    meta = pv.version_meta(PID, version)
    assert meta["author"] == "alvin"
    assert meta["note"] == "初版"
    assert meta["version"] == version


def test_stale_base_version_is_rejected() -> None:
    """本模組防 lost update 的全部依據：基線不符就拒絕，不是靜默覆蓋。"""
    v1 = _save(BODY, None)["version"]
    _save(BODY + "\n第二版\n", v1)  # 別人先存了一版

    with pytest.raises(pv.ConflictError) as exc:
        _save(BODY + "\n我的改動\n", v1)  # 我還拿著舊基線
    assert v1 in str(exc.value)


def test_first_save_rejects_non_none_base() -> None:
    """尚無任何版本時，呼叫端卻帶著某個基線＝它看到的是別處的狀態，一律拒絕。"""
    with pytest.raises(pv.ConflictError):
        _save(BODY, "v20260101000000")


def test_identical_content_does_not_create_new_version() -> None:
    v1 = _save(BODY, None)["version"]
    again = _save(BODY, v1)
    assert again == {"version": v1, "created": False}
    assert pv.list_versions(PID) == [v1]


def test_set_active_switches_pointer_without_new_file() -> None:
    v1 = _save(BODY, None)["version"]
    v2 = _save(BODY + "\n第二版\n", v1)["version"]

    pv.set_active(PID, v1, expected_base_version=v2)
    assert pv.active_version(PID) == v1
    assert pv.active_text(PID).strip() == BODY.strip()
    # 切指標不複製新檔：歷史仍只有兩版
    assert pv.list_versions(PID) == [v2, v1]


def test_set_active_also_guards_base_version() -> None:
    v1 = _save(BODY, None)["version"]
    _save(BODY + "\n第二版\n", v1)  # active 前進到第二版
    with pytest.raises(pv.ConflictError):
        pv.set_active(PID, v1, expected_base_version=v1)  # 手上的基線已過期


def test_set_active_rejects_unknown_version() -> None:
    v1 = _save(BODY, None)["version"]
    with pytest.raises(pv.VersionNotFoundError):
        pv.set_active(PID, "v20260101000000", expected_base_version=v1)


def test_list_history_marks_active() -> None:
    v1 = _save(BODY, None, author="a", note="one")["version"]
    v2 = _save(BODY + "\n2\n", v1, author="b", note="two")["version"]
    history = pv.list_history(PID)
    assert [h["version"] for h in history] == [v2, v1]
    assert [h["is_active"] for h in history] == [True, False]
    assert history[0]["note"] == "two"


def test_active_text_fails_loud_when_uninitialised() -> None:
    """缺 ACTIVE 時必須拋錯而非 fallback——靜默退回別版＝線上判準悄悄變成另一套。"""
    with pytest.raises(pv.VersionNotFoundError):
        pv.active_text(PID)


def test_active_text_fails_loud_when_pointer_dangles(_tmp_prompts) -> None:
    _save(BODY, None)
    (_tmp_prompts / PID / "ACTIVE").write_text("v20260101000000\n", encoding="utf-8")
    with pytest.raises(pv.VersionNotFoundError):
        pv.active_text(PID)


@pytest.mark.parametrize("bad", ["../../etc/passwd", "01_C-1_content/../..", "C-1", ""])
def test_prompt_id_rejects_path_traversal(bad: str) -> None:
    with pytest.raises(ValueError):
        pv.prompt_dir(bad)


@pytest.mark.parametrize("bad", ["../../etc/passwd", "v1", "20260101000000", "v2026010100000"])
def test_version_name_rejects_path_traversal(bad: str) -> None:
    _save(BODY, None)
    with pytest.raises(ValueError):
        pv.read_version(PID, bad)


def test_blank_text_rejected() -> None:
    with pytest.raises(ValueError):
        _save("   \n  ", None)


def test_concurrent_saves_only_one_wins(_tmp_prompts) -> None:
    """兩條 thread 拿同一個基線同時存：必須恰好一個成功、另一個 ConflictError。

    直接對應使用者在調試台踩過三次的情境（平行編輯靜默互蓋）。
    """
    v1 = _save(BODY, None)["version"]
    results: list[object] = []
    barrier = threading.Barrier(2)

    def worker(suffix: str) -> None:
        barrier.wait()  # 盡量讓兩者同時進鎖
        try:
            results.append(_save(f"{BODY}\n{suffix}\n", v1))
        except pv.ConflictError as exc:
            results.append(exc)

    threads = [threading.Thread(target=worker, args=(s,)) for s in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok = [r for r in results if isinstance(r, dict)]
    conflicts = [r for r in results if isinstance(r, pv.ConflictError)]
    assert len(ok) == 1, f"應恰好一個成功，實得 {results}"
    assert len(conflicts) == 1
    assert pv.active_version(PID) == ok[0]["version"]


# ── 草稿檔案層（last-write-wins，不套樂觀鎖）──────────────────────────────


class TestDrafts:
    """草稿與版本共用 frontmatter 讀寫，但併發策略刻意不同（草稿是未定案的個人狀態）。"""

    @pytest.fixture(autouse=True)
    def _tmp_drafts(self, monkeypatch, tmp_path):
        from app.judge import prompt_drafts_file as pdf

        monkeypatch.setattr(pdf, "PROMPTS_DIR", tmp_path)
        return pdf

    def test_upsert_then_get_roundtrip(self, _tmp_drafts) -> None:
        pdf = _tmp_drafts
        pdf.upsert_draft(PID, BODY, base_version="v20260724041913", updated_by="alvin")
        draft = pdf.get_draft(PID)
        assert draft is not None
        assert draft["text"].strip() == BODY.strip()
        assert draft["base_version"] == "v20260724041913"
        assert draft["updated_by"] == "alvin"
        assert draft["updated_at"]

    def test_get_missing_returns_none(self, _tmp_drafts) -> None:
        assert _tmp_drafts.get_draft(PID) is None

    def test_upsert_overwrites_last_write_wins(self, _tmp_drafts) -> None:
        pdf = _tmp_drafts
        pdf.upsert_draft(PID, BODY, base_version="v20260724041913")
        pdf.upsert_draft(PID, BODY + "\n後來的\n", base_version="v20260724041913")
        assert "後來的" in pdf.get_draft(PID)["text"]

    def test_list_drafts_excludes_body(self, _tmp_drafts) -> None:
        pdf = _tmp_drafts
        pdf.upsert_draft(PID, BODY, base_version="v20260724041913", updated_by="a")
        rows = pdf.list_drafts()
        assert [r["prompt_id"] for r in rows] == [PID]
        assert "text" not in rows[0]

    def test_delete_reports_whether_it_existed(self, _tmp_drafts) -> None:
        pdf = _tmp_drafts
        pdf.upsert_draft(PID, BODY, base_version="v20260724041913")
        assert pdf.delete_draft(PID) is True
        assert pdf.delete_draft(PID) is False

    def test_blank_draft_rejected(self, _tmp_drafts) -> None:
        with pytest.raises(ValueError):
            _tmp_drafts.upsert_draft(PID, "  \n ", base_version="v20260724041913")

    def test_draft_path_rejects_traversal(self, _tmp_drafts) -> None:
        with pytest.raises(ValueError):
            _tmp_drafts.get_draft("../../etc/passwd")
