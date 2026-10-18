package svc

import (
	"context"
	"fmt"

	c4 "example.com/c4tbl/gen/codes4/v1"
	"example.com/c4tbl/internal/fault"
)

// Ta answers c4 Ta.
type Ta struct{ c4.UnimplementedTaServer }

func (Ta) Get(ctx context.Context, in *c4.Req) (*c4.Rep, error) {
	if in.Name == "" {
		return nil, fault.ErrNotFound
	}
	return &c4.Rep{Name: in.Name}, nil
}

func (Ta) Put(ctx context.Context, in *c4.Req) (*c4.Rep, error) {
	if in.Name == "x" {
		return nil, fmt.Errorf("put %q: %w", in.Name, fault.ErrDup)
	}
	return &c4.Rep{Name: in.Name}, nil
}

// Tb answers c4 Tb.
type Tb struct{ c4.UnimplementedTbServer }

func (Tb) Get(ctx context.Context, in *c4.Req) (*c4.Rep, error) {
	return nil, fmt.Errorf("get: %w", fault.ErrBusy)
}

func (Tb) Put(ctx context.Context, in *c4.Req) (*c4.Rep, error) {
	return nil, fault.ErrStale
}
