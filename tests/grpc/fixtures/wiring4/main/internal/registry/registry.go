// Package registry lets feature packages offer themselves at init time.
package registry

import "google.golang.org/grpc"

// Installer puts a feature's services on r.
type Installer interface{ Install(r grpc.ServiceRegistrar) }

// InstallFunc adapts a function to Installer.
type InstallFunc func(r grpc.ServiceRegistrar)

// Install calls f.
func (f InstallFunc) Install(r grpc.ServiceRegistrar) { f(r) }

var features = map[string]Installer{}

// Offer records a feature under name.
func Offer(name string, in Installer) { features[name] = in }

// InstallAll installs every offered feature.
func InstallAll(r grpc.ServiceRegistrar) {
	for _, in := range features {
		in.Install(r)
	}
}
