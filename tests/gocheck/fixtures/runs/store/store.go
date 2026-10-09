// Package store: job runs.
package store

import (
	"time"

	"example.com/runs/states"
	"gorm.io/gorm"
)

const (
	RunQueued    = "queued"
	RunRunning   = "running"
	RunSucceeded = "succeeded"
	RunFailed    = "failed"
	RunCancelled = "cancelled"
)

type JobRun struct {
	ID         int64      `gorm:"primaryKey" json:"id"`
	Status     string     `json:"status"`
	FinishedAt *time.Time `json:"finished_at"`
}

// CancelRun is correct: a conditional update, its result says whether it wrote.
func CancelRun(db *gorm.DB, id int64, now time.Time) (bool, error) {
	res := db.Model(&JobRun{}).Where("id = ? AND status IN ?", id, []string{RunQueued, RunRunning}).
		Updates(map[string]any{"status": RunCancelled, "finished_at": now})
	return res.RowsAffected == 1, res.Error
}

// StartRun is correct too (> 0 form).
func StartRun(db *gorm.DB, id int64) (bool, error) {
	res := db.Model(&JobRun{}).Where("id = ? AND status = ?", id, RunQueued).Update("status", RunRunning)
	if res.Error != nil {
		return false, res.Error
	}
	return res.RowsAffected > 0, nil
}

// BadCancelAny cancels a finished run: succeeded -> cancelled has no edge.
func BadCancelAny(db *gorm.DB, id int64, now time.Time) (bool, error) {
	res := db.Model(&JobRun{}).Where("id = ? AND status IN ?", id, []string{RunQueued, RunRunning, RunSucceeded}).
		Updates(map[string]any{"status": RunCancelled, "finished_at": now})
	return res.RowsAffected == 1, res.Error
}

// BadFinishQueued: queued -> succeeded has no edge.
func BadFinishQueued(db *gorm.DB, id int64) error {
	return db.Model(&JobRun{}).Where("id = ? AND status = ?", id, RunQueued).Update("status", RunSucceeded).Error
}

// BadCancelGuarded guards a fetched row, then writes through Model(&JobRun{}) by id.
func BadCancelGuarded(db *gorm.DB, id int64, now time.Time) (bool, error) {
	var cur JobRun
	if err := db.First(&cur, id).Error; err != nil {
		return false, err
	}
	if cur.Status == RunCancelled {
		return false, nil
	}
	res := db.Model(&JobRun{}).Where("id = ?", id).Updates(map[string]any{"status": RunCancelled, "finished_at": now})
	return res.RowsAffected == 1, res.Error
}

// BadCancelGuardedByRowID: the same, keyed by cur.ID.
func BadCancelGuardedByRowID(db *gorm.DB, id int64) error {
	var cur JobRun
	if err := db.First(&cur, id).Error; err != nil {
		return err
	}
	if cur.Status != RunSucceeded {
		return nil
	}
	return db.Model(&JobRun{}).Where("id = ?", cur.ID).Update("status", RunQueued).Error
}

// OkGuardedOtherID: the write targets another id: not judged.
func OkGuardedOtherID(db *gorm.DB, id, other int64) error {
	var cur JobRun
	if err := db.First(&cur, id).Error; err != nil {
		return err
	}
	if cur.Status != RunSucceeded {
		return nil
	}
	return db.Model(&JobRun{}).Where("id = ?", other).Update("status", RunQueued).Error
}

// OkGuardedIDRebound: the id is rebound after the fetch: not judged.
func OkGuardedIDRebound(db *gorm.DB, id, other int64) error {
	var cur JobRun
	if err := db.First(&cur, id).Error; err != nil {
		return err
	}
	if cur.Status != RunSucceeded {
		return nil
	}
	id = other
	return db.Model(&JobRun{}).Where("id = ?", id).Update("status", RunQueued).Error
}

// OkGuardedLegal: running -> succeeded is legal.
func OkGuardedLegal(db *gorm.DB, id int64) error {
	var cur JobRun
	if err := db.First(&cur, id).Error; err != nil {
		return err
	}
	if cur.Status != RunRunning {
		return nil
	}
	return db.Model(&JobRun{}).Where("id = ?", id).Update("status", RunSucceeded).Error
}

// BadCancelPkgVar: the states come from another package's var slice.
func BadCancelPkgVar(db *gorm.DB, id int64) error {
	return db.Model(&JobRun{}).Where(states.ByIDAndState, id, states.Live).
		Update("status", states.Cancelled).Error
}

// BadCancelOtherPkgConsts: another package's constants in an inline slice.
func BadCancelOtherPkgConsts(db *gorm.DB, id int64) error {
	return db.Model(&JobRun{}).Where("job_runs.id = ? AND job_runs.status IN (?)", id,
		[]states.Status{states.Running, states.Succeeded}).Update("status", string(states.Cancelled)).Error
}
