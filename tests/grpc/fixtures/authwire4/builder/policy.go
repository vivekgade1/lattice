package main

type policy struct{ open map[string]bool }

func newPolicy(methods ...string) *policy {
	p := &policy{open: map[string]bool{}}
	for _, m := range methods {
		p.open[m] = true
	}
	return p
}

// Also opens one more method.
func (p *policy) Also(method string) *policy {
	p.open[method] = true
	return p
}

func (p *policy) allows(method string) bool { return p.open[method] }
