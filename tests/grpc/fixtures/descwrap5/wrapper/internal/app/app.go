// Package app assembles the server.
package app

import (
	"context"

	"google.golang.org/grpc"

	d5 "example.com/d5wrapper/gen/descwrap5/v1"
	"example.com/d5wrapper/internal/dom"
)

type eqImpl struct{ d5.UnimplementedEqServer }

func (*eqImpl) Get(ctx context.Context, in *d5.Req) (*d5.Rep, error) {
	return nil, dom.ErrMissing
}

func (*eqImpl) Put(ctx context.Context, in *d5.Req) (*d5.Rep, error) {
	return nil, dom.ErrInvalid
}

type rawImpl struct{ d5.UnimplementedRawServer }

func (*rawImpl) Get(ctx context.Context, in *d5.Req) (*d5.Rep, error) {
	return nil, dom.ErrMissing
}

func (*rawImpl) Put(ctx context.Context, in *d5.Req) (*d5.Rep, error) {
	return &d5.Rep{Name: in.Name}, nil
}

// Build assembles the server.
func Build() *grpc.Server {
	s := grpc.NewServer()
	s.RegisterService(EqDesc(), &eqImpl{})
	d5.RegisterRawServer(s, &rawImpl{})
	return s
}
