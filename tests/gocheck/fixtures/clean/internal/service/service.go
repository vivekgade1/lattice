// Package service: the pipeline rules.
package service

import (
	"errors"

	"example.com/clean/internal/models"
	"example.com/clean/internal/store"
	"gorm.io/gorm"
)

var ErrInvalid = errors.New("invalid")

type Service struct {
	db    *gorm.DB
	store *store.Store
}

func New(db *gorm.DB) *Service { return &Service{db: db, store: store.New(db)} }

// StartRun queues a run of an owned pipeline, in one transaction.
func (s *Service) StartRun(accountID, pipelineID uint) (*models.Run, error) {
	var run models.Run
	err := s.db.Transaction(func(tx *gorm.DB) error {
		st := s.store.WithTx(tx)
		run = models.Run{PipelineID: pipelineID, Status: models.RunPending}
		if err := st.AddRun(&run); err != nil {
			return err
		}
		// checked after the write: an error rolls the run back
		_, err := st.Pipeline(accountID, pipelineID)
		return err
	})
	if err != nil {
		return nil, err
	}
	return &run, nil
}

func (s *Service) Cancel(accountID, pipelineID, runID uint) error {
	if _, err := s.store.Pipeline(accountID, pipelineID); err != nil {
		return err
	}
	return s.store.Transition(runID, []models.RunStatus{models.RunPending, models.RunActive}, models.RunCancelled)
}

// Retry re-queues a failed run as a new attempt (a new row: the old one stays failed).
func (s *Service) Retry(accountID, pipelineID, runID uint) (*models.Run, error) {
	if _, err := s.store.Pipeline(accountID, pipelineID); err != nil {
		return nil, err
	}
	old, err := s.store.Run(pipelineID, runID)
	if err != nil {
		return nil, err
	}
	if old.Status != models.RunFailed {
		return nil, store.ErrConflict
	}
	next := models.Run{PipelineID: pipelineID, Status: models.RunPending, Attempt: old.Attempt + 1}
	if err := s.store.AddRun(&next); err != nil {
		return nil, err
	}
	return &next, nil
}
