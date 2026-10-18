// authwire/gate_traced: a one-service grpc-go server whose authentication is written one way (read, not run).
package main

import (
	"fmt"
	"net"
	"os"

	"google.golang.org/grpc"

	awv1 "example.com/authwire/gen/aw/v1"
)

func main() {
	g := gate{tokens: map[string]string{}}
	srv := grpc.NewServer(grpc.ChainUnaryInterceptor(steady, g.unary))
	awv1.RegisterDeskServer(srv, &desk{})
	lis, err := net.Listen("tcp", "127.0.0.1:"+os.Getenv("PORT"))
	if err != nil {
		fmt.Fprintln(os.Stderr, "authwire:", err)
		os.Exit(1)
	}
	if err := srv.Serve(lis); err != nil {
		fmt.Fprintln(os.Stderr, "authwire:", err)
		os.Exit(1)
	}
}

var _ = grpc.SupportPackageIsVersion9
