"""settings.py 核心邏輯測試（連線層 + 具名模型配置庫）。

覆蓋：effective_llm_dict 的 area/overrides 組裝分支、model_capabilities_for provider 級能力、
極舊 config 結構的一次性遷移、模型配置庫的寫入邊界校驗、save_settings 機密不覆蓋既有語意。
"""

from __future__ import annotations

import pytest

from app.core import settings as app_settings


def _cfg(cfg_id: str, provider: str, **knobs) -> dict:
    """組一筆模型配置（`llm_model_configs` 的元素形狀）；未給的旋鈕走「不覆寫」預設。

    ⚠️ **沒有 name**——名稱由規格衍生（`derive_config_name`），入參給了也會被忽略。
    model 預設按 cfg_id 區分：唯一性改判「規格重複」後，多筆同 model 同旋鈕會互撞。
    """
    return {
        "id": cfg_id,
        "provider": provider,
        "model": knobs.pop("model", f"m-{cfg_id}"),
        "thinking": knobs.pop("thinking", "default"),
        "reasoning_effort": knobs.pop("reasoning_effort", "default"),
        "temperature": knobs.pop("temperature", None),
    }


def _with_required(*configs: dict) -> list[dict]:
    """把「功能區預設起點」補進待存清單。

    `llm_model_configs` 是整包替換。守衛只要求「清單不可為空」，但測試補齊出廠起點仍有意義：
    多數斷言依賴 `areaDefaults` 指向的配置確實在庫裡（否則回落會落到清單第一筆，斷言就假綠）。
    """
    return [*(dict(c) for c in app_settings._DEFAULT_MODEL_CONFIGS_VALIDATED), *configs]


def _area_pointing_to(monkeypatch, area: str, cfg: dict) -> dict:
    """讓某功能區的出廠預設指向 `cfg`；回傳可併進 settings dict 的片段。

    ⚠️ 這動的是**出廠**預設（`LLM_AREA_DEFAULT_CONFIG_IDS`，來自 llm_model.json），不是使用者
    在 UI 綁的那份（`settings.llm_area_configs`，優先權更高）。`area` 只在呼叫端**沒帶
    overrides** 時才生效（`current()` 的 stub 路徑、腳本直呼）。
    """
    monkeypatch.setitem(app_settings.LLM_AREA_DEFAULT_CONFIG_IDS, area, cfg["id"])
    return {"llm_model_configs": [cfg]}


# ── effective_llm_dict：area 預設 ──────────────────────────────────────────────────────────
def test_effective_llm_dict_uses_area_default_config(monkeypatch):
    """沒帶 overrides → 依該區出廠預設配置組出 flat dict（連線反查對應 provider）。"""
    cfg = _cfg("cfg-a", "openai", model="gpt-5.4-mini", thinking="enabled", reasoning_effort="high")
    s = {
        "llm_connections": {"openai": {"base_url": "https://api.openai.com/v1"}},
        "llm_tokens": {"openai": "sk-live"},
        **_area_pointing_to(monkeypatch, "prejudge", cfg),
    }
    eff = app_settings.effective_llm_dict(s, area="prejudge")
    assert eff["provider"] == "openai"
    assert eff["model"] == "gpt-5.4-mini"
    assert eff["base_url"] == "https://api.openai.com/v1"
    assert eff["api_token"] == "sk-live"
    assert eff["thinking"] == "enabled"
    assert eff["reasoning_effort"] == "high"


def test_effective_llm_dict_falls_back_to_stub_when_area_empty():
    """查無 area 默認（未設或該區無資料）→ 回退 _DEFAULT_LLM（stub，無 token）。

    base_url 即使在完全未配置時也補上 openai 官方端點（2026-07-28 起 effective_llm_dict 保證非空）；
    stub 判定只看 token（client.is_stub → not has_key），不受此影響。
    """
    eff = app_settings.effective_llm_dict(app_settings._blank_settings(), area="sandbox")
    assert eff["provider"] == "openai"
    assert eff["api_token"] == ""
    assert eff["base_url"] == app_settings.default_base_url_for("openai")


def test_effective_llm_dict_none_area_falls_back_to_default_knobs_not_other_areas(monkeypatch):
    """area 缺省（None）→ 旋鈕回退 _DEFAULT_LLM（不誤用任一已存功能區的默認旋鈕）。

    連線解析仍獨立於旋鈕來源：_DEFAULT_LLM 的 provider（openai）若剛好有連線，token 仍會解出——
    這是預期行為（連線查找只認「當下決定用哪個 provider」，不論該 provider 是從 area 默認或
    _DEFAULT_LLM 來的）。
    """
    s = {
        "llm_connections": {"openai": {"base_url": "https://api.openai.com/v1"}},
        "llm_tokens": {"openai": "sk-live"},
        **_area_pointing_to(monkeypatch, "prejudge", _cfg("cfg-a", "openai", model="gpt-5.4-mini")),
    }
    eff = app_settings.effective_llm_dict(s)
    assert eff["model"] == app_settings._DEFAULT_LLM["model"]  # 不是 prejudge 的 gpt-5.4-mini
    assert eff["api_token"] == "sk-live"  # _DEFAULT_LLM provider=openai 剛好有連線 → 仍解出


# ── effective_llm_dict：overrides ──────────────────────────────────────────────────────────
def test_effective_llm_dict_overrides_apply_non_none_fields(monkeypatch):
    """overrides 的 model/thinking/reasoning_effort 非 None 值覆寫該區出廠預設配置。"""
    cfg = _cfg(
        "cfg-a",
        "openai",
        model="gpt-5-mini",
        thinking="disabled",
        reasoning_effort="medium",
        temperature=0.7,
    )
    s = {
        "llm_connections": {"openai": {"base_url": ""}},
        "llm_tokens": {"openai": "sk-live"},
        **_area_pointing_to(monkeypatch, "prompt_debug", cfg),
    }
    eff = app_settings.effective_llm_dict(
        s, area="prompt_debug", overrides={"model": "gpt-5.4-mini", "thinking": "enabled"}
    )
    assert eff["model"] == "gpt-5.4-mini"
    assert eff["thinking"] == "enabled"
    assert eff["reasoning_effort"] == "medium"  # 未覆寫沿用 area 默認
    assert eff["temperature"] == 0.7  # 未在 overrides key 中 → 不動


def test_effective_llm_dict_temperature_none_override_clears_saved_value(monkeypatch):
    """temperature 的 None 覆寫有明確語意（本次改用 API 預設），需能清掉配置裡的數值。"""
    cfg = _cfg(
        "cfg-a",
        "openai",
        model="gpt-5-mini",
        thinking="enabled",
        reasoning_effort="medium",
        temperature=0.7,
    )
    s = {
        "llm_connections": {"openai": {"base_url": ""}},
        "llm_tokens": {"openai": "sk-live"},
        **_area_pointing_to(monkeypatch, "prompt_debug", cfg),
    }
    eff = app_settings.effective_llm_dict(
        s, area="prompt_debug", overrides={"model": "gpt-5.4-mini", "temperature": None}
    )
    assert eff["model"] == "gpt-5.4-mini"
    assert eff["temperature"] is None
    assert eff["thinking"] == "enabled"  # 不在 overrides 內的欄位不受影響


def test_effective_llm_dict_overrides_provider_switches_connection(monkeypatch):
    """overrides.provider 可切換本次連線（非僅切旋鈕），token/base_url 隨新 provider 反查。"""
    cfg = _cfg("cfg-a", "openai", model="gpt-5-mini", thinking="disabled")
    s = {
        "llm_connections": {
            "openai": {"base_url": "https://api.openai.com/v1"},
            "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai"},
        },
        "llm_tokens": {"openai": "sk-openai", "gemini": "sk-gemini"},
        **_area_pointing_to(monkeypatch, "sandbox", cfg),
    }
    eff = app_settings.effective_llm_dict(
        s, area="sandbox", overrides={"provider": "gemini", "model": "gemini-3.5-flash"}
    )
    assert eff["provider"] == "gemini"
    assert eff["api_token"] == "sk-gemini"
    assert eff["base_url"].startswith("https://generativelanguage")
    assert eff["model"] == "gemini-3.5-flash"


def test_effective_llm_dict_fills_provider_default_base_url_when_connection_blank(monkeypatch):
    """連線沒填 base_url（或存過空字串）→ 補該 provider 官方端點，不得落回 OpenAI。

    回歸自 2026-07-28：空值下游被 `or "https://api.openai.com/v1"` 吃掉，Gemini/ByteDance
    拿自家 token 打 OpenAI 端點整批回 401，錯誤訊息卻指向 OpenAI。
    """
    gem = _cfg("cfg-gem", "gemini", model="gemini-3.5-flash")
    ark = _cfg("cfg-ark", "bytedance", model="seed-2-0-lite-260228")
    monkeypatch.setitem(app_settings.LLM_AREA_DEFAULT_CONFIG_IDS, "prejudge", "cfg-gem")
    monkeypatch.setitem(app_settings.LLM_AREA_DEFAULT_CONFIG_IDS, "sandbox", "cfg-ark")
    s = {
        "llm_connections": {"gemini": {"base_url": ""}, "bytedance": {}},
        "llm_model_configs": [gem, ark],
    }
    assert app_settings.effective_llm_dict(s, area="prejudge")["base_url"].startswith(
        "https://generativelanguage"
    )
    assert "bytepluses" in app_settings.effective_llm_dict(s, area="sandbox")["base_url"]


def test_default_base_url_for_matches_provider_id_for_roundtrip():
    """default_base_url_for 與 provider_id_for 互為反向；未知 id 回退 openai 端點。"""
    for pid in ("openai", "gemini", "bytedance"):
        assert app_settings.provider_id_for(app_settings.default_base_url_for(pid)) == pid
    assert app_settings.default_base_url_for("nope") == app_settings.default_base_url_for("openai")


# ── model_capabilities_for ─────────────────────────────────────────────────────────────────
def test_model_capabilities_openai_locks_temperature_when_thinking():
    cap = app_settings.model_capabilities_for("gpt-5.4-mini")
    assert cap["temperatureLockedWhenThinking"] is True
    assert cap["lockedTemperatureValue"] == 1


def test_model_capabilities_gemini_does_not_lock_temperature():
    cap = app_settings.model_capabilities_for("gemini-3.5-flash")
    assert cap["temperatureLockedWhenThinking"] is False


def test_model_capabilities_unknown_model_falls_back_to_openai_default():
    """自訂/未知 model（不在任何 provider defaultModels 內）→ 回退 openai 級預設。"""
    cap = app_settings.model_capabilities_for("some-custom-finetune")
    assert cap["temperatureLockedWhenThinking"] is True


# ── 極舊多套 config 結構 → 連線 + 具名配置庫 遷移 ────────────────────────────────────────
def test_migrate_legacy_configs_llm(temp_db):
    """極舊 llm_configs[]（active 優先）→ llm_connections + active 那套收成一筆具名配置。"""
    from app.core import db

    legacy = {
        "llm_configs": [
            {
                "id": "cfg-openai",
                "provider": "openai",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-5-mini",
                "thinking": "off",
                "reasoning_effort": "medium",
                "temperature": None,
            },
            {
                "id": "cfg-gemini",
                "provider": "gemini",
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                "model": "gemini-3.5-flash",
                "thinking": "on",
                "reasoning_effort": "high",
                "temperature": None,
            },
        ],
        "active_llm_config_id": "cfg-gemini",
        "llm_tokens": {"cfg-openai": "sk-openai", "cfg-gemini": "sk-gemini"},
    }
    db.save_settings_row(app_settings.GLOBAL_SETTINGS_KEY, legacy)

    loaded = app_settings.load_settings()
    assert loaded["llm_connections"]["openai"]["base_url"] == "https://api.openai.com/v1"
    assert loaded["llm_connections"]["gemini"]["base_url"].startswith("https://generativelanguage")
    assert loaded["llm_tokens"]["openai"] == "sk-openai"
    assert loaded["llm_tokens"]["gemini"] == "sk-gemini"
    # active（cfg-gemini）那套旋鈕收成**一筆**配置，**append 在預設配置之後**——直接取代會把
    # 功能區預設起點整組蓋掉，下一次任何儲存都會被「預設起點不可刪」擋成 400。
    configs = loaded["llm_model_configs"]
    required = {v for v in app_settings.LLM_AREA_DEFAULT_CONFIG_IDS.values() if v}
    assert required <= {c["id"] for c in configs}, "預設起點必須保留"
    # 排除**全部**預設配置——預設裡也有一筆 gemini-3.5-flash（medium），遷移進來的是 high，
    # 兩者規格不同故並存；只用 model 名（或只排除 required）都會抓到前者。
    default_ids = {c["id"] for c in app_settings._DEFAULT_MODEL_CONFIGS_VALIDATED}
    migrated = next(
        c for c in configs if c["model"] == "gemini-3.5-flash" and c["id"] not in default_ids
    )
    assert migrated["provider"] == "gemini"
    assert migrated["reasoning_effort"] == "high"
    # 舊 config 的 on/off 值域在搬遷時翻譯成當前值域，不讓爛值進新結構
    # （gemini 是 effortOnly，thinking 隨即被 R1 折成 default——這正是「名稱不騙人」的效果）
    assert migrated["thinking"] == "default"
    assert "name" not in migrated, "name 不落庫，由讀取端衍生"
    assert "llm_area_defaults" not in loaded, "退役的 key 不得復活"
    # 遷移後立即持久化為新 shape（重讀一次不再觸發遷移分支）
    raw_row = db.load_settings_row(app_settings.GLOBAL_SETTINGS_KEY)
    assert "llm_connections" in raw_row


def test_migrate_legacy_configs_qc(temp_db):
    """A schema 改造前的 qc_configs[]（同 env 多套時 active 優先）→ qc_connections（keyed by env）。"""
    from app.core import db

    legacy = {
        "qc_configs": [
            {"id": "qc-a", "env": "production", "host": "a.example", "port": 5432, "user": "u1"},
            {"id": "qc-b", "env": "production", "host": "b.example", "port": 5432, "user": "u2"},
        ],
        "active_qc_config_id": "qc-b",
        "qc_passwords": {"qc-a": "pw-a", "qc-b": "pw-b"},
    }
    db.save_settings_row(app_settings.GLOBAL_SETTINGS_KEY, legacy)

    loaded = app_settings.load_settings()
    assert loaded["qc_connections"]["production"]["host"] == "b.example"  # active 優先
    assert loaded["qc_passwords"]["production"] == "pw-b"


# ── save_settings：機密空/遮罩不覆蓋既有 ──────────────────────────────────────────────────
def test_save_settings_blank_token_does_not_clear_existing(temp_db):
    app_settings.save_settings(
        {
            "llm_connections": {"openai": {"base_url": ""}},
            "llm_tokens": {"openai": "sk-real"},
        }
    )
    # 再次 save 帶空字串 token（表單未改動送出的常見情境）→ 不覆蓋既有真值
    app_settings.save_settings({"llm_tokens": {"openai": ""}})
    loaded = app_settings.load_settings()
    assert loaded["llm_tokens"]["openai"] == "sk-real"


def test_save_settings_model_configs_roundtrip(temp_db):
    """整包存取往返：欄位收斂到白名單（**不含 name**）、字串 trim、id 缺漏補 uuid。"""
    app_settings.save_settings(
        {
            "llm_model_configs": _with_required(
                {
                    "provider": "openai",
                    "model": " gpt-5.4 ",
                    "thinking": "default",
                    "reasoning_effort": "xhigh",
                    "temperature": None,
                }
            )
        }
    )
    stored = app_settings.load_settings()["llm_model_configs"]
    mine = next(c for c in stored if c["model"] == "gpt-5.4")
    assert mine["model"] == "gpt-5.4"  # trim
    assert mine["id"], "id 缺漏時後端補 uuid"
    assert "name" not in mine, "name 不落庫——它是讀取端由規格衍生的投影欄"


def test_all_model_configs_projects_derived_name(temp_db):
    """`name` 由 `all_model_configs()` 衍生補上；DB 裡沒有這個欄位。"""
    cur = app_settings.load_settings()
    assert all("name" not in c for c in cur["llm_model_configs"])
    projected = app_settings.all_model_configs(cur)
    assert projected, "全新環境也該有預設配置（_blank_settings 種入）"
    for c in projected:
        assert c["name"] == app_settings.derive_config_name(c)


def test_save_settings_model_configs_is_whole_list_replace(temp_db):
    """整包替換語義：少送一筆＝刪除該筆（前端持有完整清單，增刪改都是對整份操作）。"""
    app_settings.save_settings(
        {"llm_model_configs": _with_required(_cfg("cfg-1", "openai"), _cfg("cfg-2", "bytedance"))}
    )
    app_settings.save_settings({"llm_model_configs": _with_required(_cfg("cfg-1", "openai"))})
    ids = {c["id"] for c in app_settings.load_settings()["llm_model_configs"]}
    assert "cfg-1" in ids
    assert "cfg-2" not in ids


def test_model_configs_of_different_providers_coexist(temp_db):
    """不同供應商的配置各自獨立共存——扁平陣列 + 每筆自帶 provider，結構上不可能互相覆蓋。"""
    app_settings.save_settings(
        {
            "llm_model_configs": _with_required(
                _cfg("cfg-o", "openai", model="gpt-5.4-mini", reasoning_effort="high"),
                _cfg("cfg-b", "bytedance", model="seed-2-0-lite-260428", thinking="enabled"),
            )
        }
    )
    by_id = {c["id"]: c for c in app_settings.load_settings()["llm_model_configs"]}
    assert by_id["cfg-o"]["reasoning_effort"] == "high"
    assert by_id["cfg-b"]["thinking"] == "enabled"


# ── 衍生命名（黃金清單與前端 modelConfigName.util.test.ts 逐字對齊）────────────────────
_GOLDEN_NAMES: list[tuple[dict, str]] = [
    (
        {"provider": "openai", "model": "gpt-5.4-mini", "reasoning_effort": "medium"},
        "OpenAI · gpt-5.4-mini · medium",
    ),
    (
        {"provider": "openai", "model": "gpt-5.5", "reasoning_effort": "high"},
        "OpenAI · gpt-5.5 · high",
    ),
    (
        {"provider": "gemini", "model": "gemini-3.5-flash", "reasoning_effort": "medium"},
        "Gemini · gemini-3.5-flash · medium",
    ),
    (
        {"provider": "bytedance", "model": "seed-2-0-lite-260228", "reasoning_effort": "medium"},
        "ByteDance · seed-2-0-lite-260228 · medium",
    ),
    (
        {
            "provider": "bytedance",
            "model": "seed-2-0-lite-260428",
            "thinking": "enabled",
            "reasoning_effort": "high",
        },
        "ByteDance · seed-2-0-lite-260428 · thinking:enabled · high",
    ),
    # ⬇️ thinking=enabled 被 R1 折掉（OpenAI 執行層根本不讀它）
    # ⬇️ thinking 被 R1 折掉、temperature 被 R3 折掉（該組合下 API 只接受預設值，送了也沒用）
    #    → 與第 1 筆規格完全相同，是**同一個配置**，故本清單不重複列出（見 fold 專屬測試）
    (
        {
            "provider": "bytedance",
            "model": "seed-2-0-lite-260428",
            "thinking": "enabled",
            "reasoning_effort": "medium",
            "temperature": 1.0,
        },
        "ByteDance · seed-2-0-lite-260428 · thinking:enabled · medium · temp:1",
    ),
    (
        {
            "provider": "openai",
            "model": "gpt-5.4-mini",
            "thinking": "enabled",
            "reasoning_effort": "xhigh",
            "temperature": 1.0,
        },
        "OpenAI · gpt-5.4-mini · xhigh",
    ),
    (
        {"provider": "gemini", "model": "gemini-2.5-flash", "reasoning_effort": "medium"},
        "Gemini · gemini-2.5-flash · medium",
    ),
]


@pytest.mark.parametrize(("cfg", "expected"), _GOLDEN_NAMES)
def test_derive_config_name_golden(cfg: dict, expected: str):
    """黃金清單：與前端 `modelConfigName.util.test.ts` 逐字相同，任一端改規則就有一邊轉紅。

    名稱是使用者辨識配置的唯一依據、也是跑批 manifest 的追溯欄位，前後端漂移會讓「同一筆配置
    在設定面板叫 A、在跑批紀錄叫 B」。
    """
    assert app_settings.derive_config_name(cfg) == expected


def test_derive_config_name_golden_all_distinct():
    """全部互異——名稱即身分，撞名代表去重失效。"""
    names = [app_settings.derive_config_name(c) for c, _ in _GOLDEN_NAMES]
    assert len(set(names)) == len(names)


def test_spec_key_r1_folds_thinking_for_effort_only_providers():
    """R1：openai/gemini 的 thinking 一律折成 default（執行層 `_reasoning_kwargs` 根本不讀它）。"""
    assert (
        app_settings.spec_key(
            {"provider": "openai", "model": "gpt-5.4-mini", "thinking": "enabled"}
        )[2]
        == "default"
    )
    assert (
        app_settings.spec_key(
            {"provider": "gemini", "model": "gemini-3.5-flash", "thinking": "disabled"}
        )[2]
        == "default"
    )


def test_spec_key_r2_folds_effort_when_ark_thinking_off_or_auto():
    """R2：nativeSwitch 在 thinking=disabled/auto 下 effort 折成 default（Ark 併送會 400）。"""
    base = {"provider": "bytedance", "model": "seed-2-0-lite-260228", "reasoning_effort": "high"}
    assert app_settings.spec_key({**base, "thinking": "disabled"})[3] == "default"
    assert app_settings.spec_key({**base, "thinking": "auto"})[3] == "default"
    assert app_settings.spec_key({**base, "thinking": "enabled"})[3] == "high"  # enabled 才保留


def test_spec_key_uses_provider_axis_not_model_lookup():
    """自訂／未登記 model 名下，能力判定仍以 **provider** 為軸，不由 model 反查。

    反查落空會靜默回退 openai 能力表 → ByteDance 配置被誤判 effortOnly → thinking 被折掉，
    但執行層（同樣看 provider）其實會送它 → 配置名與實跑不符。
    """
    assert (
        app_settings.spec_key(
            {"provider": "bytedance", "model": "my-custom-gw", "thinking": "enabled"}
        )[2]
        == "enabled"
    )


def test_spec_key_r3_rounds_temperature():
    """R3 的 round(2)：UI step 是 0.1，兩位小數無損，且讓規格鍵與名稱的數字投影一一對應。

    這裡用 `effort=default`（推理未生效）→ gpt-5.4 此時可自訂溫度，值不會被折掉。
    """
    at = lambda t: app_settings.spec_key(  # noqa: E731
        {"provider": "openai", "model": "gpt-5.4", "temperature": t}
    )[4]
    assert at(1) == 1.0
    assert at(0.1000001) == 0.1
    assert at(None) is None


def test_spec_key_r3_folds_temperature_only_when_inert():
    """R3 折疊：**送了也沒用**才折，會被真的採用就保留。

    依 2026-07-31 逐 model 實測（144 次真實 API 呼叫）：
    - `gpt-5.4-mini` + effort=medium → API 只接受預設溫度 → 折
    - `gpt-5.4-mini` + effort=default（未推理）→ 可自訂 → 不折
    - `gpt-5.5` → 任何狀態都只接受預設 → 折
    - ByteDance `seed-*` → 實測受理 0.3 → **不折**（折了就是改變送出內容）
    """
    key = lambda **kw: app_settings.spec_key(  # noqa: E731
        {"thinking": "default", "reasoning_effort": "default", **kw}
    )[4]
    assert (
        key(provider="openai", model="gpt-5.4-mini", reasoning_effort="medium", temperature=1)
        is None
    )
    assert key(provider="openai", model="gpt-5.4-mini", temperature=0.3) == 0.3
    assert key(provider="openai", model="gpt-5.5", temperature=1) is None
    assert (
        key(provider="bytedance", model="seed-2-0-lite-260228", thinking="enabled", temperature=0.3)
        == 0.3
    )


def test_spec_key_r3_fold_makes_functionally_identical_configs_collide():
    """折疊的目的：實際送出內容相同的兩筆，規格鍵必須相同（否則會並存為兩列且名字暗示假差異）。"""
    plain = {"provider": "openai", "model": "gpt-5.4-mini", "reasoning_effort": "medium"}
    with_temp = {**plain, "temperature": 1.0}
    assert app_settings.spec_key(plain) == app_settings.spec_key(with_temp)


def test_derive_config_name_keeps_temperature_zero():
    """temperature=0 是合法溫度，不得被 falsy 判斷當成「未設定」而從名稱消失。"""
    assert (
        app_settings.derive_config_name(
            {"provider": "openai", "model": "gpt-5.4", "temperature": 0}
        )
        == "OpenAI · gpt-5.4 · temp:0"
    )


def test_derive_config_name_tolerates_legacy_thinking_value():
    """datapack 匯入的舊 blob 可能還帶著已退役的 on/off——不得讓整個 GET /api/settings 500。"""
    name = app_settings.derive_config_name(
        {"provider": "bytedance", "model": "seed-2-0-lite-260228", "thinking": "on"}
    )
    assert "on" in name  # 查無標籤時原樣顯示，不拋 KeyError


# ── 模型配置寫入邊界校驗 ──────────────────────────────────────────────────────────────
def test_model_config_rejects_duplicate_spec(temp_db):
    """規格重複 → 拒收，訊息點名撞到的是哪一筆（名稱即規格，「叫什麼」就回答了「跟誰撞」）。"""
    dup = _cfg("cfg-1", "openai", model="gpt-5.4", reasoning_effort="high")
    with pytest.raises(ValueError, match="該配置已存在"):
        app_settings.save_settings(
            {"llm_model_configs": _with_required(dup, {**dup, "id": "cfg-2"})}
        )


def test_model_config_duplicate_spec_ignores_inert_knob_difference(temp_db):
    """只差一個**不生效**的旋鈕 → 仍是同一筆配置（折疊後規格相同），拒收。

    這正是折疊的目的：對 OpenAI 而言 thinking 根本不送，兩筆的實際 wire payload 一模一樣。
    """
    a = _cfg("cfg-1", "openai", model="gpt-5.4", reasoning_effort="high", thinking="enabled")
    b = _cfg("cfg-2", "openai", model="gpt-5.4", reasoning_effort="high", thinking="disabled")
    with pytest.raises(ValueError, match="該配置已存在"):
        app_settings.save_settings({"llm_model_configs": _with_required(a, b)})


def test_model_config_rejects_display_name_collision(temp_db):
    """規格不同但**顯示名相同** → 也拒收：下游（跑批分欄、Vue :key）以名稱為鍵，無從分辨。"""
    a = _cfg("cfg-1", "openai", model="gpt-5.4", reasoning_effort="high")
    b = _cfg("cfg-2", "openai", model="GPT-5.4", reasoning_effort="high")  # 只差大小寫
    with pytest.raises(ValueError, match="該配置已存在"):
        app_settings.save_settings({"llm_model_configs": _with_required(a, b)})


def test_model_config_rejects_unknown_provider_and_blank_model(temp_db):
    """供應商未登記 / model 空 → 拒收；訊息用 provider/model 定位（UI 是分頁手風琴，看不到序號）。"""
    with pytest.raises(ValueError, match="未登記"):
        app_settings.save_settings(
            {"llm_model_configs": _with_required({"provider": "nope", "model": "m"})}
        )
    with pytest.raises(ValueError, match="未指定 model"):
        app_settings.save_settings(
            {"llm_model_configs": _with_required({"provider": "openai", "model": "  "})}
        )


def test_model_config_rejects_out_of_domain_knobs(temp_db):
    """thinking / reasoning_effort 超出 SSOT 值域 → 寫入邊界直接擋。

    回歸自 2026-07-30：寫入端不驗值域時，'on' 這種舊值域會先進庫，再一路存活到前端原樣回送
    overrides，被 API 入口 validator 擋下 → 整條初判分類 422。擋在入口，爛值就進不去。
    """
    with pytest.raises(ValueError, match="thinking"):
        app_settings.save_settings(
            {"llm_model_configs": _with_required(_cfg("cfg-1", "bytedance", thinking="on"))}
        )
    with pytest.raises(ValueError, match="reasoning_effort"):
        app_settings.save_settings(
            {"llm_model_configs": _with_required(_cfg("cfg-1", "openai", reasoning_effort="turbo"))}
        )


def test_model_config_rejects_non_finite_or_negative_temperature(temp_db):
    """NaN 通過 float() 後會讓「規格已存在」永不命中（NaN != NaN）→ 可無限存入同規格列，必須擋。"""
    for bad in ("hot", float("nan"), float("inf"), -1):
        with pytest.raises(ValueError, match="temperature"):
            app_settings.save_settings(
                {"llm_model_configs": _with_required(_cfg("cfg-1", "openai", temperature=bad))}
            )


def test_model_config_folds_inert_knobs_before_storing(temp_db):
    """落庫的是**折疊後**的值——庫內值 ≡ 規格鍵 ≡ 衍生名，不會「存的跟顯示的不一樣」。"""
    app_settings.save_settings(
        {
            "llm_model_configs": _with_required(
                _cfg("cfg-1", "openai", model="gpt-5.4", thinking="enabled")
            )
        }
    )
    stored = next(
        c for c in app_settings.load_settings()["llm_model_configs"] if c["id"] == "cfg-1"
    )
    assert stored["thinking"] == "default", "OpenAI 不讀 thinking，落庫前就該折掉"


# ── 清單不可為空（唯一必要的不變式）────────────────────────────────────────────────
def test_cannot_delete_all_configs(temp_db):
    """清空整份清單 → 拒收：所有功能區都會沒有配置可用，前端執行按鈕全鎖、只能先回設定面板新增。"""
    with pytest.raises(ValueError, match="至少要保留一筆"):
        app_settings.save_settings({"llm_model_configs": []})


def test_can_delete_area_default_config(temp_db):
    """`areaDefaults` 指向的配置**可以刪**——那只是「還沒選過時的起點」，刪了會回落清單第一筆。

    刻意不擋：回落是降級不是損壞，且下拉一直顯示當前用哪一筆（可見、非靜默）；後果由前端刪除
    確認框告知（會列出受影響功能區與改用哪一筆），不是靠禁止。擋住只會製造「用不到卻刪不掉」的摩擦。
    """
    required = {v for v in app_settings.LLM_AREA_DEFAULT_CONFIG_IDS.values() if v}
    keep = [
        dict(c) for c in app_settings._DEFAULT_MODEL_CONFIGS_VALIDATED if c["id"] not in required
    ]
    assert keep, "測試前提：預設配置中要有非 areaDefaults 指向的項目"
    app_settings.save_settings({"llm_model_configs": keep})
    left = {c["id"] for c in app_settings.load_settings()["llm_model_configs"]}
    assert not (required & left), "areaDefaults 指向的配置應可被刪除"


def test_area_default_deleted_falls_back_without_error(temp_db):
    """該區預設起點被刪 → `area_default_knobs` 回退 _DEFAULT_LLM，不拋錯（三級回落的後端側）。"""
    keep = [
        dict(c)
        for c in app_settings._DEFAULT_MODEL_CONFIGS_VALIDATED
        if c["id"] not in {v for v in app_settings.LLM_AREA_DEFAULT_CONFIG_IDS.values() if v}
    ]
    app_settings.save_settings({"llm_model_configs": keep})
    knobs = app_settings.area_default_knobs(app_settings.load_settings(), "prejudge")
    assert knobs["model"] == app_settings._DEFAULT_LLM["model"]


# ── 預設配置與匯入修復 ────────────────────────────────────────────────────────────────
def test_blank_settings_seeds_default_configs():
    """全新環境一開機就有得選（免 bootstrap）；預設內容在 import 期已驗過。"""
    blank = app_settings._blank_settings()
    assert blank["llm_model_configs"], "預設配置未種入 → 全新環境下拉會是空的"
    required = {v for v in app_settings.LLM_AREA_DEFAULT_CONFIG_IDS.values() if v}
    assert required <= {c["id"] for c in blank["llm_model_configs"]}


def test_repair_model_configs_dedupes_and_restores_required(temp_db):
    """datapack 匯入路徑：含重複規格／缺預設起點的舊 blob → 修成合法形狀且不拋錯。

    不修的話，匯入後前端每次儲存（一律送整份清單）都會 400，訊息還指向使用者沒碰過的那筆。
    """
    dup = _cfg("old-1", "openai", model="gpt-5.4", reasoning_effort="high")
    fixed = app_settings.repair_model_configs([dup, {**dup, "id": "old-2"}, {"provider": "x"}])
    required = {v for v in app_settings.LLM_AREA_DEFAULT_CONFIG_IDS.values() if v}
    assert required <= {c["id"] for c in fixed}, "預設起點必須補回"
    assert sum(1 for c in fixed if c["model"] == "gpt-5.4") == 1, "重複規格只留一筆"
    assert all(c.get("provider") != "x" for c in fixed), "壞列直接丟棄"
    app_settings._validate_model_configs(fixed)  # 修完必須過得了寫入邊界


def test_area_default_knobs_falls_back_when_config_missing(temp_db, monkeypatch):
    """該區出廠預設指向的配置查無（配置檔改過）→ 回退 _DEFAULT_LLM，不炸。"""
    monkeypatch.setitem(app_settings.LLM_AREA_DEFAULT_CONFIG_IDS, "prejudge", "cfg-does-not-exist")
    knobs = app_settings.area_default_knobs(app_settings._blank_settings(), "prejudge")
    assert knobs["model"] == app_settings._DEFAULT_LLM["model"]


def test_legacy_thinking_map_covers_exactly_the_retired_domain():
    """翻譯表值域自我一致：來源皆為已退役字面、目標皆為當前合法值。"""
    assert set(app_settings._LEGACY_THINKING_MODES) == {"on", "off"}
    for stale, canonical in app_settings._LEGACY_THINKING_MODES.items():
        assert stale not in app_settings.LLM_THINKING_MODES
        assert canonical in app_settings.LLM_THINKING_MODES


def test_sanitize_drops_orphan_tokens_for_removed_connection(temp_db):
    """save 時整包替換 llm_connections 不含某 provider → 該 provider 的孤立 token 一併清除。"""
    app_settings.save_settings(
        {
            "llm_connections": {"openai": {"base_url": ""}, "gemini": {"base_url": ""}},
            "llm_tokens": {"openai": "sk-a", "gemini": "sk-b"},
        }
    )
    app_settings.save_settings({"llm_connections": {"openai": {"base_url": ""}}})
    loaded = app_settings.load_settings()
    assert "gemini" not in loaded["llm_tokens"]
    assert loaded["llm_tokens"]["openai"] == "sk-a"


# ── 旋鈕值域 SSOT（API 契約與 llm_model.json 同源）──────────────────────────────────────────
def test_thinking_modes_ssot_matches_execution_layer_native_enum():
    """thinking 值域＝Ark 原生三態，與 client._reasoning_kwargs 實際認得的字面一致。

    回歸自 2026-07-28：API 層 `LlmOverridesIn.thinking` 曾寫死舊值域 Literal["default","on","off"]，
    與執行層只認 enabled/disabled/auto 不符 → ByteDance 跑批一律 422（前端送 'enabled' 被擋在
    進入執行層之前），且 on/off 即使放行在執行層也是死值（既不設 extra_body 也不算 disabled）。
    """
    assert set(app_settings.LLM_THINKING_MODES) == {"enabled", "disabled", "auto"}
    assert "on" not in app_settings.LLM_THINKING_MODES
    assert "off" not in app_settings.LLM_THINKING_MODES
    # "default"＝不覆寫的元值，不該混進供應商參數值域
    assert "default" not in app_settings.LLM_THINKING_MODES


def test_llm_overrides_accepts_native_thinking_and_rejects_stale_values():
    """API 契約收前端旋鈕實際送出的值；舊值域字面必須被擋下（避免無聲復活）。"""
    import pydantic
    import pytest

    from app.api.routers.v1.prejudge import LlmOverridesIn

    for mode in (*app_settings.LLM_THINKING_MODES, "default", None):
        assert LlmOverridesIn(thinking=mode).thinking == mode
    for stale in ("on", "off"):
        with pytest.raises(pydantic.ValidationError):
            LlmOverridesIn(thinking=stale)
    for effort in (*app_settings.LLM_REASONING_EFFORTS, "default", None):
        assert LlmOverridesIn(reasoning_effort=effort).reasoning_effort == effort
    with pytest.raises(pydantic.ValidationError):
        LlmOverridesIn(reasoning_effort="ultra")


# ── provider 三級解析（顯式 > 由 model 反推 > 功能區默認）─────────────────────────────
def _two_provider_settings() -> dict:
    """openai / bytedance 兩家都配好連線與 token 的 settings，供反推測試分辨打到哪一家。"""
    return {
        "llm_connections": {
            "openai": {"base_url": "https://api.openai.com/v1"},
            "bytedance": {"base_url": "https://ark.cn-beijing.volces.com/api/v3"},
        },
        "llm_tokens": {"openai": "sk-OPENAI", "bytedance": "tok-ARK"},
        # prejudge 的出廠預設本來就指向 openai 系種子，這裡顯式放同一份，讓斷言不依賴巧合
        "llm_model_configs": [],
        "provider_models": {"gemini": ["my-custom-gemini-x"]},
    }


def test_provider_id_for_model_known():
    """內建 model 反推得到正確供應商（多模型跑批逐一解端點的地基）。"""
    assert app_settings.provider_id_for_model("gpt-5.4-mini") == "openai"
    assert app_settings.provider_id_for_model("seed-2-0-lite-260428") == "bytedance"


def test_provider_id_for_model_unknown_raises():
    """未登記 model 一律拋錯、**不猜**——猜錯的後果是拿 A 家 token 打 B 家端點且不報錯。"""
    import pytest

    with pytest.raises(ValueError, match="未登記的 model"):
        app_settings.provider_id_for_model("totally-unknown-zzz")


def test_effective_llm_dict_infers_provider_from_model():
    """只覆寫 model（不帶 provider）→ 由 model 反推供應商，連線與 token 一起換過去。

    這是缺陷⑤的回歸鎖：舊實作只認顯式 provider，於是 ByteDance 的 model 會配上 area 默認的
    OpenAI token 與端點送出去——不報錯、只是結果錯。
    """
    eff = app_settings.effective_llm_dict(
        _two_provider_settings(), area="prejudge", overrides={"model": "seed-2-0-lite-260428"}
    )
    assert eff["provider"] == "bytedance"
    assert eff["api_token"] == "tok-ARK"
    assert "volces" in eff["base_url"]


def test_effective_llm_dict_explicit_provider_wins_over_model_inference():
    """顯式 provider 優先於 model 反推——保住「overrides 也能切換供應商連線」的既有語義。"""
    eff = app_settings.effective_llm_dict(
        _two_provider_settings(),
        area="prejudge",
        overrides={"model": "seed-2-0-lite-260428", "provider": "openai"},
    )
    assert eff["provider"] == "openai"
    assert eff["api_token"] == "sk-OPENAI"


def test_effective_llm_dict_infers_provider_from_custom_provider_models():
    """自訂 model（存在 settings.provider_models）也要能反推，否則自訂清單一律反推失敗。"""
    eff = app_settings.effective_llm_dict(
        _two_provider_settings(), area="prejudge", overrides={"model": "my-custom-gemini-x"}
    )
    assert eff["provider"] == "gemini"


def test_effective_llm_dict_unknown_model_keeps_area_default_and_warns(caplog):
    """完全未知的 model 不拋錯（自訂名是合法用法），沿用功能區默認但**留下告警**。"""
    import logging

    with caplog.at_level(logging.WARNING, logger="app.core.settings"):
        eff = app_settings.effective_llm_dict(
            _two_provider_settings(), area="prejudge", overrides={"model": "totally-unknown-zzz"}
        )
    assert eff["provider"] == "openai"  # area 默認
    assert any("無法反推供應商" in r.getMessage() for r in caplog.records)


def test_effective_llm_dict_no_overrides_uses_area_default_config(monkeypatch):
    """沒有 overrides 時走該區出廠預設配置（反推只在「有覆寫 model」時介入）。"""
    cfg = _cfg("cfg-a", "openai", model="gpt-5.4-mini")
    s = {**_two_provider_settings(), **_area_pointing_to(monkeypatch, "prejudge", cfg)}
    eff = app_settings.effective_llm_dict(s, area="prejudge")
    assert eff["provider"] == "openai"
    assert eff["model"] == "gpt-5.4-mini"


def test_switching_provider_without_model_uses_that_providers_default_model(temp_db, monkeypatch):
    """只換 provider、沒帶 model → model 用**該家自己的**預設，不沿用前一家的。

    否則切到 ByteDance 會拿著 gpt-* 的 model 名送出去，必然失敗。
    """
    cfg = _cfg("cfg-a", "openai", model="gpt-5.4-mini")
    monkeypatch.setitem(app_settings.LLM_AREA_DEFAULT_CONFIG_IDS, "prejudge", "cfg-a")
    app_settings.save_settings(
        {
            "llm_connections": {"bytedance": {"base_url": ""}},
            "llm_model_configs": _with_required(cfg),
        }
    )
    eff = app_settings.effective_llm_dict(
        app_settings.load_settings(), area="prejudge", overrides={"provider": "bytedance"}
    )
    assert eff["provider"] == "bytedance"
    assert eff["model"] == app_settings.default_model_for("bytedance")
    assert not eff["model"].startswith("gpt-")


# ── 功能區綁定 llm_area_configs（team 共用單一份；選了就存，無獨立儲存動作）─────────────────


def test_area_config_binding_roundtrip(temp_db):
    """綁定存得進、讀得回（團隊共用單一份，不是 per-user）。"""
    cfg = _cfg("cfg-a", "openai", model="gpt-5.4-mini")
    app_settings.save_settings(
        {"llm_model_configs": _with_required(cfg), "llm_area_configs": {"prejudge": "cfg-a"}}
    )
    assert app_settings.load_settings()["llm_area_configs"] == {"prejudge": "cfg-a"}


def test_area_config_binding_wins_over_factory_default(temp_db, monkeypatch):
    """DB 綁定優先於出廠 `areaDefaults`——否則使用者在 UI 選的那筆對後端路徑完全不生效。"""
    factory = _cfg("cfg-factory", "openai")
    chosen = _cfg("cfg-chosen", "openai", reasoning_effort="high")
    monkeypatch.setitem(app_settings.LLM_AREA_DEFAULT_CONFIG_IDS, "prejudge", "cfg-factory")
    app_settings.save_settings(
        {
            "llm_model_configs": _with_required(factory, chosen),
            "llm_area_configs": {"prejudge": "cfg-chosen"},
        }
    )
    knobs = app_settings.area_default_knobs(app_settings.load_settings(), "prejudge")
    assert knobs["model"] == "m-cfg-chosen"
    assert knobs["reasoning_effort"] == "high"


def test_area_config_falls_back_to_factory_when_unbound(temp_db, monkeypatch):
    """沒綁過的功能區照樣回落出廠起點（全新環境的常態，不是例外）。"""
    factory = _cfg("cfg-factory", "openai")
    monkeypatch.setitem(app_settings.LLM_AREA_DEFAULT_CONFIG_IDS, "prejudge", "cfg-factory")
    app_settings.save_settings({"llm_model_configs": _with_required(factory)})
    assert app_settings.area_default_knobs(app_settings.load_settings(), "prejudge")["model"] == (
        "m-cfg-factory"
    )


def test_area_config_rejects_unknown_area(temp_db):
    """未知功能區擋在寫入邊界（爛值進團隊共用的那份，是全員一起受害）。"""
    cfg = _cfg("cfg-a", "openai")
    with pytest.raises(ValueError, match="未知的功能區"):
        app_settings.save_settings(
            {"llm_model_configs": _with_required(cfg), "llm_area_configs": {"nope": "cfg-a"}}
        )


def test_area_config_rejects_nonexistent_config_id(temp_db):
    """綁到不存在的配置 → 400，不讓死綁定進庫（讀取端的回落是第二道防線，不是藉口）。"""
    cfg = _cfg("cfg-a", "openai")
    with pytest.raises(ValueError, match="指向的模型配置不存在"):
        app_settings.save_settings(
            {"llm_model_configs": _with_required(cfg), "llm_area_configs": {"prejudge": "ghost"}}
        )


def test_area_config_blank_value_clears_binding(temp_db):
    """空字串＝清除該區綁定（回落出廠預設），不是錯誤。"""
    cfg = _cfg("cfg-a", "openai")
    app_settings.save_settings(
        {"llm_model_configs": _with_required(cfg), "llm_area_configs": {"prejudge": "cfg-a"}}
    )
    app_settings.save_settings({"llm_area_configs": {"prejudge": ""}})
    assert app_settings.load_settings()["llm_area_configs"] == {}


def test_area_config_validated_against_configs_in_the_same_patch(temp_db):
    """同一個 patch 同時新增配置＋綁定它 → 必須通過。

    ⚠️ 這條鎖的是 `save_settings` 裡兩段的**順序**：綁定校驗必須排在 `llm_model_configs` 之後，
    否則會拿舊清單去驗新配置，把「剛建好就選它」這個最常見的操作誤判成「配置不存在」。
    """
    cfg = _cfg("cfg-new", "openai", reasoning_effort="high")
    app_settings.save_settings(
        {"llm_model_configs": _with_required(cfg), "llm_area_configs": {"prejudge": "cfg-new"}}
    )
    assert app_settings.load_settings()["llm_area_configs"]["prejudge"] == "cfg-new"


def test_deleting_a_config_prunes_bindings_pointing_at_it(temp_db):
    """刪配置時同步剪除指向它的綁定——DB 裡不留指向不存在 id 的孤兒。

    不剪的話 `llm_area_configs` 會累積死 id：功能上靠讀取端回落還能跑，但庫裡的狀態是騙人的，
    下一個看 DB 的人會以為那一區綁著某個早就不存在的配置。
    """
    keep = _cfg("cfg-keep", "openai")
    doomed = _cfg("cfg-doomed", "openai", reasoning_effort="high")
    app_settings.save_settings(
        {
            "llm_model_configs": _with_required(keep, doomed),
            "llm_area_configs": {"prejudge": "cfg-doomed", "sandbox": "cfg-keep"},
        }
    )
    app_settings.save_settings({"llm_model_configs": _with_required(keep)})
    assert app_settings.load_settings()["llm_area_configs"] == {"sandbox": "cfg-keep"}
