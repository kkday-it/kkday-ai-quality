# AI 法官前端 —— 生產 image（多階段）。
# build 階段用 vite 產出靜態 dist，runtime 用 nginx 提供 + 反代 /api 到後端。
# 與 dev（pnpm dev 的 vite dev server）不同：這是 build 後的靜態產物，非 dev server。
# build context 為 repo 根（vite @config alias 指向 repo 根 config/）：
#   docker build -f frontend/Dockerfile -t aiq-frontend .

FROM node:22-slim AS build
RUN npm install -g pnpm
WORKDIR /app
# @config / @constants → repo 根 config/·constants/（vite.config 的 ../../../*）；build 需一併帶入
COPY config/ config/
COPY constants/ constants/
COPY frontend/ frontend/
WORKDIR /app/frontend
RUN pnpm install --frozen-lockfile && pnpm build

FROM nginx:alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/apps/console/dist /usr/share/nginx/html
EXPOSE 80
