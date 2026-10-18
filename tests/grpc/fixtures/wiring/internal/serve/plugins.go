package serve

import (
	"google.golang.org/grpc"

	wv1 "example.com/wiringapp/gen/wiring/v1"
)

// installer is what a plugin offers; the plugins are loaded from configuration at start-up.
type installer interface{ Install(grpc.ServiceRegistrar) }

var plugins []installer

type plugA struct{}

func (plugA) Install(r grpc.ServiceRegistrar) {
	wv1.RegisterOpaqueServer(r, opaqueDesk{})
}

type plugB struct{}

func (plugB) Install(r grpc.ServiceRegistrar) {
	_ = r
}
