// gocheck: Lattice's layer A on Go apps written with Gin + GORM.
//
// Input (stdin, JSON): {"root": <the module's directory>, "policy": {...}} -- the policy
// is lattice.gocheck.GoPolicy (built from a spec). Output (stdout, JSON):
// {"findings": [{code, file, line, message, hint, key}, ...]}. Exit 0 on success; 2 when
// the input cannot be read; 3 when the checker itself fails (the message on stderr).
//
// The rules and what each cannot see are in lattice/gocheck/__init__.py's docstring.
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"runtime/debug"
	"sort"
)

// Policy is the app's guarantees (lattice.gocheck.GoPolicy as JSON).
type Policy struct {
	Tables      map[string]TableSpec   `json:"tables"`
	UsersTable  string                 `json:"users_table"`
	Owners      map[string]string      `json:"owners"`
	OwnerVia    map[string][2]string   `json:"owner_via"`
	Private     map[string][]string    `json:"private"`
	Machines    map[string]MachineSpec `json:"machines"`
	AdminRoutes [][2]string            `json:"admin_routes"`
	OpenRoutes  [][2]string            `json:"open_routes"`
	AdminRole   string                 `json:"admin_role"`
	HelperDepth int                    `json:"helper_depth"`
	Rules       []string               `json:"rules"`
}

// TableSpec is one table of the spec: its SQL name and columns.
type TableSpec struct {
	SQL     string   `json:"sql"`
	Columns []string `json:"columns"`
}

// MachineSpec is a table's state machine: the column and its legal edges.
type MachineSpec struct {
	Column string      `json:"column"`
	Edges  [][2]string `json:"edges"`
}

// Finding is one guarantee violation (lattice.pycheck.Finding's fields).
type Finding struct {
	Code    string `json:"code"`
	File    string `json:"file"`
	Line    int    `json:"line"`
	Message string `json:"message"`
	Hint    string `json:"hint"`
	Key     string `json:"key"`
}

type input struct {
	Root   string `json:"root"`
	Policy Policy `json:"policy"`
}

type output struct {
	Findings []Finding `json:"findings"`
}

func main() {
	if len(os.Args) > 1 && os.Args[1] == "extract" {
		os.Exit(runExtract(os.Stdin, os.Stdout, os.Stderr))
	}
	raw, err := io.ReadAll(os.Stdin)
	if err != nil {
		fmt.Fprintln(os.Stderr, "gocheck: cannot read the input:", err)
		os.Exit(2)
	}
	var in input
	if err := json.Unmarshal(raw, &in); err != nil {
		fmt.Fprintln(os.Stderr, "gocheck: the input is not valid JSON:", err)
		os.Exit(2)
	}
	found, err := run(in.Root, &in.Policy)
	if err != nil {
		fmt.Fprintln(os.Stderr, "gocheck:", err)
		os.Exit(3)
	}
	out, _ := json.Marshal(output{Findings: found})
	os.Stdout.Write(out)
	os.Stdout.Write([]byte("\n"))
}

func run(root string, pol *Policy) (found []Finding, err error) {
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("internal error: %v\n%s", r, debug.Stack())
		}
	}()
	if pol.HelperDepth <= 0 {
		pol.HelperDepth = 3
	}
	m, err := Load(root)
	if err != nil {
		return nil, err
	}
	c := newChecker(m, pol)
	want := map[string]bool{}
	for _, r := range pol.Rules {
		want[r] = true
	}
	all := len(want) == 0
	c.found = append(c.found, m.Errors...)
	if len(m.Errors) == 0 {
		if all || want["GO801"] {
			c.checkOwnership()
		}
		if all || want["GO802"] {
			c.checkPrivate()
		}
		if all || want["GO803"] {
			c.checkBounds()
		}
		if all || want["GO807"] {
			c.checkPartialWrites()
		}
		if all || want["GO808"] {
			c.checkMachines()
		}
	}
	seen := map[string]bool{}
	sort.SliceStable(c.found, func(i, j int) bool {
		a, b := c.found[i], c.found[j]
		if a.File != b.File {
			return a.File < b.File
		}
		if a.Line != b.Line {
			return a.Line < b.Line
		}
		if a.Code != b.Code {
			return a.Code < b.Code
		}
		return a.Key < b.Key
	})
	out := []Finding{}
	for _, f := range c.found {
		if !seen[f.Key] {
			seen[f.Key] = true
			out = append(out, f)
		}
	}
	return out, nil
}

// checker holds what every rule shares.
type checker struct {
	m      *Module
	pol    *Policy
	models map[*TypeInfo]*Model
	routes []*Route
	roots  []*Root
	found  []Finding
}

func newChecker(m *Module, pol *Policy) *checker {
	c := &checker{m: m, pol: pol}
	c.models = m.Models(pol)
	c.findRoutes()
	return c
}

func (c *checker) add(code string, f *File, line int, message, hint, key string) {
	c.found = append(c.found, Finding{Code: code, File: f.Rel, Line: line, Message: message,
		Hint: hint, Key: code + "|" + key})
}
