ARG NODE_IMAGE=node:22-alpine
ARG NGINX_IMAGE=nginx:stable-alpine
FROM ${NODE_IMAGE} AS build
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Same-origin /api proxy; no server-side secrets or local .env in this build.
ENV VITE_API_BASE_URL=""
RUN npm run build

FROM ${NGINX_IMAGE}
COPY infra/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=10s --timeout=3s --retries=10 \
    CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
