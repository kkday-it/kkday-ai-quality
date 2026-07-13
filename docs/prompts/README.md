# 評測交付 Prompt 包 v3（情緒 ×1 + C-1~C-6 域 ×6）

生成：2026-07-13T08:37:43Z · git dd7f024
判準來源：**DB judge_rule_versions active 版**（非 config/ai_judge seed 檔）；規則異動後本包過期，需重新生成比對 manifest.json 版本號。

- C-1：v44（2026-07-13）
- C-2：v40（2026-07-13）
- C-3：v42（2026-07-13）
- C-4：v39（2026-07-13）
- C-5：v39（2026-07-13）
- C-6：v46（2026-07-13）
- global_rule：v36（2026-07-13）

## 跑法順序（每則評論）

1. **Step 1 極性**：跑 `prompts/00_polarity.md`（user 模板 `{TEXT}`＝評論原文）→ 得 `polarity` + `sentiment`。
2. **閘門**：`polarity == "positive"` → 該評論判 non_issue，**不跑任何域 prompt**（對齊 production `attribute_when=['negative', 'neutral']`）。
3. **Step 2 歸因**：`negative`/`neutral` → **六支域 prompt 各獨立跑一次**（同一份評論餵六次；user 模板帶 `{POLARITY}` + `{TEXT}`）。單域判官判「評論是否含本域問題」，不屬本域回空 `attributions`。

## 合併規則（六域結果 → 最終判決；鏡射 production 語義）

1. **證據落地**：`evidence_quote` 須為評論原文逐字子字串（去空白比對）；不落地者視為低信心（production 壓入人審帶）。
2. **信心閘門**：`confidence < 0.2` 整條丟棄（殭屍歸因）。
3. **同域去重**：同一域多條時保留信心最高一條（單域 prompt maxItems=2，正常不觸發）。
4. **跨域排序**：全部存活條目按 confidence 降冪；第 1 條＝primary（`is_primary=true`）不受次要閘門限制；第 2 條起 `confidence < 0.6` 丟棄。
5. **上限**：合併後取前 2 條（`max_attributions`）。

## 與 production 判決可比性

- 輸出對齊 `judgment_history` 快照形狀：`polarity` / `sentiment_score`（=sentiment）/ `l1`（由 `l2_code` 反查 `manifest.json` 的 `l2_to_l1`）/ `l2` / `l3` **恆空**（本包判到 L2 深度，對齊 production `prejudge_depth="l2"`）/ `confidence` / `evidence_quote` / `summary` / `is_primary`。
- 比對真值建議用 `judgments` 表現行判決或 free_tag 外部標籤，勿自我參照。

## 各 prompt 字元數

- 00_polarity：934 字
- 01_C-1_content：5,024 字
- 02_C-2_quality：5,492 字
- 03_C-3_supplier：7,005 字
- 04_C-4_platform：4,686 字
- 05_C-5_service：3,934 字
- 06_C-6_customer：7,211 字

## 與 production 管線的差異（編排層機制，不在 prompt 內）

- **低信心負反饋重問**（reroute_on_low_conf：首輪低信心面向排除後重問一次）——六域獨立跑已是全域掃描，本包不重放此環。
- **evidence grounding 壓信心**：production 由 code 端驗逐字落地並壓信心；本包由合併規則第 1 步等義承接。
- **confidence-gated ensemble**（跨廠投票）與 **G1 自動確認路由**：判決後編排，非 prompt 職責。

## 侷限

- 靜態文字快照：DB 規則異動不會自動反映，重跑產生器即可更新。
- 僅供離線評測比較，不回寫 production 任何資料。
- 六域獨立跑天然傾向多報（合併前最多 6×2 條候選）——合併規則的雙信心閘門為必要步驟，勿省略。
