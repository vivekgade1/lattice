package regsvc

import (
	"context"

	"google.golang.org/grpc"

	w4 "example.com/w4main/gen/wiring4/v1"
	"example.com/w4main/internal/registry"
)

type regDesk struct{ w4.UnimplementedRegServer }

func (regDesk) Get(ctx context.Context, in *w4.Req) (*w4.Rep, error) {
	return &w4.Rep{Name: in.Name}, nil
}

func (regDesk) Put(ctx context.Context, in *w4.Req) (*w4.Rep, error) {
	return &w4.Rep{Name: in.Name}, nil
}

func init() { registry.Offer("reg", registry.InstallFunc(install)) }

func install(r grpc.ServiceRegistrar) { w4.RegisterRegServer(r, regDesk{}) }
