package regb

import (
	"context"

	"google.golang.org/grpc"

	w4 "example.com/w4main/gen/wiring4/v1"
	"example.com/w4main/internal/registry"
)

type regbDesk struct{ w4.UnimplementedRegBServer }

func (regbDesk) Get(ctx context.Context, in *w4.Req) (*w4.Rep, error) {
	return &w4.Rep{Name: in.Name}, nil
}

func (regbDesk) Put(ctx context.Context, in *w4.Req) (*w4.Rep, error) {
	return &w4.Rep{Name: in.Name}, nil
}

type adapter struct{}

func (adapter) Install(r grpc.ServiceRegistrar) { w4.RegisterRegBServer(r, regbDesk{}) }

func init() { registry.Offer("regb", adapter{}) }
