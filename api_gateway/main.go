// =============================================================
// OWNER: AARON
// SERVICE: API Gateway (Golang) — Production Grade
// =============================================================

package main

import (
	"log"
	"net/http"
	"os"
	"time"

	"github.com/gin-gonic/gin"
	"remote_sensing_production/api_gateway/handlers"
	"remote_sensing_production/api_gateway/middleware"
)

func main() {
	gin.SetMode(gin.ReleaseMode)
	r := gin.New()

	// ── Global Middleware ─────────────────────────────────────
	r.Use(gin.Recovery())
	r.Use(middleware.Logger())
	r.Use(middleware.CORS())
	r.Use(middleware.RateLimiter(100, time.Minute)) // 100 req/min per IP

	// ── Dependencies (injected once, shared across handlers) ──
	deps, err := handlers.NewDeps()
	if err != nil {
		log.Fatalf("Failed to initialize dependencies: %v", err)
	}
	defer deps.Close()

	// ── Public Routes ─────────────────────────────────────────
	r.GET("/health", deps.HandleHealth)
	r.POST("/auth/login", deps.HandleLogin)
	r.POST("/auth/register", deps.HandleRegister)

	// ── Protected Routes ──────────────────────────────────────
	api := r.Group("/api")
	api.Use(middleware.JWT(os.Getenv("JWT_SECRET")))
	{
		api.POST("/process", deps.HandleProcess)
		api.GET("/status/:scene_id", deps.HandleStatus)
		api.GET("/results/:scene_id", deps.HandleResults)
		api.GET("/results/:scene_id/pixel", deps.HandlePixel)
		api.GET("/model/info", deps.HandleModelInfo)
	}

	port := os.Getenv("PORT")
	if port == "" {
		port = "8000"
	}

	srv := &http.Server{
		Addr:         ":" + port,
		Handler:      r,
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 30 * time.Second,
		IdleTimeout:  120 * time.Second,
	}

	log.Printf("API Gateway (Go/Gin) listening on :%s", port)
	if err := srv.ListenAndServe(); err != nil {
		log.Fatal(err)
	}
}
