// Package queries is sqlc-style: statements as constants over a DBTX.
package queries

import (
	"context"
	"database/sql"
)

// DBTX is what both *sql.DB and *sql.Tx satisfy.
type DBTX interface {
	ExecContext(context.Context, string, ...interface{}) (sql.Result, error)
	QueryContext(context.Context, string, ...interface{}) (*sql.Rows, error)
	QueryRowContext(context.Context, string, ...interface{}) *sql.Row
}

// Queries runs the statements.
type Queries struct{ db DBTX }

// New wraps db.
func New(db DBTX) *Queries { return &Queries{db: db} }

const getThing = `-- name: GetThing :one
SELECT id, name FROM things WHERE id = ?
`

// GetThing reads one thing.
func (q *Queries) GetThing(ctx context.Context, id string) (string, error) {
	row := q.db.QueryRowContext(ctx, getThing, id)
	var name string
	err := row.Scan(&name)
	return name, err
}
