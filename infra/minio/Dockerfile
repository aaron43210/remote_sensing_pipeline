# MinIO server built from the official open-source repository at a pinned
# release. (Prebuilt community images are no longer published on Docker Hub
# or quay.io; the source and its AGPL-3.0 licence are unchanged.)
FROM golang:1.24-alpine AS build
ARG MINIO_RELEASE=RELEASE.2025-10-15T17-29-55Z
RUN apk add --no-cache git
RUN git clone --depth 1 --branch "${MINIO_RELEASE}" https://github.com/minio/minio /src
WORKDIR /src
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -X github.com/minio/minio/cmd.ReleaseTag=${MINIO_RELEASE}" -o /out/minio .

FROM alpine:3.20
RUN apk add --no-cache ca-certificates curl && adduser -D -u 10001 minio && mkdir /data && chown minio /data
COPY --from=build /out/minio /usr/bin/minio
USER minio
EXPOSE 9000 9001
HEALTHCHECK --interval=10s --timeout=3s --retries=10 CMD curl -fsS http://localhost:9000/minio/health/live || exit 1
ENTRYPOINT ["/usr/bin/minio"]
CMD ["server", "/data", "--console-address", ":9001"]
