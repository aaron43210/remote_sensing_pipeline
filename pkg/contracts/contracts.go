// Package contracts mirrors libs/hs_common/hs_common/messages.py (the source of
// truth). contracts_test.go checks these structs against contracts/*.schema.json.
package contracts

const SchemaVersion = 1

const (
	TopicIngestRequests = "ingest.requests"
	TopicJobStatus      = "job.status"
	TopicMLResults      = "ml.results"
)

var Tasks = []string{"agriculture", "mineral", "thermal"}

type IngestRequest struct {
	SchemaVersion int       `json:"schema_version"`
	EventID       string    `json:"event_id"`
	JobID         string    `json:"job_id"`
	TS            string    `json:"ts"`
	UserID        string    `json:"user_id,omitempty"`
	BBox          []float64 `json:"bbox"`
	Tasks         []string  `json:"tasks"`
	DateFrom      string    `json:"date_from,omitempty"`
	DateTo        string    `json:"date_to,omitempty"`
	Source        string    `json:"source"`
	// clearest-first scenes from catalog_granules, used when NASA's search (CMR) is down
	EmitCandidates []EmitCandidate `json:"emit_candidates,omitempty"`
}

type EmitCandidate struct {
	GranuleID  string   `json:"granule_id"`
	URL        string   `json:"url,omitempty"`
	AcquiredAt string   `json:"acquired_at"`
	CloudCover *float64 `json:"cloud_cover"`
}

type JobStatus struct {
	SchemaVersion int      `json:"schema_version"`
	EventID       string   `json:"event_id"`
	JobID         string   `json:"job_id"`
	TS            string   `json:"ts"`
	Stage         string   `json:"stage"`
	Task          *string  `json:"task"`
	State         string   `json:"state"`
	Message       string   `json:"message"`
	Service       string   `json:"service"`
	Progress      *float64 `json:"progress"`
}

type Layer struct {
	Name     string         `json:"name"`
	TifKey   string         `json:"tif_key"`
	PngKey   *string        `json:"png_key"`
	Bounds   []float64      `json:"bounds"`
	Units    string         `json:"units"`
	Kind     string         `json:"kind"`
	Colormap string         `json:"colormap"`
	VMin     *float64       `json:"vmin"`
	VMax     *float64       `json:"vmax"`
	Classes  []string       `json:"classes"`
	Stats    map[string]any `json:"stats"`
}

type MLResult struct {
	SchemaVersion int            `json:"schema_version"`
	EventID       string         `json:"event_id"`
	JobID         string         `json:"job_id"`
	TS            string         `json:"ts"`
	Task          string         `json:"task"`
	ModelVersion  string         `json:"model_version"`
	Layers        []Layer        `json:"layers"`
	Summary       map[string]any `json:"summary"`
	Verification  map[string]any `json:"verification"`
	Timings       map[string]any `json:"timings"`
	AcquiredAt    *string        `json:"acquired_at"`
}
