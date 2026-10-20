// Orchestrator: the only writer of job state.
//
//	job.status  (every service) -> job_tasks stage/state
//	ml.results  (ml_inference)  -> layers, job_results, task DONE
//	watchdog    (every 30 s)    -> TIMED_OUT for stages past their SLA
//
// After every change it recomputes the job's status, completes jobs that were
// coalesced onto it, sets the reuse key, and publishes to Redis so SSE
// clients on any gateway replica see the change at once. Handlers are
// idempotent (processed_events), so Kafka re-delivery is harmless.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
	"github.com/segmentio/kafka-go"

	"remote_sensing_production/pkg/config"
	"remote_sensing_production/pkg/contracts"
	"remote_sensing_production/pkg/db"
	"remote_sensing_production/pkg/jobsql"
	"remote_sensing_production/pkg/kafkax"
	"remote_sensing_production/pkg/keys"
	"remote_sensing_production/pkg/obs"
)

const group = "orchestrator"

var hyperspectralTasks = map[string]bool{"agriculture": true, "mineral": true}

type orch struct {
	cfg   config.Config
	db    *pgxpool.Pool
	redis *redis.Client
}

func main() {
	obs.SetupLogging("orchestrator")
	cfg := config.Load()
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	pool, err := db.Open(ctx, cfg.DatabaseURL, 20)
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
		slog.Error("redis", "err", err)
		os.Exit(1)
	}
	o := &orch{cfg: cfg, db: pool, redis: redis.NewClient(ropt)}
	obs.ServeMetrics(":" + config.Get("METRICS_PORT", "9100"))
	go kafkax.Consume(ctx, cfg.KafkaBrokers, group, contracts.TopicJobStatus, o.onStatus)
	go kafkax.Consume(ctx, cfg.KafkaBrokers, group, contracts.TopicMLResults, o.onResult)
	go o.watchdog(ctx)
	slog.Info("orchestrator running")
	<-ctx.Done()
}

// firstTime records the event; false means it was already handled.
func firstTime(ctx context.Context, tx pgx.Tx, eventID string) (bool, error) {
	if eventID == "" {
		return true, nil
	}
	tag, err := tx.Exec(ctx, `INSERT INTO processed_events (event_id, consumer) VALUES ($1, $2)
		ON CONFLICT DO NOTHING`, eventID, group)
	return tag.RowsAffected() == 1, err
}

func (o *orch) onStatus(ctx context.Context, m kafka.Message) error {
	var ev contracts.JobStatus
	if err := json.Unmarshal(m.Value, &ev); err != nil || ev.JobID == "" {
		slog.Error("bad job.status message", "value", string(m.Value))
		return nil // unparsable: never retry
	}
	tx, err := o.db.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if ok, err := firstTime(ctx, tx, ev.EventID); err != nil || !ok {
		return err
	}
	var status string
	var tasks []string
	if err := tx.QueryRow(ctx, `SELECT status, tasks FROM jobs WHERE job_id = $1 FOR UPDATE`, ev.JobID).
		Scan(&status, &tasks); err != nil {
		if err == pgx.ErrNoRows {
			return nil
		}
		return err
	}
	if status == "CANCELLED" || status == "COMPLETED" {
		return tx.Commit(ctx)
	}
	targets := []string{}
	if ev.Task != nil {
		targets = append(targets, *ev.Task)
	} else { // the hyperspectral branch reports for agriculture and mineral together
		for _, t := range tasks {
			if hyperspectralTasks[t] {
				targets = append(targets, t)
			}
		}
	}
	state := "RUNNING"
	final := false
	switch ev.State {
	case "ERROR", "NO_DATA":
		state, final = ev.State, true
	case "DONE":
		if ev.Stage == "done" {
			state, final = "DONE", true
		}
	}
	for _, t := range targets {
		_, err := tx.Exec(ctx, `UPDATE job_tasks SET stage = $3, state = $4, message = $5, updated_at = now(),
			started_at = COALESCE(started_at, now()), finished_at = CASE WHEN $6 THEN now() ELSE finished_at END
			WHERE job_id = $1 AND task = $2 AND state NOT IN ('DONE','ERROR','NO_DATA','TIMED_OUT','CANCELLED')`,
			ev.JobID, t, ev.Stage, state, ev.Message, final)
		if err != nil {
			return err
		}
	}
	followers, err := o.rollup(ctx, tx, ev.JobID)
	if err != nil {
		return err
	}
	if err := tx.Commit(ctx); err != nil {
		return err
	}
	o.publish(ctx, append(followers, ev.JobID)...)
	return nil
}

func (o *orch) onResult(ctx context.Context, m kafka.Message) error {
	var r contracts.MLResult
	if err := json.Unmarshal(m.Value, &r); err != nil || r.JobID == "" {
		slog.Error("bad ml.results message", "value", string(m.Value))
		return nil
	}
	tx, err := o.db.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if ok, err := firstTime(ctx, tx, r.EventID); err != nil || !ok {
		return err
	}
	var status string
	if err := tx.QueryRow(ctx, `SELECT status FROM jobs WHERE job_id = $1 FOR UPDATE`, r.JobID).Scan(&status); err != nil {
		if err == pgx.ErrNoRows {
			return nil
		}
		return err
	}
	if status == "CANCELLED" {
		return tx.Commit(ctx)
	}
	for _, l := range r.Layers {
		stats, _ := json.Marshal(l.Stats)
		if _, err := tx.Exec(ctx, `INSERT INTO layers (job_id, task, name, tif_key, png_key, bounds, units, kind,
			colormap, vmin, vmax, classes, stats) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
			ON CONFLICT (job_id, task, name) DO UPDATE SET tif_key = EXCLUDED.tif_key, png_key = EXCLUDED.png_key,
			bounds = EXCLUDED.bounds, stats = EXCLUDED.stats, vmin = EXCLUDED.vmin, vmax = EXCLUDED.vmax`,
			r.JobID, r.Task, l.Name, l.TifKey, l.PngKey, l.Bounds, l.Units, l.Kind, l.Colormap, l.VMin, l.VMax,
			l.Classes, stats); err != nil {
			return err
		}
	}
	summary, _ := json.Marshal(r.Summary)
	verification, _ := json.Marshal(r.Verification)
	timings, _ := json.Marshal(r.Timings)
	if _, err := tx.Exec(ctx, `INSERT INTO job_results (job_id, task, model_version, summary, verification, timings)
		VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (job_id, task) DO UPDATE SET model_version = EXCLUDED.model_version,
		summary = EXCLUDED.summary, verification = EXCLUDED.verification, timings = EXCLUDED.timings`,
		r.JobID, r.Task, r.ModelVersion, summary, verification, timings); err != nil {
		return err
	}
	sensor, col := "EMIT", "acquired_emit"
	if r.Task == "thermal" {
		sensor, col = "LANDSAT", "acquired_landsat"
	}
	if r.AcquiredAt != nil && *r.AcquiredAt != "" {
		tx.Exec(ctx, fmt.Sprintf(`UPDATE jobs SET %s = $2 WHERE job_id = $1`, col), r.JobID, *r.AcquiredAt)
	}
	if g, ok := r.Summary["granule_id"].(string); ok && g != "" {
		tx.Exec(ctx, `INSERT INTO job_sources (job_id, sensor, granule_id, acquired_at) VALUES ($1,$2,$3,$4)
			ON CONFLICT (job_id, sensor) DO NOTHING`, r.JobID, sensor, g, r.AcquiredAt)
	}
	if _, err := tx.Exec(ctx, `UPDATE jobs SET model_version = $2 WHERE job_id = $1`, r.JobID, r.ModelVersion); err != nil {
		return err
	}
	if _, err := tx.Exec(ctx, `UPDATE job_tasks SET stage = 'done', state = 'DONE', message = '', updated_at = now(),
		finished_at = now() WHERE job_id = $1 AND task = $2`, r.JobID, r.Task); err != nil {
		return err
	}
	followers, err := o.rollup(ctx, tx, r.JobID)
	if err != nil {
		return err
	}
	if err := tx.Commit(ctx); err != nil {
		return err
	}
	o.publish(ctx, append(followers, r.JobID)...)
	return nil
}

// rollup derives the job status from its tasks and finalises when all are done.
func (o *orch) rollup(ctx context.Context, tx pgx.Tx, jobID string) ([]string, error) {
	rows, err := tx.Query(ctx, `SELECT task, stage, state, message FROM job_tasks WHERE job_id = $1`, jobID)
	if err != nil {
		return nil, err
	}
	counts := map[string]int{}
	var msgs []string
	n, started := 0, false
	for rows.Next() {
		var task, stage, state, msg string
		if err := rows.Scan(&task, &stage, &state, &msg); err != nil {
			rows.Close()
			return nil, err
		}
		n++
		counts[state]++
		if stage != "queued" || state != "PENDING" {
			started = true
		}
		if (state == "ERROR" || state == "TIMED_OUT" || state == "NO_DATA") && msg != "" {
			msgs = append(msgs, task+": "+msg)
		}
	}
	rows.Close()
	finished := counts["DONE"] + counts["ERROR"] + counts["NO_DATA"] + counts["TIMED_OUT"] + counts["CANCELLED"]
	status := "QUEUED"
	if started {
		status = "RUNNING"
	}
	if n > 0 && finished == n {
		switch {
		case counts["DONE"] == n:
			status = "COMPLETED"
		case counts["DONE"] > 0:
			status = "PARTIAL"
		case counts["NO_DATA"] == n:
			status = "NO_DATA"
		default:
			status = "FAILED"
		}
	}
	var errText any
	if len(msgs) > 0 {
		errText = strings.Join(msgs, "; ")
	}
	var prev, reuseKey string
	var created time.Time
	if err := tx.QueryRow(ctx, `SELECT status, COALESCE(reuse_key, ''), created_at FROM jobs WHERE job_id = $1`,
		jobID).Scan(&prev, &reuseKey, &created); err != nil {
		return nil, err
	}
	isFinal := status != "QUEUED" && status != "RUNNING"
	var followers []string
	if _, err := tx.Exec(ctx, `UPDATE jobs SET status = $2, error = $3, updated_at = now(),
		completed_at = CASE WHEN $4 THEN now() ELSE completed_at END WHERE job_id = $1`,
		jobID, status, errText, isFinal); err != nil {
		return nil, err
	}
	if isFinal && prev != status {
		obs.JobsFinished.WithLabelValues(status).Inc()
		obs.JobDuration.Observe(time.Since(created).Seconds())
		var err error
		if followers, err = o.finishFollowers(ctx, tx, jobID); err != nil {
			return nil, err
		}
		if reuseKey != "" {
			o.redis.Del(ctx, keys.Inflight(reuseKey))
			if status == "COMPLETED" {
				o.redis.Set(ctx, keys.Reuse(reuseKey), jobID, 7*24*time.Hour)
			}
		}
	}
	return followers, nil
}

// finishFollowers completes jobs that were coalesced onto this one.
func (o *orch) finishFollowers(ctx context.Context, tx pgx.Tx, jobID string) ([]string, error) {
	rows, err := tx.Query(ctx, `SELECT job_id FROM jobs WHERE reused_from = $1 AND status IN ('QUEUED','RUNNING')`, jobID)
	if err != nil {
		return nil, err
	}
	var ids []string
	for rows.Next() {
		var id string
		if rows.Scan(&id) == nil {
			ids = append(ids, id)
		}
	}
	rows.Close()
	for _, id := range ids {
		if err := jobsql.CopyResults(ctx, tx, jobID, id); err != nil {
			return nil, err
		}
	}
	return ids, nil
}

func (o *orch) publish(ctx context.Context, jobIDs ...string) {
	for _, id := range jobIDs {
		o.redis.Publish(ctx, keys.Events(id), `{"type":"status"}`)
	}
}

// watchdog fails tasks stuck in a stage past its SLA, so no job hangs forever.
func (o *orch) watchdog(ctx context.Context) {
	t := time.NewTicker(30 * time.Second)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
		var active int
		if o.db.QueryRow(ctx, `SELECT count(*) FROM jobs WHERE status IN ('QUEUED','RUNNING')`).Scan(&active) == nil {
			obs.QueueDepth.Set(float64(active))
		}
		o.finishOrphanedFollowers(ctx)
		for stage, sla := range o.cfg.StageTimeouts {
			rows, err := o.db.Query(ctx, `SELECT DISTINCT t.job_id FROM job_tasks t JOIN jobs j USING (job_id)
				WHERE j.status IN ('QUEUED','RUNNING') AND j.reused_from IS NULL AND t.stage = $1
				AND t.state IN ('PENDING','RUNNING') AND t.updated_at < now() - $2::interval`,
				stage, fmt.Sprintf("%d seconds", int(sla.Seconds())))
			if err != nil {
				continue
			}
			var ids []string
			for rows.Next() {
				var id string
				if rows.Scan(&id) == nil {
					ids = append(ids, id)
				}
			}
			rows.Close()
			for _, id := range ids {
				o.timeout(ctx, id, stage, sla)
			}
		}
	}
}

// finishOrphanedFollowers completes coalesced jobs whose original finished in
// the instant between the gateway reading the in-flight key and committing the
// follower (rollup had already run, so finishFollowers could not see them).
func (o *orch) finishOrphanedFollowers(ctx context.Context) {
	rows, err := o.db.Query(ctx, `SELECT f.job_id, f.reused_from FROM jobs f JOIN jobs src ON src.job_id = f.reused_from
		WHERE f.status IN ('QUEUED','RUNNING')
		AND src.status IN ('COMPLETED','PARTIAL','FAILED','NO_DATA','CANCELLED')`)
	if err != nil {
		return
	}
	type pair struct{ follower, src string }
	var todo []pair
	for rows.Next() {
		var p pair
		if rows.Scan(&p.follower, &p.src) == nil {
			todo = append(todo, p)
		}
	}
	rows.Close()
	for _, p := range todo {
		tx, err := o.db.Begin(ctx)
		if err != nil {
			return
		}
		if jobsql.CopyResults(ctx, tx, p.src, p.follower) == nil && tx.Commit(ctx) == nil {
			slog.Info("finished orphaned follower", "job_id", p.follower, "from", p.src)
			o.publish(ctx, p.follower)
		} else {
			tx.Rollback(ctx)
		}
	}
}

func (o *orch) timeout(ctx context.Context, jobID, stage string, sla time.Duration) {
	tx, err := o.db.Begin(ctx)
	if err != nil {
		return
	}
	defer tx.Rollback(ctx)
	msg := fmt.Sprintf("no progress in stage %q for %s", stage, sla)
	if _, err := tx.Exec(ctx, `UPDATE job_tasks SET state = 'TIMED_OUT', message = $3, finished_at = now(),
		updated_at = now() WHERE job_id = $1 AND stage = $2 AND state IN ('PENDING','RUNNING')`,
		jobID, stage, msg); err != nil {
		return
	}
	followers, err := o.rollup(ctx, tx, jobID)
	if err != nil || tx.Commit(ctx) != nil {
		return
	}
	slog.Warn("task timed out", "job_id", jobID, "stage", stage)
	o.publish(ctx, append(followers, jobID)...)
}
