// w4varlost: a grpc-go server, read and not run.
package main

import (
	"fmt"
	"net"
	"os"

	"example.com/w4varlost/internal/app"
)

func main() {
	srv := app.Build()
	lis, err := net.Listen("tcp", "127.0.0.1:"+os.Getenv("PORT"))
	if err != nil {
		fmt.Fprintln(os.Stderr, "w4varlost:", err)
		os.Exit(1)
	}
	if err := srv.Serve(lis); err != nil {
		fmt.Fprintln(os.Stderr, "w4varlost:", err)
		os.Exit(1)
	}
}
