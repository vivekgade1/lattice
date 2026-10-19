// ledgerd: a small gRPC ledger over MySQL, the stand-in app of round 5's SG rehearsal.
//
// Environment: PORT (gRPC), LEDGER_DSN (the Go MySQL driver DSN).
package main

import (
	"fmt"
	"net"
	"os"

	ledgerv1 "example.com/ledger5mysql/gen/ledger/v1"
	"example.com/ledger5mysql/internal/serve"
	"example.com/ledger5mysql/internal/store"
	"google.golang.org/grpc"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
)

func main() {
	port, dsn := os.Getenv("PORT"), os.Getenv("LEDGER_DSN")
	if port == "" || dsn == "" {
		fmt.Fprintln(os.Stderr, "ledgerd: PORT and LEDGER_DSN are required")
		os.Exit(2)
	}
	db, err := store.Open(dsn)
	if err != nil {
		fmt.Fprintln(os.Stderr, "ledgerd: cannot open the database:", err)
		os.Exit(1)
	}
	s := grpc.NewServer(grpc.ChainUnaryInterceptor(serve.Gate(db)))
	ledgerv1.RegisterAccountsServer(s, &serve.Accounts{DB: db})
	ledgerv1.RegisterEntriesServer(s, &serve.Entries{DB: db})
	healthpb.RegisterHealthServer(s, health.NewServer())
	lis, err := net.Listen("tcp", "127.0.0.1:"+port)
	if err != nil {
		fmt.Fprintln(os.Stderr, "ledgerd:", err)
		os.Exit(1)
	}
	if err := s.Serve(lis); err != nil {
		fmt.Fprintln(os.Stderr, "ledgerd:", err)
		os.Exit(1)
	}
}
