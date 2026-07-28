# 全量分類（after_sales_root_cause）Prompt 版本記錄

> 版本檔 append-only：升版＝新增 `vN.md` ＋ 本檔補條目 ＋ 更新 `backend/app/judge/prompt_debug.py` 的版本指針；舊版檔不改不刪。

## v3 — 2026-07-27（測試中，調試台以「輸出契約 v3」提供）

v1/v2 是同一輸出契約的格式改版；**v3 換了輸出契約本身**（《AI 判定其他欄位定義》定案版 2026-07-22）：

- 欄位：`keywords` 陣列（1–5 個×2–6 字，全 session 必填，事由→訴求→對象，僅取 [USER]）、`urgency` 由布林升 1–5 整數（≥4 觸發高優先、轉真人不自動高分）、新增 `no_actionable_content`（true ⇒ OOT∧殘段∧keywords=[]）、裁撤 `tail_theme`、全欄禁 null（不適用填 `n/a`）
- 判準：內嵌 260 筆金標調參的實測校準層（calibration／likely_cause_guide／跨類判例庫／六條 cause 硬規則，血緣＝tmp 作業線 v_hw14→v3）；「單據問題錯置到供應商線」補決勝規則（供應商線單據訴求一律歸錯置、平台線依訴求歸前四類——維度重疊實測會穩定吃掉訴求訊號，見 2026-07-27 合成單據 demo）
- 形態：**靜態快照**（分類庫已內嵌，非模板渲染）——校準層僅 [93] C12/C13 兩條併入 `config/ai_judge/after_sales_root_cause.json` SSOT（見 2026-07-27 加購方向增補），其餘仍只在本快照；受控值（類名/causes/theme 全稱字面）生成時已對齊 config，config 若改受控值需重生本檔。v3 轉正式時應將校準層併入 SSOT 改走模板渲染，並同步 batch 管線 schema
- 消費端：調試台雙契約並存（`body.contract` 擇一），v2 仍為正式批次口徑；schema/欄位校驗由 `prompt_debug.py` 的 `_CONTRACT_RUNTIME` 依契約切換

### v3 校準層增補 — 2026-07-27（同檔就地修，未升版號）

**[93]↔[COMM]↔[101] 三向邊界：連線商品「加購延長使用天數」**。真實跑批誤判案（WiFi 機用戶「怎麼加購延長一天？訂購都要三天起跳」→ 因颱風要求延長至 7/14）被判 `[COMM] 連線商品使用方式或設定諮詢`；Gemini 另有票數漂向 `不可抗力特例申請無通道`（該類校準原就寫「因不可抗力想延期/延後（如延後還機）」，字面正中本案）。正解＝`[93] 特殊需求/加購無自助入口`＋`系統缺特殊需求欄位`＋`改人數/加購`。

- 依據：線上《根因標籤架構》表口徑——加購屬訂單層操作，[COMM] 軸線是「能不能用」（安裝/連線/使用規則），不含訂單層加購；颱風僅為事由，用戶未爭取退改減免即不觸發不可抗力
- 三處校準：[93] 加購類新增 `calibration`（連線商品延長天數歸本類＋雙向排除理由）＋cause/target guide 各補一句（「三天起跳」門檻→系統缺特殊需求欄位；延長天數→改人數/加購）；[COMM] 使用諮詢 `calibration` 補轉向（延長辦理→[93]，純問使用期限才留本類）；[101] 不可抗力 `calibration` 補「延後還機/延長使用」細分（爭取特例通融→本類、願付費加購→[93]）
- 判例庫兩條 verbatim：颱風路由簇、[COMM] 簇各一條，分別堵兩個漂移方向
- 驗證：調試台 v3 契約走 app 連線實跑本案 → 四欄全對、`valid=true` 零校驗問題（gpt-5.4-mini）；tmp 作業線同步同一修正，gpt-5.4-mini 與 Gemini 各 5 票全對，三條回歸（eSIM 提前裝時程、網路怎麼用單問、金標 #18 特例延後還機）各 5/5 不動
- 待辦：線上表側建議 PM 於 [COMM] 使用諮詢的 Exclude 欄補「要求延長使用天數/加購天數 → [93] 特殊需求/加購無自助入口」（表為真相源，補上才不會在下次 taxonomy 重生時掉回去）

### 加購天數「方向不拘」增補 — 2026-07-27（v2 SSOT ＋ v3 快照同步就地修，未升版號）

**誤判案**（v3.2 跑批 session 690677，WiFi 機）：颱風假設性詢問開場 → 航班提前，用戶要「改到明天 6:00 領機、還機時間不變、我可以多付錢」，客服請其另下 1 天短單再人工合併兩單。判成 `[93] 修改受限`＋`商品規則不允許改`＋`改日期/時段/班次`；正解＝`特殊需求/加購無自助入口`＋`系統缺特殊需求欄位`＋`改人數/加購`（同金標 #83 組合）。

- 兩個洞：①既有加購校準只寫「延後歸還」語感，模型沒把「提前領取」對映成加購天數；②「修改受限」缺前提，需求最終被客服協助達成也被讀成「不允許改」
- v3 快照（判例庫）：颱風簇補「航班提前要提早領取」verbatim 一條 ＋ 緊接反面對照一條（分界＝有無加購/付費/下單動作，堵回金標 #18「延後還機求通融」）；「被規則擋→取消重訂」標的分流補前提「真的被擋、改不成」；WiFi 加購判例補「提早一天領機」與「日期只能選 X-Y」線索
- v3 快照（分類庫）：C13 加購類 `calibration` 補方向不拘＋另下短單合併＝無自助入口證據；`likely_cause_guide` 補「選不到目標日期區間」；C12 修改受限新增 `calibration`（前提＝最終確定改不了）
- **v2 SSOT**：`config/ai_judge/after_sales_root_cause.json` 的 C12/C13 首次帶 `calibration` 欄（模板 `prompts/debug/after_sales_root_cause.md` 同步宣告「calibration 優先於 Definition/Include/Exclude 字面」）；C12 另含「被擋→取消重訂」標的分流與「取消已實際辦成→[101]」判準，C13 含「只求通融無付費/下單＝[101]」反面
- 驗證（gpt-5.4-mini，調試台實跑，每案 3 票取多數）：**v2** 本案 3/3 正解（改前 3/3 誤判）；回歸 #18 3/3、#59 3/3（改前僅 1/3 完全命中）、#61 2/3、#83 3/3、#92 category 3/3（cause 與改前同為 `商品不支援原單修改`，非本次引入）。**v3** 本案 3/3、#18 3/3、#59 3/3、#61 3/3、#83 3/3
- 同步：tmp 作業線 `build_taxonomy_v3.py` / `prompt_v3.md` 同一修正並重生 `taxonomy_v3.json`＋`prompt_final_rendered_v3.md`；域判官 `theme_93/v2.md`（加購方向＋反面棄權）、`theme_101/v2.md`（願付費加購棄權）同步邊界

## v2 — 2026-07-22（現行）

依 [Claude Fable 5 prompting 指南](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5) 重寫格式，**判準語義不變**：

- 開頭補任務意圖（判定進根因統計、精準優於覆蓋、unclear/OOT 的存在理由）——give the reason, not only the request
- 受控資料改用 `<taxonomy>` / `<oot_subtype_options>` / `<modify_target_options>` XML 標籤定界，資料與指令明確分離
- 原 13 條編號混排規則按職責重組為「裁決原則／欄位判定規則／資料處理守則／輸出契約／裁決流程」，重複禁令收斂為一處
- 「逐字取值」「只輸出 JSON」等輸出約束集中至輸出契約節
- 補顯式 JSON 輸出契約範例（原版僅文字描述欄位；強化 response_format 降級為 json_object / 純 Prompt 約束時的穩健性）

## v1 — 2026-07-22

- 調試台上線版快照（原 `prompts/debug/after_sales_root_cause.md` 平鋪檔）
