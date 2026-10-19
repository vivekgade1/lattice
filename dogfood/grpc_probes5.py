"""Probes of gRPC round 5 (docs/results/2026-10-18-grpc-round5-prereg.md, with Amendment 1).

Arms (every one implemented; an arm whose input is missing says so at run time and is NOT RUN, never
a pass; ``--self-test`` runs the machinery on stand-in fixtures for the inputs later stages build):

* ``--arm 0``  static, gated (section 10.1): split, byte-compat, harness API, code names, links,
  ledger, the extractor fixtures, protected directories, rehearsal, arms, determinism, no-write,
  unit tests, scorer cases, the held-out process, budgets, regression.
* ``--arm G``  development (10.2): N, deskd, sessions, flows, leaks, round 4 re-run, timings, and
  kilnworks / harborline / toolshed as reported post hoc rows.
* ``--arm M``  PM-S5 (10.3): Streak static byte-compat at round-5 code against ``git archive 978b468``.
* ``--arm L``  the build round on the toy proto through the ``claude`` CLI only (10.4).
* ``--arm H5`` the gated held-out stage (10.5): scored ONCE per ``freeze.sha256`` through the global
  ledger; ``--rehearse`` runs it end to end on any app with a scratch ledger and no recorded-hash check.
* ``--arm SG`` PM-SG (9): a grpc-go Streak over MySQL, same ledger and rules; REC and FLOWS are
  NOT SUPPORTED on MySQL (a library gap), CD is the author's own spec on its app.

The runner checks its own sha256 (never special-cased), the scorer's, ``grpc_truth5.py``'s, the
contract's, ``freeze5.sha256``, the app freeze and the ledger chain against Amendments 2-4 BEFORE any
metric. The ledger is ``docs/results/data/2026-10-18-grpc-round5/ledger.jsonl`` resolved from this
file's own location; no flag moves it (``--process-test`` / ``--rehearse`` use a scratch ledger and
refuse the real one). The ``blind`` / ``post_hoc`` label of every row is ``grpc_scorer5.provenance5``'s.

    export UV_NO_SYNC=1 PYTHONDONTWRITEBYTECODE=1
    uv run python dogfood/grpc_probes5.py --arm 0 --base "$SCRATCH/base" --corpora "$SCRATCH/corpora" --out "$D/arm0"
    uv run python dogfood/grpc_probes5.py --arm G --base ... --corpora ... --out "$D/armG"
    uv run python dogfood/grpc_probes5.py --arm M --streak ~/Documents/streak --streak-rev a0679e9,1d00ede --base ... --out "$D/armM"
    uv run python dogfood/grpc_probes5.py --arm L --fixture tests/grpc/fixtures/toybuild5 --cap-usd 5 --out "$D/armL"
    uv run python dogfood/grpc_probes5.py --arm H5 --rehearse --app "$SCRATCH/corpora/toolshed" --out "$SCRATCH/reh"
    uv run python dogfood/grpc_probes5.py --arm H5 --app <ho5 app> --out "$D/ho5"     # once, after Amendment 3

Internal sub-commands: ``emit-det5``, ``nw-work5``, ``pms-driver`` (subprocesses) and ``--check <name>``
(one probe, exit 0 on PASS: what ``grpc_mutants5.py`` runs).
"""

# ruff: noqa: E501 -- probe texts and table rows are data lines
from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO))

import grpc_contract5 as c5  # noqa: E402
import grpc_probes as r1  # noqa: E402
import grpc_probes2 as q2  # noqa: E402
import grpc_probes3 as p3  # noqa: E402
import grpc_probes4 as p4  # noqa: E402
import grpc_scorer4 as sc4  # noqa: E402
import grpc_scorer5 as sc5  # noqa: E402
import grpc_truth5 as t5  # noqa: E402

sha = r1.sha
Probe = r1.Probe
Row = p3.Row
FIXTURES = REPO / "tests" / "grpc" / "fixtures"
DATA = REPO / "docs/results/data/2026-10-18-grpc-round5"
PREREG = REPO / "docs/results/2026-10-18-grpc-round5-prereg.md"
BASE = "978b468"  # the commit every byte-compat promise and non-Go budget is counted from
RUNNER = "dogfood/grpc_probes5.py"
HARBOR_FREEZE_SHA = p4.HARBOR_FREEZE_SHA
GO_SRC = "lattice/gocheck/src"
GRPC_GO = ("goextract_grpc.go", "goextract_grpc_auth.go", "goextract_grpc_codes.go")

MAY_CHANGE = (  # section 12
    f"{GO_SRC}/goextract_grpc.go",
    f"{GO_SRC}/goextract_proto.go",
    "lattice/importer/frontends/go_grpc.py",
    "lattice/spec/grpc.py",
    "lattice/studio/rebuild.py",
    "lattice/mcp/guard.py",
)
CREATES = (  # section 12 "creates" (prefix match)
    f"{GO_SRC}/goextract_grpc_auth.go",
    f"{GO_SRC}/goextract_grpc_codes.go",
    f"{GO_SRC}/goextract_sqlraw.go",
    "dogfood/grpc_truth5.py",
    "dogfood/grpc_contract5.py",
    "dogfood/grpc_probes5.py",
    "dogfood/grpc_scorer5.py",
    "dogfood/grpc_scorer5_cases.json",
    "dogfood/grpc_mutants5.py",
    "tests/grpc/test_",
    "tests/studio/test_rebuild_grpc5.py",
    "tests/mcp/test_guard_dirs5.py",
    *(
        f"tests/grpc/fixtures/{d}"
        for d in (
            "descwrap5",
            "builderopt5",
            "status5",
            "audience5",
            "sqlraw5",
            "truth5",
            "harness5",
            "toybuild5",
        )
    ),
    "docs/results/data/2026-10-18-grpc-round5",
    "docs/results/2026-10-18-grpc-round5-results.md",
    "docs/results/2026-10-18-grpc-round5-prereg.md",
)
NAMED_FROZEN = (  # hashed at BASE and now
    "lattice/importer/workflows.py",
    "lattice/importer/transport.py",
    "lattice/spec/process.py",
    "lattice/importer/outside.py",
    "dogfood/grpc_contract4.py",
    "dogfood/grpc_flow_scorer.py",
    "dogfood/grpc_probes.py",
    "dogfood/grpc_probes2.py",
    "dogfood/grpc_probes3.py",
    "dogfood/grpc_probes4.py",
    "dogfood/grpc_scorer3.py",
    "dogfood/grpc_scorer4.py",
)
FROZEN_ROOTS = ("lattice", "dogfood", "tests", "bench")
BUDGETS = {  # (file or "group", cap) -- insertions + deletions against BASE (Go files: against S0)
    "lattice/spec/grpc.py": 6,
    "lattice/importer/frontends/go_grpc.py": 20,
    "lattice/studio/rebuild.py": 90,
    "lattice/mcp/guard.py": 30,
    f"{GO_SRC}/goextract_proto.go": 40,
}
GO_GROUP_CAP = 650
SQLRAW_CAP = 300
FILE_LINE_CAP = 1200
FAMILY_STAND_IN = {  # self-test: what stands in for a fixture family a later stage builds
    "descwrap5": "wiring4",
    "builderopt5": "wiring4",
    "status5": "codes4",
    "audience5": "authwire4",
    "sqlraw5": "tables4",
}

PRED = {
    "HO5-RPC": "recall 0.45-1.00 / precision 0.50-1.00 (point 0.82 / 0.85); P 0.35",
    "HO5-SAFE": "0-3 confident-wrong (point 0); P(0) 0.75",
    "HO5-CODES": "recall 0.15-0.85 / precision 0.70-1.00 (point 0.45 / 0.92); P 0.2",
    "HO5-AUTH": "P(0 wrong) 0.85; unclear 0.00-1.00 (point 0.35); P 0.5",
    "HO5-TABLES": "recall 0.80-1.00 (point 1.00); P 0.9",
    "HO5-REC": "coverage 0.95-1.00, agreement 0.70-1.00 (point 0.95); P 0.65",
    "HO5-FLOWS": "truth replaying >= 0.8 of flows; verified 0-10 (point 2); edge 0.00-0.60 (point 0.20); coverage 0-6 (point 2); P 0.3",
    "HO5-DET": "10/10 identical; P 0.95",
    "HO5-NW": "0, 0, 0; P 0.95",
    "HO5-SQLT": "0.0-1.0 (point 0.6)",
}
RULE = {
    "HO5-RPC": "recall >= 0.90 and precision >= 0.95 (rpc name AND state must match; `unread` counts wrong)",
    "HO5-SAFE": "S8: 0 confident-wrong states or auth labels",
    "HO5-CODES": "S9: micro recall >= 0.70 and micro precision >= 0.90 (implemented rpcs, OK excluded; unread is no extra); S11 count reported",
    "HO5-AUTH": "S7: 0 wrong (hard) and unclear <= 0.50 of the truth's implemented + unimplemented rpcs",
    "HO5-TABLES": "recall >= 0.90 (`tables_source` reported beside it)",
    "HO5-REC": "S5 (coverage >= 0.95, agreement 1.00, supplementary 0) + S6 not FAIL + 0 auth_refused + 0 login_failed on truth-implemented non-public rpcs + every disagreement isolated",
    "HO5-FLOWS": "S13: verified >= 1, 0 verified-but-failing, edge recall >= 0.30, coverage >= 2 truth flows (truth replay by H3, links applied)",
    "HO5-DET": "10/10 identical, no absolute path, time or port",
    "HO5-NW": "0 changed, 0 new, 0 claude calls",
    "HO5-SQLT": "reported only (S14)",
}


def git(*args: str, cwd: Path = REPO) -> str:
    return p3.git(*args, cwd=cwd)


def gocmd() -> str:
    return shutil.which("go") or "go"


class RunnerError(Exception):
    """A problem of this runner (or of the app layout): raised BEFORE any metric exists."""


# ---- the Go declaration tool (P5-SPLIT) -----------------------------------------------------------

GO_DECLS = r"""package main

import (
	"bytes"
	"crypto/sha256"
	"fmt"
	"go/ast"
	"go/format"
	"go/parser"
	"go/token"
	"os"
	"sort"
	"strings"
)

type decl struct{ kind, name, text string }

func decls(path string) (string, string, []decl) {
	src, err := os.ReadFile(path)
	if err != nil {
		panic(err)
	}
	fset := token.NewFileSet()
	f, err := parser.ParseFile(fset, path, src, parser.ParseComments)
	if err != nil {
		panic(err)
	}
	var imports string
	var out []decl
	for _, d := range f.Decls {
		start := d.Pos()
		var kind, name string
		switch x := d.(type) {
		case *ast.FuncDecl:
			if x.Doc != nil {
				start = x.Doc.Pos()
			}
			kind, name = "func", x.Name.Name
			if x.Recv != nil && len(x.Recv.List) > 0 {
				var b bytes.Buffer
				format.Node(&b, fset, x.Recv.List[0].Type)
				name = b.String() + "." + name
			}
		case *ast.GenDecl:
			if x.Doc != nil {
				start = x.Doc.Pos()
			}
			kind = x.Tok.String()
			var names []string
			for _, s := range x.Specs {
				switch sp := s.(type) {
				case *ast.TypeSpec:
					names = append(names, sp.Name.Name)
				case *ast.ValueSpec:
					for _, n := range sp.Names {
						names = append(names, n.Name)
					}
				}
			}
			name = strings.Join(names, ",")
			if x.Tok == token.IMPORT {
				imports += string(src[fset.Position(d.Pos()).Offset:fset.Position(d.End()).Offset]) + "\n"
				continue
			}
		}
		text := string(src[fset.Position(start).Offset:fset.Position(d.End()).Offset])
		out = append(out, decl{kind, name, text})
	}
	return f.Name.Name, imports, out
}

func main() {
	switch os.Args[1] {
	case "decls":
		var lines []string
		for _, p := range os.Args[2:] {
			_, _, ds := decls(p)
			for _, d := range ds {
				lines = append(lines, fmt.Sprintf("%x %s %s", sha256.Sum256([]byte(d.text)), d.kind, d.name))
			}
		}
		sort.Strings(lines)
		fmt.Println(strings.Join(lines, "\n"))
	case "split":
		pkg, imports, ds := decls(os.Args[2])
		outs := os.Args[3:]
		per := (len(ds) + len(outs) - 1) / len(outs)
		for i, o := range outs {
			lo, hi := i*per, (i+1)*per
			if hi > len(ds) {
				hi = len(ds)
			}
			var b strings.Builder
			b.WriteString("package " + pkg + "\n\n" + imports + "\n")
			for _, d := range ds[lo:hi] {
				b.WriteString(d.text + "\n\n")
			}
			formatted, err := format.Source([]byte(b.String()))
			if err != nil {
				panic(err)
			}
			os.WriteFile(o, formatted, 0o644)
		}
	}
}
"""


def go_decl_tool(scratch: Path) -> Path:
    d = scratch / "decltool"
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.go").write_text(GO_DECLS)
    (d / "go.mod").write_text("module decltool\n\ngo 1.21\n")
    exe = d / "decltool"
    proc = subprocess.run(
        [gocmd(), "build", "-o", str(exe), "."],
        cwd=d,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "GOFLAGS": "-mod=mod", "GOTOOLCHAIN": "local"},
    )
    if proc.returncode != 0:
        raise RunnerError(f"the go declaration tool did not build: {proc.stderr[-400:]}")
    return exe


def go_decls(tool: Path, files: list[Path]) -> list[str]:
    proc = subprocess.run(
        [str(tool), "decls", *map(str, files)], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RunnerError(f"declaration tool failed: {proc.stderr[-300:]}")
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


def gofmt_dirty(files: list[Path]) -> list[str]:
    gofmt = shutil.which("gofmt") or str(Path(gocmd()).with_name("gofmt"))
    proc = subprocess.run(
        [gofmt, "-l", *map(str, files)], capture_output=True, text=True, check=False
    )
    return (
        [ln for ln in proc.stdout.splitlines() if ln.strip()]
        if proc.returncode == 0
        else ["gofmt failed"]
    )


# ---- helpers shared by the probes -----------------------------------------------------------------


def corpora_paths(corpora: Path | None) -> dict[str, Path | None]:
    """The dev corpora of ``--corpora`` (the section-15 layout): deskd, kilnworks and harborline parents
    and the toolshed app directory."""
    if corpora is None:
        return {"deskd": None, "kiln": None, "harbor": None, "toolshed": None}
    c = Path(corpora)

    def has(p: Path) -> Path | None:
        return p if p.exists() else None

    return {
        "deskd": has(c / "heldout" / "deskd"),
        "kiln": has(c / "heldout2"),
        "harbor": has(c / "heldout3"),
        "toolshed": has(c / "toolshed"),
    }


def module_dirs(root: Path) -> list[tuple[str, str]]:
    """Every Go module under ``root`` as ``(parent, name)`` for ``extract_json``."""
    return sorted((str(p.parent.parent), p.parent.name) for p in root.rglob("go.mod"))


def extract_targets(ctx: dict[str, Any]) -> list[tuple[str, str]]:
    targets = [(str(FIXTURES), "notes"), (str(FIXTURES), "protos"), (str(FIXTURES), "sessions")]
    c = corpora_paths(ctx.get("corpora"))
    if c["deskd"]:
        targets.append((str(Path(c["deskd"]).resolve().parent), "deskd"))
    if c["kiln"]:
        targets.append((str(c["kiln"]), p3.KILN_PKG))
    if c["harbor"]:
        targets.append((str(c["harbor"]), "harborline"))
    if c["toolshed"]:
        targets.append((str(Path(c["toolshed"]).parent), "toolshed"))
    return targets


def harness_of(app_dir: Path) -> dict[str, Any]:
    """``make_app`` / ``build_db`` / ``RUNTIME`` / ``USERS`` of an app, imported exactly as the library
    imports them (``studio.toml`` ``[app]``), no argument mapping of any kind."""
    from lattice.importer.frontends.go_gin import _harness_attr

    cfg = tomllib.loads((app_dir / "studio.toml").read_text())["app"]
    mod = cfg["app"].partition(":")[0]
    return {
        "cfg": cfg,
        "make_app": _harness_attr(app_dir, cfg["app"]),
        "build_db": _harness_attr(app_dir, cfg["db"]),
        "runtime": _harness_attr(app_dir, cfg["runtime"]),
        "users": _harness_attr(app_dir, f"{mod}:USERS"),
    }


def replay_all(
    h: dict[str, Any], truth: dict[str, Any], *, source: Any = None, strip_links: bool = False
) -> list[t5.FlowResult]:
    return [
        t5.replay_flow(
            f,
            [] if strip_links else truth["flow_links"].get(f["name"], []),
            make_app=h["make_app"],
            build_db=h["build_db"],
            runtime=h["runtime"],
            source=source,
        )
        for f in truth["flows"]
    ]


def database_source(h: dict[str, Any]) -> tuple[Any, Any]:
    """``(source, closer)`` for an app's database: SQLite files, or a throwaway MySQL server
    (``[app] database = "mysql"``; label ``lattice.owner=w-gr``, removed by ``closer``)."""
    if h["cfg"].get("database") != "mysql":
        return t5.SqliteSource(), lambda: None
    os.environ.setdefault("LATTICE_CONTAINER_OWNER", "w-gr")
    from lattice.spec.dbserver import remove_all, server_for

    server, _ = server_for("mysql")
    return t5.ServerSource(server), remove_all


# ---- the frozen files and the budgets ---------------------------------------------------------------


def numstat(path: str, base: str = BASE) -> int:
    ns = git("diff", "--numstat", base, "--", path).split()
    return int(ns[0]) + int(ns[1]) if len(ns) >= 2 and ns[0].isdigit() else 0


def line_count(path: Path) -> int:
    return len(path.read_bytes().splitlines()) if path.is_file() else 0


def frozen_check(ctx: dict[str, Any]) -> dict[str, Any]:
    """Section 12: no tracked change in the frozen paths against BASE, the named frozen hashes, every
    changed or new path inside 'may change' or 'creates', the budgets, the corpora's freezes, the dev
    truth hashes (``freeze-dev5.sha256``)."""
    out: dict[str, Any] = {}
    changed = sorted(
        {
            ln.split("\t", 1)[1]
            for ln in git("diff", "--name-status", BASE, "--", *FROZEN_ROOTS).splitlines()
            if ln.strip()
        }
    )
    untracked = [
        u
        for u in git("ls-files", "--others", "--exclude-standard", "--", *FROZEN_ROOTS).split()
        if "/__pycache__/" not in u
    ]

    def allowed(path: str) -> bool:
        return path in MAY_CHANGE or any(path == a or path.startswith(a) for a in CREATES)

    named = {}
    for f in NAMED_FROZEN:
        was = hashlib.sha256(
            subprocess.run(
                ["git", "show", f"{BASE}:{f}"], cwd=REPO, capture_output=True, check=False
            ).stdout
        ).hexdigest()
        now = sha((REPO / f).read_bytes()) if (REPO / f).is_file() else ""
        named[f] = {"base": was[:16], "now": now[:16], "same": was == now}
    out["named_frozen"] = named
    out["frozen_files_changed"] = sorted(
        [c for c in changed if not allowed(c)] + [f for f, v in named.items() if not v["same"]]
    )
    out["unexpected_untracked"] = sorted(u for u in untracked if not allowed(u))
    out["tracked_changed_vs_base"] = changed
    out["staged"] = sorted(git("diff", "--name-only", "--cached").split())
    budgets: dict[str, Any] = {}
    for f, cap in BUDGETS.items():
        if f.startswith(GO_SRC):
            s0 = ctx.get("s0")
            n = _numstat_vs(Path(s0), f) if s0 else numstat(f)
        else:
            n = numstat(f)
        budgets[f] = {"insertions+deletions": n, "budget": cap, "ok": n <= cap}
    grp = [REPO / GO_SRC / g for g in GRPC_GO]
    present = [g for g in grp if g.is_file()]
    split = len(present) == 3
    if ctx.get("s0") and split:
        n = sum(_numstat_vs(Path(ctx["s0"]), f"{GO_SRC}/{g}") for g in GRPC_GO)
        budgets["goextract_grpc*.go (3 files, total, vs S0)"] = {
            "insertions+deletions": n,
            "budget": GO_GROUP_CAP,
            "ok": n <= GO_GROUP_CAP,
        }
    sqlraw = REPO / GO_SRC / "goextract_sqlraw.go"
    if sqlraw.is_file():
        budgets["goextract_sqlraw.go"] = {
            "lines": line_count(sqlraw),
            "budget": SQLRAW_CAP,
            "ok": line_count(sqlraw) <= SQLRAW_CAP,
        }
    for g in present:
        budgets[f"cap {g.name}"] = {
            "lines": line_count(g),
            "budget": FILE_LINE_CAP,
            "ok": line_count(g) <= FILE_LINE_CAP,
        }
    out["budgets"] = budgets
    c = corpora_paths(ctx.get("corpora"))
    freezes = {
        "deskd": (c["deskd"] and Path(c["deskd"]).resolve().parent, p3.DESKD_FREEZE_SHA),
        "kilnworks": (c["kiln"] and Path(c["kiln"]) / p3.KILN_PKG, p3.KILN_FREEZE_SHA),
        "harborline": (c["harbor"] and Path(c["harbor"]) / "harborline", HARBOR_FREEZE_SHA),
    }
    for name, (root, want) in freezes.items():
        if root:
            fz = Path(root) / "freeze.sha256"
            out[f"{name}_freeze_ok"] = fz.is_file() and sha(fz.read_bytes()) == want
            out[f"{name}_files_changed"] = (
                r1._check_sha_file(fz, Path(root)) if fz.is_file() else ["freeze.sha256 missing"]
            )
    dev = DATA / "freeze-dev5.sha256"
    out["dev_truth_hashes_changed"] = (
        r1._check_sha_file(dev, REPO) if dev.is_file() else ["freeze-dev5.sha256 missing"]
    )
    out["ok"] = (
        not out["frozen_files_changed"]
        and not out["unexpected_untracked"]
        and not out["staged"]
        and all(b["ok"] for b in budgets.values())
        and all(out[k] for k in out if k.endswith("_freeze_ok"))
        and not any(
            out[k] for k in out if k.endswith("_files_changed") and k != "frozen_files_changed"
        )
        and not out["dev_truth_hashes_changed"]
    )
    return out


def _numstat_vs(s0: Path, rel: str) -> int:
    """insertions + deletions of ``rel`` against an extracted S0 tree (a missing S0 file counts whole)."""
    old = s0 / rel
    new = REPO / rel
    if not new.is_file():
        return 0
    a = old.read_text().splitlines() if old.is_file() else []
    b = new.read_text().splitlines()
    proc = subprocess.run(
        [
            "git",
            "diff",
            "--no-index",
            "--numstat",
            str(old if old.is_file() else "/dev/null"),
            str(new),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    ns = proc.stdout.split()
    return int(ns[0]) + int(ns[1]) if len(ns) >= 2 and ns[0].isdigit() else abs(len(a) - len(b))


# ---- Arm 0 -----------------------------------------------------------------------------------------


def p5_split(ctx: dict[str, Any]) -> Probe:
    """Step 0 is a pure move: the multiset of top-level declarations (source text, comments
    included) of ``goextract_grpc.go`` at BASE equals that of the three files at S0, ``gofmt -l`` is
    clean, and ``gocheck extract`` is byte-identical on the corpora and every fixture module."""
    p = Probe(
        "P5-SPLIT", "identical decls; extract byte-identical on 7 corpora + all fixtures", "exact"
    )
    scratch = Path(ctx["scratch"])
    base = ctx.get("base")
    if base is None:
        return p.not_run("no --base: git archive of 978b468 was not given")
    base = Path(base)
    tool = go_decl_tool(scratch)
    old = base / GO_SRC / GRPC_GO[0]
    if ctx.get("self_test"):
        d = scratch / "split-standin"
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        subprocess.run([str(tool), "split", str(old), *(str(d / g) for g in GRPC_GO)], check=True)
        new_files = [d / g for g in GRPC_GO]
        control = (d / "bad").mkdir() or d / "bad"
        shutil.copy(new_files[0], control / GRPC_GO[0])
        shutil.copy(new_files[1], control / GRPC_GO[1])
        text = new_files[2].read_text()
        (control / GRPC_GO[2]).write_text(
            text.replace("func ", "func  ", 1) if "func " in text else text + "\nvar x = 1\n"
        )
        tree_new, kind = d, "synthetic S0 (self-test stand-in)"
    else:
        s0 = ctx.get("s0")
        if s0 is None:
            return p.not_run("no --s0: the extracted tree of the S0 commit was not given")
        new_files = [Path(s0) / GO_SRC / g for g in GRPC_GO]
        if not all(f.is_file() for f in new_files):
            return p.not_run(
                "the S0 tree lacks goextract_grpc_auth.go / goextract_grpc_codes.go (Stage B step 0 not done)"
            )
        tree_new, kind, control = Path(s0), "S0", None
    was = go_decls(tool, [old])
    now = go_decls(tool, new_files)
    dirty = gofmt_dirty(new_files)
    row: dict[str, Any] = {
        "decls at BASE": len(was),
        f"decls at {kind}": len(now),
        "gofmt -l": dirty,
        "multiset equal": was == now,
    }
    ok = was == now and not dirty
    if control is not None:
        bad = go_decls(tool, [control / g for g in GRPC_GO])
        row["negative control (one decl altered) detected"] = bad != was
        ok = ok and bad != was
    # gocheck extract byte-identical (the S0 tree's own binary vs BASE's)
    targets = extract_targets(ctx) + [
        t for t in module_dirs(FIXTURES) if t[1] not in ("notes", "protos", "sessions")
    ]
    if ctx.get("self_test"):
        targets = extract_targets(ctx)[:3]
        row["extract compared"] = "N, T, sessions only (self-test: no S0 tree to build)"
    else:
        a = p3._extract_hashes(base, targets, scratch, "gocheck-base")
        b = p3._extract_hashes(tree_new, targets, scratch, "gocheck-s0")
        diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        row["extract targets"] = len(targets)
        row["extract differ"] = diff
        ok = ok and not diff and "error" not in a and "error" not in b
    return p.done(
        ok,
        row,
        {
            "decls only at BASE": sorted(set(was) - set(now))[:5],
            "decls only at S0": sorted(set(now) - set(was))[:5],
        },
    )


def _same_bytes(a: Path, b: Path) -> list[str]:
    """Files under ``a`` (a baseline tree) that differ from, or are missing in, ``b``."""
    out = []
    for f in sorted(a.rglob("*")):
        if f.is_file() and "__pycache__" not in f.parts:
            g = b / f.relative_to(a)
            if not g.is_file() or g.read_bytes() != f.read_bytes():
                out.append(str(f.relative_to(a)))
    return out


H1B = re.compile(r"build_db\(p := (Path\(tmp\) / \"[a-z]+\.db\")\) or p")


def p5_bc_ast(base: Path) -> Probe:
    """``spec/grpc.py``: identical to BASE once H1b's three one-line changes are undone; the frozen
    library files, the bridge and every other Go source byte-identical."""
    p = Probe(
        "P5-BC-AST",
        "grpc.py differs only at the 3 H1b sites; frozen files byte-identical",
        "identical",
    )
    bad: list[str] = []
    new_src = (REPO / "lattice/spec/grpc.py").read_text()
    undone, n_sites = H1B.subn(r"build_db(\1)", new_src)
    if ast.dump(ast.parse(undone)) != ast.dump(
        ast.parse((base / "lattice/spec/grpc.py").read_text())
    ):
        bad.append("lattice/spec/grpc.py: differs beyond the 3 H1b sites")
    if n_sites not in (0, 3):
        bad.append(f"lattice/spec/grpc.py: {n_sites} H1b sites (expected 0 or 3)")
    for f in (
        "lattice/importer/transport.py",
        "lattice/spec/process.py",
        "lattice/importer/workflows.py",
        "lattice/importer/outside.py",
    ):
        if (base / f).read_bytes() != (REPO / f).read_bytes():
            bad.append(f"{f}: bytes differ")
    bad += [
        f"lattice/grpcbridge/{x}"
        for x in _same_bytes(base / "lattice/grpcbridge", REPO / "lattice/grpcbridge")
    ]
    skip = {Path(m).name for m in MAY_CHANGE}
    src_base, src_new = base / GO_SRC, REPO / GO_SRC
    bad += [f"{GO_SRC}/{x}" for x in _same_bytes(src_base, src_new) if Path(x).name not in skip]
    return p.done(not bad, {"H1b sites found": n_sites, "differences": len(bad)}, bad)


def _declared_strip(ex: dict[str, Any], *, sql: bool, flips: bool) -> dict[str, Any]:
    """The extract without what section 13 declares may differ: interceptor-kind codes (E3d),
    ``custom_options`` (E4c), tables / writes on ``database/sql`` corpora (E5), auth flips (E4a)."""
    ex = json.loads(json.dumps(ex))
    if sql:
        ex["tables"] = []
    for s in (ex.get("grpc") or {}).get("services", []):
        for r in s.get("rpcs", []):
            r["codes"] = [c for c in r.get("codes", []) if c.get("kind") != "interceptor"]
            r.pop("custom_options", None)
            if sql:
                r.pop("tables", None)
                r.pop("writes", None)
            if flips:
                r.pop("auth", None)
                r.pop("auth_reason", None)
    if flips:
        for i in (ex.get("grpc") or {}).get("interceptors", []):
            i.pop("kind", None)
    return ex


EXTRACT_DOCS = (
    "import json,sys\nfrom pathlib import Path\n"
    "from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd\n"
    "fe=GoGrpcFrontEnd(); out={}\n"
    "for root,pkg in json.loads(sys.argv[1]):\n"
    "    out[pkg]=fe.extract_json(Path(root),pkg)\n"
    "print(json.dumps(out))\n"
)


def p5_bc_ext(ctx: dict[str, Any]) -> Probe:
    """Extract and RepoModel on the corpora and fixtures against BASE: byte-identical, or different only
    by section 13's declared field-level differences (``--declared-sql``, ``--declared-flips``)."""
    p = Probe(
        "P5-BC-EXT",
        "identical, or declared field-level differences only",
        "no undeclared difference",
    )
    base, scratch = ctx.get("base"), Path(ctx["scratch"])
    if base is None:
        return p.not_run("no --base")
    targets = extract_targets(ctx)
    a = p3._extract_hashes(Path(base), targets, scratch, "gocheck-base")
    b = p3._extract_hashes(REPO, targets, scratch, "gocheck-new")
    diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    undeclared: list[str] = []
    declared: list[str] = []
    if diff:
        pkgs = sorted({k.split(":")[0] for k in diff})
        sel = [t for t in targets if t[1] in pkgs]
        docs = {}
        for name, tree in (("base", Path(base)), ("new", REPO)):
            proc = p3._tree_run(
                tree,
                [sys.executable, "-c", EXTRACT_DOCS, json.dumps(sel)],
                scratch,
                f"gocheck-{name}",
            )
            docs[name] = json.loads(proc.stdout.splitlines()[-1]) if proc.returncode == 0 else {}
        for pkg in pkgs:
            sql = pkg in ctx.get("declared_sql", ()) or pkg in ("harborline", "toolshed")
            flips = pkg in ctx.get("declared_flips", ())
            same = _declared_strip(
                docs["base"].get(pkg, {}), sql=sql, flips=flips
            ) == _declared_strip(docs["new"].get(pkg, {}), sql=sql, flips=flips)
            (declared if same else undeclared).append(pkg)
        undeclared += [d for d in diff if d.endswith(":model") and d.split(":")[0] in undeclared]
    return p.done(
        not undeclared and "error" not in a and "error" not in b,
        {
            "compared": len(a),
            "differ": diff,
            "declared (field level)": declared,
            "undeclared": undeclared,
        },
        diff,
    )


def p5_bc(ctx: dict[str, Any]) -> Probe:
    p = Probe("P5-BC", "HTTP 73/73 identical; declared gRPC differences only (p)", "all")
    base, scratch = ctx.get("base"), Path(ctx["scratch"])
    if base is None:
        return p.not_run("no --base: git archive of 978b468 was not given")
    base = Path(base)
    ast_probe = p5_bc_ast(base)
    http = r1.bc_probe({"base": base, "scratch": scratch})
    ext = p5_bc_ext(ctx)
    rec = {}
    for kind in ("n", "sessions"):
        a = p3._emit(base, ["emit-rec", kind, str(scratch)], scratch, "gocheck-base")
        b = p3._emit(REPO, ["emit-rec", kind, str(scratch)], scratch, "gocheck-new")
        rec[kind] = a == b and "error" not in a
    flows: dict[str, Any] = {}
    kinds = [("n", FIXTURES, FIXTURES)]
    desk = corpora_paths(ctx.get("corpora"))["deskd"]
    if desk:
        d = Path(desk).resolve().parent
        kinds.append(("desk", d, d))
    for kind, rb, rn in kinds:
        a = p3._emit(base, ["emit-det", kind, str(rb), str(scratch)], scratch, "gocheck-base")
        b = p3._emit(REPO, ["emit-det", kind, str(rn), str(scratch)], scratch, "gocheck-new")
        flows[kind] = (
            {"error": str(a.get("error", b.get("error")))[:200]}
            if "error" in a or "error" in b
            else {k: a[k] == b[k] for k in sorted(a)}
        )
    n_ok = all(
        flows.get("n", {}).get(k) is True for k in ("extract", "plans", "flows", "reconcile")
    )
    d_ok = ("desk" not in flows) or all(
        flows["desk"].get(k) is True for k in ("extract", "plans", "flows")
    )
    wf = r1.run_pytest(["tests/importer/test_workflows.py"])
    ok = (
        ast_probe.status == "PASS"
        and http.status == "PASS"
        and ext.status == "PASS"
        and all(rec.values())
        and n_ok
        and d_ok
        and wf["returncode"] == 0
    )
    return p.done(
        ok,
        {
            "ast (grpc.py at the H1b sites only; frozen files)": ast_probe.status,
            "HTTP outputs vs BASE": http.status,
            "HTTP outputs compared": (http.observed or {}).get("compared"),
            "extract + RepoModel": ext.status,
            "reconcile bytes on N and sessions": rec,
            "flows/plans/extract/reconcile on N and flows/plans/extract on deskd": flows,
            "HTTP workflow tests": wf["counts"],
        },
        {"ast": ast_probe.detail, "http": http.detail, "ext": ext.detail},
    )


def _scratch_app(src: Path, scratch: Path, label: str) -> Path:
    dst = scratch / f"app-{label}"
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return dst


def _patch(app: Path, old: str, new: str) -> None:
    f = app / "harness.py"
    s = f.read_text()
    if old not in s:
        raise RunnerError(f"the fixture harness no longer contains {old!r}")
    f.write_text(s.replace(old, new))


def docker_ready() -> str:
    """'' when a MySQL server for PM-SG can start, else the reason."""
    try:
        from lattice.spec.dbserver import docker_available
    except Exception as err:  # noqa: BLE001
        return f"dbserver: {type(err).__name__}"
    if not docker_available():
        return "docker is not available"
    try:
        import pymysql  # noqa: F401
    except ImportError:
        return "pymysql is not importable (run with a driver environment)"
    return ""


def p5_hapi(ctx: dict[str, Any]) -> Probe:
    """Two fixture harnesses written from Appendix A's text only pass the contract check; the three
    round-4 failure classes each give a printout line naming the fact."""
    p = Probe("P5-HAPI", "3/3 named; both fixtures all ok", "exact")
    scratch = Path(ctx["scratch"])
    sqlite = FIXTURES / "harness5" / "sqlite"
    if not sqlite.is_dir():
        return p.not_run("tests/grpc/fixtures/harness5/sqlite does not exist")
    code, lines = c5.run(sqlite)
    rows: dict[str, Any] = {
        "sqlite fixture": {
            "exit": code,
            "lines": len(lines),
            "not ok": [ln for ln in lines if " ok" not in ln],
        }
    }
    ok = code == 0
    classes = {
        "RUNTIME is the driver": (
            'RUNTIME = process_runtime(framework="go", auth=AUTH)',
            "RUNTIME = AUTH",
            "RUNTIME is AuthDriver",
        ),
        "build_db returns None": (
            "    con.close()\n    return path",
            "    con.close()",
            "build_db returned NoneType",
        ),
        "positional AuthDriver.rpc": (
            'username_field="name",\n    password_field="password",\n    token_field="token",\n)',
            '"name", "password", "token",\n)',
            "import TypeError",
        ),
    }
    named = {}
    for name, (old, new, want) in classes.items():
        app = _scratch_app(sqlite, scratch, "hapi-" + re.sub(r"\W+", "-", name))
        try:
            _patch(app, old, new)
        except RunnerError as err:
            named[name] = f"patch failed: {err}"
            continue
        c, out = c5.run(app)
        hit = any(ln.startswith(want) for ln in out)
        named[name] = {
            "exit": c,
            "named line": next((ln for ln in out if ln.startswith(want)), None),
            "ok": c != 0 and hit,
        }
    rows["failure classes"] = named
    ok = ok and all(isinstance(v, dict) and v["ok"] for v in named.values())
    mysql = FIXTURES / "harness5" / "mysql"
    why = docker_ready()
    if not mysql.is_dir():
        rows["mysql fixture"] = "NOT RUN: tests/grpc/fixtures/harness5/mysql does not exist"
        return _hapi_result(p, ok, rows)
    if why:
        rows["mysql fixture"] = f"NOT RUN: {why}"
        return _hapi_result(p, ok, rows)
    code, lines = c5.run(mysql)
    rows["mysql fixture"] = {
        "exit": code,
        "lines": len(lines),
        "not ok": [ln for ln in lines if " ok" not in ln],
    }
    return p.done(ok and code == 0, rows)


def _hapi_result(p: Probe, ok: bool, rows: dict[str, Any]) -> Probe:
    """The MySQL half could not run: a FAIL stands, a pass of the SQLite half is NOT RUN (not a pass)."""
    if not ok:
        return p.done(False, rows)
    p.status, p.observed = "NOT RUN", rows
    return p


CODE_SPELLINGS = t5.CODE_ROWS


def p5_code(ctx: dict[str, Any] | None = None) -> Probe:
    p = Probe("P5-CODE", "all mapped, 6 refused", "exact")
    bad: list[str] = []
    mapped = 0
    for n, up, go in CODE_SPELLINGS:
        for spelling in (up, go, n, str(n)):
            mapped += 1
            if t5.normalise_code(spelling) != up:
                bad.append(f"{spelling!r} -> {t5.normalise_code(spelling)} (want {up})")
    for spelling in ("CANCELED", "CANCELLED"):
        mapped += 1
        if t5.normalise_code(spelling) != "CANCELLED":
            bad.append(f"{spelling} must map to CANCELLED")
    refused = 0
    for value in ("FAILEDPRECONDITION", "failedPrecondition", 17, -1, "", None):
        try:
            t5.normalise_code(value, "$.x")
            bad.append(f"{value!r} was accepted")
        except t5.TruthError as err:
            refused += str(err) == f"unknown gRPC code {value!r} at $.x"
    return p.done(
        not bad and refused == 6,
        {"mapped": mapped, "refused": refused, "disagreements": len(bad)},
        bad,
    )


def p5_links(ctx: dict[str, Any]) -> Probe:
    """H3 on the truth-/5 fixture (a flow that passes only with links; ``link_unresolved``; a ``#``
    length; an int64 as string; base 0 and 1; the alias), and on deskd's links file (1-based alias)
    and toolshed's truth /4 (0-based) when given."""
    p = Probe("P5-LINKS", "every flow as its fixture truth states", "exact")
    sqlite, probe_file = FIXTURES / "harness5" / "sqlite", FIXTURES / "truth5" / "probe-flows.json"
    if not (sqlite.is_dir() and probe_file.is_file()):
        return p.not_run(
            "tests/grpc/fixtures/harness5/sqlite or truth5/probe-flows.json does not exist"
        )
    h = harness_of(sqlite)
    truth = t5.load_truth(sqlite / "truth.json")
    rows: dict[str, Any] = {}
    app_res = replay_all(h, truth)
    rows["harness5/sqlite truth flows"] = {
        r.name: r.label(truth["step_index_base"]) for r in app_res
    }
    ok = all(r.ok for r in app_res)
    doc = json.loads(probe_file.read_text())
    probe_rows: dict[str, Any] = {}
    for f in doc["flows"]:
        base = f.get("base", 0)
        links = t5.canonical_links(f.get("links", []), base, len(f["steps"]))
        flow = {
            "name": f["name"],
            "steps": [
                {
                    "rpc": s["rpc"],
                    "as": s["as"],
                    "message": s["request"],
                    "expect_code": s["expect"]["code"],
                    "expect": {k: v for k, v in s["expect"].items() if k != "code"},
                }
                for s in f["steps"]
            ],
        }
        res = t5.replay_flow(
            flow,
            [] if f.get("strip_links") else links,
            make_app=h["make_app"],
            build_db=h["build_db"],
            runtime=h["runtime"],
        )
        got = res.label(base)
        probe_rows[f["name"]] = {"got": got, "want": f["replay"]}
        ok = ok and got == f["replay"]
    rows["probe flows"] = probe_rows
    c = corpora_paths(ctx.get("corpora"))
    if c["deskd"] and not ctx.get("self_test"):
        desk = q2.load_desk(Path(c["deskd"]), Path(ctx["scratch"]))
        flows, links = q2._truth_for_scorer(desk)
        no_links = q2._truth_replay(desk, flows)
        with_links = []
        for f in desk.truth["flows"]:
            lk = (
                t5.canonical_links(
                    links.get(f["id"], links.get(f.get("name"), []))
                    if isinstance(links, dict)
                    else [],
                    1,
                    len(f["steps"]),
                )
                if links
                else []
            )
            flow = {
                "name": f["id"],
                "steps": [
                    {
                        "rpc": s["rpc"],
                        "as": s["user"],
                        "message": s["message"],
                        "expect_code": s["code"],
                        "expect": {},
                    }
                    for s in f["steps"]
                ],
            }
            with_links.append(
                t5.replay_flow(
                    flow, lk, make_app=desk.make, build_db=desk.build_db, runtime=desk.runtime
                )
            )
        rows["deskd (links file, 1-based alias)"] = {
            r.name: [no_links.get(r.name), r.ok] for r in with_links
        }
        ok = ok and all(r.ok or not no_links.get(r.name, False) for r in with_links)
    if c["toolshed"] and not ctx.get("self_test"):
        tdir = Path(c["toolshed"])
        tt = t5.load_truth(tdir / "truth.json", strict=False)
        res = replay_all(harness_of(tdir), tt)
        rows["toolshed truth /4 (0-based)"] = {r.name: r.label(0) for r in res}
        ok = ok and not any(r.reason in ("link_unresolved", "server_error") for r in res)
    return p.done(ok, rows)


def p5_ledger(ctx: dict[str, Any]) -> Probe:
    """H4: two ``--out`` on one target -> index 2; a third refused; a preflight refusal consumes none;
    a tampered line refuses; ``--process-test`` / ``--rehearse`` refuse the real ledger."""
    p = Probe("P5-LEDGER", "5/5", "5/5")
    root = Path(tempfile.mkdtemp(prefix="p5-ledger-"))
    res: dict[str, Any] = {}
    try:
        led = t5.Ledger(root / "l.jsonl")
        T = "a" * 64
        kw = {"arm": "H5", "target_freeze_sha256": T, "runner_sha256": "1" * 64}
        led.append(**kw, status="started", out="out-a")
        led.append(**kw, status="crashed_before_metric", out="out-a")
        idx, prior, why = led.may_start("H5", T)
        res["1 two --out on one target: index 2 after a crash"] = (
            idx == 2 and prior.get("crashed_before_metric") is True and not why
        )
        led.append(**kw, status="started", out="out-b")
        led.append(**kw, status="scored", out="out-b")
        res["2 a third run is refused"] = "third run" in led.may_start("H5", T)[2]
        led2 = t5.Ledger(root / "l2.jsonl")
        led2.append(**kw, status="refused_preflight", out="x")
        res["3 a preflight refusal consumes no run"] = (
            led2.run_index("H5", T) == 1 and led2.may_start("H5", T)[2] == ""
        )
        lines = led.raw_lines()
        (root / "t.jsonl").write_bytes(
            b"\n".join([lines[0].replace(b"out-a", b"out-z"), *lines[1:]]) + b"\n"
        )
        tampered = t5.Ledger(root / "t.jsonl")
        with contextlib.suppress(t5.TruthError):
            tampered.append(**kw, status="started")
        res["4 a tampered line breaks the chain and nothing is appended"] = bool(
            tampered.verify()
        ) and len(tampered.raw_lines()) == len(lines)
        real = t5.real_ledger_path(Path(__file__))
        proc = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--arm",
                "H5",
                "--process-test",
                "run",
                "--ledger",
                str(real),
                "--app",
                str(root),
                "--out",
                str(root / "o"),
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=REPO,
        )
        res["5 --process-test refuses the real ledger"] = (
            proc.returncode == 2 and "real ledger" in proc.stdout
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return p.done(all(v is True for v in res.values()) and len(res) == 5, res)


# ---- the extractor fixtures (E1-E5) and the build-loop units (L1-L3): machinery runs now --------------


def _suffix_key(key: str) -> tuple[str, str]:
    svc, _, m = key.partition("/")
    if not m:
        return "", svc
    return svc.rsplit(".", 1)[-1], m


def _case_rpcs(case: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """What a fixture case's truth says per rpc, in one shape: ``rpcs`` entries that are a state
    string or an object (``state``, ``codes``, ``unread``, ``auth``, ``tables``, ``writes``,
    ``custom_options``), or an ``auth`` map (authwire4's form)."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for k, v in (case.get("rpcs") or {}).items():
        out[_suffix_key(k)] = {"state": v} if isinstance(v, str) else dict(v)
    for k, v in (case.get("auth") or {}).items():
        out.setdefault(_suffix_key(k), {})["auth"] = v
    return out


def _find_rpc(
    got: dict[tuple[str, str], dict[str, Any]], key: tuple[str, str]
) -> dict[str, Any] | None:
    """The extracted rpc a truth key names: the exact ``(service, method)``, else the service name as a
    suffix of the extracted one, else the method alone when it is unique."""
    if key in got:
        return got[key]
    hits = [
        r for (svc, m), r in got.items() if m == key[1] and (not key[0] or svc.endswith(key[0]))
    ]
    return hits[0] if len(hits) == 1 else None


def fixture_probe(
    pid: str, family: str, prediction: str, rule: str, ctx: dict[str, Any], *, case_filter: str = ""
) -> Probe:
    """An extractor fixture family (``tests/grpc/fixtures/<family>/truth.json``, round-4's shape): each
    listed field of each listed rpc is compared exactly; an S8 wrong state or auth is a failure of its
    own; the truth must be hashed in ``freeze-dev5.sha256`` before library code (checked when that file
    exists)."""
    p = Probe(pid, prediction, rule)
    real = FIXTURES / family
    use = (
        FAMILY_STAND_IN.get(family, family)
        if ctx.get("self_test") and not (real / "truth.json").is_file()
        else family
    )
    tfile = FIXTURES / use / "truth.json"
    if not tfile.is_file():
        return p.not_run(
            f"tests/grpc/fixtures/{family}/truth.json does not exist (built with the extractor decisions, Stage B)"
        )
    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd

    truth = json.loads(tfile.read_text())["cases"]
    fe = GoGrpcFrontEnd()
    bad: list[str] = []
    s8: list[str] = []
    n = 0
    for case, want in truth.items():
        if case_filter and use == family and not case.startswith(case_filter):
            continue
        ex = fe.extract_json(FIXTURES / use, case)
        got = {
            _suffix_key(f"{s['name']}/{r['name']}"): r
            for s in ex["grpc"]["services"]
            for r in s["rpcs"]
        }
        for key, w in _case_rpcs(want).items():
            n += 1
            r = _find_rpc(got, key)
            if r is None:
                bad.append(f"{case}:{key}: rpc missing")
                continue
            have = {
                "state": r.get("state"),
                "codes": sorted(
                    {c["name"] for c in r.get("codes", []) if c.get("kind") != "unimplemented_stub"}
                ),
                "unread": any(u["what"] == "grpc_code_not_literal" for u in r.get("unread", [])),
                "auth": r.get("auth"),
                "tables": sorted(r.get("tables", [])),
                "writes": sorted(f"{x['table']}:{x['op']}" for x in r.get("writes", [])),
                "custom_options": r.get("custom_options", {}),
            }
            for f, v in w.items():
                if f == "codes":
                    v = sorted(v)
                if f == "writes":
                    v = sorted(v)
                if have.get(f) != v:
                    bad.append(f"{case}:{key}: {f} {have.get(f)!r} != {v!r}")
        if "tables" in want and "rpcs" not in want:
            n += 1
            names = sorted(t["table"] for t in ex["tables"])
            if names != sorted(want["tables"]):
                bad.append(f"{case}: tables {names} != {want['tables']}")
        if "tables_source" in want:
            n += 1
        if "tables_source" in want and ex.get("tables_source") != want["tables_source"]:
            bad.append(
                f"{case}: tables_source {ex.get('tables_source')!r} != {want['tables_source']!r}"
            )
        row = sc4.score_safe(
            {
                f"{k[0]}/{k[1]}": {
                    "state": w["state"],
                    "auth": w.get("auth", (_find_rpc(got, k) or {}).get("auth", "")),
                }
                for k, w in _case_rpcs(want).items()
                if "state" in w
            },
            {
                f"{k[0]}/{k[1]}": {"state": r.get("state"), "auth": r.get("auth")}
                for k, r in got.items()
            },
        )
        s8 += [f"{case}:{w[0]}" for w in row["wrong"]]
    dev = DATA / "freeze-dev5.sha256"
    hashed = not r1._check_sha_file(dev, REPO) if dev.is_file() else True
    obs = {
        "family": use,
        "rpc facts compared": n,
        "disagreements": len(bad),
        "S8 confident-wrong": s8,
        "truth hashed before library code": hashed,
    }
    if use != family:
        obs["stand-in"] = f"{use} stands in for {family} (self-test)"
    return p.done(not bad and not s8 and hashed and n > 0, obs, bad)


def pytest_probe(
    pid: str,
    files: list[str],
    select: str,
    want: int,
    prediction: str,
    rule: str,
    ctx: dict[str, Any],
) -> Probe:
    """A unit probe run through pytest: ``files`` must exist; the tests selected by ``-k select`` must all
    pass and number ``want``."""
    p = Probe(pid, prediction, rule)
    have = [f for f in files if (REPO / f).is_file()]
    if ctx.get("self_test"):
        d = Path(ctx["scratch"]) / f"standin-{pid}"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"test_{pid.lower().replace('-', '_')}.py"
        names = "_".join(select.split()) or "x"
        f.write_text("".join(f"def test_{names}_{i}():\n    assert True\n\n" for i in range(want)))
        r = r1.run_pytest([str(f)], cwd=d)
        n = r["counts"].get("passed", 0)
        return p.done(r["returncode"] == 0 and n == want, {"stand-in tests": n, "wanted": want})
    if len(have) != len(files):
        return p.not_run(
            f"missing {sorted(set(files) - set(have))} (built with the build-loop decisions, Stage B)"
        )
    r = r1.run_pytest(have, ["-k", select])
    n = r["counts"].get("passed", 0)
    return p.done(
        r["returncode"] == 0 and n == want and not r["failing"],
        {**r["counts"], "selected by": select, "wanted": want},
        r["failing"],
    )


def p5_prot(ctx: dict[str, Any]) -> Probe:
    return pytest_probe(
        "P5-PROT",
        ["tests/studio/test_rebuild_grpc5.py", "tests/mcp/test_guard_dirs5.py"],
        "prot",
        5,
        "L1/L1b unit fixtures: directory unchanged, file changed, file added (removed), ignored file kept, guard denies a path under it",
        "5/5",
        ctx,
    )


def p5_gen(ctx: dict[str, Any]) -> Probe:
    return pytest_probe(
        "P5-GEN",
        ["tests/studio/test_rebuild_grpc5.py"],
        "gen",
        3,
        "L2: generate before the gates; a failing generate is a named gate failure; no [grpc] byte-identical",
        "3/3",
        ctx,
    )


def p5_brief(ctx: dict[str, Any]) -> Probe:
    return pytest_probe(
        "P5-BRIEF",
        ["tests/studio/test_rebuild_grpc5.py"],
        "brief",
        1,
        "L3: brief and scope brief name the protos and the generator; protos implicitly protected",
        "exact",
        ctx,
    )


# ---- the apps, and the rows both the dev apps and the held-out are scored by ---------------------------


def load_app5(
    root: Path, scratch: Path, *, truth_path: Path | None = None, strict: bool = True
) -> q2.App:
    """An app of the round (``harness.py``, ``studio.toml``, ``truth.json`` at its root) through its own
    harness, imported as the library imports it; the extractor, the harness loader and the replay all
    use ``Path(root).resolve()``. NO argument shim of any kind (section 6): a harness that calls
    ``AuthDriver.rpc`` wrongly fails to import, and the contract check names it."""
    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd
    from lattice.profile import ApiProfile, Profile
    from lattice.spec import Spec

    root = Path(root).resolve()
    pkg = root.name
    os.environ.setdefault(f"{pkg.upper()}_BIN_DIR", str(scratch / f"{pkg}-bin"))
    os.environ.setdefault("LATTICE_HELDOUT_BUILD", str(scratch / "hbuild"))
    h = harness_of(root)
    fe = GoGrpcFrontEnd()
    profile = Profile(
        schemas={},
        api=ApiProfile(
            tables={}, table_names={}, roles=frozenset({"Public", "Member"}), error_body="fastapi"
        ),
    )
    return q2.App(
        name=pkg,
        root=root,
        fixtures=root.parent,
        pkg=pkg,
        fe=fe,
        extract=fe.extract_json(root.parent, pkg),
        model=fe.extract(root.parent, pkg),
        make=h["make_app"],
        raw_make=h["make_app"],
        build_db=h["build_db"],
        runtime=h["runtime"],
        users=h["users"],
        protos=(),
        proto_paths=(),
        spec=Spec(profile=profile, users=h["users"], protos=()),
        harness=h,
        truth=t5.load_truth(truth_path, strict=strict) if truth_path else None,
    )


def relabel(rows: list[Probe], label: str, new: str) -> list[Probe]:
    for pr in rows:
        key = pr.id.removeprefix(f"{label}-")
        pr.id = f"{new}-{key}"
        if f"HO5-{key}" in PRED:
            pr.prediction, pr.rule = PRED[f"HO5-{key}"], RULE[f"HO5-{key}"]
    return rows


def rpc_rows5(app: q2.App, new: str) -> list[Probe]:
    """RPC, SAFE, CODES, AUTH, TABLES by round 4's rows (S8, S9, S7) on a truth /5; the S11 count is
    reported with the CODES row."""
    rows = relabel(p4.rpc_rows4(app, "HO4"), "HO4", new)
    for pr in rows:
        if pr.id.endswith("-CODES") and isinstance(pr.observed, dict):
            pr.observed = {**pr.observed, "codes_normalised": app.truth.get("codes_normalised")}
    return rows


def rec_row5(app: q2.App, new: str, ctx: dict[str, Any]) -> Probe:
    return relabel([p4.rec_row4(app, "HO4", ctx)], "HO4", new)[0]


def replay_app(app: q2.App, source: Any = None) -> list[t5.FlowResult]:
    """H3 on every truth flow of an app object (links canonical in a /5 truth; a dev truth's 1-based
    scorer links are read as the alias form)."""
    out = []
    for f in app.truth["flows"]:
        links = app.truth.get("flow_links", {}).get(f["name"])
        if links is None:
            links = t5.canonical_links(
                app.truth.get("links", {}).get(f["name"], []), 1, len(f["steps"])
            )
        out.append(
            t5.replay_flow(
                f,
                links,
                make_app=app.make,
                build_db=app.build_db,
                runtime=app.runtime,
                source=source,
            )
        )
    return out


def flows_row5(app: q2.App, new: str, ctx: dict[str, Any], source: Any = None) -> Probe:
    """S13: round 2's flows scorer, with the truth flows that pass their own replay taken from H3 (links
    applied, dotted ``expect`` checked), the same function the contract check runs."""
    p = Probe(f"{new}-FLOWS", PRED["HO5-FLOWS"], RULE["HO5-FLOWS"])
    truth = app.truth
    results = replay_app(app, source)
    t0 = time.perf_counter()
    flows = q2.flows_of(app)
    secs = time.perf_counter() - t0
    ctx["flows"] = flows
    verified = [f for f in flows if f["status"] == "verified"]
    vbf = q2.replay_failures(app, verified)[0] if verified else []
    row = sc5.score_flows5(
        flows,
        truth["flows"],
        truth["links"],
        [r.as_json() for r in results],
        verified_but_failing=len(vbf),
        min_verified=1,
        edge_recall_bound=0.30,
        coverage_bound=2,
    )
    row["truth_edges_total"] = sum(len(v) for v in truth["links"].values())
    row["recovered_by_kind_status"] = {}
    for f in flows:
        k = row["recovered_by_kind_status"].setdefault(f["kind"], {})
        k[f["status"]] = k.get(f["status"], 0) + 1
    keys = (
        "truth_flows",
        "truth_flows_passing",
        "truth_replay_failures",
        "truth_edges_total",
        "recovered",
        "recovered_by_status",
        "recovered_by_kind_status",
        "verified",
        "verified_but_failing",
        "edge_recall",
        "coverage",
        "exact",
        "failing_codes",
        "unbound_reasons",
        "reasons",
    )
    ctx["row_flows"] = row
    return p.done(
        row["pass"] and len(truth["flows"]) >= 1,
        {**{k: row[k] for k in keys}, "seconds": round(secs, 1)},
        {
            "score": row,
            "vbf ids": vbf,
            "truth replay": [r.as_json() for r in results],
            "not verified": [
                {"id": f["id"], "kind": f["kind"], "status": f["status"], "reason": f.get("reason")}
                for f in flows
                if f["status"] != "verified"
            ],
        },
    )


def sqlt_row5(app: q2.App, new: str) -> Probe:
    got = {
        f"{s['name']}/{r['name']}": r for s in app.extract["grpc"]["services"] for r in s["rpcs"]
    }
    row = sc5.score_sqlt(
        {
            k: {"state": v["state"], "tables": app.truth["tables_by_rpc"].get(k, [])}
            for k, v in app.truth["rpcs"].items()
        },
        {
            k: {
                "state": r.get("state"),
                "tables": r.get("tables", []),
                "writes": r.get("writes", []),
            }
            for k, r in got.items()
        },
    )
    pr = Row(f"{new}-SQLT", PRED["HO5-SQLT"], RULE["HO5-SQLT"])
    return pr.reported(
        True,
        {k: row[k] for k in ("truth_pairs", "extracted_pairs", "matched", "recall", "precision")},
        row["per_rpc"],
    )


def emit_det5(root: str, scratch: str, pkg: str) -> None:
    """Every artifact of one app, hashed (the texts go to stderr); reconcile and flows only for a
    SQLite app (a MySQL app needs a server: its static artifacts only)."""
    from lattice.importer import workflows as wf

    app = load_app5(Path(root) / pkg, Path(scratch))
    texts = {
        "extract": json.dumps(app.extract, sort_keys=True),
        "plans": json.dumps(q2._plan_view(wf.static_rpc_plans(wf.rpc_views(app.extract)))),
        "model": app.model.to_json(),
        "describe": q2._describe_text(app),
    }
    if app.harness["cfg"].get("database") != "mysql":
        texts["flows"] = json.dumps(q2.flows_of(app), sort_keys=True)
        texts["reconcile"] = json.dumps(q2.reconcile_of(app))
    print(json.dumps({k: hashlib.sha256(v.encode()).hexdigest() for k, v in texts.items()}))
    print(json.dumps(texts), file=sys.stderr)


def det_runs5(label: str, argv_for: Any, roots: list[Path], n: int = 10) -> dict[str, Any]:
    """Round 3's ``_det_runs`` (``n`` processes with random hash seeds over two roots; every artifact hash
    identical), with the caller's own ``PYTHONPATH`` kept (a driver environment's extra packages)."""
    results: list[Any] = []
    leaks: list[str] = []
    extra = os.environ.get("PYTHONPATH", "")
    for i in range(n):
        root = roots[i % 2]
        proc = subprocess.run(
            argv_for(root),
            capture_output=True,
            text=True,
            check=False,
            cwd=REPO,
            env={
                **os.environ,
                "PYTHONHASHSEED": str(random.randrange(1, 2**31)),
                "PYTHONPATH": os.pathsep.join(x for x in (str(REPO), extra) if x),
            },
        )
        if proc.returncode != 0:
            results.append(("error", proc.stderr[-400:]))
            continue
        results.append(json.loads(proc.stdout.splitlines()[-1]))
        for key, text in json.loads(proc.stderr.splitlines()[-1]).items():
            scrubbed = text.replace(str(root), "")
            for rx, what in (
                (p3.RX_ABS, "absolute path"),
                (p3.RX_TIME, "time"),
                (p3.RX_PORT, "port"),
            ):
                m = rx.search(scrubbed)
                if m and key != "extract":
                    leaks.append(f"{label}:{key}: {what} {m.group(0)!r}")
    good = [r for r in results if isinstance(r, dict)]
    same = len({json.dumps(r, sort_keys=True) for r in good}) == 1
    return {
        "identical runs": len(good) if same else 0,
        "of": n,
        "leaks": sorted(set(leaks))[:3],
        "artifacts": sorted(good[0]) if good else [],
        "errors": [r for r in results if not isinstance(r, dict)],
        "ok": len(good) == n and same and not leaks,
    }


def det_row5(root: Path, scratch: Path, new: str) -> Probe:
    p = Probe(f"{new}-DET", PRED["HO5-DET"], RULE["HO5-DET"])
    me = str(Path(__file__).resolve())
    parent = root.resolve().parent
    copy = p3._copy_root(parent, scratch, f"app-{root.name}")
    res = det_runs5(
        root.name,
        lambda r: [sys.executable, me, "emit-det5", str(r), str(scratch), root.name],
        [parent, copy],
    )
    return p.done(res["ok"], {k: v for k, v in res.items() if k != "errors"}, res["errors"])


def nw_row5(root: Path, new: str, before: dict[str, Any], log: Path) -> Probe:
    after = q2._snapshot(root)
    changed = [x for x in before if before[x] != after.get(x)]
    created = [x for x in after if x not in before]
    calls = len([ln for ln in log.read_text().splitlines() if ln.strip()])
    return Probe(f"{new}-NW", PRED["HO5-NW"], RULE["HO5-NW"]).done(
        not changed and not created and calls == 0,
        {"changed": len(changed), "new": len(created), "claude calls": calls},
        changed + created,
    )


def claude_shim(scratch: Path) -> Path:
    """A recording ``claude`` that always fails: any model call from extraction, reconcile, flows, the
    scorer or the runner is counted (and breaks)."""
    shim = scratch / "shim"
    shim.mkdir(parents=True, exist_ok=True)
    log = scratch / "claude-calls.log"
    log.write_text("")
    (shim / "claude").write_text(f'#!/bin/sh\necho "$@" >> {log}\nexit 1\n')
    (shim / "claude").chmod(0o755)
    os.environ["PATH"] = f"{shim}:{os.environ['PATH']}"
    return log


def guarded(rehearse: bool, pid: str, fn: Any) -> list[Probe]:
    """Runs a row; in a rehearsal an exception is a ``CRASH`` row (counted by P5-REH), in a scored run
    it propagates (a crash before any metric: the ledger says so, no metric file is written)."""
    if not rehearse:
        out = fn()
        return out if isinstance(out, list) else [out]
    try:
        out = fn()
        return out if isinstance(out, list) else [out]
    except Exception as err:  # noqa: BLE001
        pr = Probe(pid, "", "")
        pr.status, pr.observed = "CRASH", f"{type(err).__name__}: {str(err)[:400]}"
        return [pr]


def not_supported(pid: str, why: str) -> Probe:
    pr = Probe(pid, "", "not a pass")
    pr.status, pr.observed = "NOT SUPPORTED", why
    return pr


def score_h5(root: Path, scratch: Path, rehearse: bool) -> tuple[list[Probe], dict[str, Any]]:
    """The HO5-* rows on a finished app (a SQLite app)."""
    ctx: dict[str, Any] = {}
    os.environ.setdefault("LATTICE_GOCHECK_CACHE", str(scratch / "gocache"))
    os.environ.setdefault("LATTICE_GRPCBRIDGE_CACHE", str(scratch / "bridgecache"))
    before = q2._snapshot(root)
    log = claude_shim(scratch)
    app = load_app5(root, scratch, truth_path=root / "truth.json", strict=not rehearse)
    if app.runtime is None:
        raise RunnerError("harness RUNTIME is None")
    probes: list[Probe] = []
    probes += guarded(rehearse, "HO5-RPC", lambda: rpc_rows5(app, "HO5"))
    probes += guarded(rehearse, "HO5-REC", lambda: rec_row5(app, "HO5", ctx))
    probes += guarded(rehearse, "HO5-FLOWS", lambda: flows_row5(app, "HO5", ctx))
    probes += guarded(rehearse, "HO5-SQLT", lambda: sqlt_row5(app, "HO5"))
    probes += guarded(rehearse, "HO5-DET", lambda: det_row5(root, scratch, "HO5"))
    probes += guarded(rehearse, "HO5-NW", lambda: nw_row5(root, "HO5", before, log))
    ctx["tables_source"] = app.extract.get("tables_source", "go")
    ctx["codes_normalised"] = app.truth.get("codes_normalised")
    return probes, ctx


def score_sg(root: Path, scratch: Path, rehearse: bool) -> tuple[list[Probe], dict[str, Any]]:
    """The SG-* rows on a finished app over MySQL: the static rows, the author's own spec (layers C
    and D) on a throwaway MySQL, determinism of the static artifacts, no-write. REC and FLOWS are NOT
    SUPPORTED on MySQL (``reconcile_with`` / ``run_rpc_flows`` build SQLite paths): a library gap."""
    ctx: dict[str, Any] = {}
    os.environ.setdefault("LATTICE_GOCHECK_CACHE", str(scratch / "gocache"))
    os.environ.setdefault("LATTICE_GRPCBRIDGE_CACHE", str(scratch / "bridgecache"))
    before = q2._snapshot(root)
    log = claude_shim(scratch)
    app = load_app5(root, scratch, truth_path=root / "truth.json", strict=not rehearse)
    probes: list[Probe] = []
    probes += guarded(
        rehearse, "SG-RPC", lambda: sg_labels(relabel(p4.rpc_rows4(app, "HO4"), "HO4", "SG"))
    )
    probes.append(
        not_supported(
            "SG-REC",
            "reconcile_with builds SQLite paths (grpc.py:553/595): not supported on MySQL this round",
        )
    )
    probes.append(
        not_supported(
            "SG-FLOWS",
            "run_rpc_flows builds SQLite paths (grpc.py:677): not supported on MySQL this round",
        )
    )
    probes += guarded(rehearse, "SG-CD", lambda: sg_cd(app, root, scratch))
    probes += guarded(rehearse, "SG-SQLT", lambda: sqlt_row5(app, "SG"))
    probes += guarded(rehearse, "SG-DET", lambda: det_row5(root, scratch, "SG"))
    probes += guarded(rehearse, "SG-NW", lambda: nw_row5(root, "SG", before, log))
    ctx["tables_source"] = app.extract.get("tables_source", "go")
    return probes, ctx


def sg_cd(app: q2.App, root: Path, scratch: Path) -> Probe:
    """The author's gRPC spec (``spec.py:SPEC``) on its own app, layers C and D, on a throwaway MySQL:
    a validity check of the author's work, reported."""
    pr = Row("SG-CD", "pass (P 0.7)", "reported (validity of the author)")
    why = docker_ready()
    if why:
        pr.status, pr.observed = "NOT RUN", f"no MySQL server: {why}"
        return pr
    spec_file = root / "spec.py"
    if not spec_file.is_file():
        pr.status, pr.observed = "NOT RUN", "the app has no spec.py"
        return pr
    from lattice.importer.frontends.go_gin import _harness_attr
    from lattice.spec import check

    spec = _harness_attr(root, "spec:SPEC")
    os.environ.setdefault("LATTICE_CONTAINER_OWNER", "w-gr")
    from lattice.spec.dbserver import remove_all

    try:
        rep = check(
            spec, app.make, app.build_db, layers="CD", runtime=app.runtime, database="mysql"
        )
    finally:
        remove_all()
    return pr.reported(
        not rep.findings,
        {"findings": len(rep.findings)},
        [str(getattr(f, "rule", f))[:160] for f in rep.findings][:20],
    )


SG_PRED = {
    "RPC": "0.80-1.00 / 0.85-1.00 (point 0.95 / 0.97); P(pass) 0.6",
    "SAFE": "0-2; P(0) 0.8",
    "CODES": "0.30-0.90 / 0.75-1.00 (point 0.60 / 0.92); P 0.3",
    "AUTH": "P(0 wrong) 0.85; unclear 0.00-0.80 (point 0.2); P 0.55",
    "TABLES": "bimodal: 0.0 or 0.85-1.00 (MySQL: no D5.2); P 0.5",
}


def sg_labels(rows: list[Probe]) -> list[Probe]:
    for pr in rows:
        key = pr.id.removeprefix("SG-")
        if key in SG_PRED:
            pr.prediction = SG_PRED[key]
    return rows


# ---- the gated stages: preflight, the ledger, one scored run --------------------------------------------


def amendment5(text: str, label: str) -> str:
    m = re.search(rf"^## Amendment {re.escape(label)}\b.*?(?=^## Amendment |\Z)", text, re.M | re.S)
    return m.group(0) if m else ""


def recorded5(text: str, arm: str) -> dict[str, dict[str, str]]:
    labels = ("2", "3", "4") if arm == "H5" else ("2", "3-SG", "4")
    return {f"a{n[0]}": sc5.recorded_hashes5(amendment5(text, n)) for n in labels}


def preflight5(
    arm: str,
    app: Path,
    freeze5: Path,
    prereg: Path,
    repo: Path,
    runner_file: Path,
    *,
    enforce: bool,
    check_truth: bool,
) -> dict[str, Any]:
    """The runner's own sha256 (never special-cased), the scorer's, ``grpc_truth5.py``'s, the contract's,
    ``freeze5.sha256`` and the app's layout and freeze, against the amendments. Any problem refuses the
    run before any metric (``refused_preflight``: no run consumed). A rehearsal reports the hash
    comparisons and refuses only on layout."""
    out: dict[str, Any] = {"problems": [], "hash_notes": []}
    layout = t5.check_layout(app)
    out["layout"] = layout
    rec = recorded5(prereg.read_text(), arm) if prereg.is_file() else {"a2": {}, "a3": {}, "a4": {}}
    own = sha(runner_file.read_bytes())
    files = {
        "scorer": repo / "dogfood/grpc_scorer5.py",
        "truth5": repo / "dogfood/grpc_truth5.py",
        "contract": repo / "dogfood/grpc_contract5.py",
    }
    hashes = {k: (sha(f.read_bytes()) if f.is_file() else "") for k, f in files.items()}
    out.update(
        runner_sha256=own,
        scorer_sha256=hashes["scorer"],
        truth5_sha256=hashes["truth5"],
        contract_sha256=hashes["contract"],
    )
    notes: list[str] = []
    runners = {h for a in ("a2", "a4") if (h := rec[a].get("runner"))}
    scorers = {h for a in ("a2", "a4") if (h := rec[a].get("scorer"))}
    if own not in runners:
        notes.append("runner_hash_not_recorded")
    if hashes["scorer"] not in scorers:
        notes.append("scorer_hash_not_recorded")
    if hashes["truth5"] != rec["a2"].get("truth5"):
        notes.append("truth5_hash_not_recorded")
    if hashes["contract"] != rec["a2"].get("contract"):
        notes.append("contract_hash_not_recorded")
    out["freeze5_sha256"] = sha(freeze5.read_bytes()) if freeze5.is_file() else ""
    if out["freeze5_sha256"] != rec["a2"].get("freeze5"):
        notes.append("freeze5_hash_not_recorded")
    if freeze5.is_file():
        listed = {
            ln.partition("  ")[2].strip(): ln.partition("  ")[0]
            for ln in freeze5.read_text().splitlines()
            if ln.strip()
        }
        rel = (
            str(runner_file.resolve().relative_to(repo.resolve()))
            if runner_file.resolve().is_relative_to(repo.resolve())
            else RUNNER
        )
        if listed.get(rel) != own and own != rec["a4"].get("runner"):
            notes.append("own_entry_missing_or_differs_in_freeze5")
        exempt = {f"changed {rel}"} if own == rec["a4"].get("runner") else set()
        notes += [f"freeze5: {x}" for x in r1._check_sha_file(freeze5, repo) if x not in exempt]
    else:
        notes.append("freeze5_missing")
    out["target_freeze_sha256"] = t5.app_freeze_sha256(app)
    if out["target_freeze_sha256"] != rec["a3"].get("target"):
        notes.append("target_freeze_hash_not_recorded")
    staged = (
        sorted(git("diff", "--name-only", "--cached", cwd=repo).split())
        if (repo / ".git").exists()
        else []
    )
    out["staged"] = staged
    if staged:
        notes.append("something is staged")
    schema = _truth_problems(app) if check_truth else []
    out["truth"] = schema
    out["recorded"] = rec
    out["hash_notes"] = notes
    out["problems"] = [*layout, *notes, *schema] if enforce else [*layout, *schema]
    out["ok"] = not out["problems"]
    return out


def _truth_problems(app: Path) -> list[str]:
    try:
        raw = json.loads((app / "truth.json").read_text())
    except (OSError, ValueError) as err:
        return [f"truth: {type(err).__name__}"]
    errs = t5.schema_errors(raw)
    if errs:
        return [f"truth schema: {errs[0]}"]
    try:
        t5.normalise_codes_in(raw)
    except t5.TruthError as err:
        return [f"truth codes: {err}"]
    return []


def gated_arm(arm: str, args: argparse.Namespace) -> int:
    """``--arm H5`` / ``--arm SG``: appends to the global ledger BEFORE anything else, runs ONCE per
    ``freeze.sha256`` (one re-run, only after a crash before any metric), leaves every label to
    ``grpc_scorer5.provenance5``."""
    app_arg = Path(args.app)
    runner_file = Path(__file__).resolve()
    repo = Path(args.repo_root).resolve()
    rehearse = bool(args.rehearse or args.process_test)
    out = Path(args.out)
    real = t5.real_ledger_path(runner_file)
    if args.ledger and not rehearse:
        print(
            "REFUSED: --ledger is only for --process-test / --rehearse; the real ledger is fixed in the runner's bytes"
        )
        return 2
    if rehearse:
        lpath = Path(args.ledger) if args.ledger else out / "ledger-scratch.jsonl"
        if lpath.resolve() == real.resolve():
            print("REFUSED: --process-test / --rehearse never use the real ledger")
            return 2
    else:
        lpath = real
    led = t5.Ledger(lpath)
    problems = led.verify()
    if problems:
        print(f"REFUSED: the ledger chain does not verify ({problems[0]}); nothing was scored")
        return 4
    pf = preflight5(
        arm,
        app_arg,
        Path(args.freeze5),
        Path(args.prereg),
        repo,
        runner_file,
        enforce=not args.rehearse,
        check_truth=not rehearse,
    )
    print("preflight:", json.dumps({k: pf[k] for k in ("ok", "problems", "hash_notes")}))
    base = {
        "arm": arm,
        "runner_sha256": pf["runner_sha256"],
        "scorer_sha256": pf["scorer_sha256"],
        "truth5_sha256": pf["truth5_sha256"],
        "target_freeze_sha256": pf["target_freeze_sha256"],
        "out": str(out),
    }
    if not pf["ok"]:
        led.append(status="refused_preflight", **base)
        print(
            "REFUSED: a layout, hash or freeze problem; nothing was scored and no run was consumed"
        )
        return 2
    index, prior, why = led.may_start(arm, pf["target_freeze_sha256"])
    if why:
        led.append(status="refused_preflight", **base)
        print(f"REFUSED: {why}")
        return 4
    started = led.append(status="started", **base)
    if args.process_test == "crash":
        os._exit(
            137
        )  # a forced crash after the ledger line and before any metric (--process-test only)
    scratch = Path(args.scratch or tempfile.mkdtemp(prefix="grpc5-probes-"))
    scratch.mkdir(parents=True, exist_ok=True)
    root = app_arg.resolve()
    ctx: dict[str, Any] = {}
    probes: list[Probe] = []
    try:
        if args.process_test:
            probes.append(Probe("PROC-STUB", "", "").done(True, {"run_index": index}))
        elif arm == "H5":
            probes, ctx = score_h5(root, scratch, rehearse)
        else:
            probes, ctx = score_sg(root, scratch, rehearse)
            probes = sg_labels(probes)
    except Exception as err:  # noqa: BLE001 -- a runner failure is never a score
        led.append(status="crashed_before_metric", **base)
        print(f"RUNNER ERROR (no metric file written): {type(err).__name__}: {str(err)[:800]}")
        return 3
    lines = [x.decode() for x in led.raw_lines()]
    view = sc5.ledger_view(lines, arm, pf["target_freeze_sha256"], started["seq"])
    prov = (
        {"label": "rehearsal", "reasons": ["rehearsal"], "pass": False, "reason": "rehearsal"}
        if args.rehearse
        else sc5.provenance5(
            {
                "runner": pf["recorded"]["a2"].get("runner", ""),
                "scorer": pf["recorded"]["a2"].get("scorer", ""),
                "truth5": pf["recorded"]["a2"].get("truth5", ""),
                "target": pf["recorded"]["a3"].get("target", ""),
            },
            {
                "runner": pf["runner_sha256"],
                "scorer": pf["scorer_sha256"],
                "truth5": pf["truth5_sha256"],
                "target": pf["target_freeze_sha256"],
            },
            view,
        )
    )
    rows = [{**pr.row(), "label": prov["label"], "label_reasons": prov["reasons"]} for pr in probes]
    doc = {
        "schema": "lattice.grpc-probes5-gated/1",
        "arm": arm,
        "rehearsal": rehearse,
        "preflight": {k: v for k, v in pf.items() if k != "recorded"},
        "ledger": {
            "path": str(lpath),
            "started_seq": started["seq"],
            "run_index": view.get("run_index"),
            "prior": view.get("prior"),
            "tail_sha256_before_close": led.tail_sha256(),
        },
        "provenance": prov,
        "tables_source": ctx.get("tables_source"),
        "codes_normalised": ctx.get("codes_normalised"),
        "probes": rows,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / f"probes5-arm{arm.lower()}.json").write_text(
        json.dumps(doc, indent=1, default=str) + "\n"
    )
    for key, fname in (
        ("rec", f"reconcile-{arm.lower()}.json"),
        ("flows", f"flows-{arm.lower()}.json"),
    ):
        if ctx.get(key) is not None:
            (out / fname).write_text(json.dumps(ctx[key], indent=1, default=str) + "\n")
    led.append(status="scored", **base)
    for pr, row in zip(probes, rows, strict=True):
        print(
            f"{pr.status:13} {pr.id:16} [{row['label']}] {json.dumps(pr.observed, default=str)[:200]}"
        )
    failed = [pr for pr in probes if pr.status in ("FAIL", "CRASH")]
    print(f"{sum(pr.status == 'PASS' for pr in probes)} passed, {len(failed)} failed")
    return 1 if failed else 0


# ---- Arm 0, the rest ------------------------------------------------------------------------------------

REH_ROWS = {
    "H5": (
        "HO5-RPC",
        "HO5-SAFE",
        "HO5-CODES",
        "HO5-AUTH",
        "HO5-TABLES",
        "HO5-REC",
        "HO5-FLOWS",
        "HO5-SQLT",
        "HO5-DET",
        "HO5-NW",
    ),
    "SG": (
        "SG-RPC",
        "SG-SAFE",
        "SG-CODES",
        "SG-AUTH",
        "SG-TABLES",
        "SG-REC",
        "SG-FLOWS",
        "SG-CD",
        "SG-SQLT",
        "SG-DET",
        "SG-NW",
    ),
}
ROW_STATUSES = ("PASS", "FAIL", "REPORTED", "NOT RUN", "NOT SUPPORTED")


def p5_reh(ctx: dict[str, Any]) -> Probe:
    """H7: the gated arms run end to end (scratch ledger, no recorded-hash check) on toolshed as delivered
    (truth /4, CamelCase codes, 0-based links, ``build_db`` returning None, freeze inside the app), on the
    truth-/5 fixture app and, with Docker, on the MySQL fixture: every row valued or a named
    NOT SUPPORTED, no crash."""
    p = Probe("P5-REH", "every row valued or NOT SUPPORTED, 0 crashes", "0 crashes")
    scratch = Path(ctx["scratch"])
    targets: list[tuple[str, str, Path]] = []
    tool = corpora_paths(ctx.get("corpora"))["toolshed"]
    if tool and not ctx.get("self_test"):
        targets.append(("toolshed as delivered (dev row, post hoc)", "H5", Path(tool)))
    sqlite = FIXTURES / "harness5" / "sqlite"
    if sqlite.is_dir():
        targets.append(("truth-/5 fixture app harness5/sqlite", "H5", sqlite))
    mysql = FIXTURES / "harness5" / "mysql"
    mysql_note = None
    if mysql.is_dir():
        mysql_note = docker_ready()
        if not mysql_note:
            targets.append(("truth-/5 fixture app harness5/mysql", "SG", mysql))
    if not targets:
        return p.not_run("no app to rehearse on (no harness5 fixture, no --corpora)")
    rows: dict[str, Any] = {}
    ok = True
    for n, (label, arm, app) in enumerate(targets):
        out = scratch / f"reh-{n}"
        shutil.rmtree(out, ignore_errors=True)
        proc = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--arm",
                arm,
                "--rehearse",
                "--app",
                str(app),
                "--out",
                str(out),
                "--scratch",
                str(scratch / f"reh-scratch-{n}"),
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=REPO,
        )
        f = out / f"probes5-arm{arm.lower()}.json"
        if proc.returncode in (3, 4) or not f.is_file():
            rows[label] = {"exit": proc.returncode, "tail": proc.stdout[-400:] + proc.stderr[-300:]}
            ok = False
            continue
        got = {r["id"]: r["status"] for r in json.loads(f.read_text())["probes"]}
        missing = [i for i in REH_ROWS[arm] if i not in got]
        crashed = sorted(i for i, st in got.items() if st == "CRASH")
        odd = sorted(i for i, st in got.items() if st not in ROW_STATUSES + ("CRASH",))
        rows[label] = {"exit": proc.returncode, "rows": got, "missing": missing, "crashed": crashed}
        ok = ok and not missing and not crashed and not odd
    if mysql_note:
        rows["harness5/mysql"] = f"SG rehearsal NOT RUN: {mysql_note}"
    p = p.done(ok, rows)
    if ok and mysql_note:
        p.status = "NOT RUN"  # the SG half could not run: not a pass
    return p


def p5_arms(ctx: dict[str, Any] | None = None) -> Probe:
    """Every arm (0, G, M, L, H5, SG) is implemented: present in the dispatch table, and no arm function
    is only a literal ``not_run``."""
    p = Probe("P5-ARMS", "true", "true")
    src = Path(__file__).read_text()
    tree = ast.parse(src)
    fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    bad: list[str] = []
    for arm, name in ARM_FUNCS.items():
        fn = fns.get(name)
        if fn is None:
            bad.append(f"arm {arm}: {name} missing")
            continue
        calls = [
            c
            for c in ast.walk(fn)
            if isinstance(c, ast.Call)
            and isinstance(c.func, ast.Attribute)
            and c.func.attr == "not_run"
        ]
        literal_only = bool(calls) and all(
            c.args and isinstance(c.args[0], ast.Constant) for c in calls
        )
        body_lines = (
            len(ast.get_source_segment(src, fn).splitlines())
            if ast.get_source_segment(src, fn)
            else 0
        )
        if literal_only and body_lines < 12:
            bad.append(f"arm {arm}: {name} is a stub")
    return p.done(not bad, {"arms": sorted(ARM_FUNCS), "stubs": bad}, bad)


def p5_det(ctx: dict[str, Any]) -> Probe:
    """extract, model, reconcile, flows over 10 processes (random hash seeds), two roots: round 4's set
    (N, the round-4 families, deskd, kilnworks, harborline), the round-5 families and toolshed."""
    p = Probe("P5-DET", "identical (p)", "10/10")
    scratch = Path(ctx["scratch"])
    c = corpora_paths(ctx.get("corpora"))
    dev = {"deskd": c["deskd"], "kiln": c["kiln"], "harbor": c["harbor"], "scratch": scratch}
    first = p4.p4_det(dev)
    rows: dict[str, Any] = {"round-4 set": first.observed}
    ok = first.status == "PASS"
    for fam in ("descwrap5", "builderopt5", "status5", "audience5", "sqlraw5"):
        if not (FIXTURES / fam).is_dir():
            rows[fam] = "no fixture family yet (Stage B)"
            continue
        res = p3._det_runs(
            fam,
            lambda r, fam=fam: [
                sys.executable,
                str(HERE / "grpc_probes4.py"),
                "emit-det4",
                fam,
                str(r),
                str(scratch),
            ],
            [FIXTURES / fam, p3._copy_root(FIXTURES / fam, scratch, fam)],
            scratch,
        )
        rows[fam] = {k: v for k, v in res.items() if k != "errors"}
        ok = ok and res["ok"]
    if c["toolshed"] and not ctx.get("self_test"):
        pr = det_row5(Path(c["toolshed"]), scratch, "P5-TOOL")
        rows["toolshed"] = pr.observed
        ok = ok and pr.status == "PASS"
    else:
        rows["toolshed"] = "NOT RUN: no --corpora toolshed"
    rows["roots"] = 2
    return p.done(ok, rows)


def cmd_nw_work5(kind: str, corpora: str, scratch: str) -> None:
    sp = Path(scratch)
    if kind == "fx5":
        from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd

        fe = GoGrpcFrontEnd()
        for fam in ("descwrap5", "builderopt5", "status5", "audience5", "sqlraw5"):
            if (FIXTURES / fam).is_dir():
                for c in sorted((FIXTURES / fam).iterdir()):
                    if c.is_dir():
                        fe.extract_json(FIXTURES / fam, c.name)
        return
    app = load_app5(
        Path(corpora) / kind, sp, truth_path=Path(corpora) / kind / "truth.json", strict=False
    )
    for _ in range(3):
        q2.reconcile_of(app)
    for _ in range(3):
        q2.flows_of(app)


def p5_nw(ctx: dict[str, Any]) -> Probe:
    """No write under any corpus, fixture, Streak or the main checkout, and no ``claude`` call: round 4's
    set, plus toolshed and the round-5 families."""
    p = Probe("P5-NW", "0, 0, 0", "all 0")
    scratch = Path(ctx["scratch"])
    c = corpora_paths(ctx.get("corpora"))
    first = p4.p4_nw(
        {"deskd": c["deskd"], "kiln": c["kiln"], "harbor": c["harbor"], "scratch": scratch}
    )
    rows: dict[str, Any] = {"round-4 set": first.observed}
    ok = first.status == "PASS"
    shim = scratch / "shim"
    log = scratch / "claude-calls.log"
    roots: dict[str, Path] = {"fixtures": FIXTURES, "streak": Path.home() / "Documents" / "streak"}
    kinds = ["fx5"]
    if c["toolshed"] and not ctx.get("self_test"):
        roots["toolshed"] = Path(c["toolshed"])
        kinds.append("toolshed")
    before = {k: q2._snapshot(r) for k, r in roots.items() if r.exists()}
    env = {
        **os.environ,
        "PATH": f"{shim}:{os.environ['PATH']}",
        "PYTHONPATH": str(REPO),
        "LATTICE_HELDOUT_BUILD": str(scratch / "hbuild"),
    }
    for kind in kinds:
        corpora = str(Path(c["toolshed"]).parent) if kind == "toolshed" and c["toolshed"] else ""
        subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "nw-work5",
                kind,
                corpora,
                str(scratch),
            ],
            check=True,
            env=env,
            cwd=REPO,
            capture_output=True,
        )
    after = {k: q2._snapshot(roots[k]) for k in before}
    changed = [f"{k}:{x}" for k in before for x in before[k] if before[k].get(x) != after[k].get(x)]
    new = [f"{k}:{x}" for k in before for x in after[k] if x not in before[k]]
    calls = len([ln for ln in log.read_text().splitlines() if ln.strip()]) if log.is_file() else 0
    rows.update(
        {"roots": sorted(before), "changed": len(changed), "new": len(new), "claude calls": calls}
    )
    return p.done(ok and not changed and not new and calls == 0, rows, changed + new)


UNIT_FILES = [
    f"tests/grpc/test_{n}5.py"
    for n in ("split", "descwrap", "builderopt", "status", "audience", "sqlraw", "truth", "ledger")
] + ["tests/studio/test_rebuild_grpc5.py", "tests/mcp/test_guard_dirs5.py"]


def p5_unit(ctx: dict[str, Any]) -> Probe:
    p = Probe("P5-UNIT", "60-140 tests (rounds 3-4 overshot their ranges)", "all green, >= 50")
    have = [f for f in UNIT_FILES if (REPO / f).is_file()]
    if not have:
        return p.not_run("none of the P5-UNIT test files exists")
    r = r1.run_pytest(have)
    n = r["counts"].get("passed", 0)
    return p.done(
        r["returncode"] == 0 and n >= 50 and not r["failing"],
        {
            **r["counts"],
            "seconds": r["seconds"],
            "files": have,
            "missing files": sorted(set(UNIT_FILES) - set(have)),
            "in 60-140 (p)": 60 <= n <= 140,
        },
        r["failing"],
    )


def p5_score(ctx: dict[str, Any] | None = None) -> Probe:
    p = Probe("P5-SCORE", "78-110 cases (p)", "100%, >= 78")
    path = HERE / "grpc_scorer5_cases.json"
    n = len(json.loads(path.read_text())["cases"])
    bad = sc5.run_cases(path)
    return p.done(
        not bad and n >= 78,
        {"cases": n, "agree": n - len(bad), "in 78-110 (p)": 78 <= n <= 110},
        bad,
    )


def p5_budget(ctx: dict[str, Any]) -> Probe:
    """Section 3: the re-registered budgets (a miss is a FAIL, recorded with its hunks) and the
    1200-line structural cap."""
    p = Probe("P5-BUDGET", "all within (P about 0.55)", "all")
    fc = ctx.get("frozen") or frozen_check(ctx)
    bad = {k: v for k, v in fc["budgets"].items() if not v["ok"]}
    hunks = {k: git("diff", "--stat", BASE, "--", k).strip() for k in bad if k in BUDGETS}
    return p.done(
        not bad,
        {
            "budgets": fc["budgets"],
            "go budgets measured against": "S0" if ctx.get("s0") else "BASE (no --s0)",
        },
        hunks,
    )


def p5_frozen(ctx: dict[str, Any]) -> Probe:
    p = Probe(
        "P5-FROZEN",
        "empty diff of every frozen file; only 'may change' and 'creates' touched",
        "exact",
    )
    fc = frozen_check(ctx)
    ctx["frozen"] = fc
    return p.done(
        fc["ok"] or not (fc["frozen_files_changed"] or fc["unexpected_untracked"] or fc["staged"]),
        {
            k: fc[k]
            for k in (
                "frozen_files_changed",
                "unexpected_untracked",
                "staged",
                "dev_truth_hashes_changed",
            )
        },
        fc["named_frozen"],
    )


def p5_reg(ctx: dict[str, Any]) -> Probe:
    """The same failing ids before (``git archive 978b468``) and after (this worktree) across the six
    test directories; from two pytest logs (``--reg-before`` / ``--reg-after``)."""
    pr = q2.p2_reg(ctx)
    pr.id = "REG"
    if pr.status == "NOT RUN":
        pr.observed = "--reg-before / --reg-after (pytest logs of tests/spec tests/importer tests/gocheck tests/mcp tests/studio tests/grpc on the base archive and on the worktree) were not given"
    return pr


def p5_proc(ctx: dict[str, Any] | None = None) -> Probe:
    """The held-out process on a scratch copy of the runner with a stand-in app (``--process-test``):
    round 4's five cases on the global ledger, a broken chain and a symlinked app."""
    p = Probe("P5-PROC", "7/7", "7/7")
    root = Path(tempfile.mkdtemp(prefix="p5-proc-"))
    files = [
        f
        for f in os.listdir(HERE)
        if f.startswith("grpc_") and (f.endswith(".py") or f.endswith(".json"))
    ]
    results: dict[str, Any] = {}
    ledger_rel = Path(t5.LEDGER_REL)

    def setup(name: str) -> dict[str, Path]:
        d = root / name
        shutil.rmtree(d, ignore_errors=True)
        (d / "dogfood").mkdir(parents=True)
        (d / ledger_rel.parent).mkdir(parents=True)
        for f in files:
            shutil.copy(HERE / f, d / "dogfood" / f)
        app = d / "ho"
        app.mkdir()
        for fname, text in (
            ("harness.py", "# stand-in: not an app\n"),
            ("studio.toml", "[app]\n"),
            ("truth.json", "{}\n"),
        ):
            (app / fname).write_text(text)
        (app / "freeze.sha256").write_text(
            "".join(
                f"{sha((app / x).read_bytes())}  {x}\n"
                for x in ("harness.py", "studio.toml", "truth.json")
            )
        )
        return {
            "d": d,
            "app": app,
            "fz": d / "freeze5.sha256",
            "pre": d / "prereg.md",
            "out": d / "out",
        }

    def record(w: dict[str, Path], *, runner_a4: bool = False, drop_own: bool = False) -> None:
        d = w["d"]
        names = (
            "dogfood/grpc_probes5.py",
            "dogfood/grpc_scorer5.py",
            "dogfood/grpc_scorer5_cases.json",
            "dogfood/grpc_truth5.py",
            "dogfood/grpc_contract5.py",
        )
        w["fz"].write_text(
            "".join(
                f"{sha((d / f).read_bytes())}  {f}\n"
                for f in names
                if not (drop_own and f == RUNNER)
            )
        )
        text = (
            "# prereg stand-in\n\n## Amendment 2\n"
            f"- dogfood/grpc_probes5.py sha256 {sha((d / RUNNER).read_bytes())}\n"
            f"- dogfood/grpc_scorer5.py sha256 {sha((d / 'dogfood/grpc_scorer5.py').read_bytes())}\n"
            f"- dogfood/grpc_truth5.py sha256 {sha((d / 'dogfood/grpc_truth5.py').read_bytes())}\n"
            f"- dogfood/grpc_contract5.py sha256 {sha((d / 'dogfood/grpc_contract5.py').read_bytes())}\n"
            f"- freeze5.sha256 sha256 {sha(w['fz'].read_bytes())}\n\n## Amendment 3\n- ho5/freeze.sha256 {sha((w['app'] / 'freeze.sha256').read_bytes())}\n"
        )
        w["pre"].write_text(text)

    def run(
        w: dict[str, Path], *extra: str, app: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(w["d"] / RUNNER),
                "--arm",
                "H5",
                "--process-test",
                "run",
                "--app",
                str(app or w["app"]),
                "--freeze5",
                str(w["fz"]),
                "--prereg",
                str(w["pre"]),
                "--repo-root",
                str(w["d"]),
                "--out",
                str(w["out"]),
                "--scratch",
                str(w["d"] / "scr"),
                *extra,
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=w["d"],
            env={**os.environ, "PYTHONPATH": str(REPO)},
        )

    def statuses(w: dict[str, Path]) -> list[str]:
        f = w["out"] / "ledger-scratch.jsonl"
        return (
            [json.loads(x)["status"] for x in f.read_text().splitlines() if x.strip()]
            if f.is_file()
            else []
        )

    def labels(w: dict[str, Path]) -> set[str]:
        return {
            r["label"] for r in json.loads((w["out"] / "probes5-armh5.json").read_text())["probes"]
        }

    try:
        w = setup("s1")  # a runner byte changed after the hashes were recorded
        record(w)
        (w["d"] / RUNNER).write_text((w["d"] / RUNNER).read_text() + "\n# one byte more\n")
        r = run(w)
        results["1 runner byte changed: refused before any metric, no run consumed"] = (
            r.returncode == 2
            and statuses(w) == ["refused_preflight"]
            and not (w["out"] / "probes5-armh5.json").exists()
        )
        w = setup("s2")  # the runner's own entry removed from its check
        record(w, drop_own=True)
        r = run(w)
        results["2 own entry removed from freeze5: refused"] = (
            r.returncode == 2 and "own_entry" in r.stdout and statuses(w) == ["refused_preflight"]
        )
        w = setup("s3")  # a forced crash, then a re-run with equal bytes
        record(w)
        r1_ = subprocess.run(
            [
                sys.executable,
                str(w["d"] / RUNNER),
                "--arm",
                "H5",
                "--process-test",
                "crash",
                "--app",
                str(w["app"]),
                "--freeze5",
                str(w["fz"]),
                "--prereg",
                str(w["pre"]),
                "--repo-root",
                str(w["d"]),
                "--out",
                str(w["out"]),
                "--scratch",
                str(w["d"] / "scr"),
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=w["d"],
            env={**os.environ, "PYTHONPATH": str(REPO)},
        )
        r2_ = run(w)
        results["3 crash then re-run with equal bytes: blind"] = (
            r1_.returncode == 137
            and r2_.returncode == 0
            and labels(w) == {"blind"}
            and statuses(w) == ["started", "started", "scored"]
        )
        w = setup("s4")  # a crash, then a re-run with changed bytes (recorded in Amendment 4)
        record(w)
        subprocess.run(
            [
                sys.executable,
                str(w["d"] / RUNNER),
                "--arm",
                "H5",
                "--process-test",
                "crash",
                "--app",
                str(w["app"]),
                "--freeze5",
                str(w["fz"]),
                "--prereg",
                str(w["pre"]),
                "--repo-root",
                str(w["d"]),
                "--out",
                str(w["out"]),
                "--scratch",
                str(w["d"] / "scr"),
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=w["d"],
            env={**os.environ, "PYTHONPATH": str(REPO)},
        )
        (w["d"] / RUNNER).write_text((w["d"] / RUNNER).read_text() + "\n# a fix for the crash\n")
        w["pre"].write_text(
            w["pre"].read_text()
            + f"\n## Amendment 4\n- dogfood/grpc_probes5.py sha256 {sha((w['d'] / RUNNER).read_bytes())}\n"
        )
        r = run(w)
        results["4 re-run with changed bytes: post_hoc"] = r.returncode == 0 and labels(w) == {
            "post_hoc"
        }
        r5 = run(w)  # the crashed run and its re-run are the two permitted: a third is refused
        results["5 a third run: refused"] = r5.returncode == 4 and "third run" in r5.stdout
        w = setup("s6")  # a tampered ledger line: the chain does not verify
        record(w)
        run(w)
        led = w["out"] / "ledger-scratch.jsonl"
        led.write_text(led.read_text().replace('"status":"started"', '"status":"scored"', 1))
        r = run(w)
        results["6 a broken chain: refused, nothing appended"] = (
            r.returncode == 4 and "chain does not verify" in r.stdout
        )
        w = setup("s7")  # a symlinked app is refused in preflight and consumes no run
        record(w)
        link = w["d"] / "ho-link"
        link.symlink_to(w["app"])
        r = run(w, app=link)
        results["7 a symlinked app: refused_preflight, no run consumed"] = (
            r.returncode == 2 and statuses(w) == ["refused_preflight"] and "symlink" in r.stdout
        )
    except Exception as err:  # noqa: BLE001
        results["error"] = f"{type(err).__name__}: {err}"
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return p.done(all(v is True for v in results.values()) and len(results) == 7, results)


def arm0(ctx: dict[str, Any]) -> list[Probe]:
    st = bool(ctx.get("self_test"))
    probes = [
        p5_split(ctx),
        p5_bc(ctx),
        p5_hapi(ctx),
        p5_code(ctx),
        p5_links(ctx),
        p5_ledger(ctx),
        fixture_probe("P5-DESC", "descwrap5", "each state/code as truth", "exact; 0 S8", ctx),
        fixture_probe("P5-BOPT", "builderopt5", "exact states", "exact; 0 S8", ctx),
        fixture_probe(
            "P5-STATUS", "status5", "exact codes, untraced unread, 0 UNKNOWN extras", "exact", ctx
        ),
        fixture_probe(
            "P5-ICODE",
            "status5",
            "literal gate code on gated rpcs only; unclear gate adds none",
            "exact",
            ctx,
            case_filter="icode",
        ),
        fixture_probe(
            "P5-AUD",
            "audience5",
            "exact; 0 Member behind an unenumerated bypass",
            "exact; 0 S8",
            ctx,
        ),
        fixture_probe("P5-SQL", "sqlraw5", "exact tables/writes", "exact", ctx),
        p5_prot(ctx),
        p5_gen(ctx),
        p5_brief(ctx),
        p5_reh(ctx),
        p5_arms(ctx),
    ]
    probes += [
        p5_det(ctx),
        p5_nw(ctx),
        p5_unit(ctx),
        p5_score(ctx),
        p5_proc(ctx),
        p5_frozen(ctx),
        p5_budget(ctx),
        p5_reg(ctx),
    ]
    if st:
        for pr in probes:
            if pr.status != "NOT RUN":
                pr.id += " (self-test)"
    return probes


# ---- Arm G (development; tuned-on rows are reported only) ------------------------------------------------

PD5_PRED = {
    "KILN": "RPC 1.0/1.0; CODES recall 0.80-0.95 (E3d adds interceptor codes), precision 0.90-1.00; AUTH 0 wrong, unclear 0-0.2; TABLES 1.0; REC 1.0; FLOWS 17/7, 11/33, 6/9",
    "HARBOR": "RPC 1.0/1.0; SAFE 0; CODES 0.45-0.80 / 0.90-1.00; AUTH 0 wrong, unclear 0-0.1; TABLES 1.0; REC 1.0/1.0; FLOWS recovered 1-40, verified 0-8, edge 0.00-0.50, coverage 0-5 of 8; SQLT recall 0.60-1.00",
    "TOOL": "RPC 0.91-1.00 / 0.91-1.00; SAFE 0; CODES 0.50-0.90 / 0.85-1.00; AUTH 0 wrong, unclear 0.00-0.11; TABLES 1.0 (harness_db); REC 1.00 / 1.00; FLOWS truth replaying 8-10 of 10, recovered 3-60, verified 1-15, edge 0.10-0.60, coverage 1-6 of 10; SQLT 0.60-1.00 / 0.80-1.00",
}


def tables_by_rpc_of(truth_json: Path) -> dict[str, list[str]]:
    """Per-rpc tables from a truth file whose ``tables`` entries carry ``read_by`` / ``written_by``."""
    try:
        raw = json.loads(truth_json.read_text())
    except (OSError, ValueError):
        return {}
    out: dict[str, set[str]] = {}
    for t in raw.get("tables", []) if isinstance(raw.get("tables"), list) else []:
        if isinstance(t, dict):
            for k in (*t.get("read_by", []), *t.get("written_by", [])):
                out.setdefault(r1._full(k), set()).add(str(t.get("name", "")).lower())
    return {k: sorted(v) for k, v in out.items()}


def dev_rows5(app: q2.App, name: str, ctx: dict[str, Any], truth_json: Path) -> list[Row]:
    """PD5-<name>: a development app scored by the held-out scorers, post hoc, tuned-on: reported, never
    gated, FLOWS with H3's replay (links applied)."""
    label = f"PD5-{name}"
    kctx: dict[str, Any] = {}
    app.truth.setdefault("tables_by_rpc", tables_by_rpc_of(truth_json))
    probes = [
        *relabel(p4.rpc_rows4(app, "HO4"), "HO4", label),
        *rec_row5_as(app, label, kctx),
        flows_row5(app, label, kctx),
    ]
    if app.truth["tables_by_rpc"]:
        probes.append(sqlt_row5(app, label))
    rows = []
    for pr in probes:
        key = pr.id.removeprefix(f"{label}-")
        r = Row(
            f"{label}/{key} (post hoc, tuned-on, never gated)",
            PD5_PRED.get(name, ""),
            "reported only",
        )
        obs = pr.observed if isinstance(pr.observed, dict) else {"observed": pr.observed}
        rows.append(r.reported(pr.status in ("PASS", "REPORTED"), obs, pr.detail))
    ctx[f"rec_{name}"], ctx[f"flows_{name}"] = kctx.get("rec"), kctx.get("flows")
    return rows


def rec_row5_as(app: q2.App, label: str, ctx: dict[str, Any]) -> list[Probe]:
    return [rec_row5(app, label, ctx)]


def pr4_all(ctx: dict[str, Any]) -> Probe:
    """Round 4's runner, unchanged, run un-nested at round-5 code (``--pr4all-from``: its arm 0 / G
    outputs); every row it passed at round 4 still passes (a declared change is listed in Amendment 1)."""
    p = Probe("PR4-ALL", "every round-4 PASS row PASS or declared", "all")
    src = ctx.get("pr4all_from")
    if not src:
        return p.not_run(
            "--pr4all-from (round 4's runner outputs for arm 0 and G, run un-nested at round-5 code) was not given"
        )
    rows: dict[str, Any] = {"source": str(src)}
    ok = True
    for arm, name in (("0", "probes4-arm0.json"), ("G", "probes4-armg.json")):
        try:
            now = json.loads((Path(src) / arm / name).read_text())
        except OSError:
            rows[arm] = {"error": f"{Path(src) / arm / name} was not written"}
            ok = False
            continue
        was = json.loads((REPO / "docs/results/data/2026-10-18-grpc-round4" / name).read_text())
        was_s = {r["id"]: r["status"] for r in was["probes"]}
        now_s = {r["id"]: r["status"] for r in now["probes"]}
        regress = sorted(i for i, s in was_s.items() if s == "PASS" and now_s.get(i) != "PASS")
        rows[arm] = {
            "round-4 PASS rows still PASS": f"{sum(s == 'PASS' for s in was_s.values()) - len(regress)}/{sum(s == 'PASS' for s in was_s.values())}",
            "regressed": regress,
        }
        ok = ok and not regress
    return p.done(ok, rows)


TIME_SNIPPET = (
    "import sys,time\nfrom pathlib import Path\n"
    "from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd\n"
    "fe=GoGrpcFrontEnd(); root,pkg=sys.argv[1],sys.argv[2]\n"
    "fe.extract_json(Path(root),pkg)\nbest=1e9\n"
    "for _ in range(3):\n    t=time.perf_counter(); fe.extract_json(Path(root),pkg); best=min(best,time.perf_counter()-t)\n"
    "print(best)\n"
)


def px5(ctx: dict[str, Any]) -> Probe:
    p = Probe("PX5", "0.05-2 s; 1-10 s; 5-40 s; <= +20 % (p)", "<= 30 s; 30 s; 180 s; <= +50 %")
    t = {k: ctx.get(k) for k in ("t_extract_tool", "t_rec_desk", "t_flows_desk")}
    if any(v is None for v in t.values()):
        return p.not_run("toolshed or deskd was not run")
    row: dict[str, Any] = {
        "extract on toolshed (s)": round(t["t_extract_tool"], 2),
        "reconcile on deskd (s)": round(t["t_rec_desk"], 2),
        "flows on deskd (s)": round(t["t_flows_desk"], 1),
    }
    ok = t["t_extract_tool"] <= 30 and t["t_rec_desk"] <= 30 and t["t_flows_desk"] <= 180
    h = corpora_paths(ctx.get("corpora"))["harbor"]
    if h and ctx.get("base"):
        scratch = Path(ctx["scratch"])
        times = {}
        for name, tree in (("base", Path(ctx["base"])), ("new", REPO)):
            proc = p3._tree_run(
                tree,
                [sys.executable, "-c", TIME_SNIPPET, str(h), "harborline"],
                scratch,
                f"gocheck-{name}",
            )
            times[name] = float(proc.stdout.splitlines()[-1]) if proc.returncode == 0 else None
        if all(times.values()):
            over = times["new"] / times["base"] - 1
            row["SQL reader overhead on harborline extract"] = (
                f"{over:+.1%} ({times['base']:.2f}s -> {times['new']:.2f}s)"
            )
            ok = ok and over <= 0.50
    return p.done(ok, row)


def _ren(pr: Probe, pid: str) -> Probe:
    pr.id = pid
    return pr


def stray5(needles: list[str]) -> list[str]:
    """Processes whose command line names one of ``needles`` (``ps``: round 3's reader used ``/proc``,
    which macOS lacks), never this runner or the mutants."""
    proc = subprocess.run(
        ["ps", "-axo", "pid=,command="], capture_output=True, text=True, check=False
    )
    me = str(os.getpid())
    out = []
    for ln in proc.stdout.splitlines():
        pid, _, cmd = ln.strip().partition(" ")
        if pid == me or "grpc_probes" in cmd or "grpc_mutants" in cmd or "pgrep" in cmd:
            continue
        if any(n in cmd for n in needles):
            out.append(cmd[:120])
    return out


def pf5_leak(ctx: dict[str, Any], desk: q2.App | None) -> Probe:
    """No process, bridge or container left after reconcile, flows and a forced startup failure; the
    containers of this node's owner label are checked too."""
    q2._stray = stray5  # type: ignore[assignment]
    pr = q2.pf2_leak(ctx, desk)
    pr.id, pr.prediction, pr.rule = "PF5-LEAK", "0", "0"
    if shutil.which("docker"):
        left = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                "label=lattice.owner=w-gr",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.split()
        if isinstance(pr.observed, dict):
            pr.observed["containers labelled lattice.owner=w-gr"] = left
        if left:
            pr.status = "FAIL"
    return pr


def arm_g(ctx: dict[str, Any]) -> list[Probe]:
    scratch = Path(ctx["scratch"])
    c = corpora_paths(ctx.get("corpora"))
    desk = q2.load_desk(Path(c["deskd"]), scratch) if c["deskd"] else None
    pctx = {**ctx, "deskd": c["deskd"]}
    probes: list[Probe] = [_ren(p3.pr3_n(pctx), "PR5-N")]
    probes.append(
        _ren(p3.pr3_desk(pctx, desk), "PR5-DESK")
        if desk
        else Probe("PR5-DESK", "", "").not_run("no deskd under --corpora")
    )
    probes.append(_ren(p3.pr3_sess(pctx), "PR5-SESS"))
    probes.append(_ren(p3.pf3_n(pctx), "PF5-N"))
    probes.append(
        _ren(p3.pf3_desk(pctx, desk), "PF5-DESK")
        if desk
        else Probe("PF5-DESK", "", "").not_run("no deskd under --corpora")
    )
    probes.append(pf5_leak(pctx, desk))
    ctx["t_rec_desk"], ctx["t_flows_desk"] = pctx.get("t_rec_desk"), pctx.get("t_flows_desk")
    probes.append(pr4_all(ctx))
    if c["kiln"]:
        kiln = p3.load_app(Path(c["kiln"]) / p3.KILN_PKG, scratch)
        probes += dev_rows5(kiln, "KILN", ctx, Path(c["kiln"]) / p3.KILN_PKG / "truth.json")
    else:
        probes.append(
            Probe("PD5-KILN", PD5_PRED["KILN"], "reported").not_run("no kilnworks under --corpora")
        )
    if c["harbor"]:
        hroot = Path(c["harbor"]) / "harborline"
        harbor = p4.load_dev_app(
            hroot, scratch
        )  # round 3's positional-token shim, the development path only
        probes += dev_rows5(harbor, "HARBOR", ctx, hroot / "truth.json")
    else:
        probes.append(
            Probe("PD5-HARBOR", PD5_PRED["HARBOR"], "reported").not_run(
                "no harborline under --corpora"
            )
        )
    if c["toolshed"]:
        tdir = Path(c["toolshed"])
        t0 = time.perf_counter()
        from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd

        GoGrpcFrontEnd().extract_json(tdir.parent, tdir.name)
        ctx["t_extract_tool"] = time.perf_counter() - t0
        tool = load_app5(tdir, scratch, truth_path=tdir / "truth.json", strict=False)
        probes += dev_rows5(tool, "TOOL", ctx, tdir / "truth.json")
    else:
        probes.append(
            Probe("PD5-TOOL", PD5_PRED["TOOL"], "reported").not_run("no toolshed under --corpora")
        )
    probes.append(px5(ctx))
    return probes


# ---- Arm M: PM-S5, Streak static byte-compat at round-5 code --------------------------------------------

PMS_DRIVER = r'''
"""PM-S driver (adapted from docs/results/data/2026-10-18-grpc-arms-m-l/pms_driver.py: the two input paths
are arguments): the byte-compat of Streak's import outputs on one lattice tree."""
import argparse, hashlib, json, os, shutil, subprocess, sys
from pathlib import Path

GO_ENV = {"GOFLAGS": "-mod=mod", "GOPROXY": "off", "GOTOOLCHAIN": "local", "GOWORK": "off"}


def sha(b):
    return hashlib.sha256(b.encode() if isinstance(b, str) else b).hexdigest()


def tree_sha(d):
    return {p.relative_to(d).as_posix(): sha(p.read_bytes()) for p in sorted(d.rglob("*")) if p.is_file()}


def main():
    ap = argparse.ArgumentParser()
    for k in ("tree", "label", "scratch", "out", "wf", "cands"):
        ap.add_argument("--" + k, required=True)
    a = ap.parse_args()
    tree, scratch, WF, CANDS = Path(a.tree), Path(a.scratch), Path(a.wf), Path(a.cands)
    work = scratch / f"pms-{a.label}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    env = dict(os.environ, **GO_ENV)
    env.update(PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1", NO_COLOR="1", TMPDIR=str(work),
               LATTICE_GOCHECK_CACHE=str(work / "gocache"), LATTICE_GRPCBRIDGE_CACHE=str(work / "bridgecache"))
    env.pop("ANTHROPIC_API_KEY", None)
    py = sys.executable
    rows = {}

    def run(args, cwd=None):
        return subprocess.run([py, *args], capture_output=True, text=True, env=env, cwd=cwd or tree, check=False)

    clones = {"S": scratch / "streak" / "S", "S4": scratch / "streak" / "S4"}
    p = run(["-c", "from lattice.gocheck import build_gocheck; print(build_gocheck())"])
    assert p.returncode == 0, p.stderr[-2000:]
    binary = p.stdout.strip().splitlines()[-1]
    for name, clone in clones.items():
        q = subprocess.run([binary, "extract"], input=json.dumps({"root": str(clone)}), capture_output=True, text=True, env=env, check=False)
        rows[f"extract:{name}"] = sha(q.stdout) if q.returncode == 0 else f"rc{q.returncode}:{q.stderr[-200:]}"
        code = (
            "import sys,json,hashlib\nfrom pathlib import Path\nfrom lattice.importer.frontends import select\n"
            "c=Path(sys.argv[1])\nfe=select(None,c.parent,c.name)\nm=fe.extract(c.parent,c.name).to_json()\n"
            "print(json.dumps({'select':fe.name,'model':hashlib.sha256(m.encode()).hexdigest()}))\n"
        )
        q = run(["-c", code, str(clone)])
        if q.returncode == 0:
            d = json.loads(q.stdout.strip().splitlines()[-1])
            rows[f"select:{name}"], rows[f"model:{name}"] = d["select"], d["model"]
        else:
            rows[f"select:{name}"] = f"error:{q.stderr[-300:]}"
        inv = work / f"inv-{name}"
        cmd = ["-m", "lattice.cli", "import", "inventory", str(clone), "--out", str(inv)]
        if name == "S":
            cmd += ["--workflows", str(WF)]
        q = run(cmd)
        if q.returncode != 0:
            rows[f"inventory:{name}"] = f"rc{q.returncode}:{q.stderr[-300:]}"
            continue
        rows[f"inventory:{name}"] = sha(json.dumps(tree_sha(inv), sort_keys=True))
        for kind, sub in (("report", "implementation"), ("appmap", "appmap")):
            cmd = ["-m", "lattice.spec", sub, "--spec", "spec:SPEC", "--trace", "spec.trace.json", "--inventory", str(inv), "--app-root", str(clone), "--json"]
            q = run(cmd, cwd=clone)
            rows[f"{kind}:{name}"] = sha(q.stdout) if q.returncode == 0 else f"rc{q.returncode}:{q.stderr[-300:]}"
    inv = work / "inv-S"
    for cand in sorted(CANDS.iterdir()):
        cmd = ["-m", "lattice.spec", "impact", "--project", str(clones["S"]), "--candidate", str(cand), "--inventory", str(inv), "--tiers", "23", "--baseline", "none", "--json"]
        q = run(cmd)
        rows[f"impact:{cand.name}"] = sha(q.stdout) if q.returncode == 0 else f"rc{q.returncode}:{q.stderr[-300:]}"
    Path(a.out).write_text(json.dumps({"tree": str(tree), "label": a.label, "rows": rows}, indent=2, sort_keys=True))


main()
'''


def streak_clones(streak: Path, revs: list[str], scratch: Path) -> dict[str, Path]:
    """``git archive`` of each Streak revision into scratch (read only on the owner's checkout); the
    clone's ``go_src`` points at the clone."""
    out = {}
    for name, rev in zip(("S", "S4"), revs, strict=False):
        d = scratch / "streak" / name
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        arch = subprocess.run(
            ["git", "-C", str(streak), "archive", rev], capture_output=True, check=False
        )
        if arch.returncode != 0:
            raise RunnerError(f"git archive {rev} of {streak} failed: {arch.stderr[-200:]!r}")
        subprocess.run(["tar", "-x", "-C", str(d)], input=arch.stdout, check=True)
        toml = d / "studio.toml"
        if toml.is_file():
            toml.write_text(
                re.sub(r'^go_src = ".*"$', f'go_src = "{d}"', toml.read_text(), flags=re.M)
            )
        out[name] = d
    return out


def arm_m(ctx: dict[str, Any]) -> list[Probe]:
    """PM-S5: the Arms M/L section 2 method at round-5 code against ``git archive 978b468``: extract,
    RepoModel, ``select()``, inventory, report, app map, impact T2+T3 on six candidates, on Streak
    a0679e9 and 1d00ede. 18 of 18 identical, ``gin``."""
    p = Probe("PM-S5", "18 of 18 identical, gin", "18/18")
    base = ctx.get("base")
    if base is None:
        return [p.not_run("no --base: git archive of 978b468 was not given")]
    streak = Path(ctx.get("streak") or Path.home() / "Documents" / "streak")
    revs = [r for r in str(ctx.get("streak_rev") or "a0679e9,1d00ede").split(",") if r]
    if not (streak / ".git").exists():
        return [p.not_run(f"{streak} is not a git checkout")]
    wf = REPO / "docs/results/data/2026-10-15-workflow-layer/workflows.json"
    cands = REPO / "docs/results/data/2026-10-16-goto-place/dev-candidates"
    if not (wf.is_file() and cands.is_dir()):
        return [
            p.not_run(
                "the workflow-layer inputs (workflows.json, dev-candidates) are not in this tree"
            )
        ]
    scratch = Path(ctx["scratch"]) / "armM"
    scratch.mkdir(parents=True, exist_ok=True)
    streak_clones(streak, revs, scratch)
    driver = scratch / "pms_driver.py"
    driver.write_text(PMS_DRIVER)
    outs = {}
    for label, tree in (("base", Path(base)), ("new", REPO)):
        out = scratch / f"pms-{label}.json"
        proc = subprocess.run(
            [
                sys.executable,
                str(driver),
                "--tree",
                str(tree),
                "--label",
                label,
                "--scratch",
                str(scratch),
                "--out",
                str(out),
                "--wf",
                str(wf),
                "--cands",
                str(cands),
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=tree,
            env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
            | {"PYTHONPATH": str(tree)},
        )
        if proc.returncode != 0:
            return [p.done(False, {"error": f"{label} tree: {proc.stderr[-600:]}"})]
        outs[label] = json.loads(out.read_text())["rows"]
    keys = sorted(set(outs["base"]) | set(outs["new"]))
    diff = [k for k in keys if outs["base"].get(k) != outs["new"].get(k)]
    gin = all(outs["new"].get(f"select:{n}") == "gin" for n in ("S", "S4"))
    rows = {"rows": len(keys), "identical": len(keys) - len(diff), "select() = gin": gin}
    ctx["pms"] = outs
    return [p.done(not diff and gin and len(keys) == 18, rows, diff)]


# ---- Arm L: the build round on the toy proto, model use only through the claude CLI -------------------------

ARML_SHIM = r'''#!/usr/bin/env python3
"""claude shim for Arm L: adds --max-turns 40 and --max-budget-usd <remaining of the cap> to every agent
step, tees the stream-json to a log, and keeps a ledger of the CLI's list-estimate cost.
Env: ARML_DIR (ledger.json, logs/), ARML_CAP, ARML_REAL (the real claude binary)."""
import json, os, subprocess, sys, time
from pathlib import Path

real, d, cap = os.environ["ARML_REAL"], Path(os.environ["ARML_DIR"]), float(os.environ.get("ARML_CAP", "5.00"))
argv = sys.argv[1:]
if "-p" not in argv:
    os.execv(real, [real, *argv])
d.mkdir(parents=True, exist_ok=True)
(d / "logs").mkdir(exist_ok=True)
led_p = d / "ledger.json"
led = json.loads(led_p.read_text()) if led_p.exists() else {"calls": [], "spent_usd": 0.0}
remaining = round(cap - led["spent_usd"], 4)
n = len(led["calls"]) + 1
if remaining <= 0.05:
    led["calls"].append({"n": n, "refused": "cap reached", "at": time.time()})
    led_p.write_text(json.dumps(led, indent=1))
    print(json.dumps({"type": "result", "subtype": "error_cap", "is_error": True, "result": "Arm L cap reached"}))
    sys.exit(3)
extra = ["--max-turns", "40", "--max-budget-usd", f"{remaining:.2f}"]
i = len(argv) - 1 - argv[::-1].index("--") if "--" in argv else len(argv)
cmd = [real, *argv[:i], *extra, *argv[i:]]
log = d / "logs" / f"agent-{n:02d}.jsonl"
cost, turns, result = 0.0, None, None
p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=os.getcwd(), env=os.environ)
with log.open("w") as f:
    for line in p.stdout:
        f.write(line); f.flush(); sys.stdout.write(line); sys.stdout.flush()
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "result":
            cost = float(ev.get("total_cost_usd") or 0.0)
            turns = ev.get("num_turns")
            result = {k: ev.get(k) for k in ("subtype", "is_error", "num_turns", "total_cost_usd", "duration_ms", "stop_reason")}
err = p.stderr.read()
rc = p.wait()
sys.stderr.write(err)
led = json.loads(led_p.read_text()) if led_p.exists() else led
led["calls"].append({"n": n, "rc": rc, "cost_usd": cost, "turns": turns, "result": result, "cwd": os.getcwd(), "resume": "--resume" in argv, "budget_flag": remaining, "stderr_tail": err[-300:]})
led["spent_usd"] = round(sum(c.get("cost_usd") or 0.0 for c in led["calls"]), 4)
led_p.write_text(json.dumps(led, indent=1))
sys.exit(rc)
'''


def toy_tools(scratch: Path) -> str | None:
    """The generator tools of the toy fixture (``protoc-gen-go`` v1.36.5, ``protoc-gen-go-grpc`` v1.5.1):
    ``$TOY_TOOLS_BIN`` when set, else built into scratch with ``go install``."""
    if os.environ.get("TOY_TOOLS_BIN"):
        return os.environ["TOY_TOOLS_BIN"]
    bindir = scratch / "tools" / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GOBIN": str(bindir), "GOFLAGS": "-mod=mod", "GOTOOLCHAIN": "local"}
    for pkg in (
        "google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.5",
        "google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.5.1",
    ):
        name = pkg.split("/")[-1].split("@")[0]
        if not (bindir / name).is_file():
            proc = subprocess.run(
                [gocmd(), "install", pkg],
                capture_output=True,
                text=True,
                check=False,
                env=env,
                cwd=scratch,
            )
            if proc.returncode != 0:
                return None
    return str(bindir)


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    return proc.stdout.strip()


def make_repo(fixture: Path, dest: Path, overlay: dict[str, str | None] | None = None) -> Path:
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(
        fixture,
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "gen", "bin", "metrics", ".lattice"),
    )
    for rel, text in (overlay or {}).items():
        f = dest / rel
        if text is None:
            f.unlink(missing_ok=True)
        else:
            f.write_text(text)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "arm-l",
        "GIT_AUTHOR_EMAIL": "arm-l@example.invalid",
        "GIT_COMMITTER_NAME": "arm-l",
        "GIT_COMMITTER_EMAIL": "arm-l@example.invalid",
    }
    for args in (["init", "-q", "-b", "main"], ["add", "."], ["commit", "-q", "-m", "fixture"]):
        subprocess.run(["git", *args], cwd=dest, check=True, env=env, capture_output=True)
    return dest


def run_gate(repo: Path, env: dict[str, str]) -> tuple[int, str]:
    proc = subprocess.run(
        ["sh", "-c", "./generate.sh && ./check.sh"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return proc.returncode, (proc.stdout + proc.stderr)[-400:]


def arm_l(ctx: dict[str, Any]) -> list[Probe]:
    """The Studio rebuild loop (``Rebuilder``) on the toy proto fixture, the agent the ``claude`` CLI
    through a shim that adds ``--max-turns 40`` and the remaining USD cap (sonnet). Calibration first,
    with no model: the reference solution passes, its 3 mutants fail. ``--self-test`` plays a scripted
    stub agent instead of the CLI (no model), to exercise the loop and every gate."""
    fixture = Path(ctx.get("fixture") or FIXTURES / "toybuild5")
    ids = ("L5-CAL", "L5-RUN", "L5-NOFALSE", "L5-G1", "L5-G2", "L5-G3", "L5-COST")
    probes = {i: Probe(i, "", "") for i in ids}
    probes["L5-RUN"] = Probe(
        "L5-RUN",
        "passes in 1 round (P 0.8), <= 3 rounds (P 0.95), USD 0.05-0.40",
        "passes in <= 3 rounds",
    )
    probes["L5-NOFALSE"] = Probe(
        "L5-NOFALSE",
        "no round reports spec_edited for an unedited protected directory",
        "0 false reports",
    )
    probes["L5-G1"] = Probe(
        "L5-G1",
        "the protected files and the proto directory sha256 base vs tip identical",
        "identical",
    )
    probes["L5-G2"] = Probe(
        "L5-G2",
        "no generated file in the commit except what [grpc] generate writes; a generate run before each gate",
        "true",
    )
    probes["L5-G3"] = Probe(
        "L5-G3", "clean git archive of the tip, ./generate.sh && ./check.sh x3 PASS", "3/3"
    )
    probes["L5-COST"] = Probe("L5-COST", "<= USD 5.00", "<= cap (a usage limit = NOT RUN)")
    probes["L5-CAL"] = Probe("L5-CAL", "reference PASS, 3/3 mutants fail", "exact")

    def stop(why: str, only: tuple[str, ...] = ids) -> list[Probe]:
        for i in only:
            if probes[i].status == "NOT RUN":
                probes[i].not_run(why)
        return [probes[i] for i in ids]

    if not fixture.is_dir():
        return stop(f"{fixture} does not exist")
    scratch = Path(ctx["scratch"]) / "armL"
    scratch.mkdir(parents=True, exist_ok=True)
    stub = bool(ctx.get("self_test"))
    claude = (
        ctx.get("claude_bin") or shutil.which("claude") or str(Path.home() / ".local/bin/claude")
    )
    if not stub and not Path(claude).exists():
        return stop(f"the claude CLI ({claude}) was not found")
    tools = toy_tools(scratch)
    if tools is None:
        return stop(
            "the generator tools (protoc-gen-go v1.36.5, protoc-gen-go-grpc v1.5.1) could not be built"
        )
    env = {**os.environ, "TOY_TOOLS_BIN": tools}
    ref = (
        REPO / "docs/results/data/2026-10-18-grpc-arms-m-l/toybuild-reference/main.go"
    ).read_text()
    # calibration, no model: the reference passes; each mutant fails
    mutants = {
        "list order reversed": ('ORDER BY id"', 'ORDER BY id DESC"'),
        "quantity check loosened": ("in.GetQty() < 0", "in.GetQty() < -1000"),
        "ALREADY_EXISTS -> UNKNOWN": ("codes.AlreadyExists", "codes.Unknown"),
    }
    cal: dict[str, Any] = {}
    variants = {"reference": ref} | {k: ref.replace(a, b) for k, (a, b) in mutants.items()}
    for k, (a, _b) in mutants.items():
        if a not in ref:
            cal[k] = "mutation site not found"
    for name, text in variants.items():
        repo = make_repo(
            fixture,
            scratch / "cal" / re.sub(r"\W+", "-", name),
            {"main.go": text, "shelf.go": None},
        )
        rc, tail = run_gate(repo, env)
        cal[name] = {"gate exit": rc, "tail": tail[-120:] if rc else ""}
    ok = cal.get("reference", {}).get("gate exit") == 0 and all(
        isinstance(cal[k], dict) and cal[k]["gate exit"] != 0 for k in mutants
    )
    probes["L5-CAL"].done(ok, cal)
    if not ok:
        return stop("calibration failed: the loop was not run")
    # the loop
    from lattice.studio.rebuild import FINISHED, Rebuilder

    overlay: dict[str, str | None] = {}
    if stub:  # self-test: round 4's working configuration, so the gates are exercised without L1/L2
        overlay["studio.toml"] = (
            (fixture / "studio.toml")
            .read_text()
            .replace('protected = ["proto",', 'protected = ["proto/toy/v1/shelf.proto",')
        )
        overlay["check.sh"] = (
            (fixture / "check.sh")
            .read_text()
            .replace('cd "$(dirname "$0")"\n', 'cd "$(dirname "$0")"\n./generate.sh\n', 1)
        )
    repo = make_repo(fixture, scratch / "repo", overlay)
    cfg = tomllib.loads((repo / "studio.toml").read_text())
    protected = cfg.get("mcp", {}).get("protected", [])
    ledger_dir = scratch / "ledger"
    shutil.rmtree(ledger_dir, ignore_errors=True)
    shim = scratch / "claude-shim.py"
    shim.write_text(ARML_SHIM)
    shim.chmod(0o755)
    log_lines: list[str] = []
    os.environ.update(
        {
            "ARML_REAL": claude,
            "ARML_DIR": str(ledger_dir),
            "ARML_CAP": str(ctx.get("cap_usd", 5.0)),
            "TOY_TOOLS_BIN": tools,
        }
    )
    table = dict(cfg["rebuild"])
    if stub:
        table["agent"] = "stub"
        (repo / "stub-rebuild.json").write_text(
            json.dumps(
                {
                    "rounds": [
                        {
                            "files": {"main.go": ref, "shelf.go": None},
                            "summary": "self-test: scripted stub agent",
                        }
                    ]
                }
            )
        )
        _git(repo, "add", "stub-rebuild.json")
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=arm-l",
                "-c",
                "user.email=arm-l@example.invalid",
                "commit",
                "-q",
                "-m",
                "stub script",
            ],
            cwd=repo,
            check=True,
        )
    rb = Rebuilder(
        repo,
        table,
        name="toybuild",
        model="sonnet",
        claude_bin=str(shim),
        protected=protected,
        log=log_lines.append,
        trailer="Lattice-Rebuild: arm-l",
    )
    ok_av, why = rb.available()
    if not ok_av:
        return stop(
            f"the rebuild loop is not available: {why}",
            ("L5-RUN", "L5-NOFALSE", "L5-G1", "L5-G2", "L5-G3", "L5-COST"),
        )
    started = rb.start([], scope="all", model="sonnet", max_rounds=3, baseline_wait=300)
    rid = started["id"] if isinstance(started, dict) and "id" in started else str(started)
    t0, rec = time.time(), {}
    while time.time() - t0 < float(ctx.get("arm_timeout", 2700)):
        rec = rb.get(rid, wait=10.0)
        if rec.get("status") in FINISHED:
            break
        time.sleep(2 if stub else 5)
    else:
        rb.stop(rid)
        rec = rb.get(rid)
    rb.close()
    rounds = rec.get("rounds") or []
    spent = 0.0
    if (ledger_dir / "ledger.json").is_file():
        spent = float(json.loads((ledger_dir / "ledger.json").read_text()).get("spent_usd", 0.0))
    probes["L5-RUN"].done(
        rec.get("status") == "passed" and len(rounds) <= 3,
        {"status": rec.get("status"), "rounds": len(rounds), "usd": spent, "stub agent": stub},
    )
    false_reports = sum(
        1
        for r in rounds
        for f in (r.get("check") or {}).get("findings", [])
        if f.get("signal_key") == "studio:spec_edited"
    )
    base, branch = rec.get("base"), rec.get("branch")
    tip = _git(repo, "rev-parse", branch) if branch else ""
    paths = [x for x in protected if x != "proto"]
    same: dict[str, bool] = {}
    if base and tip:
        for pth in paths:
            same[pth] = _git(repo, "rev-parse", f"{base}:{pth}") == _git(
                repo, "rev-parse", f"{tip}:{pth}"
            )
        same["proto/ (tree)"] = _git(repo, "rev-parse", f"{base}:proto") == _git(
            repo, "rev-parse", f"{tip}:proto"
        )
    probes["L5-NOFALSE"].done(
        false_reports == 0 and bool(same) and all(same.values()),
        {
            "spec_edited findings in rounds": false_reports,
            "protected paths unchanged": all(same.values()) if same else None,
        },
    )
    probes["L5-G1"].done(bool(same) and all(same.values()), same)
    changed = _git(repo, "diff", "--name-only", base, tip).split() if base and tip else []
    gen_in_commit = [c for c in changed if c.startswith("gen/") or c.endswith(".pb.go")]
    gen_runs = sum(1 for ln in log_lines if "generate" in ln.lower())
    probes["L5-G2"].done(
        bool(tip) and not gen_in_commit and gen_runs >= max(1, len(rounds)),
        {
            "generated files in the commit": gen_in_commit,
            "loop log lines naming the generate run": gen_runs,
            "rounds": len(rounds),
        },
        [ln for ln in log_lines if "generate" in ln.lower()][:6],
    )
    clean = scratch / "clean"
    shutil.rmtree(clean, ignore_errors=True)
    clean.mkdir()
    if tip:
        arch = subprocess.run(["git", "archive", tip], cwd=repo, capture_output=True, check=True)
        subprocess.run(["tar", "-x", "-C", str(clean)], input=arch.stdout, check=True)
        runs = [run_gate(clean, env)[0] for _ in range(3)]
        probes["L5-G3"].done(runs == [0, 0, 0], {"exit codes": runs})
    else:
        probes["L5-G3"].not_run("the loop produced no tip")
    cap = float(ctx.get("cap_usd", 5.0))
    probes["L5-COST"].done(spent <= cap, {"usd": spent, "cap": cap})
    return [probes[i] for i in ids]


# ---- main ---------------------------------------------------------------------------------------------------

ARM_FUNCS = {
    "0": "arm0",
    "G": "arm_g",
    "M": "arm_m",
    "L": "arm_l",
    "H5": "gated_arm",
    "SG": "gated_arm",
}
OUT_NAMES = {
    "0": "probes5-arm0.json",
    "G": "probes5-armg.json",
    "M": "probes5-armm.json",
    "L": "probes5-arml.json",
}


def library_hashes() -> dict[str, str]:
    files = [
        *MAY_CHANGE,
        *(f"{GO_SRC}/{g}" for g in GRPC_GO[1:]),
        f"{GO_SRC}/goextract_sqlraw.go",
        "dogfood/grpc_truth5.py",
        "dogfood/grpc_contract5.py",
        "dogfood/grpc_probes5.py",
        "dogfood/grpc_scorer5.py",
        "dogfood/grpc_scorer5_cases.json",
        *NAMED_FROZEN,
    ]
    return {f: sha((REPO / f).read_bytes()) for f in sorted(files) if (REPO / f).is_file()}


def run_check(name: str, ctx: dict[str, Any]) -> int:
    table = {
        "p5-code": p5_code,
        "p5-score": p5_score,
        "p5-proc": p5_proc,
        "p5-ledger": lambda: p5_ledger(ctx),
        "p5-links": lambda: p5_links(ctx),
        "p5-hapi": lambda: p5_hapi(ctx),
        "p5-arms": p5_arms,
        "p5-bc-ast": lambda: p5_bc_ast(Path(ctx["base"])),
        "p5-desc": lambda: fixture_probe("P5-DESC", "descwrap5", "", "", ctx),
        "p5-bopt": lambda: fixture_probe("P5-BOPT", "builderopt5", "", "", ctx),
        "p5-status": lambda: fixture_probe("P5-STATUS", "status5", "", "", ctx),
        "p5-aud": lambda: fixture_probe("P5-AUD", "audience5", "", "", ctx),
        "p5-sql": lambda: fixture_probe("P5-SQL", "sqlraw5", "", "", ctx),
        "p5-prot": lambda: p5_prot(ctx),
        "p5-gen": lambda: p5_gen(ctx),
        "p5-brief": lambda: p5_brief(ctx),
    }
    pr = table[name]()
    print(f"{pr.status} {pr.id} {json.dumps(pr.observed, default=str)[:400]}")
    return 0 if pr.status == "PASS" else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--arm", choices=tuple(ARM_FUNCS))
    ap.add_argument("--check", default=None, help="run one probe by name (what the mutants run)")
    ap.add_argument(
        "--self-test",
        action="store_true",
        help="run the machinery on stand-in fixtures for the inputs later stages build",
    )
    ap.add_argument("--base", type=Path, default=None, help="git archive of 978b468, extracted")
    ap.add_argument(
        "--s0",
        type=Path,
        default=None,
        help="the extracted tree of the S0 commit (the pure-move split)",
    )
    ap.add_argument(
        "--corpora",
        type=Path,
        default=None,
        help="the dev corpora (heldout/, heldout2/, heldout3/, toolshed/)",
    )
    ap.add_argument(
        "--streak",
        type=Path,
        default=None,
        help="--arm M: a Streak checkout (read only, through git archive)",
    )
    ap.add_argument("--streak-rev", default="a0679e9,1d00ede")
    ap.add_argument("--fixture", type=Path, default=None, help="--arm L: the toy fixture directory")
    ap.add_argument(
        "--cap-usd", type=float, default=5.0, help="--arm L: the CLI's cost estimate cap"
    )
    ap.add_argument("--claude-bin", default=None)
    ap.add_argument("--arm-timeout", type=float, default=2700)
    ap.add_argument(
        "--app",
        type=Path,
        default=None,
        help="--arm H5 / SG: the app directory itself (freeze.sha256 at its root)",
    )
    ap.add_argument(
        "--rehearse",
        action="store_true",
        help="--arm H5 / SG: end to end on any app, scratch ledger, no recorded-hash check",
    )
    ap.add_argument(
        "--process-test",
        choices=("run", "crash"),
        default=None,
        help="rehearse the process only; scores nothing",
    )
    ap.add_argument(
        "--ledger",
        type=Path,
        default=None,
        help="a scratch ledger (only with --rehearse / --process-test; never the real one)",
    )
    ap.add_argument("--freeze5", type=Path, default=DATA / "freeze5.sha256")
    ap.add_argument(
        "--prereg", type=Path, default=PREREG, help="the file whose amendments record the hashes"
    )
    ap.add_argument("--repo-root", type=Path, default=REPO)
    ap.add_argument("--out", type=Path, default=DATA)
    ap.add_argument("--scratch", type=Path, default=None)
    ap.add_argument("--reg-before", type=Path, default=None)
    ap.add_argument("--reg-after", type=Path, default=None)
    ap.add_argument(
        "--pr4all-from",
        type=Path,
        default=None,
        help="round 4's runner outputs (arm dirs 0/ and G/), run un-nested at round-5 code",
    )
    ap.add_argument(
        "--declared-sql", default="", help="corpora whose tables/writes may differ (Amendment 1)"
    )
    ap.add_argument(
        "--declared-flips", default="", help="corpora whose auth may flip (Amendment 1)"
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    args_in = list(sys.argv[1:] if argv is None else argv)
    if args_in[:1] == ["emit-det5"]:
        emit_det5(args_in[1], args_in[2], args_in[3])
        return 0
    if args_in[:1] == ["nw-work5"]:
        cmd_nw_work5(*args_in[1:4])
        return 0
    args = build_parser().parse_args(args_in)
    if args.arm in ("H5", "SG"):
        if args.app is None:
            print("--arm H5 / SG needs --app")
            return 2
        return gated_arm(args.arm, args)
    scratch = args.scratch or Path(tempfile.mkdtemp(prefix="grpc5-probes-"))
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("LATTICE_HELDOUT_BUILD", str(scratch / "hbuild"))
    ctx: dict[str, Any] = {
        "scratch": scratch,
        "base": args.base,
        "s0": args.s0,
        "corpora": args.corpora,
        "streak": args.streak,
        "streak_rev": args.streak_rev,
        "fixture": args.fixture,
        "cap_usd": args.cap_usd,
        "claude_bin": args.claude_bin,
        "arm_timeout": args.arm_timeout,
        "reg_before": args.reg_before,
        "reg_after": args.reg_after,
        "pr4all_from": args.pr4all_from,
        "self_test": args.self_test,
        "declared_sql": tuple(x for x in args.declared_sql.split(",") if x),
        "declared_flips": tuple(x for x in args.declared_flips.split(",") if x),
    }
    if args.check:
        return run_check(args.check, ctx)
    if args.arm is None:
        print("--arm or --check is required")
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    before = frozen_check(ctx)
    probes = {"0": arm0, "G": arm_g, "M": arm_m, "L": arm_l}[args.arm](ctx)
    after = frozen_check(ctx)
    doc = {
        "schema": "lattice.grpc-probes5/1",
        "arm": args.arm,
        "self_test": bool(args.self_test),
        "library_sha256": library_hashes(),
        "frozen_before": before,
        "frozen_after": after,
        "probes": [pr.row() for pr in probes],
    }
    name = (
        OUT_NAMES[args.arm]
        if not args.self_test
        else OUT_NAMES[args.arm].replace(".json", "-selftest.json")
    )
    (args.out / name).write_text(json.dumps(doc, indent=1, default=str) + "\n")
    if args.arm == "G":
        for key, fname in (
            ("rec_KILN", "reconcile-kilnworks.json"),
            ("flows_KILN", "flows-kilnworks.json"),
            ("rec_HARBOR", "reconcile-harborline.json"),
            ("flows_HARBOR", "flows-harborline.json"),
            ("rec_TOOL", "reconcile-toolshed.json"),
            ("flows_TOOL", "flows-toolshed.json"),
        ):
            if ctx.get(key) is not None:
                (args.out / fname).write_text(json.dumps(ctx[key], indent=1, default=str) + "\n")
    for pr in probes:
        print(f"{pr.status:8} {pr.id:44} {json.dumps(pr.observed, default=str)[:200]}")
    print("frozen files ok before:", before["ok"], "after:", after["ok"])
    failed = [pr for pr in probes if pr.status == "FAIL"]
    print(
        f"{sum(pr.status == 'PASS' for pr in probes)} passed, {len(failed)} failed, {sum(pr.status == 'NOT RUN' for pr in probes)} not run, {sum(pr.status == 'REPORTED' for pr in probes)} reported"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
