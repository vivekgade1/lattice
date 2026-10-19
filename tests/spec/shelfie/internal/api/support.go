package api

import (
	"strings"

	"github.com/gin-gonic/gin"

	"example.com/shelfie/internal/models"
)

const userKey = "shelfie.user_id"

// fail answers with a JSON error body {"detail": ...} and stops the handler chain.
func fail(c *gin.Context, status int, detail string) {
	c.AbortWithStatusJSON(status, gin.H{"detail": detail})
}

// userID is the id of the logged-in user; only valid behind requireUser.
func userID(c *gin.Context) uint {
	return c.GetUint(userKey)
}

// bearerToken extracts the token of an "Authorization: Bearer <token>" header.
func bearerToken(header string) (string, bool) {
	scheme, token, found := strings.Cut(header, " ")
	if !found || !strings.EqualFold(scheme, "Bearer") || strings.TrimSpace(token) == "" {
		return "", false
	}
	return strings.TrimSpace(token), true
}

// bookWithOwner loads a book and the id of the user who owns it through its shelf.
func (s *Server) bookWithOwner(id uint64) (models.Book, uint, error) {
	var book models.Book
	if err := s.db.First(&book, id).Error; err != nil {
		return book, 0, err
	}
	var shelf models.Shelf
	if err := s.db.First(&shelf, book.ShelfID).Error; err != nil {
		return book, 0, err
	}
	return book, shelf.UserID, nil
}
