// Package models is the tasks service's GORM models.
package models

import "time"

type TaskState string

const (
	StateTodo    TaskState = "todo"
	StateDoing   TaskState = "doing"
	StateDone    TaskState = "done"
	StateDropped TaskState = "dropped"
)

type User struct {
	ID           uint   `json:"id" gorm:"primaryKey"`
	Email        string `json:"email" gorm:"uniqueIndex"`
	PasswordHash string `json:"password_hash"`
}

type Task struct {
	ID        uint      `json:"id" gorm:"primaryKey"`
	OwnerID   uint      `json:"owner_id" gorm:"index"`
	Title     string    `json:"title"`
	State     TaskState `json:"state"`
	CreatedAt time.Time `json:"created_at"`
}

type Comment struct {
	ID     uint   `json:"id" gorm:"primaryKey"`
	TaskID uint   `json:"task_id"`
	Body   string `json:"body"`
}
