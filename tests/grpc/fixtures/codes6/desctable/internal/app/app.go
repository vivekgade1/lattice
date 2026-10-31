// Package app is the composition root.
package app

import (
	"context"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	c6 "example.com/c6desc/gen/codes6/v1"
)

type billImpl struct{ c6.UnimplementedBillServer }

func (*billImpl) Charge(ctx context.Context, in *c6.Req) (*c6.Rep, error) {
	return nil, status.Error(codes.NotFound, "no account")
}

type tabImpl struct{ c6.UnimplementedTabServer }

func newTab() *tabImpl { return &tabImpl{} }

func (*tabImpl) Open(ctx context.Context, in *c6.Req) (*c6.Rep, error) {
	return nil, status.Error(codes.FailedPrecondition, "tab closed")
}

// binding pairs a generated service descriptor with its implementation.
type binding struct {
	desc *grpc.ServiceDesc
	impl any
}

// bindings lists the services attached through their descriptors (Never is deliberately absent).
func bindings() []binding {
	return []binding{
		{desc: &c6.Bill_ServiceDesc, impl: &billImpl{}},
	}
}

// Build registers every bound service.
func Build() *grpc.Server {
	s := grpc.NewServer()
	for _, b := range bindings() {
		s.RegisterService(b.desc, b.impl)
	}
	for _, b := range []binding{{&c6.Tab_ServiceDesc, newTab()}} {
		s.RegisterService(b.desc, b.impl)
	}
	return s
}
