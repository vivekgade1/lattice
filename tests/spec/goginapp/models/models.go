// Package models is the goginapp schema: GORM structs over SQLite (glebarez/sqlite).
//
// The truth of this fixture is T-S2 in docs/results/2026-10-17-go-parity-gaps-prereg.md.
// GORM has no association tag here on purpose: the owner of runs is read from the routes'
// evidence (owner_uses), not from a declared foreign key.
package models

// User is a caller. Password and Token never leave the service (json:"-").
type User struct {
	ID       uint   `gorm:"primaryKey" json:"id"`
	Name     string `gorm:"size:64;not null;uniqueIndex" json:"name"`
	Password string `gorm:"size:128;not null" json:"-"`
	Token    string `gorm:"size:64;not null;uniqueIndex" json:"-"`
}

// Config is shared by every caller.
type Config struct {
	ID   uint   `gorm:"primaryKey" json:"id"`
	Name string `gorm:"size:64;not null" json:"name"`
}

// Run belongs to the caller that queued it (UserID).
type Run struct {
	ID       uint   `gorm:"primaryKey" json:"id"`
	ConfigID uint   `gorm:"not null;index" json:"config_id"`
	UserID   uint   `gorm:"not null;index" json:"user_id"`
	Status   string `gorm:"size:16;not null;default:queued" json:"status"`
}
