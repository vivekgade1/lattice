package app

import (
	"context"
	"os"

	"google.golang.org/grpc"

	w4 "example.com/w4unres/gen/wiring4/v1"
)

type plainDesk struct{ w4.UnimplementedPlainServer }

func (plainDesk) Get(ctx context.Context, in *w4.Req) (*w4.Rep, error) {
	return &w4.Rep{Name: in.Name}, nil
}

func (plainDesk) Put(ctx context.Context, in *w4.Req) (*w4.Rep, error) {
	return &w4.Rep{Name: in.Name}, nil
}

type mysteryDesk struct{ w4.UnimplementedMysteryServer }

func (mysteryDesk) Get(ctx context.Context, in *w4.Req) (*w4.Rep, error) {
	return &w4.Rep{Name: in.Name}, nil
}

func (mysteryDesk) Put(ctx context.Context, in *w4.Req) (*w4.Rep, error) {
	return &w4.Rep{Name: in.Name}, nil
}

var descs = map[string]*grpc.ServiceDesc{
	"plain":   &w4.Plain_ServiceDesc,
	"mystery": &w4.Mystery_ServiceDesc,
}

func mountNamed(r grpc.ServiceRegistrar, name string, impl any) {
	r.RegisterService(descs[name], impl)
}

// Build assembles the server.
func Build() *grpc.Server {
	s := grpc.NewServer()
	w4.RegisterPlainServer(s, plainDesk{})
	mountNamed(s, os.Getenv("EXTRA"), mysteryDesk{})
	return s
}
