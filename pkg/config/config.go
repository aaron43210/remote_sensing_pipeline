// Package config reads the environment contract shared by the Go services.
// The same keys work locally (MinIO) and on AWS (S3); only values change.
package config

import (
	"errors"
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	Env          string
	Port         string
	DatabaseURL  string
	RedisURL     string
	KafkaBrokers []string

	S3Bucket         string
	S3Endpoint       string // internal endpoint (http://minio:9000); empty = AWS
	S3PublicEndpoint string // what the browser reaches for presigned URLs; empty = AWS
	S3Region         string
	S3ForcePathStyle bool
	PresignTTL       time.Duration

	JWTSecret         string        // current signing key
	JWTPreviousSecret string        // accepted for verification only, during key rotation
	CredentialsKey    string        // AES-256 key (64 hex) for users' NASA Earthdata tokens
	AccessTTL         time.Duration // short: a stolen access token is useless quickly
	RefreshTTL        time.Duration
	CookieSecure      bool   // Secure cookies (true behind HTTPS / CloudFront)
	ClientIPHeader    string // set by OUR edge proxy only; X-Forwarded-For is never trusted
	MaxBodyBytes      int64
	AllowedOrigins    []string

	// Sign-in: Google (OpenID Connect) only. OIDCIssuer is overridable for the test IdP.
	OIDCIssuer       string
	OIDCClientID     string
	OIDCClientSecret string
	OIDCRedirectURL  string   // https://<domain>/api/auth/google/callback
	AllowedEmails    []string // exact addresses allowed to sign in
	AllowedDomains   []string // Google Workspace domains (checked against the hd claim), or gmail.com
	OpenSignup       bool     // any verified Google account (off by default: compute costs money)
	AdminEmail       string   // becomes admin on sign-in

	// Admission control: keeps the platform responsive under load
	MaxActiveJobsPerUser int
	MaxQueuedJobs        int
	RateLimitPerMinute   int
	AuthRateLimitPerMin  int
	MaxBBoxDeg           float64
	AllowedRegion        [4]float64 // lon_min, lat_min, lon_max, lat_max
	AllowFixtures        bool
	EmitMaxCloud         float64 // same limit as ingestion: the coverage map shows only usable scenes

	StageTimeouts map[string]time.Duration
}

func Get(key, def string) string {
	if v, ok := os.LookupEnv(key); ok {
		return v
	}
	return def
}

func GetInt(key string, def int) int {
	if v, err := strconv.Atoi(os.Getenv(key)); err == nil {
		return v
	}
	return def
}

func GetFloat(key string, def float64) float64 {
	if v, err := strconv.ParseFloat(os.Getenv(key), 64); err == nil {
		return v
	}
	return def
}

func GetBool(key string, def bool) bool {
	v := strings.ToLower(strings.TrimSpace(os.Getenv(key)))
	if v == "" {
		return def
	}
	return v == "1" || v == "true" || v == "yes"
}

// list splits a comma-separated value, lower-cased, without blanks.
func list(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ",") {
		if p = strings.ToLower(strings.TrimSpace(p)); p != "" {
			out = append(out, p)
		}
	}
	return out
}

func region(s string) [4]float64 {
	out := [4]float64{-180, -90, 180, 90}
	parts := strings.Split(s, ",")
	if len(parts) != 4 {
		return out
	}
	for i, p := range parts {
		if v, err := strconv.ParseFloat(strings.TrimSpace(p), 64); err == nil {
			out[i] = v
		}
	}
	return out
}

func Load() Config {
	return Config{
		Env:          Get("ENV", "local"),
		Port:         Get("PORT", "8000"),
		DatabaseURL:  Get("DATABASE_URL", "postgres://spectral:spectral@postgres:5432/hyperspectral?sslmode=disable"),
		RedisURL:     Get("REDIS_URL", "redis://redis:6379/0"),
		KafkaBrokers: strings.Split(Get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092"), ","),

		S3Bucket:         Get("S3_BUCKET", "hyperspectral-local"),
		S3Endpoint:       Get("S3_ENDPOINT_URL", ""),
		S3PublicEndpoint: Get("S3_PUBLIC_ENDPOINT_URL", ""),
		S3Region:         Get("S3_REGION", "us-west-2"),
		S3ForcePathStyle: GetBool("S3_FORCE_PATH_STYLE", false),
		PresignTTL:       time.Duration(GetInt("PRESIGN_TTL_MIN", 15)) * time.Minute,

		JWTSecret:         Get("JWT_SECRET", ""),
		JWTPreviousSecret: Get("JWT_PREVIOUS_SECRET", ""),
		CredentialsKey:    Get("CREDENTIALS_KEY", ""),
		AccessTTL:         time.Duration(GetInt("ACCESS_TTL_MIN", 15)) * time.Minute,
		RefreshTTL:        time.Duration(GetInt("REFRESH_TTL_DAYS", 14)) * 24 * time.Hour,
		CookieSecure:      GetBool("COOKIE_SECURE", false),
		ClientIPHeader:    Get("CLIENT_IP_HEADER", "X-Real-IP"),
		MaxBodyBytes:      int64(GetInt("MAX_BODY_BYTES", 64*1024)),
		AllowedOrigins:    strings.Split(Get("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173"), ","),

		OIDCIssuer:       Get("OIDC_ISSUER", "https://accounts.google.com"),
		OIDCClientID:     Get("GOOGLE_CLIENT_ID", ""),
		OIDCClientSecret: Get("GOOGLE_CLIENT_SECRET", ""),
		OIDCRedirectURL:  Get("OAUTH_REDIRECT_URL", "http://localhost:3000/api/auth/google/callback"),
		AllowedEmails:    list(Get("ALLOWED_EMAILS", "")),
		AllowedDomains:   list(Get("ALLOWED_EMAIL_DOMAINS", "")),
		OpenSignup:       GetBool("OPEN_SIGNUP", false),
		AdminEmail:       strings.ToLower(strings.TrimSpace(Get("ADMIN_EMAIL", ""))),

		MaxActiveJobsPerUser: GetInt("MAX_ACTIVE_JOBS_PER_USER", 3),
		MaxQueuedJobs:        GetInt("MAX_QUEUED_JOBS", 500),
		RateLimitPerMinute:   GetInt("RATE_LIMIT_PER_MIN", 120),
		AuthRateLimitPerMin:  GetInt("AUTH_RATE_LIMIT_PER_MIN", 20),
		MaxBBoxDeg:           GetFloat("MAX_BBOX_DEG", 0.1),
		AllowedRegion:        region(Get("ALLOWED_REGION", "-124.5,32.5,-114.0,42.0")),
		AllowFixtures:        GetBool("ALLOW_FIXTURES", true),
		EmitMaxCloud:         GetFloat("EMIT_MAX_CLOUD", 40),

		StageTimeouts: map[string]time.Duration{
			"queued":     time.Duration(GetInt("SLA_QUEUED_MIN", 30)) * time.Minute,
			"ingest":     time.Duration(GetInt("SLA_INGEST_MIN", 30)) * time.Minute,
			"preprocess": time.Duration(GetInt("SLA_PREPROCESS_MIN", 10)) * time.Minute,
			"inference":  time.Duration(GetInt("SLA_INFERENCE_MIN", 10)) * time.Minute,
		},
	}
}

const GoogleIssuer = "https://accounts.google.com"

// CheckSignIn refuses a production start whose sign-in would be unsafe or open to everyone.
func (c Config) CheckSignIn() error {
	if c.Env != "prod" {
		return nil
	}
	switch {
	case c.OIDCClientID == "" || c.OIDCClientSecret == "" ||
		strings.Contains(c.OIDCClientID, "CHANGE_ME") || strings.Contains(c.OIDCClientSecret, "CHANGE_ME"):
		return errors.New("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set (sign-in is Google only)")
	case c.OIDCIssuer != GoogleIssuer:
		return errors.New("in production OIDC_ISSUER must be " + GoogleIssuer)
	case !strings.HasPrefix(c.OIDCRedirectURL, "https://"):
		return errors.New("OAUTH_REDIRECT_URL must be https:// in production")
	case !c.CookieSecure:
		return errors.New("COOKIE_SECURE must be true in production")
	case !c.OpenSignup && len(c.AllowedEmails) == 0 && len(c.AllowedDomains) == 0 && c.AdminEmail == "":
		return errors.New("set ALLOWED_EMAILS or ALLOWED_EMAIL_DOMAINS (or OPEN_SIGNUP=true): nobody could sign in")
	}
	return nil
}
