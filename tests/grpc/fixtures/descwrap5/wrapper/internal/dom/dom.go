// Package dom holds the domain errors.
package dom

import "errors"

var (
	ErrMissing = errors.New("missing")
	ErrInvalid = errors.New("invalid")
)
