// sessionsapp: a gRPC service whose sessions can be revoked or deleted, for Lattice's
// state-independent reconcile (pure-Go SQLite, no cgo).
//
// Environment: PORT (gRPC), DB_PATH.
package main

import (
	"fmt"
	"net"
	"os"

	sessionsv1 "example.com/sessionsapp/gen/sessions/v1"
	"example.com/sessionsapp/internal/svc"
	"github.com/glebarez/sqlite"
	"google.golang.org/grpc"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
	"gorm.io/gorm"
)

func main() {
	port, path := os.Getenv("PORT"), os.Getenv("DB_PATH")
	if port == "" || path == "" {
		fmt.Fprintln(os.Stderr, "sessionsapp: PORT and DB_PATH are required")
		os.Exit(2)
	}
	db, err := gorm.Open(sqlite.Open("file:"+path+"?_pragma=busy_timeout(10000)"), &gorm.Config{})
	if err != nil {
		fmt.Fprintln(os.Stderr, "sessionsapp: cannot open the database:", err)
		os.Exit(1)
	}
	if sqlDB, err := db.DB(); err == nil {
		sqlDB.SetMaxOpenConns(1)
	}
	s := grpc.NewServer(grpc.ChainUnaryInterceptor(svc.AuthInterceptor(db)))
	sessionsv1.RegisterSessionsServer(s, &svc.Sessions{DB: db})
	healthpb.RegisterHealthServer(s, health.NewServer())
	lis, err := net.Listen("tcp", "127.0.0.1:"+port)
	if err != nil {
		fmt.Fprintln(os.Stderr, "sessionsapp:", err)
		os.Exit(1)
	}
	if err := s.Serve(lis); err != nil {
		fmt.Fprintln(os.Stderr, "sessionsapp:", err)
		os.Exit(1)
	}
}
