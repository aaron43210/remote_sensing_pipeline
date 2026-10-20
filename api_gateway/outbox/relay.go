// Package outbox publishes rows of the transactional outbox to Kafka.
//
// A job and its Kafka message are written in ONE Postgres transaction (the
// outbox row), so a job can never exist without its message or the reverse.
// Every gateway replica runs a relay; FOR UPDATE SKIP LOCKED lets them share
// the work without double-sending. LISTEN/NOTIFY wakes the relay at once; the
// 1 s poll is the fallback.
package outbox

import (
	"context"
	"log/slog"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/segmentio/kafka-go"

	"remote_sensing_production/pkg/obs"
)

func Run(ctx context.Context, pool *pgxpool.Pool, w *kafka.Writer) {
	wake := make(chan struct{}, 1)
	go listen(ctx, pool, wake)
	tick := time.NewTicker(time.Second)
	defer tick.Stop()
	for {
		for drain(ctx, pool, w) {
		}
		select {
		case <-ctx.Done():
			return
		case <-tick.C:
		case <-wake:
		}
	}
}

func listen(ctx context.Context, pool *pgxpool.Pool, wake chan<- struct{}) {
	for ctx.Err() == nil {
		conn, err := pool.Acquire(ctx)
		if err != nil {
			time.Sleep(time.Second)
			continue
		}
		if _, err := conn.Exec(ctx, "LISTEN outbox"); err == nil {
			for {
				if _, err := conn.Conn().WaitForNotification(ctx); err != nil {
					break
				}
				select {
				case wake <- struct{}{}:
				default:
				}
			}
		}
		conn.Release()
	}
}

// drain sends one batch; returns true if there may be more.
func drain(ctx context.Context, pool *pgxpool.Pool, w *kafka.Writer) bool {
	tx, err := pool.Begin(ctx)
	if err != nil {
		return false
	}
	defer tx.Rollback(ctx)
	rows, err := tx.Query(ctx, `SELECT id, topic, key, payload::text FROM outbox
		WHERE sent_at IS NULL ORDER BY id LIMIT 100 FOR UPDATE SKIP LOCKED`)
	if err != nil {
		return false
	}
	var ids []int64
	var msgs []kafka.Message
	for rows.Next() {
		var id int64
		var topic, key, payload string
		if rows.Scan(&id, &topic, &key, &payload) == nil {
			ids = append(ids, id)
			msgs = append(msgs, kafka.Message{Topic: topic, Key: []byte(key), Value: []byte(payload)})
		}
	}
	rows.Close()
	if len(msgs) == 0 {
		obs.OutboxPending.Set(0)
		return false
	}
	wctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	if err := w.WriteMessages(wctx, msgs...); err != nil {
		slog.Error("outbox publish failed; will retry", "rows", len(msgs), "err", err)
		tx.Exec(ctx, `UPDATE outbox SET attempts = attempts + 1 WHERE id = ANY($1)`, ids)
		tx.Commit(ctx)
		time.Sleep(time.Second)
		return false
	}
	if _, err := tx.Exec(ctx, `UPDATE outbox SET sent_at = now() WHERE id = ANY($1)`, ids); err != nil {
		return false
	}
	if tx.Commit(ctx) != nil {
		return false
	}
	var pending int
	if pool.QueryRow(ctx, `SELECT count(*) FROM outbox WHERE sent_at IS NULL`).Scan(&pending) == nil {
		obs.OutboxPending.Set(float64(pending))
	}
	return len(msgs) == 100
}
