# Build context: repository root (go.mod lives there). Multi-arch: amd64 + arm64.
FROM --platform=$BUILDPLATFORM golang:1.26-alpine AS build
ARG TARGETOS TARGETARCH
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY pkg/ pkg/
COPY api_gateway/ api_gateway/
RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -trimpath -ldflags="-s -w" -o /out/api_gateway ./api_gateway

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/api_gateway /api_gateway
EXPOSE 8000
USER nonroot:nonroot
ENTRYPOINT ["/api_gateway"]
