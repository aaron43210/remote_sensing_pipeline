// =============================================================
// OWNER: AARON
// FILE: handlers/deps.go — Dependency injection container
// =============================================================
// All external connections (Kafka, Redis, MinIO, DB) are
// initialized once here and shared across all route handlers.
// =============================================================

package handlers

import (
	"context"
	"database/sql"
	"fmt"
	"log"
	"os"
	"time"

	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
	"github.com/redis/go-redis/v9"
	"github.com/segmentio/kafka-go"
	_ "github.com/lib/pq"
)

// Deps holds all shared dependencies.
type Deps struct {
	Kafka  *kafka.Writer
	Redis  *redis.Client
	MinIO  *minio.Client
	DB     *sql.DB
}

// NewDeps initializes all connections. Fails fast if any dependency is unreachable.
func NewDeps() (*Deps, error) {
	// ── Kafka Producer ────────────────────────────────────────
	brokers := os.Getenv("KAFKA_BOOTSTRAP_SERVERS")
	if brokers == "" {
		brokers = "kafka:29092"
	}
	kafkaWriter := &kafka.Writer{
		Addr:         kafka.TCP(brokers),
		Balancer:     &kafka.LeastBytes{},
		RequiredAcks: kafka.RequireOne,
		MaxAttempts:  5,
		WriteTimeout: 10 * time.Second,
	}

	// ── Redis ─────────────────────────────────────────────────
	redisAddr := os.Getenv("REDIS_HOST")
	if redisAddr == "" {
		redisAddr = "redis:6379"
	}
	rdb := redis.NewClient(&redis.Options{
		Addr:         redisAddr,
		DialTimeout:  5 * time.Second,
		ReadTimeout:  3 * time.Second,
		WriteTimeout: 3 * time.Second,
		PoolSize:     20,
	})
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := rdb.Ping(ctx).Err(); err != nil {
		return nil, fmt.Errorf("redis unreachable: %w", err)
	}
	log.Println("Redis connected")

	// ── MinIO ─────────────────────────────────────────────────
	minioEndpoint := os.Getenv("MINIO_ENDPOINT")
	if minioEndpoint == "" {
		minioEndpoint = "minio:9000"
	}
	minioClient, err := minio.New(minioEndpoint, &minio.Options{
		Creds:  credentials.NewStaticV4(os.Getenv("MINIO_ACCESS_KEY"), os.Getenv("MINIO_SECRET_KEY"), ""),
		Secure: false,
	})
	if err != nil {
		return nil, fmt.Errorf("minio init failed: %w", err)
	}
	log.Println("MinIO client initialized")

	// ── PostgreSQL ────────────────────────────────────────────
	dbURL := os.Getenv("DATABASE_URL")
	if dbURL == "" {
		dbURL = "postgres://spectral:spectral123@postgres:5432/hyperspectral_db?sslmode=disable"
	}
	db, err := sql.Open("postgres", dbURL)
	if err != nil {
		return nil, fmt.Errorf("postgres open failed: %w", err)
	}
	db.SetMaxOpenConns(25)
	db.SetMaxIdleConns(10)
	db.SetConnMaxLifetime(5 * time.Minute)
	if err := db.Ping(); err != nil {
		return nil, fmt.Errorf("postgres unreachable: %w", err)
	}
	log.Println("PostgreSQL connected")

	return &Deps{
		Kafka: kafkaWriter,
		Redis: rdb,
		MinIO: minioClient,
		DB:    db,
	}, nil
}

// Close gracefully shuts down all connections.
func (d *Deps) Close() {
	if d.Kafka != nil {
		d.Kafka.Close()
	}
	if d.Redis != nil {
		d.Redis.Close()
	}
	if d.DB != nil {
		d.DB.Close()
	}
}
