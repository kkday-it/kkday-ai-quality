"""從 ProductContentAIChecker 沿用的判決資產（prompts / 規則 / 確定性檢查 kernel）。

provenance 與面向對照見 MANIFEST.md。接線方式：深度 judge prompt 注入 adequacy._real()；
machine_checks 接 arbiter 第一道閘門；writer_rules 禁詞/情緒詞/維度供 codex 與 machine_checks 共用。
"""
