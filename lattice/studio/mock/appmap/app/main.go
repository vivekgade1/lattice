// A small Gin + GORM app for the implementation diff's tests (a copy of the workflow layer's
// fixture, plus a /health route and an unspecified /api/stats route): client and server ids,
// a from-state rule, an id only a list answers, a nested body, a precondition guard, a
// conditional route and a plumbing route.
package main

import (
	"os"

	"github.com/gin-gonic/gin"
	"gorm.io/driver/mysql"
	"gorm.io/gorm"

	"example.com/wf/api"
	"example.com/wf/models"
)

func main() {
	db, err := gorm.Open(mysql.Open(os.Getenv("DSN")), &gorm.Config{})
	if err != nil {
		panic(err)
	}
	if err := models.Migrate(db); err != nil {
		panic(err)
	}
	r := gin.New()
	r.GET("/health", func(c *gin.Context) { c.Status(200) })
	api.Mount(r, db)
	if os.Getenv("TEST_CLOCK") != "" {
		r.PUT("/test/clock", func(c *gin.Context) { c.Status(204) })
	}
	_ = r.Run()
}
