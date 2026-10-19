// q5gin: a Gin app over database/sql. The gRPC walk's SQL reader is never called from here.
package main

import (
	"database/sql"
	"net/http"
	"os"

	"github.com/gin-gonic/gin"
)

var db *sql.DB

func main() {
	db, _ = sql.Open("sqlite", os.Getenv("DB_PATH"))
	r := gin.New()
	r.GET("/widgets/:id", func(c *gin.Context) {
		var name string
		if err := db.QueryRow("SELECT name FROM widgets WHERE id = ?", c.Param("id")).Scan(&name); err != nil {
			c.JSON(http.StatusNotFound, gin.H{"error": "not found"})
			return
		}
		c.JSON(http.StatusOK, gin.H{"name": name})
	})
	r.POST("/widgets", func(c *gin.Context) {
		if _, err := db.Exec("INSERT INTO widgets (id, name) VALUES (?, ?)", c.PostForm("id"), c.PostForm("name")); err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "failed"})
			return
		}
		c.Status(http.StatusCreated)
	})
	_ = r.Run()
}
