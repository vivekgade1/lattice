package api

import (
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"

	"example.com/shelfie/internal/models"
)

type bookInput struct {
	Title  string `json:"title" binding:"required,max=120"`
	Author string `json:"author" binding:"required,max=120"`
}

type bookPatch struct {
	Title  *string `json:"title"`
	Author *string `json:"author"`
}

type finishInput struct {
	Rating *int `json:"rating"`
}

func (s *Server) createBook(c *gin.Context) {
	id, err := strconv.ParseUint(c.Param("id"), 10, 64)
	if err != nil {
		fail(c, http.StatusBadRequest, "shelf id must be a positive integer")
		return
	}
	var shelf models.Shelf
	if err := s.db.First(&shelf, id).Error; errors.Is(err, gorm.ErrRecordNotFound) {
		fail(c, http.StatusNotFound, "shelf not found")
		return
	} else if err != nil {
		fail(c, http.StatusInternalServerError, "could not load the shelf")
		return
	}
	if shelf.UserID != userID(c) {
		fail(c, http.StatusForbidden, "shelf belongs to another user")
		return
	}
	var in bookInput
	if err := c.ShouldBindJSON(&in); err != nil || strings.TrimSpace(in.Title) == "" || strings.TrimSpace(in.Author) == "" {
		fail(c, http.StatusUnprocessableEntity, "title and author are required (1 to 120 characters each)")
		return
	}
	book := models.Book{ShelfID: shelf.ID, Title: strings.TrimSpace(in.Title), Author: strings.TrimSpace(in.Author), Status: "queued"}
	if err := s.db.Create(&book).Error; err != nil {
		fail(c, http.StatusInternalServerError, "could not create the book")
		return
	}
	c.JSON(http.StatusCreated, book)
}

func (s *Server) getBook(c *gin.Context) {
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
	c.JSON(http.StatusOK, book)
}

func (s *Server) updateBook(c *gin.Context) {
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
	var in bookPatch
	if err := c.ShouldBindJSON(&in); err != nil || !validPatch(in) {
		fail(c, http.StatusUnprocessableEntity, "send a title and/or an author of 1 to 120 characters")
		return
	}
	changes := map[string]any{}
	if in.Title != nil {
		book.Title = strings.TrimSpace(*in.Title)
		changes["title"] = book.Title
	}
	if in.Author != nil {
		book.Author = strings.TrimSpace(*in.Author)
		changes["author"] = book.Author
	}
	if err := s.db.Model(&models.Book{}).Where("id = ?", book.ID).Updates(changes).Error; err != nil {
		fail(c, http.StatusInternalServerError, "could not update the book")
		return
	}
	c.JSON(http.StatusOK, book)
}

// validPatch holds when at least one field is sent and every field sent is 1 to 120 characters once trimmed.
func validPatch(in bookPatch) bool {
	if in.Title == nil && in.Author == nil {
		return false
	}
	for _, v := range []*string{in.Title, in.Author} {
		if v == nil {
			continue
		}
		if n := len([]rune(strings.TrimSpace(*v))); n < 1 || n > 120 {
			return false
		}
	}
	return true
}

func (s *Server) startBook(c *gin.Context) {
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
	res := s.db.Model(&models.Book{}).Where("id = ? AND status = ?", book.ID, "queued").Update("status", "reading")
	if res.Error != nil {
		fail(c, http.StatusInternalServerError, "could not start the book")
		return
	}
	if res.RowsAffected == 0 {
		fail(c, http.StatusConflict, "only a queued book can be started")
		return
	}
	book.Status = "reading"
	c.JSON(http.StatusOK, book)
}

func (s *Server) finishBook(c *gin.Context) {
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
	var in finishInput
	if err := c.ShouldBindJSON(&in); (err != nil && !errors.Is(err, io.EOF)) || (in.Rating != nil && (*in.Rating < 1 || *in.Rating > 5)) {
		fail(c, http.StatusUnprocessableEntity, "rating, when sent, must be a whole number from 1 to 5")
		return
	}
	now := time.Now().UTC()
	res := s.db.Model(&models.Book{}).Where("id = ? AND status = ?", book.ID, "reading").Updates(map[string]any{"status": "finished", "rating": in.Rating, "finished_at": now})
	if res.Error != nil {
		fail(c, http.StatusInternalServerError, "could not finish the book")
		return
	}
	if res.RowsAffected == 0 {
		fail(c, http.StatusConflict, "only a book being read can be finished")
		return
	}
	book.Status = "finished"
	book.Rating = in.Rating
	book.FinishedAt = &now
	c.JSON(http.StatusOK, book)
}
