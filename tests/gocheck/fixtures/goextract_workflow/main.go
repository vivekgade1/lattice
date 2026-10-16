// A small Gin + GORM app for the workflow layer's extractor: client and server ids, a
// from-state rule, an id only a list answers, a nested body, a precondition guard and a
// conditional route.
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
	api.Mount(r, db)
	if os.Getenv("TEST_CLOCK") != "" {
		r.PUT("/test/clock", func(c *gin.Context) { c.Status(204) })
	}
	_ = r.Run()
}
