package handlers

import (
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
)

// Catalog lists imagery already indexed (and possibly pre-staged) for a bbox,
// so the UI can show which dates exist before a job is submitted. It reads
// the PostGIS catalog the batch pipeline maintains; it never calls NASA.
func (s *Server) Catalog(c *gin.Context) {
	parts := strings.Split(c.Query("bbox"), ",")
	if len(parts) != 4 {
		fail(c, http.StatusBadRequest, "bbox=lon_min,lat_min,lon_max,lat_max required")
		return
	}
	b := make([]float64, 4)
	for i, p := range parts {
		v, err := strconv.ParseFloat(strings.TrimSpace(p), 64)
		if err != nil {
			fail(c, http.StatusBadRequest, "bbox values must be numbers")
			return
		}
		b[i] = v
	}
	rows, err := s.DB.Query(c, `SELECT granule_id, sensor, acquired_at, cloud_pct, status
		FROM catalog_granules WHERE ST_Covers(footprint, ST_MakeEnvelope($1, $2, $3, $4, 4326))
		ORDER BY acquired_at DESC LIMIT 40`, b[0], b[1], b[2], b[3])
	if err != nil {
		fail(c, http.StatusInternalServerError, "database error")
		return
	}
	defer rows.Close()
	out := []gin.H{}
	for rows.Next() {
		var id, sensor, status string
		var at time.Time
		var cloud *float64
		if rows.Scan(&id, &sensor, &at, &cloud, &status) == nil {
			out = append(out, gin.H{"granule_id": id, "sensor": sensor, "acquired_at": at,
				"cloud_pct": cloud, "staged": status == "staged"})
		}
	}
	c.JSON(http.StatusOK, gin.H{"bbox": b, "granules": out})
}

// Coverage returns the footprints of EMIT acquisitions over the service region
// in a date window, as GeoJSON, so the map can show where hyperspectral data
// exists before a box is drawn. Only scenes a job could use are returned
// (cloud cover within EMIT_MAX_CLOUD). "staged" = already downloaded (instant).
func (s *Server) Coverage(c *gin.Context) {
	to := time.Now().UTC()
	from := to.AddDate(-1, 0, 0)
	for _, q := range []struct {
		key string
		dst *time.Time
	}{{"date_from", &from}, {"date_to", &to}} {
		if v := c.Query(q.key); v != "" {
			t, err := time.Parse("2006-01-02", v)
			if err != nil {
				fail(c, http.StatusBadRequest, q.key+" must be YYYY-MM-DD")
				return
			}
			*q.dst = t
		}
	}
	if !from.Before(to.AddDate(0, 0, 1)) || to.Sub(from) > 6*366*24*time.Hour {
		fail(c, http.StatusBadRequest, "date_from must be before date_to, at most 6 years apart")
		return
	}
	r := s.Cfg.AllowedRegion
	rows, err := s.DB.Query(c, `
		WITH region AS (SELECT ST_MakeEnvelope($1, $2, $3, $4, 4326) AS g)
		SELECT c.granule_id, c.acquired_at, c.cloud_pct,
		       (c.status = 'staged' AND c.updated_at > now() - interval '30 days') OR EXISTS (
		         SELECT 1 FROM job_sources js JOIN jobs j ON j.job_id = js.job_id
		         WHERE js.granule_id = c.granule_id AND j.created_at > now() - interval '30 days'),
		       ST_AsGeoJSON(ST_SimplifyPreserveTopology(ST_Intersection(c.footprint, region.g), 0.002), 4)
		FROM catalog_granules c, region
		WHERE c.sensor = 'EMIT' AND c.footprint && region.g
		  AND c.acquired_at >= $5 AND c.acquired_at < $6
		  AND (c.cloud_pct IS NULL OR c.cloud_pct <= $7)
		ORDER BY c.acquired_at DESC LIMIT 3000`,
		r[0], r[1], r[2], r[3], from, to.AddDate(0, 0, 1), s.Cfg.EmitMaxCloud)
	if err != nil {
		fail(c, http.StatusInternalServerError, "database error")
		return
	}
	defer rows.Close()
	features := []gin.H{}
	for rows.Next() {
		var id, geom string
		var at time.Time
		var cloud *float64
		var staged bool
		if rows.Scan(&id, &at, &cloud, &staged, &geom) != nil {
			continue
		}
		features = append(features, gin.H{"type": "Feature", "geometry": json.RawMessage(geom),
			"properties": gin.H{"granule_id": id, "acquired_at": at.Format("2006-01-02"), "cloud_pct": cloud, "staged": staged}})
	}
	// The merged area (what the map draws): one clear outline instead of hundreds of overlapping swaths.
	var union *string
	_ = s.DB.QueryRow(c, `
		WITH region AS (SELECT ST_MakeEnvelope($1, $2, $3, $4, 4326) AS g)
		SELECT ST_AsGeoJSON(ST_SimplifyPreserveTopology(ST_Intersection(ST_Union(c.footprint), region.g), 0.003), 4)
		FROM catalog_granules c, region
		WHERE c.sensor = 'EMIT' AND c.footprint && region.g
		  AND c.acquired_at >= $5 AND c.acquired_at < $6 AND (c.cloud_pct IS NULL OR c.cloud_pct <= $7)
		GROUP BY region.g`,
		r[0], r[1], r[2], r[3], from, to.AddDate(0, 0, 1), s.Cfg.EmitMaxCloud).Scan(&union)
	var area any
	if union != nil {
		area = json.RawMessage(*union)
	}
	var indexedAt *time.Time
	_ = s.DB.QueryRow(c, `SELECT max(updated_at) FROM catalog_granules WHERE sensor = 'EMIT'`).Scan(&indexedAt)
	c.Header("Cache-Control", "private, max-age=300")
	c.JSON(http.StatusOK, gin.H{"type": "FeatureCollection", "features": features, "max_cloud_pct": s.Cfg.EmitMaxCloud,
		"date_from": from.Format("2006-01-02"), "date_to": to.Format("2006-01-02"), "indexed_at": indexedAt,
		"covered_area": area})
}

// Model describes the active HydraNet version, including its verification
// against the classical methods (public: anyone can see how the maps are made).
func (s *Server) Model(c *gin.Context) {
	var version, hash string
	var card, verification []byte
	var promoted *time.Time
	err := s.DB.QueryRow(c, `SELECT version, feature_spec_hash, card, map_verification, promoted_at
		FROM model_versions WHERE active`).Scan(&version, &hash, &card, &verification, &promoted)
	if err != nil {
		c.JSON(http.StatusOK, gin.H{"active": false, "message": "no model promoted yet"})
		return
	}
	var cm, vm map[string]any
	json.Unmarshal(card, &cm)
	json.Unmarshal(verification, &vm)
	c.JSON(http.StatusOK, gin.H{"active": true, "version": version, "feature_spec_hash": hash,
		"promoted_at": promoted, "card": cm, "map_verification": vm})
}

// Stats feeds the dashboard: throughput, latency and outcomes over 24 h.
func (s *Server) Stats(c *gin.Context) {
	var active, queued, done24, failed24 int
	var p50, p95 *float64
	err := s.DB.QueryRow(c, `SELECT
		count(*) FILTER (WHERE status IN ('QUEUED','RUNNING')),
		count(*) FILTER (WHERE status = 'QUEUED'),
		count(*) FILTER (WHERE status IN ('COMPLETED','PARTIAL') AND completed_at > now() - interval '24 hours'),
		count(*) FILTER (WHERE status IN ('FAILED') AND completed_at > now() - interval '24 hours'),
		percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM completed_at - created_at))
			FILTER (WHERE status = 'COMPLETED' AND reused_from IS NULL AND completed_at > now() - interval '24 hours'),
		percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM completed_at - created_at))
			FILTER (WHERE status = 'COMPLETED' AND reused_from IS NULL AND completed_at > now() - interval '24 hours')
		FROM jobs`).Scan(&active, &queued, &done24, &failed24, &p50, &p95)
	if err != nil {
		fail(c, http.StatusInternalServerError, "database error")
		return
	}
	perTask := []gin.H{}
	rows, err := s.DB.Query(c, `SELECT task, state, count(*) FROM job_tasks t JOIN jobs j USING (job_id)
		WHERE j.created_at > now() - interval '24 hours' GROUP BY task, state ORDER BY task, state`)
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var task, state string
			var n int
			if rows.Scan(&task, &state, &n) == nil {
				perTask = append(perTask, gin.H{"task": task, "state": state, "count": n})
			}
		}
	}
	c.JSON(http.StatusOK, gin.H{"active_jobs": active, "queued_jobs": queued, "completed_24h": done24,
		"failed_24h": failed24, "latency_p50_s": p50, "latency_p95_s": p95, "tasks_24h": perTask,
		"model_version": s.activeModel(c), "generated_at": time.Now().UTC()})
}
