package api

import (
	"errors"
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"

	"example.com/wf/models"
)

// Mount registers every route of the app.
func Mount(r *gin.Engine, db *gorm.DB) {
	r.POST("/api/login", Login(db))
	auth := r.Group("/api", RequireUser(db))
	auth.POST("/projects", CreateProject(db))
	auth.GET("/projects/:id", GetProject(db))
	auth.POST("/projects/:id/publish", Publish(db))
	auth.POST("/projects/:id/archive", Archive(db))
	auth.POST("/tickets", CreateTicket(db))
	auth.GET("/tickets", ListTickets(db))
	auth.GET("/tickets/:id", GetTicket(db))
	auth.DELETE("/tickets/:id", DeleteTicket(db))
	auth.POST("/batch", Batch(db))
	auth.POST("/jobs", CreateJob(db))
	auth.POST("/jobs/:id/activate", Activate(db))
	auth.POST("/jobs/:id/runs", StartRun(db))
}

type loginReq struct {
	Email string `json:"email" binding:"required"`
}

func Login(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req loginReq
		if err := c.ShouldBindJSON(&req); err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"code": "bad_request"})
			return
		}
		var u models.User
		if err := db.Where("email = ?", req.Email).First(&u).Error; err != nil {
			c.JSON(http.StatusUnauthorized, gin.H{"code": "bad_credentials"})
			return
		}
		c.JSON(http.StatusOK, gin.H{"token": "x"})
	}
}

// RequireUser authenticates: a 401 refusal, and the caller recorded.
func RequireUser(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		h := c.GetHeader("Authorization")
		if len(h) < 8 || h[:7] != "Bearer " {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"code": "unauthorized"})
			return
		}
		var u models.User
		if err := db.Where("id = ?", 1).Take(&u).Error; err != nil {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"code": "unauthorized"})
			return
		}
		c.Set("caller", &u)
		c.Next()
	}
}

func caller(c *gin.Context) *models.User {
	return c.MustGet("caller").(*models.User)
}

type projectReq struct {
	ID   string `json:"id" binding:"required"`
	Name string `json:"name" binding:"required"`
	Kind string `json:"kind" binding:"required"`
}

// validKind compares the field to its constants.
func validKind(k string) bool { return k == "public" || k == "private" }

// CreateProject: the key is the client's (body `id`).
func CreateProject(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req projectReq
		if err := c.ShouldBindJSON(&req); err != nil || !validKind(req.Kind) {
			c.JSON(http.StatusUnprocessableEntity, gin.H{"code": "invalid"})
			return
		}
		p := models.Project{ID: req.ID, OwnerID: caller(c).ID, Name: req.Name, Kind: req.Kind, Status: "draft"}
		if err := db.Create(&p).Error; err != nil {
			c.JSON(http.StatusConflict, gin.H{"code": "conflict"})
			return
		}
		c.JSON(http.StatusCreated, p)
	}
}

// ownedProject loads the caller's project (the path id, through a helper).
func ownedProject(db *gorm.DB, userID uint64, id string) (models.Project, error) {
	var p models.Project
	err := db.Where("id = ? AND owner_id = ?", id, userID).Take(&p).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return p, errNotFound
	}
	return p, err
}

var errNotFound = errors.New("not found")

func GetProject(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		p, err := ownedProject(db, caller(c).ID, c.Param("id"))
		if err != nil {
			c.JSON(http.StatusNotFound, gin.H{"code": "not_found"})
			return
		}
		c.JSON(http.StatusOK, p)
	}
}

// Publish moves a draft to published: the from-state is an enclosing test.
func Publish(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		p, err := ownedProject(db, caller(c).ID, c.Param("id"))
		if err != nil {
			c.JSON(http.StatusNotFound, gin.H{"code": "not_found"})
			return
		}
		if p.Status == "draft" {
			if err := db.Model(&p).Update("status", "published").Error; err != nil {
				c.Status(http.StatusInternalServerError)
				return
			}
			c.JSON(http.StatusOK, p)
			return
		}
		c.JSON(http.StatusConflict, gin.H{"code": "not_draft"})
	}
}

// Archive archives a published or a draft project: a switch on the column.
func Archive(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		p, err := ownedProject(db, caller(c).ID, c.Param("id"))
		if err != nil {
			c.JSON(http.StatusNotFound, gin.H{"code": "not_found"})
			return
		}
		switch p.Status {
		case "draft", "published":
			db.Model(&p).Update("status", "archived")
			c.Status(http.StatusNoContent)
		default:
			c.JSON(http.StatusConflict, gin.H{"code": "already_archived"})
		}
	}
}

type ticketReq struct {
	ProjectID string `json:"project_id" binding:"required"`
	Subject   string `json:"subject" binding:"required"`
}

// CreateTicket: the server assigns the id and does not answer it (only the list does).
func CreateTicket(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req ticketReq
		if err := c.ShouldBindJSON(&req); err != nil {
			c.JSON(http.StatusUnprocessableEntity, gin.H{"code": "invalid"})
			return
		}
		if _, err := ownedProject(db, caller(c).ID, req.ProjectID); err != nil {
			c.JSON(http.StatusNotFound, gin.H{"code": "not_found"})
			return
		}
		t := models.Ticket{ProjectID: req.ProjectID, Subject: req.Subject}
		db.Create(&t)
		c.JSON(http.StatusCreated, gin.H{"ok": true})
	}
}

func ListTickets(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var rows []models.Ticket
		db.Order("id").Find(&rows)
		c.JSON(http.StatusOK, gin.H{"items": rows})
	}
}

// GetTicket compares the path value in the query itself.
func GetTicket(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var t models.Ticket
		if err := db.Where("id = ?", c.Param("id")).First(&t).Error; err != nil {
			c.JSON(http.StatusNotFound, gin.H{"code": "not_found"})
			return
		}
		c.JSON(http.StatusOK, t)
	}
}

// DeleteTicket reads the id as GORM's primary key (First(&t, id)).
func DeleteTicket(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		id, err := strconv.ParseUint(c.Param("id"), 10, 64)
		if err != nil {
			c.JSON(http.StatusUnprocessableEntity, gin.H{"code": "invalid"})
			return
		}
		var t models.Ticket
		if err := db.First(&t, id).Error; err != nil {
			c.JSON(http.StatusNotFound, gin.H{"code": "not_found"})
			return
		}
		db.Delete(&t)
		c.Status(http.StatusNoContent)
	}
}

type batchItem struct {
	Subject string `json:"subject"`
}

type batchReq struct {
	ProjectID string      `json:"project_id" binding:"required"`
	Items     []batchItem `json:"items"`
}

// Batch has a nested body: items[].
func Batch(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req batchReq
		if err := c.ShouldBindJSON(&req); err != nil || len(req.Items) == 0 {
			c.JSON(http.StatusUnprocessableEntity, gin.H{"code": "invalid"})
			return
		}
		c.JSON(http.StatusOK, gin.H{"n": len(req.Items)})
	}
}

type jobReq struct {
	Name string `json:"name" binding:"required"`
}

// CreateJob starts a job queued.
func CreateJob(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req jobReq
		if err := c.ShouldBindJSON(&req); err != nil {
			c.JSON(http.StatusUnprocessableEntity, gin.H{"code": "invalid"})
			return
		}
		j := models.Job{OwnerID: caller(c).ID, Name: req.Name, Status: "queued"}
		db.Create(&j)
		c.JSON(http.StatusCreated, j)
	}
}

// Activate moves a queued job to active: the from-state is the query's condition.
func Activate(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		res := db.Model(&models.Job{}).Where("id = ? AND owner_id = ? AND status = ?",
			c.Param("id"), caller(c).ID, "queued").Update("status", "active")
		if res.RowsAffected == 0 {
			c.JSON(http.StatusConflict, gin.H{"code": "not_queued"})
			return
		}
		c.Status(http.StatusNoContent)
	}
}

// StartRun needs an active job: a 409 guard.
func StartRun(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var j models.Job
		if err := db.Where("id = ? AND owner_id = ?", c.Param("id"), caller(c).ID).Take(&j).Error; err != nil {
			c.JSON(http.StatusNotFound, gin.H{"code": "not_found"})
			return
		}
		if j.Status != "active" {
			c.JSON(http.StatusConflict, gin.H{"code": "job_not_active"})
			return
		}
		r := models.Run{JobID: j.ID, State: "running"}
		db.Create(&r)
		c.JSON(http.StatusCreated, r)
	}
}
