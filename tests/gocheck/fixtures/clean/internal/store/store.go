// Package store: data access.
package store

import (
	"errors"

	"example.com/clean/internal/models"
	"gorm.io/gorm"
)

var ErrNotFound = errors.New("not found")
var ErrConflict = errors.New("conflict")

type Store struct {
	db *gorm.DB
}

func New(db *gorm.DB) *Store { return &Store{db: db} }

func (s *Store) WithTx(tx *gorm.DB) *Store { return &Store{db: tx} }

func ownedBy(accountID uint) func(*gorm.DB) *gorm.DB {
	return func(db *gorm.DB) *gorm.DB { return db.Where("account_id = ?", accountID) }
}

func (s *Store) Pipeline(accountID, id uint) (*models.Pipeline, error) {
	var p models.Pipeline
	err := s.db.Scopes(ownedBy(accountID)).First(&p, id).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return nil, ErrNotFound
	}
	return &p, err
}

func (s *Store) Pipelines(accountID uint, limit, offset int) ([]models.Pipeline, error) {
	var ps []models.Pipeline
	err := s.db.Where("account_id = ?", accountID).Order("id").Limit(limit).Offset(offset).Find(&ps).Error
	return ps, err
}

func (s *Store) CreatePipeline(p *models.Pipeline) error {
	return s.db.Create(p).Error
}

func (s *Store) AddRun(r *models.Run) error {
	return s.db.Create(r).Error
}

// Transition moves a run between states only from the states given.
func (s *Store) Transition(id uint, from []models.RunStatus, to models.RunStatus) error {
	res := s.db.Model(&models.Run{}).Where("id = ? AND status IN ?", id, from).Update("status", to)
	if res.Error != nil {
		return res.Error
	}
	if res.RowsAffected == 0 {
		return ErrConflict
	}
	return nil
}

func (s *Store) Run(pipelineID, id uint) (*models.Run, error) {
	var r models.Run
	if err := s.db.Where("pipeline_id = ?", pipelineID).First(&r, id).Error; err != nil {
		return nil, ErrNotFound
	}
	return &r, nil
}
