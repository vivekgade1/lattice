// aw4: a grpc-go server whose authentication is written one way (read, not run).
package main

import (
	"fmt"
	"net"
	"os"

	"google.golang.org/grpc"

	aw "example.com/aw4exactprefix/gen/aw4/v1"
)

func main() {
	g := newGate(newPolicy().AllowPrefix("/aw4.v1.Desk/"))
	srv := grpc.NewServer(grpc.UnaryInterceptor(g.Unary))
	aw.RegisterDeskServer(srv, deskSvc{})
	lis, err := net.Listen("tcp", "127.0.0.1:"+os.Getenv("PORT"))
	if err != nil {
		fmt.Fprintln(os.Stderr, "aw4:", err)
		os.Exit(1)
	}
	if err := srv.Serve(lis); err != nil {
		fmt.Fprintln(os.Stderr, "aw4:", err)
		os.Exit(1)
	}
}

var _ = os.Args
