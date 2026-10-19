// Package app is the composition root.
package app

import (
	"context"
	"database/sql"

	"google.golang.org/grpc"

	q5 "example.com/q5main/gen/sqlraw5/v1"
	"example.com/q5main/internal/queries"
	"example.com/q5main/internal/store"
)

// App holds what the handlers share.
type App struct {
	st    *store.Store
	q     *queries.Queries
	parts store.PartStore
}

type litImpl struct {
	q5.UnimplementedLitServer
	a *App
}

func (h *litImpl) Get(ctx context.Context, in *q5.Req) (*q5.Rep, error) {
	a := h.a
	name, err := a.st.Widget(ctx, in.Name)
	return &q5.Rep{Name: name}, err
}

func (h *litImpl) Put(ctx context.Context, in *q5.Req) (*q5.Rep, error) {
	a := h.a
	if err := a.st.AddWidget(ctx, in.Name, in.Name); err != nil {
		return nil, err
	}
	return &q5.Rep{Name: in.Name}, nil
}

type cstImpl struct {
	q5.UnimplementedCstServer
	a *App
}

func (h *cstImpl) Bump(ctx context.Context, in *q5.Req) (*q5.Rep, error) {
	a := h.a
	if err := a.st.Bump(ctx, in.Name); err != nil {
		return nil, err
	}
	return &q5.Rep{Name: in.Name}, nil
}

func (h *cstImpl) Drop(ctx context.Context, in *q5.Req) (*q5.Rep, error) {
	a := h.a
	if err := a.st.Drop(ctx, in.Name); err != nil {
		return nil, err
	}
	return &q5.Rep{Name: in.Name}, nil
}

type catImpl struct {
	q5.UnimplementedCatServer
	a *App
}

func (h *catImpl) Cat(ctx context.Context, in *q5.Req) (*q5.Rep, error) {
	a := h.a
	_, err := a.st.Order(ctx, in.Name)
	return &q5.Rep{Name: in.Name}, err
}

func (h *catImpl) Fmt(ctx context.Context, in *q5.Req) (*q5.Rep, error) {
	a := h.a
	if err := a.st.Stock(ctx, 3); err != nil {
		return nil, err
	}
	return &q5.Rep{Name: in.Name}, nil
}

func (h *catImpl) Dyn(ctx context.Context, in *q5.Req) (*q5.Rep, error) {
	a := h.a
	if err := a.st.Any(ctx, in.Name); err != nil {
		return nil, err
	}
	return &q5.Rep{Name: in.Name}, nil
}

type txnImpl struct {
	q5.UnimplementedTxnServer
	a *App
}

func (h *txnImpl) Move(ctx context.Context, in *q5.Req) (*q5.Rep, error) {
	a := h.a
	if err := a.st.Move(ctx, in.Name, in.Name, 1); err != nil {
		return nil, err
	}
	return &q5.Rep{Name: in.Name}, nil
}

type dbtxImpl struct {
	q5.UnimplementedDbtxServer
	a *App
}

func (h *dbtxImpl) Get(ctx context.Context, in *q5.Req) (*q5.Rep, error) {
	a := h.a
	name, err := a.q.GetThing(ctx, in.Name)
	return &q5.Rep{Name: name}, err
}

type ifcImpl struct {
	q5.UnimplementedIfcServer
	a *App
}

func (h *ifcImpl) Find(ctx context.Context, in *q5.Req) (*q5.Rep, error) {
	a := h.a
	name, err := a.parts.Find(ctx, in.Name)
	return &q5.Rep{Name: name}, err
}

type cteImpl struct {
	q5.UnimplementedCteServer
	a *App
}

func (h *cteImpl) Recent(ctx context.Context, in *q5.Req) (*q5.Rep, error) {
	a := h.a
	if err := a.st.Recent(ctx, in.Name); err != nil {
		return nil, err
	}
	return &q5.Rep{Name: in.Name}, nil
}

type upsImpl struct {
	q5.UnimplementedUpsServer
	a *App
}

func (h *upsImpl) Pref(ctx context.Context, in *q5.Req) (*q5.Rep, error) {
	a := h.a
	if err := a.st.SetPref(ctx, in.Name); err != nil {
		return nil, err
	}
	return &q5.Rep{Name: in.Name}, nil
}

func (h *upsImpl) Flag(ctx context.Context, in *q5.Req) (*q5.Rep, error) {
	a := h.a
	if err := a.st.SetFlag(ctx, in.Name); err != nil {
		return nil, err
	}
	return &q5.Rep{Name: in.Name}, nil
}

func (h *upsImpl) Cache(ctx context.Context, in *q5.Req) (*q5.Rep, error) {
	a := h.a
	if err := a.st.SetCache(ctx, in.Name); err != nil {
		return nil, err
	}
	return &q5.Rep{Name: in.Name}, nil
}

type subImpl struct {
	q5.UnimplementedSubServer
	a *App
}

func (h *subImpl) Nested(ctx context.Context, in *q5.Req) (*q5.Rep, error) {
	a := h.a
	if err := a.st.Nested(ctx); err != nil {
		return nil, err
	}
	return &q5.Rep{Name: in.Name}, nil
}

func (h *subImpl) Quoted(ctx context.Context, in *q5.Req) (*q5.Rep, error) {
	a := h.a
	if err := a.st.Quoted(ctx); err != nil {
		return nil, err
	}
	return &q5.Rep{Name: in.Name}, nil
}

func (h *subImpl) Purge(ctx context.Context, in *q5.Req) (*q5.Rep, error) {
	a := h.a
	if err := a.st.Purge(ctx); err != nil {
		return nil, err
	}
	return &q5.Rep{Name: in.Name}, nil
}

// Build assembles the server over db.
func Build(db *sql.DB) *grpc.Server {
	a := &App{st: store.New(db), q: queries.New(db), parts: &store.SQLParts{DB: db}}
	s := grpc.NewServer()
	q5.RegisterLitServer(s, &litImpl{a})
	q5.RegisterCstServer(s, &cstImpl{a})
	q5.RegisterCatServer(s, &catImpl{a})
	q5.RegisterTxnServer(s, &txnImpl{a})
	q5.RegisterDbtxServer(s, &dbtxImpl{a})
	q5.RegisterIfcServer(s, &ifcImpl{a})
	q5.RegisterCteServer(s, &cteImpl{a})
	q5.RegisterUpsServer(s, &upsImpl{a})
	q5.RegisterSubServer(s, &subImpl{a})
	return s
}
