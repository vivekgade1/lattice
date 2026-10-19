// Package store opens the SQLite file and creates the schema.
package store

import (
	"log"
	"os"
	"time"

	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"

	"example.com/shelfie/internal/models"
)

// Open connects to the SQLite file at path (created when missing) and creates any missing table or index.
// A file that already holds the same schema is left as it is.
func Open(path string) (*gorm.DB, error) {
	db, err := gorm.Open(sqlite.Open(path+"?_pragma=busy_timeout(5000)"), &gorm.Config{
		DisableForeignKeyConstraintWhenMigrating: true,
		NowFunc:                                  func() time.Time { return time.Now().UTC() },
		Logger: logger.New(log.New(os.Stderr, "", log.LstdFlags), logger.Config{
			SlowThreshold:             time.Second,
			LogLevel:                  logger.Warn,
			IgnoreRecordNotFoundError: true,
		}),
	})
	if err != nil {
		return nil, err
	}
	sqlDB, err := db.DB()
	if err != nil {
		return nil, err
	}
	// One connection serialises writes, so a request never meets "database is locked" from this process.
	sqlDB.SetMaxOpenConns(1)

	if err := db.AutoMigrate(&models.User{}, &models.Shelf{}, &models.Book{}, &models.Note{}); err != nil {
		return nil, err
	}
	return db, nil
}
