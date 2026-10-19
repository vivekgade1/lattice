package models

import "time"

// Note is a remark a user wrote about a book. It carries its author's id and the book it is about.
type Note struct {
	ID        uint      `gorm:"primaryKey" json:"id"`
	UserID    uint      `gorm:"not null;index" json:"user_id"`
	BookID    uint      `gorm:"not null;index" json:"book_id"`
	Body      string    `gorm:"not null" json:"body"`
	CreatedAt time.Time `json:"created_at"`
}
