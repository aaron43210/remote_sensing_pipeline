package secretbox

import (
	"bytes"
	"strings"
	"testing"
)

func TestSealOpenIsBoundToContext(t *testing.T) {
	b, err := New(strings.Repeat("ab", 32))
	if err != nil {
		t.Fatal(err)
	}
	sealed := b.Seal([]byte("edl-token"), "earthdata:user:1")
	if got, err := b.Open(sealed, "earthdata:user:1"); err != nil || string(got) != "edl-token" {
		t.Fatalf("round trip: %q, %v", got, err)
	}
	if _, err := b.Open(sealed, "earthdata:user:2"); err == nil {
		t.Error("opened a value sealed for another user")
	}
	if bytes.Equal(sealed, b.Seal([]byte("edl-token"), "earthdata:user:1")) {
		t.Error("same ciphertext twice: nonce reused")
	}
	sealed[len(sealed)-1] ^= 1
	if _, err := b.Open(sealed, "earthdata:user:1"); err == nil {
		t.Error("opened a tampered value")
	}
	if _, err := b.Open(sealed[:10], "earthdata:user:1"); err == nil {
		t.Error("opened a truncated value")
	}
}

func TestRejectsBadKeys(t *testing.T) {
	for _, k := range []string{"", "abc", strings.Repeat("zz", 32), strings.Repeat("ab", 16), strings.Repeat("ab", 33)} {
		if _, err := New(k); err == nil {
			t.Errorf("accepted key %q", k)
		}
	}
}
