package handlers

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/coreos/go-oidc/v3/oidc"
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"golang.org/x/oauth2"
)

// Google sign-in (OpenID Connect, authorization code + PKCE). The browser only
// ever holds a random id in a short-lived cookie; state, nonce and the PKCE
// verifier stay in Redis and are single-use. Google's access token is never
// stored: only the verified identity (sub, email) is kept.

const (
	oauthCookie     = "hn_oauth"
	oauthCookiePath = "/api/auth/google"
	oauthTTL        = 10 * time.Minute
)

type oauthPending struct {
	State    string `json:"state"`
	Nonce    string `json:"nonce"`
	Verifier string `json:"verifier"`
	From     string `json:"from"`
}

type googleClaims struct {
	Sub           string `json:"sub"`
	Email         string `json:"email"`
	EmailVerified bool   `json:"email_verified"`
	Name          string `json:"name"`
	HD            string `json:"hd"` // Google Workspace domain; empty for gmail.com
}

// oidcProvider is discovered lazily (and retried), so the gateway starts even
// when Google is briefly unreachable.
type oidcProvider struct {
	mu sync.Mutex
	p  *oidc.Provider
}

var idp oidcProvider

func oauthHTTP(ctx context.Context) context.Context {
	return oidc.ClientContext(ctx, &http.Client{Timeout: 10 * time.Second})
}

func (s *Server) provider(ctx context.Context) (*oidc.Provider, error) {
	idp.mu.Lock()
	defer idp.mu.Unlock()
	if idp.p != nil {
		return idp.p, nil
	}
	if s.Cfg.OIDCClientID == "" {
		return nil, errors.New("sign-in is not configured")
	}
	p, err := oidc.NewProvider(oauthHTTP(ctx), s.Cfg.OIDCIssuer)
	if err != nil {
		return nil, err
	}
	idp.p = p
	return p, nil
}

func (s *Server) oauthConfig(p *oidc.Provider) *oauth2.Config {
	return &oauth2.Config{ClientID: s.Cfg.OIDCClientID, ClientSecret: s.Cfg.OIDCClientSecret,
		RedirectURL: s.Cfg.OIDCRedirectURL, Endpoint: p.Endpoint(),
		Scopes: []string{oidc.ScopeOpenID, "email", "profile"}}
}

var samePath = regexp.MustCompile(`^/[\w\-./?=&%]*$`)

// safeFrom keeps a post-login destination on this site: never "//evil", "/\evil" or an absolute URL.
func safeFrom(p string) string {
	if len(p) > 200 || !samePath.MatchString(p) || strings.HasPrefix(p, "//") || strings.Contains(p, "\\") {
		return "/"
	}
	return p
}

func (s *Server) setOAuthCookie(c *gin.Context, v string, maxAge int) {
	// Lax, not Strict: it must come back on Google's top-level redirect to the callback.
	http.SetCookie(c.Writer, &http.Cookie{Name: oauthCookie, Value: v, Path: oauthCookiePath, MaxAge: maxAge,
		HttpOnly: true, Secure: s.Cfg.CookieSecure, SameSite: http.SameSiteLaxMode})
}

// loginError sends the browser back to the sign-in page with a fixed error code (never raw text).
func (s *Server) loginError(c *gin.Context, code string, uid *int64, detail map[string]any) {
	s.audit(c, uid, "login_fail", map[string]any{"reason": code, "detail": detail})
	s.setOAuthCookie(c, "", -1)
	c.Redirect(http.StatusSeeOther, "/login?error="+url.QueryEscape(code))
}

// GoogleLogin starts sign-in: GET /api/auth/google/login?from=/jobs/123
func (s *Server) GoogleLogin(c *gin.Context) {
	ctx := c.Request.Context()
	p, err := s.provider(ctx)
	if err != nil {
		s.loginError(c, "unavailable", nil, map[string]any{"err": err.Error()})
		return
	}
	pend := oauthPending{State: newOpaqueToken(), Nonce: newOpaqueToken(), Verifier: oauth2.GenerateVerifier(),
		From: safeFrom(c.Query("from"))}
	id := newOpaqueToken()
	b, _ := json.Marshal(pend)
	if err := s.Redis.Set(ctx, "oauth:"+id, b, oauthTTL).Err(); err != nil {
		s.loginError(c, "unavailable", nil, map[string]any{"err": "redis"})
		return
	}
	s.setOAuthCookie(c, id, int(oauthTTL.Seconds()))
	opts := []oauth2.AuthCodeOption{oidc.Nonce(pend.Nonce), oauth2.S256ChallengeOption(pend.Verifier),
		oauth2.SetAuthURLParam("prompt", "select_account")}
	if len(s.Cfg.AllowedDomains) == 1 && s.Cfg.AllowedDomains[0] != "gmail.com" && len(s.Cfg.AllowedEmails) == 0 {
		opts = append(opts, oauth2.SetAuthURLParam("hd", s.Cfg.AllowedDomains[0])) // a hint only; enforced below
	}
	c.Redirect(http.StatusFound, s.oauthConfig(p).AuthCodeURL(pend.State, opts...))
}

// allowed: admin, exact addresses, or a domain Google is authoritative for
// (a Workspace domain, proven by the hd claim, or gmail.com).
func (s *Server) allowed(email, hd string) bool {
	if s.Cfg.OpenSignup || (s.Cfg.AdminEmail != "" && email == s.Cfg.AdminEmail) {
		return true
	}
	for _, e := range s.Cfg.AllowedEmails {
		if e == email {
			return true
		}
	}
	domain := email[strings.LastIndex(email, "@")+1:]
	for _, d := range s.Cfg.AllowedDomains {
		if d == domain && (strings.ToLower(hd) == d || (d == "gmail.com" && hd == "")) {
			return true
		}
	}
	return false
}

// GoogleCallback finishes sign-in: GET /api/auth/google/callback?code=...&state=...
func (s *Server) GoogleCallback(c *gin.Context) {
	ctx := c.Request.Context()
	id, err := c.Cookie(oauthCookie)
	if err != nil || id == "" || len(id) > 100 {
		s.loginError(c, "expired", nil, nil)
		return
	}
	raw, err := s.Redis.GetDel(ctx, "oauth:"+id).Bytes() // single use: a replayed callback finds nothing
	var pend oauthPending
	if err != nil || json.Unmarshal(raw, &pend) != nil {
		s.loginError(c, "expired", nil, nil)
		return
	}
	s.setOAuthCookie(c, "", -1)
	if c.Query("error") != "" {
		s.loginError(c, "cancelled", nil, map[string]any{"google": c.Query("error")})
		return
	}
	if subtle.ConstantTimeCompare([]byte(c.Query("state")), []byte(pend.State)) != 1 {
		s.loginError(c, "state", nil, nil)
		return
	}
	code := c.Query("code")
	if code == "" || len(code) > 2048 {
		s.loginError(c, "failed", nil, map[string]any{"err": "no code"})
		return
	}
	p, err := s.provider(ctx)
	if err != nil {
		s.loginError(c, "unavailable", nil, nil)
		return
	}
	hctx := oauthHTTP(ctx)
	tok, err := s.oauthConfig(p).Exchange(hctx, code, oauth2.VerifierOption(pend.Verifier))
	if err != nil {
		s.loginError(c, "failed", nil, map[string]any{"err": "code exchange"})
		return
	}
	rawID, _ := tok.Extra("id_token").(string)
	idt, err := p.Verifier(&oidc.Config{ClientID: s.Cfg.OIDCClientID}).Verify(hctx, rawID)
	if err != nil || subtle.ConstantTimeCompare([]byte(idt.Nonce), []byte(pend.Nonce)) != 1 {
		s.loginError(c, "failed", nil, map[string]any{"err": "id token"})
		return
	}
	var cl googleClaims
	if err := idt.Claims(&cl); err != nil || cl.Sub == "" {
		s.loginError(c, "failed", nil, map[string]any{"err": "claims"})
		return
	}
	email := strings.ToLower(strings.TrimSpace(cl.Email))
	if !cl.EmailVerified || !strings.Contains(email, "@") || len(email) > 254 {
		s.loginError(c, "unverified", nil, nil)
		return
	}
	if !s.allowed(email, cl.HD) {
		s.loginError(c, "not_allowed", nil, map[string]any{"email": email})
		return
	}
	uid, err := s.upsertGoogleUser(ctx, cl.Sub, email, cl.Name)
	if errors.Is(err, errAccountConflict) {
		s.loginError(c, "conflict", nil, map[string]any{"email": email})
		return
	}
	if err != nil {
		s.loginError(c, "failed", nil, map[string]any{"err": "database"})
		return
	}
	if err := s.newRefresh(c, uid, uuid.New()); err != nil {
		s.loginError(c, "failed", &uid, map[string]any{"err": "session"})
		return
	}
	s.audit(c, &uid, "login_ok", map[string]any{"provider": "google"})
	// The SPA restores the session from the HttpOnly refresh cookie (POST /api/auth/refresh).
	c.Redirect(http.StatusSeeOther, pend.From)
}

var errAccountConflict = errors.New("email belongs to another Google account")

// upsertGoogleUser finds the user by Google's stable subject id, else links an
// existing account with the same verified email that has no Google id yet,
// else creates one. ADMIN_EMAIL is made admin.
func (s *Server) upsertGoogleUser(ctx context.Context, sub, email, name string) (int64, error) {
	name = strings.TrimSpace(name)
	if len(name) > 100 {
		name = name[:100]
	}
	if name == "" {
		name = strings.Split(email, "@")[0]
	}
	admin := s.Cfg.AdminEmail != "" && email == s.Cfg.AdminEmail
	var id int64
	err := s.DB.QueryRow(ctx, `UPDATE users SET name = $2, last_login_at = now(),
		role = CASE WHEN $3 THEN 'admin' ELSE role END WHERE google_sub = $1
		RETURNING id`, sub, name, admin).Scan(&id)
	if !errors.Is(err, pgx.ErrNoRows) {
		return id, err
	}
	newRole := "user"
	if admin {
		newRole = "admin"
	}
	err = s.DB.QueryRow(ctx, `INSERT INTO users (email, name, role, google_sub, last_login_at)
		VALUES ($1, $2, $3, $4, now())
		ON CONFLICT (email) DO UPDATE SET google_sub = EXCLUDED.google_sub, name = EXCLUDED.name,
			last_login_at = now(), role = CASE WHEN $5 THEN 'admin' ELSE users.role END
		WHERE users.google_sub IS NULL
		RETURNING id`, email, name, newRole, sub, admin).Scan(&id)
	if errors.Is(err, pgx.ErrNoRows) {
		return 0, errAccountConflict
	}
	return id, err
}
