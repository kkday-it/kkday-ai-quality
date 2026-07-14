# AI 法官後端 —— 生產 image。
# 與 dev（run.sh 的 uvicorn --reload）不同：無 reload；schema 由 entrypoint 先對齊（見 docker-entrypoint.sh）。
# build context 為 repo 根（需同時 COPY config/ 與 backend/）：
#   docker build -f backend/Dockerfile -t aiq-backend .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AIQ_CONFIG_DIR=/app/config

WORKDIR /app

# 依賴快取層：先只 COPY pyproject 抽 base deps 安裝——改 code 重 build 不再全量重裝，
# 只有 pyproject.toml 變動才使此層失效（layer-cache 排序，deps 與 code 分層）。
COPY backend/pyproject.toml /tmp/pyproject.toml
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('/tmp/pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt

# config/ + constants/ 為前後端共用 SSOT，後端 runtime 讀取（config 經 AIQ_CONFIG_DIR；constants 走 REPO_ROOT/constants=/app/constants）
COPY config/ config/
COPY constants/ constants/
# docs/prompts/：判決 Prompt 唯一真相源（prompt_source 檔案 fallback 讀 /app/docs/prompts/*.md）。
# 只 COPY prompts 子樹（其餘 docs 為人閱文檔，runtime 不需），image 精簡。
COPY docs/prompts/ docs/prompts/
COPY backend/ backend/

# editable 安裝（與 dev 一致；app 指向 source）。--no-deps：依賴已於上方快取層裝好，此步秒級。
RUN pip install --no-cache-dir --no-deps -e ./backend

WORKDIR /app/backend
EXPOSE 8100

# entrypoint 先對齊 schema（空庫 create_all+stamp / 既有庫 alembic upgrade），再交棒給 CMD。
ENTRYPOINT ["./docker-entrypoint.sh"]
# ⚠️ 單 worker：4 套 job registry（export/import/prejudge/upload）為 module 級 in-mem dict，
# 多 worker（獨立 process 無共享記憶體）下 job 啟動 / SSE 進度 / 下載會落到不同 worker → 「job 不存在」/
# 下載 404，且 prejudge Semaphore 併發上限被 ×worker 數放大。P1 將 job 狀態遷共享儲存（Redis/PG）後恢復多 worker。
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8100", "--workers", "1"]
