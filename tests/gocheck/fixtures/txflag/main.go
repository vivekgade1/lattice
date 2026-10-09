package main

import (
	"net/http"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

type User struct {
	ID   uint   `json:"id"`
	Name string `json:"name"`
}

type Membership struct {
	ID     uint   `json:"id"`
	UserID uint   `json:"user_id"`
	Role   string `json:"role"`
}

type H struct{ db *gorm.DB }

func abortDetail(c *gin.Context, status int, msg string) {
	c.AbortWithStatusJSON(status, gin.H{"detail": msg})
}

func setup(r *gin.Engine, h *H) {
	r.POST("/ok/flag", h.OkFlagNotFound)
	r.POST("/ok/flag-two", h.OkFlagTwoSites)
	r.POST("/ok/flag-nested", h.OkFlagNested)
	r.POST("/bad/flag-after-write", h.BadFlagAfterWrite)
	r.POST("/bad/flag-no-return", h.BadFlagNoReturn)
	r.POST("/bad/flag-loop", h.BadFlagLoop)
	r.POST("/bad/flag-reset", h.BadFlagSetOutside)
}

func (h *H) OkFlagNotFound(c *gin.Context) {
	uid := c.Param("id")
	var notFound bool
	err := h.db.Transaction(func(tx *gorm.DB) error {
		var n int64
		if err := tx.Model(&User{}).Where("id = ?", uid).Count(&n).Error; err != nil {
			return err
		}
		if n == 0 {
			notFound = true
			return nil
		}
		if err := tx.Create(&Membership{Role: "executor"}).Error; err != nil {
			return err
		}
		return tx.Model(&User{}).Where("id = ?", uid).Update("name", "x").Error
	})
	if err != nil {
		abortDetail(c, http.StatusInternalServerError, "db")
		return
	}
	if notFound {
		abortDetail(c, http.StatusNotFound, "User not found")
		return
	}
	c.Status(http.StatusCreated)
}

func (h *H) OkFlagTwoSites(c *gin.Context) {
	uid := c.Param("id")
	missing := false
	err := h.db.Transaction(func(tx *gorm.DB) error {
		var u User
		if err := tx.First(&u, uid).Error; err != nil {
			missing = true
			return nil
		}
		var n int64
		tx.Model(&Membership{}).Where("user_id = ?", u.ID).Count(&n)
		if n == 0 {
			missing = true
			return nil
		}
		return tx.Create(&Membership{UserID: u.ID, Role: "executor"}).Error
	})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{})
		return
	}
	if missing {
		c.JSON(http.StatusNotFound, gin.H{"detail": "not found"})
		return
	}
	c.Status(http.StatusCreated)
}

func (h *H) OkFlagNested(c *gin.Context) {
	uid := c.Param("id")
	var notFound bool
	err := h.db.Transaction(func(tx *gorm.DB) error {
		var n int64
		if err := tx.Model(&User{}).Where("id = ?", uid).Count(&n).Error; err == nil {
			if n == 0 {
				notFound = true
				return nil
			}
		} else {
			return err
		}
		return tx.Create(&Membership{Role: "executor"}).Error
	})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{})
		return
	}
	if notFound {
		c.JSON(http.StatusNotFound, gin.H{"detail": "not found"})
		return
	}
	c.Status(http.StatusCreated)
}

// the flag is set after the membership was written: the commit precedes the 404
func (h *H) BadFlagAfterWrite(c *gin.Context) {
	uid := c.Param("id")
	var notFound bool
	err := h.db.Transaction(func(tx *gorm.DB) error {
		if err := tx.Create(&Membership{Role: "executor"}).Error; err != nil {
			return err
		}
		var n int64
		tx.Model(&User{}).Where("id = ?", uid).Count(&n)
		if n == 0 {
			notFound = true
			return nil
		}
		return nil
	})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{})
		return
	}
	if notFound {
		c.JSON(http.StatusNotFound, gin.H{"detail": "not found"})
		return
	}
	c.Status(http.StatusCreated)
}

// the flag is set, then the function goes on to write
func (h *H) BadFlagNoReturn(c *gin.Context) {
	uid := c.Param("id")
	var notFound bool
	err := h.db.Transaction(func(tx *gorm.DB) error {
		var n int64
		tx.Model(&User{}).Where("id = ?", uid).Count(&n)
		if n == 0 {
			notFound = true
		}
		return tx.Create(&Membership{Role: "executor"}).Error
	})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{})
		return
	}
	if notFound {
		c.JSON(http.StatusNotFound, gin.H{"detail": "not found"})
		return
	}
	c.Status(http.StatusCreated)
}

// an earlier pass of the loop wrote before the flag is set
func (h *H) BadFlagLoop(c *gin.Context) {
	ids := []uint{1, 2, 3}
	var notFound bool
	err := h.db.Transaction(func(tx *gorm.DB) error {
		for _, id := range ids {
			var n int64
			tx.Model(&User{}).Where("id = ?", id).Count(&n)
			if n == 0 {
				notFound = true
				return nil
			}
			tx.Create(&Membership{UserID: id, Role: "executor"})
		}
		return nil
	})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{})
		return
	}
	if notFound {
		c.JSON(http.StatusNotFound, gin.H{"detail": "not found"})
		return
	}
	c.Status(http.StatusCreated)
}

// the flag is also set outside the transaction: not the narrow pattern
func (h *H) BadFlagSetOutside(c *gin.Context) {
	uid := c.Param("id")
	var notFound bool
	err := h.db.Transaction(func(tx *gorm.DB) error {
		var n int64
		tx.Model(&User{}).Where("id = ?", uid).Count(&n)
		if n == 0 {
			notFound = true
			return nil
		}
		return tx.Create(&Membership{Role: "executor"}).Error
	})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{})
		return
	}
	if c.Query("strict") == "1" {
		notFound = true
	}
	if notFound {
		c.JSON(http.StatusNotFound, gin.H{"detail": "not found"})
		return
	}
	c.Status(http.StatusCreated)
}

func main() {}
