// Package app is the composition root.
package app

import (
	"context"
	"errors"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"

	c6 "example.com/c6pass/gen/codes6/v1"
)

type passImpl struct{ c6.UnimplementedPassServer }

func find(name string) error {
	if name == "" {
		return newErr(codes.NotFound, "no "+name)
	}
	if name == "x" {
		return errors.New("db down")
	}
	return nil
}

func lookup(name string) error {
	if name == "" {
		return ErrMissing
	}
	return nil
}

func (*passImpl) Get(ctx context.Context, in *c6.Req) (*c6.Rep, error) {
	if err := find(in.Name); err != nil {
		return nil, keep(err)
	}
	return &c6.Rep{}, nil
}

func (*passImpl) Put(ctx context.Context, in *c6.Req) (*c6.Rep, error) {
	return nil, keep(errors.New("x"))
}

func (*passImpl) Del(ctx context.Context, in *c6.Req) (*c6.Rep, error) {
	return nil, toStatus(ErrBad)
}

func (*passImpl) Find(ctx context.Context, in *c6.Req) (*c6.Rep, error) {
	if err := lookup(in.Name); err != nil {
		return nil, toStatus(err)
	}
	return &c6.Rep{}, nil
}

func (*passImpl) Conv(ctx context.Context, in *c6.Req) (*c6.Rep, error) {
	return nil, conv(newErr(codes.Aborted, "again"))
}

func (*passImpl) Raw(ctx context.Context, in *c6.Req) (*c6.Rep, error) {
	return nil, echo(newErr(codes.DataLoss, "lost"))
}

// Build wires the server.
func Build() *grpc.Server {
	s := grpc.NewServer()
	c6.RegisterPassServer(s, &passImpl{})
	return s
}
