# repositories/ — WIP（尚未接線）

新攝取架構（PostgreSQL + SQLAlchemy）的資料存取層。**目前為 WIP 死代碼，未接判決鏈。**

⚠️ 這些 repo（`product_repo` / `signal_repo` / `interaction_repo` / `sync_repo`）`from app.models import ...`，
但 **`app/models.py`（SQLAlchemy ORM）尚未建立** → 直接 import 會 ImportError。

- 主力資料層仍走 `app/core/db.py`（SQLite）+ `app/core/roster.py`（灌數/彙總）。
- 接線前置：① 建 `app/models.py`（ORM：Product/Package/Signal/Interaction…）② 決定 SQLite→PostgreSQL 遷移時程。
- 配套：`app/ingestion/`（connector/parser，base.py 已補可 import）。

> 為何留著不刪：新攝取架構的完整 scaffold（parser/connector 已寫完），刪掉成本高於標記隔離。
> 接線計畫對齊「6 源來源匯總架構」（見 docs/PLAN-分類判決-GPT校準.md Gate -1）。
