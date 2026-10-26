// Package kafkax wraps segmentio/kafka-go with the platform's conventions:
// JSON payloads keyed by job_id, all-replica acks, consumer groups that
// commit only after a message was handled.
package kafkax

import (
	"context"
	"errors"
	"log/slog"
	"time"

	"github.com/segmentio/kafka-go"
)

func NewWriter(brokers []string) *kafka.Writer {
	return &kafka.Writer{
		Addr:                   kafka.TCP(brokers...),
		Balancer:               &kafka.Hash{}, // same job_id -> same partition -> ordered
		RequiredAcks:           kafka.RequireAll,
		MaxAttempts:            10,
		WriteTimeout:           10 * time.Second,
		BatchTimeout:           5 * time.Millisecond,
		AllowAutoTopicCreation: false,
		Compression:            kafka.Zstd,
	}
}

// Consume runs handler for every message of topic in group until ctx ends.
// The offset is committed after handler returns nil; a handler error is
// logged and the message is retried after a pause (the orchestrator's
// handlers are idempotent).
func Consume(ctx context.Context, brokers []string, group, topic string,
	handler func(context.Context, kafka.Message) error) {
	r := kafka.NewReader(kafka.ReaderConfig{
		Brokers: brokers, GroupID: group, Topic: topic,
		MinBytes: 1, MaxBytes: 10e6, MaxWait: 500 * time.Millisecond,
		CommitInterval: 0, StartOffset: kafka.FirstOffset,
	})
	defer r.Close()
	for {
		m, err := r.FetchMessage(ctx)
		if err != nil {
			if errors.Is(err, context.Canceled) || ctx.Err() != nil {
				return
			}
			slog.Error("kafka fetch", "topic", topic, "err", err)
			time.Sleep(time.Second)
			continue
		}
		for attempt := 1; ; attempt++ {
			if err = handler(ctx, m); err == nil || ctx.Err() != nil {
				break
			}
			slog.Error("handler failed", "topic", topic, "attempt", attempt, "err", err)
			if attempt >= 5 { // poison message: log it and move on, never block the partition
				slog.Error("dropping message after retries", "topic", topic, "value", string(m.Value))
				break
			}
			time.Sleep(time.Duration(attempt) * time.Second)
		}
		if err := r.CommitMessages(ctx, m); err != nil && ctx.Err() == nil {
			slog.Error("kafka commit", "topic", topic, "err", err)
		}
	}
}
