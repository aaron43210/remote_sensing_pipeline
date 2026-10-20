// Package obs sets up JSON logging and Prometheus metrics for the Go services.
package obs

import (
	"log/slog"
	"net/http"
	"os"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

func SetupLogging(service string) {
	h := slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo})
	slog.SetDefault(slog.New(h).With("service", service))
}

var (
	HTTPRequests = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "hs_http_requests_total", Help: "HTTP requests"}, []string{"route", "method", "code"})
	HTTPLatency = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name: "hs_http_request_seconds", Help: "HTTP latency",
		Buckets: []float64{.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5}}, []string{"route", "method"})
	JobsSubmitted = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "hs_jobs_submitted_total", Help: "Jobs accepted"}, []string{"outcome"})
	JobsFinished = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "hs_jobs_finished_total", Help: "Jobs reaching a final state"}, []string{"status"})
	JobDuration = promauto.NewHistogram(prometheus.HistogramOpts{
		Name: "hs_job_seconds", Help: "Job wall-clock time, submit to final state",
		Buckets: []float64{5, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600}})
	SSEClients = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "hs_sse_clients", Help: "Open Server-Sent Events connections"})
	OutboxPending = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "hs_outbox_pending", Help: "Outbox rows not yet published"})
	QueueDepth = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "hs_jobs_active", Help: "Jobs queued or running"})
)

// ServeMetrics exposes /metrics on addr (for services without an HTTP API).
func ServeMetrics(addr string) {
	mux := http.NewServeMux()
	mux.Handle("/metrics", promhttp.Handler())
	mux.HandleFunc("/health", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })
	go func() {
		if err := http.ListenAndServe(addr, mux); err != nil {
			slog.Error("metrics server", "err", err)
		}
	}()
}
