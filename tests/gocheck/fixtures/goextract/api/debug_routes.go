//go:build ignore

package api

import "github.com/gin-gonic/gin"

// RegisterIgnored is in a file a build constraint drops.
func RegisterIgnored(r *gin.Engine) {
	r.GET("/ignored", Status)
}
