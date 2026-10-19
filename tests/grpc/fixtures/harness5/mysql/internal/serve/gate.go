// Package serve holds the handlers of the ledger.
package serve

import (
	"context"
	"database/sql"
	"strings"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

type userKey struct{}

var open = map[string]bool{
	"/ledger.v1.Accounts/Login":    true,
	"/grpc.health.v1.Health/Check": true,
}

// Gate lets the open methods through and gives every other call its user.
func Gate(db *sql.DB) grpc.UnaryServerInterceptor {
	return func(ctx context.Context, req any, info *grpc.UnaryServerInfo, next grpc.UnaryHandler) (any, error) {
		if open[info.FullMethod] {
			return next(ctx, req)
		}
		md, _ := metadata.FromIncomingContext(ctx)
		vals := md.Get("authorization")
		if len(vals) == 0 {
			return nil, status.Error(codes.Unauthenticated, "missing token")
		}
		var name string
		err := db.QueryRowContext(ctx, "SELECT user_name FROM sessions WHERE token = ?", strings.TrimPrefix(vals[0], "Bearer ")).Scan(&name)
		if err != nil {
			return nil, status.Error(codes.Unauthenticated, "bad token")
		}
		return next(context.WithValue(ctx, userKey{}, name), req)
	}
}

func caller(ctx context.Context) string {
	name, _ := ctx.Value(userKey{}).(string)
	return name
}
