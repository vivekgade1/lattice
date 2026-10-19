// Package app assembles the server.
package app

import (
	"context"
	"os"

	"google.golang.org/grpc"

	d5 "example.com/d5namedyn/gen/descwrap5/v1"
)

// DynDesc copies the generated descriptor and renames it from the environment.
func DynDesc() *grpc.ServiceDesc {
	d := d5.Dyn_ServiceDesc
	d.ServiceName = os.Getenv("SVC_NAME")
	return &d
}

type plainImpl struct{ d5.UnimplementedPlainServer }

func (*plainImpl) Get(ctx context.Context, in *d5.Req) (*d5.Rep, error) {
	return &d5.Rep{Name: in.Name}, nil
}

func (*plainImpl) Put(ctx context.Context, in *d5.Req) (*d5.Rep, error) {
	return &d5.Rep{Name: in.Name}, nil
}

type dynImpl struct{ d5.UnimplementedDynServer }

func (*dynImpl) Get(ctx context.Context, in *d5.Req) (*d5.Rep, error) {
	return &d5.Rep{Name: in.Name}, nil
}

func (*dynImpl) Put(ctx context.Context, in *d5.Req) (*d5.Rep, error) {
	return &d5.Rep{Name: in.Name}, nil
}

// Build assembles the server.
func Build() *grpc.Server {
	s := grpc.NewServer()
	d5.RegisterPlainServer(s, &plainImpl{})
	s.RegisterService(DynDesc(), &dynImpl{})
	return s
}
