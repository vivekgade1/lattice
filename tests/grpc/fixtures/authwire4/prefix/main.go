// aw4: a grpc-go server whose authentication is written one way (read, not run).
package main

import (
	"fmt"
	"net"
	"os"

	"google.golang.org/grpc"

	aw "example.com/aw4prefix/gen/aw4/v1"
)

func main() {
	g := newGate(newPolicy(aw.Shut_Get_FullMethodName).AndService(aw.Open_ServiceDesc.ServiceName))
	srv := grpc.NewServer(grpc.UnaryInterceptor(g.Unary))
	aw.RegisterOpenServer(srv, openSvc{})
	aw.RegisterShutServer(srv, shutSvc{})
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
