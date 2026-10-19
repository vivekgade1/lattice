// Package app assembles the server.
package app

import (
	"context"

	"google.golang.org/grpc"

	d5 "example.com/d5methods/gen/descwrap5/v1"
)

// RngDesc rebuilds Methods by index, one element per original element.
func RngDesc() *grpc.ServiceDesc {
	src := d5.Rng_ServiceDesc
	out := src
	out.Methods = make([]grpc.MethodDesc, len(src.Methods))
	for i, m := range src.Methods {
		out.Methods[i] = grpc.MethodDesc{MethodName: m.MethodName, Handler: m.Handler}
	}
	return &out
}

// ApdDesc rebuilds Methods by append, one element per original element.
func ApdDesc() *grpc.ServiceDesc {
	d := d5.Apd_ServiceDesc
	d.Methods = nil
	for _, m := range d5.Apd_ServiceDesc.Methods {
		d.Methods = append(d.Methods, grpc.MethodDesc{MethodName: m.MethodName, Handler: m.Handler})
	}
	return &d
}

// LitDesc serves only Get: Methods is a literal of one method.
func LitDesc() *grpc.ServiceDesc {
	d := d5.Lit_ServiceDesc
	d.Methods = []grpc.MethodDesc{{MethodName: "Get", Handler: d5.Lit_ServiceDesc.Methods[0].Handler}}
	return &d
}

func keep(ms []grpc.MethodDesc, drop string) []grpc.MethodDesc {
	var out []grpc.MethodDesc
	for _, m := range ms {
		if m.MethodName != drop {
			out = append(out, m)
		}
	}
	return out
}

// OpaqueDesc filters Methods through a function: not a literal set.
func OpaqueDesc() *grpc.ServiceDesc {
	d := d5.Opaque_ServiceDesc
	d.Methods = keep(d.Methods, "Put")
	return &d
}

type rngImpl struct{ d5.UnimplementedRngServer }

func (*rngImpl) Get(ctx context.Context, in *d5.Req) (*d5.Rep, error) {
	return &d5.Rep{Name: in.Name}, nil
}

func (*rngImpl) Put(ctx context.Context, in *d5.Req) (*d5.Rep, error) {
	return &d5.Rep{Name: in.Name}, nil
}

type apdImpl struct{ d5.UnimplementedApdServer }

func (*apdImpl) Get(ctx context.Context, in *d5.Req) (*d5.Rep, error) {
	return &d5.Rep{Name: in.Name}, nil
}

func (*apdImpl) Put(ctx context.Context, in *d5.Req) (*d5.Rep, error) {
	return &d5.Rep{Name: in.Name}, nil
}

type litImpl struct{ d5.UnimplementedLitServer }

func (*litImpl) Get(ctx context.Context, in *d5.Req) (*d5.Rep, error) {
	return &d5.Rep{Name: in.Name}, nil
}

func (*litImpl) Put(ctx context.Context, in *d5.Req) (*d5.Rep, error) {
	return &d5.Rep{Name: in.Name}, nil
}

type opaqueImpl struct{ d5.UnimplementedOpaqueServer }

func (*opaqueImpl) Get(ctx context.Context, in *d5.Req) (*d5.Rep, error) {
	return &d5.Rep{Name: in.Name}, nil
}

func (*opaqueImpl) Put(ctx context.Context, in *d5.Req) (*d5.Rep, error) {
	return &d5.Rep{Name: in.Name}, nil
}

// Build assembles the server.
func Build() *grpc.Server {
	s := grpc.NewServer()
	s.RegisterService(RngDesc(), &rngImpl{})
	s.RegisterService(ApdDesc(), &apdImpl{})
	s.RegisterService(LitDesc(), &litImpl{})
	s.RegisterService(OpaqueDesc(), &opaqueImpl{})
	return s
}
