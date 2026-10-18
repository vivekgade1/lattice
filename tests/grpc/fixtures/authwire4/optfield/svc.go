package main

import (
	"context"

	aw "example.com/aw4optfield/gen/aw4/v1"
)

type deskSvc struct{ aw.UnimplementedDeskServer }

func (deskSvc) Get(ctx context.Context, in *aw.Req) (*aw.Rep, error) {
	return &aw.Rep{Name: in.Name}, nil
}

func (deskSvc) Put(ctx context.Context, in *aw.Req) (*aw.Rep, error) {
	return &aw.Rep{Name: in.Name}, nil
}
