# 初判歸因分類準確度升級方案（成熟輪子優先 · 驗證先行）

> 研究日期 2026-06-30。基於 11 個並行研究 agent（DSPy/校準/HTC/embedding/結構化/人審/評估/業界案例 + 整合架構 + 繁中選型 + 對抗批判）。
> 原則：**不重造輪子、不手刻 prompt**，用社群驗證的現成方案；但**先量測與驗證根因，再投入工具**，避免過度工程。

## 一句話結論

負向歸因合理率 68% 偏弱，**最高 ROI 的成熟輪子是「嵌入檢索把 229 條 L3 候選縮到 top-k 再讓 LLM 精排」（Booking.com Text2Topic 同規模 239 類已驗 92.9% mAP）+ 動態 few-shot + DSPy 自動優化 prompt + 評估底座**；但**動工前必做 3 件驗證**（人工真值校驗、規則 vs 工具根因實驗、embedding recall@k 實測），否則可能是循環論證或解錯問題。

## 現狀（已驗證）

- 階層漏斗 LLM（Stage1 選 7 個 L1 域 + 證據門檻棄判 → Stage2 域內 ~40 條 L3 評分取最高，共 229 條 L3），gpt-5-mini，規則 canon/forbid 存 `config/ai_judge/*.json`。
- 2021 筆稽核：極性準確 97.5%（高）、**負向歸因合理率 68%（弱）**。已知錯誤：L1 域錯位 ~32%、verdict 誤用、L2/L3 空白、force_majeure 誤用。
- 資料在 PostgreSQL（intake_items + judgments）；本機已跑 Milvus（claude-context 用，可複用）。

## 動工前必做的 3 件驗證（最高優先 · 對抗批判結論）

1. **真值可信度校驗（破循環論證）**：2021 筆真值是「LLM agent 稽核」產出，非人工。研究指 LLM-judge 與人類 Cohen's Kappa 僅 ~0.23、自偏好偏差 -38%~+90%。**抽 150-200 筆負向給客服/QC 主管人工重標**，算 Kappa：≥0.6 可用；<0.4 則 golden set 需重建。否則用它訓 DSPy/Cleanlab/校準＝放大 LLM 自身偏誤。
2. **根因實驗：規則 vs 工具**：抽 50 筆不合理案例，分類「A=規則模糊（改規則可修）/ B=規則清楚但 LLM 不遵循（需工具）」。memory 記錄的四大錯誤（域邊界不清、verdict 樹邏輯、覆蓋空白、force_majeure 定義）**多屬規則品質**。若 A>50% → 先花 2-3 天重寫 229 條 canon/forbid，重跑評估，再決定加工具。改規則＝0 依賴、最低成本。
3. **embedding recall@k 實測**：用 100 筆已知正確 L3 的真實評論，測 Qwen3/BGE-M3 的 recall@5/10/20。若正解漏出 top-k，LLM 再準也救不回。recall@20 <90% → 嵌入粗篩需 fine-tune 或放棄。

## 分階段 Roadmap（成熟輪子 → 我們的接入點）

### P0 — 量測底座（先量才知道升多少 · ~2 人日 · 低風險）
- **promptfoo**（OpenAI 收購, 22k★）：2021 golden set 當 CSV，CI A/B 防退化，半天起步。
- **DeepEval + HiClass**（hierarchical F1）：階層 partial credit（L1 對 L3 錯給部分分），唯一能量化「改版升多少」。二選一即可，勿雙開。
- **Cleanlab**（11k★, Apache-2.0）：`find_label_issues` 找 2021 筆系統性標錯/可疑判（**須先過第 1 件人工校驗**再信）。
- **錯誤聚類**：BERTopic 對 false cases 自動聚類找 failure modes（需 ≥300 筆才有意義）。

### P1 — 嵌入檢索粗篩 + 動態 few-shot（最大準確度槓桿 · ~4 人日 · 中風險）
- **embedding 選型定案：Qwen3-Embedding-0.6B**（C-MTEB Retrieval 71.03 居首、Apache-2.0、639MB CPU 可跑、繁簡皆強；備選 BGE-M3 MIT）。**放棄** OpenAI embedding（中文表現未公開+評論隱私）、jina（授權商業受限）。
- **sentence-transformers + Milvus**（複用現有實例，新建 `l3_canon` / `golden_judgments` collection，HNSW+COSINE）：229 條 L3 canon 預嵌入 → 每筆評論檢索 top-k 候選 → Stage2 只精排 top-k（~40→~10）。佐證：Booking.com Text2Topic（239 topic, 92.9% micro mAP）、TELEClass（WWW 2025）、Class-RAG。
- **動態 few-shot**：從 2021 筆已判決檢索 top-3 相似範例注入 prompt（+5-12% F1）。⚠️ 稀有 L3 僅 1 筆時勿重複注入（過擬合）。
- 接入點：新增 `core/embedding.py`；`judge/prejudge.py` 的 `_build_l1_prompt`/`_build_l3_prompt` 加候選/範例注入槽。feature flag 灰度。

### P2 — 自動優化 prompt + 結構化輸出（治本 · ~5 人日 · 中高風險）
- **DSPy MIPROv2**（Stanford, 22k★）：用 golden set 自動優化 Stage1/Stage2 的 instruction + few-shot 選擇，**取代手刻 prompt**。離線 compile → 存 JSON，inference 只讀 JSON（compile 失敗不影響線上）。⚠️ 限制：`auto=medium` 上限 300 val 範例、o 系列 reasoning 模型不相容、小集易過擬合 → 先 temperature=0 跑 5 次測方差，方差 > 改善幅度則無意義。
- **Instructor**（11k★）：Pydantic 結構化輸出 + 自動 retry，取代裸 json_object；`@model_validator` 強制「L3 必屬所選 L1 域」（PtV 驗證）。Stage1 可選 **Marvin logit_bias** 從採樣層鎖死合法 L1（零 retry）。
- 技術級便宜修補（對應已知錯誤，各 S）：**PtV**（Stage2 後驗證 L3 是否合 L1）、**dual-expert gate**（L1 top-2 接近時雙域評分由 L3 決勝，解 eSIM/網路平票）、**parent-prob 約束**（conf(L3)≤conf(L1) 否則 flag）。⚠️ PtV 用同模型驗自己對系統性偏誤無效，需異質檢查。

### P3 — 校準 + 棄判 + 人審飛輪（穩態 · ~4 人日 · 低風險）
- **sklearn IsotonicRegression**（已有 calibration.py）：用 golden set 校準信心，取代手寫 cap 係數，ECE 量化。
- **MAPIE conformal**：自動定 needs_review 閾值（覆蓋率保證）。⚠️ 229 類×~2 筆下 per-class 保證失效，**僅在 L1 層（7 類）用**或先合併稀有 L3。
- **Argilla**（HuggingFace, HF Spaces 免費部署）：needs_review → 人審 UI → 修正回灌 golden set → 飛輪。⚠️ 稀有 L3 月個位數，飛輪轉速慢，需務實預期。

## 業界共識（可借鏡的成熟模式）
1. **Coarse-to-Fine Cascade**（Meta/Zendesk/Intercom/Uber COTA）：規則/embedding/小模型粗篩 → 大 LLM 只處理難案，信心路由人審，省 40-79% 成本。
2. **Taxonomy-Driven Bi-Encoder**（Booking.com Text2Topic 239 類 92.9% mAP / TELEClass）：label 描述本身編碼做相似度匹配，最能處理大 label space + 支援 zero-shot 新類別。
3. **Confidence-Gated HITL + A/B 飛輪**（Intercom/Amazon/Zendesk）：信心閘門路由 + Shadow→Canary→A/B 漸進上線防退化。

## 灰度上線（防退化）
Shadow mode（新舊並跑、只記 diff 不回新結果，比對 L1 翻轉率/棄判率/信心漂移/verdict KL）→ Canary 10%→50%→100%；DSPy compile 後 golden set 閘門：L1/L3/hF1 不退化才更新 prompt JSON。

## 成本/延遲（2000 筆批量，估算）
- 現狀 ~2,800 token/筆；P1 後 Stage2 候選 40→10、input −60% → ~2,200 token/筆，**API 費用 -17%**；本機 embedding +~30s 一次性（可流水線降至 +10s）。
- DSPy compile 一次性離線 ~$3.6（gpt-5-mini）。

## 預期增益（保守估，需 P0 量測後校正）
- 負向歸因合理率：68% → P1 ~78% → P2 ~85%。
- L1 域準確率：~68% → ~82%（P1）→ ~87%（P2）。

## 明確「不要做/暫緩」（避免過度工程）
- 不雙開 promptfoo+DeepEval（擇一）；不為 229×2 筆硬上 per-class conformal；不引 LiteLLM（2026-03 供應鏈攻擊前例）/ jina（授權）/ Outlines（僅自架模型才有約束解碼優勢）；SetFit 229 類超設計範圍暫緩；Snorkel 僅在要擴訓練集時才用。

## 採用的成熟輪子清單（不自寫）
Qwen3-Embedding-0.6B · sentence-transformers · Milvus（已有）· DSPy(MIPROv2) · Instructor · Marvin(可選) · Cleanlab · scikit-learn(isotonic) · MAPIE(限 L1) · promptfoo 或 DeepEval+HiClass · BERTopic · Argilla。

## 最小可行第一步（本週）
P0 量測底座 + 3 件驗證並行。**先確認真值可信 + 根因（規則 vs 工具）+ recall@k**，再決定 P1 投入規模。這是把後續每一分工程投到刀口上的前提。
