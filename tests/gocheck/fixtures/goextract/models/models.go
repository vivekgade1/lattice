package models

import (
	"time"

	"gorm.io/gorm"
)

// User is a row of users.
type User struct {
	ID           uint   `gorm:"primaryKey"`
	Email        string `gorm:"uniqueIndex;size:255;not null"`
	PasswordHash string `gorm:"not null" json:"-"`
	IsAdmin      bool   `gorm:"default:false"`
}

// Item is a row of items: gorm.Model's columns plus its own.
type Item struct {
	gorm.Model
	OwnerID uint    `gorm:"index;not null"`
	Name    string  `gorm:"size:100;not null"`
	State   string  `gorm:"type:varchar(16);not null;default:draft"`
	Price   float64 `gorm:"not null"`
	Tags    []Tag   `gorm:"foreignKey:ItemID"`
}

// Tag is a row of item_tags.
type Tag struct {
	ID     uint      `gorm:"primaryKey"`
	ItemID uint      `gorm:"not null;uniqueIndex:idx_item_label"`
	Label  string    `gorm:"type:varchar(32);not null;uniqueIndex:idx_item_label"`
	Seen   time.Time `gorm:"type:date"`
}

// TableName of Tag.
func (Tag) TableName() string { return "item_tags" }

// Audit is only ever a chain's destination: it is a table by use.
type Audit struct {
	ID     uint `gorm:"primaryKey"`
	Action string
	Actor  uint
}

// Skipped is no table: nothing migrates, names or writes it.
type Skipped struct {
	Name string
}

// Migrate creates the tables.
func Migrate(db *gorm.DB) error {
	return db.AutoMigrate(&User{}, &Item{}, &Tag{})
}
