// Package api wires the HTTP routes to their handlers.
package api

import (
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"

	"example.com/shelfie/internal/auth"
)

// Server carries what every handler needs.
type Server struct {
	db     *gorm.DB
	signer *auth.Signer
}

// NewRouter builds the engine with every route of the service.
func NewRouter(db *gorm.DB, signer *auth.Signer) *gin.Engine {
	gin.SetMode(gin.ReleaseMode)
	s := &Server{db: db, signer: signer}

	r := gin.New()
	r.Use(gin.Recovery())

	r.GET("/health", s.health)
	r.POST("/login", s.login)

	member := r.Group("", s.requireUser)
	member.GET("/me", s.me)
	member.GET("/shelves", s.listShelves)
	member.POST("/shelves", s.createShelf)
	member.GET("/shelves/:id", s.getShelf)
	member.DELETE("/shelves/:id", s.deleteShelf)
	member.POST("/shelves/:id/books", s.createBook)
	member.GET("/books/:id", s.getBook)
	member.PATCH("/books/:id", s.updateBook)
	member.POST("/books/:id/start", s.startBook)
	member.POST("/books/:id/finish", s.finishBook)
	member.GET("/books/:id/notes", s.listNotes)
	member.POST("/books/:id/notes", s.createNote)
	member.DELETE("/notes/:id", s.deleteNote)

	return r
}
