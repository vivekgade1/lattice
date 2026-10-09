package main

import (
	"errors"
	"fmt"
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

var errNotQueued = errors.New("the job is not queued")

const statusUnprocessable = 422

func respond(c *gin.Context, status int, msg string) {
	c.JSON(status, gin.H{"detail": msg})
}

func (h *Handler) logEvent(jobID uint, kind string) {
	h.db.Create(&JobEvent{JobID: jobID, Kind: kind})
}

// startJob writes an event, then can still return an error the caller refuses with.
func (h *Handler) startJob(id uint) error {
	h.db.Create(&JobEvent{JobID: id, Kind: "start"})
	var j Job
	if err := h.db.First(&j, id).Error; err != nil {
		return err
	}
	if j.Status != "queued" {
		return errNotQueued
	}
	return h.db.Model(&j).Update("status", "running").Error
}

func (h *Handler) BadCreateThenValidate(c *gin.Context) {
	var job Job
	if err := c.ShouldBindJSON(&job); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"detail": err.Error()})
		return
	}
	if err := h.db.Create(&job).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "db"})
		return
	}
	if job.Name == "" {
		c.JSON(http.StatusUnprocessableEntity, gin.H{"detail": "name is required"})
		return
	}
	c.JSON(http.StatusCreated, job)
}

func (h *Handler) BadWriteThenNotFound(c *gin.Context) {
	id, _ := strconv.Atoi(c.Param("id"))
	h.db.Create(&JobEvent{JobID: uint(id), Kind: "lookup"})
	var job Job
	if err := h.db.First(&job, id).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"detail": "not found"})
		return
	}
	c.JSON(http.StatusOK, job)
}

func (h *Handler) BadHelperCommits(c *gin.Context) {
	id, _ := strconv.Atoi(c.Param("id"))
	h.logEvent(uint(id), "touch")
	if id > 100 {
		c.AbortWithStatus(http.StatusForbidden)
		return
	}
	c.Status(http.StatusNoContent)
}

func (h *Handler) BadHelperError(c *gin.Context) {
	id, _ := strconv.Atoi(c.Param("id"))
	if err := h.startJob(uint(id)); err != nil {
		c.JSON(http.StatusConflict, gin.H{"detail": err.Error()})
		return
	}
	c.Status(http.StatusNoContent)
}

func (h *Handler) BadCommitThenRefuse(c *gin.Context) {
	tx := h.db.Begin()
	tx.Create(&JobEvent{Kind: "a"})
	tx.Commit()
	var job Job
	if err := h.db.First(&job, c.Param("id")).Error; err != nil {
		c.AbortWithStatusJSON(http.StatusNotFound, gin.H{"detail": "not found"})
		return
	}
	c.JSON(http.StatusOK, job)
}

func (h *Handler) BadLoop(c *gin.Context) {
	var jobs []Job
	if err := c.ShouldBindJSON(&jobs); err != nil {
		c.JSON(400, gin.H{"detail": "bad body"})
		return
	}
	for _, j := range jobs {
		if j.Name == "" {
			c.JSON(statusUnprocessable, gin.H{"detail": "name"})
			return
		}
		h.db.Create(&j)
	}
	c.Status(http.StatusCreated)
}

func (h *Handler) BadRespondHelper(c *gin.Context) {
	var job Job
	h.db.First(&job, c.Param("id"))
	h.db.Model(&job).Update("name", "renamed")
	if job.Status == "cancelled" {
		respond(c, http.StatusConflict, "cancelled")
		return
	}
	c.JSON(http.StatusOK, job)
}

func (h *Handler) BadTransactionThenRefuse(c *gin.Context) {
	var job Job
	err := h.db.Transaction(func(tx *gorm.DB) error {
		if err := tx.Create(&job).Error; err != nil {
			return err
		}
		return tx.Create(&JobEvent{JobID: job.ID, Kind: "created"}).Error
	})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "db"})
		return
	}
	if job.Name == "" {
		c.JSON(http.StatusBadRequest, gin.H{"detail": "name"})
		return
	}
	c.JSON(http.StatusCreated, job)
}

func (h *Handler) BadDBInsideTransaction(c *gin.Context) {
	err := h.db.Transaction(func(tx *gorm.DB) error {
		h.db.Create(&JobEvent{Kind: "outside the transaction"})
		var job Job
		if err := tx.First(&job, c.Param("id")).Error; err != nil {
			return err
		}
		if job.Status != "queued" {
			return errNotQueued
		}
		return tx.Model(&job).Update("status", "running").Error
	})
	if errors.Is(err, errNotQueued) {
		c.JSON(http.StatusConflict, gin.H{"detail": "not queued"})
		return
	}
	c.Status(http.StatusNoContent)
}

func (h *Handler) BadExec(c *gin.Context) {
	h.db.Exec("UPDATE jobs SET name = ? WHERE id = ?", "x", c.Param("id"))
	if c.Query("strict") == "1" {
		c.JSON(http.StatusBadRequest, gin.H{"detail": "strict"})
		return
	}
	c.Status(http.StatusNoContent)
}

func (h *Handler) BadDeleteThenAbort(c *gin.Context) {
	h.db.Where("job_id = ?", c.Param("id")).Delete(&JobEvent{})
	var job Job
	res := h.db.Where("id = ?", c.Param("id")).First(&job)
	if res.Error != nil {
		c.AbortWithStatus(http.StatusNotFound)
		return
	}
	c.JSON(http.StatusOK, job)
}

// ---- the correct idioms -------------------------------------------------------------

func (h *Handler) OkCheckFirst(c *gin.Context) {
	var job Job
	if err := c.ShouldBindJSON(&job); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"detail": err.Error()})
		return
	}
	if job.Name == "" {
		c.JSON(http.StatusUnprocessableEntity, gin.H{"detail": "name is required"})
		return
	}
	if err := h.db.Create(&job).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "db"})
		return
	}
	c.JSON(http.StatusCreated, job)
}

func (h *Handler) OkTransaction(c *gin.Context) {
	id := c.Param("id")
	err := h.db.Transaction(func(tx *gorm.DB) error {
		if err := tx.Create(&JobEvent{Kind: "start"}).Error; err != nil {
			return err
		}
		var job Job
		if err := tx.First(&job, id).Error; err != nil {
			return err
		}
		if job.Status != "queued" {
			return errNotQueued
		}
		return tx.Model(&job).Update("status", "running").Error
	})
	if errors.Is(err, errNotQueued) {
		c.JSON(http.StatusConflict, gin.H{"detail": "not queued"})
		return
	}
	if errors.Is(err, gorm.ErrRecordNotFound) {
		c.JSON(http.StatusNotFound, gin.H{"detail": "not found"})
		return
	}
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "db"})
		return
	}
	c.Status(http.StatusNoContent)
}

func (h *Handler) OkConditionalUpdate(c *gin.Context) {
	res := h.db.Model(&Job{}).Where("id = ? AND status IN ?", c.Param("id"), []string{"queued", "running"}).
		Update("status", "cancelled")
	if res.Error != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "db"})
		return
	}
	if res.RowsAffected == 0 {
		c.JSON(http.StatusConflict, gin.H{"detail": "the job cannot be cancelled"})
		return
	}
	c.Status(http.StatusNoContent)
}

func (h *Handler) OkWriteError(c *gin.Context) {
	job := Job{Name: "x", Status: "queued"}
	if err := h.db.Create(&job).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "db"})
		return
	}
	c.JSON(http.StatusCreated, job)
}

func (h *Handler) OkBeginRollback(c *gin.Context) {
	tx := h.db.Begin()
	defer tx.Rollback()
	if err := tx.Create(&JobEvent{Kind: "a"}).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "db"})
		return
	}
	var job Job
	if err := tx.First(&job, c.Param("id")).Error; err != nil {
		tx.Rollback()
		c.JSON(http.StatusNotFound, gin.H{"detail": "not found"})
		return
	}
	if err := tx.Commit().Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "commit"})
		return
	}
	c.JSON(http.StatusOK, job)
}

func (h *Handler) OkBranch(c *gin.Context) {
	if c.Query("kind") == "event" {
		ev := JobEvent{Kind: "manual"}
		h.db.Create(&ev)
		c.JSON(http.StatusCreated, ev)
		return
	}
	c.JSON(http.StatusBadRequest, gin.H{"detail": "unknown kind"})
}

func (h *Handler) save(j *Job) error {
	return h.db.Save(j).Error
}

func (h *Handler) OkDBErrorHelper(c *gin.Context) {
	var job Job
	if err := h.db.First(&job, c.Param("id")).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"detail": "not found"})
		return
	}
	job.Name = "renamed"
	if err := h.save(&job); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "db"})
		return
	}
	c.JSON(http.StatusOK, job)
}

func validate(j *Job) error {
	if j.Name == "" {
		return errors.New("name is required")
	}
	return nil
}

func (h *Handler) OkValidateHelper(c *gin.Context) {
	var job Job
	_ = c.ShouldBindJSON(&job)
	if err := validate(&job); err != nil {
		c.JSON(http.StatusUnprocessableEntity, gin.H{"detail": err.Error()})
		return
	}
	h.db.Create(&job)
	c.JSON(http.StatusCreated, job)
}

func (h *Handler) create(j *Job) error {
	if j.Name == "" {
		return errors.New("name is required")
	}
	if err := h.db.Create(j).Error; err != nil {
		return fmt.Errorf("create: %w", err)
	}
	return nil
}

func (h *Handler) OkCreateHelper(c *gin.Context) {
	var job Job
	_ = c.ShouldBindJSON(&job)
	if err := h.create(&job); err != nil {
		c.JSON(http.StatusUnprocessableEntity, gin.H{"detail": err.Error()})
		return
	}
	c.JSON(http.StatusCreated, job)
}

func (h *Handler) OkRowsAffectedChain(c *gin.Context) {
	if h.db.Model(&Job{}).Where("id = ? AND status = ?", c.Param("id"), "running").
		Update("status", "succeeded").RowsAffected == 0 {
		c.JSON(http.StatusConflict, gin.H{"detail": "not running"})
		return
	}
	c.Status(http.StatusNoContent)
}

func (h *Handler) OkValidateAllFirst(c *gin.Context) {
	var jobs []Job
	if err := c.ShouldBindJSON(&jobs); err != nil {
		c.JSON(400, gin.H{"detail": "bad body"})
		return
	}
	for _, j := range jobs {
		if j.Name == "" {
			c.JSON(statusUnprocessable, gin.H{"detail": "name"})
			return
		}
	}
	for i := range jobs {
		h.db.Create(&jobs[i])
	}
	c.Status(http.StatusCreated)
}

func (h *Handler) OkSuccessOnly(c *gin.Context) {
	ev := JobEvent{Kind: "ping"}
	h.db.Create(&ev)
	c.JSON(http.StatusCreated, ev)
}

func (h *Handler) OkSaveErrorWrapped(c *gin.Context) {
	var job Job
	if err := h.db.First(&job, c.Param("id")).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"detail": "not found"})
		return
	}
	result := h.db.Save(&job)
	if result.Error != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": result.Error.Error()})
		return
	}
	c.JSON(http.StatusOK, job)
}

type Repo struct{ db *gorm.DB }

func NewRepo(db *gorm.DB) *Repo { return &Repo{db: db} }

func (r *Repo) AddEvent(ev *JobEvent) error { return r.db.Create(ev).Error }

func (h *Handler) OkRequestTx(c *gin.Context) {
	db := c.MustGet("tx").(*gorm.DB) // a middleware commits or rolls it back
	db.Create(&JobEvent{Kind: "a"})
	if c.Query("bad") != "" {
		c.JSON(http.StatusBadRequest, gin.H{"detail": "bad"})
		return
	}
	c.Status(http.StatusNoContent)
}

func (h *Handler) OkRepoWithTx(c *gin.Context) {
	err := h.db.Transaction(func(tx *gorm.DB) error {
		repo := NewRepo(tx)
		if err := repo.AddEvent(&JobEvent{Kind: "start"}); err != nil {
			return err
		}
		if c.Query("force") == "" {
			return errNotQueued
		}
		return nil
	})
	if errors.Is(err, errNotQueued) {
		c.JSON(http.StatusConflict, gin.H{"detail": "not queued"})
		return
	}
	c.Status(http.StatusNoContent)
}

func (h *Handler) BadHelperOutsideTx(c *gin.Context) {
	err := h.db.Transaction(func(tx *gorm.DB) error {
		h.logEvent(1, "outside") // writes on h.db, not tx: kept on rollback
		if c.Query("force") == "" {
			return errNotQueued
		}
		return tx.Create(&JobEvent{Kind: "inside"}).Error
	})
	if err != nil {
		c.JSON(http.StatusConflict, gin.H{"detail": err.Error()})
		return
	}
	c.Status(http.StatusNoContent)
}

func (h *Handler) OkReloadFailure(c *gin.Context) {
	var job Job
	h.db.Model(&Job{}).Where("id = ?", c.Param("id")).Update("name", "renamed")
	if err := h.db.First(&job, c.Param("id")).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "reload failed"})
		return
	}
	c.JSON(http.StatusOK, job)
}

func (h *Handler) OkClosureOverTx(c *gin.Context) {
	tx := h.db.Begin()
	add := func(kind string) error { return tx.Create(&JobEvent{Kind: kind}).Error }
	if err := add("a"); err != nil {
		tx.Rollback()
		c.JSON(http.StatusInternalServerError, gin.H{})
		return
	}
	if c.Query("bad") != "" {
		tx.Rollback()
		c.JSON(http.StatusBadRequest, gin.H{})
		return
	}
	tx.Commit()
	c.Status(http.StatusNoContent)
}
