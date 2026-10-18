package main

import (
	"context"
	"time"

	"google.golang.org/grpc"
)

// logUnary times every call (it decides nothing).
func logUnary(ctx context.Context, req any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
	started := time.Now()
	resp, err := handler(ctx, req)
	_ = time.Since(started)
	return resp, err
}
