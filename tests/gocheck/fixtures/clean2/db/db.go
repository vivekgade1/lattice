// Package db holds the connection and the models.
package db

import "gorm.io/gorm"

var DB *gorm.DB

type Ticket struct {
	gorm.Model
	ReporterID uint   `json:"reporter_id"`
	Title      string `json:"title" gorm:"size:200"`
	State      string `json:"state"`
	Secret     string `json:"-"`
}

type Note struct {
	gorm.Model
	TicketID uint   `json:"ticket_id"`
	Text     string `json:"text"`
}

const (
	Open     = "open"
	Assigned = "assigned"
	Resolved = "resolved"
	Closed   = "closed"
)

// Allowed is the ticket workflow.
var Allowed = map[string]map[string]bool{
	Open:     {Assigned: true, Closed: true},
	Assigned: {Resolved: true, Open: true},
	Resolved: {Closed: true, Assigned: true},
}
