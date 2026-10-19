// s5icodeunclear: a grpc-go server, read and not run.
package main

import (
	"fmt"
	"net"
	"os"

	"example.com/s5icodeunclear/internal/app"
)

func main() {
	srv := app.Build()
	lis, err := net.Listen("tcp", "127.0.0.1:"+os.Getenv("PORT"))
	if err != nil {
		fmt.Fprintln(os.Stderr, "s5:", err)
		os.Exit(1)
	}
	if err := srv.Serve(lis); err != nil {
		fmt.Fprintln(os.Stderr, "s5:", err)
		os.Exit(1)
	}
}
