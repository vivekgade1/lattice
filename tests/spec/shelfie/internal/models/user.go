// Package models holds the persisted types; their JSON tags are the shapes the API answers with.
package models

// User is a person who can log in. Password is stored but never returned by any route.
type User struct {
	ID          uint   `gorm:"primaryKey" json:"id"`
	Username    string `gorm:"not null;uniqueIndex" json:"username"`
	Password    string `gorm:"not null" json:"-"`
	DisplayName string `gorm:"not null" json:"display_name"`
}
