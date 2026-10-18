package serve

import (
	"context"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	wv1 "example.com/wiringapp/gen/wiring/v1"
)

type alphaDesk struct {
	wv1.UnimplementedAlphaServer
	db *DB
}

func newAlpha(db *DB) *alphaDesk { return &alphaDesk{db: db} }

func (d *alphaDesk) Get(ctx context.Context, in *wv1.Req) (*wv1.Rep, error) {
	if in.Name == "" {
		return nil, status.Error(codes.InvalidArgument, "name required")
	}
	return &wv1.Rep{Name: in.Name}, nil
}

func (d *alphaDesk) Put(ctx context.Context, in *wv1.Req) (*wv1.Rep, error) {
	if in.Name == "" {
		return nil, status.Error(codes.InvalidArgument, "name required")
	}
	return &wv1.Rep{Name: in.Name}, nil
}

type betaDesk struct {
	wv1.UnimplementedBetaServer
	db *DB
}

func (b betaDesk) Get(ctx context.Context, in *wv1.Req) (*wv1.Rep, error) {
	if in.Name == "" {
		return nil, status.Error(codes.InvalidArgument, "name required")
	}
	return &wv1.Rep{Name: in.Name}, nil
}

type gammaDesk struct {
	wv1.UnimplementedGammaServer
	db *DB
}

// NewGamma answers the interface, not the type.
func NewGamma(db *DB) wv1.GammaServer { return &gammaDesk{db: db} }

func (g *gammaDesk) Get(ctx context.Context, in *wv1.Req) (*wv1.Rep, error) {
	if in.Name == "" {
		return nil, status.Error(codes.InvalidArgument, "name required")
	}
	return &wv1.Rep{Name: in.Name}, nil
}

func (g *gammaDesk) Put(ctx context.Context, in *wv1.Req) (*wv1.Rep, error) {
	if in.Name == "" {
		return nil, status.Error(codes.InvalidArgument, "name required")
	}
	return &wv1.Rep{Name: in.Name}, nil
}

type deltaCore struct {
	wv1.UnimplementedDeltaServer
	db *DB
}

// deltaDesk gets its methods from the embedded core.
type deltaDesk struct{ *deltaCore }

func (c *deltaCore) Get(ctx context.Context, in *wv1.Req) (*wv1.Rep, error) {
	if in.Name == "" {
		return nil, status.Error(codes.InvalidArgument, "name required")
	}
	return &wv1.Rep{Name: in.Name}, nil
}

type epsilonDesk struct {
	wv1.UnimplementedEpsilonServer
	db *DB
}

func (e *epsilonDesk) Get(ctx context.Context, in *wv1.Req) (*wv1.Rep, error) {
	if in.Name == "" {
		return nil, status.Error(codes.InvalidArgument, "name required")
	}
	return &wv1.Rep{Name: in.Name}, nil
}

func (e *epsilonDesk) Put(ctx context.Context, in *wv1.Req) (*wv1.Rep, error) {
	if in.Name == "" {
		return nil, status.Error(codes.InvalidArgument, "name required")
	}
	return &wv1.Rep{Name: in.Name}, nil
}

type zetaDesk struct {
	wv1.UnimplementedZetaServer
	db *DB
}

func (z zetaDesk) Get(ctx context.Context, in *wv1.Req) (*wv1.Rep, error) {
	if in.Name == "" {
		return nil, status.Error(codes.InvalidArgument, "name required")
	}
	return &wv1.Rep{Name: in.Name}, nil
}

func (z zetaDesk) Put(ctx context.Context, in *wv1.Req) (*wv1.Rep, error) {
	if in.Name == "" {
		return nil, status.Error(codes.InvalidArgument, "name required")
	}
	return &wv1.Rep{Name: in.Name}, nil
}

type etaDesk struct {
	wv1.UnimplementedEtaServer
	db *DB
}

func (e etaDesk) Get(ctx context.Context, in *wv1.Req) (*wv1.Rep, error) {
	if in.Name == "" {
		return nil, status.Error(codes.InvalidArgument, "name required")
	}
	return &wv1.Rep{Name: in.Name}, nil
}

func (e etaDesk) Put(ctx context.Context, in *wv1.Req) (*wv1.Rep, error) {
	if in.Name == "" {
		return nil, status.Error(codes.InvalidArgument, "name required")
	}
	return &wv1.Rep{Name: in.Name}, nil
}

type deadDesk struct {
	wv1.UnimplementedDeadServer
}

func (d deadDesk) Get(ctx context.Context, in *wv1.Req) (*wv1.Rep, error) {
	if in.Name == "" {
		return nil, status.Error(codes.InvalidArgument, "name required")
	}
	return &wv1.Rep{Name: in.Name}, nil
}

func (d deadDesk) Put(ctx context.Context, in *wv1.Req) (*wv1.Rep, error) {
	if in.Name == "" {
		return nil, status.Error(codes.InvalidArgument, "name required")
	}
	return &wv1.Rep{Name: in.Name}, nil
}

type goneDesk struct {
	wv1.UnimplementedGoneServer
}

func (d goneDesk) Get(ctx context.Context, in *wv1.Req) (*wv1.Rep, error) {
	if in.Name == "" {
		return nil, status.Error(codes.InvalidArgument, "name required")
	}
	return &wv1.Rep{Name: in.Name}, nil
}

func (d goneDesk) Put(ctx context.Context, in *wv1.Req) (*wv1.Rep, error) {
	if in.Name == "" {
		return nil, status.Error(codes.InvalidArgument, "name required")
	}
	return &wv1.Rep{Name: in.Name}, nil
}

type opaqueDesk struct {
	wv1.UnimplementedOpaqueServer
}

func (d opaqueDesk) Get(ctx context.Context, in *wv1.Req) (*wv1.Rep, error) {
	if in.Name == "" {
		return nil, status.Error(codes.InvalidArgument, "name required")
	}
	return &wv1.Rep{Name: in.Name}, nil
}

func (d opaqueDesk) Put(ctx context.Context, in *wv1.Req) (*wv1.Rep, error) {
	if in.Name == "" {
		return nil, status.Error(codes.InvalidArgument, "name required")
	}
	return &wv1.Rep{Name: in.Name}, nil
}
