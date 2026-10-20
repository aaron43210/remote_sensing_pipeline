package handlers

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"golang.org/x/crypto/bcrypt"

	"remote_sensing_production/api_gateway/middleware"
)

const (
	bcryptCost    = 11
	refreshCookie = "hn_refresh"
	cookiePath    = "/api/auth"
	lockWindow    = 15 * time.Minute
)

// dummyHash lets a login for an unknown email cost the same bcrypt work as a real one.
var dummyHash, _ = bcrypt.GenerateFromPassword([]byte("not-a-real-password"), bcryptCost)

// The most common leaked passwords (subset of public breach top lists).
var commonPasswords = map[string]bool{}

func init() {
	for _, p := range strings.Fields(`password password1 password123 12345678 123456789 1234567890 qwerty123
		qwertyuiop 11111111 00000000 iloveyou admin123 welcome1 welcome123 letmein1 abc12345 abcd1234
		sunshine1 princess1 football1 baseball1 dragon123 monkey123 passw0rd p@ssw0rd p@ssword trustno1
		superman1 starwars1 master123 shadow123 michael1 jennifer1 computer1 whatever1 changeme
		changeme1 secret123 hello123 qazwsxedc 1q2w3e4r 1q2w3e4r5t zaq12wsx asdfghjkl 87654321 12341234
		123123123 password! hydranet hydranet1 hydranet123`) {
		commonPasswords[p] = true
	}
}

type credentials struct {
	Email    string `json:"email" binding:"required,email,max=254"`
	Password string `json:"password" binding:"required,min=8,max=200"`
	Name     string `json:"name" binding:"omitempty,max=100"`
}

type authResponse struct {
	Token     string `json:"token"`      // access token: keep it in memory, never in storage
	ExpiresAt int64  `json:"expires_at"` // unix seconds
	User      gin.H  `json:"user"`
}

func passwordProblem(pw, email string) string {
	if len(pw) < 10 {
		return "password must be at least 10 characters"
	}
	if len([]byte(pw)) > 72 { // bcrypt only uses 72 bytes; longer would silently be truncated
		return "password must be at most 72 bytes"
	}
	low := strings.ToLower(pw)
	if commonPasswords[low] {
		return "this password is too common; choose another"
	}
	if local := strings.Split(strings.ToLower(email), "@")[0]; len(local) >= 4 && strings.Contains(low, local) {
		return "password must not contain your email name"
	}
	if strings.Count(pw, string(pw[0])) == len(pw) {
		return "password must not be one repeated character"
	}
	return ""
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

// startSession issues an access token and a fresh refresh-token family.
func (s *Server) startSession(c *gin.Context, code int, id int64, email, name, role string, version int) {
	s.issue(c, code, id, email, name, role, version, uuid.New())
}

func (s *Server) issue(c *gin.Context, code int, id int64, email, name, role string, version int, family uuid.UUID) {
	access, exp, err := s.Keys.Sign(strconv.FormatInt(id, 10), email, name, role, version, s.Cfg.AccessTTL)
	if err != nil {
		fail(c, http.StatusInternalServerError, "could not create token")
		return
	}
	refresh := newOpaqueToken()
	if _, err := s.DB.Exec(c, `INSERT INTO refresh_tokens (user_id, family_id, token_hash, expires_at, ip, user_agent)
		VALUES ($1,$2,$3,$4,$5,$6)`, id, family, hashToken(refresh), time.Now().Add(s.Cfg.RefreshTTL),
		c.ClientIP(), c.Request.UserAgent()); err != nil {
		fail(c, http.StatusInternalServerError, "could not create session")
		return
	}
	s.setRefreshCookie(c, refresh, int(s.Cfg.RefreshTTL.Seconds()))
	c.JSON(code, authResponse{Token: access, ExpiresAt: exp.Unix(),
		User: gin.H{"id": id, "email": email, "name": name, "role": role}})
}

func (s *Server) Register(c *gin.Context) {
	var req credentials
	if err := c.ShouldBindJSON(&req); err != nil {
		fail(c, http.StatusBadRequest, "a valid email and a password of 10+ characters are required")
		return
	}
	req.Email = strings.ToLower(strings.TrimSpace(req.Email))
	if p := passwordProblem(req.Password, req.Email); p != "" {
		fail(c, http.StatusBadRequest, p)
		return
	}
	name := strings.TrimSpace(req.Name)
	if name == "" {
		name = strings.Split(req.Email, "@")[0]
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(req.Password), bcryptCost)
	if err != nil {
		fail(c, http.StatusInternalServerError, "could not hash password")
		return
	}
	var id int64
	err = s.DB.QueryRow(c, `INSERT INTO users (email, password_hash, name) VALUES ($1, $2, $3) RETURNING id`,
		req.Email, string(hash), name).Scan(&id)
	var pgErr *pgconn.PgError
	if errors.As(err, &pgErr) && pgErr.Code == "23505" {
		fail(c, http.StatusConflict, "an account with this email already exists")
		return
	}
	if err != nil {
		fail(c, http.StatusInternalServerError, "could not create account")
		return
	}
	s.audit(c, &id, "register", nil)
	s.startSession(c, http.StatusCreated, id, req.Email, name, "user", 0)
}

func lockKey(email string) string { return "login_fail:" + email }

func (s *Server) Login(c *gin.Context) {
	var req credentials
	if err := c.ShouldBindJSON(&req); err != nil {
		fail(c, http.StatusBadRequest, "email and password are required")
		return
	}
	email := strings.ToLower(strings.TrimSpace(req.Email))
	ctx := c.Request.Context()
	// Per-ACCOUNT lockout: stops slow, distributed guessing that per-IP limits miss.
	if n, _ := s.Redis.Get(ctx, lockKey(email)).Int(); n >= s.Cfg.LoginMaxFailures {
		ttl, _ := s.Redis.TTL(ctx, lockKey(email)).Result()
		c.Header("Retry-After", strconv.Itoa(int(ttl.Seconds())))
		s.audit(c, nil, "login_locked", map[string]any{"email": email})
		fail(c, http.StatusTooManyRequests, fmt.Sprintf("too many failed sign-ins for this account; try again in %d min",
			int(ttl.Minutes())+1))
		return
	}
	var (
		id               int64
		hash, name, role string
		version          int
	)
	err := s.DB.QueryRow(ctx, `SELECT id, password_hash, name, role, token_version FROM users WHERE email = $1`,
		email).Scan(&id, &hash, &name, &role, &version)
	failed := func() {
		pipe := s.Redis.TxPipeline()
		pipe.Incr(ctx, lockKey(email))
		pipe.Expire(ctx, lockKey(email), lockWindow)
		pipe.Exec(ctx)
		s.audit(c, nil, "login_fail", map[string]any{"email": email})
		fail(c, http.StatusUnauthorized, "wrong email or password")
	}
	if errors.Is(err, pgx.ErrNoRows) {
		bcrypt.CompareHashAndPassword(dummyHash, []byte(req.Password)) // same cost: no email enumeration by timing
		failed()
		return
	}
	if err != nil {
		fail(c, http.StatusInternalServerError, "database error")
		return
	}
	if bcrypt.CompareHashAndPassword([]byte(hash), []byte(req.Password)) != nil {
		failed()
		return
	}
	s.Redis.Del(ctx, lockKey(email))
	s.audit(c, &id, "login_ok", nil)
	s.startSession(c, http.StatusOK, id, email, name, role, version)
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
