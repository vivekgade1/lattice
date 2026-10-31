// Package app is the composition root.
package app

import (
	"context"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	c6 "example.com/c6icodes/gen/codes6/v1"
)

type openImpl struct{ c6.UnimplementedOpenServer }

func (*openImpl) Hello(ctx context.Context, in *c6.Req) (*c6.Rep, error) {
	return &c6.Rep{Name: "hi"}, nil
}

type shopImpl struct{ c6.UnimplementedShopServer }

func (*shopImpl) Buy(ctx context.Context, in *c6.Req) (*c6.Rep, error) {
	return nil, status.Error(codes.NotFound, "no such item")
}

func (*shopImpl) Look(ctx context.Context, in *c6.Req) (*c6.Rep, error) {
	return &c6.Rep{}, nil
}

type tillImpl struct{ c6.UnimplementedTillServer }

func (*tillImpl) Pay(ctx context.Context, in *c6.Req) (*c6.Rep, error) {
	return nil, status.Error(codes.InvalidArgument, "bad amount")
}

// Build wires the server behind three interceptors.
func Build() *grpc.Server {
	s := grpc.NewServer(grpc.ChainUnaryInterceptor(versionGate, quotaGate, tillGate))
	c6.RegisterOpenServer(s, &openImpl{})
	c6.RegisterShopServer(s, &shopImpl{})
	c6.RegisterTillServer(s, &tillImpl{})
	return s
}
