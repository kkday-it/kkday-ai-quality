# app — 後端應用（FastAPI）

三層：**api**（HTTP 邊界）→ **judge**（初判引擎）→ **core**（領域基礎：資料存取 + 判準 config + 模型 + 設定）。

| 子模組 | 職責 | 詳見 |
|---|---|---|
| `api/` | FastAPI gateway（路由 → 委派 core/judge，薄層）| `api/README.md` |
| `judge/` | 初判歸因引擎 + 批量編排 + 上傳落庫 + LLM client | `judge/README.md` |
| `core/` | db（資料存取）· judge_config（判準 loader）· schema · settings · config · paths · auth | `core/README.md` |

依賴方向：`api → judge → core`；`core` 內 `config`/`paths` 為最底層。判決主流程見根 [README.md](../../README.md) 核心流程。
