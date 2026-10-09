package main

import (
	"os"

	"example.com/clean/internal/api"
	"example.com/clean/internal/models"
	"github.com/gin-gonic/gin"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

func main() {
	db, err := gorm.Open(sqlite.Open(os.Getenv("DB_PATH")), &gorm.Config{})
	if err != nil {
		panic(err)
	}
	_ = db.AutoMigrate(&models.Account{}, &models.Pipeline{}, &models.Run{})
	r := gin.Default()
	api.New(db).Routes(r)
	_ = r.Run(":" + os.Getenv("PORT"))
}
