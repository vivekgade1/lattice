package main

import (
	"context"

	awv1 "example.com/authwire/gen/aw/v1"
)

type desk struct{ awv1.UnimplementedDeskServer }

func (d *desk) Ping(ctx context.Context, in *awv1.Req) (*awv1.Rep, error) {
	return &awv1.Rep{Name: in.Name}, nil
}

func (d *desk) Read(ctx context.Context, in *awv1.Req) (*awv1.Rep, error) {
	who, err := currentUser(ctx)
	if err != nil {
		return nil, err
	}
	return &awv1.Rep{Name: who}, nil
}
