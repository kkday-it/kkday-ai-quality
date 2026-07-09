# AI 法官後端 —— 生產 image。
# 與 dev（run.sh 的 uvicorn --reload）不同：無 reload；schema 由 entrypoint 先對齊（見 docker-entrypoint.sh）。
# build context 為 repo 根（需同時 COPY config/ 與 backend/）：
#   docker build -f backend/Dockerfile -t aiq-backend .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AIQ_CONFIG_DIR=/app/config

WORKDIR /app

# config/ + constants/ 為前後端共用 SSOT，後端 runtime 讀取（config 經 AIQ_CONFIG_DIR；constants 走 REPO_ROOT/constants=/app/constants）
COPY config/ config/
COPY constants/ constants/
COPY backend/ backend/

# 依賴 SSOT＝pyproject.toml（editable，與 dev 一致；app 指向 source，config 定位穩定）
RUN pip install --no-cache-dir -e ./backend

WORKDIR /app/backend
EXPOSE 8100

# entrypoint 先對齊 schema（空庫 create_all+stamp / 既有庫 alembic upgrade），再交棒給 CMD。
ENTRYPOINT ["./docker-entrypoint.sh"]
# ⚠️ 單 worker：4 套 job registry（export/import/prejudge/upload）為 module 級 in-mem dict，
# 多 worker（獨立 process 無共享記憶體）下 job 啟動 / SSE 進度 / 下載會落到不同 worker → 「job 不存在」/
# 下載 404，且 prejudge Semaphore 併發上限被 ×worker 數放大。P1 將 job 狀態遷共享儲存（Redis/PG）後恢復多 worker。
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8100", "--workers", "1"]
