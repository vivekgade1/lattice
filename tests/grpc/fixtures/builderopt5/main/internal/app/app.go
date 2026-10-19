// Package app is the composition root.
package app

import (
	"context"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	b5 "example.com/b5main/gen/builderopt5/v1"
	"example.com/b5main/internal/srv"
)

type oneImpl struct{ b5.UnimplementedOneServer }

func (*oneImpl) Get(ctx context.Context, in *b5.Req) (*b5.Rep, error) {
	return nil, status.Error(codes.NotFound, "no one")
}

func (*oneImpl) Put(ctx context.Context, in *b5.Req) (*b5.Rep, error) {
	return &b5.Rep{Name: in.Name}, nil
}

type twoImpl struct{ b5.UnimplementedTwoServer }

func (*twoImpl) Get(ctx context.Context, in *b5.Req) (*b5.Rep, error) {
	return nil, status.Error(codes.InvalidArgument, "bad two")
}

func (*twoImpl) Put(ctx context.Context, in *b5.Req) (*b5.Rep, error) {
	return &b5.Rep{Name: in.Name}, nil
}

type parkedImpl struct{ b5.UnimplementedParkedServer }

func (*parkedImpl) Get(ctx context.Context, in *b5.Req) (*b5.Rep, error) {
	return &b5.Rep{Name: in.Name}, nil
}

func (*parkedImpl) Put(ctx context.Context, in *b5.Req) (*b5.Rep, error) {
	return &b5.Rep{Name: in.Name}, nil
}

// serviceOptions lists the services this process offers.
func serviceOptions() []srv.Option {
	return []srv.Option{
		srv.WithService(&b5.Two_ServiceDesc, &twoImpl{}),
		srv.WithParked(&b5.Parked_ServiceDesc, &parkedImpl{}),
	}
}

// Build assembles the server.
func Build() *grpc.Server {
	opts := append(serviceOptions(), srv.WithService(&b5.One_ServiceDesc, &oneImpl{}))
	return srv.New(opts...)
}
