// Package api: handlers.
package api

import (
	"errors"
	"net/http"
	"strconv"
	"time"

	"example.com/runs/store"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

var errGone = errors.New("gone")

type API struct{ DB *gorm.DB }

func fail(c *gin.Context, status int, msg string) {
	c.AbortWithStatusJSON(status, gin.H{"detail": msg})
}

func unprocessable(c *gin.Context, msg string) {
	c.JSON(http.StatusUnprocessableEntity, gin.H{"detail": msg})
}

func isUniqueViolation(err error) bool { return err != nil && err.Error() == "UNIQUE" }

func (a *API) Routes(r *gin.Engine) {
	r.POST("/runs/:id/cancel", a.OkCancel)
	r.POST("/runs/:id/start", a.OkStart)
	r.POST("/runs/:id/archive", a.OkTxSwitch)
	r.POST("/runs/:id/copy", a.OkTxIfInit)
	r.POST("/runs/:id/copy2", a.OkTxUnique)
	r.POST("/runs/:id/bad", a.BadTxThenRefuse)
	r.PUT("/runs/:id", a.OkTxAnswerHelper)
	r.PUT("/runs/:id/bad", a.BadAnswerAfterCommit)
	r.POST("/runs/:id/archive2", a.OkTxSwitchMulti)
	r.POST("/runs/:id/archive3", a.BadTxSwitchMulti)
}

func id(c *gin.Context) int64 {
	n, _ := strconv.ParseInt(c.Param("id"), 10, 64)
	return n
}

func (a *API) OkCancel(c *gin.Context) {
	var r store.JobRun
	if err := a.DB.First(&r, id(c)).Error; err != nil {
		fail(c, http.StatusNotFound, "Not Found")
		return
	}
	live, err := store.CancelRun(a.DB, r.ID, time.Now())
	if err != nil {
		fail(c, http.StatusInternalServerError, "db")
		return
	}
	if !live {
		fail(c, http.StatusConflict, "the run is finished")
		return
	}
	c.JSON(http.StatusOK, gin.H{"id": r.ID})
}

func (a *API) OkStart(c *gin.Context) {
	ok, err := store.StartRun(a.DB, id(c))
	if err != nil {
		fail(c, http.StatusInternalServerError, "db")
		return
	}
	if ok == false {
		unprocessable(c, "not queued")
		return
	}
	c.Status(http.StatusNoContent)
}

func (a *API) OkTxSwitch(c *gin.Context) {
	err := a.DB.Transaction(func(tx *gorm.DB) error {
		res := tx.Model(&store.JobRun{}).Where("id = ?", id(c)).Update("status", store.RunCancelled)
		if res.Error != nil {
			return res.Error
		}
		if res.RowsAffected == 0 {
			return errGone
		}
		return tx.Exec("DELETE FROM job_runs WHERE id = ?", id(c)).Error
	})
	switch {
	case errors.Is(err, errGone):
		fail(c, http.StatusNotFound, "gone")
	case isUniqueViolation(err):
		fail(c, http.StatusConflict, "duplicate")
	case err != nil:
		fail(c, http.StatusInternalServerError, "db")
	default:
		c.JSON(http.StatusOK, gin.H{})
	}
}

func (a *API) OkTxIfInit(c *gin.Context) {
	if err := a.DB.Transaction(func(tx *gorm.DB) error {
		if err := tx.Create(&store.JobRun{Status: store.RunQueued}).Error; err != nil {
			return err
		}
		if c.Query("x") == "" {
			return errGone
		}
		return nil
	}); err != nil {
		unprocessable(c, err.Error())
		return
	}
	c.Status(http.StatusCreated)
}

func (a *API) OkTxUnique(c *gin.Context) {
	err := a.DB.Transaction(func(tx *gorm.DB) error {
		return tx.Create(&store.JobRun{Status: store.RunQueued}).Error
	})
	if isUniqueViolation(err) {
		fail(c, http.StatusConflict, "duplicate")
		return
	}
	if err != nil {
		fail(c, http.StatusInternalServerError, "db")
		return
	}
	c.Status(http.StatusCreated)
}

// BadTxThenRefuse refuses after the transaction committed (err is nil there).
func (a *API) BadTxThenRefuse(c *gin.Context) {
	err := a.DB.Transaction(func(tx *gorm.DB) error {
		return tx.Create(&store.JobRun{Status: store.RunQueued}).Error
	})
	if err != nil {
		fail(c, http.StatusInternalServerError, "db")
		return
	}
	if c.Query("strict") != "" {
		fail(c, http.StatusBadRequest, "strict")
		return
	}
	c.Status(http.StatusCreated)
}

func serverError(c *gin.Context) { c.JSON(http.StatusInternalServerError, gin.H{"detail": "internal"}) }

func (a *API) answerRunWrite(c *gin.Context, r *store.JobRun, err error) {
	switch {
	case err == nil:
		c.JSON(http.StatusOK, r)
	case isUniqueViolation(err):
		fail(c, http.StatusConflict, "duplicate")
	default:
		serverError(c)
	}
}

func (a *API) OkTxAnswerHelper(c *gin.Context) {
	r := &store.JobRun{ID: id(c)}
	err := a.DB.Transaction(func(tx *gorm.DB) error {
		if err := tx.Save(r).Error; err != nil {
			return err
		}
		return tx.First(r, r.ID).Error
	})
	a.answerRunWrite(c, r, err)
}

// BadAnswerAfterCommit hands the helper an error that is not the transaction's.
func (a *API) BadAnswerAfterCommit(c *gin.Context) {
	r := &store.JobRun{ID: id(c)}
	a.DB.Save(r)
	a.answerRunWrite(c, r, errors.New("UNIQUE"))
}

// A case listing several error tests is taken only when the transaction failed.
func (a *API) OkTxSwitchMulti(c *gin.Context) {
	err := a.DB.Transaction(func(tx *gorm.DB) error {
		res := tx.Model(&store.JobRun{}).Where("id = ?", id(c)).Update("status", store.RunCancelled)
		if res.Error != nil {
			return res.Error
		}
		if res.RowsAffected == 0 {
			return errGone
		}
		return nil
	})
	switch {
	case errors.Is(err, errGone), errors.Is(err, gorm.ErrRecordNotFound):
		fail(c, http.StatusNotFound, "gone")
	case err != nil:
		fail(c, http.StatusInternalServerError, "db")
	default:
		c.JSON(http.StatusOK, gin.H{})
	}
}

// One of the listed tests does not need the transaction to have failed.
func (a *API) BadTxSwitchMulti(c *gin.Context) {
	err := a.DB.Transaction(func(tx *gorm.DB) error {
		return tx.Model(&store.JobRun{}).Where("id = ?", id(c)).Update("status", store.RunCancelled).Error
	})
	switch {
	case errors.Is(err, errGone), c.Query("strict") != "":
		fail(c, http.StatusConflict, "refused")
	default:
		c.JSON(http.StatusOK, gin.H{})
	}
}
