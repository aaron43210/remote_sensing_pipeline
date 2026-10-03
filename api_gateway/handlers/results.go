// =============================================================
// OWNER: AARON
// FILE: handlers/results.go — Fetch final result map URLs
// =============================================================

package handlers

import (
	"context"
	"fmt"
	"net/http"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/minio/minio-go/v7"
)

const (
	mlResultsBucket  = "ml-results"
	presignExpiry    = 2 * time.Hour
)

type ResultsResponse struct {
	SceneID        string            `json:"scene_id"`
	Status         string            `json:"status"`
	Layers         map[string]string `json:"layers"`       // layer name → presigned URL
	Summary        map[string]interface{} `json:"summary,omitempty"`
}

// HandleResults generates presigned MinIO URLs for all 3 output maps.
// The frontend uses these URLs to directly download and render the maps.
func (d *Deps) HandleResults(c *gin.Context) {
	sceneID := c.Param("scene_id")
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	// Verify scene is completed first
	statusVal, _ := d.Redis.Get(ctx, fmt.Sprintf("status:%s", sceneID)).Result()
	if statusVal != "COMPLETED" {
		c.JSON(http.StatusConflict, gin.H{
			"error":  "results not ready",
			"status": statusVal,
		})
		return
	}

	// Generate presigned URLs for all 3 output maps from the Hydra ML model
	layers := map[string]string{
		"agriculture":          fmt.Sprintf("%s/agriculture.npy", sceneID),
		"mineral_class":        fmt.Sprintf("%s/mineral_class.npy", sceneID),
		"mineral_probabilities":fmt.Sprintf("%s/mineral_probabilities.npy", sceneID),
		"anomaly":              fmt.Sprintf("%s/anomaly.npy", sceneID),
	}

	presignedURLs := make(map[string]string)
	for layerName, objectPath := range layers {
		url, err := d.MinIO.PresignedGetObject(
			ctx,
			mlResultsBucket,
			objectPath,
			presignExpiry,
			nil,
		)
		if err != nil {
			// If a specific layer is missing, skip it (not all scenes may have all layers)
			continue
		}
		presignedURLs[layerName] = url.String()
	}

	if len(presignedURLs) == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "no result files found in storage"})
		return
	}

	c.JSON(http.StatusOK, ResultsResponse{
		SceneID: sceneID,
		Status:  "COMPLETED",
		Layers:  presignedURLs,
	})
}

// HandlePixel returns the predicted values for a single pixel coordinate.
// Used when user clicks on the map to see detailed pixel values.
func (d *Deps) HandlePixel(c *gin.Context) {
	sceneID := c.Param("scene_id")
	xStr := c.Query("x")
	yStr := c.Query("y")

	x, err1 := strconv.Atoi(xStr)
	y, err2 := strconv.Atoi(yStr)
	if err1 != nil || err2 != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "x and y must be valid integers"})
		return
	}

	// Check Redis cache for pixel result
	ctx := context.Background()
	cacheKey := fmt.Sprintf("pixel:%s:%d:%d", sceneID, x, y)
	cached, _ := d.Redis.Get(ctx, cacheKey).Result()
	if cached != "" {
		c.Data(http.StatusOK, "application/json", []byte(cached))
		return
	}

	// NOTE: Pixel-level data extraction from numpy files requires a
	// small Python helper. In production, this is handled by a
	// dedicated /pixel microservice that the Go gateway proxies to.
	// Alternatively, store pixel-ready JSON in Redis during ML inference.
	c.JSON(http.StatusOK, gin.H{
		"scene_id": sceneID,
		"pixel":    gin.H{"x": x, "y": y},
		"message":  "pixel detail available after ML inference completes",
	})
}

// HandleModelInfo returns static metadata about the Hydra ML model.
func (d *Deps) HandleModelInfo(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{
		"model_name":       "HydraMTLNet",
		"architecture":     "Multi-Task Learning (Shared Trunk + 3 Heads)",
		"parameters":       "~3,000",
		"input_bands":      10,
		"inference_device": "CPU",
		"inference_ms_per_pixel": 0.1,
		"outputs": gin.H{
			"agriculture_head": []string{"Chlorophyll (Cab)", "Water (Cw)", "LAI", "Leaf Structure (N)"},
			"mineral_head":     []string{"Background", "Kaolinite", "Calcite", "Illite", "Hematite", "Montmorillonite"},
			"anomaly_head":     []string{"Fire/Thermal Anomaly Probability"},
		},
		"satellites_supported": []string{"NASA EMIT", "EnMAP", "PRISMA"},
	})
}

// HandleHealth checks liveness of all downstream dependencies.
func (d *Deps) HandleHealth(c *gin.Context) {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	redisOK := d.Redis.Ping(ctx).Err() == nil
	dbOK := d.DB.PingContext(ctx) == nil

	// Check MinIO
	_, minioErr := d.MinIO.ListBuckets(ctx)
	minioOK := minioErr == nil

	overall := "healthy"
	statusCode := http.StatusOK
	if !redisOK || !dbOK || !minioOK {
		overall = "degraded"
		statusCode = http.StatusServiceUnavailable
	}

	c.JSON(statusCode, gin.H{
		"status":    overall,
		"redis":     boolToStatus(redisOK),
		"postgres":  boolToStatus(dbOK),
		"minio":     boolToStatus(minioOK),
		"timestamp": time.Now().UTC().Format(time.RFC3339),
	})
}

func boolToStatus(ok bool) string {
	if ok {
		return "ok"
	}
	return "unreachable"
}
