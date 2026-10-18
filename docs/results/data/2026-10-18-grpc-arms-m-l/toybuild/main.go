// toy-server: the Shelf gRPC service (scaffold: the server starts and answers health; the Shelf rpcs are not built yet).
package main

import (
	"database/sql"
	"fmt"
	"net"
	"os"

	toyv1 "example.com/toybuild/gen/toy/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
	_ "modernc.org/sqlite"
)

func main() {
	port, path := os.Getenv("PORT"), os.Getenv("DB_PATH")
	if port == "" || path == "" {
		fmt.Fprintln(os.Stderr, "toy-server: PORT and DB_PATH are required")
		os.Exit(2)
	}
	db, err := sql.Open("sqlite", "file:"+path+"?_pragma=busy_timeout(10000)")
	if err != nil {
		fmt.Fprintln(os.Stderr, "toy-server:", err)
		os.Exit(1)
	}
	db.SetMaxOpenConns(1)
	lis, err := net.Listen("tcp", "127.0.0.1:"+port)
	if err != nil {
		fmt.Fprintln(os.Stderr, "toy-server:", err)
		os.Exit(1)
	}
	s := grpc.NewServer()
	toyv1.RegisterShelfServer(s, &shelf{db: db})
	healthpb.RegisterHealthServer(s, health.NewServer())
	if err := s.Serve(lis); err != nil {
		fmt.Fprintln(os.Stderr, "toy-server:", err)
		os.Exit(1)
	}
}
