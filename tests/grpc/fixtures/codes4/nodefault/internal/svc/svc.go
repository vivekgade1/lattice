package svc

import (
	"context"
	"fmt"

	c4 "example.com/c4nodefault/gen/codes4/v1"
	"example.com/c4nodefault/internal/fault"
)

// Nd answers c4 Nd.
type Nd struct{ c4.UnimplementedNdServer }

func (Nd) Get(ctx context.Context, in *c4.Req) (*c4.Rep, error) {
	return nil, fmt.Errorf("get: %w", fault.ErrOne)
}

func (Nd) Put(ctx context.Context, in *c4.Req) (*c4.Rep, error) {
	return nil, fault.ErrTwo
}
