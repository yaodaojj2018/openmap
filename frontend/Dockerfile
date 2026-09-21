# ---- 构建阶段：Vite 打包（前端 AK 为构建期注入）----
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY . .
ARG VITE_BMAP_AK=""
ENV VITE_BMAP_AK=$VITE_BMAP_AK
RUN npm run build

# ---- 运行阶段：nginx 托管静态资源并反代 /api ----
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
