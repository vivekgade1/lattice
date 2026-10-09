// Package handlers is the tasks service's routes.
package handlers

import (
	"errors"
	"net/http"

	"example.com/tasks/internal/models"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

type API struct {
	DB *gorm.DB
}

type createTask struct {
	Title string `json:"title" binding:"required,max=120"`
}

type addComment struct {
	Body string `json:"body" binding:"required,max=2000"`
}

var errNotYours = errors.New("not your task")

func caller(c *gin.Context) uint { return c.GetUint("uid") }

func (a *API) Register(r *gin.RouterGroup) {
	r.POST("/tasks", a.CreateTask)
	r.GET("/tasks/:id", a.GetTask)
	r.POST("/tasks/:id/start", a.StartTask)
	r.POST("/tasks/:id/reopen", a.ReopenTask)
	r.POST("/tasks/:id/comments", a.AddComment)
	r.GET("/me", a.Me)
}

func (a *API) CreateTask(c *gin.Context) {
	var req createTask
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusUnprocessableEntity, gin.H{"detail": err.Error()})
		return
	}
	task := models.Task{OwnerID: caller(c), Title: req.Title, State: models.StateTodo}
	if err := a.DB.Create(&task).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "db"})
		return
	}
	c.JSON(http.StatusCreated, task)
}

func (a *API) GetTask(c *gin.Context) {
	var task models.Task
	if err := a.DB.Where("id = ? AND owner_id = ?", c.Param("id"), caller(c)).First(&task).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"detail": "Not Found"})
		return
	}
	c.JSON(http.StatusOK, task)
}

// StartTask is correct: a conditional update, refused when it changed no row.
func (a *API) StartTask(c *gin.Context) {
	res := a.DB.Model(&models.Task{}).
		Where("id = ? AND owner_id = ? AND state = ?", c.Param("id"), caller(c), models.StateTodo).
		Update("state", models.StateDoing)
	if res.Error != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "db"})
		return
	}
	if res.RowsAffected == 0 {
		c.JSON(http.StatusConflict, gin.H{"detail": "the task is not todo"})
		return
	}
	c.Status(http.StatusNoContent)
}

// ReopenTask moves a finished task back to todo: done -> todo has no edge.
func (a *API) ReopenTask(c *gin.Context) {
	var task models.Task
	if err := a.DB.Where("id = ? AND owner_id = ?", c.Param("id"), caller(c)).First(&task).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"detail": "Not Found"})
		return
	}
	if task.State != models.StateDone {
		c.JSON(http.StatusConflict, gin.H{"detail": "the task is not done"})
		return
	}
	task.State = models.StateTodo
	a.DB.Save(&task)
	c.JSON(http.StatusOK, task)
}

// AddComment writes the comment before it checks the task is the caller's.
func (a *API) AddComment(c *gin.Context) {
	var req addComment
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusUnprocessableEntity, gin.H{"detail": err.Error()})
		return
	}
	var task models.Task
	if err := a.DB.First(&task, c.Param("id")).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"detail": "Not Found"})
		return
	}
	comment := models.Comment{TaskID: task.ID, Body: req.Body}
	a.DB.Create(&comment)
	if task.OwnerID != caller(c) {
		c.JSON(http.StatusNotFound, gin.H{"detail": "Not Found"})
		return
	}
	c.JSON(http.StatusCreated, comment)
}

// Me answers the caller's row, password hash included.
func (a *API) Me(c *gin.Context) {
	var user models.User
	if err := a.DB.First(&user, caller(c)).Error; err != nil {
		c.JSON(http.StatusUnauthorized, gin.H{"detail": "Not authenticated"})
		return
	}
	c.JSON(http.StatusOK, user)
}
