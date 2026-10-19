package app

import (
	"context"
	"os"
	"strings"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

func bearer(ctx context.Context) string {
	md, _ := metadata.FromIncomingContext(ctx)
	for _, v := range md.Get("authorization") {
		if strings.HasPrefix(v, "Bearer ") {
			return strings.TrimPrefix(v, "Bearer ")
		}
	}
	return ""
}

// allows reads the open methods from the environment at call time.
func allows(m string) bool { return strings.Contains(os.Getenv("OPEN_METHODS"), m) }

// gate refuses calls without a bearer token, except the listed method.
func gate(ctx context.Context, req any, info *grpc.UnaryServerInfo, next grpc.UnaryHandler) (any, error) {
	if allows(info.FullMethod) {
		return next(ctx, req)
	}
	if bearer(ctx) == "" {
		return nil, status.Error(codes.Unauthenticated, "missing token")
	}
	return next(ctx, req)
}
