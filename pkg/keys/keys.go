// Package keys names every Redis key the Go services use, in one place.
package keys

import "fmt"

func Job(id string) string        { return "job:" + id }        // hash: cached job JSON (24 h)
func Events(id string) string     { return "events:" + id }     // pub/sub channel for SSE
func Reuse(hash string) string    { return "reuse:" + hash }    // finished job for an identical request
func Inflight(hash string) string { return "inflight:" + hash } // running job for an identical request
func Idem(user, k string) string  { return fmt.Sprintf("idem:%s:%s", user, k) }
func Rate(scope, who string, minute int64) string {
	return fmt.Sprintf("rl:%s:%s:%d", scope, who, minute)
}
func Cancelled(id string) string { return "cancelled:" + id } // read by the Python workers
