# AI 法官前端 —— 生產 image（多階段）。
# build 階段用 vite 產出靜態 dist，runtime 用 nginx 提供 + 反代 /api 到後端。
# 與 dev（pnpm dev 的 vite dev server）不同：這是 build 後的靜態產物，非 dev server。
# 層序：先只 COPY manifest → pnpm fetch（依賴快取層）→ 再 COPY code → offline install + build。
# pnpm 走 corepack（吃 package.json 的 packageManager pin，與 dev image 版本一致）。
# build context 為 repo 根（vite @config alias 指向 repo 根 config/）：
#   docker build -f frontend/Dockerfile -t aiq-frontend .

FROM node:22-slim AS build
RUN corepack enable
WORKDIR /app/frontend
# 依賴快取層：package.json 供 corepack 解析 pin；lockfile 供 fetch（只有 lockfile 變動才失效）
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
# 本機透明代理偶發截斷國際線路回應（error 23）→ shell 層整段重試（比照 ProductContentAIChecker 實測解法）；
# 網路持續不穩時可 --build-arg NPM_REGISTRY=https://registry.npmmirror.com 換鏡像（integrity sha 仍驗證）。
ARG NPM_REGISTRY=https://registry.npmjs.org
RUN for i in 1 2 3 4 5; do \
      echo "[pnpm fetch] attempt $i"; \
      pnpm fetch --registry "${NPM_REGISTRY}" && exit 0; \
      echo "[pnpm fetch] attempt $i failed, retry in 5s"; sleep 5; \
    done; \
    echo "[pnpm fetch] failed after retries"; exit 1
# code + 共用配置（@config / @constants → repo 根 config/·constants/，vite.config 的 ../../../*）
WORKDIR /app
COPY config/ config/
COPY constants/ constants/
COPY frontend/ frontend/
WORKDIR /app/frontend
RUN pnpm install --frozen-lockfile --offline && pnpm build

FROM nginx:alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/apps/console/dist /usr/share/nginx/html
EXPOSE 80
