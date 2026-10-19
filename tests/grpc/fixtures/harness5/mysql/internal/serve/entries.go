package serve

import (
	"context"
	"database/sql"
	"strings"

	ledgerv1 "example.com/ledger5mysql/gen/ledger/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// serial is larger than 2^53, so a client sees it as a decimal string.
const serial int64 = 9007199254740993

// Entries serves ledger.v1.Entries; Audit and Watch are left to the embedded server.
type Entries struct {
	ledgerv1.UnimplementedEntriesServer
	DB *sql.DB
}

func (e *Entries) load(ctx context.Context, id int64) (*ledgerv1.Entry, error) {
	var out ledgerv1.Entry
	var tags string
	err := e.DB.QueryRowContext(ctx, "SELECT id, title, owner, state, tags FROM entries WHERE id = ?", id).Scan(&out.Id, &out.Title, &out.Owner, &out.State, &tags)
	if err == sql.ErrNoRows {
		return nil, status.Error(codes.NotFound, "no such entry")
	}
	if err != nil {
		return nil, status.Error(codes.Internal, "load")
	}
	out.Serial = serial
	if tags != "" {
		out.Tags = strings.Split(tags, ",")
	}
	return &out, nil
}

// Create adds an entry owned by the caller.
func (e *Entries) Create(ctx context.Context, req *ledgerv1.CreateRequest) (*ledgerv1.Entry, error) {
	if req.Title == "" {
		return nil, status.Error(codes.InvalidArgument, "title is required")
	}
	var taken int
	_ = e.DB.QueryRowContext(ctx, "SELECT COUNT(*) FROM entries WHERE owner = ? AND title = ?", caller(ctx), req.Title).Scan(&taken)
	if taken > 0 {
		return nil, status.Error(codes.AlreadyExists, "title taken")
	}
	res, err := e.DB.ExecContext(ctx, "INSERT INTO entries (title, owner, state, tags) VALUES (?, ?, 'OPEN', ?)", req.Title, caller(ctx), strings.Join(req.Tags, ","))
	if err != nil {
		return nil, status.Error(codes.Internal, "insert")
	}
	id, _ := res.LastInsertId()
	return e.load(ctx, id)
}

// Get answers one entry.
func (e *Entries) Get(ctx context.Context, req *ledgerv1.GetRequest) (*ledgerv1.Entry, error) {
	return e.load(ctx, req.Id)
}

// List answers the caller's entries.
func (e *Entries) List(ctx context.Context, req *ledgerv1.ListRequest) (*ledgerv1.ListResponse, error) {
	rows, err := e.DB.QueryContext(ctx, "SELECT id FROM entries WHERE owner = ? ORDER BY id", caller(ctx))
	if err != nil {
		return nil, status.Error(codes.Internal, "list")
	}
	var ids []int64
	for rows.Next() {
		var id int64
		if err := rows.Scan(&id); err != nil {
			rows.Close()
			return nil, status.Error(codes.Internal, "scan")
		}
		ids = append(ids, id)
	}
	rows.Close()
	out := &ledgerv1.ListResponse{}
	for _, id := range ids {
		en, err := e.load(ctx, id)
		if err != nil {
			return nil, err
		}
		out.Entries = append(out.Entries, en)
	}
	return out, nil
}

// Close closes an open entry.
func (e *Entries) Close(ctx context.Context, req *ledgerv1.CloseRequest) (*ledgerv1.Entry, error) {
	cur, err := e.load(ctx, req.Id)
	if err != nil {
		return nil, err
	}
	if cur.State != "OPEN" {
		return nil, status.Error(codes.FailedPrecondition, "entry is not open")
	}
	if _, err := e.DB.ExecContext(ctx, "UPDATE entries SET state = 'CLOSED' WHERE id = ?", req.Id); err != nil {
		return nil, status.Error(codes.Internal, "update")
	}
	return e.load(ctx, req.Id)
}
