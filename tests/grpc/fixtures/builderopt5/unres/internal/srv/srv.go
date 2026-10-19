// Package srv assembles the *grpc.Server from functional options.
package srv

import "google.golang.org/grpc"

type entry struct {
	desc *grpc.ServiceDesc
	impl any
}

type settings struct {
	services []entry
	parked   []entry
	unary    []grpc.UnaryServerInterceptor
}

// Option configures New.
type Option func(*settings)

// WithService asks New to register impl under desc once the server exists.
func WithService(desc *grpc.ServiceDesc, impl any) Option {
	return func(st *settings) { st.services = append(st.services, entry{desc, impl}) }
}

// WithParked keeps a service for later: nothing in New uses it.
func WithParked(desc *grpc.ServiceDesc, impl any) Option {
	return func(st *settings) { st.parked = append(st.parked, entry{desc: desc, impl: impl}) }
}

// WithUnary adds an interceptor.
func WithUnary(u grpc.UnaryServerInterceptor) Option {
	return func(st *settings) { st.unary = append(st.unary, u) }
}

// New builds the server and registers the services given by WithService.
func New(opts ...Option) *grpc.Server {
	var st settings
	for _, o := range opts {
		o(&st)
	}
	gs := grpc.NewServer(grpc.ChainUnaryInterceptor(st.unary...))
	for _, e := range st.services {
		gs.RegisterService(e.desc, e.impl)
	}
	return gs
}
