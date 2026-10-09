// Package models: a pipeline service's tables.
package models

import (
	"time"

	"gorm.io/gorm"
)

type RunStatus string

const (
	RunPending   RunStatus = "pending"
	RunActive    RunStatus = "active"
	RunSucceeded RunStatus = "succeeded"
	RunFailed    RunStatus = "failed"
	RunCancelled RunStatus = "cancelled"
)

type Account struct {
	gorm.Model
	Email        string `gorm:"uniqueIndex;size:254" json:"email"`
	PasswordHash string `json:"-"`
	Role         string `json:"role"`
}

type Pipeline struct {
	ID        uint      `gorm:"primaryKey" json:"id"`
	AccountID uint      `gorm:"index;not null" json:"account_id"`
	Name      string    `gorm:"size:100" json:"name"`
	CreatedAt time.Time `json:"created_at"`
}

type Run struct {
	ID         uint      `gorm:"primaryKey" json:"id"`
	PipelineID uint      `gorm:"index" json:"pipeline_id"`
	Status     RunStatus `gorm:"size:20" json:"status"`
	Attempt    int       `json:"attempt"`
}

func (Run) TableName() string { return "pipeline_runs" }
