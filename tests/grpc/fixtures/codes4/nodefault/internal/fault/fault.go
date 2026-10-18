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
	ErrOne = errors.New("one")
	ErrTwo = errors.New("two")
)

var table = []struct {
	err  error
	code codes.Code
}{
	{ErrOne, codes.Aborted},
}

// Wire converts the errors it knows; any other passes through.
func Wire(err error) error {
	for _, row := range table {
		if errors.Is(err, row.err) {
			return status.Error(row.code, err.Error())
		}
	}
	return err
}

// Translate is a unary interceptor applying Wire to every handler error.
func Translate(ctx context.Context, req any, _ *grpc.UnaryServerInfo, next grpc.UnaryHandler) (any, error) {
	resp, err := next(ctx, req)
	if err != nil {
		return nil, Wire(err)
	}
	return resp, nil
}
