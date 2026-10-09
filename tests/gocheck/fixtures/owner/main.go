package main

import (
	"net/http"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

type User struct {
	ID   uint
	Name string
}

type Job struct {
	ID     uint   `json:"id"`
	UserID uint   `json:"user_id"`
	Name   string `json:"name"`
}

type JobEvent struct {
	ID    uint   `json:"id"`
	JobID uint   `json:"job_id"`
	Kind  string `json:"kind"`
}

type Config struct {
	ID   uint
	Name string
}

type Authorizer interface {
	Check(c *gin.Context, job *Job) bool
}

type allowAll struct{}

func (allowAll) Check(c *gin.Context, job *Job) bool { return true }

type ownerOnly struct{}

func (ownerOnly) Check(c *gin.Context, job *Job) bool { return job.UserID == c.GetUint("user_id") }

type H struct {
	db    *gorm.DB
	authz Authorizer
}

func auth() gin.HandlerFunc {
	return func(c *gin.Context) {
		c.Set("user_id", uint(1))
		c.Next()
	}
}

func setup(db *gorm.DB) *gin.Engine {
	r := gin.New()
	h := &H{db: db, authz: ownerOnly{}}
	api := r.Group("/api", auth())
	api.GET("/jobs/:id", h.BadGetJob)
	api.GET("/jobs", h.BadListJobs)
	api.DELETE("/jobs/:id", h.BadDeleteJob)
	api.GET("/jobs/:id/events", h.BadListEvents)
	api.GET("/jobs/:id/helper", h.BadHelperLookup)
	api.GET("/mine/:id", h.OkScoped)
	api.GET("/mine/:id/compare", h.OkCompare)
	api.POST("/mine", h.OkCreate)
	api.GET("/mine/:id/events", h.OkEventsViaParent)
	api.GET("/admin/jobs", h.OkAdminList)
	api.GET("/configs", h.OkShared)
	api.GET("/mine/:id/helper", h.OkHelper)
	api.PATCH("/mine/:id", h.loadOwnedJob, h.OkRenameWithMiddleware)
	api.GET("/mine/:id/authz", h.OkOpaqueCheck)
	api.GET("/public/jobs", h.OkOpenRoute)
	return r
}

func (h *H) BadGetJob(c *gin.Context) {
	var job Job
	if err := h.db.First(&job, c.Param("id")).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"detail": "not found"})
		return
	}
	c.JSON(http.StatusOK, job)
}

func (h *H) BadListJobs(c *gin.Context) {
	var jobs []Job
	h.db.Find(&jobs)
	c.JSON(http.StatusOK, jobs)
}

func (h *H) BadDeleteJob(c *gin.Context) {
	h.db.Delete(&Job{}, c.Param("id"))
	c.Status(http.StatusNoContent)
}

func (h *H) BadListEvents(c *gin.Context) {
	var events []JobEvent
	h.db.Where("job_id = ?", c.Param("id")).Find(&events)
	c.JSON(http.StatusOK, events)
}

func (h *H) findJob(id string) (*Job, error) {
	var job Job
	err := h.db.First(&job, id).Error
	return &job, err
}

func (h *H) BadHelperLookup(c *gin.Context) {
	job, err := h.findJob(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusNotFound, gin.H{})
		return
	}
	c.JSON(http.StatusOK, job)
}

func (h *H) OkScoped(c *gin.Context) {
	var job Job
	uid := c.GetUint("user_id")
	if err := h.db.Where("id = ? AND user_id = ?", c.Param("id"), uid).First(&job).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{})
		return
	}
	c.JSON(http.StatusOK, job)
}

func (h *H) OkCompare(c *gin.Context) {
	var job Job
	h.db.First(&job, c.Param("id"))
	if job.UserID != c.GetUint("user_id") {
		c.JSON(http.StatusNotFound, gin.H{})
		return
	}
	c.JSON(http.StatusOK, job)
}

func (h *H) OkCreate(c *gin.Context) {
	var job Job
	_ = c.ShouldBindJSON(&job)
	job.UserID = c.GetUint("user_id")
	h.db.Create(&job)
	c.JSON(http.StatusCreated, job)
}

func (h *H) OkEventsViaParent(c *gin.Context) {
	var job Job
	if err := h.db.Where("id = ? AND user_id = ?", c.Param("id"), c.GetUint("user_id")).First(&job).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{})
		return
	}
	var events []JobEvent
	h.db.Where("job_id = ?", job.ID).Find(&events)
	c.JSON(http.StatusOK, events)
}

func (h *H) OkAdminList(c *gin.Context) {
	var jobs []Job
	h.db.Find(&jobs)
	c.JSON(http.StatusOK, jobs)
}

func (h *H) OkShared(c *gin.Context) {
	var cfgs []Config
	h.db.Find(&cfgs)
	c.JSON(http.StatusOK, cfgs)
}

func (h *H) ownedJob(c *gin.Context, id string) (*Job, bool) {
	var job Job
	if err := h.db.First(&job, "id = ? AND user_id = ?", id, c.GetUint("user_id")).Error; err != nil {
		return nil, false
	}
	return &job, true
}

func (h *H) OkHelper(c *gin.Context) {
	job, ok := h.ownedJob(c, c.Param("id"))
	if !ok {
		c.JSON(http.StatusNotFound, gin.H{})
		return
	}
	c.JSON(http.StatusOK, job)
}

func (h *H) loadOwnedJob(c *gin.Context) {
	var job Job
	if err := h.db.First(&job, c.Param("id")).Error; err != nil || job.UserID != c.GetUint("user_id") {
		c.AbortWithStatus(http.StatusNotFound)
		return
	}
	c.Set("job", &job)
	c.Next()
}

func (h *H) OkRenameWithMiddleware(c *gin.Context) {
	job := c.MustGet("job").(*Job)
	h.db.Model(job).Update("name", c.Query("name"))
	c.JSON(http.StatusOK, job)
}

func (h *H) OkOpaqueCheck(c *gin.Context) {
	var job Job
	h.db.First(&job, c.Param("id"))
	if !h.authz.Check(c, &job) {
		c.JSON(http.StatusNotFound, gin.H{})
		return
	}
	c.JSON(http.StatusOK, job)
}

func (h *H) OkOpenRoute(c *gin.Context) {
	var jobs []Job
	h.db.Find(&jobs)
	c.JSON(http.StatusOK, jobs)
}

func main() {}
