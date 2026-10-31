// The bodybind fixture (node GD): request bodies read without a Gin bind method in the
// handler -- a module helper that decodes into its `any` parameter (c.GetRawData or
// io.ReadAll of c.Request.Body, then json.Unmarshal), json.NewDecoder over c.Request.Body,
// a helper that hands its parameter to c.ShouldBindJSON -- fields whose absence the code
// refuses, and a handler that authenticates itself (no middleware).
package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"strings"

	"example.com/bb/api"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

type User struct {
	ID       uint   `gorm:"primarykey"`
	Email    string `gorm:"uniqueIndex"`
	Password string
	Token    string `gorm:"uniqueIndex"`
}

type App struct {
	ID   uint   `gorm:"primarykey"`
	Name string `gorm:"uniqueIndex"`
}

type Note struct {
	ID   uint `gorm:"primarykey"`
	Text string
	Tag  string
}

type Server struct{ db *gorm.DB }

const defaults = `{"limit": 10}`

var errBad = "bad body"

func unprocessable(c *gin.Context, msg string) {
	c.AbortWithStatusJSON(http.StatusUnprocessableEntity, gin.H{"detail": msg})
}

// readJSON decodes the raw body into v.
func readJSON(c *gin.Context, v any) bool {
	raw, err := c.GetRawData()
	if err != nil {
		unprocessable(c, "unreadable body")
		return false
	}
	raw = bytes.TrimSpace(raw)
	if err := json.Unmarshal(raw, v); err != nil {
		unprocessable(c, "invalid JSON body")
		return false
	}
	return true
}

// decodeBody reads at most 1 MiB of the body into dst.
func decodeBody(c *gin.Context, dst any) bool {
	raw, err := io.ReadAll(io.LimitReader(c.Request.Body, 1<<20))
	if err != nil || json.Unmarshal(raw, dst) != nil {
		unprocessable(c, "invalid JSON body")
		return false
	}
	return true
}

// bindVia hands its parameter to the Gin binder.
func bindVia(c *gin.Context, v any) bool {
	if err := c.ShouldBindJSON(v); err != nil {
		unprocessable(c, "invalid body")
		return false
	}
	return true
}

func (s *Server) authMW() gin.HandlerFunc {
	return func(c *gin.Context) {
		h := c.GetHeader("Authorization")
		var u User
		if err := s.db.Where("token = ?", strings.TrimPrefix(h, "Bearer ")).First(&u).Error; err != nil {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"detail": "Not authenticated"})
			return
		}
		c.Set("user", u)
		c.Next()
	}
}

// authenticate is the per-handler check of an app with no auth middleware.
func (s *Server) authenticate(c *gin.Context) (*User, bool) {
	h := c.GetHeader("Authorization")
	if !strings.HasPrefix(h, "Bearer ") {
		c.JSON(http.StatusUnauthorized, gin.H{"detail": "Not authenticated"})
		return nil, false
	}
	var u User
	if err := s.db.Where("token = ?", strings.TrimPrefix(h, "Bearer ")).First(&u).Error; err != nil {
		c.JSON(http.StatusUnauthorized, gin.H{"detail": "Could not validate credentials"})
		return nil, false
	}
	return &u, true
}

func (s *Server) login(c *gin.Context) {
	var in struct {
		Email    *string `json:"email"`
		Password *string `json:"password"`
	}
	if !readJSON(c, &in) {
		return
	}
	if in.Email == nil || in.Password == nil {
		unprocessable(c, "email and password are required")
		return
	}
	var u User
	if err := s.db.Where("email = ?", *in.Email).First(&u).Error; err != nil || u.Password != *in.Password {
		c.JSON(http.StatusUnauthorized, gin.H{"detail": "bad login"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"access_token": u.Token})
}

func (s *Server) createApp(c *gin.Context) {
	var in struct {
		Name *string `json:"name"`
	}
	if !readJSON(c, &in) {
		return
	}
	if in.Name == nil || len(*in.Name) > 100 {
		unprocessable(c, "name must be 1-100 characters")
		return
	}
	app := App{Name: *in.Name}
	s.db.Create(&app)
	c.JSON(http.StatusCreated, app)
}

func (s *Server) createUser(c *gin.Context) {
	var in api.UserIn
	if !decodeBody(c, &in) {
		return
	}
	switch {
	case in.Email == "":
		unprocessable(c, "email is required")
		return
	case in.Nickname != nil && len(*in.Nickname) > 20:
		unprocessable(c, "nickname is too long")
		return
	}
	u := User{Email: in.Email}
	s.db.Create(&u)
	c.JSON(http.StatusCreated, u)
}

func (s *Server) createNote(c *gin.Context) {
	var in struct {
		Text string `json:"text"`
		Tag  string `json:"tag"`
	}
	if err := json.NewDecoder(c.Request.Body).Decode(&in); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"detail": "bad body"})
		return
	}
	if len(in.Text) < 1 {
		c.JSON(http.StatusUnprocessableEntity, gin.H{"detail": "text is required"})
		return
	}
	if in.Tag != "" && len(in.Tag) > 10 {
		c.JSON(http.StatusUnprocessableEntity, gin.H{"detail": "tag is too long"})
		return
	}
	n := Note{Text: in.Text, Tag: in.Tag}
	s.db.Create(&n)
	c.JSON(http.StatusCreated, n)
}

func (s *Server) rename(c *gin.Context) {
	var in struct {
		Name string `json:"name" binding:"required,max=100"`
	}
	if !bindVia(c, &in) {
		return
	}
	s.db.Model(&App{}).Where("id = ?", c.Param("id")).Update("name", in.Name)
	c.Status(http.StatusNoContent)
}

func (s *Server) config(c *gin.Context) {
	var cfg struct {
		Limit int `json:"limit"`
	}
	_ = json.Unmarshal([]byte(defaults), &cfg)
	c.JSON(http.StatusOK, cfg)
}

// addMember collects the missing fields and refuses them together; a check nested under
// another condition does not make its field required.
func (s *Server) addMember(c *gin.Context) {
	var in struct {
		Role *string `json:"role"`
		Note *string `json:"note"`
	}
	if !readJSON(c, &in) {
		return
	}
	var errs []string
	if in.Role == nil {
		errs = append(errs, "role")
	}
	if len(errs) > 0 {
		unprocessable(c, strings.Join(errs, ","))
		return
	}
	if in.Note != nil {
		if *in.Note == "" {
			unprocessable(c, "note is empty")
			return
		}
	}
	c.Status(http.StatusNoContent)
}

// createTag calls the same refusal twice with the same arguments; a conjunction does not make
// its field required.
func (s *Server) createTag(c *gin.Context) {
	var in struct {
		Label *string `json:"label"`
		Color *string `json:"color"`
	}
	if !readJSON(c, &in) {
		return
	}
	if in.Color != nil && *in.Color == "" {
		unprocessable(c, errBad)
		return
	}
	if in.Label == nil {
		unprocessable(c, errBad)
		return
	}
	c.Status(http.StatusCreated)
}

func (s *Server) self(c *gin.Context) {
	u, ok := s.authenticate(c)
	if !ok {
		return
	}
	c.JSON(http.StatusOK, u)
}

func (s *Server) ping(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"status": "ok"})
}

func main() {
	db, _ := gorm.Open(nil, &gorm.Config{})
	_ = db.AutoMigrate(&User{}, &App{}, &Note{})
	s := &Server{db: db}
	r := gin.New()
	r.POST("/login", s.login)
	r.GET("/self", s.self)
	r.GET("/ping", s.ping)
	r.GET("/config", s.config)
	g := r.Group("", s.authMW())
	g.POST("/apps", s.createApp)
	g.POST("/users", s.createUser)
	g.POST("/notes", s.createNote)
	g.PUT("/apps/:id", s.rename)
	g.POST("/members", s.addMember)
	g.POST("/tags", s.createTag)
	_ = r.Run()
}
