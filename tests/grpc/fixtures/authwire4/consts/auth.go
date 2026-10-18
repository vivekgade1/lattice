package main

import (
	"context"

	healthpb "google.golang.org/grpc/health/grpc_health_v1"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"

	aw "example.com/aw4consts/gen/aw4/v1"
)

var _ = aw.Req{}

// refuse answers the caller's token or the refusal.
func refuse(ctx context.Context) error {
	md, ok := metadata.FromIncomingContext(ctx)
	if !ok {
		return status.Error(codes.Unauthenticated, "no metadata")
	}
	if len(md.Get("authorization")) == 0 {
		return status.Error(codes.Unauthenticated, "missing token")
	}
	return nil
}

var anonymous = map[string]bool{
	aw.Desk_Get_FullMethodName:           true,
	healthpb.Health_Check_FullMethodName: true,
	healthpb.Health_Watch_FullMethodName: true,
}

func authUnary(ctx context.Context, req any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
	if anonymous[info.FullMethod] {
		return handler(ctx, req)
	}
	if err := refuse(ctx); err != nil {
		return nil, err
	}
	return handler(ctx, req)
}
