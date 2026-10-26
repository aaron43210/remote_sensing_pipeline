// Package handlers implements the HTTP API. Every handler is stateless: all
// state lives in Postgres (truth), Redis (cache, pub/sub, limits) and S3, so
// the gateway scales horizontally behind a load balancer.
package handlers

import (
	"context"
	"net/http"

	"github.com/gin-gonic/gin"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"

	"remote_sensing_production/api_gateway/middleware"
	"remote_sensing_production/pkg/config"
	"remote_sensing_production/pkg/s3x"
	"remote_sensing_production/pkg/secretbox"
)

type Server struct {
	Cfg   config.Config
	DB    *pgxpool.Pool
	Redis *redis.Client
	S3    *s3x.Store
	Keys  *middleware.KeyRing
	Box   *secretbox.Box // users' NASA Earthdata tokens, encrypted at rest
}

func (s *Server) Routes() *gin.Engine {
	r := gin.New()
	// Client IP: X-Forwarded-For is attacker-controlled and is NEVER trusted. Our
	// edge proxy (nginx) overwrites CLIENT_IP_HEADER with the real peer address;
	// the gateway is not reachable except through it.
	r.SetTrustedProxies(nil)
	r.TrustedPlatform = s.Cfg.ClientIPHeader
	r.Use(gin.Recovery(), middleware.RequestID(), middleware.Metrics(), middleware.Logger(),
		middleware.SecurityHeaders(), middleware.CORS(s.Cfg.AllowedOrigins), middleware.BodyLimit(s.Cfg.MaxBodyBytes))
	r.MaxMultipartMemory = 1 << 20

	r.GET("/health", s.Health)
	r.GET("/ready", s.Ready)
	r.GET("/metrics", gin.WrapH(middleware.MetricsHandler()))

	api := r.Group("/api")
	authLimit := middleware.RateLimit(s.Redis, "auth", s.Cfg.AuthRateLimitPerMin, middleware.ByIP)
	csrf := middleware.SameOriginCSRF(s.Cfg.AllowedOrigins)
	api.GET("/auth/google/login", authLimit, s.GoogleLogin) // sign-in is Google only
	api.GET("/auth/google/callback", authLimit, s.GoogleCallback)
	api.POST("/auth/refresh", authLimit, csrf, s.Refresh) // refresh-token cookie
	api.POST("/auth/logout", csrf, s.Logout)
	api.GET("/jobs/:id/events", s.Events) // authenticated by a one-time stream ticket
	api.GET("/model", s.Model)            // public: what model produces the maps, and its verification
	api.GET("/config", s.Config)          // public: limits the UI must respect

	priv := api.Group("")
	priv.Use(middleware.JWT(s.Keys, s.Redis), middleware.RateLimit(s.Redis, "api", s.Cfg.RateLimitPerMinute, middleware.ByUser))
	priv.GET("/me", s.Me)
	priv.GET("/me/earthdata", s.EarthdataStatus)
	priv.POST("/me/earthdata", s.ConnectEarthdata)
	priv.DELETE("/me/earthdata", s.DisconnectEarthdata)
	priv.POST("/auth/logout-all", s.LogoutAll)
	priv.POST("/jobs/:id/stream-ticket", s.StreamTicket)
	priv.POST("/jobs", s.CreateJob)
	priv.GET("/jobs", s.ListJobs)
	priv.GET("/jobs/:id", s.GetJob)
	priv.DELETE("/jobs/:id", s.CancelJob)
	priv.GET("/jobs/:id/layers", s.Layers)
	priv.GET("/catalog", s.Catalog)
	priv.GET("/coverage", s.Coverage)
	priv.GET("/stats", middleware.RequireAdmin(), s.Stats)

	r.NoRoute(func(c *gin.Context) { c.JSON(http.StatusNotFound, gin.H{"error": "not found"}) })
	return r
}

func (s *Server) Health(c *gin.Context) { c.JSON(http.StatusOK, gin.H{"status": "ok"}) }

// Config tells the UI the server-side limits, so they are never duplicated in JS.
func (s *Server) Config(c *gin.Context) {
	c.Header("Cache-Control", "public, max-age=300")
	c.JSON(http.StatusOK, gin.H{
		"allowed_region": s.Cfg.AllowedRegion, "max_bbox_deg": s.Cfg.MaxBBoxDeg,
		"allow_fixtures": s.Cfg.AllowFixtures, "tasks": []string{"agriculture", "mineral", "thermal"},
		"max_active_jobs_per_user": s.Cfg.MaxActiveJobsPerUser, "env": s.Cfg.Env,
		"emit_max_cloud": s.Cfg.EmitMaxCloud,
	})
}

// Ready checks every dependency; the load balancer only routes to ready replicas.
func (s *Server) Ready(c *gin.Context) {
	ctx, cancel := context.WithTimeout(c.Request.Context(), 3e9)
	defer cancel()
	checks := gin.H{"postgres": "ok", "redis": "ok", "storage": "ok"}
	code := http.StatusOK
	if err := s.DB.Ping(ctx); err != nil {
		checks["postgres"], code = err.Error(), http.StatusServiceUnavailable
	}
	if err := s.Redis.Ping(ctx).Err(); err != nil {
		checks["redis"], code = err.Error(), http.StatusServiceUnavailable
	}
	if err := s.S3.Ping(ctx); err != nil {
		checks["storage"], code = err.Error(), http.StatusServiceUnavailable
	}
	c.JSON(code, checks)
}

func fail(c *gin.Context, code int, msg string) {
	c.AbortWithStatusJSON(code, gin.H{"error": msg})
}
