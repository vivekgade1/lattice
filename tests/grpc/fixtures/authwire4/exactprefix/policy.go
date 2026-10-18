package main

type policy struct{ prefixes []string }

func newPolicy() *policy { return &policy{} }

// AllowPrefix lists a name that opens a call.
func (p *policy) AllowPrefix(pre string) *policy {
	p.prefixes = append(p.prefixes, pre)
	return p
}

func (p *policy) allows(full string) bool {
	for _, pre := range p.prefixes {
		if full == pre {
			return true
		}
	}
	return false
}
