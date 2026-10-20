# Build context: repository root.
FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine
# The entrypoint renders /etc/nginx/templates/** with the container env at start:
#   nginx.conf.template -> /etc/nginx/nginx.conf, conf.d/default.conf.template -> /etc/nginx/conf.d/default.conf
ENV NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx TRUST_CLOUDFRONT=off STORAGE_ORIGIN=http://localhost:9000 CSP_EXTRA=""
RUN rm -f /etc/nginx/conf.d/default.conf
COPY infra/nginx/nginx.conf.template /etc/nginx/templates/nginx.conf.template
COPY infra/nginx/default.conf.template /etc/nginx/templates/conf.d/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
