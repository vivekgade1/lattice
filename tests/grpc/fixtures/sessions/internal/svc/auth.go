package svc

import (
	"context"
	"strings"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"gorm.io/gorm"
)

type sessionKey struct{}

// publicMethods are the rpcs a caller may reach without a token.
var publicMethods = map[string]bool{
	"/sessions.v1.Sessions/Login":  true,
	"/sessions.v1.Sessions/Health": true,
	"/grpc.health.v1.Health/Check": true,
}

// AuthInterceptor lets the public rpcs through and gives every other rpc its session.
func AuthInterceptor(db *gorm.DB) grpc.UnaryServerInterceptor {
	return func(ctx context.Context, req any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
		if publicMethods[info.FullMethod] {
			return handler(ctx, req)
		}
		md, ok := metadata.FromIncomingContext(ctx)
		if !ok {
			return nil, status.Error(codes.Unauthenticated, "no metadata")
		}
		vals := md.Get("authorization")
		if len(vals) == 0 {
			return nil, status.Error(codes.Unauthenticated, "missing token")
		}
		token := strings.TrimPrefix(vals[0], "Bearer ")
		var s Session
		if err := db.Where("token = ?", token).First(&s).Error; err != nil {
			return nil, status.Error(codes.Unauthenticated, "bad token")
		}
		return handler(context.WithValue(ctx, sessionKey{}, &s), req)
	}
}

// session is the session the interceptor put on the context.
func session(ctx context.Context) (*Session, error) {
	s, _ := ctx.Value(sessionKey{}).(*Session)
	if s == nil {
		return nil, status.Error(codes.Unauthenticated, "no session")
	}
	return s, nil
}
