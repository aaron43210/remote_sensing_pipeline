package handlers

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"remote_sensing_production/api_gateway/middleware"
	"remote_sensing_production/pkg/contracts"
	"remote_sensing_production/pkg/jobsql"
	"remote_sensing_production/pkg/keys"
	"remote_sensing_production/pkg/obs"
)

type createJob struct {
	BBox     []float64 `json:"bbox" binding:"required,len=4"`
	Tasks    []string  `json:"tasks" binding:"required,min=1,max=3"`
	DateFrom string    `json:"date_from"`
	DateTo   string    `json:"date_to"`
	Source   string    `json:"source"`
}

var stageWeight = map[string]float64{"queued": 0, "ingest": 25, "preprocess": 55, "inference": 80, "done": 100}

var finalStates = map[string]bool{"COMPLETED": true, "PARTIAL": true, "FAILED": true, "NO_DATA": true, "CANCELLED": true}

type TaskState struct {
	Task       string     `json:"task"`
	Stage      string     `json:"stage"`
	State      string     `json:"state"`
	Message    string     `json:"message"`
	StartedAt  *time.Time `json:"started_at"`
	FinishedAt *time.Time `json:"finished_at"`
}

type JobView struct {
	JobID           string      `json:"job_id"`
	Status          string      `json:"status"`
	Error           *string     `json:"error"`
	BBox            []float64   `json:"bbox"`
	Tasks           []string    `json:"tasks"`
	DateFrom        *string     `json:"date_from"`
	DateTo          *string     `json:"date_to"`
	Source          string      `json:"source"`
	ModelVersion    *string     `json:"model_version"`
	ReusedFrom      *string     `json:"reused_from"`
	AcquiredEMIT    *time.Time  `json:"acquired_emit"`
	AcquiredLandsat *time.Time  `json:"acquired_landsat"`
	CreatedAt       time.Time   `json:"created_at"`
	UpdatedAt       time.Time   `json:"updated_at"`
	CompletedAt     *time.Time  `json:"completed_at"`
	TaskStates      []TaskState `json:"task_states"`
	Progress        float64     `json:"progress"`
	QueuePosition   *int        `json:"queue_position,omitempty"`
	Final           bool        `json:"final"`
}

func validTask(t string) bool {
	for _, x := range contracts.Tasks {
		if x == t {
			return true
		}
	}
	return false
}

func (s *Server) validate(req *createJob) error {
	b := req.BBox
	for _, v := range b {
		if math.IsNaN(v) || math.IsInf(v, 0) {
			return errors.New("bbox values must be numbers")
		}
	}
	if b[0] >= b[2] || b[1] >= b[3] {
		return errors.New("bbox must be [lon_min, lat_min, lon_max, lat_max] with min < max")
	}
	if b[2]-b[0] > s.Cfg.MaxBBoxDeg+1e-9 || b[3]-b[1] > s.Cfg.MaxBBoxDeg+1e-9 {
		return fmt.Errorf("bbox too large: at most %.2f degrees per side", s.Cfg.MaxBBoxDeg)
	}
	if b[2]-b[0] < 0.002 || b[3]-b[1] < 0.002 {
		return errors.New("bbox too small: at least 0.002 degrees per side")
	}
	r := s.Cfg.AllowedRegion
	if b[0] < r[0] || b[1] < r[1] || b[2] > r[2] || b[3] > r[3] {
		return fmt.Errorf("bbox outside the supported region %v", r)
	}
	seen := map[string]bool{}
	clean := []string{}
	for _, t := range req.Tasks {
		t = strings.ToLower(strings.TrimSpace(t))
		if !validTask(t) {
			return fmt.Errorf("unknown task %q (agriculture, mineral, thermal)", t)
		}
		if !seen[t] {
			seen[t] = true
			clean = append(clean, t)
		}
	}
	sort.Strings(clean)
	req.Tasks = clean
	for _, d := range []*string{&req.DateFrom, &req.DateTo} {
		if *d != "" {
			if _, err := time.Parse("2006-01-02", *d); err != nil {
				return errors.New("dates must be YYYY-MM-DD")
			}
		}
	}
	if req.DateFrom != "" && req.DateTo != "" && req.DateFrom > req.DateTo {
		return errors.New("date_from must be before date_to")
	}
	if req.Source == "" {
		req.Source = "nasa"
	}
	if req.Source != "nasa" && !(req.Source == "fixture" && s.Cfg.AllowFixtures) {
		return errors.New("source must be 'nasa'")
	}
	return nil
}

func (s *Server) activeModel(ctx context.Context) string {
	var v string
	if err := s.DB.QueryRow(ctx, `SELECT version FROM model_versions WHERE active`).Scan(&v); err != nil {
		return "none"
	}
	return v
}

// emitCandidates lists, clearest first then newest (the order ingestion picks in),
// the catalogued EMIT scenes that fully cover the box in the job's dates. They
// travel with the request so ingestion still works while NASA's search is down.
// Best effort: an empty catalog or a query error just means no fallback.
func (s *Server) emitCandidates(ctx context.Context, req createJob) []contracts.EmitCandidate {
	if !needsEMIT(req) {
		return nil
	}
	to := time.Now().UTC()
	if t, err := time.Parse("2006-01-02", req.DateTo); err == nil {
		to = t
	}
	from := to.AddDate(-1, 0, 0)
	if t, err := time.Parse("2006-01-02", req.DateFrom); err == nil {
		from = t
	}
	b := req.BBox
	rows, err := s.DB.Query(ctx, `SELECT granule_id, coalesce(url, ''), acquired_at, cloud_pct FROM catalog_granules
		WHERE sensor = 'EMIT' AND ST_Covers(footprint, ST_MakeEnvelope($1, $2, $3, $4, 4326))
		  AND acquired_at >= $5 AND acquired_at < $6 AND (cloud_pct IS NULL OR cloud_pct <= $7)
		ORDER BY round(coalesce(cloud_pct, 0) / 10), acquired_at DESC LIMIT 5`,
		b[0], b[1], b[2], b[3], from, to.AddDate(0, 0, 1), s.Cfg.EmitMaxCloud)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []contracts.EmitCandidate
	for rows.Next() {
		var c contracts.EmitCandidate
		var at time.Time
		if rows.Scan(&c.GranuleID, &c.URL, &at, &c.CloudCover) == nil {
			c.AcquiredAt = at.UTC().Format(time.RFC3339)
			out = append(out, c)
		}
	}
	return out
}

// needsEMIT: the job maps agriculture or minerals from real NASA EMIT imagery.
func needsEMIT(req createJob) bool {
	return req.Source == "nasa" && (contains(req.Tasks, "agriculture") || contains(req.Tasks, "mineral"))
}

func contains(xs []string, x string) bool {
	for _, v := range xs {
		if v == x {
			return true
		}
	}
	return false
}

// modelIdentity names the exact active model for result reuse: version, feature
// spec and promotion time, so re-promoting a version (or a new model under an old
// name) never serves results computed by a different network.
func (s *Server) modelIdentity(ctx context.Context) string {
	var v, hash string
	var at *time.Time
	if err := s.DB.QueryRow(ctx, `SELECT version, feature_spec_hash, promoted_at FROM model_versions WHERE active`).
		Scan(&v, &hash, &at); err != nil {
		return "none"
	}
	promoted := int64(0)
	if at != nil {
		promoted = at.Unix()
	}
	return fmt.Sprintf("%s|%s|%d", v, hash, promoted)
}

// requestHash identifies "the same request": same area (to ~10 m), tasks,
// dates, source and model identity. Identical requests share one computation.
func requestHash(req createJob, model string) string {
	r := func(v float64) string { return strconv.FormatFloat(math.Round(v*1e4)/1e4, 'f', 4, 64) }
	key := strings.Join([]string{r(req.BBox[0]), r(req.BBox[1]), r(req.BBox[2]), r(req.BBox[3]),
		strings.Join(req.Tasks, ","), req.DateFrom, req.DateTo, req.Source, model}, "|")
	h := sha256.Sum256([]byte(key))
	return hex.EncodeToString(h[:16])
}

func nullable(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func (s *Server) CreateJob(c *gin.Context) {
	var req createJob
	if err := c.ShouldBindJSON(&req); err != nil {
		fail(c, http.StatusBadRequest, "body must be {bbox:[lon_min,lat_min,lon_max,lat_max], tasks:[...]}")
		return
	}
	if err := s.validate(&req); err != nil {
		fail(c, http.StatusBadRequest, err.Error())
		return
	}
	ctx := c.Request.Context()
	user := middleware.UserID(c)
	uid := middleware.UserNum(c)

	// Idempotency: a retried POST (double click, network retry) returns the first job.
	idemKey := c.GetHeader("Idempotency-Key")
	if idemKey != "" && len(idemKey) <= 100 {
		if prev, err := s.Redis.Get(ctx, keys.Idem(user, idemKey)).Result(); err == nil {
			s.respondJob(c, http.StatusOK, prev)
			return
		}
	}

	// Admission control
	var userActive, globalActive int
	if err := s.DB.QueryRow(ctx, `SELECT
		count(*) FILTER (WHERE user_id = $1), count(*)
		FROM jobs WHERE status IN ('QUEUED','RUNNING') AND reused_from IS NULL`, uid).
		Scan(&userActive, &globalActive); err != nil {
		fail(c, http.StatusInternalServerError, "database error")
		return
	}
	if userActive >= s.Cfg.MaxActiveJobsPerUser {
		obs.JobsSubmitted.WithLabelValues("user_quota").Inc()
		c.Header("Retry-After", "30")
		fail(c, http.StatusTooManyRequests,
			fmt.Sprintf("you already have %d jobs running; wait for one to finish", userActive))
		return
	}
	if globalActive >= s.Cfg.MaxQueuedJobs {
		obs.JobsSubmitted.WithLabelValues("busy").Inc()
		c.Header("Retry-After", "60")
		fail(c, http.StatusServiceUnavailable, "the platform is at capacity; please retry in a minute")
		return
	}

	model := s.activeModel(ctx)
	hash := requestHash(req, s.modelIdentity(ctx))
	jobID := "job_" + strings.ReplaceAll(uuid.NewString(), "-", "")[:20]

	// Reuse a finished identical job, or attach to one that is running.
	var reusedFrom, initial string = "", "QUEUED"
	if orig, err := s.Redis.Get(ctx, keys.Reuse(hash)).Result(); err == nil {
		reusedFrom, initial = orig, "COMPLETED"
	} else if orig, err := s.Redis.Get(ctx, keys.Inflight(hash)).Result(); err == nil {
		reusedFrom = orig
	}

	tx, err := s.DB.Begin(ctx)
	if err != nil {
		fail(c, http.StatusInternalServerError, "database error")
		return
	}
	defer tx.Rollback(ctx)
	b := req.BBox
	_, err = tx.Exec(ctx, `INSERT INTO jobs (job_id, user_id, bbox, bbox_arr, tasks, date_from, date_to, source,
		status, model_version, reuse_key, reused_from, completed_at)
		VALUES ($1, $2, ST_MakeEnvelope($3, $4, $5, $6, 4326), $7, $8, $9, $10, $11, $12, $13, $14, $15,
		        CASE WHEN $12 = 'COMPLETED' THEN now() END)`,
		jobID, uid, b[0], b[1], b[2], b[3], b, req.Tasks, nullable(req.DateFrom), nullable(req.DateTo),
		req.Source, initial, model, hash, nullable(reusedFrom))
	if err != nil {
		fail(c, http.StatusInternalServerError, "could not create job")
		return
	}
	for _, t := range req.Tasks {
		if _, err := tx.Exec(ctx, `INSERT INTO job_tasks (job_id, task) VALUES ($1, $2)`, jobID, t); err != nil {
			fail(c, http.StatusInternalServerError, "could not create job")
			return
		}
	}
	switch {
	case initial == "COMPLETED":
		if err := jobsql.CopyResults(ctx, tx, reusedFrom, jobID); err != nil {
			fail(c, http.StatusInternalServerError, "could not reuse results")
			return
		}
		obs.JobsSubmitted.WithLabelValues("reused").Inc()
	case reusedFrom != "":
		obs.JobsSubmitted.WithLabelValues("coalesced").Inc() // the orchestrator copies results when the original finishes
	default:
		if needsEMIT(req) { // reused and attached jobs download nothing, so they need no token
			if err := s.handOverEarthdata(ctx, uid, jobID); errors.Is(err, errNoEarthdata) {
				c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{"error": err.Error(), "code": "earthdata_required"})
				return
			} else if err != nil {
				fail(c, http.StatusInternalServerError, "could not use your NASA Earthdata connection")
				return
			}
		}
		msg := contracts.IngestRequest{SchemaVersion: contracts.SchemaVersion, EventID: uuid.NewString(),
			JobID: jobID, TS: time.Now().UTC().Format(time.RFC3339Nano), UserID: user, BBox: b,
			Tasks: req.Tasks, DateFrom: req.DateFrom, DateTo: req.DateTo, Source: req.Source,
			EmitCandidates: s.emitCandidates(ctx, req)}
		payload, _ := json.Marshal(msg)
		if _, err := tx.Exec(ctx, `INSERT INTO outbox (topic, key, payload) VALUES ($1, $2, $3)`,
			contracts.TopicIngestRequests, jobID, payload); err != nil {
			fail(c, http.StatusInternalServerError, "could not queue job")
			return
		}
		obs.JobsSubmitted.WithLabelValues("queued").Inc()
	}
	if err := tx.Commit(ctx); err != nil {
		fail(c, http.StatusInternalServerError, "could not create job")
		return
	}
	if reusedFrom == "" {
		s.Redis.Set(ctx, keys.Inflight(hash), jobID, 2*time.Hour)
	} else if initial == "QUEUED" {
		// The original may have finished between reading the in-flight key and our commit.
		var src string
		if s.DB.QueryRow(ctx, `SELECT status FROM jobs WHERE job_id = $1`, reusedFrom).Scan(&src) == nil && finalStates[src] {
			if tx2, err := s.DB.Begin(ctx); err == nil {
				if jobsql.CopyResults(ctx, tx2, reusedFrom, jobID) == nil {
					tx2.Commit(ctx)
				} else {
					tx2.Rollback(ctx)
				}
			}
		}
	}
	if idemKey != "" {
		s.Redis.Set(ctx, keys.Idem(user, idemKey), jobID, 24*time.Hour)
	}
	s.respondJob(c, http.StatusAccepted, jobID)
}

func (s *Server) respondJob(c *gin.Context, code int, jobID string) {
	v, err := s.loadJob(c.Request.Context(), jobID, middleware.UserID(c), middleware.IsAdmin(c))
	if err != nil {
		fail(c, http.StatusNotFound, "job not found")
		return
	}
	c.JSON(code, v)
}

// LoadJob is used by the handlers and by SSE.
func (s *Server) loadJob(ctx context.Context, jobID, user string, admin bool) (*JobView, error) {
	v := &JobView{}
	var owner string
	err := s.DB.QueryRow(ctx, `SELECT job_id, user_id::text, status, error, bbox_arr, tasks,
		to_char(date_from, 'YYYY-MM-DD'), to_char(date_to, 'YYYY-MM-DD'), source, model_version, reused_from,
		acquired_emit, acquired_landsat, created_at, updated_at, completed_at FROM jobs WHERE job_id = $1`, jobID).
		Scan(&v.JobID, &owner, &v.Status, &v.Error, &v.BBox, &v.Tasks, &v.DateFrom, &v.DateTo, &v.Source,
			&v.ModelVersion, &v.ReusedFrom, &v.AcquiredEMIT, &v.AcquiredLandsat, &v.CreatedAt, &v.UpdatedAt,
			&v.CompletedAt)
	if err != nil {
		return nil, err
	}
	if owner != user && !admin {
		return nil, pgx.ErrNoRows // never reveal that someone else's job exists
	}
	rows, err := s.DB.Query(ctx, `SELECT task, stage, state, message, started_at, finished_at
		FROM job_tasks WHERE job_id = $1 ORDER BY task`, jobID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	total := 0.0
	for rows.Next() {
		var t TaskState
		if err := rows.Scan(&t.Task, &t.Stage, &t.State, &t.Message, &t.StartedAt, &t.FinishedAt); err != nil {
			return nil, err
		}
		w := stageWeight[t.Stage]
		if t.State == "DONE" || t.State == "NO_DATA" || t.State == "ERROR" || t.State == "TIMED_OUT" ||
			t.State == "CANCELLED" {
			w = 100
		}
		total += w
		v.TaskStates = append(v.TaskStates, t)
	}
	if n := len(v.TaskStates); n > 0 {
		v.Progress = math.Round(total / float64(n))
	}
	v.Final = finalStates[v.Status]
	if v.Status == "QUEUED" {
		var pos int
		if s.DB.QueryRow(ctx, `SELECT count(*) FROM jobs WHERE status IN ('QUEUED','RUNNING')
			AND reused_from IS NULL AND created_at < $1`, v.CreatedAt).Scan(&pos) == nil {
			pos++
			v.QueuePosition = &pos
		}
	}
	return v, nil
}

func (s *Server) GetJob(c *gin.Context) { s.respondJob(c, http.StatusOK, c.Param("id")) }

func (s *Server) ListJobs(c *gin.Context) {
	limit, _ := strconv.Atoi(c.DefaultQuery("limit", "20"))
	if limit < 1 || limit > 100 {
		limit = 20
	}
	before := c.Query("before") // created_at cursor (RFC3339)
	if before == "" {
		before = time.Now().Add(time.Hour).UTC().Format(time.RFC3339Nano)
	}
	rows, err := s.DB.Query(c, `SELECT job_id, status, bbox_arr, tasks, source, created_at, completed_at
		FROM jobs WHERE user_id = $1 AND created_at < $2 ORDER BY created_at DESC LIMIT $3`,
		middleware.UserNum(c), before, limit)
	if err != nil {
		fail(c, http.StatusInternalServerError, "database error")
		return
	}
	defer rows.Close()
	out := []gin.H{}
	var last time.Time
	for rows.Next() {
		var (
			id, status, source string
			bbox               []float64
			tasks              []string
			created            time.Time
			completed          *time.Time
		)
		if err := rows.Scan(&id, &status, &bbox, &tasks, &source, &created, &completed); err != nil {
			fail(c, http.StatusInternalServerError, "database error")
			return
		}
		last = created
		out = append(out, gin.H{"job_id": id, "status": status, "bbox": bbox, "tasks": tasks, "source": source,
			"created_at": created, "completed_at": completed, "final": finalStates[status]})
	}
	resp := gin.H{"jobs": out}
	if len(out) == limit {
		resp["next_before"] = last.UTC().Format(time.RFC3339Nano)
	}
	c.JSON(http.StatusOK, resp)
}

func (s *Server) CancelJob(c *gin.Context) {
	ctx := c.Request.Context()
	id := c.Param("id")
	v, err := s.loadJob(ctx, id, middleware.UserID(c), middleware.IsAdmin(c))
	if err != nil {
		fail(c, http.StatusNotFound, "job not found")
		return
	}
	if v.Final {
		fail(c, http.StatusConflict, "job already finished")
		return
	}
	if _, err := s.DB.Exec(ctx, `UPDATE jobs SET status = 'CANCELLED', updated_at = now(), completed_at = now()
		WHERE job_id = $1`, id); err != nil {
		fail(c, http.StatusInternalServerError, "database error")
		return
	}
	s.DB.Exec(ctx, `UPDATE job_tasks SET state = 'CANCELLED', updated_at = now(), finished_at = now()
		WHERE job_id = $1 AND state IN ('PENDING','RUNNING')`, id)
	s.Redis.Set(ctx, keys.Cancelled(id), 1, 24*time.Hour) // workers skip cancelled jobs
	s.Redis.Publish(ctx, keys.Events(id), `{"type":"status"}`)
	obs.JobsFinished.WithLabelValues("CANCELLED").Inc()
	s.respondJob(c, http.StatusOK, id)
}

type layerView struct {
	Task     string         `json:"task"`
	Name     string         `json:"name"`
	Kind     string         `json:"kind"`
	Units    string         `json:"units"`
	Colormap string         `json:"colormap"`
	VMin     *float64       `json:"vmin"`
	VMax     *float64       `json:"vmax"`
	Classes  []string       `json:"classes"`
	Bounds   []float64      `json:"bounds"`
	Stats    map[string]any `json:"stats"`
	PngURL   string         `json:"png_url"`
	TifURL   string         `json:"tif_url"`
}

// Layers returns every map of a job with short-lived presigned URLs: the
// browser downloads straight from object storage, never through the gateway.
func (s *Server) Layers(c *gin.Context) {
	ctx := c.Request.Context()
	id := c.Param("id")
	if _, err := s.loadJob(ctx, id, middleware.UserID(c), middleware.IsAdmin(c)); err != nil {
		fail(c, http.StatusNotFound, "job not found")
		return
	}
	rows, err := s.DB.Query(ctx, `SELECT task, name, kind, units, colormap, vmin, vmax, classes, bounds, stats,
		tif_key, png_key FROM layers WHERE job_id = $1 ORDER BY task, name`, id)
	if err != nil {
		fail(c, http.StatusInternalServerError, "database error")
		return
	}
	defer rows.Close()
	out := []layerView{}
	for rows.Next() {
		var (
			l     layerView
			tif   string
			png   *string
			stats []byte
		)
		if err := rows.Scan(&l.Task, &l.Name, &l.Kind, &l.Units, &l.Colormap, &l.VMin, &l.VMax, &l.Classes,
			&l.Bounds, &stats, &tif, &png); err != nil {
			fail(c, http.StatusInternalServerError, "database error")
			return
		}
		json.Unmarshal(stats, &l.Stats)
		l.TifURL, _ = s.S3.Presign(ctx, tif, s.Cfg.PresignTTL, fmt.Sprintf("%s_%s.tif", id, l.Name))
		if png != nil {
			l.PngURL, _ = s.S3.Presign(ctx, *png, s.Cfg.PresignTTL, "")
		}
		out = append(out, l)
	}
	results := map[string]gin.H{}
	rrows, err := s.DB.Query(ctx, `SELECT task, model_version, summary, verification, timings
		FROM job_results WHERE job_id = $1`, id)
	if err == nil {
		defer rrows.Close()
		for rrows.Next() {
			var task, model string
			var summary, verification, timings []byte
			if rrows.Scan(&task, &model, &summary, &verification, &timings) == nil {
				var sm, vm, tm map[string]any
				json.Unmarshal(summary, &sm)
				json.Unmarshal(verification, &vm)
				json.Unmarshal(timings, &tm)
				results[task] = gin.H{"model_version": model, "summary": sm, "verification": vm, "timings": tm}
			}
		}
	}
	c.JSON(http.StatusOK, gin.H{"job_id": id, "layers": out, "results": results,
		"expires_in_s": int(s.Cfg.PresignTTL.Seconds())})
}
