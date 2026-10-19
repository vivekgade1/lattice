package api

import (
	"crypto/subtle"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"

	"example.com/shelfie/internal/models"
)

type loginInput struct {
	Username string `json:"username" binding:"required"`
	Password string `json:"password" binding:"required"`
}

func (s *Server) health(c *gin.Context) {
	sqlDB, err := s.db.DB()
	if err != nil || sqlDB.PingContext(c.Request.Context()) != nil {
		fail(c, http.StatusServiceUnavailable, "database unavailable")
		return
	}
	c.JSON(http.StatusOK, gin.H{"status": "ok"})
}

func (s *Server) login(c *gin.Context) {
	var in loginInput
	if err := c.ShouldBindJSON(&in); err != nil {
		fail(c, http.StatusUnprocessableEntity, "username and password are required")
		return
	}
	var users []models.User
	if err := s.db.Where("username = ?", in.Username).Limit(1).Find(&users).Error; err != nil {
		fail(c, http.StatusInternalServerError, "could not look up the user")
		return
	}
	if len(users) == 0 || subtle.ConstantTimeCompare([]byte(users[0].Password), []byte(in.Password)) != 1 {
		fail(c, http.StatusUnauthorized, "invalid username or password")
		return
	}
	c.JSON(http.StatusOK, gin.H{
		"access_token": s.signer.Issue(users[0].ID, time.Now()),
		"token_type":   "bearer",
		"user":         users[0],
	})
}

func (s *Server) me(c *gin.Context) {
	var user models.User
	if err := s.db.First(&user, userID(c)).Error; err != nil {
		fail(c, http.StatusInternalServerError, "could not load the user")
		return
	}
	c.JSON(http.StatusOK, user)
}
