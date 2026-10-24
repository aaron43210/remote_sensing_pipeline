// API gateway: HTTP API, auth, admission control, outbox relay to Kafka.
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/redis/go-redis/v9"

	"remote_sensing_production/api_gateway/handlers"
	"remote_sensing_production/api_gateway/middleware"
	"remote_sensing_production/api_gateway/outbox"
	"remote_sensing_production/pkg/config"
	"remote_sensing_production/pkg/db"
	"remote_sensing_production/pkg/kafkax"
	"remote_sensing_production/pkg/obs"
	"remote_sensing_production/pkg/s3x"
	"remote_sensing_production/pkg/secretbox"
)

func main() {
	if len(os.Args) > 1 && os.Args[1] == "-healthcheck" { // distroless image: no shell, no curl
		resp, err := http.Get("http://127.0.0.1:" + config.Get("PORT", "8000") + "/health")
		if err != nil || resp.StatusCode != http.StatusOK {
			os.Exit(1)
		}
		os.Exit(0)
	}
	obs.SetupLogging("api_gateway")
	cfg := config.Load()
	if len(cfg.JWTSecret) < 32 {
		slog.Error("JWT_SECRET must be at least 32 characters (openssl rand -hex 32)")
		os.Exit(1)
	}
	box, err := secretbox.New(cfg.CredentialsKey)
	if err != nil {
		slog.Error(err.Error())
		os.Exit(1)
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	pool, err := db.Open(ctx, cfg.DatabaseURL, int32(config.GetInt("DB_MAX_CONNS", 30)))
	if err != nil {
		slog.Error("database", "err", err)
		os.Exit(1)
	}
	defer pool.Close()
	if err := db.Migrate(ctx, pool); err != nil {
		slog.Error("migrations", "err", err)
		os.Exit(1)
	}
	ropt, err := redis.ParseURL(cfg.RedisURL)
	if err != nil {
		slog.Error("redis url", "err", err)
		os.Exit(1)
	}
	ropt.PoolSize = config.GetInt("REDIS_POOL", 200) // one pub/sub conn per SSE client
	rdb := redis.NewClient(ropt)
	store, err := s3x.New(cfg.S3Bucket, cfg.S3Endpoint, cfg.S3PublicEndpoint, cfg.S3Region, cfg.S3ForcePathStyle)
	if err != nil {
		slog.Error("storage", "err", err)
		os.Exit(1)
	}
	writer := kafkax.NewWriter(cfg.KafkaBrokers)
	defer writer.Close()
	go outbox.Run(ctx, pool, writer)

	gin.SetMode(gin.ReleaseMode)
	if cfg.JWTPreviousSecret != "" && len(cfg.JWTPreviousSecret) < 32 {
		slog.Error("JWT_PREVIOUS_SECRET must be at least 32 characters")
		os.Exit(1)
	}
	srv := &handlers.Server{Cfg: cfg, DB: pool, Redis: rdb, S3: store,
		Keys: middleware.NewKeyRing(cfg.JWTSecret, cfg.JWTPreviousSecret), Box: box}
	httpSrv := &http.Server{
		Addr: ":" + cfg.Port, Handler: srv.Routes(),
		ReadHeaderTimeout: 10 * time.Second, ReadTimeout: 30 * time.Second,
		WriteTimeout: 0, // SSE streams stay open; per-route timeouts are handled in handlers
		IdleTimeout:  120 * time.Second, MaxHeaderBytes: 1 << 16,
	}
	go func() {
		slog.Info("listening", "port", cfg.Port)
		if err := httpSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			slog.Error("http server", "err", err)
			stop()
		}
	}()
	<-ctx.Done()
	slog.Info("shutting down")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	httpSrv.Shutdown(shutdownCtx)
}
