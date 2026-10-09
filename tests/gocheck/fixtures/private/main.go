package main

import (
	"encoding/json"
	"net/http"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

type User struct {
	ID           uint   `json:"id"`
	Name         string `json:"name"`
	PasswordHash string `json:"-"`
	APIToken     string `json:"api_token"`
}

type Account struct {
	ID     uint   `json:"id"`
	Secret string `json:"-"`
}

type Session struct {
	ID    uint
	Token string
}

func (s Session) MarshalJSON() ([]byte, error) {
	return json.Marshal(map[string]any{"id": s.ID})
}

type Key struct {
	ID     uint `json:"id"`
	secret string
}

type UserView struct {
	User
	Extra string `json:"extra"`
}

type PublicUser struct {
	ID   uint   `json:"id"`
	Name string `json:"name"`
}

type App struct {
	db *gorm.DB
}

func RequireAdmin() gin.HandlerFunc {
	return func(c *gin.Context) {
		if !c.GetBool("is_admin") {
			c.AbortWithStatus(http.StatusForbidden)
		}
	}
}

func (a *App) routes(r *gin.Engine) {
	r.GET("/users/:id", a.BadUser)
	r.GET("/users", a.BadUsers)
	r.GET("/users/:id/wrapped", a.BadWrapped)
	r.GET("/accounts/:id/secret", a.BadSecretValue)
	r.GET("/users/:id/view", a.BadEmbedded)
	r.GET("/users/:id/helper", a.BadHelper)
	r.GET("/accounts/:id/map", a.BadMapVar)
	r.GET("/accounts/:id", a.OkAccount)
	r.GET("/users/:id/fields", a.OkFields)
	r.GET("/users/:id/public", a.OkPublic)
	r.GET("/admin/users", a.OkAdminSpec)
	admin := r.Group("/staff", RequireAdmin())
	admin.GET("/users", a.OkAdminGroup)
	r.GET("/sessions/:id", a.OkMarshaler)
	r.GET("/keys/:id", a.OkUnexported)
}

func (a *App) BadUser(c *gin.Context) {
	var u User
	a.db.First(&u, c.Param("id"))
	c.JSON(http.StatusOK, u)
}

func (a *App) BadUsers(c *gin.Context) {
	var users []User
	a.db.Find(&users)
	c.JSON(http.StatusOK, users)
}

func (a *App) BadWrapped(c *gin.Context) {
	var u User
	a.db.First(&u, c.Param("id"))
	c.JSON(http.StatusOK, gin.H{"user": u})
}

func (a *App) BadSecretValue(c *gin.Context) {
	var acc Account
	a.db.First(&acc, c.Param("id"))
	c.JSON(http.StatusOK, gin.H{"id": acc.ID, "secret": acc.Secret})
}

func (a *App) BadEmbedded(c *gin.Context) {
	var u User
	a.db.First(&u, c.Param("id"))
	c.JSON(http.StatusOK, UserView{User: u, Extra: "x"})
}

func toView(u User) gin.H {
	return gin.H{"name": u.Name, "key": u.APIToken}
}

func (a *App) BadHelper(c *gin.Context) {
	var u User
	a.db.First(&u, c.Param("id"))
	c.JSON(http.StatusOK, toView(u))
}

func (a *App) BadMapVar(c *gin.Context) {
	var acc Account
	a.db.First(&acc, c.Param("id"))
	resp := gin.H{"id": acc.ID}
	resp["secret"] = acc.Secret
	c.JSON(http.StatusOK, resp)
}

func (a *App) OkAccount(c *gin.Context) {
	var acc Account
	a.db.First(&acc, c.Param("id"))
	c.JSON(http.StatusOK, acc)
}

func (a *App) OkFields(c *gin.Context) {
	var u User
	a.db.First(&u, c.Param("id"))
	c.JSON(http.StatusOK, gin.H{"id": u.ID, "name": u.Name})
}

func (a *App) OkPublic(c *gin.Context) {
	var u User
	a.db.First(&u, c.Param("id"))
	c.JSON(http.StatusOK, PublicUser{ID: u.ID, Name: u.Name})
}

func (a *App) OkAdminSpec(c *gin.Context) {
	var users []User
	a.db.Find(&users)
	c.JSON(http.StatusOK, users)
}

func (a *App) OkAdminGroup(c *gin.Context) {
	var users []User
	a.db.Find(&users)
	c.JSON(http.StatusOK, users)
}

func (a *App) OkMarshaler(c *gin.Context) {
	var s Session
	a.db.First(&s, c.Param("id"))
	c.JSON(http.StatusOK, s)
}

func (a *App) OkUnexported(c *gin.Context) {
	var k Key
	a.db.First(&k, c.Param("id"))
	c.JSON(http.StatusOK, k)
}

func main() {}
