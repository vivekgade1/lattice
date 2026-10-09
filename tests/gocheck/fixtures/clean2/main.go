package main

import (
	"example.com/clean2/routes"
	"github.com/gin-gonic/gin"
)

func main() {
	r := gin.Default()
	routes.Mount(r)
	_ = r.Run()
}
