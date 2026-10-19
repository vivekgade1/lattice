// Package store opens the MySQL database.
package store

import (
	"database/sql"

	_ "github.com/go-sql-driver/mysql"
)

// Open opens the database named by the driver DSN with a small pool.
func Open(dsn string) (*sql.DB, error) {
	db, err := sql.Open("mysql", dsn)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(4)
	return db, nil
}
