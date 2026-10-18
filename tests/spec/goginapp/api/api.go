// Package api is the goginapp HTTP surface: the goapp service (tests/spec/goapp) rewritten
// on Gin + GORM, with run ownership.
//
//	GET  /health                  200 once the router is up
//	POST /login                   {"username", "password"} -> {"access_token", "user"}
//	GET  /configs                 every config
//	POST /configs/:cid/runs       queue a run for the caller; at most one live run per config
//	GET  /runs                    the caller's runs (?limit=1..50)
//	GET  /runs/:rid               one of the caller's runs
//	POST /runs/:rid/cancel        cancel one of the caller's queued runs
package api

import (
	"net/http"
	"strconv"
	"strings"
	"sync"

	"github.com/gin-gonic/gin"
	"golang.org/x/crypto/bcrypt"
	"gorm.io/gorm"

	"example.com/goginapp/models"
)

var liveMu sync.Mutex

// Mount registers every route of the app.
func Mount(r *gin.Engine, db *gorm.DB) {
	r.GET("/health", Health)
	r.POST("/login", Login(db))

	auth := r.Group("/", RequireUser(db))
	auth.GET("/configs", ListConfigs(db))
	auth.POST("/configs/:cid/runs", CreateRun(db))
	auth.GET("/runs", ListRuns(db))
	auth.GET("/runs/:rid", GetRun(db))
	auth.POST("/runs/:rid/cancel", CancelRun(db))
}

// Health answers 200.
func Health(c *gin.Context) { c.JSON(http.StatusOK, gin.H{"ok": true}) }

func detail(c *gin.Context, status int, msg string) {
	c.JSON(status, gin.H{"detail": msg})
}

type loginReq struct {
	Username string `json:"username" binding:"required"`
	Password string `json:"password" binding:"required"`
}

// Login checks the password (a bcrypt hash, or a plain value as the import's seed writes
// it) and answers the user's token.
func Login(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req loginReq
		if err := c.ShouldBindJSON(&req); err != nil {
			detail(c, http.StatusUnprocessableEntity, "bad body")
			return
		}
		var u models.User
		if err := db.Where("name = ?", req.Username).First(&u).Error; err != nil {
			detail(c, http.StatusUnauthorized, "Incorrect username or password")
			return
		}
		if err := bcrypt.CompareHashAndPassword([]byte(u.Password), []byte(req.Password)); err != nil &&
			u.Password != req.Password {
			detail(c, http.StatusUnauthorized, "Incorrect username or password")
			return
		}
		c.JSON(http.StatusOK, gin.H{"access_token": u.Token, "token_type": "bearer", "user": u})
	}
}

// RequireUser authenticates: a 401 refusal, and the caller recorded under userID.
func RequireUser(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		scheme, token, _ := strings.Cut(c.GetHeader("Authorization"), " ")
		if !strings.EqualFold(scheme, "bearer") || token == "" {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"detail": "Not authenticated"})
			return
		}
		var u models.User
		if err := db.Where("token = ?", token).Take(&u).Error; err != nil {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"detail": "Not authenticated"})
			return
		}
		c.Set("userID", u.ID)
		c.Next()
	}
}

// ListConfigs answers every config (shared).
func ListConfigs(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var configs []models.Config
		db.Order("id").Find(&configs)
		c.JSON(http.StatusOK, configs)
	}
}

// CreateRun queues a run for the caller on config cid.
func CreateRun(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		uid := c.GetUint("userID")
		cid, err := strconv.ParseUint(c.Param("cid"), 10, 64)
		if err != nil {
			detail(c, http.StatusUnprocessableEntity, "bad id")
			return
		}
		var cfg models.Config
		if err := db.First(&cfg, cid).Error; err != nil {
			detail(c, http.StatusNotFound, "Not found")
			return
		}
		liveMu.Lock()
		defer liveMu.Unlock()
		var live int64
		db.Model(&models.Run{}).Where("config_id = ? AND status = ?", cfg.ID, "queued").Count(&live)
		if live > 0 {
			detail(c, http.StatusConflict, "already live")
			return
		}
		run := models.Run{ConfigID: cfg.ID, UserID: uid, Status: "queued"}
		if err := db.Create(&run).Error; err != nil {
			detail(c, http.StatusInternalServerError, err.Error())
			return
		}
		c.JSON(http.StatusCreated, run)
	}
}

type listQuery struct {
	Limit int `form:"limit" binding:"omitempty,min=1,max=50"`
}

// ListRuns answers the caller's runs.
func ListRuns(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var q listQuery
		if err := c.ShouldBindQuery(&q); err != nil {
			detail(c, http.StatusUnprocessableEntity, "bad limit")
			return
		}
		limit := q.Limit
		if limit == 0 {
			limit = 50
		}
		var runs []models.Run
		db.Where("user_id = ?", c.GetUint("userID")).Order("id").Limit(limit).Find(&runs)
		c.JSON(http.StatusOK, runs)
	}
}

// GetRun answers one of the caller's runs.
func GetRun(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		rid, err := strconv.ParseUint(c.Param("rid"), 10, 64)
		if err != nil {
			detail(c, http.StatusUnprocessableEntity, "bad id")
			return
		}
		var run models.Run
		if err := db.Where("id = ? AND user_id = ?", rid, c.GetUint("userID")).First(&run).Error; err != nil {
			detail(c, http.StatusNotFound, "Not found")
			return
		}
		c.JSON(http.StatusOK, run)
	}
}

// CancelRun cancels one of the caller's queued runs.
func CancelRun(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		rid, err := strconv.ParseUint(c.Param("rid"), 10, 64)
		if err != nil {
			detail(c, http.StatusUnprocessableEntity, "bad id")
			return
		}
		var run models.Run
		if err := db.Where("id = ? AND user_id = ?", rid, c.GetUint("userID")).First(&run).Error; err != nil {
			detail(c, http.StatusNotFound, "Not found")
			return
		}
		if run.Status != "queued" {
			detail(c, http.StatusConflict, "already final")
			return
		}
		if err := db.Model(&run).Update("status", "cancelled").Error; err != nil {
			detail(c, http.StatusInternalServerError, err.Error())
			return
		}
		c.JSON(http.StatusOK, run)
	}
}
