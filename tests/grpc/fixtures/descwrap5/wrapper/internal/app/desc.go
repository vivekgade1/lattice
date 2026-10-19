package app

import (
	"context"
	"errors"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	d5 "example.com/d5wrapper/gen/descwrap5/v1"
	"example.com/d5wrapper/internal/dom"
)

// codeFor maps domain errors to transport codes.
var codeFor = []struct {
	err  error
	code codes.Code
}{
	{dom.ErrMissing, codes.NotFound},
	{dom.ErrInvalid, codes.InvalidArgument},
}

// translate turns a handler error into a status error.
func translate(err error) error {
	if err == nil {
		return nil
	}
	for _, m := range codeFor {
		if errors.Is(err, m.err) {
			return status.Error(m.code, m.err.Error())
		}
	}
	return status.Error(codes.Internal, "internal error")
}

// EqDesc returns a copy of the generated descriptor in which every handler translates domain errors.
func EqDesc() *grpc.ServiceDesc {
	src := d5.Eq_ServiceDesc
	out := src
	out.Methods = make([]grpc.MethodDesc, len(src.Methods))
	for i, m := range src.Methods {
		inner := m.Handler
		out.Methods[i] = grpc.MethodDesc{
			MethodName: m.MethodName,
			Handler: func(srv any, ctx context.Context, dec func(any) error, ic grpc.UnaryServerInterceptor) (any, error) {
				resp, err := inner(srv, ctx, dec, ic)
				return resp, translate(err)
			},
		}
	}
	return &out
}
