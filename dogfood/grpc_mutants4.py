"""Mutants of gRPC round 4 (docs/results/2026-10-18-grpc-round4-prereg.md, section 8): twenty-seven
new deliberate faults (GW4-1..9 registration, GU4-1..6 auth, GK4-1..4 codes, GT4-1..2 tables, GL4-1..3
login, GS4-1..3 scorer and runner) and round 3's thirty-five (GW1-GW11, GU1-GU3, GC1-GC2, GS1-GS2 of
``grpc_mutants3.py``, GR1-GR17 of ``grpc_mutants2.py``) re-applied at round-4 code. The run works on a
scratch copy of the worktree -- never on the worktree itself -- and the control (the copy unmodified)
runs every check first.

    uv run python dogfood/grpc_mutants4.py --scratch "$SCRATCH/m" --deskd <deskd> --kiln <parent> \\
        --harbor <parent> --base "$SCRATCH/base" --base-r1 "$SCRATCH/base-r1" $(seq -f GW4-%g 1 9) ...

A *check* is a pytest selection of the copy, a probe of ``grpc_probes4.py --check <name>`` or (for the
re-applied GR mutants) a probe of round 2's ``grpc_probes2.py --check <name>``, run against the copy
through ``PYTHONPATH``. A mutant is KILLED when a check gives a different verdict than the control's
(for pytest: a different set of failing ids), INVALID when its patch did not apply. Every listed check
is run, so the table says which killed it. A round-3 mutant whose text no longer applies is
re-expressed over the code that exists (``note``) and the table says so.
"""

# ruff: noqa: E501 -- mutant texts are data lines
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

import grpc_mutants2 as m2  # noqa: E402
import grpc_mutants3 as m3  # noqa: E402

GO = "lattice/gocheck/src/goextract_grpc.go"
GOPY = "lattice/importer/frontends/go_grpc.py"
GRPC = "lattice/spec/grpc.py"
SC4 = "dogfood/grpc_scorer4.py"
RUN4 = "dogfood/grpc_probes4.py"

WIRE = ["probe4", "p4-wire"]
AUTHI = ["probe4", "p4-authi"]
CODES = ["probe4", "p4-codes"]
TABLES = ["probe4", "p4-tables"]
LOGIN = ["probe4", "p4-login"]
SCORE = ["probe4", "p4-score"]
PROC = ["probe4", "p4-proc"]
BCEXT = ["probe4", "p4-bc-ext"]
BCAST = ["probe4", "p4-bc-ast"]
T_WIRE4 = ["pytest", "tests/grpc/test_wiring4.py"]
T_AUTH4 = ["pytest", "tests/grpc/test_auth4.py"]
T_CODES4 = ["pytest", "tests/grpc/test_codes4.py"]
T_TABLES4 = ["pytest", "tests/grpc/test_tables4.py"]
T_LOGIN4 = ["pytest", "tests/grpc/test_login4.py"]

NEW: dict[str, dict[str, Any]] = {
    "GW4-1": {
        "what": "D1.1 off: a register call with a parameter descriptor/impl is not resolved at call sites",
        "edits": [
            (
                GO,
                "fn.Lit == nil && (ip >= 0 || dp >= 0 && r.desc == nil)",
                "false && (ip >= 0 || dp >= 0 && r.desc == nil)",
            )
        ],
        "checks": [WIRE, T_WIRE4],
        "killer": "P4-WIRE, P4-UNIT",
    },
    "GW4-2": {
        "what": "D1.1 resolves only the first call site of a helper",
        "edits": [
            (
                GO,
                "					rs = append(rs, &c)\n",
                "					rs = append(rs, &c)\n					break\n",
            )
        ],
        "checks": [WIRE, T_WIRE4],
        "killer": "P4-WIRE (second service)",
    },
    "GW4-3": {
        "what": "D1.2 off: an options-applying constructor returning an interface is not followed",
        "edits": [
            (
                GO,
                "	if c, ok := unparen(x).(*ast.CallExpr); ok {\n		if callee := g.m.Callee(e, c); callee != nil {\n			set :=",
                "	if c, ok := unparen(x).(*ast.CallExpr); ok && false {\n		if callee := g.m.Callee(e, c); callee != nil {\n			set :=",
            )
        ],
        "checks": [WIRE, T_WIRE4],
        "killer": "P4-WIRE",
        "note": "D1.2 is realised by round 3's constructor rule (implType follows the one concrete type a local is built as); the mutant is round 3's GW6 expressed for the options-applying shape",
    },
    "GW4-4": {
        "what": "D1.3 off: an unresolved registration does not block not_registered (round-3 behaviour)",
        "edits": [(GO, "case reg == nil && first != nil:", "case false:")],
        "checks": [WIRE, T_WIRE4],
        "killer": "P4-WIRE (S8), P4-UNIT",
    },
    "GW4-5": {
        "what": "D1.3 too broad: an unreferenced function's unresolved call also blocks not_registered",
        "edits": [
            (
                GO,
                "if r.desc == nil && r.svc && !r.lost {\n\t\t\t\t\tcontinue\n\t\t\t\t}",
                "if r.desc == nil && r.svc {\n\t\t\t\t\tr.lost = true\n\t\t\t\t}",
            )
        ],
        "checks": [WIRE, T_WIRE4],
        "killer": "P4-WIRE (dead case)",
    },
    "GW4-6": {
        "what": "round-3 deviation 8.7 left: a package var read only by unreached code is not counted",
        "edits": [(GO, "			w.varFunc(p, n)\n", "			_ = n\n")],
        "checks": [WIRE, T_WIRE4],
        "killer": "P4-WIRE",
    },
    "GW4-7": {
        "what": "D1.4 off: interface method calls unresolved",
        "edits": [(GO, "} else if call && !ok {", "} else if false {")],
        "checks": [WIRE, T_WIRE4],
        "killer": "P4-WIRE (init registry)",
    },
    "GW4-8": {
        "what": "D1.4 too broad: implementers never instantiated also reached",
        "edits": [
            (
                GO,
                "if w.inst[fn.Recv] {\n				w.okM[n]",
                "if true {\n				w.okM[n]",
            )
        ],
        "checks": [WIRE, T_WIRE4],
        "killer": "P4-WIRE (negative)",
    },
    "GW4-9": {
        "what": "D1.4 func-type adapter: a conversion T(f) does not count as building T",
        "edits": [
            (
                GO,
                "w.inst[t.Sel.Name] = w.g.m.ByImport[path].Types[t.Sel.Name] != nil",
                "w.inst[t.Sel.Name] = false",
            )
        ],
        "checks": [WIRE, T_WIRE4],
        "killer": "P4-WIRE",
    },
    "GU4-1": {
        "what": "D2.1 off: method-value interceptors unresolved",
        "edits": [
            (
                GO,
                "	case *ast.Ident, *ast.SelectorExpr:\n		return g.funcRef(e, v, 0)",
                "	case *ast.Ident:\n		return g.funcRef(e, v, 0)",
            )
        ],
        "checks": [AUTHI, T_AUTH4],
        "killer": "P4-AUTHI",
    },
    "GU4-2": {
        "what": "D2.2 off: option-field flow not followed",
        "edits": [
            (
                GO,
                "ok && depth < 4 {\n			if ti :=",
                "ok && depth < 0 {\n			if ti :=",
            )
        ],
        "checks": [AUTHI, T_AUTH4],
        "killer": "P4-AUTHI",
    },
    "GU4-3": {
        "what": "D2.3 off: the standard health constants are not literal",
        "edits": [
            (
                GO,
                "		s, ok := healthStr[v.Sel.Name]\n		return s, ok",
                '		return "", false',
            )
        ],
        "checks": [AUTHI, T_AUTH4],
        "killer": "P4-AUTHI",
    },
    "GU4-4": {
        "what": "D2.4: a prefix assumed without HasPrefix evidence",
        "edits": [
            (
                GO,
                "func (g *gx) prefixField(field string) (found bool) {\n",
                "func (g *gx) prefixField(field string) (found bool) {\n	return true\n",
            )
        ],
        "checks": [AUTHI, T_AUTH4],
        "killer": "P4-AUTHI (wrong Public)",
    },
    "GU4-5": {
        "what": "D2.4: prefix entries matched exactly even with HasPrefix",
        "edits": [
            (
                GO,
                "func (g *gx) prefixField(field string) (found bool) {\n",
                "func (g *gx) prefixField(field string) (found bool) {\n	return false\n",
            )
        ],
        "checks": [AUTHI, T_AUTH4],
        "killer": "P4-AUTHI",
    },
    "GU4-6": {
        "what": "D2.4: an unresolvable builder argument dropped (allowlist treated as literal)",
        "edits": [
            (
                GO,
                "s, ok := g.strOf(e.fn.File, e, a)\n					good = good && ok",
                "s, ok := g.strOf(e.fn.File, e, a)\n					_ = ok",
            )
        ],
        "checks": [AUTHI, T_AUTH4],
        "killer": "P4-AUTHI (Public behind unread)",
    },
    "GK4-1": {
        "what": "D3.1 off: interceptor sentinel tables not read",
        "edits": [
            (GO, "; len(t.code) > 0 && len(res.codes) == 1", "; false && len(res.codes) == 1")
        ],
        "checks": [CODES, T_CODES4],
        "killer": "P4-CODES",
    },
    "GK4-2": {
        "what": "D3.1: %w wrapping not followed",
        "edits": [(GO, 'if strings.Contains(strings.ToLower(nm), "err") {', "if false {")],
        "checks": [CODES, T_CODES4],
        "killer": "P4-CODES",
    },
    "GK4-3": {
        "what": "D3.2 off: constant-key map lookups not read",
        "edits": [(GO, "ok && lit(ix.X) != nil {", "ok && false {")],
        "checks": [CODES, T_CODES4],
        "killer": "P4-CODES",
    },
    "GK4-4": {
        "what": "D3.3 off: the UNKNOWN placeholder kept for a plain error beside a translator",
        "edits": [
            (
                GO,
                'out.addUnread(xUnread{File: c.File, Line: c.Line, What: "grpc_code_not_literal", Reason: "the code of a plain error is chosen by a translation the extractor cannot read to a literal"})',
                "out.addCode(c)",
            )
        ],
        "checks": [CODES, T_CODES4],
        "killer": "P4-CODES",
        "note": "re-expressed after the first run: the first text (evalSentinel's unread branch) is an equivalent mutant, because translated() turns any plain error beside a translator into unread; the mutant now removes that step. Killed by the named unit tests, not by P4-CODES (its fixtures hold no plain error beside a translator)",
    },
    "GT4-1": {
        "what": "D5: harness-db tables override non-empty Go tables",
        "edits": [
            (
                GOPY,
                'bool(data["tables"]))',
                "False)",
            )
        ],
        "checks": [BCEXT, TABLES, T_TABLES4],
        "killer": "P4-BC, P4-TABLES",
    },
    "GT4-2": {
        "what": "D5.2 builds the database under the target root",
        "edits": [
            (
                GOPY,
                'tempfile.TemporaryDirectory(prefix="lattice-grpc-db-")',
                'tempfile.TemporaryDirectory(prefix="lattice-grpc-db-", dir=root)',
            )
        ],
        "checks": [TABLES, T_TABLES4],
        "killer": "P4-TABLES, P4-NW",
    },
    "GL4-1": {
        "what": "D6 off: the default username_field='name' / password_field='password' restored",
        "edits": [
            (
                GRPC,
                '    username_field: str | None = None,\n    password_field: str | None = None,\n    token_field: str = "token",\n) -> Any:',
                '    username_field: str | None = "name",\n    password_field: str | None = "password",\n    token_field: str = "token",\n) -> Any:',
            )
        ],
        "checks": [LOGIN, T_LOGIN4],
        "killer": "P4-LOGIN",
    },
    "GL4-2": {
        "what": "D6.2: an ambiguous message picks the first string fields",
        "edits": [
            (
                GRPC,
                "    if user is None or passw is None:\n        raise RuntimeError(",
                "    user, passw = user or (names[0] if names else None), passw or (names[1] if len(names) > 1 else None)\n    if user is None or passw is None:\n        raise RuntimeError(",
            )
        ],
        "checks": [LOGIN, T_LOGIN4],
        "killer": "P4-LOGIN",
    },
    "GL4-3": {
        "what": "D6.1: discovery overrides an explicit field",
        "edits": [
            (
                GRPC,
                "        if uf is None or pf is None:\n            uf, pf = _login_fields(rpc, service, method, uf, pf)",
                "        uf, pf = _login_fields(rpc, service, method, None, None)",
            )
        ],
        "checks": [LOGIN, T_LOGIN4],
        "killer": "P4-LOGIN, P4-BC",
    },
    "GS4-1": {
        "what": "scorer S10: a runner hash mismatch still labelled blind",
        "edits": [
            (
                SC4,
                "        elif run.get(k) != recorded[k]:",
                '        elif run.get(k) != recorded[k] and k != "runner":',
            )
        ],
        "checks": [SCORE, PROC],
        "killer": "P4-SCORE, P4-PROC",
    },
    "GS4-2": {
        "what": "scorer S8: not_registered vs implemented is not counted",
        "edits": [
            (
                SC4,
                'if (es in ("not_registered", "unimplemented") and ts == "implemented") or (',
                'if (es in ("unimplemented",) and ts == "implemented") or (',
            )
        ],
        "checks": [SCORE],
        "killer": "P4-SCORE",
    },
    "GS4-3": {
        "what": "runner: its own entry excluded from its freeze check (round 3's fix 2)",
        "edits": [
            (
                RUN4,
                'if listed.get(RUNNER) != own and own not in {rec["a4"].get("runner")}:',
                "if False:",
            )
        ],
        "checks": [PROC],
        "killer": "P4-PROC",
    },
}

# round-3 mutants re-expressed where their text no longer applies at round-4 code
RE: dict[str, dict[str, Any]] = {
    "GR17": {
        "checks": [["probe", "p2-bc-ast"], ["pytest", "tests/importer/test_workflows.py"], BCAST],
        "note": "re-expressed after the first run: round 2's p2-bc-ast is red at round-4 code (control, 3 differences: rpc_login, _login_fields, reconcile_with) and cannot see the mutant; P4-BC-AST (workflows.py byte-identical to BASE) is added",
    },
    "GW1": {
        "edits": [
            (
                GO,
                "func (w *grpcWalk) useVar(pkg *Pkg, name string) { w.add(w.varFunc(pkg, name)) }",
                "func (w *grpcWalk) useVar(pkg *Pkg, name string) {}",
            )
        ],
        "note": "re-expressed: useVar is now a one-line wrapper over varFunc",
    },
    "GW8": {
        "edits": [
            (
                GO,
                "				if nm != t.Short {\n					r.lost = w.vrefs[nm] > 0\n				}\n",
                "				r.lost = false\n",
            )
        ],
        "note": "re-expressed: the reference test moved into the per-registration loop (r.lost)",
    },
    "GW9": {
        "edits": [(GO, "r.lost = w.refs[nm] > 0 &&", "r.lost = w.refs[nm] >= 0 &&")],
        "note": "re-expressed over r.lost",
    },
    "GW11": {
        "edits": [
            (
                GO,
                "		for _, p := range g.m.Pkgs { // unwalked package variables, listed unwired (round-3 deviation 8.7)\n",
                "		for fn := range w.wired {\n			g.wired[fn] = true\n		}\n		for _, p := range g.m.Pkgs { // unwalked package variables, listed unwired (round-3 deviation 8.7)\n",
            )
        ],
        "note": "re-expressed after the extended walk loop",
    },
}

SWAP = {  # round 3's checks -> their round-4 counterparts
    "p3-wire": WIRE,
    "p3-authu": AUTHI,
    "p3-score": SCORE,
    "pr3-desk": ["probe4", "pr4-desk"],
    "p3-bc-ext": BCEXT,
}


def r3_checks(m: dict[str, Any]) -> list[list[str]]:
    out: list[list[str]] = []
    for c in m["checks"]:
        if c[0] == "probe3":
            out.append(SWAP.get(c[1], ["probe4", c[1]]))
        elif c == m3.T_WIRE:
            out += [list(c), T_WIRE4]
        elif c == m3.T_AUTH:
            out += [list(c), T_AUTH4]
        else:
            out.append(list(c))
    return out


def mutant(mid: str) -> dict[str, Any]:
    if mid in NEW:
        return NEW[mid]
    if mid in m3.NEW:
        m = {**m3.NEW[mid], **RE.get(mid, {})}
        return {**m, "checks": r3_checks(m)}
    return {**m2.MUTANTS[mid], **RE.get(mid, {})}


def run_check(tree: Path, args: argparse.Namespace, check: list[str], label: str) -> dict[str, Any]:
    env = {
        **os.environ,
        "PYTHONPATH": str(tree),
        "PYTHONDONTWRITEBYTECODE": "1",
        "LATTICE_GOCHECK_CACHE": str(args.scratch / f"gocheck-{label}"),
        "LATTICE_GRPCBRIDGE_CACHE": str(args.scratch / "bridge-shared"),
        "LATTICE_GRPC_NOTES_BIN": str(args.scratch / "notesapp"),
        "LATTICE_GRPC_SESSIONS_BIN": str(args.scratch / "sessionsapp"),
        "LATTICE_HELDOUT_BUILD": str(args.scratch / "hbuild"),
    }
    t0 = time.perf_counter()
    if check[0] == "pytest":
        cmd = [sys.executable, "-m", "pytest", *check[1:], "-q", "-p", "no:cacheprovider", "-rfE"]
    elif check[0] == "probe4":
        cmd = [
            sys.executable,
            str(tree / "dogfood" / "grpc_probes4.py"),
            "--check",
            check[1],
            "--scratch",
            str(args.scratch / f"s-{label}"),
            "--base",
            str(args.base),
            "--deskd",
            str(args.deskd),
            "--kiln",
            str(args.kiln),
            "--harbor",
            str(args.harbor),
        ]
    else:  # round 2's probe, for a re-applied GR mutant (its base is the round-1 tree)
        cmd = [
            sys.executable,
            str(tree / "dogfood" / "grpc_probes2.py"),
            "--check",
            check[1],
            "--scratch",
            str(args.scratch / f"s-{label}"),
            "--base",
            str(args.base_r1),
            "--deskd",
            str(args.deskd),
        ]
    p = subprocess.run(
        cmd, cwd=tree, env=env, capture_output=True, text=True, check=False, timeout=3600
    )
    text = p.stdout + p.stderr
    return {
        "check": " ".join(check[1:]),
        "kind": check[0],
        "returncode": p.returncode,
        "seconds": round(time.perf_counter() - t0, 1),
        "failed": sorted(set(re.findall(r"^(?:FAILED|ERROR) (\S+)", text, re.M))),
        "summary": (text.strip().splitlines() or [""])[-1][:200],
        "tail": text[-600:],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("ids", nargs="+")
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--base", type=Path, required=True, help="git archive of be26a94")
    ap.add_argument(
        "--base-r1", type=Path, required=True, help="git archive cea85fc (the GR mutants' probes)"
    )
    ap.add_argument("--deskd", type=Path, required=True)
    ap.add_argument("--kiln", type=Path, required=True)
    ap.add_argument("--harbor", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=REPO / "docs/results/data/2026-10-18-grpc-round4")
    ap.add_argument("--name", default="mutants4", help="the output file stem")
    args = ap.parse_args(argv)
    args.scratch.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO))
    os.environ.setdefault("LATTICE_GRPCBRIDGE_CACHE", str(args.scratch / "bridge-shared"))
    from tests.grpc.fixtures.notes import harness as notes
    from tests.grpc.fixtures.sessions import harness as sessions

    for name, h in (("notesapp", notes), ("sessionsapp", sessions)):
        shutil.copyfile(h.binary(), args.scratch / name)
        (args.scratch / name).chmod(0o755)
    tree = args.scratch / "tree"
    m2.copy_tree(tree)
    distinct: dict[str, list[str]] = {}
    for mid in args.ids:
        for c in mutant(mid)["checks"]:
            distinct.setdefault(" ".join(c), list(c))
    control: dict[str, dict[str, Any]] = {}
    for key, c in distinct.items():
        control[key] = run_check(tree, args, c, "control")
        print(
            f"control {key}: rc={control[key]['returncode']} {control[key]['summary'][:90]} ({control[key]['seconds']}s)",
            flush=True,
        )
    rows: list[dict[str, Any]] = []
    for mid in args.ids:
        m = mutant(mid)
        m2.copy_tree(tree)
        why = m2.apply(tree, m["edits"])
        row: dict[str, Any] = {
            "id": mid,
            "mutant": m["what"],
            "killer": m["killer"],
            "checks": [],
            "note": m.get("note", ""),
        }
        if why:
            row |= {"result": "INVALID", "why": why}
        else:
            killers = []
            for c in m["checks"]:
                r = run_check(tree, args, c, mid.lower())
                ctrl = control[" ".join(c)]
                differs = r["returncode"] != ctrl["returncode"] or r["failed"] != ctrl["failed"]
                r["differs_from_control"] = differs
                row["checks"].append(
                    {
                        k: r[k]
                        for k in (
                            "check",
                            "kind",
                            "returncode",
                            "seconds",
                            "failed",
                            "summary",
                            "differs_from_control",
                        )
                    }
                )
                if differs:
                    killers.append(f"{r['kind']}:{r['check']}")
            row["result"] = "KILLED" if killers else "SURVIVED"
            row["killed_by"] = killers
        rows.append(row)
        print(
            f"{row['result']:9} {mid:6} {m['what'][:60]:60} {row.get('killed_by', row.get('why', ''))}",
            flush=True,
        )
    killed = sum(r["result"] == "KILLED" for r in rows)
    print(
        f"{killed}/{len(rows)} killed (control checks: "
        + ", ".join(f"{k}={v['returncode']}" for k, v in control.items())
        + ")"
    )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"{args.name}.json").write_text(
        json.dumps(
            {
                "schema": "lattice.grpc-mutants4/1",
                "control": {
                    k: {x: v[x] for x in ("returncode", "summary", "seconds", "failed")}
                    for k, v in control.items()
                },
                "killed": killed,
                "of": len(rows),
                "mutants": rows,
            },
            indent=1,
        )
        + "\n"
    )
    return 0 if killed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
