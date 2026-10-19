package app

import (
	"context"
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

// gate refuses calls without a bearer token, except the listed method.
func gate(ctx context.Context, req any, info *grpc.UnaryServerInfo, next grpc.UnaryHandler) (any, error) {
	if info.FullMethod == "/status5.v1.Open/Hello" {
		return next(ctx, req)
	}
	if bearer(ctx) == "" {
		return nil, status.Error(codes.Unauthenticated, "missing token")
	}
	return next(ctx, req)
}
