// Package legacy is imported by nobody.
package legacy

import "google.golang.org/grpc"

var table = map[string]*grpc.ServiceDesc{}

// Mount is referenced from nowhere.
func Mount(r grpc.ServiceRegistrar, name string) { r.RegisterService(table[name], nil) }
