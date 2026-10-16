package api

import (
	"errors"
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"

	"example.com/gx/models"
)

// ErrGone is mapped to 410 by the error writer.
var ErrGone = errors.New("gone")

// Mount registers every route of the app.
func Mount(r *gin.Engine, db *gorm.DB) {
	r.POST("/api/login", Login(db))
	r.GET("/api/status", Status)

	auth := r.Group("/api", RequireUser(db))
	auth.GET("/items", ListItems(db))
	auth.POST("/items", CreateItem(db))
	auth.GET("/items/:id", GetItem(db))
	auth.GET("/stats", Handle(stats(db)))
	auth.DELETE("/items/:id", Handle(removeItem(db)))

	admin := auth.Group("/admin", RequireAdmin())
	admin.GET("/audit", AuditLog(db))

	tables := []struct {
		Path string
		H    gin.HandlerFunc
	}{{"/api/a", Status}, {"/api/b", Status}}
	for _, t := range tables {
		r.GET(t.Path, t.H)
	}
}

// RegisterOrphan is never called: its routes are not wired.
func RegisterOrphan(r *gin.Engine) {
	r.GET("/orphan", Status)
}

// Status is a plain handler.
func Status(c *gin.Context) { c.JSON(http.StatusOK, gin.H{"status": "ok"}) }

type loginReq struct {
	Email    string `json:"email" binding:"required"`
	Password string `json:"password" binding:"required,min=8,max=72"`
}

// Login is a handler factory: the literal it returns is the handler.
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
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"code": "unknown_user"})
			return
		}
		c.Set("userID", u.ID)
		c.Next()
	}
}

// RequireAdmin refuses non-admins with 403.
func RequireAdmin() gin.HandlerFunc {
	return func(c *gin.Context) {
		if v, _ := c.Get("isAdmin"); v != true {
			c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"code": "admin_only"})
			return
		}
		c.Next()
	}
}

type createItem struct {
	Name  string  `json:"name" binding:"required,min=1,max=100"`
	Price float64 `json:"price" binding:"gte=0,lte=1000"`
	State string  `json:"state" binding:"oneof=draft live"`
}

// CreateItem binds a struct and stamps the caller as the owner.
func CreateItem(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		uid := c.GetUint("userID")
		var in createItem
		if err := c.ShouldBindJSON(&in); err != nil {
			c.JSON(http.StatusUnprocessableEntity, gin.H{"code": "invalid"})
			return
		}
		item := models.Item{OwnerID: uid, Name: in.Name, Price: in.Price, State: "draft"}
		db.Create(&item)
		db.Create(&models.Audit{Action: "create", Actor: uid})
		c.JSON(http.StatusCreated, item)
	}
}

// ListItems filters on the caller.
func ListItems(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var items []models.Item
		db.Where("owner_id = ?", c.GetUint("userID")).Find(&items)
		c.JSON(http.StatusOK, items)
	}
}

// GetItem reads one of the caller's items; the id is a path parameter.
func GetItem(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		id, err := strconv.Atoi(c.Param("id"))
		if err != nil || id < 1 {
			c.JSON(http.StatusNotFound, gin.H{"code": "not_found"})
			return
		}
		var item models.Item
		if err := db.First(&item, "id = ? AND owner_id = ?", id, c.GetUint("userID")).Error; err != nil {
			c.JSON(http.StatusNotFound, gin.H{"code": "not_found"})
			return
		}
		c.JSON(http.StatusOK, item)
	}
}

// Handle is a generic wrapper: the handler is its argument.
func Handle[T any](fn func(c *gin.Context) (T, error)) gin.HandlerFunc {
	return func(c *gin.Context) {
		out, err := fn(c)
		if err != nil {
			write(c, err)
			return
		}
		c.JSON(http.StatusOK, out)
	}
}

func write(c *gin.Context, err error) {
	switch {
	case errors.Is(err, ErrGone):
		c.AbortWithStatusJSON(http.StatusGone, gin.H{"code": "gone"})
	default:
		c.AbortWithStatusJSON(http.StatusInternalServerError, gin.H{"code": "internal"})
	}
}

func stats(db *gorm.DB) func(c *gin.Context) (map[string]int, error) {
	return func(c *gin.Context) (map[string]int, error) {
		var n int64
		db.Model(&models.Item{}).Where("owner_id = ?", c.GetUint("userID")).Count(&n)
		return map[string]int{"items": int(n)}, nil
	}
}

func removeItem(db *gorm.DB) func(c *gin.Context) (string, error) {
	return func(c *gin.Context) (string, error) {
		var item models.Item
		if err := db.First(&item, c.Param("id")).Error; err != nil {
			return "", ErrGone
		}
		db.Delete(&item)
		return "deleted", nil
	}
}

// AuditLog is under the admin group.
func AuditLog(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var rows []models.Audit
		db.Find(&rows)
		c.JSON(http.StatusOK, rows)
	}
}
