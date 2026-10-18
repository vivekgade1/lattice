package main

import (
	"context"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

var publicMethods = map[string]bool{
	"/aw.v1.Desk/Ping": true,
}

// authUnary lets the public rpcs through and refuses the rest without a token.
func authUnary(ctx context.Context, req any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
	if publicMethods[info.FullMethod] {
		return handler(ctx, req)
	}
	md, ok := metadata.FromIncomingContext(ctx)
	if !ok {
		return nil, status.Error(codes.Unauthenticated, "no metadata")
	}
	if len(md.Get("authorization")) == 0 {
		return nil, status.Error(codes.Unauthenticated, "missing token")
	}
	return handler(ctx, req)
}

// gate asks a verifier it was given (a function value) whether the caller may pass.
type gate struct{ verify func(context.Context) error }

func (g gate) unary(ctx context.Context, req any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
	if err := g.verify(ctx); err != nil {
		return nil, err
	}
	return handler(ctx, req)
}

func checkToken(ctx context.Context) error {
	if _, ok := metadata.FromIncomingContext(ctx); !ok {
		return status.Error(codes.Unauthenticated, "no metadata")
	}
	return nil
}
