// Package app is the composition root.
package app

import (
	"context"

	"google.golang.org/grpc"

	c6 "example.com/c6map/gen/codes6/v1"
)

type mapImpl struct{ c6.UnimplementedMapServer }

func (*mapImpl) Get(ctx context.Context, in *c6.Req) (*c6.Rep, error) {
	return nil, fail("not_found")
}

func (*mapImpl) Put(ctx context.Context, in *c6.Req) (*c6.Rep, error) {
	if in.Name == "" {
		return nil, invalid("name")
	}
	return &c6.Rep{Name: in.Name}, nil
}

func (*mapImpl) Del(ctx context.Context, in *c6.Req) (*c6.Rep, error) {
	return nil, failField("taken", "name")
}

func (*mapImpl) Odd(ctx context.Context, in *c6.Req) (*c6.Rep, error) {
	return nil, fail("no_such_code")
}

func (*mapImpl) Dyn(ctx context.Context, in *c6.Req) (*c6.Rep, error) {
	return nil, fail(in.Name)
}

func (*mapImpl) Help(ctx context.Context, in *c6.Req) (*c6.Rep, error) {
	if err := load(in.Name); err != nil {
		return nil, err
	}
	return &c6.Rep{}, nil
}

func (*mapImpl) Two(ctx context.Context, in *c6.Req) (*c6.Rep, error) {
	if in.Name == "" {
		return nil, pick2(in.Name)
	}
	return nil, pick(in.Name)
}

func load(name string) error {
	if name == "" {
		return fail("busy")
	}
	return nil
}

// Build wires the server.
func Build() *grpc.Server {
	s := grpc.NewServer()
	c6.RegisterMapServer(s, &mapImpl{})
	return s
}
