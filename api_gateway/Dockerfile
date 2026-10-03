# =============================================================
# OWNER: AARON
# API Gateway — Multi-Stage Production Dockerfile
# Final image: ~12MB (scratch-based Go binary)
# =============================================================

# Stage 1: Build the Go binary
FROM golang:1.22-alpine AS builder

WORKDIR /app

# Cache dependencies first
COPY go.mod go.sum ./
RUN go mod download

# Copy source code
COPY . .

# Build a static binary (no CGO = no libc dependencies)
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -ldflags="-s -w" -o api_gateway ./api_gateway/

# Stage 2: Minimal production image (~12MB vs ~300MB full Go image)
FROM gcr.io/distroless/static-debian12

WORKDIR /

COPY --from=builder /app/api_gateway /api_gateway

EXPOSE 8000

USER nonroot:nonroot

ENTRYPOINT ["/api_gateway"]
