package serve

import (
	"google.golang.org/grpc"

	wv1 "example.com/wiringapp/gen/wiring/v1"
)

// registerDead is called from nowhere.
func registerDead(r grpc.ServiceRegistrar) {
	wv1.RegisterDeadServer(r, deadDesk{})
}

// registerGone is called from serve_test.go only.
func registerGone(r grpc.ServiceRegistrar) {
	wv1.RegisterGoneServer(r, goneDesk{})
}
