// Package app is the composition root.
package app

import (
	"context"
	"os"

	"google.golang.org/grpc"

	b5 "example.com/b5unres/gen/builderopt5/v1"
	"example.com/b5unres/internal/srv"
)

var descs = map[string]*grpc.ServiceDesc{
	"fine": &b5.Fine_ServiceDesc,
	"dyn":  &b5.Dyn_ServiceDesc,
}

type fineImpl struct{ b5.UnimplementedFineServer }

func (*fineImpl) Get(ctx context.Context, in *b5.Req) (*b5.Rep, error) {
	return &b5.Rep{Name: in.Name}, nil
}

func (*fineImpl) Put(ctx context.Context, in *b5.Req) (*b5.Rep, error) {
	return &b5.Rep{Name: in.Name}, nil
}

type dynImpl struct{ b5.UnimplementedDynServer }

func (*dynImpl) Get(ctx context.Context, in *b5.Req) (*b5.Rep, error) {
	return &b5.Rep{Name: in.Name}, nil
}

func (*dynImpl) Put(ctx context.Context, in *b5.Req) (*b5.Rep, error) {
	return &b5.Rep{Name: in.Name}, nil
}

// Build assembles the server.
func Build() *grpc.Server {
	return srv.New(
		srv.WithService(&b5.Fine_ServiceDesc, &fineImpl{}),
		srv.WithService(descs[os.Getenv("SERVICE")], &dynImpl{}),
	)
}
