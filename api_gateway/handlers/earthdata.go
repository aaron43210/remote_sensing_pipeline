package handlers

// Each user connects their own NASA Earthdata account once, with a user token
// (NASA issues them for 60 days); EMIT downloads for their jobs then use it.
// The token is stored AES-256-GCM encrypted and is never returned by the API.
// For a job that needs an EMIT download it is re-sealed for that job alone and
// handed to ingestion through Redis (never Kafka, whose messages persist for a
// week), expiring with the job's ingest deadline. Landsat needs no account.

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/jackc/pgx/v5"

	"remote_sensing_production/api_gateway/middleware"
	"remote_sensing_production/pkg/keys"
)

var errNoEarthdata = errors.New("connect your NASA Earthdata account to make agriculture and mineral maps")

func userContext(uid int64) string   { return fmt.Sprintf("earthdata:user:%d", uid) }
func jobContext(jobID string) string { return "earthdata:job:" + jobID } // ingestion/earthdata.py opens it

// edlClaims reads the user name and expiry an Earthdata token carries. Not
// verified (NASA's signing key is not ours): used for display and to refuse an
// already-expired token early. ok is false when it is not a JWT at all.
func edlClaims(token string) (uid string, exp *time.Time, ok bool) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return "", nil, false
	}
	raw, err := base64.RawURLEncoding.DecodeString(strings.TrimRight(parts[1], "="))
	if err != nil {
		return "", nil, false
	}
	var c struct {
		UID string `json:"uid"`
		Exp int64  `json:"exp"`
	}
	if json.Unmarshal(raw, &c) != nil {
		return "", nil, false
	}
	if c.Exp > 0 {
		t := time.Unix(c.Exp, 0).UTC()
		exp = &t
	}
	return c.UID, exp, true
}

type verdict int

const (
	unverified verdict = iota // NASA could not be asked (no catalogued scene, network, outage)
	accepted
	refused
)

// nasaVerdict tries the token on a real EMIT file, first byte only: proof that
// it works for LP DAAC downloads, not just that it is well formed.
func (s *Server) nasaVerdict(ctx context.Context, token string) verdict {
	var file string
	if s.DB.QueryRow(ctx, `SELECT url FROM catalog_granules WHERE sensor = 'EMIT' AND url LIKE 'https://%'
		ORDER BY acquired_at DESC LIMIT 1`).Scan(&file) != nil {
		return unverified
	}
	if u, err := url.Parse(file); err != nil || !strings.HasSuffix(u.Hostname(), ".nasa.gov") {
		return unverified // a user's token is only ever sent to NASA
	}
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, file, nil)
	if err != nil {
		return unverified
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Range", "bytes=0-0")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return unverified
	}
	resp.Body.Close()
	switch {
	case resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden,
		resp.Request.URL.Hostname() == "urs.earthdata.nasa.gov": // sent to the login page instead of the file
		return refused
	case resp.StatusCode == http.StatusOK || resp.StatusCode == http.StatusPartialContent:
		return accepted
	}
	return unverified
}

func (s *Server) EarthdataStatus(c *gin.Context) {
	var (
		name    *string
		expires *time.Time
	)
	err := s.DB.QueryRow(c, `SELECT username, expires_at FROM earthdata_tokens WHERE user_id = $1`,
		middleware.UserNum(c)).Scan(&name, &expires)
	if errors.Is(err, pgx.ErrNoRows) {
		c.JSON(http.StatusOK, gin.H{"connected": false})
		return
	}
	if err != nil {
		fail(c, http.StatusInternalServerError, "database error")
		return
	}
	expired := expires != nil && time.Now().After(*expires)
	c.JSON(http.StatusOK, gin.H{"connected": !expired, "expired": expired, "username": name, "expires_at": expires})
}

func (s *Server) ConnectEarthdata(c *gin.Context) {
	var req struct {
		Token string `json:"token" binding:"required,max=4096"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		fail(c, http.StatusBadRequest, "paste your NASA Earthdata token")
		return
	}
	token := strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(req.Token), "Bearer "))
	if token == "" || strings.ContainsAny(token, " \t\r\n") {
		fail(c, http.StatusBadRequest, "paste the token exactly as NASA shows it, without spaces")
		return
	}
	uid := middleware.UserNum(c)
	name, exp, isJWT := edlClaims(token)
	if exp != nil && time.Now().After(*exp) {
		fail(c, http.StatusBadRequest, fmt.Sprintf("this token expired on %s; generate a new one", exp.Format("2 Jan 2006")))
		return
	}
	v := s.nasaVerdict(c.Request.Context(), token)
	if v == refused {
		s.audit(c, &uid, "earthdata_refused", nil)
		fail(c, http.StatusBadRequest, "NASA Earthdata refused this token: check it was copied completely "+
			"and that your Earthdata account can download EMIT data")
		return
	}
	if v == unverified && !isJWT {
		fail(c, http.StatusBadRequest, "this does not look like a NASA Earthdata token")
		return
	}
	if _, err := s.DB.Exec(c, `INSERT INTO earthdata_tokens (user_id, ciphertext, username, expires_at)
		VALUES ($1, $2, $3, $4) ON CONFLICT (user_id) DO UPDATE SET ciphertext = EXCLUDED.ciphertext,
		username = EXCLUDED.username, expires_at = EXCLUDED.expires_at, updated_at = now()`,
		uid, s.Box.Seal([]byte(token), userContext(uid)), nullable(name), exp); err != nil {
		fail(c, http.StatusInternalServerError, "could not save the token")
		return
	}
	s.audit(c, &uid, "earthdata_connect", map[string]any{"username": name, "verified": v == accepted})
	c.JSON(http.StatusOK, gin.H{"connected": true, "username": nullable(name), "expires_at": exp,
		"verified": v == accepted})
}

func (s *Server) DisconnectEarthdata(c *gin.Context) {
	uid := middleware.UserNum(c)
	if _, err := s.DB.Exec(c, `DELETE FROM earthdata_tokens WHERE user_id = $1`, uid); err != nil {
		fail(c, http.StatusInternalServerError, "database error")
		return
	}
	s.audit(c, &uid, "earthdata_disconnect", nil)
	c.Status(http.StatusNoContent)
}

// handOverEarthdata gives ingestion the owner's token for this one job.
func (s *Server) handOverEarthdata(ctx context.Context, uid int64, jobID string) error {
	var (
		sealed  []byte
		expires *time.Time
	)
	err := s.DB.QueryRow(ctx, `SELECT ciphertext, expires_at FROM earthdata_tokens WHERE user_id = $1`, uid).
		Scan(&sealed, &expires)
	if errors.Is(err, pgx.ErrNoRows) || (err == nil && expires != nil && time.Now().After(*expires)) {
		return errNoEarthdata
	}
	if err != nil {
		return err
	}
	token, err := s.Box.Open(sealed, userContext(uid))
	if err != nil {
		return err
	}
	ttl := s.Cfg.StageTimeouts["queued"] + s.Cfg.StageTimeouts["ingest"] // the job has timed out by then
	return s.Redis.Set(ctx, keys.Earthdata(jobID), s.Box.Seal(token, jobContext(jobID)), ttl).Err()
}
