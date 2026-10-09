package models

type JobStatus string

const (
	StatusQueued    JobStatus = "queued"
	StatusRunning   JobStatus = "running"
	StatusSucceeded JobStatus = "succeeded"
	StatusFailed    JobStatus = "failed"
	StatusCancelled JobStatus = "cancelled"
)

type Job struct {
	ID     uint      `json:"id" gorm:"primaryKey"`
	UserID uint      `json:"user_id"`
	Status JobStatus `json:"status" gorm:"column:status"`
}

func (Job) TableName() string { return "jobs" }

// Transitions is the app's own table of legal moves.
var Transitions = map[JobStatus][]JobStatus{
	StatusQueued:  {StatusRunning, StatusCancelled},
	StatusRunning: {StatusSucceeded, StatusFailed, StatusCancelled},
}
