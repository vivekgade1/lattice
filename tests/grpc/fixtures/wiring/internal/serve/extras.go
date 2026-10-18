package serve

import (
	"google.golang.org/grpc"

	wv1 "example.com/wiringapp/gen/wiring/v1"
)

var deltaDesc = &wv1.Delta_ServiceDesc

var extras = map[string]func(grpc.ServiceRegistrar, *DB){
	"delta": installDelta,
}

func installDelta(s grpc.ServiceRegistrar, db *DB) {
	s.RegisterService(deltaDesc, &deltaDesk{deltaCore: &deltaCore{db: db}})
}

func wireEpsilon(r grpc.ServiceRegistrar, db *DB) {
	wv1.RegisterEpsilonServer(r, &epsilonDesk{db: db})
}

func installZeta(r grpc.ServiceRegistrar, db *DB) {
	wv1.RegisterZetaServer(r, zetaDesk{db: db})
}
