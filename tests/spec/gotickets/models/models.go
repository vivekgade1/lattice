// Package models is the gotickets schema: GORM structs over SQLite (glebarez/sqlite).
//
// GORM has no association tag here on purpose: the owner of tickets is read from the
// routes' evidence, not from a declared foreign key.
package models

// User is a caller. PasswordHash never leaves the service (json:"-").
type User struct {
	ID           uint   `gorm:"primaryKey" json:"id"`
	Name         string `gorm:"size:64;uniqueIndex" json:"name"`
	PasswordHash string `gorm:"size:128" json:"-"`
}

// Queue is shared by every caller.
type Queue struct {
	ID   uint   `gorm:"primaryKey" json:"id"`
	Name string `json:"name"`
}

// Ticket belongs to the caller that reported it (ReporterID).
type Ticket struct {
	ID         uint   `gorm:"primaryKey" json:"id"`
	QueueID    uint   `gorm:"index" json:"queue_id"`
	ReporterID uint   `gorm:"index" json:"reporter_id"`
	Title      string `gorm:"size:80" json:"title"`
	Status     string `gorm:"size:16;default:open" json:"status"`
}

// Handoff records a ticket handed from one user to another.
type Handoff struct {
	ID         uint `gorm:"primaryKey" json:"id"`
	TicketID   uint `gorm:"index" json:"ticket_id"`
	FromUserID uint `gorm:"index" json:"from_user_id"`
	ToUserID   uint `gorm:"index" json:"to_user_id"`
}
