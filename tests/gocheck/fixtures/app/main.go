package main

import (
	"example.com/tasks/internal/handlers"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

func auth(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		c.Set("uid", uint(1))
		c.Next()
	}
}

func router(db *gorm.DB) *gin.Engine {
	r := gin.New()
	r.GET("/health", func(c *gin.Context) { c.Status(200) })
	api := &handlers.API{DB: db}
	api.Register(r.Group("", auth(db)))
	return r
}

func main() {}
