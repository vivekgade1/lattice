package main

import (
	"context"
	"database/sql"
	"fmt"
	"net"
	"os"
	"strings"

	toyv1 "example.com/toybuild/gen/toy/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/status"
	_ "modernc.org/sqlite"
)

type shelf struct {
	toyv1.UnimplementedShelfServer
	db *sql.DB
}

func (s *shelf) AddItem(ctx context.Context, in *toyv1.AddItemRequest) (*toyv1.Item, error) {
	if in.GetName() == "" {
		return nil, status.Error(codes.InvalidArgument, "name is required")
	}
	if in.GetQty() < 0 {
		return nil, status.Error(codes.InvalidArgument, "qty must not be negative")
	}
	res, err := s.db.ExecContext(ctx, "INSERT INTO items (name, qty) VALUES (?, ?)", in.GetName(), in.GetQty())
	if err != nil {
		if strings.Contains(err.Error(), "UNIQUE") {
			return nil, status.Error(codes.AlreadyExists, "name already used")
		}
		return nil, status.Error(codes.Internal, err.Error())
	}
	id, _ := res.LastInsertId()
	return &toyv1.Item{Id: id, Name: in.GetName(), Qty: in.GetQty()}, nil
}

func (s *shelf) GetItem(ctx context.Context, in *toyv1.GetItemRequest) (*toyv1.Item, error) {
	it := &toyv1.Item{}
	err := s.db.QueryRowContext(ctx, "SELECT id, name, qty FROM items WHERE id = ?", in.GetId()).Scan(&it.Id, &it.Name, &it.Qty)
	if err == sql.ErrNoRows {
		return nil, status.Error(codes.NotFound, "no such item")
	}
	if err != nil {
		return nil, status.Error(codes.Internal, err.Error())
	}
	return it, nil
}

func (s *shelf) ListItems(ctx context.Context, _ *toyv1.ListItemsRequest) (*toyv1.ListItemsResponse, error) {
	rows, err := s.db.QueryContext(ctx, "SELECT id, name, qty FROM items ORDER BY id")
	if err != nil {
		return nil, status.Error(codes.Internal, err.Error())
	}
	defer rows.Close()
	out := &toyv1.ListItemsResponse{}
	for rows.Next() {
		it := &toyv1.Item{}
		if err := rows.Scan(&it.Id, &it.Name, &it.Qty); err != nil {
			return nil, status.Error(codes.Internal, err.Error())
		}
		out.Items = append(out.Items, it)
	}
	return out, nil
}

func (s *shelf) RemoveItem(ctx context.Context, in *toyv1.RemoveItemRequest) (*toyv1.RemoveItemResponse, error) {
	res, err := s.db.ExecContext(ctx, "DELETE FROM items WHERE id = ?", in.GetId())
	if err != nil {
		return nil, status.Error(codes.Internal, err.Error())
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return nil, status.Error(codes.NotFound, "no such item")
	}
	return &toyv1.RemoveItemResponse{}, nil
}

func main() {
	port, path := os.Getenv("PORT"), os.Getenv("DB_PATH")
	if port == "" || path == "" {
		fmt.Fprintln(os.Stderr, "PORT and DB_PATH are required")
		os.Exit(2)
	}
	db, err := sql.Open("sqlite", "file:"+path+"?_pragma=busy_timeout(10000)")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	db.SetMaxOpenConns(1)
	lis, err := net.Listen("tcp", "127.0.0.1:"+port)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	s := grpc.NewServer()
	toyv1.RegisterShelfServer(s, &shelf{db: db})
	healthpb.RegisterHealthServer(s, health.NewServer())
	if err := s.Serve(lis); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
