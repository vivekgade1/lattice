package models

import "gorm.io/gorm"

type User struct {
	ID    uint64 `gorm:"primaryKey"`
	Email string `gorm:"size:200;not null;uniqueIndex"`
}

// Project has a client-chosen key.
type Project struct {
	ID      string `gorm:"primaryKey;size:36"`
	OwnerID uint64 `gorm:"not null;index"`
	Name    string `gorm:"size:80;not null"`
	Kind    string `gorm:"size:10;not null"`
	Status  string `gorm:"size:10;not null"`
}

// Ticket has a server-assigned key.
type Ticket struct {
	ID        uint64 `gorm:"primaryKey"`
	ProjectID string `gorm:"size:36;not null;index"`
	Subject   string `gorm:"size:80;not null"`
}

// Job is a state machine: queued -> active -> done.
type Job struct {
	ID      uint64 `gorm:"primaryKey"`
	OwnerID uint64 `gorm:"not null"`
	Name    string `gorm:"size:80;not null"`
	Status  string `gorm:"size:10;not null"`
}

// Run belongs to an active job.
type Run struct {
	ID    uint64 `gorm:"primaryKey"`
	JobID uint64 `gorm:"not null;index"`
	State string `gorm:"size:10;not null"`
}

func Migrate(db *gorm.DB) error {
	return db.AutoMigrate(&User{}, &Project{}, &Ticket{}, &Job{}, &Run{})
}
