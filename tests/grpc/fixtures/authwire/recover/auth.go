package main

import (
	"context"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// recoverUnary turns a handler panic into INTERNAL.
func recoverUnary(ctx context.Context, req any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (resp any, err error) {
	defer func() {
		if r := recover(); r != nil {
			resp, err = nil, status.Errorf(codes.Internal, "panic: %v", r)
		}
	}()
	return handler(ctx, req)
}
