// Package svc is the sessions service: one gRPC service over GORM and pure-Go SQLite.
package svc

// User is a person who can log in.
type User struct {
	ID       uint   `gorm:"primaryKey"`
	Name     string `gorm:"uniqueIndex;not null"`
	Password string `gorm:"not null"`
}

// Session is one login: the token a caller presents, and whose it is.
type Session struct {
	Token  string `gorm:"primaryKey"`
	UserID uint   `gorm:"not null;index"`
}
