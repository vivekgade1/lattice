package app

import (
	"google.golang.org/grpc"

	c4 "example.com/c4tbl/gen/codes4/v1"
	"example.com/c4tbl/internal/fault"
	"example.com/c4tbl/internal/svc"
)

// Build assembles the server.
func Build() *grpc.Server {
	s := grpc.NewServer(grpc.UnaryInterceptor(fault.Translate))
	c4.RegisterTaServer(s, svc.Ta{})
	c4.RegisterTbServer(s, svc.Tb{})
	return s
}

var _ = c4.Req{}
