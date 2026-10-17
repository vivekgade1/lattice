package svc

import (
	"context"
	"strings"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"gorm.io/gorm"
)

type userKey struct{}

// publicMethods are the rpcs a caller may reach without a token.
var publicMethods = map[string]bool{
	"/notes.v1.Accounts/Register":  true,
	"/notes.v1.Accounts/Login":     true,
	"/grpc.health.v1.Health/Check": true,
}

// LogInterceptor times every call (it decides nothing).
func LogInterceptor(ctx context.Context, req any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
	started := time.Now()
	resp, err := handler(ctx, req)
	_ = time.Since(started)
	return resp, err
}

// AuthInterceptor lets the public rpcs through and gives every other rpc its caller.
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
		var u User
		if err := db.Where("token = ?", token).First(&u).Error; err != nil {
			return nil, status.Error(codes.Unauthenticated, "bad token")
		}
		return handler(context.WithValue(ctx, userKey{}, &u), req)
	}
}

// caller is the user the interceptor put on the context.
func caller(ctx context.Context) *User {
	u, _ := ctx.Value(userKey{}).(*User)
	return u
}

// currentUser is caller with an answer for a context that has none.
func currentUser(ctx context.Context) (*User, error) {
	u := caller(ctx)
	if u == nil {
		return nil, status.Error(codes.Unauthenticated, "no caller")
	}
	return u, nil
}
