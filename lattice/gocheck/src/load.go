// Loading a Go module's source: files, packages, declarations (syntax only).
package main

import (
	"bufio"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

const (
	ginPath  = "github.com/gin-gonic/gin"
	gormPath = "gorm.io/gorm"
	httpPath = "net/http"
)

// Module is every package of the Go module under one root.
type Module struct {
	Root     string
	Path     string // the module path (go.mod), "" when there is none
	Fset     *token.FileSet
	Pkgs     []*Pkg
	ByImport map[string]*Pkg
	Funcs    []*Func // every declared function and method
	lits     map[*ast.FuncLit]*Func
	envs     map[*Func]*Env
	methods  map[string][]*Func // method name -> methods of that name (all types)
	Errors   []Finding          // GO800: files that do not parse
}

// Pkg is one directory's package.
type Pkg struct {
	Dir, Rel, Name, ImportPath string
	Files                      []*File
	Funcs                      map[string]*Func
	Methods                    map[string]map[string]*Func // type -> method -> func
	Types                      map[string]*TypeInfo
	Consts                     map[string]constInfo
	Vars                       map[string]varInfo
}

type constInfo struct {
	value ast.Expr // nil for an iota/implicit value
	typ   ast.Expr
	file  *File
}

type varInfo struct {
	typ   ast.Expr
	value ast.Expr
	file  *File
}

// File is one parsed source file.
type File struct {
	Path, Rel string
	AST       *ast.File
	Pkg       *Pkg
	Imports   map[string]string // the local name -> import path
}

// TypeInfo is a declared named type.
type TypeInfo struct {
	Name   string
	Pkg    *Pkg
	File   *File
	Struct *ast.StructType
	Iface  *ast.InterfaceType
	Under  ast.Expr // the underlying type expression (any kind)
}

// Func is a declared function, a method, or a function literal.
type Func struct {
	Name     string // "createJob", "Handler.CreateJob", "createJob.func1" for a literal
	Short    string // the declared name ("CreateJob"), "" for a literal
	Recv     string // the receiver's type name, "" for a function
	RecvVar  string
	Type     *ast.FuncType
	Body     *ast.BlockStmt
	File     *File
	Lit      *ast.FuncLit
	Parent   *Func // the enclosing function of a literal
	Pos      token.Pos
	nLits    int
	isMethod bool
}

func (f *Func) Pkg() *Pkg { return f.File.Pkg }

// Params answers (name, type expression) for each parameter ("" for an unnamed one).
func (f *Func) Params() []param {
	return fieldList(f.Type.Params)
}

type param struct {
	name string
	typ  ast.Expr
}

func fieldList(fl *ast.FieldList) []param {
	var out []param
	if fl == nil {
		return out
	}
	for _, fld := range fl.List {
		if len(fld.Names) == 0 {
			out = append(out, param{"", fld.Type})
			continue
		}
		for _, n := range fld.Names {
			out = append(out, param{n.Name, fld.Type})
		}
	}
	return out
}

// Results answers the result type expressions.
func (f *Func) Results() []param { return fieldList(f.Type.Results) }

// ErrorResult reports whether the last result is `error`.
func (f *Func) ErrorResult() bool {
	rs := f.Results()
	if len(rs) == 0 {
		return false
	}
	id, ok := rs[len(rs)-1].typ.(*ast.Ident)
	return ok && id.Name == "error"
}

func skipDir(name string) bool {
	return name == "vendor" || name == "testdata" || name == "node_modules" ||
		strings.HasPrefix(name, ".") || strings.HasPrefix(name, "_")
}

func readModulePath(root string) string {
	fh, err := os.Open(filepath.Join(root, "go.mod"))
	if err != nil {
		return ""
	}
	defer fh.Close()
	sc := bufio.NewScanner(fh)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if strings.HasPrefix(line, "module ") {
			return strings.Trim(strings.TrimSpace(strings.TrimPrefix(line, "module ")), `"`)
		}
	}
	return ""
}

// Load parses every non-test .go file under root.
func Load(root string) (*Module, error) {
	m := &Module{
		Root: root, Fset: token.NewFileSet(), ByImport: map[string]*Pkg{},
		lits: map[*ast.FuncLit]*Func{}, envs: map[*Func]*Env{}, methods: map[string][]*Func{},
	}
	m.Path = readModulePath(root)
	byDir := map[string]*Pkg{}
	var paths []string
	err := filepath.WalkDir(root, func(p string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			if p != root && skipDir(d.Name()) {
				return filepath.SkipDir
			}
			return nil
		}
		if strings.HasSuffix(p, ".go") && !strings.HasSuffix(p, "_test.go") {
			paths = append(paths, p)
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	sort.Strings(paths)
	for _, p := range paths {
		rel, _ := filepath.Rel(root, p)
		rel = filepath.ToSlash(rel)
		src, err := os.ReadFile(p)
		if err != nil {
			return nil, err
		}
		af, err := parser.ParseFile(m.Fset, p, src, parser.ParseComments)
		if err != nil {
			line := 1
			msg := err.Error()
			if el, ok := err.(interface{ Unwrap() []error }); ok && len(el.Unwrap()) > 0 {
				msg = el.Unwrap()[0].Error()
			}
			if pos := strings.Index(msg, ": "); pos >= 0 {
				// "file.go:12:3: expected ..."
				head := msg[:pos]
				parts := strings.Split(head, ":")
				if len(parts) >= 2 {
					if n := atoi(parts[len(parts)-2]); n > 0 {
						line = n
					}
				}
				msg = strings.TrimSpace(msg[pos+2:])
				if i := strings.LastIndex(msg, ": "); i >= 0 && strings.HasPrefix(msg, rel) {
					msg = msg[i+2:]
				}
			}
			m.Errors = append(m.Errors, Finding{
				Code: "GO800", File: rel, Line: line,
				Message: "the file does not parse: " + msg,
				Hint:    "fix the syntax error; the app cannot build",
				Key:     "GO800|" + rel,
			})
			continue
		}
		dir := filepath.Dir(p)
		pkg := byDir[dir]
		if pkg == nil {
			drel, _ := filepath.Rel(root, dir)
			drel = filepath.ToSlash(drel)
			pkg = &Pkg{
				Dir: dir, Rel: drel, Name: af.Name.Name,
				Funcs: map[string]*Func{}, Methods: map[string]map[string]*Func{},
				Types: map[string]*TypeInfo{}, Consts: map[string]constInfo{},
				Vars: map[string]varInfo{},
			}
			if m.Path != "" {
				if drel == "." {
					pkg.ImportPath = m.Path
				} else {
					pkg.ImportPath = m.Path + "/" + drel
				}
			}
			byDir[dir] = pkg
			m.Pkgs = append(m.Pkgs, pkg)
			if pkg.ImportPath != "" {
				m.ByImport[pkg.ImportPath] = pkg
			}
		}
		f := &File{Path: p, Rel: rel, AST: af, Pkg: pkg, Imports: map[string]string{}}
		pkg.Files = append(pkg.Files, f)
	}
	// imports need every package's name
	for _, pkg := range m.Pkgs {
		for _, f := range pkg.Files {
			for _, im := range f.AST.Imports {
				path := strings.Trim(im.Path.Value, "`\"")
				name := ""
				if im.Name != nil {
					name = im.Name.Name
				} else if ip := m.ByImport[path]; ip != nil {
					name = ip.Name
				} else {
					name = defaultImportName(path)
				}
				if name == "_" || name == "." {
					continue
				}
				f.Imports[name] = path
			}
			m.declare(f)
		}
	}
	for _, fn := range m.Funcs {
		if fn.isMethod {
			m.methods[fn.Short] = append(m.methods[fn.Short], fn)
		}
	}
	return m, nil
}

func defaultImportName(path string) string {
	parts := strings.Split(path, "/")
	last := parts[len(parts)-1]
	if len(parts) > 1 && len(last) >= 2 && last[0] == 'v' && isDigits(last[1:]) {
		last = parts[len(parts)-2]
	}
	if i := strings.Index(last, ".v"); i > 0 && isDigits(last[i+2:]) {
		last = last[:i]
	}
	last = strings.TrimPrefix(last, "go-")
	last = strings.ReplaceAll(last, "-", "")
	return last
}

func isDigits(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

func atoi(s string) int {
	n := 0
	for _, r := range strings.TrimSpace(s) {
		if r < '0' || r > '9' {
			return 0
		}
		n = n*10 + int(r-'0')
	}
	return n
}

func (m *Module) declare(f *File) {
	pkg := f.Pkg
	for _, d := range f.AST.Decls {
		switch d := d.(type) {
		case *ast.FuncDecl:
			if d.Body == nil {
				continue
			}
			fn := &Func{Short: d.Name.Name, Type: d.Type, Body: d.Body, File: f, Pos: d.Pos()}
			if d.Recv != nil && len(d.Recv.List) > 0 {
				fn.isMethod = true
				r := d.Recv.List[0]
				if len(r.Names) > 0 {
					fn.RecvVar = r.Names[0].Name
				}
				fn.Recv = recvTypeName(r.Type)
				fn.Name = fn.Recv + "." + fn.Short
				if pkg.Methods[fn.Recv] == nil {
					pkg.Methods[fn.Recv] = map[string]*Func{}
				}
				pkg.Methods[fn.Recv][fn.Short] = fn
			} else {
				fn.Name = fn.Short
				if fn.Short != "init" && fn.Short != "_" {
					pkg.Funcs[fn.Short] = fn
				}
			}
			m.Funcs = append(m.Funcs, fn)
		case *ast.GenDecl:
			for _, s := range d.Specs {
				switch s := s.(type) {
				case *ast.TypeSpec:
					ti := &TypeInfo{Name: s.Name.Name, Pkg: pkg, File: f, Under: s.Type}
					switch t := s.Type.(type) {
					case *ast.StructType:
						ti.Struct = t
					case *ast.InterfaceType:
						ti.Iface = t
					}
					pkg.Types[s.Name.Name] = ti
				case *ast.ValueSpec:
					for i, n := range s.Names {
						var v ast.Expr
						if i < len(s.Values) {
							v = s.Values[i]
						}
						if d.Tok == token.CONST {
							pkg.Consts[n.Name] = constInfo{value: v, typ: s.Type, file: f}
						} else if d.Tok == token.VAR {
							pkg.Vars[n.Name] = varInfo{typ: s.Type, value: v, file: f}
						}
					}
				}
			}
		}
	}
}

func recvTypeName(e ast.Expr) string {
	switch t := e.(type) {
	case *ast.StarExpr:
		return recvTypeName(t.X)
	case *ast.Ident:
		return t.Name
	case *ast.IndexExpr:
		return recvTypeName(t.X)
	case *ast.IndexListExpr:
		return recvTypeName(t.X)
	}
	return ""
}

// LitFunc answers the Func of a function literal inside parent.
func (m *Module) LitFunc(lit *ast.FuncLit, parent *Func) *Func {
	if fn, ok := m.lits[lit]; ok {
		return fn
	}
	parent.nLits++
	fn := &Func{
		Name: parent.Name + ".func" + itoa(parent.nLits), Type: lit.Type, Body: lit.Body,
		File: parent.File, Lit: lit, Parent: parent, Pos: lit.Pos(),
	}
	m.lits[lit] = fn
	return fn
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	if neg {
		b = append([]byte{'-'}, b...)
	}
	return string(b)
}

// Line answers the line of a position.
func (m *Module) Line(p token.Pos) int { return m.Fset.Position(p).Line }

// AllFuncs answers every declared function and every function literal inside one.
func (m *Module) AllFuncs() []*Func {
	var out []*Func
	var visit func(fn *Func)
	visit = func(fn *Func) {
		out = append(out, fn)
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			if lit, ok := n.(*ast.FuncLit); ok {
				visit(m.LitFunc(lit, fn))
				return false
			}
			return true
		})
	}
	for _, fn := range m.Funcs {
		visit(fn)
	}
	return out
}
