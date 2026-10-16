// A small Gin + GORM app whose idioms the Go extractor must read: a handler factory, a
// generic wrapper, bound structs, middleware groups, a conditional registration, a route
// table built in a loop (not readable), a build-tagged file, and a Register nothing calls.
package main

import (
	"os"

	"github.com/gin-gonic/gin"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"

	"example.com/gx/api"
	"example.com/gx/models"
)

func main() {
	db, err := gorm.Open(sqlite.Open("app.db"), &gorm.Config{})
	if err != nil {
		panic(err)
	}
	if err := models.Migrate(db); err != nil {
		panic(err)
	}
	r := gin.Default()
	api.Mount(r, db)
	if os.Getenv("DEBUG") != "" {
		r.GET("/debug/ping", func(c *gin.Context) { c.String(200, "pong") })
	}
	r.NoRoute(func(c *gin.Context) { c.JSON(404, gin.H{"error": "not found"}) })
	_ = r.Run()
}
