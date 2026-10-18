package main

import (
	"context"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"

	awv1 "example.com/authwire/gen/aw/v1"
)

var errNoTicket = status.Error(codes.Unauthenticated, "sign in first")

// openDoors are the rpcs anyone may call.
var openDoors = map[string]bool{
	awv1.Desk_Ping_FullMethodName: true,
}

type gate struct{ tokens map[string]string }

func (g gate) resolve(ctx context.Context) (context.Context, error) {
	md, ok := metadata.FromIncomingContext(ctx)
	if !ok {
		return nil, errNoTicket
	}
	vals := md.Get("authorization")
	if len(vals) == 0 {
		return nil, errNoTicket
	}
	if _, known := g.tokens[vals[0]]; !known {
		return nil, errNoTicket
	}
	return ctx, nil
}

func (g gate) unary(ctx context.Context, req any, info *grpc.UnaryServerInfo, next grpc.UnaryHandler) (any, error) {
	if openDoors[info.FullMethod] {
		return next(ctx, req)
	}
	ctx, err := g.resolve(ctx)
	if err != nil {
		return nil, err
	}
	return next(ctx, req)
}

// steady turns a handler panic into INTERNAL instead of killing the server.
func steady(ctx context.Context, req any, _ *grpc.UnaryServerInfo, next grpc.UnaryHandler) (resp any, err error) {
	defer func() {
		if r := recover(); r != nil {
			resp, err = nil, status.Errorf(codes.Internal, "panic: %v", r)
		}
	}()
	return next(ctx, req)
}
