# Build context: repository root. Test-only fake Google (OpenID Connect) for CI.
FROM golang:1.26.10-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY tests/mockoidc/ tests/mockoidc/
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/mockoidc ./tests/mockoidc

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/mockoidc /mockoidc
EXPOSE 9998
USER nonroot:nonroot
ENTRYPOINT ["/mockoidc"]
