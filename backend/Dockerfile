# AI 法官後端 —— 生產 image。
# 與 dev（run.sh 的 uvicorn --reload）不同：多 worker、無 reload；migration 由 entrypoint 先跑。
# build context 為 repo 根（需同時 COPY config/ 與 backend/）：
#   docker build -f backend/Dockerfile -t aiq-backend .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AIQ_CONFIG_DIR=/app/config

WORKDIR /app

# config/ 為前後端共用 SSOT，後端 runtime 讀取（AIQ_CONFIG_DIR 明確指向，免依賴 parents[3] 推導）
COPY config/ config/
COPY backend/ backend/

# 依賴 SSOT＝pyproject.toml（editable，與 dev 一致；app 指向 source，config 定位穩定）
RUN pip install --no-cache-dir -e ./backend

WORKDIR /app/backend
EXPOSE 8100

# entrypoint 先跑 alembic upgrade head，再交棒給 CMD（生產 schema 演進唯一入口）
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8100", "--workers", "4"]
