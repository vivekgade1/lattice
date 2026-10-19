// Package app is the composition root.
package app

import (
	"context"
	"fmt"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"

	s5 "example.com/s5main/gen/status5/v1"
)

type verifier interface{ Verify(name string) error }

type strict struct{}

func (strict) Verify(name string) error { return fmt.Errorf("strict: %s", name) }

type lax struct{}

func (lax) Verify(name string) error { return nil }

type untImpl struct {
	s5.UnimplementedUntServer
	check verifier
}

type litImpl struct{ s5.UnimplementedLitServer }

func (*litImpl) Get(ctx context.Context, in *s5.Req) (*s5.Rep, error) {
	return nil, &missing{in.Name}
}

func (*litImpl) Put(ctx context.Context, in *s5.Req) (*s5.Rep, error) {
	return nil, missing{in.Name}
}

func (*litImpl) Del(ctx context.Context, in *s5.Req) (*s5.Rep, error) {
	return nil, newMissing(in.Name)
}

type fldImpl struct{ s5.UnimplementedFldServer }

func (*fldImpl) Get(ctx context.Context, in *s5.Req) (*s5.Rep, error) {
	return nil, &refusal{codes.InvalidArgument, "bad"}
}

func (*fldImpl) Put(ctx context.Context, in *s5.Req) (*s5.Rep, error) {
	return nil, &refusal{code: codes.AlreadyExists, msg: "dup"}
}

func (*fldImpl) Del(ctx context.Context, in *s5.Req) (*s5.Rep, error) {
	return nil, refuse(codes.NotFound, "none")
}

func (*fldImpl) Dyn(ctx context.Context, in *s5.Req) (*s5.Rep, error) {
	return nil, &refusal{code: pickCode(nil), msg: "x"}
}

func (*fldImpl) Var(ctx context.Context, in *s5.Req) (*s5.Rep, error) {
	return nil, errGone
}

type wrpImpl struct{ s5.UnimplementedWrpServer }

func (*wrpImpl) Get(ctx context.Context, in *s5.Req) (*s5.Rep, error) {
	return nil, fmt.Errorf("wrapped: %w", &refusal{code: codes.Aborted, msg: "x"})
}

func (*wrpImpl) Put(ctx context.Context, in *s5.Req) (*s5.Rep, error) {
	return nil, fmt.Errorf("lookup: %w", missing{in.Name})
}

type parImpl struct{ s5.UnimplementedParServer }

func (*parImpl) Get(ctx context.Context, in *s5.Req) (*s5.Rep, error) {
	return nil, fail(codes.PermissionDenied, "no")
}

func (*parImpl) Put(ctx context.Context, in *s5.Req) (*s5.Rep, error) {
	return nil, failf(codes.OutOfRange, "n=%d", 3)
}

func (*parImpl) Del(ctx context.Context, in *s5.Req) (*s5.Rep, error) {
	return nil, deny(codes.FailedPrecondition)
}

func (*parImpl) Wrap(ctx context.Context, in *s5.Req) (*s5.Rep, error) {
	return nil, wrapped(codes.Internal)
}

func (*parImpl) Mystery(ctx context.Context, in *s5.Req) (*s5.Rep, error) {
	return nil, fail(pickCode(nil), "?")
}

type detImpl struct{ s5.UnimplementedDetServer }

func (*detImpl) Get(ctx context.Context, in *s5.Req) (*s5.Rep, error) {
	return nil, detailed(in.Name)
}

func (*detImpl) Put(ctx context.Context, in *s5.Req) (*s5.Rep, error) {
	return nil, split(in.Name)
}

func (*detImpl) Del(ctx context.Context, in *s5.Req) (*s5.Rep, error) {
	return nil, rich(codes.PermissionDenied, in.Name)
}

func (u *untImpl) Get(ctx context.Context, in *s5.Req) (*s5.Rep, error) {
	return nil, u.check.Verify(in.Name)
}

func (*untImpl) Put(ctx context.Context, in *s5.Req) (*s5.Rep, error) {
	return &s5.Rep{Name: in.Name}, nil
}

// Build assembles the server.
func Build() *grpc.Server {
	s := grpc.NewServer()
	s5.RegisterLitServer(s, &litImpl{})
	s5.RegisterFldServer(s, &fldImpl{})
	s5.RegisterWrpServer(s, &wrpImpl{})
	s5.RegisterParServer(s, &parImpl{})
	s5.RegisterDetServer(s, &detImpl{})
	s5.RegisterUntServer(s, &untImpl{check: strict{}})
	return s
}
