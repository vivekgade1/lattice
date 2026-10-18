// goginapp: the goapp service (tests/spec/goapp) on Gin + GORM + glebarez/sqlite, for the
// Go parity gaps (Spike J, node J1). Environment: PORT, DB_PATH; STARTUP_FAIL=1 exits at once.
package main

import (
	"fmt"
	"os"

	"github.com/gin-gonic/gin"
	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"

	"example.com/goginapp/api"
	"example.com/goginapp/models"
)

func main() {
	if os.Getenv("STARTUP_FAIL") != "" {
		fmt.Fprintln(os.Stderr, "goginapp: refusing to start: STARTUP_FAIL is set")
		os.Exit(3)
	}
	port, path := os.Getenv("PORT"), os.Getenv("DB_PATH")
	if port == "" || path == "" {
		fmt.Fprintln(os.Stderr, "goginapp: PORT and DB_PATH are required")
		os.Exit(2)
	}
	db, err := gorm.Open(sqlite.Open("file:"+path+"?_pragma=busy_timeout(10000)"),
		&gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	if err != nil {
		fmt.Fprintln(os.Stderr, "goginapp: cannot open the database:", err)
		os.Exit(1)
	}
	if sqlDB, err := db.DB(); err == nil {
		sqlDB.SetMaxOpenConns(1)
	}
	if err := db.AutoMigrate(&models.User{}, &models.Config{}, &models.Run{}); err != nil {
		fmt.Fprintln(os.Stderr, "goginapp: migration failed:", err)
		os.Exit(1)
	}
	gin.SetMode(gin.ReleaseMode)
	r := gin.New()
	r.Use(gin.Recovery())
	api.Mount(r, db)
	if err := r.Run("127.0.0.1:" + port); err != nil {
		fmt.Fprintln(os.Stderr, "goginapp:", err)
		os.Exit(1)
	}
}
