package handlers

import (
	"testing"

	"remote_sensing_production/pkg/config"
)

func TestSafeFromKeepsRedirectsOnSite(t *testing.T) {
	for in, want := range map[string]string{
		"/jobs/abc":            "/jobs/abc",
		"/jobs?x=1&y=2":        "/jobs?x=1&y=2",
		"":                     "/",
		"//evil.example":       "/",
		"/\\evil.example":      "/",
		"https://evil.example": "/",
		"javascript:alert(1)":  "/",
		"/a b":                 "/",
		"/%0d%0aSet-Cookie:x":  "/",
	} {
		if got := safeFrom(in); got != want {
			t.Errorf("safeFrom(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestAllowedEmails(t *testing.T) {
	s := &Server{Cfg: config.Config{AdminEmail: "boss@gmail.com", AllowedEmails: []string{"friend@outlook.com"},
		AllowedDomains: []string{"lab.org", "gmail.com"}}}
	cases := []struct {
		email, hd string
		want      bool
	}{
		{"boss@gmail.com", "", true},
		{"friend@outlook.com", "", true},
		{"someone@lab.org", "lab.org", true},
		{"someone@lab.org", "", false}, // a Google account on lab.org that lab.org's Workspace does not own
		{"anyone@gmail.com", "", true},
		{"anyone@gmail.com", "evil.org", false},
		{"stranger@other.org", "other.org", false},
		{"friend@outlook.com.evil.org", "", false},
	}
	for _, c := range cases {
		if got := s.allowed(c.email, c.hd); got != c.want {
			t.Errorf("allowed(%q, hd=%q) = %v, want %v", c.email, c.hd, got, c.want)
		}
	}
	open := &Server{Cfg: config.Config{OpenSignup: true}}
	if !open.allowed("x@y.z", "") {
		t.Error("OPEN_SIGNUP should allow any verified account")
	}
}

func TestProductionRefusesUnsafeSignIn(t *testing.T) {
	good := config.Config{Env: "prod", OIDCIssuer: config.GoogleIssuer, OIDCClientID: "id", OIDCClientSecret: "s",
		OIDCRedirectURL: "https://maps.example.com/api/auth/google/callback", CookieSecure: true, AdminEmail: "a@b.c"}
	if err := good.CheckSignIn(); err != nil {
		t.Fatalf("good config refused: %v", err)
	}
	bad := map[string]func(c *config.Config){
		"no secret":      func(c *config.Config) { c.OIDCClientSecret = "" },
		"placeholder":    func(c *config.Config) { c.OIDCClientID = "CHANGE_ME" },
		"test issuer":    func(c *config.Config) { c.OIDCIssuer = "http://mock-oidc:9998" },
		"http redirect":  func(c *config.Config) { c.OIDCRedirectURL = "http://maps.example.com/cb" },
		"insecure":       func(c *config.Config) { c.CookieSecure = false },
		"nobody allowed": func(c *config.Config) { c.AdminEmail = "" },
	}
	for name, mutate := range bad {
		c := good
		mutate(&c)
		if c.CheckSignIn() == nil {
			t.Errorf("%s: production start should be refused", name)
		}
	}
}
