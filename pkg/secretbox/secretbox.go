// Package secretbox encrypts small secrets (users' NASA Earthdata tokens) with
// AES-256-GCM. Every value is bound to where it belongs by the additional data
// (e.g. "earthdata:user:42"), so a ciphertext copied to another row or Redis key
// does not decrypt. Format: 12-byte nonce || ciphertext || 16-byte tag, the
// same layout Python's cryptography AESGCM reads (ingestion/earthdata.py).
package secretbox

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/hex"
	"errors"
)

type Box struct{ aead cipher.AEAD }

// New takes the key as 64 hex characters (openssl rand -hex 32).
func New(hexKey string) (*Box, error) {
	key, err := hex.DecodeString(hexKey)
	if err != nil || len(key) != 32 {
		return nil, errors.New("CREDENTIALS_KEY must be 64 hex characters (openssl rand -hex 32)")
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	return &Box{aead: aead}, nil
}

// Seal encrypts plain for context, with a fresh random nonce every time.
func (b *Box) Seal(plain []byte, context string) []byte {
	nonce := make([]byte, b.aead.NonceSize())
	rand.Read(nonce)
	return b.aead.Seal(nonce, nonce, plain, []byte(context))
}

// Open fails if the value was tampered with or sealed for another context.
func (b *Box) Open(sealed []byte, context string) ([]byte, error) {
	n := b.aead.NonceSize()
	if len(sealed) < n+b.aead.Overhead() {
		return nil, errors.New("secretbox: ciphertext too short")
	}
	return b.aead.Open(nil, sealed[:n], sealed[n:], []byte(context))
}
