// Package fault holds the domain errors and their wire translation.
package fault

import (
	"context"
	"errors"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// Domain errors handlers return or wrap with %w.
var (
	ErrNotFound = errors.New("not found")
	ErrDup      = errors.New("duplicate")
	ErrBusy     = errors.New("busy")
	ErrStale    = errors.New("stale")
)

var byError = map[error]codes.Code{
	ErrNotFound: codes.NotFound,
	ErrDup:      codes.AlreadyExists,
}

// Wire converts a handler error into a status error; unknown errors become INTERNAL.
func Wire(err error) error {
	for e, c := range byError {
		if errors.Is(err, e) {
			return status.Error(c, err.Error())
		}
	}
	switch {
	case errors.Is(err, ErrBusy):
		return status.Error(codes.FailedPrecondition, "busy")
	}
	return status.Error(codes.Internal, "internal")
}

// Translate is a unary interceptor applying Wire to every handler error.
func Translate(ctx context.Context, req any, _ *grpc.UnaryServerInfo, next grpc.UnaryHandler) (any, error) {
	resp, err := next(ctx, req)
	if err != nil {
		return nil, Wire(err)
	}
	return resp, nil
}
