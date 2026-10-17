// notesapp: a gRPC notes service for Lattice's gRPC spike (pure-Go SQLite, no cgo).
//
// Environment: PORT (gRPC), HTTP_PORT (the grpc-gateway, optional), DB_PATH, BUG (a
// deliberate fault, tests only), MIGRATE=1 (create the tables), STARTUP_FAIL=1 (exit at once).
package main

import (
	"context"
	"fmt"
	"net"
	"net/http"
	"os"

	notesv1 "example.com/notesapp/gen/notes/v1"
	"example.com/notesapp/internal/svc"
	"github.com/glebarez/sqlite"
	"github.com/grpc-ecosystem/grpc-gateway/v2/runtime"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/protobuf/encoding/protojson"
	"gorm.io/gorm"
)

func main() {
	if os.Getenv("STARTUP_FAIL") != "" {
		fmt.Fprintln(os.Stderr, "notesapp: refusing to start: STARTUP_FAIL is set")
		os.Exit(3)
	}
	port, path := os.Getenv("PORT"), os.Getenv("DB_PATH")
	if port == "" || path == "" {
		fmt.Fprintln(os.Stderr, "notesapp: PORT and DB_PATH are required")
		os.Exit(2)
	}
	db, err := gorm.Open(sqlite.Open("file:"+path+"?_pragma=busy_timeout(10000)"), &gorm.Config{})
	if err != nil {
		fmt.Fprintln(os.Stderr, "notesapp: cannot open the database:", err)
		os.Exit(1)
	}
	if sqlDB, err := db.DB(); err == nil {
		sqlDB.SetMaxOpenConns(1)
	}
	if os.Getenv("MIGRATE") == "1" {
		if err := db.AutoMigrate(&svc.User{}, &svc.Note{}); err != nil {
			fmt.Fprintln(os.Stderr, "notesapp: migrate:", err)
			os.Exit(1)
		}
	}
	bug := os.Getenv("BUG")
	s := grpc.NewServer(grpc.ChainUnaryInterceptor(svc.LogInterceptor, svc.AuthInterceptor(db)))
	notesv1.RegisterAccountsServer(s, svc.NewAccounts(db, bug))
	notesv1.RegisterNotesServer(s, &svc.Notes{DB: db, Bug: bug})
	healthpb.RegisterHealthServer(s, health.NewServer())
	lis, err := net.Listen("tcp", "127.0.0.1:"+port)
	if err != nil {
		fmt.Fprintln(os.Stderr, "notesapp:", err)
		os.Exit(1)
	}
	if httpPort := os.Getenv("HTTP_PORT"); httpPort != "" {
		mux := runtime.NewServeMux(runtime.WithMarshalerOption(runtime.MIMEWildcard, &runtime.JSONPb{
			MarshalOptions: protojson.MarshalOptions{UseProtoNames: true, EmitUnpopulated: true},
		}))
		opts := []grpc.DialOption{grpc.WithTransportCredentials(insecure.NewCredentials())}
		if err := notesv1.RegisterNotesHandlerFromEndpoint(context.Background(), mux, "127.0.0.1:"+port, opts); err != nil {
			fmt.Fprintln(os.Stderr, "notesapp: gateway:", err)
			os.Exit(1)
		}
		go func() {
			if err := http.ListenAndServe("127.0.0.1:"+httpPort, mux); err != nil {
				fmt.Fprintln(os.Stderr, "notesapp: http:", err)
				os.Exit(1)
			}
		}()
	}
	if err := s.Serve(lis); err != nil {
		fmt.Fprintln(os.Stderr, "notesapp:", err)
		os.Exit(1)
	}
}
