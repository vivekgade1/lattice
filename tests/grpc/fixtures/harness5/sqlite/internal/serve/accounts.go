package serve

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"

	ledgerv1 "example.com/ledger5/gen/ledger/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// Accounts serves ledger.v1.Accounts.
type Accounts struct {
	ledgerv1.UnimplementedAccountsServer
	DB *sql.DB
}

// Login checks the credentials and mints a session token.
func (a *Accounts) Login(ctx context.Context, req *ledgerv1.LoginRequest) (*ledgerv1.LoginResponse, error) {
	if req.Name == "" {
		return nil, status.Error(codes.InvalidArgument, "name is required")
	}
	var found string
	err := a.DB.QueryRowContext(ctx, "SELECT name FROM users WHERE name = ? AND password = ?", req.Name, req.Password).Scan(&found)
	if err != nil {
		return nil, status.Error(codes.Unauthenticated, "bad credentials")
	}
	raw := make([]byte, 8)
	if _, err := rand.Read(raw); err != nil {
		return nil, status.Error(codes.Internal, "entropy")
	}
	token := "t-" + hex.EncodeToString(raw)
	if _, err := a.DB.ExecContext(ctx, "INSERT INTO sessions (token, user_name) VALUES (?, ?)", token, found); err != nil {
		return nil, status.Error(codes.Internal, "session")
	}
	return &ledgerv1.LoginResponse{Token: token}, nil
}

// Whoami answers the caller's name.
func (a *Accounts) Whoami(ctx context.Context, req *ledgerv1.WhoamiRequest) (*ledgerv1.WhoamiResponse, error) {
	return &ledgerv1.WhoamiResponse{Name: caller(ctx)}, nil
}
