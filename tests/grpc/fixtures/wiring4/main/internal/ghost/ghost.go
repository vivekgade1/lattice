package ghost

import (
	"context"

	w4 "example.com/w4main/gen/wiring4/v1"
	"google.golang.org/grpc"
)

type ghostDesk struct{ w4.UnimplementedGhostServer }

func (ghostDesk) Get(ctx context.Context, in *w4.Req) (*w4.Rep, error) {
	return &w4.Rep{Name: in.Name}, nil
}

func (ghostDesk) Put(ctx context.Context, in *w4.Req) (*w4.Rep, error) {
	return &w4.Rep{Name: in.Name}, nil
}

// ghost is an Installer nothing ever builds.
type ghost struct{}

func (ghost) Install(r grpc.ServiceRegistrar) { w4.RegisterGhostServer(r, ghostDesk{}) }
