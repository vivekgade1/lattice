// Package app is the composition root.
package app

import (
	"context"

	"google.golang.org/grpc"

	a5 "example.com/a5noskip/gen/audience5/v1"
	"example.com/a5noskip/internal/authn"
)

type impl struct{ a5.UnimplementedVaultServer }

func (*impl) Open(ctx context.Context, in *a5.Req) (*a5.Rep, error) {
	return &a5.Rep{Name: in.Name}, nil
}

func (*impl) Shut(ctx context.Context, in *a5.Req) (*a5.Rep, error) {
	return &a5.Rep{Name: in.Name}, nil
}

// Build assembles the server.
func Build() *grpc.Server {
	s := grpc.NewServer(grpc.UnaryInterceptor(authn.NewGate().Unary()))
	a5.RegisterVaultServer(s, &impl{})
	return s
}
