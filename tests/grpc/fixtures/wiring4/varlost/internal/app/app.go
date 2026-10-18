package app

import (
	"context"

	"google.golang.org/grpc"

	w4 "example.com/w4varlost/gen/wiring4/v1"
)

type liveDesk struct{ w4.UnimplementedLiveServer }

func (liveDesk) Get(ctx context.Context, in *w4.Req) (*w4.Rep, error) {
	return &w4.Rep{Name: in.Name}, nil
}

func (liveDesk) Put(ctx context.Context, in *w4.Req) (*w4.Rep, error) {
	return &w4.Rep{Name: in.Name}, nil
}

type heldDesk struct{ w4.UnimplementedHeldServer }

func (heldDesk) Get(ctx context.Context, in *w4.Req) (*w4.Rep, error) {
	return &w4.Rep{Name: in.Name}, nil
}

func (heldDesk) Put(ctx context.Context, in *w4.Req) (*w4.Rep, error) {
	return &w4.Rep{Name: in.Name}, nil
}

// holder registers Held; only unreachedMount reads it.
var holder = func(r grpc.ServiceRegistrar) { w4.RegisterHeldServer(r, heldDesk{}) }

func unreachedMount(r grpc.ServiceRegistrar) { holder(r) }

// Build assembles the server.
func Build() *grpc.Server {
	s := grpc.NewServer()
	w4.RegisterLiveServer(s, liveDesk{})
	return s
}
