// Package store opens the SQLite database.
package store

import (
	"database/sql"

	_ "modernc.org/sqlite"
)

// Open opens the database file at path with one connection.
func Open(path string) (*sql.DB, error) {
	db, err := sql.Open("sqlite", "file:"+path+"?_pragma=busy_timeout(10000)")
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	return db, nil
}
