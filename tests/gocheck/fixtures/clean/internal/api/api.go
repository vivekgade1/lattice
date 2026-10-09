// Package api: the HTTP layer.
package api

import (
	"errors"
	"net/http"
	"strconv"

	"example.com/clean/internal/models"
	"example.com/clean/internal/service"
	"example.com/clean/internal/store"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

type Server struct {
	db  *gorm.DB
	svc *service.Service
	st  *store.Store
}

type pipelineIn struct {
	Name string `json:"name" binding:"required,min=1,max=100"`
}

type pipelineOut struct {
	ID   uint   `json:"id"`
	Name string `json:"name"`
}

type accountOut struct {
	ID    uint   `json:"id"`
	Email string `json:"email"`
}

func New(db *gorm.DB) *Server {
	return &Server{db: db, svc: service.New(db), st: store.New(db)}
}

func (s *Server) Routes(r *gin.Engine) {
	r.GET("/healthz", func(c *gin.Context) { c.JSON(http.StatusOK, gin.H{"ok": true}) })
	v1 := r.Group("/v1")
	v1.Use(s.authenticate)
	v1.GET("/me", s.me)
	v1.GET("/pipelines", s.listPipelines)
	v1.POST("/pipelines", s.createPipeline)
	v1.GET("/pipelines/:pid", s.getPipeline)
	v1.POST("/pipelines/:pid/runs", s.startRun)
	v1.POST("/pipelines/:pid/runs/:rid/cancel", s.cancelRun)
	v1.POST("/pipelines/:pid/runs/:rid/retry", s.retryRun)
}

func (s *Server) authenticate(c *gin.Context) {
	var acc models.Account
	if err := s.db.Where("email = ?", c.GetHeader("X-Email")).First(&acc).Error; err != nil {
		c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"detail": "Not authenticated"})
		return
	}
	c.Set("account", &acc)
	c.Next()
}

func current(c *gin.Context) *models.Account { return c.MustGet("account").(*models.Account) }

func fail(c *gin.Context, err error) {
	switch {
	case errors.Is(err, store.ErrNotFound):
		c.JSON(http.StatusNotFound, gin.H{"detail": "Not Found"})
	case errors.Is(err, store.ErrConflict):
		c.JSON(http.StatusConflict, gin.H{"detail": "conflict"})
	default:
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "internal"})
	}
}

func idParam(c *gin.Context, name string) (uint, bool) {
	n, err := strconv.ParseUint(c.Param(name), 10, 64)
	if err != nil {
		c.JSON(http.StatusUnprocessableEntity, gin.H{"detail": name + " must be an integer"})
		return 0, false
	}
	return uint(n), true
}

func (s *Server) me(c *gin.Context) {
	acc := current(c)
	c.JSON(http.StatusOK, accountOut{ID: acc.ID, Email: acc.Email})
}

func (s *Server) listPipelines(c *gin.Context) {
	limit, err := strconv.Atoi(c.DefaultQuery("limit", "20"))
	if err != nil || limit < 1 || limit > 100 {
		c.JSON(http.StatusUnprocessableEntity, gin.H{"detail": "limit must be 1..100"})
		return
	}
	offset, _ := strconv.Atoi(c.DefaultQuery("offset", "0"))
	ps, err := s.st.Pipelines(current(c).ID, limit, offset)
	if err != nil {
		fail(c, err)
		return
	}
	out := make([]pipelineOut, 0, len(ps))
	for _, p := range ps {
		out = append(out, pipelineOut{ID: p.ID, Name: p.Name})
	}
	c.JSON(http.StatusOK, out)
}

func (s *Server) createPipeline(c *gin.Context) {
	var in pipelineIn
	if err := c.ShouldBindJSON(&in); err != nil {
		c.JSON(http.StatusUnprocessableEntity, gin.H{"detail": err.Error()})
		return
	}
	p := models.Pipeline{AccountID: current(c).ID, Name: in.Name}
	if err := s.st.CreatePipeline(&p); err != nil {
		fail(c, err)
		return
	}
	c.JSON(http.StatusCreated, pipelineOut{ID: p.ID, Name: p.Name})
}

func (s *Server) getPipeline(c *gin.Context) {
	pid, ok := idParam(c, "pid")
	if !ok {
		return
	}
	p, err := s.st.Pipeline(current(c).ID, pid)
	if err != nil {
		fail(c, err)
		return
	}
	c.JSON(http.StatusOK, pipelineOut{ID: p.ID, Name: p.Name})
}

func (s *Server) startRun(c *gin.Context) {
	pid, ok := idParam(c, "pid")
	if !ok {
		return
	}
	run, err := s.svc.StartRun(current(c).ID, pid)
	if err != nil {
		fail(c, err)
		return
	}
	c.JSON(http.StatusCreated, run)
}

func (s *Server) cancelRun(c *gin.Context) {
	pid, ok := idParam(c, "pid")
	if !ok {
		return
	}
	rid, ok := idParam(c, "rid")
	if !ok {
		return
	}
	if err := s.svc.Cancel(current(c).ID, pid, rid); err != nil {
		fail(c, err)
		return
	}
	c.Status(http.StatusNoContent)
}

func (s *Server) retryRun(c *gin.Context) {
	pid, ok := idParam(c, "pid")
	if !ok {
		return
	}
	rid, ok := idParam(c, "rid")
	if !ok {
		return
	}
	run, err := s.svc.Retry(current(c).ID, pid, rid)
	if err != nil {
		fail(c, err)
		return
	}
	c.JSON(http.StatusCreated, run)
}
