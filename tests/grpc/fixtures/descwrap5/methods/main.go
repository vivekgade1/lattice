// d5methods: a grpc-go server, read and not run.
package main

import (
	"fmt"
	"net"
	"os"

	"example.com/d5methods/internal/app"
)

func main() {
	srv := app.Build()
	lis, err := net.Listen("tcp", "127.0.0.1:"+os.Getenv("PORT"))
	if err != nil {
		fmt.Fprintln(os.Stderr, "d5:", err)
		os.Exit(1)
	}
	if err := srv.Serve(lis); err != nil {
		fmt.Fprintln(os.Stderr, "d5:", err)
		os.Exit(1)
	}
}
