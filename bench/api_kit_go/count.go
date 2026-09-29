package platformkit

import (
	"context"
	"database/sql/driver"
	"errors"
	"strings"
	"sync/atomic"

	"modernc.org/sqlite"
)

// countedStatement is bench.api_runner._is_counted_statement: a data statement;
// transaction control and pragmas are not counted. Words are split as Python's
// str.split() splits them.
func countedStatement(query string) bool {
	f := strings.FieldsFunc(query, pyIsSpace)
	if len(f) == 0 {
		return false
	}
	switch strings.ToUpper(f[0]) {
	case "SELECT", "INSERT", "UPDATE", "DELETE", "WITH", "REPLACE":
		return true
	}
	return false
}

// The counting driver wraps modernc.org/sqlite's. Its connections implement neither
// QueryerContext nor ExecerContext, so database/sql prepares every statement and runs
// it through countingStmt, which counts each execution of a data statement. That is
// what Python's sqlite3 trace callback counts: a statement is traced when it starts to
// run, so one that fails to prepare (a syntax error, an unknown table) is not counted
// and one that fails while running (a constraint violation) is.

type counter struct {
	dsn string
	drv driver.Driver
	n   *atomic.Int64
}

func newCounter(dsn string, n *atomic.Int64) *counter {
	return &counter{dsn: dsn, drv: &sqlite.Driver{}, n: n}
}

func (c *counter) Connect(context.Context) (driver.Conn, error) {
	conn, err := c.drv.Open(c.dsn)
	if err != nil {
		return nil, err
	}
	return &countingConn{inner: conn, n: c.n}, nil
}

func (c *counter) Driver() driver.Driver { return c.drv }

type countingConn struct {
	inner driver.Conn
	n     *atomic.Int64
}

func (c *countingConn) Prepare(query string) (driver.Stmt, error) {
	return c.PrepareContext(context.Background(), query)
}

func (c *countingConn) PrepareContext(ctx context.Context, query string) (driver.Stmt, error) {
	var st driver.Stmt
	var err error
	if p, ok := c.inner.(driver.ConnPrepareContext); ok {
		st, err = p.PrepareContext(ctx, query)
	} else {
		st, err = c.inner.Prepare(query)
	}
	if err != nil {
		return nil, err
	}
	return &countingStmt{inner: st, counted: countedStatement(query), n: c.n}, nil
}

func (c *countingConn) Close() error { return c.inner.Close() }

// Begin is driver.Conn's; database/sql calls BeginTx.
func (c *countingConn) Begin() (driver.Tx, error) { return c.inner.Begin() }

func (c *countingConn) BeginTx(ctx context.Context, opts driver.TxOptions) (driver.Tx, error) {
	return c.inner.(driver.ConnBeginTx).BeginTx(ctx, opts)
}

func (c *countingConn) ResetSession(ctx context.Context) error {
	return c.inner.(driver.SessionResetter).ResetSession(ctx)
}

func (c *countingConn) IsValid() bool { return c.inner.(driver.Validator).IsValid() }

func (c *countingConn) Ping(ctx context.Context) error {
	return c.inner.(driver.Pinger).Ping(ctx)
}

// countingStmt is a modernc statement (which has ExecContext and QueryContext), counted.
type countingStmt struct {
	inner   driver.Stmt
	counted bool
	n       *atomic.Int64
}

// ran counts one execution: it succeeded, or SQLite failed while running it (an error
// before it ran -- arguments database/sql could not convert -- is not a sqlite.Error).
func (s *countingStmt) ran(err error) {
	var se *sqlite.Error
	if s.counted && (err == nil || errors.As(err, &se)) {
		s.n.Add(1)
	}
}

func (s *countingStmt) Close() error  { return s.inner.Close() }
func (s *countingStmt) NumInput() int { return s.inner.NumInput() }

func (s *countingStmt) Exec(args []driver.Value) (driver.Result, error) {
	r, err := s.inner.Exec(args)
	s.ran(err)
	return r, err
}

func (s *countingStmt) Query(args []driver.Value) (driver.Rows, error) {
	r, err := s.inner.Query(args)
	s.ran(err)
	return r, err
}

func (s *countingStmt) ExecContext(ctx context.Context, args []driver.NamedValue) (driver.Result, error) {
	r, err := s.inner.(driver.StmtExecContext).ExecContext(ctx, args)
	s.ran(err)
	return r, err
}

func (s *countingStmt) QueryContext(ctx context.Context, args []driver.NamedValue) (driver.Rows, error) {
	r, err := s.inner.(driver.StmtQueryContext).QueryContext(ctx, args)
	s.ran(err)
	return r, err
}
