// t4broken: a grpc-go server, read and not run.
package main

import (
	"fmt"
	"net"
	"os"

	"google.golang.org/grpc"
)

func main() {
	srv := grpc.NewServer()
	lis, err := net.Listen("tcp", "127.0.0.1:"+os.Getenv("PORT"))
	if err != nil {
		fmt.Fprintln(os.Stderr, "t4broken:", err)
		os.Exit(1)
	}
	if err := srv.Serve(lis); err != nil {
		fmt.Fprintln(os.Stderr, "t4broken:", err)
		os.Exit(1)
	}
}
