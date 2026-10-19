// q5main: a grpc-go server over database/sql, read and not run.
package main

import (
	"database/sql"
	"fmt"
	"net"
	"os"

	"example.com/q5main/internal/app"
)

func main() {
	db, err := sql.Open("sqlite", os.Getenv("DB_PATH"))
	if err != nil {
		fmt.Fprintln(os.Stderr, "q5:", err)
		os.Exit(1)
	}
	srv := app.Build(db)
	lis, err := net.Listen("tcp", "127.0.0.1:"+os.Getenv("PORT"))
	if err != nil {
		fmt.Fprintln(os.Stderr, "q5:", err)
		os.Exit(1)
	}
	if err := srv.Serve(lis); err != nil {
		fmt.Fprintln(os.Stderr, "q5:", err)
		os.Exit(1)
	}
}
