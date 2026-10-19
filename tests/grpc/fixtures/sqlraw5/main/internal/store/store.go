// Package store runs the SQL.
package store

import (
	"context"
	"database/sql"
	"fmt"
)

// Store wraps the database handle.
type Store struct{ db *sql.DB }

// New opens a Store on db.
func New(db *sql.DB) *Store { return &Store{db: db} }

const bump = "UPDATE counters SET n = n + 1 WHERE k = ?"

const dropSession = `DELETE FROM sessions WHERE token = ?`

// Widget reads one widget.
func (s *Store) Widget(ctx context.Context, id string) (string, error) {
	var name string
	err := s.db.QueryRowContext(ctx, "SELECT name FROM widgets WHERE id = ?", id).Scan(&name)
	return name, err
}

// AddWidget inserts one widget.
func (s *Store) AddWidget(ctx context.Context, id, name string) error {
	_, err := s.db.ExecContext(ctx, "INSERT INTO widgets (id, name) VALUES (?, ?)", id, name)
	return err
}

// Bump increments a counter.
func (s *Store) Bump(ctx context.Context, k string) error {
	_, err := s.db.ExecContext(ctx, bump, k)
	return err
}

// Drop deletes a session.
func (s *Store) Drop(ctx context.Context, token string) error {
	_, err := s.db.Exec(dropSession, token)
	return err
}

// Order reads an order total; the table name is spelled as a concatenation.
func (s *Store) Order(ctx context.Context, id string) (int, error) {
	var total int
	err := s.db.QueryRowContext(ctx, "SELECT total FROM "+"orders"+" WHERE id = ?", id).Scan(&total)
	return total, err
}

// Stock formats a number into the statement; the table is literal.
func (s *Store) Stock(ctx context.Context, min int) error {
	rows, err := s.db.QueryContext(ctx, fmt.Sprintf("SELECT id FROM items WHERE qty > %d", min))
	if err != nil {
		return err
	}
	return rows.Close()
}

// Any reads whatever table it is given: not a literal statement.
func (s *Store) Any(ctx context.Context, table string) error {
	rows, err := s.db.QueryContext(ctx, fmt.Sprintf("SELECT id FROM %s", table))
	if err != nil {
		return err
	}
	return rows.Close()
}

func (s *Store) inTx(ctx context.Context, f func(tx *sql.Tx) error) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	if err := f(tx); err != nil {
		_ = tx.Rollback()
		return err
	}
	return tx.Commit()
}

// Move transfers an amount between accounts inside one transaction.
func (s *Store) Move(ctx context.Context, from, to string, amt int) error {
	return s.inTx(ctx, func(tx *sql.Tx) error {
		if _, err := tx.ExecContext(ctx, "UPDATE accounts SET bal = bal - ? WHERE id = ?", amt, from); err != nil {
			return err
		}
		_, err := tx.ExecContext(ctx, "INSERT INTO ledger (src, dst, amt) VALUES (?, ?, ?)", from, to, amt)
		return err
	})
}

// Recent reads through a common table expression.
func (s *Store) Recent(ctx context.Context, since string) error {
	rows, err := s.db.QueryContext(ctx, `WITH recent AS (SELECT id FROM events WHERE ts > ?)
SELECT u.name FROM recent r JOIN users u ON u.id = r.id`, since)
	if err != nil {
		return err
	}
	return rows.Close()
}

// SetPref upserts a preference (SQLite spelling).
func (s *Store) SetPref(ctx context.Context, k string) error {
	_, err := s.db.ExecContext(ctx, "INSERT INTO prefs (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", k, k)
	return err
}

// SetFlag upserts a flag (MySQL spelling).
func (s *Store) SetFlag(ctx context.Context, k string) error {
	_, err := s.db.ExecContext(ctx, "INSERT INTO flags (k, v) VALUES (?, ?) ON DUPLICATE KEY UPDATE v = VALUES(v)", k, k)
	return err
}

// SetCache replaces a cache entry.
func (s *Store) SetCache(ctx context.Context, k string) error {
	_, err := s.db.ExecContext(ctx, "REPLACE INTO caches (k, v) VALUES (?, ?)", k, k)
	return err
}

// Nested reads with a subquery.
func (s *Store) Nested(ctx context.Context) error {
	rows, err := s.db.QueryContext(ctx, "SELECT id FROM alpha WHERE id IN (SELECT alpha_id FROM beta)")
	if err != nil {
		return err
	}
	return rows.Close()
}

// Quoted reads with quoted and schema-qualified names.
func (s *Store) Quoted(ctx context.Context) error {
	rows, err := s.db.QueryContext(ctx, `SELECT u.name FROM public."Users" u JOIN "order_lines" l ON l.uid = u.id`)
	if err != nil {
		return err
	}
	return rows.Close()
}

// Purge deletes expired sessions.
func (s *Store) Purge(ctx context.Context) error {
	_, err := s.db.ExecContext(ctx, "DELETE FROM sessions WHERE exp < 100")
	return err
}

// PartStore finds parts.
type PartStore interface {
	Find(ctx context.Context, id string) (string, error)
}

// SQLParts is the database-backed PartStore.
type SQLParts struct{ DB *sql.DB }

// Find reads one part.
func (p *SQLParts) Find(ctx context.Context, id string) (string, error) {
	var name string
	err := p.DB.QueryRowContext(ctx, "SELECT name FROM parts WHERE id = ?", id).Scan(&name)
	return name, err
}

// MemParts is a PartStore nothing builds in the server.
type MemParts struct{ DB *sql.DB }

// Find reads a table the server never touches.
func (p *MemParts) Find(ctx context.Context, id string) (string, error) {
	var name string
	err := p.DB.QueryRowContext(ctx, "SELECT name FROM ghost WHERE id = ?", id).Scan(&name)
	return name, err
}
