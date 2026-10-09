// Package routes: closure-style handlers over the package-level DB.
package routes

import (
	"net/http"
	"strconv"

	"example.com/clean2/db"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

type ticketIn struct {
	Title string `json:"title" binding:"required,max=200"`
}

type moveIn struct {
	To string `json:"to" binding:"required,oneof=open assigned resolved closed"`
}

type noteIn struct {
	Text string `json:"text" binding:"required,max=5000"`
}

func abortWith(c *gin.Context, code int, msg string) {
	c.AbortWithStatusJSON(code, gin.H{"detail": msg})
}

func uid(c *gin.Context) uint {
	v, _ := c.Get("uid")
	id, _ := v.(uint)
	return id
}

func Mount(r *gin.Engine) {
	g := r.Group("/api")
	g.Use(Auth())
	g.POST("/tickets", CreateTicket())
	g.GET("/tickets", ListTickets())
	g.POST("/tickets/:id/move", MoveTicket())
	g.POST("/tickets/:id/notes", AddNote())
	g.DELETE("/tickets/:id", DeleteTicket())
}

func Auth() gin.HandlerFunc {
	return func(c *gin.Context) {
		n, err := strconv.Atoi(c.GetHeader("X-User"))
		if err != nil {
			abortWith(c, http.StatusUnauthorized, "Not authenticated")
			return
		}
		c.Set("uid", uint(n))
		c.Next()
	}
}

func CreateTicket() gin.HandlerFunc {
	return func(c *gin.Context) {
		var in ticketIn
		if err := c.ShouldBindJSON(&in); err != nil {
			abortWith(c, http.StatusUnprocessableEntity, err.Error())
			return
		}
		t := db.Ticket{ReporterID: uid(c), Title: in.Title, State: db.Open}
		if res := db.DB.Create(&t); res.Error != nil {
			abortWith(c, http.StatusInternalServerError, "db")
			return
		}
		c.IndentedJSON(http.StatusCreated, t)
	}
}

func ListTickets() gin.HandlerFunc {
	return func(c *gin.Context) {
		size := 50
		if s := c.Query("size"); s != "" {
			n, err := strconv.Atoi(s)
			if err != nil || n <= 0 || n > 200 {
				abortWith(c, http.StatusUnprocessableEntity, "size")
				return
			}
			size = n
		}
		var ts []db.Ticket
		db.DB.Where("reporter_id = ?", uid(c)).Limit(size).Find(&ts)
		c.JSON(http.StatusOK, ts)
	}
}

func MoveTicket() gin.HandlerFunc {
	return func(c *gin.Context) {
		var in moveIn
		if err := c.ShouldBindJSON(&in); err != nil {
			abortWith(c, http.StatusUnprocessableEntity, err.Error())
			return
		}
		err := db.DB.Transaction(func(tx *gorm.DB) error {
			var t db.Ticket
			if err := tx.Where("id = ? AND reporter_id = ?", c.Param("id"), uid(c)).First(&t).Error; err != nil {
				return err
			}
			if !db.Allowed[t.State][in.To] {
				return errBadMove
			}
			return tx.Model(&t).Update("state", in.To).Error
		})
		switch {
		case err == errBadMove:
			abortWith(c, http.StatusConflict, "illegal move")
		case err == gorm.ErrRecordNotFound:
			abortWith(c, http.StatusNotFound, "Not Found")
		case err != nil:
			abortWith(c, http.StatusInternalServerError, "db")
		default:
			c.Status(http.StatusNoContent)
		}
	}
}

func AddNote() gin.HandlerFunc {
	return func(c *gin.Context) {
		var in noteIn
		if err := c.ShouldBindJSON(&in); err != nil {
			abortWith(c, http.StatusUnprocessableEntity, err.Error())
			return
		}
		var t db.Ticket
		if err := db.DB.Where("id = ? AND reporter_id = ?", c.Param("id"), uid(c)).First(&t).Error; err != nil {
			abortWith(c, http.StatusNotFound, "Not Found")
			return
		}
		if t.State == db.Closed {
			abortWith(c, http.StatusConflict, "closed")
			return
		}
		n := db.Note{TicketID: t.ID, Text: in.Text}
		db.DB.Create(&n)
		c.JSON(http.StatusCreated, n)
	}
}

func DeleteTicket() gin.HandlerFunc {
	return func(c *gin.Context) {
		res := db.DB.Where("id = ? AND reporter_id = ? AND state = ?", c.Param("id"), uid(c), db.Closed).
			Delete(&db.Ticket{})
		if res.Error != nil {
			abortWith(c, http.StatusInternalServerError, "db")
			return
		}
		if res.RowsAffected == 0 {
			abortWith(c, http.StatusNotFound, "Not Found")
			return
		}
		c.Status(http.StatusNoContent)
	}
}
