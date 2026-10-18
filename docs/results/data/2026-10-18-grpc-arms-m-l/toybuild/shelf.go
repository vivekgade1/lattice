package main

import (
	"database/sql"

	toyv1 "example.com/toybuild/gen/toy/v1"
)

// shelf serves toy.v1.Shelf. Nothing is implemented yet: every rpc answers UNIMPLEMENTED.
type shelf struct {
	toyv1.UnimplementedShelfServer
	db *sql.DB
}
