package middleware

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/golang-jwt/jwt/v5"
	"github.com/redis/go-redis/v9"
)

const (
	Issuer   = "hydranet-gateway"
	Audience = "hydranet-api"
	maxToken = 4096
)

// Claims of an ACCESS token. Short-lived (minutes); the long-lived credential
// is the refresh token, which never reaches JavaScript (HttpOnly cookie).
type Claims struct {
	Email   string `json:"email"`
	Name    string `json:"name"`
	Role    string `json:"role"`
	Version int    `json:"ver"` // users.token_version: bump it to kill every token of a user
	jwt.RegisteredClaims
}

// KeyRing signs with the current key and still accepts the previous one, so
// JWT_SECRET can be rotated without logging everybody out.
type KeyRing struct {
	current, previous []byte
	currentID, prevID string
}

func keyID(k []byte) string {
	h := sha256.Sum256(k)
	return hex.EncodeToString(h[:4])
}

func NewKeyRing(current, previous string) *KeyRing {
	r := &KeyRing{current: []byte(current), currentID: keyID([]byte(current))}
	if previous != "" {
		r.previous, r.prevID = []byte(previous), keyID([]byte(previous))
	}
	return r
}

func (r *KeyRing) Sign(userID, email, name, role string, version int, ttl time.Duration) (string, time.Time, error) {
	now := time.Now()
	exp := now.Add(ttl)
	jti := make([]byte, 16)
	rand.Read(jti)
	c := Claims{Email: email, Name: name, Role: role, Version: version, RegisteredClaims: jwt.RegisteredClaims{
		Subject: userID, Issuer: Issuer, Audience: jwt.ClaimStrings{Audience},
		IssuedAt: jwt.NewNumericDate(now), NotBefore: jwt.NewNumericDate(now), ExpiresAt: jwt.NewNumericDate(exp),
		ID: hex.EncodeToString(jti)}}
	t := jwt.NewWithClaims(jwt.SigningMethodHS256, c)
	t.Header["kid"] = r.currentID
	s, err := t.SignedString(r.current)
	return s, exp, err
}

// Parse accepts only HS256 tokens signed by a key in the ring, with our issuer
// and audience, inside their validity window. "alg": "none", RS/HS confusion,
// foreign issuers and expired tokens are all refused.
func (r *KeyRing) Parse(raw string) (*Claims, error) {
	if len(raw) > maxToken {
		return nil, jwt.ErrTokenMalformed
	}
	claims := &Claims{}
	_, err := jwt.ParseWithClaims(raw, claims, func(t *jwt.Token) (any, error) {
		switch t.Header["kid"] {
		case r.currentID:
			return r.current, nil
		case r.prevID:
			if r.previous != nil {
				return r.previous, nil
			}
		}
		return nil, jwt.ErrTokenUnverifiable
	}, jwt.WithValidMethods([]string{"HS256"}), jwt.WithIssuer(Issuer), jwt.WithAudience(Audience),
		jwt.WithIssuedAt(), jwt.WithExpirationRequired(), jwt.WithLeeway(15*time.Second))
	if err != nil {
		return nil, err
	}
	return claims, nil
}

func RevokedKey(jti string) string { return "revoked_jti:" + jti }
func VersionKey(uid string) string { return "token_version:" + uid }

// JWT authenticates "Authorization: Bearer <access token>" ONLY. Tokens are never
// read from query strings (they would leak into logs and browser history).
// A logged-out token (denylisted jti) or one from an older token_version is refused.
func JWT(ring *KeyRing, rdb *redis.Client) gin.HandlerFunc {
	return func(c *gin.Context) {
		h := c.GetHeader("Authorization")
		if !strings.HasPrefix(strings.ToLower(h), "bearer ") {
			c.Header("WWW-Authenticate", `Bearer realm="hydranet"`)
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "missing bearer token"})
			return
		}
		claims, err := ring.Parse(strings.TrimSpace(h[7:]))
		if err != nil {
			c.Header("WWW-Authenticate", `Bearer error="invalid_token"`)
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "invalid or expired token"})
			return
		}
		ctx := c.Request.Context()
		pipe := rdb.Pipeline()
		revoked := pipe.Exists(ctx, RevokedKey(claims.ID))
		ver := pipe.Get(ctx, VersionKey(claims.Subject))
		pipe.Exec(ctx)
		if revoked.Val() > 0 {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "token revoked"})
			return
		}
		if v, err := ver.Int(); err == nil && v > claims.Version {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "session ended, sign in again"})
			return
		}
		c.Set("user_id", claims.Subject)
		c.Set("email", claims.Email)
		c.Set("name", claims.Name)
		c.Set("role", claims.Role)
		c.Set("jti", claims.ID)
		c.Set("token_exp", claims.ExpiresAt.Time)
		c.Next()
	}
}

func UserID(c *gin.Context) string { return c.GetString("user_id") }

// UserNum is the numeric user id (users.id is BIGINT).
func UserNum(c *gin.Context) int64 {
	n, _ := strconv.ParseInt(c.GetString("user_id"), 10, 64)
	return n
}

func IsAdmin(c *gin.Context) bool { return c.GetString("role") == "admin" }

// RequireAdmin guards admin-only routes.
func RequireAdmin() gin.HandlerFunc {
	return func(c *gin.Context) {
		if !IsAdmin(c) {
			c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"error": "admin only"})
			return
		}
		c.Next()
	}
}

// SameOriginCSRF protects the cookie-authenticated endpoints (refresh, logout):
// SameSite=Strict already stops cross-site sends; this adds a custom header a
// cross-site form cannot set, and an Origin check when the browser sends one.
func SameOriginCSRF(allowed []string) gin.HandlerFunc {
	ok := map[string]bool{}
	for _, o := range allowed {
		ok[strings.TrimSpace(o)] = true
	}
	return func(c *gin.Context) {
		if c.GetHeader("X-Requested-With") != "hydranet" {
			c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"error": "missing anti-CSRF header"})
			return
		}
		if o := c.GetHeader("Origin"); o != "" && !ok[o] {
			c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"error": "cross-origin request refused"})
			return
		}
		c.Next()
	}
}

// BodyLimit caps request bodies (JSON APIs here never need more).
func BodyLimit(n int64) gin.HandlerFunc {
	return func(c *gin.Context) {
		if c.Request.ContentLength > n {
			c.AbortWithStatusJSON(http.StatusRequestEntityTooLarge, gin.H{"error": "request body too large"})
			return
		}
		c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, n)
		c.Next()
	}
}
