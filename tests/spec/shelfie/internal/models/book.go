package models

import "time"

// Book sits on a shelf and belongs to a user only through that shelf. Status is one of
// "queued" (new), "reading" and "finished"; the routes move it queued -> reading -> finished.
type Book struct {
	ID         uint       `gorm:"primaryKey" json:"id"`
	ShelfID    uint       `gorm:"not null;index" json:"shelf_id"`
	Title      string     `gorm:"not null" json:"title"`
	Author     string     `gorm:"not null" json:"author"`
	Status     string     `gorm:"not null" json:"status"`
	Rating     *int       `json:"rating"`
	CreatedAt  time.Time  `json:"created_at"`
	FinishedAt *time.Time `json:"finished_at"`
}
