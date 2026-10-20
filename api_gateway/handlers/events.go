package handlers

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/gin-gonic/gin"

	"remote_sensing_production/api_gateway/middleware"
	"remote_sensing_production/pkg/keys"
	"remote_sensing_production/pkg/obs"
)

// StreamTicket issues a single-use, 30-second ticket for one job's event
// stream. EventSource cannot send an Authorization header, and putting the
// access token in the URL would leak it into logs; the ticket is worthless
// once used or after 30 s.
func (s *Server) StreamTicket(c *gin.Context) {
	id := c.Param("id")
	if _, err := s.loadJob(c.Request.Context(), id, middleware.UserID(c), middleware.IsAdmin(c)); err != nil {
		fail(c, http.StatusNotFound, "job not found")
		return
	}
	b := make([]byte, 24)
	rand.Read(b)
	t := hex.EncodeToString(b)
	role := "user"
	if middleware.IsAdmin(c) {
		role = "admin"
	}
	s.Redis.Set(c.Request.Context(), "sse_ticket:"+t, middleware.UserID(c)+"|"+role+"|"+id, 30*time.Second)
	c.JSON(http.StatusOK, gin.H{"ticket": t, "expires_in_s": 30})
}

// Events streams the job's state as Server-Sent Events: one full JobView on
// connect and after every change (the orchestrator publishes to Redis), a
// heartbeat every 15 s, and a final "end" event when the job is finished.
// Any gateway replica can serve any client: the fan-out goes through Redis.
func (s *Server) Events(c *gin.Context) {
	ctx := c.Request.Context()
	id := c.Param("id")
	ticket := c.Query("ticket")
	if len(ticket) != 48 {
		fail(c, http.StatusUnauthorized, "stream ticket required")
		return
	}
	val, err := s.Redis.GetDel(ctx, "sse_ticket:"+ticket).Result() // single use
	parts := strings.SplitN(val, "|", 3)
	if err != nil || len(parts) != 3 || parts[2] != id {
		fail(c, http.StatusUnauthorized, "invalid or expired stream ticket")
		return
	}
	user, admin := parts[0], parts[1] == "admin"
	v, err := s.loadJob(ctx, id, user, admin)
	if err != nil {
		fail(c, http.StatusNotFound, "job not found")
		return
	}
	h := c.Writer.Header()
	h.Set("Content-Type", "text/event-stream")
	h.Set("Cache-Control", "no-cache")
	h.Set("Connection", "keep-alive")
	h.Set("X-Accel-Buffering", "no") // nginx: do not buffer the stream
	obs.SSEClients.Inc()
	defer obs.SSEClients.Dec()

	send := func(event string, data any) bool {
		b, _ := json.Marshal(data)
		if _, err := fmt.Fprintf(c.Writer, "event: %s\ndata: %s\n\n", event, b); err != nil {
			return false
		}
		c.Writer.Flush()
		return true
	}
	if !send("job", v) || v.Final {
		send("end", gin.H{"status": v.Status})
		return
	}
	sub := s.Redis.Subscribe(ctx, keys.Events(id))
	defer sub.Close()
	msgs := sub.Channel()
	heartbeat := time.NewTicker(15 * time.Second)
	defer heartbeat.Stop()
	// safety net: re-read state every 10 s even if a pub/sub message was lost
	resync := time.NewTicker(10 * time.Second)
	defer resync.Stop()
	maxAge := time.NewTimer(2 * time.Hour)
	defer maxAge.Stop()

	push := func() bool {
		v, err := s.loadJob(ctx, id, user, admin)
		if err != nil {
			return false
		}
		if !send("job", v) {
			return false
		}
		if v.Final {
			send("end", gin.H{"status": v.Status})
			return false
		}
		return true
	}
	for {
		select {
		case <-ctx.Done():
			return
		case <-maxAge.C:
			return // the browser's EventSource reconnects by itself
		case <-heartbeat.C:
			if _, err := fmt.Fprint(c.Writer, ": ping\n\n"); err != nil {
				return
			}
			c.Writer.Flush()
		case <-resync.C:
			if !push() {
				return
			}
		case _, ok := <-msgs:
			if !ok || !push() {
				return
			}
		}
	}
}
