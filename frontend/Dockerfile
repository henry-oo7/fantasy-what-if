FROM node:24-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./

RUN npm ci

COPY index.html ./
COPY public ./public
COPY src ./src
COPY tsconfig.json tsconfig.app.json tsconfig.node.json ./
COPY vite.config.ts ./

RUN npm run build


FROM nginxinc/nginx-unprivileged:1.29-alpine

USER root

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

RUN chown -R nginx:nginx         /usr/share/nginx/html         /etc/nginx/conf.d/default.conf

USER nginx

EXPOSE 8080

HEALTHCHECK \
    --interval=30s \
    --timeout=5s \
    --start-period=10s \
    --retries=3 \
    CMD wget \
        --quiet \
        --tries=1 \
        --spider \
        http://127.0.0.1:8080/frontend-health \
        || exit 1
