// Package api holds a request body declared outside the handler's package.
package api

type UserIn struct {
	Email    string  `json:"email"`
	Nickname *string `json:"nickname"`
}
