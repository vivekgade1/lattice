// Package auth issues and checks the signed bearer tokens handed out by the login route.
package auth

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"
)

// ErrInvalid is answered for every token that is malformed, forged or expired.
var ErrInvalid = errors.New("invalid token")

// Signer signs tokens of the form base64(userID:expiry).base64(HMAC-SHA256).
type Signer struct {
	secret []byte
	ttl    time.Duration
}

// NewSigner returns a Signer for the secret; tokens live for ttl.
func NewSigner(secret string, ttl time.Duration) *Signer {
	return &Signer{secret: []byte(secret), ttl: ttl}
}

// Issue returns a token for the user, valid from now for the signer's lifetime.
func (s *Signer) Issue(userID uint, now time.Time) string {
	payload := fmt.Sprintf("%d:%d", userID, now.Add(s.ttl).Unix())
	return base64.RawURLEncoding.EncodeToString([]byte(payload)) + "." + s.sign(payload)
}

// Verify returns the user id inside a token that is genuine and not yet expired.
func (s *Signer) Verify(token string, now time.Time) (uint, error) {
	encoded, sig, found := strings.Cut(token, ".")
	if !found {
		return 0, ErrInvalid
	}
	raw, err := base64.RawURLEncoding.DecodeString(encoded)
	if err != nil {
		return 0, ErrInvalid
	}
	payload := string(raw)
	if !hmac.Equal([]byte(sig), []byte(s.sign(payload))) {
		return 0, ErrInvalid
	}
	idText, expText, found := strings.Cut(payload, ":")
	if !found {
		return 0, ErrInvalid
	}
	id, err := strconv.ParseUint(idText, 10, 32)
	if err != nil {
		return 0, ErrInvalid
	}
	exp, err := strconv.ParseInt(expText, 10, 64)
	if err != nil || now.Unix() >= exp {
		return 0, ErrInvalid
	}
	return uint(id), nil
}

func (s *Signer) sign(payload string) string {
	mac := hmac.New(sha256.New, s.secret)
	mac.Write([]byte(payload))
	return base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}
