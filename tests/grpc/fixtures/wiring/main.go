// wiringapp: a server wired eleven different ways, for the extractor's registration reading.
package main

import (
	"fmt"
	"net"
	"os"

	"example.com/wiringapp/internal/serve"
)

func main() {
	srv := serve.Build(&serve.DB{})
	lis, err := net.Listen("tcp", "127.0.0.1:"+os.Getenv("PORT"))
	if err != nil {
		fmt.Fprintln(os.Stderr, "wiringapp:", err)
		os.Exit(1)
	}
	if err := srv.Serve(lis); err != nil {
		fmt.Fprintln(os.Stderr, "wiringapp:", err)
		os.Exit(1)
	}
}
