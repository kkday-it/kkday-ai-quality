# [93] 訂單申請修改 域判官 Prompt 版本記錄

> 版本檔 append-only：升版＝新增 `vN.md` ＋ 本檔補條目 ＋ 更新 `backend/app/judge/prompt_debug.py` 注冊表 `DOMAINS["theme_93"]["prompt_version"]` 指針；舊版檔不改不刪。

### 加購天數「方向不拘」邊界增補 — 2026-07-27（就地修，未升版號）

本域邊界速記兩條：①「修改受限」加前提（規則/供應商明確拒絕；最後改成或另下短單合併達成＝不是本類）；②連線商品延長・加購天數方向不拘（提前領取／延後歸還／中間延長），願付費、另下單合併 → 加購類＋`改人數/加購`，自助塞不進需求 → `系統缺特殊需求欄位`；反面「只求通融、無加購/付費/下單動作」→ 不可抗力訴求，棄權。血緣＝全量分類 v3 同日誤判案（WiFi 機航班提前要提早領機），詳見 `prompts/debug/after_sales_root_cause/CHANGELOG.md`。

## v2 — 2026-07-22（現行）

依 [Claude Fable 5 prompting 指南](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5) 重寫格式，**判準語義不變**：

- 開頭補任務意圖（多判官並行合議、判定進根因統計、寧棄權勿誤收的原因）——give the reason, not only the request
- 本域受控資料改用 `<taxonomy>` XML 標籤定界，資料與指令明確分離
- 原 13 條編號混排規則按職責重組為「裁決原則／本域邊界／資料處理守則／輸出契約／裁決流程」，重複禁令收斂為一處
- 「逐字取值」「只輸出 JSON」等輸出約束集中至輸出契約節

## v1 — 2026-07-22

- 調試台上線版快照（原 `prompts/debug/domains/theme_93.md` 平鋪檔）
