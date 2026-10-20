// Package jobsql holds SQL shared by the gateway and the orchestrator.
package jobsql

import (
	"context"

	"github.com/jackc/pgx/v5"
)

// CopyResults gives job dst the finished outputs of job src (same request).
func CopyResults(ctx context.Context, tx pgx.Tx, src, dst string) error {
	stmts := []string{
		`UPDATE job_tasks d SET stage = s.stage, state = s.state, message = s.message,
		   started_at = s.started_at, finished_at = s.finished_at, updated_at = now()
		 FROM job_tasks s WHERE s.job_id = $1 AND d.job_id = $2 AND d.task = s.task`,
		`INSERT INTO layers (job_id, task, name, tif_key, png_key, bounds, units, kind, colormap, vmin, vmax, classes, stats)
		 SELECT $2, task, name, tif_key, png_key, bounds, units, kind, colormap, vmin, vmax, classes, stats
		 FROM layers WHERE job_id = $1 ON CONFLICT DO NOTHING`,
		`INSERT INTO job_results (job_id, task, model_version, summary, verification, timings)
		 SELECT $2, task, model_version, summary, verification, timings FROM job_results WHERE job_id = $1
		 ON CONFLICT DO NOTHING`,
		`INSERT INTO job_sources (job_id, sensor, granule_id, acquired_at)
		 SELECT $2, sensor, granule_id, acquired_at FROM job_sources WHERE job_id = $1 ON CONFLICT DO NOTHING`,
		`UPDATE jobs d SET status = s.status, error = s.error, acquired_emit = s.acquired_emit,
		   acquired_landsat = s.acquired_landsat, completed_at = now(), updated_at = now()
		 FROM jobs s WHERE s.job_id = $1 AND d.job_id = $2`,
	}
	for _, q := range stmts {
		if _, err := tx.Exec(ctx, q, src, dst); err != nil {
			return err
		}
	}
	return nil
}
