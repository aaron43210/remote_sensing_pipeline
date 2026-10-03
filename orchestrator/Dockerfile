# =============================================================
# OWNER: AARON
# Orchestrator — Multi-Stage Production Dockerfile
# =============================================================

FROM golang:1.22-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY orchestrator/ ./orchestrator/
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o orchestrator ./orchestrator/

FROM gcr.io/distroless/static-debian12
COPY --from=builder /app/orchestrator /orchestrator
USER nonroot:nonroot
ENTRYPOINT ["/orchestrator"]
