package app

import (
	"google.golang.org/grpc"

	c4 "example.com/c4nodefault/gen/codes4/v1"
	"example.com/c4nodefault/internal/fault"
	"example.com/c4nodefault/internal/svc"
)

// Build assembles the server.
func Build() *grpc.Server {
	s := grpc.NewServer(grpc.UnaryInterceptor(fault.Translate))
	c4.RegisterNdServer(s, svc.Nd{})
	return s
}

var _ = c4.Req{}
