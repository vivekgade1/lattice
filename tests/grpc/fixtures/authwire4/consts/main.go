// aw4: a grpc-go server whose authentication is written one way (read, not run).
package main

import (
	"fmt"
	"net"
	"os"

	"google.golang.org/grpc"

	aw "example.com/aw4consts/gen/aw4/v1"
)

func main() {
	srv := grpc.NewServer(grpc.UnaryInterceptor(authUnary))
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
