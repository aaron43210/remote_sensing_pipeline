package middleware

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/redis/go-redis/v9"

	"remote_sensing_production/pkg/keys"
	"remote_sensing_production/pkg/obs"
)

func RequestID() gin.HandlerFunc {
	return func(c *gin.Context) {
		id := c.GetHeader("X-Request-ID")
		if id == "" || len(id) > 64 {
			b := make([]byte, 8)
			rand.Read(b)
			id = hex.EncodeToString(b)
		}
		c.Set("request_id", id)
		c.Header("X-Request-ID", id)
		c.Next()
	}
}

func Logger() gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()
		c.Next()
		if c.FullPath() == "/health" || c.FullPath() == "/metrics" {
			return
		}
		slog.Info("http", "method", c.Request.Method, "route", c.FullPath(), "status", c.Writer.Status(),
			"ms", time.Since(start).Milliseconds(), "user", c.GetString("user_id"),
			"request_id", c.GetString("request_id"), "ip", c.ClientIP())
	}
}

func Metrics() gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()
		c.Next()
		route := c.FullPath()
		if route == "" {
			route = "unmatched"
		}
		obs.HTTPRequests.WithLabelValues(route, c.Request.Method, strconv.Itoa(c.Writer.Status())).Inc()
		if route != "/api/jobs/:id/events" { // long-lived streams would distort latency
			obs.HTTPLatency.WithLabelValues(route, c.Request.Method).Observe(time.Since(start).Seconds())
		}
	}
}

func MetricsHandler() http.Handler { return promhttp.Handler() }

func SecurityHeaders() gin.HandlerFunc {
	return func(c *gin.Context) {
		h := c.Writer.Header()
		h.Set("X-Content-Type-Options", "nosniff")
		h.Set("X-Frame-Options", "DENY")
		h.Set("Referrer-Policy", "strict-origin-when-cross-origin")
		h.Set("Cache-Control", "no-store")
		c.Next()
	}
}

// CORS allows only the configured origins (the frontend is normally served
// from the same origin through nginx/CloudFront, so this matters for dev).
func CORS(origins []string) gin.HandlerFunc {
	allowed := map[string]bool{}
	for _, o := range origins {
		allowed[o] = true
	}
	return func(c *gin.Context) {
		if o := c.GetHeader("Origin"); o != "" && (allowed[o] || allowed["*"]) {
			c.Header("Access-Control-Allow-Origin", o)
			c.Header("Vary", "Origin")
			c.Header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
			c.Header("Access-Control-Allow-Headers", "Authorization, Content-Type, Idempotency-Key, X-Request-ID, X-Requested-With")
			c.Header("Access-Control-Allow-Credentials", "true")
			c.Header("Access-Control-Max-Age", "600")
		}
		if c.Request.Method == http.MethodOptions {
			c.AbortWithStatus(http.StatusNoContent)
			return
		}
		c.Next()
	}
}

func ByIP(c *gin.Context) string   { return c.ClientIP() }
func ByUser(c *gin.Context) string { return c.GetString("user_id") }

// RateLimit is a fixed one-minute window in Redis, so every gateway replica
// shares the same counters. Redis down -> fail open (availability first).
func RateLimit(rdb *redis.Client, scope string, perMinute int, who func(*gin.Context) string) gin.HandlerFunc {
	return func(c *gin.Context) {
		if c.FullPath() == "/api/jobs/:id/events" { // the stream is gated by its ticket
			c.Next()
			return
		}
		minute := time.Now().Unix() / 60
		k := keys.Rate(scope, who(c), minute)
		pipe := rdb.TxPipeline()
		incr := pipe.Incr(c.Request.Context(), k)
		pipe.Expire(c.Request.Context(), k, 2*time.Minute)
		if _, err := pipe.Exec(c.Request.Context()); err == nil && incr.Val() > int64(perMinute) {
			retry := 60 - time.Now().Unix()%60
			c.Header("Retry-After", strconv.FormatInt(retry, 10))
			c.AbortWithStatusJSON(http.StatusTooManyRequests,
				gin.H{"error": fmt.Sprintf("rate limit: %d requests per minute", perMinute), "retry_after_s": retry})
			return
		}
		c.Next()
	}
}
