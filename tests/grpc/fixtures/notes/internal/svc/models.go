// Package svc is the notes service: the two gRPC services over GORM and pure-Go SQLite.
package svc

// User is a person who owns notes.
type User struct {
	ID       uint   `gorm:"primaryKey"`
	Name     string `gorm:"uniqueIndex;not null"`
	Password string `gorm:"not null"`
	Token    string `gorm:"uniqueIndex;not null"`
}

// Note is one note. State moves draft -> published -> archived, or draft -> archived.
type Note struct {
	ID        uint   `gorm:"primaryKey"`
	UserID    uint   `gorm:"not null;index"`
	Title     string `gorm:"not null;size:80"`
	Body      string
	State     string `gorm:"not null"`
	Priority  int
	ClientRev int64
}
