package main

import (
	"context"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

func currentUser(ctx context.Context) (string, error) {
	md, ok := metadata.FromIncomingContext(ctx)
	if !ok || len(md.Get("authorization")) == 0 {
		return "", status.Error(codes.Unauthenticated, "no caller")
	}
	return md.Get("authorization")[0], nil
}
