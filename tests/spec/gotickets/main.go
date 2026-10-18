// gotickets: a small ticket service on Gin + GORM + glebarez/sqlite.
// Environment: PORT, DB_PATH (both required), TICKETS_SECRET (the token key; a constant default).
package main

import (
	"fmt"
	"io"
	"os"

	"github.com/gin-gonic/gin"
	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"

	"example.com/gotickets/api"
	"example.com/gotickets/models"
)

// defaultSecret signs the tokens when TICKETS_SECRET is unset.
const defaultSecret = "gotickets-dev-secret"

func main() {
	port, path := os.Getenv("PORT"), os.Getenv("DB_PATH")
	if port == "" || path == "" {
		fmt.Fprintln(os.Stderr, "gotickets: PORT and DB_PATH are required")
		os.Exit(2)
	}
	secret := os.Getenv("TICKETS_SECRET")
	if secret == "" {
		secret = defaultSecret
	}
	db, err := gorm.Open(sqlite.Open("file:"+path+"?_pragma=busy_timeout(10000)"),
		&gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	if err != nil {
		fmt.Fprintln(os.Stderr, "gotickets: cannot open the database:", err)
		os.Exit(1)
	}
	if sqlDB, err := db.DB(); err == nil {
		sqlDB.SetMaxOpenConns(1)
	}
	if err := db.AutoMigrate(&models.User{}, &models.Queue{}, &models.Ticket{}, &models.Handoff{}); err != nil {
		fmt.Fprintln(os.Stderr, "gotickets: migration failed:", err)
		os.Exit(1)
	}
	gin.SetMode(gin.ReleaseMode)
	r := gin.New()
	r.Use(gin.LoggerWithWriter(io.Discard), gin.Recovery())
	api.Mount(r, db, []byte(secret))
	if err := r.Run("127.0.0.1:" + port); err != nil {
		fmt.Fprintln(os.Stderr, "gotickets:", err)
		os.Exit(1)
	}
}
