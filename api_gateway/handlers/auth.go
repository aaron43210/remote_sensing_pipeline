package handlers

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"

	"remote_sensing_production/api_gateway/middleware"
)

const (
	refreshCookie = "hn_refresh"
	cookiePath    = "/api/auth"
)

type authResponse struct {
	Token     string `json:"token"`      // access token: keep it in memory, never in storage
	ExpiresAt int64  `json:"expires_at"` // unix seconds
	User      gin.H  `json:"user"`
}

func (s *Server) audit(c *gin.Context, userID *int64, event string, detail map[string]any) {
	d, _ := json.Marshal(detail)
	ua := c.Request.UserAgent()
	if len(ua) > 300 {
		ua = ua[:300]
	}
	// background: auditing must never slow down or fail the request
	go s.DB.Exec(context.Background(), `INSERT INTO audit_log (user_id, event, ip, user_agent, detail) VALUES ($1,$2,$3,$4,$5)`,
		userID, event, c.ClientIP(), ua, d)
}

func hashToken(t string) []byte {
	h := sha256.Sum256([]byte(t))
	return h[:]
}

func newOpaqueToken() string {
	b := make([]byte, 32)
	rand.Read(b)
	return base64.RawURLEncoding.EncodeToString(b)
}

func (s *Server) setRefreshCookie(c *gin.Context, token string, maxAge int) {
	http.SetCookie(c.Writer, &http.Cookie{Name: refreshCookie, Value: token, Path: cookiePath, MaxAge: maxAge,
		HttpOnly: true, Secure: s.Cfg.CookieSecure, SameSite: http.SameSiteStrictMode})
}

// newRefresh stores a refresh token of the given family and sets its cookie.
func (s *Server) newRefresh(c *gin.Context, id int64, family uuid.UUID) error {
	refresh := newOpaqueToken()
	if _, err := s.DB.Exec(c, `INSERT INTO refresh_tokens (user_id, family_id, token_hash, expires_at, ip, user_agent)
		VALUES ($1,$2,$3,$4,$5,$6)`, id, family, hashToken(refresh), time.Now().Add(s.Cfg.RefreshTTL),
		c.ClientIP(), c.Request.UserAgent()); err != nil {
		return err
	}
	s.setRefreshCookie(c, refresh, int(s.Cfg.RefreshTTL.Seconds()))
	return nil
}

// issue rotates into a new refresh token of the same family and returns a fresh access token.
func (s *Server) issue(c *gin.Context, code int, id int64, email, name, role string, version int, family uuid.UUID) {
	access, exp, err := s.Keys.Sign(strconv.FormatInt(id, 10), email, name, role, version, s.Cfg.AccessTTL)
	if err != nil {
		fail(c, http.StatusInternalServerError, "could not create token")
		return
	}
	if err := s.newRefresh(c, id, family); err != nil {
		fail(c, http.StatusInternalServerError, "could not create session")
		return
	}
	c.JSON(code, authResponse{Token: access, ExpiresAt: exp.Unix(),
		User: gin.H{"id": id, "email": email, "name": name, "role": role}})
}

// Refresh rotates the refresh token (cookie) and returns a new access token.
// Presenting an already-rotated token means a copy was stolen: the whole
// family (every token descended from that login) is revoked.
func (s *Server) Refresh(c *gin.Context) {
	cookie, err := c.Cookie(refreshCookie)
	if err != nil || cookie == "" {
		c.Status(http.StatusNoContent) // not signed in: a normal state, not an error
		return
	}
	if len(cookie) > 100 {
		fail(c, http.StatusUnauthorized, "invalid session")
		return
	}
	ctx := c.Request.Context()
	tx, err := s.DB.Begin(ctx)
	if err != nil {
		fail(c, http.StatusInternalServerError, "database error")
		return
	}
	defer tx.Rollback(ctx)
	var (
		tid, uid          int64
		family            uuid.UUID
		expires           time.Time
		rotated, revoked  *time.Time
		email, name, role string
		version           int
	)
	err = tx.QueryRow(ctx, `SELECT t.id, t.user_id, t.family_id, t.expires_at, t.rotated_at, t.revoked_at,
		u.email, u.name, u.role, u.token_version
		FROM refresh_tokens t JOIN users u ON u.id = t.user_id WHERE t.token_hash = $1 FOR UPDATE OF t`,
		hashToken(cookie)).Scan(&tid, &uid, &family, &expires, &rotated, &revoked, &email, &name, &role, &version)
	if err != nil {
		s.setRefreshCookie(c, "", -1)
		fail(c, http.StatusUnauthorized, "session expired, sign in again")
		return
	}
	if rotated != nil || revoked != nil {
		tx.Exec(ctx, `UPDATE refresh_tokens SET revoked_at = now() WHERE family_id = $1 AND revoked_at IS NULL`, family)
		tx.Commit(ctx)
		s.audit(c, &uid, "refresh_reuse_detected", map[string]any{"family": family.String()})
		s.setRefreshCookie(c, "", -1)
		fail(c, http.StatusUnauthorized, "session revoked for safety, sign in again")
		return
	}
	if time.Now().After(expires) {
		s.setRefreshCookie(c, "", -1)
		fail(c, http.StatusUnauthorized, "session expired, sign in again")
		return
	}
	if _, err := tx.Exec(ctx, `UPDATE refresh_tokens SET rotated_at = now() WHERE id = $1`, tid); err != nil {
		fail(c, http.StatusInternalServerError, "database error")
		return
	}
	if err := tx.Commit(ctx); err != nil {
		fail(c, http.StatusInternalServerError, "database error")
		return
	}
	s.issue(c, http.StatusOK, uid, email, name, role, version, family)
}

// Logout ends this session: its refresh family is revoked and the current
// access token is denylisted until it would have expired anyway.
func (s *Server) Logout(c *gin.Context) {
	ctx := c.Request.Context()
	if cookie, err := c.Cookie(refreshCookie); err == nil && cookie != "" {
		s.DB.Exec(ctx, `UPDATE refresh_tokens SET revoked_at = now() WHERE family_id =
			(SELECT family_id FROM refresh_tokens WHERE token_hash = $1) AND revoked_at IS NULL`, hashToken(cookie))
	}
	if raw := strings.TrimSpace(strings.TrimPrefix(c.GetHeader("Authorization"), "Bearer ")); raw != "" {
		if cl, err := s.Keys.Parse(raw); err == nil {
			if ttl := time.Until(cl.ExpiresAt.Time); ttl > 0 {
				s.Redis.Set(ctx, middleware.RevokedKey(cl.ID), 1, ttl)
			}
			uid, _ := strconv.ParseInt(cl.Subject, 10, 64)
			s.audit(c, &uid, "logout", nil)
		}
	}
	s.setRefreshCookie(c, "", -1)
	c.Status(http.StatusNoContent)
}

// LogoutAll ends every session of the caller on every device.
func (s *Server) LogoutAll(c *gin.Context) {
	ctx := c.Request.Context()
	uid := middleware.UserNum(c)
	var version int
	if err := s.DB.QueryRow(ctx, `UPDATE users SET token_version = token_version + 1 WHERE id = $1 RETURNING token_version`,
		uid).Scan(&version); err != nil {
		fail(c, http.StatusInternalServerError, "database error")
		return
	}
	s.DB.Exec(ctx, `UPDATE refresh_tokens SET revoked_at = now() WHERE user_id = $1 AND revoked_at IS NULL`, uid)
	s.Redis.Set(ctx, middleware.VersionKey(middleware.UserID(c)), version, s.Cfg.RefreshTTL)
	s.audit(c, &uid, "logout_all", nil)
	s.setRefreshCookie(c, "", -1)
	c.Status(http.StatusNoContent)
}

func (s *Server) Me(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"id": middleware.UserID(c), "email": c.GetString("email"),
		"name": c.GetString("name"), "role": c.GetString("role")})
}
