// =============================================================
// OWNER: AARON
// FILE: handlers/status.go — Poll job status
// =============================================================

package handlers

import (
	"context"
	"net/http"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
)

type StatusResponse struct {
	SceneID   string  `json:"scene_id"`
	Status    string  `json:"status"`
	Progress  float64 `json:"progress_pct,omitempty"`
	Error     string  `json:"error,omitempty"`
	UpdatedAt string  `json:"updated_at,omitempty"`
}

// HandleStatus reads job status from Redis.
// The Python orchestrator writes the status; Go reads it — clean separation.
func (d *Deps) HandleStatus(c *gin.Context) {
	sceneID := c.Param("scene_id")
	if sceneID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "scene_id is required"})
		return
	}

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	statusKey := fmt.Sprintf("status:%s", sceneID)
	val, err := d.Redis.Get(ctx, statusKey).Result()
	if err != nil {
		c.JSON(http.StatusNotFound, gin.H{
			"scene_id": sceneID,
			"status":   "NOT_FOUND",
			"message":  "No job found with this scene_id",
		})
		return
	}

	resp := StatusResponse{
		SceneID:   sceneID,
		Status:    val,
		UpdatedAt: time.Now().UTC().Format(time.RFC3339),
	}

	// Parse error details if status is ERROR
	if strings.HasPrefix(val, "ERROR") {
		resp.Error = strings.TrimPrefix(val, "ERROR: ")
		resp.Status = "ERROR"
	}

	// Estimate progress based on known pipeline stages
	switch val {
	case "STARTING":
		resp.Progress = 5
	case "INGESTING":
		resp.Progress = 20
	case "PREPROCESSING":
		resp.Progress = 50
	case "PROCESSING":
		resp.Progress = 75
	case "COMPLETED":
		resp.Progress = 100
	}

	c.JSON(http.StatusOK, resp)
}
