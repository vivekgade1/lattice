// Package genx serves wiring4.v1.Gen.
package genx

import (
	"context"

	w4 "example.com/w4main/gen/wiring4/v1"
)

// Descriptor is the service this package implements.
var Descriptor = &w4.Gen_ServiceDesc

// Option tunes the service.
type Option func(*service)

// WithLimit caps the answers.
func WithLimit(n int) Option { return func(s *service) { s.limit = n } }

type service struct {
	w4.UnimplementedGenServer
	limit int
}

// New answers the Gen service.
func New(opts ...Option) w4.GenServer {
	s := &service{limit: 10}
	for _, o := range opts {
		o(s)
	}
	return s
}

func (s *service) Get(ctx context.Context, in *w4.Req) (*w4.Rep, error) {
	return &w4.Rep{Name: in.Name}, nil
}
