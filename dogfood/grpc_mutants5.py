"""Mutants of gRPC round 5 (docs/results/2026-10-18-grpc-round5-prereg.md, section 11): forty new
deliberate faults and round 4's sixty-one killed mutants re-applied (GW4-9, the known survivor, is
excluded). The run works on a scratch copy of the worktree -- never on the worktree itself -- and the
control (the copy unmodified) runs every check first.

    uv run python dogfood/grpc_mutants5.py --scratch "$SCRATCH/m" --base "$SCRATCH/base" \\
        --s0 "$SCRATCH/s0" --corpora "$SCRATCH/corpora" --base-r1 "$SCRATCH/base-r1" --all-new --reapply-round4

A *check* is a pytest selection of the copy or a probe of ``grpc_probes5.py --check <name>`` run
against the copy through ``PYTHONPATH`` (round 4's probes and round 2's for the re-applied mutants). A
mutant is KILLED when a check gives a different verdict than the control's (for pytest: a different
set of failing ids), INVALID when its patch did not apply. Every listed check is run, so the table
says which killed it. A round-4 mutant whose text moved to another file of the split, or no longer
applies, is redirected or re-expressed over the code that exists (``note``) and the table says so.
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
import grpc_mutants4 as m4  # noqa: E402

SRC = "lattice/gocheck/src/"
GO = SRC + "goextract_grpc.go"
AUTH = SRC + "goextract_grpc_auth.go"
CODES = SRC + "goextract_grpc_codes.go"
SQL = SRC + "goextract_sqlraw.go"
PROTO = SRC + "goextract_proto.go"
ASSEMBLE = SRC + "goextract_assemble.go"
TRUTH = "dogfood/grpc_truth5.py"
CONTRACT = "dogfood/grpc_contract5.py"
SCORER = "dogfood/grpc_scorer5.py"
RUN5 = "dogfood/grpc_probes5.py"
GRPCPY = "lattice/spec/grpc.py"
REBUILD = "lattice/studio/rebuild.py"
GUARD = "lattice/mcp/guard.py"


def probe(name: str) -> list[str]:
    return ["probe5", name]


def pytest(*paths: str) -> list[str]:
    return ["pytest", *paths]


CODE, SCORE, PROC, LEDGER, LINKS, HAPI = (
    probe(x) for x in ("p5-code", "p5-score", "p5-proc", "p5-ledger", "p5-links", "p5-hapi")
)
DESC, BOPT, STATUS, AUD, SQLP = (
    probe(x) for x in ("p5-desc", "p5-bopt", "p5-status", "p5-aud", "p5-sql")
)
PROT, GEN, BRIEF = (probe(x) for x in ("p5-prot", "p5-gen", "p5-brief"))
T_TRUTH = pytest("tests/grpc/test_truth5.py")
T_LEDGER = pytest("tests/grpc/test_ledger5.py")
T_DESC = pytest("tests/grpc/test_descwrap5.py")
T_BOPT = pytest("tests/grpc/test_builderopt5.py")
T_STATUS = pytest("tests/grpc/test_status5.py")
T_AUD = pytest("tests/grpc/test_audience5.py")
T_SQL = pytest("tests/grpc/test_sqlraw5.py")
T_REBUILD = pytest("tests/studio/test_rebuild_grpc5.py")
T_GUARD = pytest("tests/mcp/test_guard_dirs5.py")

NEW: dict[str, dict[str, Any]] = {
    "GH5-1": {
        "what": "contract check prints a generic error for a build_db that returns None",
        "edits": [
            (
                CONTRACT,
                "f\"build_db returned {type(ref).__name__ if ref is None or not isinstance(ref, (str, os.PathLike)) else 'a different path'}\"",
                '"build_db failed"',
            )
        ],
        "checks": [HAPI],
        "killer": "P5-HAPI",
    },
    "GH5-2": {
        "what": "H1b off: a build_db that returns None is not 'the path I was given' (make_app(None))",
        "edits": [
            (
                GRPCPY,
                'db = build_db(p := Path(tmp) / "reconcile.db") or p',
                'db = build_db(p := Path(tmp) / "reconcile.db")',
            ),
            (
                GRPCPY,
                'db = build_db(p := Path(tmp) / "isolated.db") or p',
                'db = build_db(p := Path(tmp) / "isolated.db")',
            ),
            (
                GRPCPY,
                'db = build_db(p := Path(tmp) / "flow.db") or p',
                'db = build_db(p := Path(tmp) / "flow.db")',
            ),
        ],
        "checks": [probe("p5-bc-ast"), T_TRUTH],
        "killer": "P5-BC-AST (the three H1b sites), P5-BC",
    },
    "GN5-1": {
        "what": "H2: the grpc-go String() names are no longer in the table (the round-4 .upper() path)",
        "edits": [
            (
                TRUTH,
                "        elif value in _BY_GO:\n            name = _BY_GO[value]\n        else:",
                "        else:",
            )
        ],
        "checks": [CODE, LINKS, T_TRUTH],
        "killer": "P5-CODE, P5-LINKS",
    },
    "GN5-2": {
        "what": "H2: Canceled is not mapped to CANCELLED",
        "edits": [
            (TRUTH, '(1, "CANCELLED", "Canceled")', '(1, "CANCELLED", "Cancelled")'),
            (TRUTH, ', "CANCELED": "CANCELLED"}', "}"),
        ],
        "checks": [CODE, T_TRUTH],
        "killer": "P5-CODE",
    },
    "GN5-3": {
        "what": "H2: an unknown name is passed through instead of refused",
        "edits": [
            (
                TRUTH,
                '        raise TruthError(f"unknown gRPC code {value!r} at {at}")\n    return name',
                "        return str(value)\n    return name",
            )
        ],
        "checks": [CODE, T_TRUTH],
        "killer": "P5-CODE",
    },
    "GK5-L1": {
        "what": "H3: the replay ignores links",
        "edits": [
            (
                TRUTH,
                '        for ln in links:\n            if ln["to_step"] != i:',
                '        for ln in []:\n            if ln["to_step"] != i:',
            )
        ],
        "checks": [LINKS, T_TRUTH],
        "killer": "P5-LINKS",
    },
    "GK5-L2": {
        "what": "H3: the replay ignores conventions.step_index_base",
        "edits": [(TRUTH, "    frm, to = frm - b, to - b", "    frm, to = frm - 1, to - 1")],
        "checks": [LINKS, T_TRUTH],
        "killer": "P5-LINKS",
    },
    "GK5-L3": {
        "what": "H3: the replay checks the code only, not the dotted expect paths",
        "edits": [
            (
                TRUTH,
                '        for path, expect in (st.get("expect") or {}).items():',
                "        for path, expect in ():",
            )
        ],
        "checks": [LINKS, T_TRUTH],
        "killer": "P5-LINKS",
    },
    "GK5-L4": {
        "what": "H3: a missing from_field sends an empty field instead of link_unresolved",
        "edits": [
            (
                TRUTH,
                "            if _unresolved(value):\n                return FlowResult(",
                "            if False:\n                return FlowResult(",
            )
        ],
        "checks": [LINKS, T_TRUTH],
        "killer": "P5-LINKS",
    },
    "GL5-1": {
        "what": "H4: the ledger counts runs per --out (round 4)",
        "edits": [
            (
                TRUTH,
                'if e["arm"] != arm or e["target_freeze_sha256"] != target:',
                'if e["arm"] != arm or e["target_freeze_sha256"] != target or e["out"] != entries[-1]["out"]:',
            )
        ],
        "checks": [LEDGER, T_LEDGER],
        "killer": "P5-LEDGER",
    },
    "GL5-2": {
        "what": "H4: the hash chain is not verified",
        "edits": [(TRUTH, '        if e["prev_sha256"] != prev:', "        if False:")],
        "checks": [LEDGER, PROC, T_LEDGER],
        "killer": "P5-LEDGER, P5-PROC",
    },
    "GL5-3": {
        "what": "H4: a preflight refusal consumes a run",
        "edits": [
            (
                TRUTH,
                '        if e["status"] == "started":',
                '        if e["status"] in ("started", "refused_preflight"):',
            )
        ],
        "checks": [LEDGER, T_LEDGER],
        "killer": "P5-LEDGER",
    },
    "GL5-4": {
        "what": "H5: a symlinked app is accepted",
        "edits": [(TRUTH, "    if app.is_symlink():", "    if False:")],
        "checks": [PROC, T_LEDGER, T_TRUTH],
        "killer": "P5-PROC",
    },
    "GW5-1": {
        "what": "E1 off: a descriptor returned by a function is not read",
        "edits": [
            (
                GO,
                "	if c, ok := x.(*ast.CallExpr); ok {\n		return g.descFromFunc(e, c, 0)",
                "	if c, ok := x.(*ast.CallExpr); ok && false {\n		return g.descFromFunc(e, c, 0)",
            )
        ],
        "checks": [DESC, T_DESC],
        "killer": "P5-DESC",
    },
    "GW5-2": {
        "what": "E1.2: a ServiceName that is not a literal is taken as the original's",
        "edits": [(CODES, '		bad, cp.name = true, ""', "		cp.name = d.name")],
        "checks": [DESC, T_DESC],
        "killer": "P5-DESC",
    },
    "GW5-3": {
        "what": "E1.3: a Methods literal is ignored (the original set is kept)",
        "edits": [
            (
                GO,
                "case reg.desc != nil && reg.desc.setLit && !slices.Contains(reg.desc.methods, r.Name):",
                "case false:",
            )
        ],
        "checks": [DESC, T_DESC],
        "killer": "P5-DESC (S8)",
    },
    "GW5-4": {
        "what": "E1.4: the wrapper's translation is not applied",
        "edits": [
            (GO, "if reg != nil && reg.desc != nil && len(reg.desc.wrap) > 0 {", "if false {")
        ],
        "checks": [DESC, STATUS, T_DESC],
        "killer": "P5-DESC",
    },
    "GW5-5": {
        "what": "E2 off: a registration in a builder loop is not read from the option calls",
        "edits": [
            (
                GO,
                "if os := g.optionRegs(w, r, call, fn); os != nil {",
                "if os := g.optionRegs(w, r, call, fn); false && os != nil {",
            )
        ],
        "checks": [BOPT, T_BOPT],
        "killer": "P5-BOPT",
    },
    "GW5-6": {
        "what": "E2 resolves only the first option call site",
        "edits": [
            (
                GO,
                "			rs = append(rs, &c)\n		}\n	}\n	return rs",
                "			rs = append(rs, &c)\n			break\n		}\n	}\n	return rs",
            )
        ],
        "checks": [BOPT, T_BOPT],
        "killer": "P5-BOPT",
    },
    "GW5-7": {
        "what": "E2 registers from an option no builder ranges over",
        "edits": [
            (
                GO,
                "for _, fl := range w.fieldFlows(bt, fs.Sel.Name) {",
                'for _, fl := range append(w.fieldFlows(bt, fs.Sel.Name), w.fieldFlows(bt, "parked")...) {',
            )
        ],
        "checks": [BOPT, T_BOPT],
        "killer": "P5-BOPT (negative, S8)",
    },
    "GK5-1": {
        "what": "E3a off: a GRPCStatus() error type is not read",
        "edits": [
            (
                CODES,
                "		if ti := g.statusType(e, x); ti != nil {",
                "		if ti := g.statusType(e, x); ti != nil && false {",
            )
        ],
        "checks": [STATUS, T_STATUS],
        "killer": "P5-STATUS",
    },
    "GK5-2": {
        "what": "E3a: a field code is the union over all construction sites, not the return site's",
        "edits": [
            (
                CODES,
                "				return g.siteField(b, e, x, ti, s.Sel.Name, run)",
                """				for _, f2 := range g.m.AllFuncs() {
					e2 := g.m.EnvOf(f2)
					ast.Inspect(f2.Body, func(n ast.Node) bool {
						if cl, ok := n.(*ast.CompositeLit); ok && g.m.Infer(e2, cl).deref().rowTypeAny() == ti {
							if c, ok := g.codeOf(f2.File, e2, fieldExpr(cl, ti, s.Sel.Name), 0); ok {
								out.addCode(xCode{Name: codeNameList[c], Number: c, Kind: "status_error", File: file, Line: line, In: "union"})
							}
						}
						return true
					})
				}
				return 0, false""",
            )
        ],
        "checks": [STATUS, T_STATUS],
        "killer": "P5-STATUS",
    },
    "GK5-3": {
        "what": "E3b off: a code taken as a parameter is not bound at the call",
        "edits": [
            (CODES, '			if g.src(p.typ) == "codes.Code" {', "			if false {")
        ],
        "checks": [STATUS, T_STATUS],
        "killer": "P5-STATUS",
    },
    "GK5-4": {
        "what": "E3c: the WithDetails chain is not read through",
        "edits": [
            (
                CODES,
                'isSel && s.Sel.Name == "WithDetails" {',
                'isSel && s.Sel.Name == "WithDetails" && false {',
            )
        ],
        "checks": [STATUS, T_STATUS],
        "killer": "P5-STATUS",
    },
    "GK5-5": {
        "what": "E3d adds the gate's code to the Public rpcs too",
        "edits": [
            (
                AUTH,
                "				a := lits[0]\n				if pub {",
                "				a := lits[0]\n				g.gateCodes(r, a)\n				if pub {",
            )
        ],
        "checks": [STATUS, T_STATUS],
        "killer": "P5-ICODE",
    },
    "GK5-6": {
        "what": "E3d adds codes from an unclear interceptor",
        "edits": [
            (
                AUTH,
                '		x.Kind, x.Reason = "auth_not_literal", why\n',
                '		x.Kind, x.Reason = "auth_not_literal", why\n		for _, c := range es.codes {\n			if c.Kind == "status_error" {\n				c.Kind, c.File, c.Line, c.In = "interceptor", x.File, x.Line, x.Func\n				x.codes = append(x.codes, c)\n			}\n		}\n',
            ),
            (
                AUTH,
                '					r.Auth, r.AuthReason = "unclear", why\n',
                '					r.Auth, r.AuthReason = "unclear", why\n					g.gateCodes(r, auth[0])\n',
            ),
        ],
        "checks": [STATUS, T_STATUS],
        "killer": "P5-ICODE",
    },
    "GA5-1": {
        "what": "E4a off: the method name's other uses are not checked (the round-4 empty allowlist)",
        "edits": [
            (
                AUTH,
                'if w := g.methodFlows(b, isM, true, &x, 0); w != "" && literal {',
                'if w := g.methodFlows(b, isM, true, &x, 0); w != "" && literal && false {',
            )
        ],
        "checks": [AUD, T_AUD],
        "killer": "P5-AUD (S8)",
    },
    "GA5-2": {
        "what": "E4b off: an option gate is read as unclear for every rpc",
        "edits": [
            (AUTH, "	if !ok || be.Op != token.EQL && be.Op != token.NEQ {", "	if true {")
        ],
        "checks": [AUD, T_AUD],
        "killer": "P5-AUD",
    },
    "GA5-3": {
        "what": "E4b: an absent option is treated as the public value",
        "edits": [(AUTH, "	val := o.Zero\n", "	val := o.Value\n")],
        "checks": [AUD, T_AUD],
        "killer": "P5-AUD (wrong Public)",
    },
    "GA5-4": {
        "what": "E4c: a custom option is recorded on every rpc parsed before it in the service",
        "edits": [
            (
                PROTO,
                "						r.CustomOptions[o.key] = o.val\n",
                "						r.CustomOptions[o.key] = o.val\n						for _, pr := range svc.Rpcs {\n							pr.CustomOptions = r.CustomOptions\n						}\n",
            )
        ],
        "checks": [AUD, T_AUD],
        "killer": "P5-AUD",
    },
    "GS5-1": {
        "what": "E5 off: database/sql statements are not read",
        "edits": [
            (
                SQL,
                "	tables, writes, unread := g.rpcSQL(ci)\n",
                "	var tables []string\n	var writes []xWrite\n	var unread []xUnread\n",
            )
        ],
        "checks": [SQLP, T_SQL],
        "killer": "P5-SQL",
    },
    "GS5-2": {
        "what": "E5.2: a %s table is read as a literal",
        "edits": [
            (
                SQL,
                "				case last == 's' || last == 'v' || last == 'q':",
                "				case last == 'q':",
            )
        ],
        "checks": [SQLP, T_SQL],
        "killer": "P5-SQL",
    },
    "GS5-3": {
        "what": "E5.3: an UPDATE is classed create",
        "edits": [
            (
                SQL,
                '	case kw == "UPDATE":\n		op = "update"',
                '	case kw == "UPDATE":\n		op = "create"',
            )
        ],
        "checks": [SQLP, T_SQL],
        "killer": "P5-SQL",
    },
    "GS5-4": {
        "what": "E5.4: the HTTP walk reads the SQL statements too",
        "edits": [
            (
                ASSEMBLE,
                "		sort.Strings(out.Tables)\n		for _, md := range ci.models {",
                "		hq, _, _ := g.rpcSQL(ci)\n		out.Tables = append(out.Tables, hq...)\n		sort.Strings(out.Tables)\n		for _, md := range ci.models {",
            )
        ],
        "checks": [probe("p5-bc-ast"), T_SQL],
        "killer": "P5-BC (HTTP bytes), unit test_e5_4",
    },
    "GR5-1": {
        "what": "L1 off: a protected directory is compared as a file",
        "edits": [
            (
                REBUILD,
                "            if paths is None and self._is_tree(run, p):",
                "            if False and paths is None and self._is_tree(run, p):",
            )
        ],
        "checks": [PROT, T_REBUILD],
        "killer": "P5-PROT",
    },
    "GR5-2": {
        "what": "L1: a file added under a protected directory is kept",
        "edits": [
            (
                REBUILD,
                "                (run.worktree / p).unlink(missing_ok=True)\n",
                "                pass\n",
            )
        ],
        "checks": [PROT, T_REBUILD],
        "killer": "P5-PROT",
    },
    "GR5-3": {
        "what": "L1b off: the guard does not protect a path under a protected directory",
        "edits": [(GUARD, "os.path.isdir(p) and k.startswith(", "False and k.startswith(")],
        "checks": [PROT, T_GUARD],
        "killer": "P5-PROT",
    },
    "GR5-4": {
        "what": "L2: generate runs after the gates, not before",
        "edits": [
            (
                REBUILD,
                "            gen = self._generate(run, n)  # L2: the stubs, before the gates\n            gates, counts, gate_findings = self._gate_round(run, n)\n",
                "            gates, counts, gate_findings = self._gate_round(run, n)\n            gen = self._generate(run, n)\n",
            )
        ],
        "checks": [GEN, T_REBUILD],
        "killer": "P5-GEN",
    },
    "GR5-5": {
        "what": "L3: the brief omits the protos",
        "edits": [
            (
                REBUILD,
                "    if not protos:\n        return []\n    gen = (",
                "    if True:\n        return []\n    gen = (",
            )
        ],
        "checks": [BRIEF, T_REBUILD],
        "killer": "P5-BRIEF",
    },
    "GP5-1": {
        "what": "S10'/S12: the run index is taken from the runner's output, not the ledger",
        "edits": [
            (
                SCORER,
                '            "run_index": ledger.get("run_index"),',
                '            "run_index": run.get("run_index", 1),',
            )
        ],
        "checks": [SCORE, PROC],
        "killer": "P5-SCORE, P5-PROC",
    },
}

REAPPLY = [m for m in m4.NEW if m != "GW4-9"] + [m for m in m3.NEW] + [m for m in m2.MUTANTS]
FILES = (GO, AUTH, CODES)


def apply5(tree: Path, edits: list[tuple[str, str, str]]) -> tuple[str | None, list[str]]:
    """Like round 2's ``apply``; an edit of the split file whose anchor moved to a sibling is redirected."""
    notes: list[str] = []
    for rel, old, new in edits:
        target = rel
        if rel == m4.GO and old not in (tree / rel).read_text():
            for alt in FILES[1:]:
                if old in (tree / alt).read_text():
                    target = alt
                    notes.append(f"{old[:40]!r}: text is in {Path(alt).name}")
                    break
        if rel == m4.GO and target == rel and old not in (tree / rel).read_text():
            return (
                f"{rel}: the text to replace is not in any of the three files: {old[:70]!r}",
                notes,
            )
        path = tree / target
        text = path.read_text()
        if old not in text:
            return f"{target}: the text to replace is not there: {old[:70]!r}", notes
        path.write_text(text.replace(old, new, 1))
    return None, notes


def run_check(tree: Path, args: argparse.Namespace, check: list[str], label: str) -> dict[str, Any]:
    if check[0] != "probe5":
        return m4.run_check(tree, args, check, label)
    env = {
        **os.environ,
        "PYTHONPATH": str(tree),
        "PYTHONDONTWRITEBYTECODE": "1",
        "LATTICE_GOCHECK_CACHE": str(args.scratch / f"gocheck-{label}"),
        "LATTICE_GRPCBRIDGE_CACHE": str(args.scratch / "bridge-shared"),
        "LATTICE_HELDOUT_BUILD": str(args.scratch / "hbuild"),
    }
    cmd = [sys.executable, str(tree / "dogfood" / "grpc_probes5.py"), "--check", check[1],
           "--scratch", str(args.scratch / f"s-{label}"), "--base", str(args.base),
           "--s0", str(args.s0), "--corpora", str(args.corpora)]  # fmt: skip
    t0 = time.perf_counter()
    p = subprocess.run(
        cmd, cwd=tree, env=env, capture_output=True, text=True, check=False, timeout=3600
    )
    text = p.stdout + p.stderr
    return {
        "check": check[1],
        "kind": "probe5",
        "returncode": p.returncode,
        "seconds": round(time.perf_counter() - t0, 1),
        "failed": re.findall(r"^(PASS|FAIL|NOT RUN)\b", text, re.M)[:1],  # the probe's verdict
        "summary": (text.strip().splitlines() or [""])[-1][:300],
        "tail": text[-600:],
    }


# round-4 mutants whose text changed at round-5 code, re-expressed over the code that exists
RE5: dict[str, dict[str, Any]] = {
    "GK4-2": {
        "edits": [
            (
                CODES,
                'if strings.Contains(strings.ToLower(nm), "err") || g.statusType(e, a) != nil {',
                "if false {",
            )
        ],
        "note": "re-expressed: the anchor carries E3a's `|| g.statusType(e, a) != nil` clause",
    },
    "GR17": {
        "checks": [probe("p5-bc-ast"), ["pytest", "tests/importer/test_workflows.py"]],
        "note": "re-expressed: round 4's p4-bc-ast is red at round-5 code in the control (grpc.py carries H1b) and cannot see the mutant; P5-BC-AST (workflows.py byte-identical to BASE) takes its place",
    },
}


def mutant(mid: str) -> dict[str, Any]:
    return NEW[mid] if mid in NEW else {**m4.mutant(mid), **RE5.get(mid, {})}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--base", type=Path, required=True, help="git archive of 978b468")
    ap.add_argument("--s0", type=Path, required=True, help="the extracted S0 tree")
    ap.add_argument("--corpora", type=Path, required=True)
    ap.add_argument(
        "--base-r1", type=Path, required=True, help="git archive cea85fc (round 2's probes)"
    )
    ap.add_argument("--all-new", action="store_true")
    ap.add_argument("--reapply-round4", action="store_true")
    ap.add_argument("--out", type=Path, default=REPO / "docs/results/data/2026-10-18-grpc-round5")
    ap.add_argument("--name", default="mutants5", help="the output file stem")
    args = ap.parse_args(argv)
    ids = list(args.ids)
    if args.all_new:
        ids += list(NEW)
    if args.reapply_round4:
        ids += REAPPLY
    # the round-4 checks take the round-4 spellings of the corpora paths
    args.deskd = args.corpora / "heldout" / "deskd"
    args.kiln = args.corpora / "heldout2"
    args.harbor = args.corpora / "heldout3"
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
    for mid in ids:
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
    for mid in ids:
        m = mutant(mid)
        m2.copy_tree(tree)
        why, notes_ = apply5(tree, m["edits"])
        row: dict[str, Any] = {"id": mid, "round": "5" if mid in NEW else "4", "mutant": m["what"], "killer": m["killer"], "checks": [], "note": "; ".join([m.get("note", ""), *notes_]).strip("; ")}  # fmt: skip
        if why:
            row |= {"result": "INVALID", "why": why}
        else:
            killers = []
            for c in m["checks"]:
                r = run_check(tree, args, c, mid.lower())
                ctrl = control[" ".join(c)]
                differs = r["returncode"] != ctrl["returncode"] or r["failed"] != ctrl["failed"]
                r["differs_from_control"] = differs
                row["checks"].append({k: r[k] for k in ("check", "kind", "returncode", "seconds", "failed", "summary", "differs_from_control")})  # fmt: skip
                if differs:
                    killers.append(f"{r['kind']}:{r['check']}")
            row["result"] = "KILLED" if killers else "SURVIVED"
            row["killed_by"] = killers
        rows.append(row)
        print(
            f"{row['result']:9} {mid:7} {m['what'][:60]:60} {row.get('killed_by', row.get('why', ''))}",
            flush=True,
        )
    killed = sum(r["result"] == "KILLED" for r in rows)
    new_rows = [r for r in rows if r["round"] == "5"]
    old_rows = [r for r in rows if r["round"] == "4"]
    print(f"{killed}/{len(rows)} killed: new {sum(r['result'] == 'KILLED' for r in new_rows)}/{len(new_rows)}, re-applied {sum(r['result'] == 'KILLED' for r in old_rows)}/{len(old_rows)}")  # fmt: skip
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"{args.name}.json").write_text(
        json.dumps(
            {
                "schema": "lattice.grpc-mutants5/1",
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
