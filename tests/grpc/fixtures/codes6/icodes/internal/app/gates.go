package app

import (
	"context"
	"errors"
	"log"
	"os"
	"strings"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"

	c6 "example.com/c6icodes/gen/codes6/v1"
)

// refusal carries its own gRPC code.
type refusal struct {
	code codes.Code
	msg  string
}

func (r *refusal) Error() string { return r.msg }

// GRPCStatus lets grpc-go report the code.
func (r *refusal) GRPCStatus() *status.Status { return status.New(r.code, r.msg) }

func header(ctx context.Context, key string) string {
	md, _ := metadata.FromIncomingContext(ctx)
	if v := md.Get(key); len(v) > 0 {
		return v[0]
	}
	return ""
}

// checkVersion refuses an old client on every method.
func checkVersion(ctx context.Context) error {
	if header(ctx, "x-client-version") == "0" {
		return &refusal{codes.FailedPrecondition, "upgrade required"}
	}
	return nil
}

// translate reports any handler error: application errors keep their code, anything else is INTERNAL.
func translate(method string, err error) error {
	if err == nil {
		return nil
	}
	var r *refusal
	if errors.As(err, &r) {
		return r.GRPCStatus().Err()
	}
	if _, ok := status.FromError(err); ok {
		return err
	}
	log.Printf("internal error in %s: %v", method, err)
	return status.Error(codes.Internal, "internal")
}

// versionGate runs on every method but the health service.
func versionGate(ctx context.Context, req any, info *grpc.UnaryServerInfo, next grpc.UnaryHandler) (any, error) {
	if strings.HasPrefix(info.FullMethod, "/grpc.health.") {
		return next(ctx, req)
	}
	if err := checkVersion(ctx); err != nil {
		return nil, translate(info.FullMethod, err)
	}
	if strings.Contains(info.FullMethod, os.Getenv("FREEZE")) {
		return nil, status.Error(codes.Aborted, "frozen")
	}
	if err := limit(info.FullMethod); err != nil {
		return nil, err
	}
	resp, err := next(ctx, req)
	return resp, translate(info.FullMethod, err)
}

// metered are the methods with a quota.
var metered = map[string]bool{
	"/codes6.v1.Till/Pay": true,
	"/codes6.v1.Shop/Buy": true,
}

// quotaGate refuses metered methods over quota.
func quotaGate(ctx context.Context, req any, info *grpc.UnaryServerInfo, next grpc.UnaryHandler) (any, error) {
	if metered[info.FullMethod] {
		if header(ctx, "x-over") != "" {
			return nil, status.Error(codes.ResourceExhausted, "over quota")
		}
	}
	return next(ctx, req)
}

// tillGate acts on the Till service only.
func tillGate(ctx context.Context, req any, info *grpc.UnaryServerInfo, next grpc.UnaryHandler) (any, error) {
	if !strings.HasPrefix(info.FullMethod, "/"+c6.Till_ServiceDesc.ServiceName+"/") {
		return next(ctx, req)
	}
	if header(ctx, "x-till-closed") != "" {
		return nil, status.Error(codes.Unavailable, "till closed")
	}
	return next(ctx, req)
}

// limit decides by the method: the code depends on which method is called.
func limit(method string) error {
	if method == "/codes6.v1.Shop/Look" && os.Getenv("LIMIT") != "" {
		return status.Error(codes.OutOfRange, "limited")
	}
	return nil
}
