# [101] 訂單取消 域判官 Prompt 版本記錄

> 版本檔 append-only：升版＝新增 `vN.md` ＋ 本檔補條目 ＋ 更新 `backend/app/judge/prompt_debug.py` 注冊表 `DOMAINS["theme_101"]["prompt_version"]` 指針；舊版檔不改不刪。

### 加購/延長天數棄權邊界增補 — 2026-07-27（就地修，未升版號）

必棄權清單補一條：主動要加購/延長連線商品天數（願付費、走加購或另下新單合併），**即使事由是颱風/航班改期**→ 修改訴求，非本域；只有在爭取特例通融或減免才屬本域。血緣＝全量分類 v3 同日誤判案，詳見 `prompts/debug/after_sales_root_cause/CHANGELOG.md`。

## v2 — 2026-07-22（現行）

依 [Claude Fable 5 prompting 指南](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5) 重寫格式，**判準語義不變**：

- 開頭補任務意圖（多判官並行合議、判定進根因統計、寧棄權勿誤收的原因）——give the reason, not only the request
- 本域受控資料改用 `<taxonomy>` XML 標籤定界，資料與指令明確分離
- 原 12 條編號混排規則按職責重組為「裁決原則／本域邊界／資料處理守則／輸出契約／裁決流程」，重複禁令收斂為一處
- 「逐字取值」「只輸出 JSON」等輸出約束集中至輸出契約節

## v1 — 2026-07-22

- 調試台上線版快照（原 `prompts/debug/domains/theme_101.md` 平鋪檔）
