// =============================================================
// OWNER: AARON
// SERVICE: Kafka Orchestrator (Golang) — Production Grade
// =============================================================
// Responsibilities:
//   - Consumes "ml-analyzed" Kafka topic (published by Python ML service)
//   - Writes COMPLETED status to Redis
//   - Updates scene status in PostgreSQL for audit log
//   - Handles graceful shutdown on SIGINT/SIGTERM
// =============================================================

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/segmentio/kafka-go"
	_ "github.com/lib/pq"
)

// MLAnalyzedMessage is the structure published by the Python ML service.
type MLAnalyzedMessage struct {
	SceneID           string   `json:"scene_id"`
	Timestamp         string   `json:"timestamp"`
	Shape             []int    `json:"shape"`
	ProcessingTimeSec float64  `json:"processing_time_sec"`
	InferenceMsPerPx  float64  `json:"inference_ms_per_px"`
	OutputLayers      []string `json:"output_layers"`
}

func main() {
	// ── Init Dependencies ─────────────────────────────────────
	brokers := getEnv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
	redisAddr := getEnv("REDIS_HOST", "redis:6379")
	dbURL := getEnv("DATABASE_URL", "postgres://spectral:spectral123@postgres:5432/hyperspectral_db?sslmode=disable")

	// Redis
	rdb := redis.NewClient(&redis.Options{
		Addr:        redisAddr,
		DialTimeout: 5 * time.Second,
		PoolSize:    10,
	})
	ctx := context.Background()
	if err := rdb.Ping(ctx).Err(); err != nil {
		log.Fatalf("Redis unreachable: %v", err)
	}
	log.Println("Orchestrator: Redis connected")

	// PostgreSQL
	db, err := sql.Open("postgres", dbURL)
	if err != nil {
		log.Fatalf("DB open failed: %v", err)
	}
	defer db.Close()
	db.SetMaxOpenConns(10)
	if err := db.Ping(); err != nil {
		log.Fatalf("DB ping failed: %v", err)
	}
	log.Println("Orchestrator: PostgreSQL connected")

	// Kafka Consumer
	reader := kafka.NewReader(kafka.ReaderConfig{
		Brokers:        []string{brokers},
		Topic:          "ml-analyzed",
		GroupID:        "go-orchestrator-group",
		MinBytes:       10e3,  // 10KB
		MaxBytes:       10e6,  // 10MB
		CommitInterval: time.Second,
		MaxWait:        5 * time.Second,
	})
	defer reader.Close()

	// ── Graceful Shutdown ─────────────────────────────────────
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

	msgCh := make(chan kafka.Message, 100)

	// Consumer goroutine
	go func() {
		for {
			msg, err := reader.FetchMessage(ctx)
			if err != nil {
				if ctx.Err() != nil {
					return
				}
				log.Printf("Kafka fetch error: %v", err)
				time.Sleep(time.Second)
				continue
			}
			msgCh <- msg
		}
	}()

	log.Println("Orchestrator: listening on kafka topic: ml-analyzed")

	for {
		select {
		case sig := <-sigCh:
			log.Printf("Orchestrator: received signal %v — shutting down", sig)
			return

		case msg := <-msgCh:
			if err := handleMessage(ctx, msg, rdb, db); err != nil {
				log.Printf("ERROR handling message: %v", err)
			} else {
				reader.CommitMessages(ctx, msg)
			}
		}
	}
}

// handleMessage processes one "ml-analyzed" Kafka message.
func handleMessage(ctx context.Context, msg kafka.Message, rdb *redis.Client, db *sql.DB) error {
	var payload MLAnalyzedMessage
	if err := json.Unmarshal(msg.Value, &payload); err != nil {
		return fmt.Errorf("unmarshal error: %w", err)
	}

	sceneID := payload.SceneID
	if sceneID == "" {
		return fmt.Errorf("missing scene_id in message")
	}

	log.Printf("Orchestrator: ML complete for scene=%s (%.2fs, %.4fms/px)",
		sceneID, payload.ProcessingTimeSec, payload.InferenceMsPerPx)

	// ── Write COMPLETED status to Redis ──────────────────────
	statusKey := fmt.Sprintf("status:%s", sceneID)
	if err := rdb.Set(ctx, statusKey, "COMPLETED", time.Hour).Err(); err != nil {
		return fmt.Errorf("redis write failed: %w", err)
	}

	// Store result metadata in Redis for fast retrieval
	resultKey := fmt.Sprintf("result:%s", sceneID)
	resultJSON, _ := json.Marshal(map[string]interface{}{
		"scene_id":            sceneID,
		"status":              "COMPLETED",
		"completed_at":        time.Now().UTC().Format(time.RFC3339),
		"processing_time_sec": payload.ProcessingTimeSec,
		"inference_ms_per_px": payload.InferenceMsPerPx,
		"output_layers":       payload.OutputLayers,
		"shape":               payload.Shape,
	})
	rdb.Set(ctx, resultKey, resultJSON, 24*time.Hour)

	// ── Update PostgreSQL for audit trail ─────────────────────
	_, dbErr := db.ExecContext(ctx,
		`UPDATE scenes SET status = 'COMPLETED', completed_at = NOW(),
		 processing_time_sec = $1 WHERE scene_id = $2`,
		payload.ProcessingTimeSec, sceneID,
	)
	if dbErr != nil {
		// Non-fatal: Redis is the source of truth for status
		log.Printf("WARN: DB update failed for %s: %v", sceneID, dbErr)
	}

	return nil
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
