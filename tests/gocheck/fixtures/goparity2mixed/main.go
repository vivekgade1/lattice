// goparity2mixed: a parse-only Gin module whose bind refusals disagree (400 and 422), for
// E3's "the statuses disagree: nothing changes" case (Spike J, node J2). Never built or run.
package main

import (
	"net/http"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

// Item is a row.
type Item struct {
	ID   uint   `gorm:"primaryKey" json:"id"`
	Name string `gorm:"size:64" json:"name"`
}

type itemReq struct {
	Name string `json:"name" binding:"required,max=64"`
}

type pageReq struct {
	Page int `form:"page" binding:"omitempty,min=1"`
}

func main() {
	db, err := gorm.Open(nil, &gorm.Config{})
	if err != nil {
		panic(err)
	}
	r := gin.New()
	r.POST("/items", CreateItem(db))
	r.GET("/items", ListItems(db))
	_ = r.Run()
}

// CreateItem refuses a bad body with 400.
func CreateItem(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req itemReq
		if err := c.ShouldBindJSON(&req); err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"detail": "bad body"})
			return
		}
		it := Item{Name: req.Name}
		db.Create(&it)
		c.JSON(http.StatusCreated, it)
	}
}

// ListItems refuses a bad query with 422.
func ListItems(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var q pageReq
		if err := c.ShouldBindQuery(&q); err != nil {
			c.JSON(http.StatusUnprocessableEntity, gin.H{"detail": "bad page"})
			return
		}
		var items []Item
		db.Find(&items)
		c.JSON(http.StatusOK, items)
	}
}
