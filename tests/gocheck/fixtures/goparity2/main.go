// goparity2: a parse-only Gin + GORM module for the Go parity gaps, round 2 (Spike J, node
// J2). It carries one case per decision E1-E6 and nothing else; it is never built or run.
// The truth of this fixture is truth.json, fixed before any round-2 library code.
//
//	E1  /a/:id parses the id (Atoi, error refused 400)        -> parse Int
//	    /b/:id parses it and ignores the error (`_`)          -> no key
//	    /c/:id never parses it                                -> no key
//	E2  /n1  binding:"omitempty,min=1,max=10"                  -> ge 0, le 10
//	    /n2  binding:"omitempty,min=3,max=10"                  -> ge null, le 10, one unread
//	    /n3  binding:"min=1,max=10"                            -> ge 1, le 10
//	E3  every bind refusal answers 422
//	E6  /notes sits under Use(gin.Recovery(), gin.LoggerWithWriter(...)) and reads every
//	    note unscoped; /guarded/notes sits under ext.Guard() (an import nothing resolves)
package main

import (
	"io"
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"

	ext "github.com/acme/guard"
)

// Note belongs to the caller that wrote it (OwnerID).
type Note struct {
	ID      uint   `gorm:"primaryKey" json:"id"`
	OwnerID uint   `gorm:"not null;index" json:"owner_id"`
	Body    string `gorm:"size:200" json:"body"`
}

type n1 struct {
	N int `form:"n" binding:"omitempty,min=1,max=10"`
}

type n2 struct {
	N int `form:"n" binding:"omitempty,min=3,max=10"`
}

type n3 struct {
	N int `form:"n" binding:"min=1,max=10"`
}

func main() {
	db, err := gorm.Open(nil, &gorm.Config{})
	if err != nil {
		panic(err)
	}
	if err := db.AutoMigrate(&Note{}); err != nil {
		panic(err)
	}
	r := gin.New()
	r.Use(gin.Recovery(), gin.LoggerWithWriter(io.Discard))
	r.GET("/a/:id", A)
	r.GET("/b/:id", B)
	r.GET("/c/:id", C)
	r.GET("/n1", N1)
	r.GET("/n2", N2)
	r.GET("/n3", N3)
	r.GET("/notes", ListNotes(db))
	guarded := r.Group("/guarded", ext.Guard())
	guarded.GET("/notes", ListGuardedNotes(db))
	_ = r.Run()
}

// A parses the id and refuses a bad one with 400.
func A(c *gin.Context) {
	id, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"detail": "bad id"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"id": id})
}

// B parses the id and ignores the error.
func B(c *gin.Context) {
	id, _ := strconv.Atoi(c.Param("id"))
	c.JSON(http.StatusOK, gin.H{"id": id})
}

// C never parses the id.
func C(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"id": c.Param("id")})
}

// N1 binds an omitempty,min=1 query.
func N1(c *gin.Context) {
	var q n1
	if err := c.ShouldBindQuery(&q); err != nil {
		c.JSON(http.StatusUnprocessableEntity, gin.H{"detail": "bad n"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"n": q.N})
}

// N2 binds an omitempty,min=3 query.
func N2(c *gin.Context) {
	var q n2
	if err := c.ShouldBindQuery(&q); err != nil {
		c.JSON(http.StatusUnprocessableEntity, gin.H{"detail": "bad n"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"n": q.N})
}

// N3 binds a min=1 query.
func N3(c *gin.Context) {
	var q n3
	if err := c.ShouldBindQuery(&q); err != nil {
		c.JSON(http.StatusUnprocessableEntity, gin.H{"detail": "bad n"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"n": q.N})
}

// ListNotes answers every note, whoever wrote it (unscoped, on purpose).
func ListNotes(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var notes []Note
		db.Find(&notes)
		c.JSON(http.StatusOK, notes)
	}
}

// ListGuardedNotes answers every note too, behind a guard the checker cannot read.
func ListGuardedNotes(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var notes []Note
		db.Find(&notes)
		c.JSON(http.StatusOK, notes)
	}
}
