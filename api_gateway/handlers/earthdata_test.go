package handlers

import (
	"encoding/base64"
	"testing"
)

func jwtWith(payload string) string {
	enc := base64.RawURLEncoding.EncodeToString
	return enc([]byte(`{"typ":"JWT","alg":"RS256"}`)) + "." + enc([]byte(payload)) + ".c2ln"
}

func TestEdlClaims(t *testing.T) {
	uid, exp, ok := edlClaims(jwtWith(`{"type":"User","uid":"jdoe","exp":1893456000,"iss":"Earthdata Login"}`))
	if !ok || uid != "jdoe" || exp == nil || exp.Unix() != 1893456000 {
		t.Fatalf("got %q %v %v", uid, exp, ok)
	}
	if _, exp, ok := edlClaims(jwtWith(`{"uid":"jdoe"}`)); !ok || exp != nil {
		t.Errorf("no exp claim: %v %v", exp, ok)
	}
	for _, bad := range []string{"", "opaque-token", "a.b", "a.!!!.c", jwtWith(`not json`)} {
		if _, _, ok := edlClaims(bad); ok {
			t.Errorf("accepted %q as a JWT", bad)
		}
	}
}
