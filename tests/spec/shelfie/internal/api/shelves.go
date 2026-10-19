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

type shelfInput struct {
	Name        string `json:"name" binding:"required,max=80"`
	Description string `json:"description" binding:"max=500"`
}

func (s *Server) listShelves(c *gin.Context) {
	shelves := []models.Shelf{}
	if err := s.db.Where("user_id = ?", userID(c)).Order("id").Find(&shelves).Error; err != nil {
		fail(c, http.StatusInternalServerError, "could not load the shelves")
		return
	}
	c.JSON(http.StatusOK, gin.H{"shelves": shelves})
}

func (s *Server) createShelf(c *gin.Context) {
	var in shelfInput
	if err := c.ShouldBindJSON(&in); err != nil || strings.TrimSpace(in.Name) == "" {
		fail(c, http.StatusUnprocessableEntity, "name is required (1 to 80 characters) and description at most 500")
		return
	}
	shelf := models.Shelf{UserID: userID(c), Name: strings.TrimSpace(in.Name), Description: strings.TrimSpace(in.Description)}
	if err := s.db.Create(&shelf).Error; err != nil {
		fail(c, http.StatusInternalServerError, "could not create the shelf")
		return
	}
	c.JSON(http.StatusCreated, shelf)
}

func (s *Server) getShelf(c *gin.Context) {
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
	books := []models.Book{}
	if err := s.db.Where("shelf_id = ?", shelf.ID).Order("id").Find(&books).Error; err != nil {
		fail(c, http.StatusInternalServerError, "could not load the books of the shelf")
		return
	}
	c.JSON(http.StatusOK, gin.H{"shelf": shelf, "books": books})
}

func (s *Server) deleteShelf(c *gin.Context) {
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
	var books int64
	if err := s.db.Model(&models.Book{}).Where("shelf_id = ?", shelf.ID).Count(&books).Error; err != nil {
		fail(c, http.StatusInternalServerError, "could not count the books of the shelf")
		return
	}
	if books > 0 {
		fail(c, http.StatusConflict, "shelf still has books")
		return
	}
	if err := s.db.Delete(&shelf).Error; err != nil {
		fail(c, http.StatusInternalServerError, "could not delete the shelf")
		return
	}
	c.JSON(http.StatusOK, gin.H{"deleted": shelf.ID})
}
