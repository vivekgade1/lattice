// t4gorm: a grpc-go server over GORM models (read, not run).
package main

import (
	"context"
	"fmt"
	"net"
	"os"

	"google.golang.org/grpc"
	"gorm.io/gorm"

	t4 "example.com/t4gorm/gen/tables4/v1"
)

// Note is a stored note.
type Note struct {
	ID    uint `gorm:"primaryKey"`
	Title string
	Body  string
}

type notesSvc struct {
	t4.UnimplementedNotesServer
	db *gorm.DB
}

func (s *notesSvc) Get(ctx context.Context, in *t4.Req) (*t4.Rep, error) {
	var n Note
	s.db.First(&n, "title = ?", in.Name)
	return &t4.Rep{Name: n.Title}, nil
}

func (s *notesSvc) Put(ctx context.Context, in *t4.Req) (*t4.Rep, error) {
	s.db.Create(&Note{Title: in.Name})
	return &t4.Rep{Name: in.Name}, nil
}

func main() {
	var db *gorm.DB
	db.AutoMigrate(&Note{})
	srv := grpc.NewServer()
	t4.RegisterNotesServer(srv, &notesSvc{db: db})
	lis, err := net.Listen("tcp", "127.0.0.1:"+os.Getenv("PORT"))
	if err != nil {
		fmt.Fprintln(os.Stderr, "t4gorm:", err)
		os.Exit(1)
	}
	if err := srv.Serve(lis); err != nil {
		fmt.Fprintln(os.Stderr, "t4gorm:", err)
		os.Exit(1)
	}
}
