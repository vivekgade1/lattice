package main

import "google.golang.org/grpc"

type config struct{ unary []grpc.UnaryServerInterceptor }

type option func(*config)

func withUnary(in ...grpc.UnaryServerInterceptor) option {
	return func(c *config) { c.unary = append(c.unary, in...) }
}

func (c *config) serverOptions() []grpc.ServerOption {
	return []grpc.ServerOption{grpc.ChainUnaryInterceptor(c.unary...)}
}
