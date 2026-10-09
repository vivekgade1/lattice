package main

import "gorm.io/gorm"

type User struct {
	ID           uint   `json:"id"`
	Name         string `json:"name"`
	PasswordHash string `json:"-"`
}

type Job struct {
	ID     uint   `json:"id"`
	UserID uint   `json:"user_id"`
	Name   string `json:"name"`
	Status string `json:"status"`
}

type JobEvent struct {
	ID    uint   `json:"id"`
	JobID uint   `json:"job_id"`
	Kind  string `json:"kind"`
}

type Handler struct {
	db *gorm.DB
}
