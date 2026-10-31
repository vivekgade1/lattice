// Package app is the composition root.
package app

import (
	"context"

	"google.golang.org/grpc"

	c6 "example.com/c6as/gen/codes6/v1"
)

type billImpl struct{ c6.UnimplementedBillServer }

func find(name string) error {
	if name == "" {
		return fault(NoInvoice, "no invoice %s", name)
	}
	return nil
}

func (*billImpl) Get(ctx context.Context, in *c6.Req) (*c6.Rep, error) {
	if err := find(in.Name); err != nil {
		return nil, err
	}
	return nil, fault(NotYours, "not yours")
}

func (*billImpl) Pay(ctx context.Context, in *c6.Req) (*c6.Rep, error) {
	return nil, &Fault{Kind: TooLarge, Detail: "too large"}
}

type otherImpl struct{ c6.UnimplementedOtherServer }

func (*otherImpl) Get(ctx context.Context, in *c6.Req) (*c6.Rep, error) {
	return nil, fault(NotYours, "not yours")
}

// Build wires the server behind the fault interceptor.
func Build() *grpc.Server {
	s := grpc.NewServer(grpc.ChainUnaryInterceptor(faultInterceptor))
	c6.RegisterBillServer(s, &billImpl{})
	c6.RegisterOtherServer(s, &otherImpl{})
	return s
}
