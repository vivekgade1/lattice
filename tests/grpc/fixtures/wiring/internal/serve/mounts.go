package serve

import (
	"context"

	"google.golang.org/grpc"

	wv1 "example.com/wiringapp/gen/wiring/v1"
)

// A mount attaches one slice of the app to the server.
type mount func(grpc.ServiceRegistrar, *DB)

var mounts = []mount{
	func(r grpc.ServiceRegistrar, db *DB) { wv1.RegisterAlphaServer(r, newAlpha(db)) },
	mountWorkshop,
}

func mountWorkshop(r grpc.ServiceRegistrar, db *DB) {
	wv1.RegisterBetaServer(r, &betaDesk{db: db})
	wv1.RegisterGammaServer(r, NewGamma(db))
	// the gateway of Beta: reachability for it stays the HTTP walker's (D1.0)
	_ = wv1.RegisterBetaHandlerFromEndpoint(context.Background(), nil, "127.0.0.1:0", nil)
}
