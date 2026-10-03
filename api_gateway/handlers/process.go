// =============================================================
// OWNER: AARON
// FILE: handlers/process.go — Submit a scene for processing
// =============================================================

package handlers

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"github.com/segmentio/kafka-go"
)

// ProcessRequest is the JSON body the frontend sends.
type ProcessRequest struct {
	BBox      [4]float64 `json:"bbox" binding:"required"`  // [lon_min, lat_min, lon_max, lat_max]
	Satellite string     `json:"satellite"`                 // "EMIT" or "EnMAP"
}

// ProcessResponse is returned to the frontend immediately.
type ProcessResponse struct {
	SceneID   string `json:"scene_id"`
	Status    string `json:"status"`
	Satellite string `json:"satellite"`
}

// HandleProcess validates the request, writes status to Redis,
// and publishes a job to Kafka for the Python Ingestion service.
func (d *Deps) HandleProcess(c *gin.Context) {
	var req ProcessRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	// Validate bounding box
	lon_min, lat_min, lon_max, lat_max := req.BBox[0], req.BBox[1], req.BBox[2], req.BBox[3]
	if lon_min >= lon_max || lat_min >= lat_max {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid bbox: min must be less than max"})
		return
	}
	if lon_min < -180 || lon_max > 180 || lat_min < -90 || lat_max > 90 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "bbox coordinates out of valid range"})
		return
	}

	// Default satellite
	satellite := req.Satellite
	if satellite == "" {
		satellite = "EMIT"
	}

	// Generate unique scene ID
	sceneID := fmt.Sprintf("%s_%s", satellite, uuid.New().String()[:8])

	// ── Write initial status to Redis ─────────────────────────
	ctx := context.Background()
	statusKey := fmt.Sprintf("status:%s", sceneID)
	d.Redis.Set(ctx, statusKey, "STARTING", time.Hour)

	// ── Publish to Kafka for Python Ingestion service ─────────
	jobPayload, _ := json.Marshal(map[string]interface{}{
		"scene_id":  sceneID,
		"bbox":      req.BBox,
		"satellite": satellite,
		"timestamp": time.Now().UTC().Format(time.RFC3339),
	})

	err := d.Kafka.WriteMessages(ctx, kafka.Message{
		Topic: "raw-ingest-requests",
		Key:   []byte(sceneID),
		Value: jobPayload,
	})
	if err != nil {
		// Roll back Redis status
		d.Redis.Set(ctx, statusKey, "ERROR: failed to queue job", time.Hour)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to queue job: " + err.Error()})
		return
	}

	// ── Log scene to PostgreSQL for audit trail ───────────────
	go func() {
		_, dbErr := d.DB.Exec(
			`INSERT INTO scenes (scene_id, satellite, bbox, status, created_at)
			 VALUES ($1, $2, $3, $4, NOW())`,
			sceneID, satellite, fmt.Sprintf("%v", req.BBox), "STARTING",
		)
		if dbErr != nil {
			// Non-fatal: just log
			_ = dbErr
		}
	}()

	c.JSON(http.StatusAccepted, ProcessResponse{
		SceneID:   sceneID,
		Status:    "STARTING",
		Satellite: satellite,
	})
}
