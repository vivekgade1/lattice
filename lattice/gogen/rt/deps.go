//go:build tools

package rt

// modernc.org/sqlite is required by the generated apps (a later part of this module).
// This file keeps it in go.mod and go.sum (go mod tidy reads every build tag) so they
// build offline, without linking it into the runtime until something uses it.
import _ "modernc.org/sqlite"
