# PRD — 售後根因 Prompt 人工評判 × AI 定點改寫閉環

> 對象：`/judge/prompt-debug`（Prompt 調試台）。定案 2026-07-28。
> 一句話：把「PM 丟誤判案 → 人找洞 → 手改 Prompt → 手跑回歸 → 手寫 CHANGELOG」這條目前全靠人力的鏈路，
> 變成頁面上可操作的閉環。

## 0. 為什麼要做

`prompts/debug/after_sales_root_cause/CHANGELOG.md` 現有四條版本記錄，每條的形狀都一樣：

1. PM 給一個誤判案（AI 判 X、正解是 Y）
2. 人逐段讀 2.7 萬字 Prompt，找出是哪幾句話把模型帶偏（「洞在哪」）
3. 改那幾段，同時在 `config/ai_judge/after_sales_root_cause.json` 同步 calibration
4. 用調試台重跑本案 N 票確認修好，再跑金標子集回歸確認沒改壞別的
5. 手寫 CHANGELOG 條目

第 2、3、5 步目前完全靠人腦與手工編輯，一次改版耗時以小時計，且**找洞這件事高度依賴對全文的記憶**——
Prompt 已 2.7 萬字，人很難確信「這個誤判只跟這三段有關」。第 4 步的回歸雖然每次都做，但案例散在
`tmp/` 各處與對話記錄裡，沒有累積成可重跑的資產。

本功能把 2、3、5 交給 GPT 最強模型輔助，把 1、4 的素材沉澱成資料庫案例庫。

## 1. 範圍

**做**：單條調試結果的人工評判、案例庫、AI 定點補丁改寫、補丁套用與 diff 預覽、案例回歸重跑。

**不做**（本期）：
- 跑批結果的逐筆評判（幾百筆的檢視表 / 抽樣 / 批次彙整——工期翻倍，另案）
- 自動改 `config/ai_judge/after_sales_root_cause.json`（SSOT 同步仍由人做；AI 補丁只動 Prompt 全文快照）
- 自動 commit / 自動寫 CHANGELOG 檔（AI 產出條目草稿，人複製後自行落檔）

## 2. 資料模型

新表 `prompt_debug_reviews`（`backend/app/core/db/tables.py`）。一列＝一個人工評判過的案例。

| 欄 | 型別 | 說明 |
|---|---|---|
| `id` | BigInteger PK | autoincrement |
| `conversation` | Text | 當時的調試文本原文（完整 IM session） |
| `ai_output` | JSONB | AI 判定的全部欄位（14 欄原樣） |
| `corrections` | JSONB | 人標的正解 `{欄名: 正解值}`；**只存被標錯的欄**，全對＝`{}` |
| `confirmed` | JSONB | 人明確標「對」的欄名清單 |
| `comment` | Text | 人寫的修改建議（自由文字，可空） |
| `prompt_version` | Text | 當時線上 Prompt 版本名；空＝送出前臨時編輯過 |
| `model` | Text | 當時用的模型 |
| `reviewer` | Text | 評判人 email |
| `created_at` | timestamptz | |

索引 `idx_prompt_debug_reviews_created`（列表新→舊）。

配套（依 `.claude/rules/datapack-consistency.md`）：
- alembic migration 建表
- 登記進 `datapack.TABLE_LOAD_ORDER`（放在 `prompt_drafts` 之後、`attributions` 之前）
- 非敏感表、非 autoincrement 序列重置豁免 → 需進 `_SEQUENCE_TABLES`（PK 為 autoincrement）

**`corrections` 與 `confirmed` 要成對存在**（實作時補上的，初稿只有 `corrections`）：回歸判分需要
區分三種狀態——「改完要變成這樣」（corrections）、「改完不准變」（confirmed）、「人沒看過」（兩者
皆無）。只有 corrections 的話，非 corrections 的欄要嘛全當正解（把當時判錯但沒被標到的欄也當成
標準答案，分數虛高）、要嘛全部忽略（過度矯正就抓不到）——兩種都不對。後端擋同一欄同時出現在兩邊。

**刻意不存的**：Prompt 全文快照。案例只記版本名，全文靠 `prompt_debug_versions.read_version()` 回查
（版本檔 append-only 不改不刪，回查一定拿得到）；臨時編輯過的（`prompt_version=''`）就是回查不到，
這種案例回歸時以「當時基準未知」標示，不阻斷。

## 3. 人工評判（頁內區塊）

落點：`PromptDebugger.vue` 第三欄「AI 流式輸出」的結果卡區域。

- 現有 14 張結果卡（`displayedResults`）每張加「✓ 對 / ✗ 錯」二選一
- 標錯 → 就地展開正解輸入。**控件依欄位型別自動決定，選項一律從後端 `output_schema` 派生，不手抄枚舉**：

  | 欄位型別 | 控件 |
  |---|---|
  | enum（L1 / L2 / L3 / L4 / sentiment） | `a-select`（可搜尋；選項＝schema enum） |
  | boolean（三個 flag + no_actionable_content） | `a-switch` |
  | urgency（integer 1–5） | `a-radio-group type="button"` |
  | keywords（array） | `a-input-tag` |
  | summary / confidence | `a-textarea` / `a-input-number` |

- 區塊底部：整體修改建議 `a-textarea` + 「存為案例」按鈕
- 全欄皆標「對」也允許存（＝正例，回歸時用來防過度矯正）

## 4. AI 定點改寫

### 4.1 產出契約

新 module `backend/app/judge/prompt_reviser.py`。送 gpt-5.5，strict `json_schema`：

```jsonc
{
  "diagnosis": "洞在哪：這批案例暴露的是哪一條判準寫壞了（一段話）",
  "patches": [
    {
      "anchor":      "現行 Prompt 中要被取代的片段（逐字）",
      "replacement": "換成什麼",
      "reason":      "為什麼這樣改",
      "risk":        "這樣改可能過度矯正到哪一類案例"
    }
  ],
  "changelog": "CHANGELOG 條目草稿（markdown，比照既有條目格式）"
}
```

送進去的 user prompt 含：現行 Prompt 全文、選中案例（對話 + AI 判的 + 人標的正解 + 建議）、
以及**改寫紀律**——明確禁止大幅重寫、禁止刪除既有判例庫與硬規則、每條補丁的 anchor 必須逐字複製自原文。

> 紀律這條有血的教訓：memory `hw-root-cause-prompt-tuning` 記著 v_hw12 那次「對齊瘦身」砍掉實測校準
> 錨點，L3 直接掉 8.7 分，逐行 diff 證明 7 錯中 6 錯直接對應被砍的規則。**結構欄的分數載體
> 是校準層錨點，不是判準的字面工整**。所以本設計不給模型「整篇重寫」這個選項。

### 4.2 anchor 驗證（正確性核心）

後端拿到補丁後逐條驗證 `prompt.count(anchor)`：

- `== 1` → `matched`，可套用
- `== 0` → `not_found`（模型沒逐字複製，常見於它自己「順手改了標點」）
- `> 1` → `ambiguous`（片段太短，套用會改到不該改的地方）

後兩者**不給勾選套用，但照樣顯示**——它想改什麼仍是有價值的診斷資訊，人可以自己手動去改。

### 4.3 套用

`apply_patches(prompt, patches, selected_indices) -> str`：依 anchor 在全文中的位置**由後往前**替換
（先替換靠後的，前面的 offset 才不會位移）。套用在後端而非前端：唯一性驗證與替換順序是正確性核心，
兩邊各寫一份必然 drift，且套完的全文要直接餵給既有的「存為新版本」。

### 4.4 UI

新抽屜 `PromptReviseDrawer.vue`（右側，寬 1040，`defineAsyncComponent` 懶載）：

- **左**：案例清單（勾選要餵給 AI 的）＋ 本次 LLM 配置（`LlmConfigPicker` + `LlmKnobs`，area=`prompt_revise`）
- **右**：AI 診斷 → 補丁清單（每條：原文片段 / 替換後 / 理由 / 風險 / 命中狀態 / 勾選框）
- **套用後**：`MdTextDiff`（既有公共元件）左右對照 → 「存為新版本」（既有 `savePromptVersion`）
  ＋ CHANGELOG 條目草稿可一鍵複製

## 5. 回歸重跑

勾選案例庫的 N 條 → 用**候選新 Prompt**（未存版本的草稿全文亦可）逐條重跑 → 逐欄比對。

逐欄四種結果：
- **修好 / 還是不對**：`corrections` 的欄——新輸出對上人標的正解＝修好
- **守住 / 改壞**：`confirmed` 的欄——新輸出與當時一致＝守住，變了＝改壞
- 兩者皆無的欄：人沒看過，**不計分**

彙總回四格計分 ＋ 逐案例列出改壞/未修好的欄（欄名、應為、實得）。有任何「改壞」即在頁面上紅字
攔阻：這版不該直接上線。

實作走輕量 in-mem job（比照 `prompt_sandbox.py` 的 `JobStore` pattern，該檔 docstring 已明說這是
「小規模調適用途、不需要正式批量管線的控制項」的既有輪子），**不用** `prompt_debug_batch`——
那條是檔案上傳導向、有 run 目錄與斷點續跑，回歸是 DB 來源、通常 <50 條、比對邏輯完全不同。

**這段不是加分項**：沒有它，AI 改完 Prompt 只能確認「那一條修好了」，不知道有沒有順手弄壞另外二十條。
CHANGELOG 四條版本記錄每一條都做了回歸，就是因為這事真的會發生（2026-07-27-225310 那條就攔下一次
過度矯正：初版把「商品規格」寫進判準，把配備詢問案吸走，靠回歸才發現）。

## 6. 配置與權限

- `config/global/llm_model.json` 的 `areas` 新增 `"prompt_revise"`，預設模型 gpt-5.5（最新旗艦）。
  與裁決用的功能區分開：裁決要便宜（跑批 gpt-5.4-mini），改 Prompt 要聰明。
- 權限沿用既有：存案例 / 跑 AI 改寫 / 回歸重跑 → `PREJUDGE_RUN`；存為新版本 → `JUDGE_RULE_MANAGE`。

## 7. API

| 方法 | 路徑（前綴 `/api/v1/prejudge`） | 用途 |
|---|---|---|
| POST | `/prompt-debug/reviews` | 存一則評判案例 |
| GET | `/prompt-debug/reviews` | 案例列表（新→舊） |
| DELETE | `/prompt-debug/reviews/{id}` | 刪案例 |
| POST | `/prompt-debug/revise` | 依選中案例產出補丁清單（含 anchor 命中狀態） |
| POST | `/prompt-debug/revise/apply` | 套用選中補丁，回新全文 |
| POST | `/prompt-debug/regression` | 啟動回歸重跑，回 job_id |
| GET | `/prompt-debug/regression/{job_id}` | 回歸進度 / 結果輪詢 |

## 8. 交付批次

| 批 | 內容 | 完成後可用 |
|---|---|---|
| 1 | 表 + migration + CRUD + 頁內評判區塊 | 能標、能存、能看案例 |
| 2 | `prompt_reviser` + revise/apply 端點 + `PromptReviseDrawer` | 閉環可用：評判 → AI 改 → diff → 存版 |
| 3 | 回歸 job + 結果比對 + 抽屜分頁 | 安全網：改完能驗有沒有改壞 |

每批各自可跑、各自 commit。

## 9. 文檔同步清單（依 `.claude/rules/docs-sync.md`）

- 根 `README.md` API 一覽表（7 個新端點）
- `backend/app/api/README.md`、`backend/app/judge/README.md`、`backend/app/core/db/README.md`
- `config/README.md`（新 area）
- `frontend/apps/console/src/features/README.md`
- `docs/README.md`（本檔進索引）
