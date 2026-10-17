// A Gin + GORM app that only imports gRPC as a client (a call out to another service).
// It registers no gRPC server: the front end for it stays Gin.
package main

import (
	"github.com/gin-gonic/gin"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

// Pet is a row of pets.
type Pet struct {
	ID   uint   `gorm:"primaryKey"`
	Name string `gorm:"size:50;not null"`
}

func main() {
	db, err := gorm.Open(sqlite.Open("pets.db"), &gorm.Config{})
	if err != nil {
		panic(err)
	}
	_ = db.AutoMigrate(&Pet{})
	conn, err := grpc.NewClient("127.0.0.1:50051", grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		panic(err)
	}
	defer conn.Close()
	r := gin.Default()
	r.GET("/pets", func(c *gin.Context) {
		var pets []Pet
		db.Find(&pets)
		c.JSON(200, pets)
	})
	_ = r.Run()
}
