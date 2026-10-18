// t4harness: a grpc-go server over database/sql; its schema lives in harness.py (read, not run).
package main

import (
	"context"
	"database/sql"
	"fmt"
	"net"
	"os"

	"google.golang.org/grpc"

	t4 "example.com/t4harness/gen/tables4/v1"
)

type ledger struct {
	t4.UnimplementedLedgerServer
	db *sql.DB
}

func (l *ledger) Get(ctx context.Context, in *t4.Req) (*t4.Rep, error) {
	var name string
	if err := l.db.QueryRowContext(ctx, "SELECT name FROM accounts WHERE name = ?", in.Name).Scan(&name); err != nil {
		return nil, err
	}
	return &t4.Rep{Name: name}, nil
}

func (l *ledger) Put(ctx context.Context, in *t4.Req) (*t4.Rep, error) {
	_, err := l.db.ExecContext(ctx, "INSERT INTO accounts (name) VALUES (?)", in.Name)
	return &t4.Rep{Name: in.Name}, err
}

func main() {
	db, err := sql.Open("sqlite", os.Getenv("DB"))
	if err != nil {
		os.Exit(1)
	}
	srv := grpc.NewServer()
	t4.RegisterLedgerServer(srv, &ledger{db: db})
	lis, err := net.Listen("tcp", "127.0.0.1:"+os.Getenv("PORT"))
	if err != nil {
		fmt.Fprintln(os.Stderr, "t4harness:", err)
		os.Exit(1)
	}
	if err := srv.Serve(lis); err != nil {
		fmt.Fprintln(os.Stderr, "t4harness:", err)
		os.Exit(1)
	}
}
