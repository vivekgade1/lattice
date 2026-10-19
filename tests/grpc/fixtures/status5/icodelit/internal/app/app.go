// Package app is the composition root.
package app

import (
	"context"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	s5 "example.com/s5icodelit/gen/status5/v1"
)

type openImpl struct{ s5.UnimplementedOpenServer }

func (*openImpl) Hello(ctx context.Context, in *s5.Req) (*s5.Rep, error) {
	return &s5.Rep{Name: in.Name}, nil
}

type shutImpl struct{ s5.UnimplementedShutServer }

func (*shutImpl) Get(ctx context.Context, in *s5.Req) (*s5.Rep, error) {
	return nil, status.Error(codes.NotFound, "no such")
}

// Build assembles the server.
func Build() *grpc.Server {
	s := grpc.NewServer(grpc.UnaryInterceptor(gate))
	s5.RegisterOpenServer(s, &openImpl{})
	s5.RegisterShutServer(s, &shutImpl{})
	return s
}
