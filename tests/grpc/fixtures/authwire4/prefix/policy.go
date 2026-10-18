package main

import "strings"

type policy struct {
	methods  map[string]bool
	services []string
}

func newPolicy(methods ...string) *policy {
	p := &policy{methods: map[string]bool{}}
	for _, m := range methods {
		p.methods[m] = true
	}
	return p
}

// AndService opens every method of the named service.
func (p *policy) AndService(name string) *policy {
	p.services = append(p.services, "/"+name+"/")
	return p
}

func (p *policy) allows(full string) bool {
	if p.methods[full] {
		return true
	}
	for _, pre := range p.services {
		if strings.HasPrefix(full, pre) {
			return true
		}
	}
	return false
}
