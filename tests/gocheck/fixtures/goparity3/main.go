// goparity3: a parse-only Gin + GORM module for the Go parity gaps, round 3 (Spike J, node
// G3, B1). It carries one case per decision K1, K2, K3 and K5 and nothing else; it is never
// built or run. The truth of this fixture is truth.json, fixed before any round-3 library code.
//
//	K1  /w1/:id   Handle(h.getW1)         one-level wrapper, reads a Widget unscoped   -> GO801
//	    /w2/:id   Handle(h.getW2)         the same, scoped by owner_id                  -> silent
//	    /w3/:id   Handle(Wrap2(h.getW3))  two-level wrapper, unscoped                   -> silent
//	    /w4/:id   HandleMsg(h.getW4)      a wrapper that calls err.Error() on the error its
//	                                      parameter returns (an uninferable receiver)   -> silent
//	    /m1/w/:id behind HMACGuard(...)   a guard returned through the wrapper; it signs
//	                                      with hmac.New(...).Write/Sum; unscoped read    -> GO801
//	K2  /m2/w/:id behind ExtGuard(v)      a guard that hands the *gin.Context to a method
//	                                      of an outside type; unscoped read             -> silent
//	K3  POST /widgets binds widgetBody (title: required, max=40)
//	K5  Gadget.Label gets one constant ("x"); Gadget.Kind gets two ("a", "b")
package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"errors"
	"net/http"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"

	ext "github.com/acme/guard"
)

// Widget belongs to the caller that wrote it (OwnerID).
type Widget struct {
	ID      uint   `gorm:"primaryKey" json:"id"`
	OwnerID uint   `gorm:"not null;index" json:"owner_id"`
	Title   string `gorm:"size:40" json:"title"`
}

// Gadget is nobody's: its two string columns are written with constants.
type Gadget struct {
	ID    uint   `gorm:"primaryKey" json:"id"`
	Label string `gorm:"size:20" json:"label"`
	Kind  string `gorm:"size:20" json:"kind"`
}

type widgetBody struct {
	Title string `json:"title" binding:"required,max=40"`
}

type handler struct{ db *gorm.DB }

func main() {
	db, err := gorm.Open(nil, &gorm.Config{})
	if err != nil {
		panic(err)
	}
	if err := db.AutoMigrate(&Widget{}, &Gadget{}); err != nil {
		panic(err)
	}
	h := handler{db: db}
	r := gin.New()
	r.GET("/w1/:id", Handle(h.getW1))
	r.GET("/w2/:id", Handle(h.getW2))
	r.GET("/w3/:id", Handle(Wrap2(h.getW3)))
	r.GET("/w4/:id", HandleMsg(h.getW4))
	g1 := r.Group("/m1", HMACGuard([]byte("secret")))
	g1.GET("/w/:id", GetWidgetM1(db))
	g2 := r.Group("/m2", ExtGuard(ext.NewVerifier()))
	g2.GET("/w/:id", GetWidgetM2(db))
	r.POST("/widgets", CreateWidget(db))
	r.POST("/gadgets", CreateGadget(db))
	r.POST("/gadgets/:id/kind", SetGadgetKind(db))
	_ = r.Run()
}

// Handle adapts a handler that returns its refusal as an error (a one-level wrapper).
func Handle(fn func(c *gin.Context) error) gin.HandlerFunc {
	return func(c *gin.Context) {
		if err := fn(c); err != nil {
			refuse(c, err)
		}
	}
}

// HandleMsg is Handle with the refusal written inline: it calls a method of the error its
// parameter returns, a receiver whose type the checker cannot infer.
func HandleMsg(fn func(c *gin.Context) error) gin.HandlerFunc {
	return func(c *gin.Context) {
		if err := fn(c); err != nil {
			c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{"detail": err.Error()})
		}
	}
}

func refuse(c *gin.Context, err error) {
	c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{"detail": err.Error()})
}

// Wrap2 is a second wrapper level: Handle(Wrap2(h.x)) binds two levels deep.
func Wrap2(fn func(c *gin.Context) error) func(c *gin.Context) error {
	return func(c *gin.Context) error { return fn(c) }
}

// getW1 reads any widget by id: unscoped, on purpose.
func (h handler) getW1(c *gin.Context) error {
	var w Widget
	if err := h.db.First(&w, c.Param("id")).Error; err != nil {
		return err
	}
	c.JSON(http.StatusOK, w)
	return nil
}

// getW2 reads the caller's widget: scoped by owner_id.
func (h handler) getW2(c *gin.Context) error {
	var w Widget
	if err := h.db.Where("id = ? AND owner_id = ?", c.Param("id"), c.GetUint("uid")).First(&w).Error; err != nil {
		return err
	}
	c.JSON(http.StatusOK, w)
	return nil
}

// getW3 reads any widget by id: unscoped, behind two wrapper levels.
func (h handler) getW3(c *gin.Context) error {
	var w Widget
	if err := h.db.First(&w, c.Param("id")).Error; err != nil {
		return err
	}
	c.JSON(http.StatusOK, w)
	return nil
}

// getW4 reads any widget by id: unscoped, behind HandleMsg.
func (h handler) getW4(c *gin.Context) error {
	var w Widget
	if err := h.db.First(&w, c.Param("id")).Error; err != nil {
		return err
	}
	c.JSON(http.StatusOK, w)
	return nil
}

// HMACGuard verifies a signed header. It signs with an outside type (hash.Hash from
// hmac.New) and returns its guard through the wrapper.
func HMACGuard(secret []byte) gin.HandlerFunc {
	return Handle(func(c *gin.Context) error {
		mac := hmac.New(sha256.New, secret)
		mac.Write([]byte(c.GetHeader("X-User")))
		if !hmac.Equal([]byte(c.GetHeader("X-Sig")), mac.Sum(nil)) {
			return errors.New("bad signature")
		}
		return nil
	})
}

// ExtGuard hands the request to a method of an outside type, which may check anything.
func ExtGuard(v *ext.Verifier) gin.HandlerFunc {
	return func(c *gin.Context) {
		if !v.Check(c) {
			c.AbortWithStatus(http.StatusUnauthorized)
		}
	}
}

// GetWidgetM1 reads any widget by id, behind HMACGuard.
func GetWidgetM1(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var w Widget
		if err := db.First(&w, c.Param("id")).Error; err != nil {
			c.JSON(http.StatusNotFound, gin.H{"detail": "not found"})
			return
		}
		c.JSON(http.StatusOK, w)
	}
}

// GetWidgetM2 reads any widget by id, behind ExtGuard.
func GetWidgetM2(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var w Widget
		if err := db.First(&w, c.Param("id")).Error; err != nil {
			c.JSON(http.StatusNotFound, gin.H{"detail": "not found"})
			return
		}
		c.JSON(http.StatusOK, w)
	}
}

// CreateWidget binds a named body struct (title: required, at most 40) and creates the
// caller's widget.
func CreateWidget(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var body widgetBody
		if err := c.ShouldBindJSON(&body); err != nil {
			c.JSON(http.StatusUnprocessableEntity, gin.H{"detail": "invalid body"})
			return
		}
		w := Widget{OwnerID: c.GetUint("uid"), Title: body.Title}
		if err := db.Create(&w).Error; err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"detail": "error"})
			return
		}
		c.JSON(http.StatusCreated, w)
	}
}

// CreateGadget writes one constant to Label ("x") and one to Kind ("a").
func CreateGadget(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		g := Gadget{Label: "x", Kind: "a"}
		if err := db.Create(&g).Error; err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"detail": "error"})
			return
		}
		c.JSON(http.StatusCreated, g)
	}
}

// SetGadgetKind writes the second constant to Kind ("b").
func SetGadgetKind(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		if err := db.Model(&Gadget{}).Where("id = ?", c.Param("id")).Update("kind", "b").Error; err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"detail": "error"})
			return
		}
		c.Status(http.StatusNoContent)
	}
}
