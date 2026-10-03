// =============================================================
// OWNER: AARON
// FILE: middleware/cors.go & logger.go & ratelimiter.go
// =============================================================

package middleware

import (
	"fmt"
	"net/http"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
)

// CORS returns a middleware that allows cross-origin requests from the frontend.
func CORS() gin.HandlerFunc {
	return func(c *gin.Context) {
		c.Header("Access-Control-Allow-Origin", "*")
		c.Header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
		c.Header("Access-Control-Allow-Headers", "Authorization, Content-Type, Accept")
		c.Header("Access-Control-Max-Age", "86400")

		if c.Request.Method == http.MethodOptions {
			c.AbortWithStatus(http.StatusNoContent)
			return
		}

		c.Next()
	}
}

// Logger returns a structured request logger middleware.
func Logger() gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()
		path := c.Request.URL.Path

		c.Next()

		latency := time.Since(start)
		statusCode := c.Writer.Status()

		fmt.Printf("[GIN] %s | %3d | %12v | %s %s\n",
			time.Now().Format("2006/01/02 - 15:04:05"),
			statusCode,
			latency,
			c.Request.Method,
			path,
		)
	}
}

// ── Rate Limiter ──────────────────────────────────────────────
// In-memory sliding window rate limiter.
// In production, replace with Redis-based limiter for multi-replica safety.

type rateLimiter struct {
	mu       sync.Mutex
	requests map[string][]time.Time
	limit    int
	window   time.Duration
}

var limiter *rateLimiter

// RateLimiter returns a middleware that limits requests per IP.
func RateLimiter(limit int, window time.Duration) gin.HandlerFunc {
	limiter = &rateLimiter{
		requests: make(map[string][]time.Time),
		limit:    limit,
		window:   window,
	}

	return func(c *gin.Context) {
		ip := c.ClientIP()

		limiter.mu.Lock()
		now := time.Now()
		windowStart := now.Add(-limiter.window)

		// Remove requests outside the window
		valid := []time.Time{}
		for _, t := range limiter.requests[ip] {
			if t.After(windowStart) {
				valid = append(valid, t)
			}
		}

		if len(valid) >= limiter.limit {
			limiter.mu.Unlock()
			c.Header("Retry-After", fmt.Sprintf("%d", int(limiter.window.Seconds())))
			c.AbortWithStatusJSON(http.StatusTooManyRequests, gin.H{
				"error": fmt.Sprintf("rate limit exceeded: max %d requests per %s", limiter.limit, limiter.window),
			})
			return
		}

		limiter.requests[ip] = append(valid, now)
		limiter.mu.Unlock()

		c.Next()
	}
}
