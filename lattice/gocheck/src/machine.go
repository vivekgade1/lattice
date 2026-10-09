// GO808 state machines: a state written from a guarded state with no edge to it.
package main

import (
	"fmt"
	"go/ast"
	"go/token"
	"regexp"
	"sort"
	"strings"
)

type machine struct {
	table  string
	column string
	edges  map[[2]string]bool
	states []string
}

func (mc *machine) legal(a, b string) bool { return a == b || mc.edges[[2]string{a, b}] }

// mfield is a machine's column on one struct.
type mfield struct {
	mach  *machine
	field string
	md    *Model
}

type stateSet map[string]bool

func (s stateSet) sorted() []string {
	out := make([]string, 0, len(s))
	for k := range s {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func (s stateSet) and(o stateSet) stateSet {
	out := stateSet{}
	for k := range s {
		if o[k] {
			out[k] = true
		}
	}
	return out
}

func (s stateSet) or(o stateSet) stateSet {
	out := stateSet{}
	for k := range s {
		out[k] = true
	}
	for k := range o {
		out[k] = true
	}
	return out
}

func setOf(xs ...string) stateSet {
	out := stateSet{}
	for _, x := range xs {
		out[x] = true
	}
	return out
}

type kn struct {
	states stateSet
	line   int
}

// known is, for a name holding a machine row, the states a dominating check leaves it in.
type known map[string]kn

func (k known) clone() known {
	if k == nil {
		return nil
	}
	c := known{}
	for a, b := range k {
		c[a] = b
	}
	return c
}

func joinKnown(bs ...known) known {
	var live []known
	for _, b := range bs {
		if b != nil {
			live = append(live, b)
		}
	}
	if len(live) == 0 {
		return nil
	}
	out := known{}
	for name, v := range live[0] {
		states, line, all := v.states, v.line, true
		for _, b := range live[1:] {
			o, ok := b[name]
			if !ok {
				all = false
				break
			}
			states = states.or(o.states)
			if o.line > line {
				line = o.line
			}
		}
		if all {
			out[name] = kn{states, line}
		}
	}
	return out
}

type tableAlias struct {
	row string
	mf  *mfield
}

type okAlias struct {
	row    string
	mf     *mfield
	target string
}

// ms walks one function for GO808.
type ms struct {
	c      *checker
	m      *Module
	fn     *Func
	env    *Env
	byType map[*TypeInfo]*mfield
	all    []*mfield
	tables map[string]tableAlias
	oks    map[string]okAlias
	judged map[ast.Node]bool
	// a row name -> the id expression it was fetched by (First(&cur, id))
	fetched map[string]string
}

func (c *checker) machineFields() []*mfield {
	var out []*mfield
	for key, spec := range c.pol.Machines {
		table := key
		if _, ok := c.pol.Tables[key]; !ok {
			for name, t := range c.pol.Tables {
				if t.SQL == key {
					table = name
				}
			}
		}
		mc := &machine{table: table, column: spec.Column, edges: map[[2]string]bool{}}
		seen := map[string]bool{}
		for _, e := range spec.Edges {
			mc.edges[e] = true
			for _, s := range e {
				if !seen[s] {
					seen[s] = true
					mc.states = append(mc.states, s)
				}
			}
		}
		sort.Strings(mc.states)
		for _, md := range c.modelsOfTable(table) {
			if f, ok := md.FieldFor(spec.Column); ok {
				out = append(out, &mfield{mach: mc, field: f.name, md: md})
			}
		}
	}
	return out
}

func (c *checker) checkMachines() {
	fields := c.machineFields()
	if len(fields) == 0 {
		return
	}
	byType := map[*TypeInfo]*mfield{}
	for _, f := range fields {
		byType[f.md.TI] = f
	}
	for _, fn := range c.m.AllFuncs() {
		s := &ms{c: c, m: c.m, fn: fn, env: c.m.EnvOf(fn), byType: byType, all: fields,
			tables: map[string]tableAlias{}, oks: map[string]okAlias{}, judged: map[ast.Node]bool{},
			fetched: map[string]string{}}
		s.block(fn.Body.List, known{})
	}
}

// -- reading the code --

// rowOf answers the machine field of a name holding a row with field `field`.
func (s *ms) rowOf(x ast.Expr, field string) (string, *mfield) {
	id, ok := unparen(x).(*ast.Ident)
	if !ok {
		return "", nil
	}
	t := s.m.Infer(s.env, id)
	if ti := t.deref().rowTypeAny(); ti != nil {
		if mf := s.byType[ti]; mf != nil && mf.field == field {
			return id.Name, mf
		}
		return "", nil
	}
	if t.Kind != "unknown" {
		return "", nil
	}
	var found *mfield
	for _, mf := range s.all {
		if mf.field == field {
			if found != nil && found.mach != mf.mach {
				return "", nil
			}
			found = mf
		}
	}
	if found == nil {
		return "", nil
	}
	return id.Name, found
}

// subject answers `row.Status` (or `string(row.Status)`) of a machine row.
func (s *ms) subject(x ast.Expr) (string, *mfield) {
	x = unparen(x)
	if call, ok := x.(*ast.CallExpr); ok && len(call.Args) == 1 {
		if id, ok := call.Fun.(*ast.Ident); ok && id.Name == "string" {
			x = unparen(call.Args[0])
		}
	}
	sel, ok := x.(*ast.SelectorExpr)
	if !ok {
		return "", nil
	}
	return s.rowOf(sel.X, sel.Sel.Name)
}

// state answers the machine state a constant spells (case-insensitively), or "".
func (s *ms) state(x ast.Expr, mf *mfield) string {
	v, ok := s.m.constString(s.env, x)
	if !ok {
		return ""
	}
	hit := ""
	for _, st := range mf.mach.states {
		if strings.EqualFold(st, v) {
			if hit != "" {
				return ""
			}
			hit = st
		}
	}
	return hit
}

// statesOf answers the states of a list literal (`[]string{"a", "b"}`), or nil.
func (s *ms) statesOf(x ast.Expr, mf *mfield) stateSet {
	var file *File // where the elements are read (a package var's own file)
	cl, ok := unparen(x).(*ast.CompositeLit)
	if !ok {
		// a package-level `var live = []string{A, B}`
		if v, f := s.pkgVarValue(x); v != nil {
			cl, ok = unparen(v).(*ast.CompositeLit)
			file = f
		}
		if !ok || cl == nil {
			return nil
		}
	}
	if file != nil {
		out := stateSet{}
		for _, e := range cl.Elts {
			v, ok := s.m.constStringDepth(file, nil, e, 4)
			st := ""
			if ok {
				for _, cand := range mf.mach.states {
					if strings.EqualFold(cand, v) {
						st = cand
					}
				}
			}
			if st == "" {
				return nil
			}
			out[st] = true
		}
		return out
	}
	out := stateSet{}
	for _, e := range cl.Elts {
		st := s.state(e, mf)
		if st == "" {
			return nil
		}
		out[st] = true
	}
	return out
}

// pkgVarValue answers the value of a package-level var a name or pkg.Name refers to.
func (s *ms) pkgVarValue(x ast.Expr) (ast.Expr, *File) {
	switch v := unparen(x).(type) {
	case *ast.Ident:
		if _, local := s.env.lookup(v.Name); local {
			return nil, nil
		}
		if vi, ok := s.fn.File.Pkg.Vars[v.Name]; ok {
			return vi.value, vi.file
		}
	case *ast.SelectorExpr:
		if path, ok := s.m.importOf(s.env, v.X); ok {
			if pkg := s.m.ByImport[path]; pkg != nil {
				if vi, ok := pkg.Vars[v.Sel.Name]; ok {
					return vi.value, vi.file
				}
			}
		}
	}
	return nil, nil
}

func (s *ms) complement(mf *mfield, x stateSet) stateSet {
	out := stateSet{}
	for _, st := range mf.mach.states {
		if !x[st] {
			out[st] = true
		}
	}
	return out
}

func (s *ms) fullGuard(mf *mfield, target string) stateSet {
	out := stateSet{}
	for _, st := range mf.mach.states {
		if mf.mach.legal(st, target) {
			out[st] = true
		}
	}
	return out
}

// tableLookup answers the row whose allowed next states `x` holds: `T[row.Status]`, or
// a name assigned one.
func (s *ms) tableLookup(x ast.Expr) (string, *mfield) {
	switch v := unparen(x).(type) {
	case *ast.IndexExpr:
		return s.subject(v.Index)
	case *ast.Ident:
		if a, ok := s.tables[v.Name]; ok {
			return a.row, a.mf
		}
	}
	return "", nil
}

// mentions answers the machine rows an expression reads or hands on.
func (s *ms) mentions(n ast.Node) map[string]bool {
	out := map[string]bool{}
	ast.Inspect(n, func(x ast.Node) bool {
		switch v := x.(type) {
		case *ast.FuncLit:
			return false
		case *ast.SelectorExpr:
			if name, mf := s.subject(v); mf != nil {
				out[name] = true
			}
		case *ast.Ident:
			t := s.m.Infer(s.env, v)
			if ti := t.deref().rowTypeAny(); ti != nil && s.byType[ti] != nil {
				out[v.Name] = true
			}
		}
		return true
	})
	return out
}

// constraint answers, for each machine row, the states it can be in when cond is
// `truth`, and the rows cond tests in a way the checker cannot read.
func (s *ms) constraint(cond ast.Expr, truth bool) (map[string]stateSet, map[string]bool) {
	none := map[string]stateSet{}
	switch v := unparen(cond).(type) {
	case *ast.UnaryExpr:
		if v.Op == token.NOT {
			return s.constraint(v.X, !truth)
		}
	case *ast.BinaryExpr:
		switch v.Op {
		case token.LAND, token.LOR:
			a, ua := s.constraint(v.X, truth)
			b, ub := s.constraint(v.Y, truth)
			unrec := map[string]bool{}
			for k := range ua {
				unrec[k] = true
			}
			for k := range ub {
				unrec[k] = true
			}
			out := map[string]stateSet{}
			if (v.Op == token.LAND) == truth { // every operand holds
				for k, x := range a {
					out[k] = x
				}
				for k, x := range b {
					if o, ok := out[k]; ok {
						out[k] = o.and(x)
					} else {
						out[k] = x
					}
				}
			} else { // some operand holds
				for k, x := range a {
					if y, ok := b[k]; ok {
						out[k] = x.or(y)
					}
				}
			}
			return out, unrec
		case token.EQL, token.NEQ:
			positive := truth == (v.Op == token.EQL)
			for _, pair := range [][2]ast.Expr{{v.X, v.Y}, {v.Y, v.X}} {
				name, mf := s.subject(pair[0])
				if mf == nil {
					continue
				}
				st := s.state(pair[1], mf)
				if st == "" {
					return none, map[string]bool{name: true}
				}
				if positive {
					return map[string]stateSet{name: setOf(st)}, nil
				}
				return map[string]stateSet{name: s.complement(mf, setOf(st))}, nil
			}
			// `T[row.Status][X] == true` and the like: not read
		}
	case *ast.CallExpr:
		// slices.Contains(list, row.Status) / slices.Contains(T[row.Status], X)
		if sel, ok := v.Fun.(*ast.SelectorExpr); ok && sel.Sel.Name == "Contains" && len(v.Args) == 2 {
			if x, ok := sel.X.(*ast.Ident); ok && (x.Name == "slices" || x.Name == "lo") {
				if name, mf := s.subject(v.Args[1]); mf != nil {
					if allowed := s.statesOf(v.Args[0], mf); allowed != nil {
						if truth {
							return map[string]stateSet{name: allowed}, nil
						}
						return map[string]stateSet{name: s.complement(mf, allowed)}, nil
					}
					return none, map[string]bool{name: true}
				}
				if name, mf := s.tableLookup(v.Args[0]); mf != nil {
					if target := s.state(v.Args[1], mf); target != "" {
						if truth {
							return map[string]stateSet{name: s.fullGuard(mf, target)}, nil
						}
						return none, nil
					}
					return none, map[string]bool{name: true}
				}
			}
		}
	case *ast.IndexExpr:
		// T[row.Status][X] / allowed[X] (a map of booleans)
		if name, mf := s.tableLookup(v.X); mf != nil {
			if target := s.state(v.Index, mf); target != "" {
				if truth {
					return map[string]stateSet{name: s.fullGuard(mf, target)}, nil
				}
				return none, nil
			}
			return none, map[string]bool{name: true}
		}
	case *ast.Ident:
		if a, ok := s.oks[v.Name]; ok {
			if truth {
				return map[string]stateSet{a.row: s.fullGuard(a.mf, a.target)}, nil
			}
			return none, nil
		}
	}
	return none, s.mentions(cond)
}

// -- the walk --

func (s *ms) apply(k known, c map[string]stateSet, unrec map[string]bool, line int) known {
	out := k.clone()
	for name := range unrec {
		delete(out, name)
	}
	for name, v := range c {
		if unrec[name] {
			continue
		}
		if old, ok := out[name]; ok {
			out[name] = kn{old.states.and(v), line}
		} else {
			out[name] = kn{v, line}
		}
	}
	return out
}

// touched answers the names a node may rebind, or whose row it may change.
func (s *ms) touched(n ast.Node) map[string]bool {
	out := map[string]bool{}
	if n == nil {
		return out
	}
	ast.Inspect(n, func(x ast.Node) bool {
		switch v := x.(type) {
		case *ast.AssignStmt:
			for _, l := range v.Lhs {
				switch t := l.(type) {
				case *ast.Ident:
					out[t.Name] = true
				case *ast.SelectorExpr:
					if id := rootIdent(t.X); id != "" {
						out[id] = true
					}
				case *ast.IndexExpr:
					if id := rootIdent(t.X); id != "" {
						out[id] = true
					}
				}
			}
		case *ast.IncDecStmt:
			if id := rootIdent(v.X); id != "" {
				out[id] = true
			}
		case *ast.RangeStmt:
			if id := rootIdent(v.Key); id != "" {
				out[id] = true
			}
			if id := rootIdent(v.Value); id != "" {
				out[id] = true
			}
		case *ast.ValueSpec:
			for _, nm := range v.Names {
				out[nm.Name] = true
			}
		case *ast.UnaryExpr:
			if v.Op == token.AND {
				if id := rootIdent(v.X); id != "" {
					out[id] = true
				}
			}
		case *ast.CallExpr:
			if s.harmless(v) {
				return true
			}
			for _, a := range v.Args {
				if id := rootIdent(a); id != "" {
					out[id] = true
				}
			}
			if sel, ok := v.Fun.(*ast.SelectorExpr); ok {
				if id := rootIdent(sel.X); id != "" && !s.m.isGorm(s.env, sel.X) && !s.m.isCtx(s.env, sel.X) {
					if _, pkg := s.m.importOf(s.env, sel.X); !pkg {
						out[id] = true
					}
				}
			}
		}
		return true
	})
	return out
}

// harmless reports whether a call cannot change a row handed to it.
func (s *ms) harmless(c *ast.CallExpr) bool {
	sel, ok := c.Fun.(*ast.SelectorExpr)
	if !ok {
		if id, ok := c.Fun.(*ast.Ident); ok {
			switch id.Name {
			case "len", "string", "panic", "print", "println", "cap":
				return true
			}
		}
		return false
	}
	if s.m.isCtx(s.env, sel.X) {
		return refusalMethods[sel.Sel.Name] || sel.Sel.Name == "Param" || sel.Sel.Name == "Query"
	}
	if path, ok := s.m.importOf(s.env, sel.X); ok {
		switch path {
		case "fmt", "log", "errors", "strings", "strconv", "slices", "time", "log/slog":
			return true
		}
	}
	return false
}

func (s *ms) forget(k known, n ast.Node) known {
	if k == nil {
		return nil
	}
	out := k.clone()
	for name := range s.touched(n) {
		delete(out, name)
		delete(s.fetched, name)
	}
	for name := range s.rebound(n) {
		for row, id := range s.fetched {
			if id == name || strings.HasPrefix(id, name+".") {
				delete(s.fetched, row)
			}
		}
	}
	s.recordFetch(n)
	return out
}

// rebound answers the names a node assigns (or takes the address of).
func (s *ms) rebound(n ast.Node) map[string]bool {
	out := map[string]bool{}
	if n == nil {
		return out
	}
	ast.Inspect(n, func(x ast.Node) bool {
		switch v := x.(type) {
		case *ast.FuncLit:
			return false
		case *ast.AssignStmt:
			for _, l := range v.Lhs {
				if id := rootIdent(l); id != "" {
					out[id] = true
				}
			}
		case *ast.IncDecStmt:
			if id := rootIdent(v.X); id != "" {
				out[id] = true
			}
		case *ast.UnaryExpr:
			if v.Op == token.AND {
				if id := rootIdent(v.X); id != "" {
					out[id] = true
				}
			}
		case *ast.RangeStmt:
			for _, e := range []ast.Expr{v.Key, v.Value} {
				if id := rootIdent(e); id != "" {
					out[id] = true
				}
			}
		}
		return true
	})
	return out
}

// simpleRef answers the text of a name or a field of one (`id`, `req.ID`), else "".
func simpleRef(x ast.Expr) string {
	switch v := unparen(x).(type) {
	case *ast.Ident:
		return v.Name
	case *ast.SelectorExpr:
		if inner := simpleRef(v.X); inner != "" {
			return inner + "." + v.Sel.Name
		}
	}
	return ""
}

// recordFetch records `db.First(&row, id)` / `db.Where("id = ?", id).First(&row)`.
func (s *ms) recordFetch(n ast.Node) {
	if n == nil {
		return
	}
	ast.Inspect(n, func(x ast.Node) bool {
		if _, ok := x.(*ast.FuncLit); ok {
			return false
		}
		call, ok := x.(*ast.CallExpr)
		if !ok {
			return true
		}
		links, _ := s.m.gormChain(s.env, call)
		if links == nil {
			return true
		}
		last := links[len(links)-1]
		switch last.name {
		case "First", "Take", "Last":
		default:
			return true
		}
		if len(last.call.Args) == 0 {
			return true
		}
		row, ok := stripAddr(last.call.Args[0]).(*ast.Ident)
		if !ok {
			return true
		}
		id := ""
		if len(last.call.Args) == 2 {
			id = simpleRef(last.call.Args[1])
		} else if len(last.call.Args) == 1 {
			for _, l := range links[:len(links)-1] {
				if l.name == "Where" {
					if arg := whereIDArg(s, l.call); arg != nil {
						id = simpleRef(arg)
					}
				}
			}
		}
		if id != "" {
			s.fetched[row.Name] = id
		}
		return true
	})
}

var idConj = regexp.MustCompile(`(?i)^\(?\s*(?:[\w"` + "`" + `]+\.)?["` + "`" + `]?id["` + "`" + `]?\s*=\s*\?\s*\)?$`)

// whereIDArg answers the argument a Where's `id = ?` conjunct takes, or nil.
func whereIDArg(s *ms, call *ast.CallExpr) ast.Expr {
	if len(call.Args) < 2 {
		return nil
	}
	q, ok := s.m.constString(s.env, call.Args[0])
	if !ok || containsWord(strings.ToLower(q), "or") {
		return nil
	}
	placeholder := 0
	for _, part := range andSplit.Split(strings.TrimSpace(q), -1) {
		part = strings.TrimSpace(part)
		if idConj.MatchString(part) {
			if 1+placeholder < len(call.Args) {
				return call.Args[1+placeholder]
			}
			return nil
		}
		placeholder += strings.Count(part, "?")
	}
	return nil
}

func (s *ms) block(list []ast.Stmt, k known) known {
	for _, st := range list {
		if k == nil {
			return nil
		}
		k = s.stmt(st, k)
	}
	return k
}

func (s *ms) stmt(st ast.Stmt, k known) known {
	switch v := st.(type) {
	case *ast.BlockStmt:
		return s.block(v.List, k)
	case *ast.ReturnStmt:
		s.writes(v, k)
		return nil
	case *ast.BranchStmt:
		if v.Tok == token.FALLTHROUGH {
			return k
		}
		return nil
	case *ast.LabeledStmt:
		return s.stmt(v.Stmt, k)
	case *ast.IfStmt:
		if v.Init != nil {
			k = s.stmt(v.Init, k)
			if k == nil {
				return nil
			}
		}
		s.writes(v.Cond, k)
		k = s.forget(k, v.Cond)
		yc, yu := s.constraint(v.Cond, true)
		nc, nu := s.constraint(v.Cond, false)
		line := s.m.Line(v.Pos())
		yes := s.block(v.Body.List, s.apply(k, yc, yu, line))
		no := s.apply(k, nc, nu, line)
		if v.Else != nil {
			no = s.stmt(v.Else, no)
		}
		return joinKnown(yes, no)
	case *ast.ForStmt, *ast.RangeStmt:
		inner := s.forget(k, st)
		var body *ast.BlockStmt
		if f, ok := v.(*ast.ForStmt); ok {
			body = f.Body
		} else {
			body = v.(*ast.RangeStmt).Body
		}
		s.block(body.List, inner)
		return inner
	case *ast.SwitchStmt:
		if v.Init != nil {
			k = s.stmt(v.Init, k)
			if k == nil {
				return nil
			}
		}
		if v.Tag != nil {
			s.writes(v.Tag, k)
			k = s.forget(k, v.Tag)
		}
		line := s.m.Line(v.Pos())
		name, mf := "", (*mfield)(nil)
		if v.Tag != nil {
			name, mf = s.subject(v.Tag)
		}
		var outs []known
		hasDefault := false
		covered := stateSet{}
		readable := mf != nil
		for _, cl := range v.Body.List {
			cc := cl.(*ast.CaseClause)
			if mf != nil {
				for _, x := range cc.List {
					if st := s.state(x, mf); st != "" {
						covered[st] = true
					} else {
						readable = false
					}
				}
			}
		}
		var noneTrue known = k.clone()
		for _, cl := range v.Body.List {
			cc := cl.(*ast.CaseClause)
			b := k.clone()
			switch {
			case cc.List == nil:
				hasDefault = true
				if mf != nil && readable {
					b = s.apply(b, map[string]stateSet{name: s.complement(mf, covered)}, nil, line)
				} else if mf != nil {
					delete(b, name)
				} else if v.Tag == nil {
					b = noneTrueOf(s, v, k, line)
				}
			case mf != nil:
				set := stateSet{}
				ok := true
				for _, x := range cc.List {
					if st := s.state(x, mf); st != "" {
						set[st] = true
					} else {
						ok = false
					}
				}
				if ok {
					b = s.apply(b, map[string]stateSet{name: set}, nil, line)
				} else {
					delete(b, name)
				}
			case v.Tag == nil && len(cc.List) == 1:
				c, u := s.constraint(cc.List[0], true)
				b = s.apply(b, c, u, line)
			default:
				for _, x := range cc.List {
					b = s.forget(b, x)
					for n := range s.mentions(x) {
						delete(b, n)
					}
				}
			}
			outs = append(outs, s.block(cc.Body, b))
		}
		if !hasDefault {
			switch {
			case mf != nil && readable:
				noneTrue = s.apply(k, map[string]stateSet{name: s.complement(mf, covered)}, nil, line)
			case mf != nil:
				delete(noneTrue, name)
			case v.Tag == nil:
				noneTrue = noneTrueOf(s, v, k, line)
			}
			outs = append(outs, noneTrue)
		}
		return joinKnown(outs...)
	case *ast.TypeSwitchStmt, *ast.SelectStmt:
		inner := s.forget(k, st)
		var outs []known
		var body *ast.BlockStmt
		if ts, ok := v.(*ast.TypeSwitchStmt); ok {
			body = ts.Body
		} else {
			body = v.(*ast.SelectStmt).Body
		}
		for _, cl := range body.List {
			switch cc := cl.(type) {
			case *ast.CaseClause:
				outs = append(outs, s.block(cc.Body, inner.clone()))
			case *ast.CommClause:
				outs = append(outs, s.block(cc.Body, inner.clone()))
			}
		}
		outs = append(outs, inner)
		return joinKnown(outs...)
	case *ast.GoStmt, *ast.DeferStmt:
		return s.forget(k, st)
	case *ast.ExprStmt:
		s.writes(v, k)
		if isTerminal(s.m, s.env, v.X) {
			return nil
		}
		return s.forget(k, v)
	case *ast.AssignStmt:
		s.writes(v, k)
		s.aliases(v)
		return s.forget(k, v)
	}
	// declarations, sends, inc/dec
	s.writes(st, k)
	return s.forget(k, st)
}

// noneTrueOf answers the knowledge where no case of a tagless switch held.
func noneTrueOf(s *ms, v *ast.SwitchStmt, k known, line int) known {
	out := k.clone()
	for _, cl := range v.Body.List {
		cc := cl.(*ast.CaseClause)
		for _, x := range cc.List {
			c, u := s.constraint(x, false)
			out = s.apply(out, c, u, line)
		}
	}
	return out
}

// aliases records `allowed := T[row.Status]` and `_, ok := T[row.Status][X]`.
func (s *ms) aliases(a *ast.AssignStmt) {
	for _, l := range a.Lhs {
		if id, ok := l.(*ast.Ident); ok {
			delete(s.tables, id.Name)
			delete(s.oks, id.Name)
		}
	}
	if len(a.Rhs) != 1 {
		return
	}
	last, ok := a.Lhs[len(a.Lhs)-1].(*ast.Ident)
	if !ok {
		return
	}
	ix, ok := unparen(a.Rhs[0]).(*ast.IndexExpr)
	if !ok {
		return
	}
	if len(a.Lhs) == 1 {
		if name, mf := s.subject(ix.Index); mf != nil {
			s.tables[last.Name] = tableAlias{name, mf}
			return
		}
	}
	if name, mf := s.tableLookup(ix.X); mf != nil {
		if target := s.state(ix.Index, mf); target != "" {
			s.oks[last.Name] = okAlias{name, mf, target}
		}
	}
}

// laterSave reports whether row is saved (Save/Updates of it) after pos.
func (s *ms) laterSave(row string, pos token.Pos) bool {
	found := false
	ast.Inspect(s.fn.Body, func(n ast.Node) bool {
		if found {
			return false
		}
		if _, ok := n.(*ast.FuncLit); ok {
			return false
		}
		call, ok := n.(*ast.CallExpr)
		if !ok || call.Pos() < pos {
			return true
		}
		sel, ok := call.Fun.(*ast.SelectorExpr)
		if !ok || !s.m.isGorm(s.env, sel.X) {
			return true
		}
		switch sel.Sel.Name {
		case "Save", "Updates", "UpdateColumns":
			for _, a := range call.Args {
				if rootIdent(stripAddr(a)) == row {
					found = true
				}
			}
		}
		return true
	})
	return found
}

func stripAddr(x ast.Expr) ast.Expr {
	if u, ok := unparen(x).(*ast.UnaryExpr); ok && u.Op == token.AND {
		return unparen(u.X)
	}
	return unparen(x)
}

// writes judges the state writes a node makes.
func (s *ms) writes(n ast.Node, k known) {
	if n == nil || k == nil {
		return
	}
	if a, ok := n.(*ast.AssignStmt); ok && len(a.Lhs) == len(a.Rhs) {
		for i, l := range a.Lhs {
			sel, ok := l.(*ast.SelectorExpr)
			if !ok {
				continue
			}
			name, mf := s.rowOf(sel.X, sel.Sel.Name)
			if mf == nil || !s.laterSave(name, a.End()) {
				continue
			}
			s.judge(name, mf, a.Rhs[i], a, k, nil, 0)
		}
	}
	ast.Inspect(n, func(x ast.Node) bool {
		if _, ok := x.(*ast.FuncLit); ok {
			return false
		}
		if call, ok := x.(*ast.CallExpr); ok {
			s.chainWrite(call, k)
		}
		return true
	})
}

var whereConj = regexp.MustCompile(`(?i)^\(?\s*(?:[\w"` + "`" + `]+\.)?["` + "`" + `]?(\w+)["` + "`" + `]?\s*(=|==|<>|!=|\bin\b|\bnot\s+in\b)\s*(.*?)\s*\)?$`)
var andSplit = regexp.MustCompile(`(?i)\s+and\s+`)

// chainWhere answers what a chain's Where calls say of a machine column: the states it
// leaves (nil: nothing), and whether one is unreadable (a condition on the column the
// checker cannot read).
func (s *ms) chainWhere(links []chainLink, mf *mfield) (stateSet, int, bool) {
	var out stateSet
	line := 0
	for _, l := range links {
		switch l.name {
		case "Or", "Not":
			if s.mentionsColumn(l.call, mf) {
				return nil, 0, true
			}
		case "Where":
			set, ok := s.whereStates(l.call, mf)
			if !ok {
				return nil, 0, true
			}
			if set != nil {
				if out == nil {
					out = set
				} else {
					out = out.and(set)
				}
				line = s.m.callLine(l.call)
			}
		}
	}
	return out, line, false
}

func (s *ms) mentionsColumn(call *ast.CallExpr, mf *mfield) bool {
	found := false
	ast.Inspect(call, func(n ast.Node) bool {
		if bl, ok := n.(*ast.BasicLit); ok {
			if v, ok := stringLit(bl); ok && containsWord(strings.ToLower(v), strings.ToLower(mf.mach.column)) {
				found = true
			}
		}
		if id, ok := n.(*ast.Ident); ok && id.Name == mf.field {
			found = true
		}
		return true
	})
	return found
}

func containsWord(s, w string) bool {
	re := regexp.MustCompile(`(^|[^a-z0-9_])` + regexp.QuoteMeta(w) + `([^a-z0-9_]|$)`)
	return re.MatchString(s)
}

// whereStates reads one Where call (ok false: it conditions the column unreadably).
func (s *ms) whereStates(call *ast.CallExpr, mf *mfield) (stateSet, bool) {
	if len(call.Args) == 0 {
		return nil, true
	}
	col := strings.ToLower(mf.mach.column)
	switch a := stripAddr(call.Args[0]).(type) {
	case *ast.CompositeLit:
		for _, e := range a.Elts {
			kv, ok := e.(*ast.KeyValueExpr)
			if !ok {
				continue
			}
			key := ""
			if id, ok := kv.Key.(*ast.Ident); ok {
				if id.Name == mf.field {
					key = col
				}
			} else if v, ok := s.m.constString(s.env, kv.Key); ok {
				key = strings.ToLower(v)
			}
			if key != col {
				continue
			}
			if st := s.state(kv.Value, mf); st != "" {
				return setOf(st), true
			}
			if set := s.statesOf(kv.Value, mf); set != nil {
				return set, true
			}
			return nil, false
		}
		return nil, true
	}
	q, ok := s.m.constString(s.env, call.Args[0])
	if !ok {
		return nil, !s.mentionsColumn(call, mf)
	}
	lq := strings.ToLower(q)
	if !containsWord(lq, col) {
		return nil, true
	}
	if containsWord(lq, "or") || containsWord(lq, "not") && !regexp.MustCompile(`(?i)\bnot\s+in\b`).MatchString(lq) {
		return nil, false
	}
	args := call.Args[1:]
	placeholder := 0
	var out stateSet
	for _, part := range andSplit.Split(strings.TrimSpace(q), -1) {
		m := whereConj.FindStringSubmatch(strings.TrimSpace(part))
		nq := strings.Count(part, "?")
		if m == nil || !strings.EqualFold(m[1], col) {
			if containsWord(strings.ToLower(part), col) {
				return nil, false
			}
			placeholder += nq
			continue
		}
		op := strings.ToLower(strings.Join(strings.Fields(m[2]), " "))
		rhs := strings.TrimSpace(m[3])
		var set stateSet
		switch {
		case strings.Trim(rhs, "() ") == "?":
			if placeholder >= len(args) {
				return nil, false
			}
			arg := args[placeholder]
			if st := s.state(arg, mf); st != "" {
				set = setOf(st)
			} else if ss := s.statesOf(arg, mf); ss != nil {
				set = ss
			} else {
				return nil, false
			}
		default:
			lits := regexp.MustCompile(`'([^']*)'`).FindAllStringSubmatch(rhs, -1)
			if len(lits) == 0 {
				return nil, false
			}
			set = stateSet{}
			for _, l := range lits {
				hit := ""
				for _, st := range mf.mach.states {
					if strings.EqualFold(st, l[1]) {
						hit = st
					}
				}
				if hit == "" {
					return nil, false
				}
				set[hit] = true
			}
		}
		placeholder += nq
		switch op {
		case "=", "==", "in":
		case "<>", "!=", "not in":
			set = s.complement(mf, set)
		default:
			return nil, false
		}
		if out == nil {
			out = set
		} else {
			out = out.and(set)
		}
	}
	return out, true
}

// fetchedRow answers the guarded row an update by id writes: `Model(&T{})` of the row's
// table with `.Where("id = ?", x)`, x the id the row was fetched by or `row.ID`.
func (s *ms) fetchedRow(links []chainLink, mf *mfield, k known) string {
	var arg ast.Expr
	for _, l := range links {
		if l.name == "Where" {
			if a := whereIDArg(s, l.call); a != nil {
				if arg != nil {
					return ""
				}
				arg = a
			}
		}
	}
	ref := simpleRef(arg)
	if ref == "" {
		return ""
	}
	found := ""
	for row := range k {
		if _, rmf := s.rowOf(ast.NewIdent(row), mf.field); rmf != mf {
			continue
		}
		if s.fetched[row] == ref || ref == row+".ID" {
			if found != "" {
				return ""
			}
			found = row
		}
	}
	return found
}

// chainWrite judges `db.Model(&row).Where(...).Update("status", X)` and its forms.
func (s *ms) chainWrite(call *ast.CallExpr, k known) {
	if s.judged[call] {
		return
	}
	links, _ := s.m.gormChain(s.env, call)
	if links == nil {
		return
	}
	last := links[len(links)-1]
	var value ast.Expr
	var mf *mfield
	row := ""
	// the chain's table: Model(&row), Model(&Job{}), Table("jobs"), or Updates(&Job{...})
	for _, l := range links {
		switch l.name {
		case "Model":
			if len(l.call.Args) == 1 {
				a := stripAddr(l.call.Args[0])
				if id, ok := a.(*ast.Ident); ok {
					t := s.m.Infer(s.env, id)
					if ti := t.deref().rowTypeAny(); ti != nil {
						mf = s.byType[ti]
						row = id.Name
					}
				} else {
					t := s.m.Infer(s.env, l.call.Args[0])
					if ti := t.deref().rowTypeAny(); ti != nil {
						mf = s.byType[ti]
					}
				}
			}
		case "Table":
			if len(l.call.Args) >= 1 {
				if name, ok := s.m.constString(s.env, l.call.Args[0]); ok {
					for _, f := range s.all {
						if strings.EqualFold(f.md.SQL, name) {
							mf = f
						}
					}
				}
			}
		}
	}
	col := ""
	switch last.name {
	case "Update", "UpdateColumn":
		if len(last.call.Args) != 2 {
			return
		}
		c, ok := s.m.constString(s.env, last.call.Args[0])
		if !ok {
			return
		}
		col, value = c, last.call.Args[1]
	case "Updates", "UpdateColumns":
		if len(last.call.Args) != 1 {
			return
		}
		arg := stripAddr(last.call.Args[0])
		cl, ok := arg.(*ast.CompositeLit)
		if !ok {
			return
		}
		if mf == nil {
			if ti := s.m.Infer(s.env, cl).rowTypeAny(); ti != nil {
				mf = s.byType[ti]
			}
		}
		if mf == nil {
			return
		}
		for _, e := range cl.Elts {
			kv, ok := e.(*ast.KeyValueExpr)
			if !ok {
				continue
			}
			if id, ok := kv.Key.(*ast.Ident); ok && id.Name == mf.field {
				col, value = mf.field, kv.Value
			} else if v, ok := s.m.constString(s.env, kv.Key); ok && (strings.EqualFold(v, mf.mach.column) || v == mf.field) {
				col, value = v, kv.Value
			}
		}
	default:
		return
	}
	if mf == nil || value == nil || !(strings.EqualFold(col, mf.mach.column) || col == mf.field) {
		return
	}
	s.judged[call] = true
	if row == "" {
		row = s.fetchedRow(links, mf, k)
	}
	where, whereLine, unreadable := s.chainWhere(links, mf)
	if unreadable {
		return
	}
	s.judge(row, mf, value, call, k, where, whereLine)
}

func (s *ms) judge(row string, mf *mfield, value ast.Expr, node ast.Node, k known, where stateSet, whereLine int) {
	target := s.state(value, mf)
	if target == "" {
		return
	}
	var states stateSet
	line := 0
	how := ""
	if row != "" {
		if kv, ok := k[row]; ok {
			states, line = kv.states, kv.line
			how = fmt.Sprintf("the check at line %d", kv.line)
		}
	}
	if where != nil {
		if states == nil {
			states = where
		} else {
			states = states.and(where)
		}
		line = whereLine
		if how == "" {
			how = fmt.Sprintf("the update's `.Where(...)` at line %d", whereLine)
		} else {
			how += fmt.Sprintf(" and the update's `.Where(...)` at line %d", whereLine)
		}
	}
	if states == nil {
		return
	}
	var bad []string
	for _, st := range states.sorted() {
		if !mf.mach.legal(st, target) {
			bad = append(bad, st)
		}
	}
	if len(bad) == 0 {
		return
	}
	var sources []string
	for _, st := range mf.mach.states {
		if st != target && mf.mach.legal(st, target) {
			sources = append(sources, st)
		}
	}
	at := s.m.Line(node.Pos())
	if c, ok := node.(*ast.CallExpr); ok {
		at = s.m.callLine(c)
	}
	subject := row + "." + mf.field
	if row == "" {
		subject = mf.md.TI.Name + "." + mf.field
	}
	var edges []string
	for _, b := range bad {
		edges = append(edges, fmt.Sprintf("'%s' -> '%s'", b, target))
	}
	message := fmt.Sprintf("%s sets %s to '%s' where %s leaves it %s, and the %s.%s machine has no edge %s",
		s.fn.Name, subject, target, how, quoteList(states.sorted(), " or "), mf.mach.table,
		mf.mach.column, strings.Join(edges, ", "))
	allowed := "never (no state moves to it)"
	if len(sources) > 0 {
		allowed = "one of " + quoteList(sources, ", ")
	}
	hint := fmt.Sprintf("refuse the request unless %s allows '%s': it may move there from %s -- "+
		"check `if %s != ... { c.JSON(http.StatusConflict, ...); return }` before the write, or "+
		"make the update conditional (`.Where(\"%s IN ?\", []string{...})` with only those "+
		"states, and refuse when `RowsAffected == 0`)", subject, target, allowed, subject,
		mf.mach.column)
	_ = line
	key := fmt.Sprintf("%s:%s|%s.%s=%s", s.fn.File.Rel, s.fn.Name, subject, mf.mach.column, target)
	s.c.add("GO808", s.fn.File, at, message, hint, key)
}
