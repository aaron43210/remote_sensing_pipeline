// Test-only OpenID Connect provider standing in for Google in CI and local
// tests (docker compose --profile test). The gateway refuses it in production
// (OIDC_ISSUER must be https://accounts.google.com there).
//
// /authorize signs in whoever is named by login_hint (default MOCK_DEFAULT_EMAIL)
// without a password. Test knobs: verified=false (unverified email),
// hd=<domain> (Workspace claim; default: the email's domain unless gmail.com),
// deny=1 (user cancels).
package main

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"log"
	"math/big"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

type grant struct {
	email, hd, nonce, challenge, redirect string
	verified                              bool
	expires                               time.Time
}

var (
	issuer   = env("ISSUER", "http://mock-oidc:9998")
	public   = env("PUBLIC_URL", "http://localhost:9998")
	clientID = env("CLIENT_ID", "ci-client")
	secret   = env("CLIENT_SECRET", "ci-secret")
	defEmail = env("MOCK_DEFAULT_EMAIL", "tester@example.com")
	key, _   = rsa.GenerateKey(rand.Reader, 2048)
	mu       sync.Mutex
	grants   = map[string]grant{}
)

func env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}

func b64(b []byte) string { return base64.RawURLEncoding.EncodeToString(b) }

func token() string {
	b := make([]byte, 24)
	rand.Read(b)
	return b64(b)
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func discovery(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, 200, map[string]any{"issuer": issuer, "authorization_endpoint": public + "/authorize",
		"token_endpoint": issuer + "/token", "jwks_uri": issuer + "/jwks", "response_types_supported": []string{"code"},
		"subject_types_supported": []string{"public"}, "id_token_signing_alg_values_supported": []string{"RS256"},
		"code_challenge_methods_supported": []string{"S256"}})
}

func jwks(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, 200, map[string]any{"keys": []map[string]string{{"kty": "RSA", "kid": "mock", "use": "sig",
		"alg": "RS256", "n": b64(key.N.Bytes()), "e": b64(big.NewInt(int64(key.E)).Bytes())}}})
}

func authorize(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	redirect, err := url.Parse(q.Get("redirect_uri"))
	if err != nil || q.Get("client_id") != clientID || q.Get("response_type") != "code" ||
		q.Get("code_challenge_method") != "S256" || q.Get("code_challenge") == "" {
		http.Error(w, "bad authorization request", 400)
		return
	}
	back := redirect.Query()
	back.Set("state", q.Get("state"))
	if q.Get("deny") == "1" {
		back.Set("error", "access_denied")
	} else {
		email := strings.ToLower(q.Get("login_hint"))
		if email == "" {
			email = defEmail
		}
		hd := ""
		if v := q["hd"]; len(v) > 0 {
			hd = v[len(v)-1] // the test's own value (appended last) beats the gateway's hint
		} else {
			if d := email[strings.LastIndex(email, "@")+1:]; d != "gmail.com" {
				hd = d
			}
		}
		code := token()
		mu.Lock()
		grants[code] = grant{email: email, hd: hd, nonce: q.Get("nonce"), challenge: q.Get("code_challenge"),
			redirect: q.Get("redirect_uri"), verified: q.Get("verified") != "false", expires: time.Now().Add(2 * time.Minute)}
		mu.Unlock()
		back.Set("code", code)
	}
	redirect.RawQuery = back.Encode()
	http.Redirect(w, r, redirect.String(), http.StatusFound)
}

func tokenEndpoint(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost || r.ParseForm() != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid_request"})
		return
	}
	id, sec, ok := r.BasicAuth()
	if !ok {
		id, sec = r.PostForm.Get("client_id"), r.PostForm.Get("client_secret")
	}
	if id != clientID || sec != secret {
		writeJSON(w, 401, map[string]string{"error": "invalid_client"})
		return
	}
	mu.Lock()
	g, found := grants[r.PostForm.Get("code")]
	delete(grants, r.PostForm.Get("code")) // single use
	mu.Unlock()
	sum := sha256.Sum256([]byte(r.PostForm.Get("code_verifier")))
	if !found || time.Now().After(g.expires) || r.PostForm.Get("grant_type") != "authorization_code" ||
		r.PostForm.Get("redirect_uri") != g.redirect || b64(sum[:]) != g.challenge {
		writeJSON(w, 400, map[string]string{"error": "invalid_grant"})
		return
	}
	now := time.Now()
	sub := sha256.Sum256([]byte(g.email))
	claims := jwt.MapClaims{"iss": issuer, "aud": clientID, "sub": "mock-" + b64(sub[:12]), "email": g.email,
		"email_verified": g.verified, "name": strings.Split(g.email, "@")[0], "nonce": g.nonce,
		"iat": now.Unix(), "exp": now.Add(5 * time.Minute).Unix()}
	if g.hd != "" {
		claims["hd"] = g.hd
	}
	t := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	t.Header["kid"] = "mock"
	idToken, err := t.SignedString(key)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "server_error"})
		return
	}
	writeJSON(w, 200, map[string]any{"access_token": token(), "token_type": "Bearer", "expires_in": 300, "id_token": idToken})
}

func main() {
	http.HandleFunc("/.well-known/openid-configuration", discovery)
	http.HandleFunc("/jwks", jwks)
	http.HandleFunc("/authorize", authorize)
	http.HandleFunc("/token", tokenEndpoint)
	http.HandleFunc("/health", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })
	log.Printf("mock OIDC provider: issuer %s, browser URL %s", issuer, public)
	srv := &http.Server{Addr: ":9998", ReadHeaderTimeout: 5 * time.Second}
	log.Fatal(srv.ListenAndServe())
}
