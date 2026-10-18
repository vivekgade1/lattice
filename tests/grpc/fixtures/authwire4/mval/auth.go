package main

import (
	"context"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"

	aw "example.com/aw4mval/gen/aw4/v1"
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

type keeper struct{}

// Unary lets Desk/Get through and refuses the rest without a token.
func (k *keeper) Unary(ctx context.Context, req any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
	if info.FullMethod == "/aw4.v1.Desk/Get" {
		return handler(ctx, req)
	}
	if err := refuse(ctx); err != nil {
		return nil, err
	}
	return handler(ctx, req)
}
