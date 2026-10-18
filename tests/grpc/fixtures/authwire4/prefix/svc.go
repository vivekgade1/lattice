package main

import (
	"context"

	aw "example.com/aw4prefix/gen/aw4/v1"
)

type openSvc struct{ aw.UnimplementedOpenServer }

func (openSvc) Get(ctx context.Context, in *aw.Req) (*aw.Rep, error) {
	return &aw.Rep{Name: in.Name}, nil
}

func (openSvc) Put(ctx context.Context, in *aw.Req) (*aw.Rep, error) {
	return &aw.Rep{Name: in.Name}, nil
}

type shutSvc struct{ aw.UnimplementedShutServer }

func (shutSvc) Get(ctx context.Context, in *aw.Req) (*aw.Rep, error) {
	return &aw.Rep{Name: in.Name}, nil
}

func (shutSvc) Put(ctx context.Context, in *aw.Req) (*aw.Rep, error) {
	return &aw.Rep{Name: in.Name}, nil
}
