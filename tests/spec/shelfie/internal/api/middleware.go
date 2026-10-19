package api

import (
	"net/http"
	"time"

	"github.com/gin-gonic/gin"

	"example.com/shelfie/internal/models"
)

// requireUser lets a request through only when it carries a valid bearer token of an existing user.
func (s *Server) requireUser(c *gin.Context) {
	uid, ok := s.authenticate(c)
	if !ok {
		fail(c, http.StatusUnauthorized, "authentication required")
		return
	}
	c.Set(userKey, uid)
	c.Next()
}

func (s *Server) authenticate(c *gin.Context) (uint, bool) {
	token, ok := bearerToken(c.GetHeader("Authorization"))
	if !ok {
		return 0, false
	}
	uid, err := s.signer.Verify(token, time.Now())
	if err != nil {
		return 0, false
	}
	var n int64
	if err := s.db.Model(&models.User{}).Where("id = ?", uid).Count(&n).Error; err != nil || n == 0 {
		return 0, false
	}
	return uid, true
}
