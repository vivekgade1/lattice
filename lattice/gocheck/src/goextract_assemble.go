// goextract: the routes of the module, their middleware chains, auth (D5) and the auth
// model, assembled from the registrations and closures.
package main

import (
	"fmt"
	"go/ast"
	"regexp"
	"sort"
	"strings"
)

type xGuard struct {
	Kind   string  `json:"kind"`
	Source string  `json:"source"`
	Status *int    `json:"status"`
	Code   *string `json:"detail"`
	Line   int     `json:"line"`
	File   string  `json:"file"`
}

type xMw struct {
	Func           string    `json:"func"`
	File           string    `json:"file"`
	Line           int       `json:"line"`
	Wrappers       []string  `json:"wrappers"`
	Authenticating bool      `json:"authenticating"`
	RecordsCaller  []string  `json:"records_caller"`
	Admin          bool      `json:"mentions_admin"`
	Raises         []xRaise  `json:"raises"`
	UsersTable     *string   `json:"users_table"`
	Bearer         bool      `json:"bearer"`
	Unread         []xUnread `json:"unread"`
	ci             *closureInfo
	fn             *Func
}

type xRoute struct {
	Method       string    `json:"method"`
	Path         string    `json:"path"`
	PathTemplate string    `json:"path_template"`
	PathExact    bool      `json:"path_exact"`
	File         string    `json:"file"`
	Line         int       `json:"line"`
	Condition    *string   `json:"condition"`
	RouterChain  []string  `json:"router_chain"`
	Handler      *xHandler `json:"handler"`
	Middleware   []xMwRef  `json:"middleware"`
	Auth         string    `json:"auth"`
	AuthReason   string    `json:"auth_reason"`
	Member       bool      `json:"member"`
	Guards       []xGuard  `json:"guards"`
	Params       []xParam  `json:"params"`
	Raises       []xRaise  `json:"raises"`
	OwnerUses    []xOwner  `json:"owner_uses"`
	Tables       []string  `json:"tables"`
	BodyModels   []string  `json:"body_models"`
	Tx           bool      `json:"tx"`
	Calls        []string  `json:"calls"`
	StatusCode   *int      `json:"status_code"`
	StatusCodes  []int     `json:"status_codes"`
	ErrorWriter  *string   `json:"error_writer"`
	Unread       []xUnread `json:"unread"`
}

type xAuth struct {
	Middleware     *string           `json:"middleware"`
	Scheme         *string           `json:"scheme"`
	UsersTable     *string           `json:"users_table"`
	LoginPath      *string           `json:"login_path"`
	PasswordColumn *string           `json:"password_column"`
	UsernameField  *string           `json:"username_field"`
	PasswordField  *string           `json:"password_field"`
	LoginEncoding  string            `json:"login_encoding"`
	TokenField     *string           `json:"token_field"`
	CallerKeys     map[string]string `json:"caller_keys"`
	Notes          []string          `json:"notes"`
}

type routeRec struct {
	r       *reg
	alt     routerRes
	altIdx  int
	h       hres
	mwRes   []hres
	mwVia   []string
	closure *closureInfo
	out     *xRoute
}

var usernameRE = regexp.MustCompile(`(?i)^(email|e_?mail|user_?name|username|login|user|identifier|handle|account)$`)
var passwordRE = regexp.MustCompile(`(?i)pass|pwd|secret`)

func (g *gx) findRoutes() {
	regs := g.findRegistrations()
	for _, r := range regs {
		if !g.isWired(r.fn) {
			g.res.Excluded = append(g.res.Excluded, xExcluded{Kind: "unwired", What: "route", Method: r.method,
				Path: r.path, File: r.fn.File.Rel, Line: g.line(r.call.Pos()), Reason: "not reachable from main"})
			continue
		}
		methods := r.methods
		if len(methods) == 0 {
			methods = []string{r.method}
		}
		g.routerSteps = 0
		alts := g.router(r.e, r.routerX, r.call.Pos(), 4)
		h := g.resolveFn(r.e, r.handlerX, 4)
		for ai, a := range alts {
			items := append(append([]mwItem(nil), a.mws...), g.routeMw(r)...)
			var res []hres
			var via []string
			for _, it := range items {
				res = append(res, g.resolveFn(it.e, it.x, 4))
				via = append(via, it.via)
			}
			for _, method := range methods {
				rec := &routeRec{r: r, alt: a, altIdx: ai, h: h, mwRes: res, mwVia: via}
				rec.out = &xRoute{Method: method}
				g.routeList = append(g.routeList, rec)
			}
		}
	}
	// the writers of every wrapper in use
	for _, rec := range g.routeList {
		all := append([]hres{rec.h}, rec.mwRes...)
		for _, h := range all {
			for _, w := range h.wrappers {
				if wf := g.wrapWriter[w]; wf != nil {
					wr := g.writerOf(wf)
					name := qual(w)
					found := false
					for _, x := range wr.Wrappers {
						if x == name {
							found = true
						}
					}
					if !found {
						wr.Wrappers = append(wr.Wrappers, name)
						sort.Strings(wr.Wrappers)
					}
				}
			}
		}
	}
	g.findConstructors()
	// the middleware: which one authenticates, and what it sets
	mwFns := map[*Func]bool{}
	for _, rec := range g.routeList {
		for _, h := range rec.mwRes {
			if h.fn != nil {
				mwFns[h.fn] = true
			}
		}
	}
	var mwList []*Func
	for fn := range mwFns {
		mwList = append(mwList, fn)
	}
	sort.SliceStable(mwList, func(i, j int) bool { return g.funcKey(mwList[i]) < g.funcKey(mwList[j]) })
	for _, fn := range mwList {
		ci := g.analyze(fn)
		mw := &xMw{Func: qual(fn), File: fn.File.Rel, Line: g.line(fn.Pos), Wrappers: []string{},
			RecordsCaller: []string{}, Raises: ci.raises, Unread: ci.unread, ci: ci, fn: fn, Bearer: ci.bearer && ci.authHdr}
		if mw.Raises == nil {
			mw.Raises = []xRaise{}
		}
		if mw.Unread == nil {
			mw.Unread = []xUnread{}
		}
		has401 := false
		for _, r := range ci.raises {
			if r.Status != nil && *r.Status == 401 {
				has401 = true
			}
		}
		mw.Authenticating = has401 && len(ci.sets) > 0
		if mw.Authenticating {
			if ci.usersTbl != "" {
				u := ci.usersTbl
				mw.UsersTable = &u
			}
			for _, s := range ci.sets {
				mw.RecordsCaller = append(mw.RecordsCaller, s.key)
				kind := "struct"
				if s.scalar {
					kind = "scalar"
				}
				if old, ok := g.callerKeys[s.key]; !ok || old == "scalar" {
					g.callerKeys[s.key] = kind
				}
			}
			sort.Strings(mw.RecordsCaller)
			mw.RecordsCaller = dedupStrings(mw.RecordsCaller)
		}
		mw.Admin = g.ck.mentionsAdmin(fn.Body)
		g.mws[fn] = mw
	}
	// functions that read a key the authenticating middleware sets
	if len(g.callerKeys) > 0 {
		for _, fn := range g.m.Funcs {
			if fn.isMethod && false {
				continue
			}
			e := g.m.EnvOf(fn)
			kind := ""
			ast.Inspect(fn.Body, func(n ast.Node) bool {
				if c, ok := n.(*ast.CallExpr); ok {
					if _, k, ok := g.callerKeyOf(e, c); ok {
						if kind == "" || k == "struct" {
							kind = k
						}
					}
				}
				return true
			})
			if kind != "" && len(fn.Results()) > 0 {
				g.callerFuncs[fn] = kind
			}
		}
	}
	// the handlers
	for _, rec := range g.routeList {
		if rec.h.fn != nil {
			rec.closure = g.analyze(rec.h.fn)
		}
	}
	sort.SliceStable(g.routeList, func(i, j int) bool {
		a, b := g.routeList[i], g.routeList[j]
		if a.r.fn.File.Rel != b.r.fn.File.Rel {
			return a.r.fn.File.Rel < b.r.fn.File.Rel
		}
		if la, lb := g.line(a.r.call.Pos()), g.line(b.r.call.Pos()); la != lb {
			return la < lb
		}
		return a.altIdx < b.altIdx
	})
	// the aliasing guard: a factory literal handling two or more routes
	byLit := map[*Func]int{}
	for _, rec := range g.routeList {
		if rec.h.fn != nil && rec.h.factory {
			byLit[rec.h.fn]++
		}
	}
	for _, rec := range g.routeList {
		g.assemble(rec, byLit)
		g.res.Routes = append(g.res.Routes, rec.out)
	}
	if g.res.Routes == nil {
		g.res.Routes = []*xRoute{}
	}
	var mwNames []*Func
	for fn := range g.mws {
		mwNames = append(mwNames, fn)
	}
	sort.SliceStable(mwNames, func(i, j int) bool { return g.funcKey(mwNames[i]) < g.funcKey(mwNames[j]) })
	wrapsOf := map[*Func][]string{}
	for _, rec := range g.routeList {
		for _, h := range rec.mwRes {
			if h.fn != nil && wrapsOf[h.fn] == nil {
				for _, w := range h.wrappers {
					wrapsOf[h.fn] = append(wrapsOf[h.fn], qual(w))
				}
			}
		}
	}
	for _, fn := range mwNames {
		g.mws[fn].Wrappers = append(g.mws[fn].Wrappers, wrapsOf[fn]...)
		g.res.Middleware = append(g.res.Middleware, g.mws[fn])
	}
	if g.res.Middleware == nil {
		g.res.Middleware = []*xMw{}
	}
	g.buildAuth()
	g.findStatusWrites()
	g.finishWriters()
	g.resolveForeignKeys()
	// models
	seen := map[*TypeInfo]bool{}
	for _, rec := range g.routeList {
		if rec.closure == nil {
			continue
		}
		for _, md := range rec.closure.models {
			if !seen[md.ti] {
				seen[md.ti] = true
				g.res.Models = append(g.res.Models, md)
			}
		}
	}
	sort.SliceStable(g.res.Models, func(i, j int) bool {
		if g.res.Models[i].File != g.res.Models[j].File {
			return g.res.Models[i].File < g.res.Models[j].File
		}
		return g.res.Models[i].Line < g.res.Models[j].Line
	})
	if g.res.Models == nil {
		g.res.Models = []*xModel{}
	}
	var sents []string
	for k := range g.sentinels {
		sents = append(sents, k)
	}
	sort.Strings(sents)
	for _, k := range sents {
		s := g.sentinels[k]
		if mp, w, ok := g.writerTarget(k); ok {
			st := mp.Status
			s.Status, s.Code, s.Writer = &st, mp.Code, w.Func
		}
		g.res.Sentinels = append(g.res.Sentinels, s)
	}
	if g.res.Sentinels == nil {
		g.res.Sentinels = []*xSentinel{}
	}
}

func (g *gx) routeMw(r *reg) []mwItem {
	var out []mwItem
	for _, x := range r.mwX {
		out = append(out, mwItem{r.e, x, fmt.Sprintf("route %s:%d", r.fn.File.Rel, g.line(r.call.Pos()))})
	}
	return out
}

func (g *gx) finishWriters() {
	var ws []*xWriter
	for _, w := range g.writers {
		ws = append(ws, w)
	}
	sort.SliceStable(ws, func(i, j int) bool { return ws[i].File+ws[i].Func < ws[j].File+ws[j].Func })
	g.res.Writers = ws
	if g.res.Writers == nil {
		g.res.Writers = []*xWriter{}
	}
	var best *xWriter
	for _, w := range ws {
		if best == nil || len(w.Wrappers) > len(best.Wrappers) {
			best = w
		}
	}
	g.res.ErrorWriter = best
}

func (g *gx) assemble(rec *routeRec, aliased map[*Func]int) {
	m := g.m
	r, out := rec.r, rec.out
	out.Path = joinPath(rec.alt.prefix, r.path)
	out.PathTemplate = pathTemplate(out.Path)
	out.PathExact = rec.alt.exact || (rec.alt.engine && rec.alt.exact)
	out.File, out.Line = r.fn.File.Rel, g.line(r.call.Pos())
	if r.cond != "" {
		c := r.cond
		out.Condition = &c
	}
	out.RouterChain = rec.alt.chain
	if out.RouterChain == nil {
		out.RouterChain = []string{}
	}
	out.Guards, out.Params, out.Raises, out.OwnerUses = []xGuard{}, []xParam{}, []xRaise{}, []xOwner{}
	out.Tables, out.BodyModels, out.Calls, out.Unread = []string{}, []string{}, []string{}, []xUnread{}
	out.StatusCodes = []int{}
	unreadf := func(file string, line int, what, reason string) {
		out.Unread = append(out.Unread, xUnread{File: file, Line: line, What: what, Reason: reason})
		g.addUnread(file, line, what, reason)
	}
	// path-level problems
	for _, u := range rec.alt.unread {
		unreadf(out.File, out.Line, "router", u)
	}
	// handler
	h := rec.h
	switch {
	case h.fn != nil:
		out.Handler = &xHandler{Func: qual(h.fn), Module: funcImport(h.fn), File: h.fn.File.Rel,
			Line: g.line(h.fn.Pos), Wrappers: []string{}}
		for _, w := range h.wrappers {
			out.Handler.Wrappers = append(out.Handler.Wrappers, qual(w))
		}
		if h.factory && aliased[h.fn] >= 2 {
			unreadf(out.File, out.Line, "handler", fmt.Sprintf("the literal %s returned by a factory is the handler of %d routes: the factory's arguments are not read", qual(h.fn), aliased[h.fn]))
		}
		if wf := g.wrapWriter[firstOr(h.wrappers)]; wf != nil {
			n := qual(wf)
			out.ErrorWriter = &n
		}
	case h.external != "":
		unreadf(out.File, out.Line, "handler", "the handler is "+h.external+" (outside the module)")
	default:
		unreadf(out.File, out.Line, "handler", h.unread)
	}
	// middleware
	unclearWhy := ""
	var authenticating, adminMw *xMw
	for i, mh := range rec.mwRes {
		ref := xMwRef{Via: rec.mwVia[i]}
		switch {
		case mh.fn != nil:
			ref.Func, ref.File, ref.Line, ref.Resolved = qual(mh.fn), mh.fn.File.Rel, g.line(mh.fn.Pos), true
			for _, w := range mh.wrappers {
				ref.Wrappers = append(ref.Wrappers, qual(w))
			}
			if info := g.mws[mh.fn]; info != nil {
				if info.Authenticating && authenticating == nil {
					authenticating = info
				}
				refuses403 := false
				for _, rs := range info.Raises {
					if rs.Status != nil && *rs.Status == 403 {
						refuses403 = true
					}
				}
				if (info.Authenticating || refuses403) && info.Admin && adminMw == nil {
					adminMw = info
				}
				// guards
				seenStatus := map[int]bool{}
				for _, rs := range info.Raises {
					if rs.Status == nil || seenStatus[*rs.Status] {
						continue
					}
					seenStatus[*rs.Status] = true
					kind := "custom"
					if info.Authenticating && *rs.Status == 401 {
						kind = "authenticated"
					}
					out.Guards = append(out.Guards, xGuard{Kind: kind, Source: info.Func, Status: rs.Status,
						Code: rs.Code, Line: rs.Line, File: rs.File})
				}
			}
		case mh.external != "":
			ref.Func, ref.External, ref.Resolved = mh.external, mh.external, externalOK[mh.external]
			if !externalOK[mh.external] && unclearWhy == "" {
				unclearWhy = "middleware " + mh.external + " is outside the module and not known to be harmless (" + rec.mwVia[i] + ")"
			}
		default:
			ref.Func = "?"
			if unclearWhy == "" {
				unclearWhy = "a middleware is not resolved (" + rec.mwVia[i] + "): " + mh.unread
			}
			unreadf(out.File, out.Line, "middleware", mh.unread+" ("+rec.mwVia[i]+")")
		}
		out.Middleware = append(out.Middleware, ref)
	}
	if out.Middleware == nil {
		out.Middleware = []xMwRef{}
	}
	// the closure
	if ci := rec.closure; ci != nil {
		out.Raises = ci.raises
		if out.Raises == nil {
			out.Raises = []xRaise{}
		}
		for _, name := range pathParams(out.Path) {
			out.Params = append(out.Params, xParam{Name: name, Annotation: "string", Type: strp("Str"), Source: "path",
				Required: true, Choices: []string{}, File: out.File, Line: out.Line})
		}
		for i := range out.Params {
			if at, ok := ci.parsed[out.Params[i].Name]; ok && out.Params[i].Source == "path" {
				out.Params[i].Parse, out.Params[i].ParseAt = "Int", fmt.Sprintf("%s:%d", at.file, at.line) // opt-in
			}
		}
		for _, k := range ci.porder {
			out.Params = append(out.Params, *ci.params[k])
		}
		out.OwnerUses = ci.owners
		if out.OwnerUses == nil {
			out.OwnerUses = []xOwner{}
		}
		for t := range ci.tables {
			out.Tables = append(out.Tables, t)
		}
		sort.Strings(out.Tables)
		for _, md := range ci.models {
			out.BodyModels = append(out.BodyModels, md.Name)
		}
		out.Tx = ci.tx
		for c := range ci.calls {
			out.Calls = append(out.Calls, c)
		}
		sort.Strings(out.Calls)
		for s := range ci.ok2xx {
			out.StatusCodes = append(out.StatusCodes, s)
		}
		sort.Ints(out.StatusCodes)
		if len(out.StatusCodes) == 1 {
			s := out.StatusCodes[0]
			out.StatusCode = &s
		}
		for _, u := range ci.unread {
			out.Unread = append(out.Unread, u)
		}
	} else {
		for _, name := range pathParams(out.Path) {
			out.Params = append(out.Params, xParam{Name: name, Annotation: "string", Type: strp("Str"), Source: "path",
				Required: true, Choices: []string{}, File: out.File, Line: out.Line})
		}
		out.Raises = []xRaise{}
	}
	// auth (D5)
	switch {
	case unclearWhy != "":
		out.Auth, out.AuthReason = "unclear", unclearWhy
	case !out.PathExact || len(rec.alt.unread) > 0:
		out.Auth, out.AuthReason = "unclear", "the router chain is not exact: "+strings.Join(rec.alt.unread, "; ")
	case adminMw != nil:
		out.Auth = "Admin"
		out.AuthReason = fmt.Sprintf("%s (%s:%d) names an admin concept", adminMw.Func, adminMw.File, adminMw.Line)
	case authenticating != nil:
		out.Auth, out.Member = "Customer", true
		out.AuthReason = fmt.Sprintf("authenticated by %s (%s:%d): a 401 refusal and the caller recorded under %s; chain %s",
			authenticating.Func, authenticating.File, authenticating.Line, strings.Join(authenticating.RecordsCaller, ","),
			strings.Join(rec.alt.chain, " > "))
	default:
		out.Auth = "Public"
		out.AuthReason = "no authenticating middleware in the chain (" + strings.Join(rec.alt.chain, " > ") + ")"
	}
	_ = m
}

func firstOr(ws []*Func) *Func {
	if len(ws) == 0 {
		return nil
	}
	return ws[0]
}

// buildAuth reads the auth model: the authenticating middleware, the users table, the
// login route (the Public POST whose closure compares a bcrypt hash) and its fields.
func (g *gx) buildAuth() {
	var mw *xMw
	var mws []*xMw
	for _, x := range g.res.Middleware {
		if x.Authenticating {
			mws = append(mws, x)
		}
	}
	if len(mws) == 0 {
		return
	}
	// the one that guards the most routes
	count := map[string]int{}
	for _, r := range g.res.Routes {
		for _, m := range r.Middleware {
			count[m.Func]++
		}
	}
	for _, x := range mws {
		if mw == nil || count[x.Func] > count[mw.Func] {
			mw = x
		}
	}
	a := &xAuth{LoginEncoding: "json", CallerKeys: map[string]string{}, Notes: []string{}}
	f := mw.Func
	a.Middleware = &f
	if mw.Bearer {
		a.Scheme = strp("bearer")
	}
	a.UsersTable = mw.UsersTable
	for k, v := range g.callerKeys {
		a.CallerKeys[k] = v
	}
	type login struct {
		rec *routeRec
	}
	var logins []*routeRec
	for _, rec := range g.routeList {
		if rec.out.Auth == "Public" && rec.out.Method == "POST" && rec.closure != nil && rec.closure.bcrypt != nil {
			logins = append(logins, rec)
		}
	}
	paths := map[string]bool{}
	for _, l := range logins {
		paths[l.out.PathTemplate] = true
	}
	if len(paths) == 1 {
		l := logins[0]
		p := l.out.PathTemplate
		a.LoginPath = &p
		col := l.closure.bcrypt.column
		a.PasswordColumn = &col
		if a.UsersTable == nil {
			t := l.closure.bcrypt.table
			a.UsersTable = &t
		}
		if l.closure.encoding != "" {
			a.LoginEncoding = l.closure.encoding
		}
		var users, pws []string
		for _, p := range l.out.Params {
			if p.Source != "body" {
				continue
			}
			switch {
			case passwordRE.MatchString(p.Name):
				pws = append(pws, p.Name)
			case usernameRE.MatchString(p.Name):
				users = append(users, p.Name)
			}
		}
		if len(users) == 1 && len(pws) == 1 {
			a.UsernameField, a.PasswordField = &users[0], &pws[0]
		}
		if l.closure.jsonTok != "" {
			t := l.closure.jsonTok
			a.TokenField = &t
		}
	} else if len(paths) > 1 {
		a.Notes = append(a.Notes, fmt.Sprintf("%d public POST routes compare a password hash: no login route is named", len(paths)))
	}
	g.res.Auth = a
}
