package serve

import (
	"google.golang.org/grpc"

	wv1 "example.com/wiringapp/gen/wiring/v1"
)

// DB stands in for the ledger.
type DB struct{ path string }

// app is one named slice of the server and the function that wires it.
type app struct {
	name string
	wire func(grpc.ServiceRegistrar, *DB)
}

// bundle holds the registrar it was given.
type bundle struct{ reg grpc.ServiceRegistrar }

func (b *bundle) addEta(db *DB) {
	wv1.RegisterEtaServer(b.reg, etaDesk{db: db})
}

func with(s grpc.ServiceRegistrar, db *DB, fns ...func(grpc.ServiceRegistrar, *DB)) {
	for _, f := range fns {
		f(s, db)
	}
}

// Build assembles the server over db.
func Build(db *DB) *grpc.Server {
	s := grpc.NewServer()
	for _, m := range mounts {
		m(s, db)
	}
	for _, f := range extras {
		f(s, db)
	}
	for _, a := range []app{{name: "epsilon", wire: wireEpsilon}} {
		a.wire(s, db)
	}
	with(s, db, installZeta)
	b := &bundle{reg: s}
	b.addEta(db)
	for _, p := range plugins {
		p.Install(s)
	}
	return s
}
