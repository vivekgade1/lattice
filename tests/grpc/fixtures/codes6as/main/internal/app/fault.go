package app

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	c6 "example.com/c6as/gen/codes6/v1"
)

// Kind classifies a billing failure; handlers never choose a gRPC code.
type Kind int

const (
	NoInvoice Kind = iota + 1
	NotYours
	TooLarge
)

// Fault is a domain failure.
type Fault struct {
	Kind   Kind
	Detail string
}

func (f *Fault) Error() string { return f.Detail }

func fault(kind Kind, format string, args ...any) *Fault {
	return &Fault{Kind: kind, Detail: fmt.Sprintf(format, args...)}
}

var codeForKind = map[Kind]codes.Code{
	NoInvoice: codes.NotFound,
	NotYours:  codes.PermissionDenied,
	TooLarge:  codes.OutOfRange,
}

// faultInterceptor reports Bill's faults; other methods pass through untouched.
func faultInterceptor(ctx context.Context, req any, info *grpc.UnaryServerInfo, next grpc.UnaryHandler) (any, error) {
	if !strings.HasPrefix(info.FullMethod, "/"+c6.Bill_ServiceDesc.ServiceName+"/") {
		return next(ctx, req)
	}
	resp, err := next(ctx, req)
	if err == nil {
		return resp, nil
	}
	var f *Fault
	if errors.As(err, &f) {
		return nil, status.Error(codeForKind[f.Kind], f.Detail)
	}
	if _, isStatus := status.FromError(err); isStatus {
		return nil, err
	}
	return nil, status.Error(codes.Internal, "internal error")
}
