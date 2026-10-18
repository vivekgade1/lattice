// Package api is the gotickets HTTP surface.
//
//	GET  /health                   200 once the router is up (public)
//	POST /login                    {"username", "password"} -> {"access_token", "token_type"} (public)
//	GET  /queues                   every queue (public, shared)
//	GET  /me                       the caller
//	POST /queues/:qid/tickets      open a ticket for the caller; one open ticket per queue and caller
//	GET  /tickets                  the caller's tickets (?page_size=1..100, ?status=open|closed)
//	GET  /tickets/:tid             one of the caller's tickets
//	POST /tickets/:tid/close       open -> closed
//	POST /tickets/:tid/reopen      closed -> open
//	POST /tickets/:tid/handoffs    hand one of the caller's tickets to another user
//	GET  /handoffs                 the handoffs from or to the caller
package api

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"strconv"
	"strings"
	"sync"

	"github.com/gin-gonic/gin"
	"golang.org/x/crypto/bcrypt"
	"gorm.io/gorm"

	"example.com/gotickets/models"
)

var openMu sync.Mutex

// Mount registers every route of the app; secret signs the tokens.
func Mount(r *gin.Engine, db *gorm.DB, secret []byte) {
	r.GET("/health", Health)
	r.POST("/login", Login(db, secret))
	r.GET("/queues", ListQueues(db))

	auth := r.Group("/", RequireUser(db, secret))
	auth.GET("/me", Me(db))
	auth.POST("/queues/:qid/tickets", CreateTicket(db))
	auth.GET("/tickets", ListTickets(db))
	auth.GET("/tickets/:tid", GetTicket(db))
	auth.POST("/tickets/:tid/close", CloseTicket(db))
	auth.POST("/tickets/:tid/reopen", ReopenTicket(db))
	auth.POST("/tickets/:tid/handoffs", CreateHandoff(db))
	auth.GET("/handoffs", ListHandoffs(db))
}

// Health answers 200.
func Health(c *gin.Context) { c.JSON(http.StatusOK, gin.H{"ok": true}) }

func detail(c *gin.Context, status int, msg string) {
	c.JSON(status, gin.H{"detail": msg})
}

func sign(secret []byte, id string) string {
	mac := hmac.New(sha256.New, secret)
	mac.Write([]byte(id))
	return hex.EncodeToString(mac.Sum(nil))
}

type loginReq struct {
	Username string `json:"username" binding:"required"`
	Password string `json:"password" binding:"required"`
}

// Login checks the password (a bcrypt hash, or an equal plain value as the seed writes it)
// and answers a signed token "<id>.<hex hmac-sha256(id)>". No token is stored.
func Login(db *gorm.DB, secret []byte) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req loginReq
		if err := c.ShouldBindJSON(&req); err != nil {
			detail(c, http.StatusUnprocessableEntity, "invalid body")
			return
		}
		var u models.User
		if err := db.Where("name = ?", req.Username).First(&u).Error; err != nil {
			detail(c, http.StatusUnauthorized, "Incorrect username or password")
			return
		}
		if err := bcrypt.CompareHashAndPassword([]byte(u.PasswordHash), []byte(req.Password)); err != nil &&
			u.PasswordHash != req.Password {
			detail(c, http.StatusUnauthorized, "Incorrect username or password")
			return
		}
		id := strconv.FormatUint(uint64(u.ID), 10)
		c.JSON(http.StatusOK, gin.H{"access_token": id + "." + sign(secret, id), "token_type": "bearer"})
	}
}

// RequireUser authenticates the bearer token: a 401 refusal, else the caller under "uid".
func RequireUser(db *gorm.DB, secret []byte) gin.HandlerFunc {
	return func(c *gin.Context) {
		scheme, token, _ := strings.Cut(c.GetHeader("Authorization"), " ")
		id, sig, ok := strings.Cut(token, ".")
		if !strings.EqualFold(scheme, "bearer") || !ok || !hmac.Equal([]byte(sig), []byte(sign(secret, id))) {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"detail": "Not authenticated"})
			return
		}
		n, err := strconv.ParseUint(id, 10, 64)
		var u models.User
		if err != nil || db.First(&u, n).Error != nil {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"detail": "Not authenticated"})
			return
		}
		c.Set("uid", u.ID)
		c.Next()
	}
}

// ListQueues answers every queue (shared).
func ListQueues(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var queues []models.Queue
		db.Order("id").Find(&queues)
		c.JSON(http.StatusOK, queues)
	}
}

// Me answers the caller.
func Me(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var u models.User
		if err := db.First(&u, c.GetUint("uid")).Error; err != nil {
			detail(c, http.StatusUnauthorized, "Not authenticated")
			return
		}
		c.JSON(http.StatusOK, u)
	}
}

type ticketBody struct {
	Title string `json:"title" binding:"required,max=80"`
}

// CreateTicket opens a ticket for the caller on queue qid.
func CreateTicket(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		uid := c.GetUint("uid")
		qid, err := strconv.Atoi(c.Param("qid"))
		if err != nil {
			detail(c, http.StatusBadRequest, "bad id")
			return
		}
		var q models.Queue
		if err := db.First(&q, qid).Error; err != nil {
			detail(c, http.StatusNotFound, "Not found")
			return
		}
		var body ticketBody
		if err := c.ShouldBindJSON(&body); err != nil {
			detail(c, http.StatusUnprocessableEntity, "invalid body")
			return
		}
		openMu.Lock()
		defer openMu.Unlock()
		var open int64
		db.Model(&models.Ticket{}).Where("queue_id = ? AND reporter_id = ? AND status = ?", qid, uid, "open").Count(&open)
		if open > 0 {
			detail(c, http.StatusConflict, "already open")
			return
		}
		t := models.Ticket{QueueID: q.ID, ReporterID: uid, Title: body.Title, Status: "open"}
		if err := db.Create(&t).Error; err != nil {
			detail(c, http.StatusInternalServerError, err.Error())
			return
		}
		c.JSON(http.StatusCreated, t)
	}
}

type ticketQuery struct {
	PageSize int    `form:"page_size" binding:"omitempty,min=1,max=100"`
	Status   string `form:"status" binding:"omitempty,oneof=open closed"`
}

// ListTickets answers the caller's tickets.
func ListTickets(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var q ticketQuery
		if err := c.ShouldBindQuery(&q); err != nil {
			detail(c, http.StatusUnprocessableEntity, "invalid query")
			return
		}
		limit := q.PageSize
		if limit == 0 {
			limit = 100
		}
		tx := db.Where("reporter_id = ?", c.GetUint("uid"))
		if q.Status != "" {
			tx = tx.Where("status = ?", q.Status)
		}
		tickets := []models.Ticket{}
		tx.Order("id").Limit(limit).Find(&tickets)
		c.JSON(http.StatusOK, tickets)
	}
}

// GetTicket answers one of the caller's tickets.
func GetTicket(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		tid, err := strconv.Atoi(c.Param("tid"))
		if err != nil {
			detail(c, http.StatusBadRequest, "bad id")
			return
		}
		var t models.Ticket
		if err := db.Where("id = ? AND reporter_id = ?", tid, c.GetUint("uid")).First(&t).Error; err != nil {
			detail(c, http.StatusNotFound, "Not found")
			return
		}
		c.JSON(http.StatusOK, t)
	}
}

// CloseTicket closes one of the caller's open tickets.
func CloseTicket(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		tid, err := strconv.Atoi(c.Param("tid"))
		if err != nil {
			detail(c, http.StatusBadRequest, "bad id")
			return
		}
		var t models.Ticket
		if err := db.Where("id = ? AND reporter_id = ?", tid, c.GetUint("uid")).First(&t).Error; err != nil {
			detail(c, http.StatusNotFound, "Not found")
			return
		}
		if t.Status != "open" {
			detail(c, http.StatusConflict, "not open")
			return
		}
		if err := db.Model(&t).Update("status", "closed").Error; err != nil {
			detail(c, http.StatusInternalServerError, err.Error())
			return
		}
		c.JSON(http.StatusOK, t)
	}
}

// ReopenTicket reopens one of the caller's closed tickets.
func ReopenTicket(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		tid, err := strconv.Atoi(c.Param("tid"))
		if err != nil {
			detail(c, http.StatusBadRequest, "bad id")
			return
		}
		var t models.Ticket
		if err := db.Where("id = ? AND reporter_id = ?", tid, c.GetUint("uid")).First(&t).Error; err != nil {
			detail(c, http.StatusNotFound, "Not found")
			return
		}
		if t.Status != "closed" {
			detail(c, http.StatusConflict, "not closed")
			return
		}
		if err := db.Model(&t).Update("status", "open").Error; err != nil {
			detail(c, http.StatusInternalServerError, err.Error())
			return
		}
		c.JSON(http.StatusOK, t)
	}
}

type handoffBody struct {
	ToUserID uint `json:"to_user_id" binding:"required,min=1"`
}

// CreateHandoff hands one of the caller's tickets to another user.
func CreateHandoff(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		uid := c.GetUint("uid")
		tid, err := strconv.Atoi(c.Param("tid"))
		if err != nil {
			detail(c, http.StatusBadRequest, "bad id")
			return
		}
		var t models.Ticket
		if err := db.Where("id = ? AND reporter_id = ?", tid, uid).First(&t).Error; err != nil {
			detail(c, http.StatusNotFound, "Not found")
			return
		}
		var body handoffBody
		if err := c.ShouldBindJSON(&body); err != nil {
			detail(c, http.StatusUnprocessableEntity, "invalid body")
			return
		}
		var to models.User
		if err := db.First(&to, body.ToUserID).Error; err != nil {
			detail(c, http.StatusNotFound, "No such user")
			return
		}
		h := models.Handoff{TicketID: t.ID, FromUserID: uid, ToUserID: to.ID}
		if err := db.Create(&h).Error; err != nil {
			detail(c, http.StatusInternalServerError, err.Error())
			return
		}
		c.JSON(http.StatusCreated, h)
	}
}

// ListHandoffs answers the handoffs from or to the caller.
func ListHandoffs(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		uid := c.GetUint("uid")
		handoffs := []models.Handoff{}
		db.Where("from_user_id = ? OR to_user_id = ?", uid, uid).Order("id").Find(&handoffs)
		c.JSON(http.StatusOK, handoffs)
	}
}
