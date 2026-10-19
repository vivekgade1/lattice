// Package authn is the authentication gate.
package authn

import (
	"context"
	"strings"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

// ownNamespace is the proto package prefix whose methods the gate governs.
const ownNamespace = "audience5."

// Gate refuses calls to member-audience methods that carry no token.
type Gate struct{}

// NewGate builds a Gate.
func NewGate() *Gate { return &Gate{} }

func bearer(ctx context.Context) string {
	md, _ := metadata.FromIncomingContext(ctx)
	for _, v := range md.Get("authorization") {
		if strings.HasPrefix(v, "Bearer ") {
			return strings.TrimPrefix(v, "Bearer ")
		}
	}
	return ""
}

// Unary is the unary half of the gate.
func (g *Gate) Unary() grpc.UnaryServerInterceptor {
	return func(ctx context.Context, req any, info *grpc.UnaryServerInfo, next grpc.UnaryHandler) (any, error) {
		if bearer(ctx) == "" {
			return nil, status.Error(codes.Unauthenticated, "missing token")
		}
		return next(ctx, req)
	}
}
