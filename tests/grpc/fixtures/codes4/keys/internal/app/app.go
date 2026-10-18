package app

import (
	"google.golang.org/grpc"

	c4 "example.com/c4keys/gen/codes4/v1"
	"example.com/c4keys/internal/svc"
)

// Build assembles the server.
func Build() *grpc.Server {
	s := grpc.NewServer()
	c4.RegisterKcServer(s, svc.Kc{})
	c4.RegisterKdServer(s, svc.Kd{})
	return s
}

var _ = c4.Req{}
