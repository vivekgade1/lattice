package api

import (
	"errors"
	"net/http"
	"strconv"
	"strings"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"

	"example.com/shelfie/internal/models"
)

type noteInput struct {
	Body string `json:"body" binding:"required,max=2000"`
}

func (s *Server) listNotes(c *gin.Context) {
	id, err := strconv.ParseUint(c.Param("id"), 10, 64)
	if err != nil {
		fail(c, http.StatusBadRequest, "book id must be a positive integer")
		return
	}
	book, owner, err := s.bookWithOwner(id)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		fail(c, http.StatusNotFound, "book not found")
		return
	} else if err != nil {
		fail(c, http.StatusInternalServerError, "could not load the book")
		return
	}
	if owner != userID(c) {
		fail(c, http.StatusForbidden, "book belongs to another user")
		return
	}
	notes := []models.Note{}
	if err := s.db.Where("book_id = ? AND user_id = ?", book.ID, owner).Order("id").Find(&notes).Error; err != nil {
		fail(c, http.StatusInternalServerError, "could not load the notes")
		return
	}
	c.JSON(http.StatusOK, gin.H{"notes": notes})
}

func (s *Server) createNote(c *gin.Context) {
	id, err := strconv.ParseUint(c.Param("id"), 10, 64)
	if err != nil {
		fail(c, http.StatusBadRequest, "book id must be a positive integer")
		return
	}
	book, owner, err := s.bookWithOwner(id)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		fail(c, http.StatusNotFound, "book not found")
		return
	} else if err != nil {
		fail(c, http.StatusInternalServerError, "could not load the book")
		return
	}
	if owner != userID(c) {
		fail(c, http.StatusForbidden, "book belongs to another user")
		return
	}
	var in noteInput
	if err := c.ShouldBindJSON(&in); err != nil || strings.TrimSpace(in.Body) == "" {
		fail(c, http.StatusUnprocessableEntity, "body is required (1 to 2000 characters)")
		return
	}
	note := models.Note{UserID: owner, BookID: book.ID, Body: strings.TrimSpace(in.Body)}
	if err := s.db.Create(&note).Error; err != nil {
		fail(c, http.StatusInternalServerError, "could not create the note")
		return
	}
	c.JSON(http.StatusCreated, note)
}

func (s *Server) deleteNote(c *gin.Context) {
	id, err := strconv.ParseUint(c.Param("id"), 10, 64)
	if err != nil {
		fail(c, http.StatusBadRequest, "note id must be a positive integer")
		return
	}
	var note models.Note
	if err := s.db.First(&note, id).Error; errors.Is(err, gorm.ErrRecordNotFound) {
		fail(c, http.StatusNotFound, "note not found")
		return
	} else if err != nil {
		fail(c, http.StatusInternalServerError, "could not load the note")
		return
	}
	if note.UserID != userID(c) {
		fail(c, http.StatusForbidden, "note belongs to another user")
		return
	}
	if err := s.db.Delete(&note).Error; err != nil {
		fail(c, http.StatusInternalServerError, "could not delete the note")
		return
	}
	c.JSON(http.StatusOK, gin.H{"deleted": note.ID})
}
