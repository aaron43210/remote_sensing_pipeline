# Build context: repository root.
FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM nginx:1.30-alpine
# The entrypoint renders $NGINX_ENVSUBST_TEMPLATE_DIR/** with the container env at start
# (nginx.conf, site.inc, conf.d/default.conf). templates/ = local HTTP on :80;
# templates-aws/ = HTTPS on :443 for CloudFront (docker-compose.aws.yml selects it).
ENV NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx TRUST_CLOUDFRONT=off STORAGE_ORIGIN=http://localhost:9000 CSP_EXTRA="" \
    ORIGIN_SECRET="" ORIGIN_HOST=""
RUN apk upgrade --no-cache && rm -f /etc/nginx/conf.d/default.conf
COPY infra/nginx/nginx.conf.template infra/nginx/site.inc.template /etc/nginx/templates/
COPY infra/nginx/default.conf.template /etc/nginx/templates/conf.d/default.conf.template
COPY infra/nginx/nginx.conf.template infra/nginx/site.inc.template /etc/nginx/templates-aws/
COPY infra/nginx/default.tls.conf.template /etc/nginx/templates-aws/conf.d/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
