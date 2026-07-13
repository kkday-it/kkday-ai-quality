# C-1 冻結資料集

由 `build_dataset.py` 產出，供 `evaluate_prompt.py` 評測。**冻結資料入 Git**（PRD §14）。

## 檔案

| 檔案 | 說明 |
|---|---|
| `c1-v{N}-dev.jsonl` | 70% Dev：可查看逐條錯誤並調 prompt（`FrozenCase` 記錄） |
| `c1-v{N}-holdout.jsonl` | 30% Holdout：只在候選 prompt 定稿時跑一次 |
| `c1-v{N}-manifest.json` | 版本、split seed、SHA-256、覆蓋矩陣、防泄漏檢查、來源模型、局限 |

> 目前僅提交計畫與工具；`c1-v1-*` 需先經 Generator→Auditor→人工複核產出（見上層 README 工作流）。
> 全量生成需 `OPENAI_API_KEY` 與生成/審核模型，且 contrast pair 與 uncertain 全部須人工複核。

## 不變式（`build_dataset.py` 保證，發現即 fail-loud）

- **分層 70/30**：按 layer / expected_domain / L2 / boundary / family 分層切分。
- **pair 不拆**：同一 `contrast_pair_id` 兩側必進同一 split。
- **無跨集泄漏**：case_id、exact text、normalized text、contrast_pair 三者皆不得跨 Dev/Holdout。
- **固定 seed**：`--split-seed 42`，切分可完全複現。
- **版本即 hash**：任何修改都應產生新 `dataset_version` 與新 SHA-256（記於 manifest）。
- **重複 id 拒絕**：候選含重複 `case_id` 直接報錯。

## 納入判準（對齊 PRD §8 必審規則）

- 人工 `decision=accept/edit` → 納入（`edit` 套用修改、`origin=human_edited`、重算逐字證據）；
- `reject` → 剔除；
- 無人工決定：僅當 Auditor `status=accepted` 且**非 uncertain 且非 contrast_pair** 時納入
  （uncertain 與 contrast pair 一律須人工複核）。

## 重新冻結

沿用上層 README 工作流步驟 1–3，用新的 `--dataset-version`（如 `c1-v2`）；依 Holdout 具體錯誤調 prompt 後，
必須建立新一輪 Holdout（PRD §9）。
