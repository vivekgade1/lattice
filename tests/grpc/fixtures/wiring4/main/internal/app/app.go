// Package app assembles the server.
package app

import (
	"context"

	"google.golang.org/grpc"

	w4 "example.com/w4main/gen/wiring4/v1"
	"example.com/w4main/internal/genx"
	_ "example.com/w4main/internal/ghost"
	_ "example.com/w4main/internal/regb"
	"example.com/w4main/internal/registry"
	_ "example.com/w4main/internal/regsvc"
)

// expose puts any one service on r.
func expose[T any](r grpc.ServiceRegistrar, desc *grpc.ServiceDesc, impl T) {
	r.RegisterService(desc, impl)
}

type twinDesk struct{ w4.UnimplementedTwinServer }

func (twinDesk) Get(ctx context.Context, in *w4.Req) (*w4.Rep, error) {
	return &w4.Rep{Name: in.Name}, nil
}

func (twinDesk) Put(ctx context.Context, in *w4.Req) (*w4.Rep, error) {
	return &w4.Rep{Name: in.Name}, nil
}

func newTwin() w4.TwinServer { return twinDesk{} }

// Build assembles the server.
func Build() *grpc.Server {
	s := grpc.NewServer()
	expose(s, genx.Descriptor, genx.New(genx.WithLimit(3)))
	expose(s, &w4.Twin_ServiceDesc, newTwin())
	registry.InstallAll(s)
	return s
}
