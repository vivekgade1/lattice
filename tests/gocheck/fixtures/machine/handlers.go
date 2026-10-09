package main

import (
	"net/http"
	"slices"

	"example.com/machine/models"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

type Handler struct {
	DB *gorm.DB
}

func (h *Handler) load(c *gin.Context) (*models.Job, bool) {
	var job models.Job
	if err := h.DB.First(&job, c.Param("id")).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"detail": "not found"})
		return nil, false
	}
	return &job, true
}

// ---- writes the machine forbids from a guarded state --------------------------------

func (h *Handler) BadRetrySucceeded(c *gin.Context) {
	var job models.Job
	if err := h.DB.First(&job, c.Param("id")).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"detail": "not found"})
		return
	}
	if job.Status != models.StatusSucceeded && job.Status != models.StatusFailed {
		c.JSON(http.StatusConflict, gin.H{"detail": "not finished"})
		return
	}
	job.Status = models.StatusQueued // succeeded/failed -> queued: no edge
	h.DB.Save(&job)
	c.JSON(http.StatusOK, job)
}

func (h *Handler) BadUpdateFromRunning(c *gin.Context) {
	var job models.Job
	h.DB.First(&job, c.Param("id"))
	if job.Status == "running" {
		h.DB.Model(&job).Update("status", "queued") // running -> queued
	}
	c.Status(http.StatusNoContent)
}

func (h *Handler) BadUpdatesMap(c *gin.Context) {
	var job models.Job
	h.DB.First(&job, c.Param("id"))
	if job.Status != "cancelled" {
		c.JSON(http.StatusConflict, gin.H{})
		return
	}
	h.DB.Model(&job).Updates(map[string]interface{}{"status": "running"}) // cancelled -> running
	c.Status(http.StatusNoContent)
}

func (h *Handler) BadWhereGuard(c *gin.Context) {
	res := h.DB.Model(&models.Job{}).
		Where("id = ? AND status IN ?", c.Param("id"), []string{"queued", "succeeded"}).
		Update("status", "running") // succeeded -> running
	if res.RowsAffected == 0 {
		c.JSON(http.StatusConflict, gin.H{})
		return
	}
	c.Status(http.StatusNoContent)
}

func (h *Handler) BadSwitch(c *gin.Context) {
	job, ok := h.load(c)
	if !ok {
		return
	}
	switch job.Status {
	case models.StatusFailed:
		h.DB.Model(job).UpdateColumn("status", models.StatusSucceeded) // failed -> succeeded
	}
	c.Status(http.StatusNoContent)
}

func (h *Handler) BadUpdatesStruct(c *gin.Context) {
	var job models.Job
	h.DB.First(&job, c.Param("id"))
	if job.Status != models.StatusQueued {
		return
	}
	h.DB.Model(&job).Updates(models.Job{Status: models.StatusSucceeded}) // queued -> succeeded
}

func (h *Handler) BadWhereLiteral(c *gin.Context) {
	h.DB.Table("jobs").Where("status = 'cancelled' AND id = ?", c.Param("id")).
		Update("status", "queued") // cancelled -> queued
}

// ---- silent: legal, unguarded, or guarded elsewhere ----------------------------------

func (h *Handler) OkCancel(c *gin.Context) {
	var job models.Job
	h.DB.First(&job, c.Param("id"))
	if job.Status != models.StatusQueued && job.Status != models.StatusRunning {
		c.JSON(http.StatusConflict, gin.H{"detail": "cannot cancel"})
		return
	}
	job.Status = models.StatusCancelled
	h.DB.Save(&job)
	c.JSON(http.StatusOK, job)
}

func (h *Handler) OkConditionalCancel(c *gin.Context) {
	res := h.DB.Model(&models.Job{}).
		Where("id = ? AND status IN ?", c.Param("id"), []string{"queued", "running"}).
		Update("status", "cancelled")
	if res.Error != nil {
		c.JSON(http.StatusInternalServerError, gin.H{})
		return
	}
	if res.RowsAffected == 0 {
		c.JSON(http.StatusConflict, gin.H{"detail": "cannot cancel"})
		return
	}
	c.Status(http.StatusNoContent)
}

func (h *Handler) OkUnguarded(c *gin.Context) {
	var job models.Job
	h.DB.First(&job, c.Param("id"))
	h.DB.Model(&job).Update("status", "running") // no check: not judged
}

func (h *Handler) OkTableGuard(c *gin.Context) {
	var job models.Job
	h.DB.First(&job, c.Param("id"))
	if !slices.Contains(models.Transitions[job.Status], models.StatusSucceeded) {
		c.JSON(http.StatusConflict, gin.H{})
		return
	}
	h.DB.Model(&job).Update("status", models.StatusSucceeded)
}

func canMove(from, to models.JobStatus) bool {
	return slices.Contains(models.Transitions[from], to)
}

func (h *Handler) OkHelperGuard(c *gin.Context) {
	var job models.Job
	h.DB.First(&job, c.Param("id"))
	if job.Status == models.StatusCancelled {
		return
	}
	if !canMove(job.Status, models.StatusRunning) {
		c.JSON(http.StatusConflict, gin.H{})
		return
	}
	job.Status = models.StatusRunning
	h.DB.Save(&job)
}

func (h *Handler) OkReloaded(c *gin.Context) {
	var job models.Job
	h.DB.First(&job, c.Param("id"))
	if job.Status != models.StatusSucceeded {
		return
	}
	h.DB.First(&job, c.Query("other")) // another row now
	h.DB.Model(&job).Update("status", "running")
}

func (h *Handler) OkNotSaved(c *gin.Context) {
	var job models.Job
	h.DB.First(&job, c.Param("id"))
	if job.Status != models.StatusSucceeded {
		return
	}
	job.Status = models.StatusQueued // a copy for the answer, never saved
	c.JSON(http.StatusOK, job)
}

func (h *Handler) OkWhereRunning(c *gin.Context) {
	h.DB.Model(&models.Job{}).Where("status = ?", models.StatusRunning).
		Update("status", models.StatusFailed)
}

func (h *Handler) OkLoopTouched(c *gin.Context) {
	var jobs []models.Job
	h.DB.Find(&jobs)
	for _, job := range jobs {
		if job.Status != models.StatusRunning {
			continue
		}
		h.DB.Model(&job).Update("status", models.StatusFailed)
	}
}

func (h *Handler) OkOrWhere(c *gin.Context) {
	h.DB.Model(&models.Job{}).Where("status = ? OR status = ?", "queued", "succeeded").
		Update("status", "running")
}

func (h *Handler) OkUnknownValue(c *gin.Context) {
	var job models.Job
	h.DB.First(&job, c.Param("id"))
	if job.Status != models.StatusSucceeded {
		return
	}
	h.DB.Model(&job).Update("status", c.Query("to"))
}
