package main

import (
	"net/http"
	"strconv"
	"strings"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

type Job struct {
	ID       uint   `json:"id"`
	Name     string `json:"name"`
	Priority int    `json:"priority"`
	Kind     string `json:"kind"`
}

type CreateJob struct {
	Name     string `json:"name" binding:"required"`
	Priority int    `json:"priority"`
	Kind     string `json:"kind" binding:"required,oneof=batch stream"`
}

type CreateJobBounded struct {
	Name     string `json:"name" binding:"required,max=200"`
	Priority int    `json:"priority" binding:"min=0,max=10"`
}

type Rename struct {
	Name string `json:"name"`
}

func (r Rename) Validate() error { return nil }

type ListQuery struct {
	Limit int `form:"limit"`
}

type ListQueryBounded struct {
	Limit int `form:"limit" binding:"max=100"`
}

type H struct{ db *gorm.DB }

func setup(r *gin.Engine, h *H) {
	r.POST("/bad/jobs", h.BadCreate)
	r.POST("/bad/jobs/whole", h.BadWhole)
	r.PATCH("/bad/jobs/:id", h.BadUpdate)
	r.PATCH("/bad/jobs/:id/query", h.BadQueryWrite)
	r.GET("/bad/jobs", h.BadLimit)
	r.GET("/bad/jobs/struct", h.BadLimitStruct)
	r.POST("/ok/jobs", h.OkBounded)
	r.POST("/ok/jobs/hand", h.OkHandChecked)
	r.PATCH("/ok/jobs/:id", h.OkValidated)
	r.GET("/ok/jobs", h.OkClamped)
	r.GET("/ok/jobs/struct", h.OkLimitStruct)
	r.GET("/ok/jobs/min", h.OkMin)
}

func (h *H) BadCreate(c *gin.Context) {
	var req CreateJob
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"detail": err.Error()})
		return
	}
	job := Job{Name: strings.TrimSpace(req.Name), Priority: req.Priority, Kind: req.Kind}
	h.db.Create(&job)
	c.JSON(http.StatusCreated, job)
}

func (h *H) BadWhole(c *gin.Context) {
	var job Job
	if err := c.ShouldBindJSON(&job); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{})
		return
	}
	job.Kind = "batch"
	h.db.Create(&job)
	c.JSON(http.StatusCreated, job)
}

func (h *H) BadUpdate(c *gin.Context) {
	var req CreateJob
	_ = c.ShouldBindJSON(&req)
	var job Job
	h.db.First(&job, c.Param("id"))
	job.Name = req.Name
	h.db.Save(&job)
	c.JSON(http.StatusOK, job)
}

func (h *H) BadQueryWrite(c *gin.Context) {
	name := c.Query("name")
	h.db.Model(&Job{}).Where("id = ?", c.Param("id")).Update("name", name)
	c.Status(http.StatusNoContent)
}

func (h *H) BadLimit(c *gin.Context) {
	limit, _ := strconv.Atoi(c.DefaultQuery("limit", "20"))
	var jobs []Job
	h.db.Limit(limit).Find(&jobs)
	c.JSON(http.StatusOK, jobs)
}

func (h *H) BadLimitStruct(c *gin.Context) {
	var q ListQuery
	_ = c.ShouldBindQuery(&q)
	var jobs []Job
	h.db.Limit(q.Limit).Find(&jobs)
	c.JSON(http.StatusOK, jobs)
}

func (h *H) OkBounded(c *gin.Context) {
	var req CreateJobBounded
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusUnprocessableEntity, gin.H{"detail": err.Error()})
		return
	}
	job := Job{Name: req.Name, Priority: req.Priority}
	h.db.Create(&job)
	c.JSON(http.StatusCreated, job)
}

func (h *H) OkHandChecked(c *gin.Context) {
	var req CreateJob
	_ = c.ShouldBindJSON(&req)
	if len(req.Name) > 200 || req.Priority < 0 || req.Priority > 10 {
		c.JSON(http.StatusUnprocessableEntity, gin.H{})
		return
	}
	h.db.Create(&Job{Name: req.Name, Priority: req.Priority, Kind: req.Kind})
	c.Status(http.StatusCreated)
}

func (h *H) OkValidated(c *gin.Context) {
	var req Rename
	_ = c.ShouldBindJSON(&req)
	if err := req.Validate(); err != nil {
		c.JSON(http.StatusUnprocessableEntity, gin.H{})
		return
	}
	h.db.Model(&Job{}).Where("id = ?", c.Param("id")).Update("name", req.Name)
}

func (h *H) OkClamped(c *gin.Context) {
	limit, err := strconv.Atoi(c.Query("limit"))
	if err != nil || limit <= 0 || limit > 100 {
		limit = 20
	}
	var jobs []Job
	h.db.Limit(limit).Find(&jobs)
	c.JSON(http.StatusOK, jobs)
}

func (h *H) OkLimitStruct(c *gin.Context) {
	var q ListQueryBounded
	_ = c.ShouldBindQuery(&q)
	var jobs []Job
	h.db.Limit(q.Limit).Find(&jobs)
	c.JSON(http.StatusOK, jobs)
}

func (h *H) OkMin(c *gin.Context) {
	n, _ := strconv.Atoi(c.Query("limit"))
	n = min(n, 50)
	var jobs []Job
	h.db.Limit(n).Find(&jobs)
	c.JSON(http.StatusOK, jobs)
}

func main() {}
