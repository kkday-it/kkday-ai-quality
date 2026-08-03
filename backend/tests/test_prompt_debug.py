"""售後根因 Prompt 調試台：分類 SSOT、單一輸出契約、Prompt 版本庫與單次配置覆蓋。"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

from app.judge import prompt_debug
from app.judge import prompt_debug_versions as versions


def _base_result(**overrides):
    """一筆合法的非跳出判定（單一契約：全欄禁 null、keywords 陣列、urgency 1–5）。"""
    value = {
        "L2": "憑證/取票未送達或催件",
        "L1": "[104] 訂單確認問題",
        "L3": "憑證送達延遲",
        "L4": "n/a",
        "summary": "旅客出發前仍未收到電子票，要求協助確認送達時程。",
        "keywords": ["電子票", "未收到", "出發前"],
        "sentiment": "negative",
        "urgency": 4,
        "money_mention_flag": False,
        "fulfillment_mention_flag": True,
        "multi_issue_flag": False,
        "redirected_to_cancel_flag": False,
        "no_actionable_content": False,
        "confidence": 0.93,
    }
    value.update(overrides)
    return value


def _embedded_taxonomy(prompt: str) -> dict:
    """抽 Prompt 快照內嵌的分類庫與兩個 L4 值域（`<taxonomy>`／`<L4_*>` 區塊）。"""
    block = re.search(r"<taxonomy>\s*(\{.*?\})\s*</taxonomy>", prompt, re.S)
    assert block, "Prompt 快照找不到 <taxonomy> 區塊：分類庫沒內嵌，模型會拿不到判準"
    out = json.loads(block.group(1))
    for tag in ("L4_modify_target", "L4_oot_subtype"):
        values = re.search(rf"<{tag}>\s*(\[.*?\])\s*</{tag}>", prompt, re.S)
        assert values, f"Prompt 快照找不到 <{tag}> 區塊"
        out[tag] = json.loads(values.group(1))
    return out


def test_defaults_carry_both_tracks_and_taxonomy_derived_schema() -> None:
    """payload 只有一套契約：兩軌 Prompt 各就各位 ＋ 由分類 SSOT 派生的 schema/欄位卡。

    調試台是**草稿工作台**，載入口徑＝最新草稿（`system_prompt`）；線上口徑另走
    `release_prompt`／`active_release`。兩者刻意分開斷言——混成一條會在草稿領先正式版時
    誤紅（2026-08-03 就是這樣：v4 草稿已存、正式版還在 v3，這條斷言先紅了）。
    """
    payload = prompt_debug.defaults_payload()
    assert payload["L2_count"] == 24
    assert payload["L1_count"] == 6
    assert payload["latest_draft"] == versions.latest_draft()
    assert payload["system_prompt"] == versions.read_draft(payload["latest_draft"])
    assert payload["active_release"] == versions.active_release()
    assert payload["release_prompt"] == versions.active_prompt()
    # 正式版清單裡恰有一個 active，且就是它
    actives = [r["name"] for r in payload["releases"] if r["is_active"]]
    assert actives == [payload["active_release"]]
    assert payload["drafts"], "草稿區是空的：調試台會沒有可比對的歷史"
    # 靜態快照：分類庫已內嵌，不該再留模板佔位符
    assert "{{TAXONOMY_JSON}}" not in payload["system_prompt"]

    schema = payload["output_schema"]
    assert "其他" in schema["properties"]["L2"]["enum"]
    assert "n/a" in schema["properties"]["L4"]["enum"]
    assert "$schema" not in schema
    assert [field["key"] for field in payload["output_fields"]] == [
        "L1",
        "L2",
        "L3",
        "L4",
        "summary",
        "keywords",
        "sentiment",
        "urgency",
        "money_mention_flag",
        "fulfillment_mention_flag",
        "multi_issue_flag",
        "redirected_to_cancel_flag",
        "no_actionable_content",
        "confidence",
    ]
    # 已清退的欄位不得復活（tail_theme / urgency_flag＝v2 契約；oot_subtype 自 2026-07-28 起
    # 不再是獨立欄位——260803 表把跳出子型收進 L4 的第三分支，值域名沿用但欄位不得回來）
    assert {"tail_theme", "urgency_flag", "oot_subtype"}.isdisjoint(
        {field["key"] for field in payload["output_fields"]}
    )
    assert payload["sources"]["field_definitions_document"]["document_id"] == (
        "1FFFqsGPUhOd0oVG4uDbSgVfsdqdYYRuy5fLIE0tYpMA"
    )


def test_output_cascade_narrows_each_level_to_its_parent_branch() -> None:
    """L1→L2→L3 與條件式 L2→L4 級聯由 SSOT 派生：下層清單必須是上層那一支底下的值，且與 schema enum 同源。"""
    taxonomy = prompt_debug.load_taxonomy()
    cascade = prompt_debug.output_cascade(taxonomy)
    schema = prompt_debug.output_schema(taxonomy)

    assert cascade["L2"]["parent"] == "L1"
    assert cascade["L3"]["parent"] == "L2"
    assert cascade["L4"]["parent"] == "L2"

    by_l1 = cascade["L2"]["options_by_parent"]
    # 6 個主題（含無代碼的「現場履約問題」）+ 跳出分支；攤平後恰為 schema 的 L2 enum，
    # 不多不少，證明沒有漏掛的類
    assert len(by_l1) == 7
    assert sorted(c for opts in by_l1.values() for c in opts) == sorted(
        schema["properties"]["L2"]["enum"]
    )
    assert by_l1["其他"] == ["其他"]
    # 無代碼主題的 L1 值就是名稱本身，不得留下拼接的前導空格
    assert "現場履約問題" in by_l1 and " 現場履約問題" not in by_l1

    # 每個 L2 都掛在自己 L1 底下（抽一類驗證，避免只是形狀對但歸屬錯）
    assert "取消政策爭議（規則僵化或揭露不清）" in by_l1["[101] 訂單取消"]
    assert "取消政策爭議（規則僵化或揭露不清）" not in by_l1["[93] 訂單申請修改"]

    by_l2 = cascade["L3"]["options_by_parent"]
    assert by_l2["其他"] == ["其他"]
    for row in taxonomy["L2_entries"]:
        assert by_l2[row["name"]] == row["L3_options"]

    # L4 三分支：[93] 挑修改標的、跳出挑子型、其餘只剩 n/a 哨兵
    by_l2_l4 = cascade["L4"]["options_by_parent"]
    assert by_l2_l4["旅客/聯絡人資料修正"] == taxonomy["L4_options"]["modify_target"]
    assert by_l2_l4["其他"] == taxonomy["L4_options"]["oot_subtype"]
    assert by_l2_l4["憑證/取票未送達或催件"] == ["n/a"]


def test_defaults_payload_carries_cascade_for_review_controls() -> None:
    """人工評判的下拉靠 payload 的 output_cascade 收窄，缺了它 L2 會退回攤平的 24 類。"""
    payload = prompt_debug.defaults_payload()
    assert payload["output_cascade"]["L2"]["parent"] == "L1"
    assert payload["output_cascade"]["L3"]["parent"] == "L2"
    assert payload["output_cascade"]["L4"]["parent"] == "L2"


def test_default_prompt_taxonomy_matches_contract_ssot() -> None:
    """調試台默認口徑的 Prompt 內嵌分類庫，必須與契約 SSOT 是**同一版表**。

    2026-08-03 的事故就是這條沒被守住：Prompt 升到 260803 表、`after_sales_root_cause.json`
    還留在 260722，於是 Structured Outputs 把新表答案硬塞回舊 enum——模型被迫輸出
    `L2=取消政策本身僵化`＋`L3=用戶自填錯` 這種跨類組合，新欄位 `redirected_to_cancel_flag`
    還因 `additionalProperties: false` 被靜默丟掉。校驗只報得出「L3 不屬於該 L2」，看不出真因。
    """
    taxonomy = prompt_debug.load_taxonomy()
    embedded = _embedded_taxonomy(prompt_debug.defaults_payload()["system_prompt"])

    by_name = {entry["name"]: entry for entry in embedded["L2_entries"]}
    assert set(by_name) == {row["name"] for row in taxonomy["L2_entries"]}
    for row in taxonomy["L2_entries"]:
        assert by_name[row["name"]]["L1"] == prompt_debug._l1_value(row)
        assert by_name[row["name"]]["L3_options"] == row["L3_options"]
    assert embedded["L4_modify_target"] == taxonomy["L4_options"]["modify_target"]
    assert embedded["L4_oot_subtype"] == taxonomy["L4_options"]["oot_subtype"]


def test_slashes_inside_controlled_causes_are_not_split() -> None:
    taxonomy = prompt_debug.load_taxonomy()
    causes = {cause for category in taxonomy["L2_entries"] for cause in category["L3_options"]}
    assert "下單流程統編/抬頭欄位易漏填或誤填" in causes
    assert "代收轉付收據性質未於下單/商品頁說明" in causes
    assert "用戶對發票/收據/三聯式概念混淆" in causes
    assert "商品頁說明" not in causes


def test_taxonomy_drift_warning_fires_only_on_cross_table_prompts() -> None:
    """執行期防漂移：送出的 Prompt 內嵌分類庫與 SSOT 不同表就警示，同表與無內嵌區塊都放行。

    守門測試只管 repo 內的默認口徑；實際送出的可能是任一歷史草稿／正式版／頁面臨時貼的全文，
    這條是那些路徑的最後一道提示（不阻斷——判不判由人決定，但不能讓人以為結果可信）。
    """
    taxonomy = prompt_debug.load_taxonomy()
    warn = prompt_debug.taxonomy_drift_warning

    # 無內嵌區塊＝刻意的臨時實驗，不吵
    assert warn("隨手貼的實驗版 Prompt，沒有內嵌分類庫", taxonomy) == ""
    # 同表＝零警示（拿默認口徑那份真 Prompt 驗，不是自組的最小樣本）
    assert warn(prompt_debug.defaults_payload()["system_prompt"], taxonomy) == ""

    rows = taxonomy["L2_entries"]
    same_table = {"L2_entries": [{"name": r["name"], "L3_options": r["L3_options"]} for r in rows]}

    def wrap(payload: dict) -> str:
        return f"<taxonomy>\n{json.dumps(payload, ensure_ascii=False)}\n</taxonomy>"

    assert warn(wrap(same_table), taxonomy) == ""

    # 舊表殘留的類名（260722 的「取消政策本身僵化」）→ 兩個方向的差集都要點出來
    cross = {"L2_entries": [{"name": "取消政策本身僵化", "L3_options": ["其他"]}]}
    message = warn(wrap(cross), taxonomy)
    assert "不是同一版表" in message
    assert "取消政策本身僵化" in message
    assert "L2 SSOT 有、Prompt 沒有" in message.replace("個 ", "")

    # 類名對得上、L3 受控值被改過（最陰的一種：schema 照舊放行，判準卻不同）
    shifted = {
        "L2_entries": [
            {"name": r["name"], "L3_options": (["憑空多出來的值"] if i == 0 else r["L3_options"])}
            for i, r in enumerate(rows)
        ]
    }
    assert "L3 受控值不同" in warn(wrap(shifted), taxonomy)

    # 區塊在但 JSON 壞掉（快照被截斷）→ 明說無法比對，不靜默放行
    assert "不是合法 JSON" in warn('<taxonomy>\n{"L2_entries": [\n</taxonomy>', taxonomy)


def test_validate_result_accepts_controlled_non_oot() -> None:
    assert prompt_debug.validate_result(_base_result()) == []


def test_validate_result_enforces_summary_length_from_field_definition() -> None:
    issues = prompt_debug.validate_result(_base_result(summary="太短"))
    assert issues and issues[0].startswith("Schema summary:")


def test_validate_result_rejects_cross_l2_cause_and_l1() -> None:
    issues = prompt_debug.validate_result(_base_result(L1="[101] 訂單取消", L3="退款作業時程長"))
    assert "L1 必須是 [104] 訂單確認問題" in issues
    assert "L3 不屬於該 L2 的受控選項" in issues


def test_validate_result_accepts_oot_contract() -> None:
    """跳出：L1／L2／L3 三層皆「其他」，L4 改由跳出子型承接（260803 表起不再是 n/a）。"""
    value = _base_result(L2="其他", L1="其他", L3="其他", L4="售前_商品資訊詢問")
    assert prompt_debug.validate_result(value) == []

    assert "跳出的 L4 必須是跳出子型之一" in prompt_debug.validate_result(
        _base_result(L2="其他", L1="其他", L3="其他", L4="n/a")
    )
    assert "跳出的 L3 必須是 其他" in prompt_debug.validate_result(
        _base_result(L2="其他", L1="其他", L3="憑證送達延遲", L4="純技術操作")
    )


def test_validate_result_enforces_no_actionable_content_linkage() -> None:
    """no_actionable_content=true 必須連動跳出 ＋ keywords 清空 ＋ L4 為對話殘段子型。"""
    assert "no_actionable_content=true 時 L2 必須是 其他" in (
        prompt_debug.validate_result(_base_result(no_actionable_content=True))
    )
    assert "no_actionable_content=true 時 L4 必須是 對話殘段/無實質" in (
        prompt_debug.validate_result(
            _base_result(
                L2="其他",
                L1="其他",
                L3="其他",
                L4="純技術操作",
                no_actionable_content=True,
                keywords=[],
            )
        )
    )
    value = _base_result(
        L2="其他",
        L1="其他",
        L3="其他",
        L4="對話殘段/無實質",
        no_actionable_content=True,
        keywords=[],
    )
    assert prompt_debug.validate_result(value) == []


def test_validate_result_requires_l4_for_93() -> None:
    value = _base_result(
        L2="修改受限（規則不允許改）",
        L1="[93] 訂單申請修改",
        L3="規則不允許改",
    )
    assert "[93] L2 的 L4 必須是修改標的之一（不可為 n/a 或跳出子型）" in (
        prompt_debug.validate_result(value)
    )
    # 值域對但分支錯（拿跳出子型當修改標的）同樣要擋——schema 的 L4 enum 是三分支聯集，攔不住這個
    value["L4"] = "純技術操作"
    assert "[93] L2 的 L4 必須是修改標的之一（不可為 n/a 或跳出子型）" in (
        prompt_debug.validate_result(value)
    )
    value["L4"] = "改日期/時段/班次"
    assert prompt_debug.validate_result(value) == []


def test_validate_result_binds_redirected_to_cancel_flag_to_93() -> None:
    """R2「改與取消不互相吸收」的落地：被導向取消只可能發生在 [93]（自檢規則④）。"""
    message = "redirected_to_cancel_flag=true 時 L1 必須是 [93] 訂單申請修改"
    assert message in prompt_debug.validate_result(
        _base_result(redirected_to_cancel_flag=True)  # [104] 類
    )
    assert message in prompt_debug.validate_result(
        _base_result(
            L2="其他", L1="其他", L3="其他", L4="純技術操作", redirected_to_cancel_flag=True
        )
    )
    value = _base_result(
        L2="修改受限（規則不允許改）",
        L1="[93] 訂單申請修改",
        L3="規則不允許改",
        L4="改日期/時段/班次",
        redirected_to_cancel_flag=True,
    )
    assert prompt_debug.validate_result(value) == []


# ── Prompt 版本庫（草稿／正式版雙軌）────────────────────────────────────────────


def _wire_dirs(monkeypatch, tmp_path):
    """把兩區與 index 檔都導到 tmp；回 (草稿區, 正式版區)。"""
    drafts, releases = tmp_path / "drafts", tmp_path / "versions"
    drafts.mkdir()
    releases.mkdir()
    monkeypatch.setattr(versions, "DRAFTS_DIR", drafts)
    monkeypatch.setattr(versions, "RELEASES_DIR", releases)
    monkeypatch.setattr(versions, "INDEX_FILE", releases / "index.json")
    return drafts, releases


def test_repo_dirs_wired_and_active_release_resolvable() -> None:
    """repo 內兩區都必須解得出來（防搬目錄／改命名後靜默失效——2026-07-30 就出過一次）。"""
    assert versions.list_drafts(), "草稿區是空的：檔名需為 YYYY-MM-DD-HHMMSS.md"
    assert versions.active_release(), "正式版區沒有 active"
    assert versions.active_prompt().strip()


def test_latest_draft_is_newest_filename_not_mtime(monkeypatch, tmp_path) -> None:
    """最新草稿＝檔名時間戳最大者；先寫的舊名檔即使 mtime 較新也不該勝出。"""
    drafts, _ = _wire_dirs(monkeypatch, tmp_path)
    (drafts / "2026-07-27-185628.md").write_text("新版", encoding="utf-8")
    (drafts / "2026-01-01-090000.md").write_text("舊版", encoding="utf-8")
    (drafts / "CHANGELOG.md").write_text("不是版本檔", encoding="utf-8")

    assert versions.list_drafts() == ["2026-07-27-185628", "2026-01-01-090000"]
    assert versions.latest_draft() == "2026-07-27-185628"


def test_save_draft_does_not_change_online_prompt(monkeypatch, tmp_path) -> None:
    """**存草稿不動線上口徑**——這是雙軌制的核心不變式（舊設計是存檔即上線）。"""
    drafts, releases = _wire_dirs(monkeypatch, tmp_path)
    (drafts / "2026-01-01-090000.md").write_text("舊草稿\n", encoding="utf-8")
    (releases / "release-v1.md").write_text("線上版\n", encoding="utf-8")
    versions._write_index(
        {"schema": 1, "active_release": "release-v1", "releases": {}, "drafts": {}}
    )

    created = versions.save_draft("改過的 Prompt", note="試一下", author="a@b.c")
    assert created["created"] is True
    assert created["version"] > "2026-01-01-090000"
    assert versions.latest_draft() == created["version"]
    # 關鍵：線上口徑完全沒動
    assert versions.active_release() == "release-v1"
    assert versions.active_prompt().strip() == "線上版"
    # meta 進了 index
    meta = {m["version"]: m for m in versions.draft_meta()}
    assert meta[str(created["version"])]["note"] == "試一下"

    again = versions.save_draft("改過的 Prompt")
    assert again == {"version": created["version"], "created": False}
    assert len(versions.list_drafts()) == 2


def test_promote_switches_active_and_keeps_old_readable(monkeypatch, tmp_path) -> None:
    """升版＝草稿變 active 正式版；舊正式版仍可讀（不刪不覆寫）。"""
    drafts, releases = _wire_dirs(monkeypatch, tmp_path)
    (drafts / "2026-01-02-100000.md").write_text("候選內容\n", encoding="utf-8")
    (releases / "release-v1.md").write_text("舊線上版\n", encoding="utf-8")
    versions._write_index(
        {"schema": 1, "active_release": "release-v1", "releases": {}, "drafts": {}}
    )

    out = versions.promote("2026-01-02-100000", "release-v2", note="上線理由", author="a@b.c")
    assert out["previous_active"] == "release-v1"
    assert versions.active_release() == "release-v2"
    assert versions.active_prompt().strip() == "候選內容"
    assert versions.read_release("release-v1").strip() == "舊線上版"  # 舊版仍在

    by_name = {r["name"]: r for r in versions.list_releases()}
    assert by_name["release-v2"]["is_active"] is True
    assert by_name["release-v2"]["source_draft"] == "2026-01-02-100000"
    assert by_name["release-v2"]["note"] == "上線理由"
    assert by_name["release-v1"]["is_active"] is False


def test_promote_rejects_duplicate_name_and_missing_draft(monkeypatch, tmp_path) -> None:
    """正式版不覆寫（重名即拒）；來源草稿不存在則 FileNotFoundError。"""
    drafts, releases = _wire_dirs(monkeypatch, tmp_path)
    (drafts / "2026-01-02-100000.md").write_text("內容\n", encoding="utf-8")
    (releases / "release-v1.md").write_text("已存在\n", encoding="utf-8")

    try:
        versions.promote("2026-01-02-100000", "release-v1")
    except ValueError:
        pass
    else:
        raise AssertionError("重名應被拒")

    try:
        versions.promote("2026-09-09-090909", "release-v9")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("來源草稿不存在應拋 FileNotFoundError")


def test_resolve_defaults_to_release_and_flags_draft_only_when_allowed(
    monkeypatch, tmp_path
) -> None:
    """空字串＝當前正式版；草稿只在 allow_draft 時被認出（跑批不認）。"""
    drafts, releases = _wire_dirs(monkeypatch, tmp_path)
    (releases / "release-v1.md").write_text("線上版\n", encoding="utf-8")
    (drafts / "2026-01-01-090000.md").write_text("草稿內容\n", encoding="utf-8")
    versions._write_index(
        {"schema": 1, "active_release": "release-v1", "releases": {}, "drafts": {}}
    )

    assert versions.resolve("  ") == ("線上版\n", "release-v1", "release")
    assert versions.resolve("線上版") == ("線上版", "release-v1", "release")
    # 單次調試認草稿
    assert versions.resolve("草稿內容", allow_draft=True) == (
        "草稿內容",
        "2026-01-01-090000",
        "draft",
    )
    # 跑批不認草稿 → 降級成臨時編輯（靠 sha256 追）
    assert versions.resolve("草稿內容", allow_draft=False) == ("草稿內容", "", "")
    assert versions.resolve("臨時改一句", allow_draft=True) == ("臨時改一句", "", "")


def test_active_release_falls_back_when_index_lost(monkeypatch, tmp_path) -> None:
    """index 遺失/失效時 fail-soft：單支就用它、多支取 mtime 最新，不因缺 meta 讓版本讀不到。"""
    _, releases = _wire_dirs(monkeypatch, tmp_path)
    (releases / "release-v1.md").write_text("唯一一支\n", encoding="utf-8")
    assert versions.active_release() == "release-v1"  # 完全沒有 index.json

    # index 指向不存在的名字 → 同樣走 fallback，不拋錯
    versions._write_index(
        {"schema": 1, "active_release": "release-gone", "releases": {}, "drafts": {}}
    )
    assert versions.active_release() == "release-v1"

    # index 是壞 JSON → 也不該炸
    versions.INDEX_FILE.write_text("{壞掉的 json", encoding="utf-8")
    assert versions.active_release() == "release-v1"


def test_index_write_is_atomic(monkeypatch, tmp_path) -> None:
    """原子寫：寫完不留 .tmp，且內容可完整 parse（防每請求重讀撞上半寫入狀態）。"""
    _, releases = _wire_dirs(monkeypatch, tmp_path)
    versions._write_index({"schema": 1, "active_release": "x", "releases": {}, "drafts": {}})
    assert list(releases.glob("*.tmp")) == []
    assert versions._read_index()["active_release"] == "x"


def test_read_draft_and_release_reject_path_traversal(monkeypatch, tmp_path) -> None:
    """兩區各自守門：草稿只收時間戳、正式版只收英數與 . _ -，都不能是路徑。"""
    _wire_dirs(monkeypatch, tmp_path)
    for bad in ("../../etc/passwd", "v3", "2026-07-27"):
        try:
            versions.read_draft(bad)
        except ValueError:
            continue
        raise AssertionError(f"應拒絕非法草稿名：{bad!r}")

    for bad in ("../../etc/passwd", "a/b", "..", "release v1"):
        try:
            versions.read_release(bad)
        except ValueError:
            continue
        raise AssertionError(f"應拒絕非法正式版名：{bad!r}")


def test_stream_frames_uses_final_chunk_usage_for_same_call(monkeypatch) -> None:
    raw = json.dumps(_base_result(), ensure_ascii=False)
    usage = SimpleNamespace(
        prompt_tokens=1_000,
        completion_tokens=200,
        prompt_tokens_details=SimpleNamespace(cached_tokens=100),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=40),
    )
    chunks = iter(
        [
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=raw[:40]))], usage=None
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=raw[40:]))], usage=None
            ),
            SimpleNamespace(choices=[], usage=usage),
        ]
    )
    monkeypatch.setattr(prompt_debug.app_settings, "resolve_provider_token", lambda _: "sk-test")
    monkeypatch.setattr(prompt_debug, "_request_compat", lambda cfg, kwargs: (chunks, []))
    recorded: list[dict] = []
    monkeypatch.setattr(
        prompt_debug,
        "_record_usage_best_effort",
        lambda cfg, payload, job_id: recorded.append(payload),
    )

    frames = list(
        prompt_debug.stream_frames(
            "[USER] 尚未收到電子票",
            "只輸出 JSON",
            {
                "token": "sk-test",
                "base_url": "",
                "model": "gpt-5-mini",
                "temperature": None,
                "thinking": "off",
                "reasoning_effort": "minimal",
            },
        )
    )

    assert sum(frame.startswith("event: delta") for frame in frames) == 2
    result_frame = next(frame for frame in frames if frame.startswith("event: result"))
    assert '"valid": true' in result_frame
    usage_frame = next(frame for frame in frames if frame.startswith("event: usage"))
    assert '"prompt_tokens": 1000' in usage_frame
    assert '"completion_tokens": 200' in usage_frame
    assert '"reasoning_tokens": 40' in usage_frame
    assert recorded and recorded[0]["total_tokens"] == 1_200
    assert recorded[0]["cost_usd"] > 0


# ── _request_compat 相容降級階梯 ───────────────────────────────────────────────────────────
# 這條階梯先前零測試，卻同時服務調試台串流與跑批首筆探測（收斂後的形狀還會被
# _settle_request_shape 發給所有 worker）——降級判準寫錯的代價是整批走錯形狀。
def _bad_request(*, param=None, message="err"):
    """建帶 param 的 SDK BadRequestError（模擬相容端點對特定參數的 400）。"""
    import httpx
    from openai import BadRequestError

    req = httpx.Request("POST", "https://ark.example/api/v3/chat/completions")
    body = {"param": param} if param else {}
    return BadRequestError(message, response=httpx.Response(400, request=req), body=body or None)


def _ladder_client(monkeypatch, failures):
    """讓 _complete_effort_safe 依序拋出 failures 內的例外，之後成功；回傳每次收到的 kwargs 快照。"""
    from app.judge.llm import client

    seen: list[dict] = []
    pending = list(failures)

    def _fake(cfg, kwargs, ck, stage="", label=None):
        seen.append({k: (dict(v) if isinstance(v, dict) else v) for k, v in kwargs.items()})
        if pending:
            raise pending.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))])

    monkeypatch.setattr(client, "_complete_effort_safe", _fake)
    return seen


_ARK_CFG = {"base_url": "https://ark.ap-southeast.bytepluses.com/api/v3", "model": "seed-x"}
_SCHEMA_RF = {"type": "json_schema", "json_schema": {"name": "r", "strict": True, "schema": {}}}


def test_request_compat_drops_stream_options_first(monkeypatch) -> None:
    """端點不支援 stream_options → 只移除該參數，response_format 不受牽連。"""
    seen = _ladder_client(monkeypatch, [_bad_request(param="stream_options")])
    kwargs = {"model": "m", "messages": [], "stream": True, "stream_options": {}, **{}}
    _, warnings = prompt_debug._request_compat(_ARK_CFG, kwargs)
    assert "stream_options" not in kwargs and kwargs["stream"] is True
    assert len(seen) == 2 and "串流 usage" in warnings[0]


def test_request_compat_degrades_json_schema_to_json_object(monkeypatch) -> None:
    """strict json_schema 被拒 → 先試 Responses API，該階也失敗才降為 JSON mode。

    ⚠️ 需要**兩次**失敗才走到降級：Ark 這類 provider 有 `/responses` 端點，故階梯會先導流過去
    （那是唯一保住「API 端強制結構化輸出」的選項），只有它也被拒才放棄 strict。
    第一次失敗只會換 wire、不動 response_format。
    """
    seen = _ladder_client(
        monkeypatch,
        [
            _bad_request(
                param="response_format", message="json_schema"
            ),  # Chat 拒 strict → 轉 Responses
            _bad_request(
                param="response_format", message="json_schema"
            ),  # Responses 也拒 → 降 JSON mode
        ],
    )
    kwargs = {"model": "m", "messages": [], "response_format": dict(_SCHEMA_RF)}
    _, warnings = prompt_debug._request_compat(_ARK_CFG, kwargs)
    assert kwargs["response_format"] == {"type": "json_object"}
    assert len(seen) == 3
    # Responses 那條樂觀 warning 在該階失敗時已被收回，只留降級這條
    assert len(warnings) == 1 and "JSON mode" in warnings[0]


def test_request_compat_removes_response_format_when_json_object_also_rejected(monkeypatch) -> None:
    """json_object 也被拒 → 整個移除 response_format，改由 Prompt 約束（實測 Ark 新模型即此路徑）。

    完整階梯＝Chat strict 被拒 → Responses 也被拒 → 降 json_object → 連 json_object 也被拒 → 移除。
    """
    seen = _ladder_client(
        monkeypatch,
        [
            _bad_request(param="response_format", message="json_schema is not supported"),
            _bad_request(param="response_format", message="json_schema is not supported"),
            _bad_request(param="response_format", message="json_object is not supported"),
        ],
    )
    kwargs = {"model": "m", "messages": [], "response_format": dict(_SCHEMA_RF)}
    _, warnings = prompt_debug._request_compat(_ARK_CFG, kwargs)
    assert "response_format" not in kwargs
    assert len(seen) == 4 and len(warnings) == 2


def test_request_compat_does_not_retry_responses_stage(monkeypatch) -> None:
    """**回歸鎖**：Responses 階只試一次，不得與「清標記」互相彈跳把降級階梯餓死。

    2026-07-30 實測過的 bug：失敗時 `kwargs.pop(WIRE_API_KEY)` 清掉標記，而
    `can_use_responses_api()` 又以「標記不存在」為可導流條件 → 每輪都重新導流 Responses，
    5 輪預算全燒在同一階，`json_schema` 從未降級。修法＝用不會被清掉的區域旗標記錄「試過了」。
    """
    from app.judge.llm import responses_api

    seen = _ladder_client(
        monkeypatch,
        [_bad_request(param="response_format", message="json_schema")] * 3,
    )
    kwargs = {"model": "m", "messages": [], "response_format": dict(_SCHEMA_RF)}
    prompt_debug._request_compat(_ARK_CFG, kwargs)
    # 恰好一輪走 Responses；其餘都在 Chat Completions 上逐級放棄 strict
    on_responses = [s for s in seen if s.get(responses_api.WIRE_API_KEY)]
    assert len(on_responses) == 1, f"Responses 階被重試了 {len(on_responses)} 次"


def test_request_compat_openai_never_degrades(monkeypatch) -> None:
    """OpenAI 的 400 一律原樣拋——多為 prompt 過長/參數非法，降級只會掩蓋問題。"""
    import pytest
    from openai import BadRequestError

    seen = _ladder_client(monkeypatch, [_bad_request(param="response_format")])
    kwargs = {"model": "m", "messages": [], "response_format": dict(_SCHEMA_RF)}
    with pytest.raises(BadRequestError):
        prompt_debug._request_compat({"base_url": "https://api.openai.com/v1"}, kwargs)
    assert len(seen) == 1 and kwargs["response_format"] == _SCHEMA_RF  # 未被改寫
