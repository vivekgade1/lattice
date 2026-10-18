package svc

import (
	"context"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	c4 "example.com/c4keys/gen/codes4/v1"
)

type reason int

const (
	reasonNoRow reason = iota
	reasonBad
	reasonLate
)

var codeFor = map[reason]codes.Code{
	reasonNoRow: codes.NotFound,
	reasonBad:   codes.InvalidArgument,
	reasonLate:  codes.Aborted,
}

func fail(r reason, msg string) error { return status.Error(codeFor[r], msg) }

// Kc answers c4 Kc.
type Kc struct{ c4.UnimplementedKcServer }

func (Kc) Get(ctx context.Context, in *c4.Req) (*c4.Rep, error) {
	if in.Name == "" {
		return nil, fail(reasonNoRow, "no row")
	}
	if in.Name == "?" {
		return nil, fail(reasonBad, "bad name")
	}
	return &c4.Rep{Name: in.Name}, nil
}

func (Kc) Put(ctx context.Context, in *c4.Req) (*c4.Rep, error) {
	return nil, status.Error(codeFor[reasonLate], "late")
}

// Kd answers c4 Kd.
type Kd struct{ c4.UnimplementedKdServer }

func (Kd) Get(ctx context.Context, in *c4.Req) (*c4.Rep, error) {
	return &c4.Rep{Name: in.Name}, nil
}
