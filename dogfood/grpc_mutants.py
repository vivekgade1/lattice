"""Mutants of Spike I (docs/results/2026-10-17-grpc-prereg.md, section 6): sixteen deliberate
faults in the gRPC code, each of which the probes (here: the named tests the probes run) must
catch. The run works on a scratch copy of the worktree -- never on the worktree itself --
and the control (the copy unmodified) must pass first.

    uv run python dogfood/grpc_mutants.py --scratch "$SCRATCH/m" $(seq -f GX%g 1 16)

A mutant is KILLED when its selected tests fail, SURVIVED when they pass and INVALID when the
patch did not apply or the mutated Go did not build (an invalid mutant is not a kill).
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
REPO = Path(__file__).resolve().parents[1]

G = "lattice/gocheck/src"
GR = f"{G}/goextract_grpc.go"
PR = f"{G}/goextract_proto.go"
BR = "lattice/grpcbridge"

EXTRACT = ["tests/grpc/test_extract_notes.py"]
READER = ["tests/grpc/test_proto_reader.py"]
RUNTIME = "tests/grpc/test_runtime.py"

# id -> (what, file, old, new, [test nodes / -k expressions], the pre-registration's killer)
MUTANTS: dict[str, dict[str, Any]] = {
    "GX1": {
        "what": "the proto reader treats `repeated` / proto3 `optional` as a single field",
        "edits": [
            (PR, 'label = "repeated"', 'label = "single"'),
            (PR, 'label = "optional"', 'label = "single"'),
        ],
        "tests": READER,
        "killer": "P0-PARSE",
    },
    "GX2": {
        "what": "a streaming rpc kept as unary",
        "edits": [(GR, "case r.ClientStreaming || r.ServerStreaming:", "case false:")],
        "tests": READER + EXTRACT,
        "killer": "P0-UNREAD, P0-EXT",
    },
    "GX3": {
        "what": "an rpc served by Unimplemented*Server reported implemented",
        "edits": [(GR, "if h == nil || isStub(h) {", "if h == nil {")],
        "tests": EXTRACT + [RUNTIME, "-k", "reconcile or test_extract_matches"],
        "killer": "P0-EXT, PG-REC",
    },
    "GX4": {
        "what": "status.Errorf's code ignored (every site UNKNOWN)",
        "edits": [
            (
                GR,
                '\t\t\t\tout.addCode(site(n, "status_error"))\n\t\t\t} else {\n\t\t\t\tout.addUnread(xUnread{File: file, Line: line, What: "grpc_code_not_literal",\n\t\t\t\t\tReason: "the code of `" + g.src(c) + "` is chosen at run time"})\n\t\t\t}\n\t\t\treturn out\n\t\tcase path == "errors"',
                '\t\t\t\tif name == "Errorf" {\n\t\t\t\t\tn = 2\n\t\t\t\t}\n\t\t\t\tout.addCode(site(n, "status_error"))\n\t\t\t} else {\n\t\t\t\tout.addUnread(xUnread{File: file, Line: line, What: "grpc_code_not_literal",\n\t\t\t\t\tReason: "the code of `" + g.src(c) + "` is chosen at run time"})\n\t\t\t}\n\t\t\treturn out\n\t\tcase path == "errors"',
            )
        ],
        "tests": EXTRACT,
        "killer": "P0-EXT",
    },
    "GX5": {
        "what": "the interceptor's public set ignored (every rpc Member)",
        "edits": [(GR, "for _, m := range auth[0].Public {", "for _, m := range []string{} {")],
        "tests": EXTRACT
        + [RUNTIME, "-k", "extracted_auth or access_matrix or test_extract_matches"],
        "killer": "P0-EXT, PG-AUTH",
    },
    "GX6": {
        "what": "`return nil, err` not followed through the closure",
        "edits": [
            (
                GR,
                '\t\t\t\treturn g.evalCallErr(b, e, c, depth, run)\n\t\t\t}\n\t\t\tnotTraced(v.Pos(), "the error `"',
                '\t\t\t\t_ = c\n\t\t\t\treturn out\n\t\t\t}\n\t\t\tnotTraced(v.Pos(), "the error `"',
            )
        ],
        "tests": EXTRACT,
        "killer": "P0-EXT (N's passthrough rpcs)",
    },
    "GX7": {
        "what": "a non-literal code guessed as the first map value instead of unread",
        "edits": [
            (
                GR,
                '\t\t\t} else {\n\t\t\t\tout.addUnread(xUnread{File: file, Line: line, What: "grpc_code_not_literal",\n\t\t\t\t\tReason: "the code of `" + g.src(c) + "` is chosen at run time"})\n\t\t\t}\n\t\t\treturn out\n\t\tcase path == "errors"',
                '\t\t\t} else {\n\t\t\t\tout.addCode(site(9, "status_error"))\n\t\t\t}\n\t\t\treturn out\n\t\tcase path == "errors"',
            )
        ],
        "tests": EXTRACT,
        "killer": "P0-EXT",
    },
    "GX8": {
        "what": 'goextract emits "grpc": null for an HTTP-only module',
        "edits": [(f"{G}/goextract.go", 'json:"grpc,omitempty"', 'json:"grpc"')],
        "tests": ["tests/grpc/test_bc.py", "tests/grpc/test_extract_notes.py"],
        "killer": "P0-BC (fixture hashes)",
    },
    "GX9": {
        "what": "Route.transport serialised for HTTP routes (OMIT_DEFAULT dropped)",
        "edits": [
            (
                "lattice/importer/appmodel.py",
                'transport: str = field(default="http", metadata=OMIT_DEFAULT)',
                'transport: str = "http"',
            )
        ],
        "tests": ["tests/grpc/test_bc.py", "tests/grpc/test_extract_notes.py"],
        "killer": "P0-BC (RepoModel JSON)",
    },
    "GX10": {
        "what": "go_grpc.detect true on G-mixed (a gRPC client import only)",
        "edits": [
            (
                "lattice/importer/frontends/go_grpc.py",
                "        return has_server_registration(module_dir)",
                "        return True",
            )
        ],
        "tests": ["tests/grpc/test_bc.py", "tests/grpc/test_extract_notes.py"],
        "killer": "P0-BC (select answers gin)",
    },
    "GX11": {
        "what": "a gateway binding added as a second endpoint",
        "edits": [
            (
                "lattice/importer/frontends/go_grpc.py",
                "    return tuple(routes), tuple(excluded)",
                '    for r in list(routes):\n        for m, p in r.http_bindings:\n            routes.append(dataclasses.replace(r, method=m, path=p, transport="http", rpc=None, http_bindings=()))\n    return tuple(routes), tuple(excluded)',
            )
        ],
        "tests": EXTRACT,
        "killer": "P0-MODEL (11 endpoints, not 13)",
    },
    "GX12": {
        "what": "the checker passes an Rpc whatever its code (code not compared)",
        "edits": [
            ("lattice/spec/check.py", "    if answer.code != step.expect_code:", "    if False:")
        ],
        "tests": [RUNTIME, "-k", "deliberate_fault or code_expectations or dev_spec"],
        "killer": "PG-D",
    },
    "GX13": {
        "what": "int64 kept as protojson strings",
        "edits": [
            (f"{BR}/call.go", "\t\tif n >= -maxSafe && n <= maxSafe {", "\t\tif false {"),
            (f"{BR}/call.go", "\t\tif n <= uint64(maxSafe) {", "\t\tif false {"),
        ],
        "tests": [RUNTIME, "-k", "message_form or expect_message"],
        "killer": "PG-J",
    },
    "GX14": {
        "what": "expect_db / layer C skipped after an rpc",
        "edits": [
            (
                "lattice/spec/check.py",
                "    if monitor is not None and callable(after):  # layer C: the invariants after the rpc",
                "    if False:",
            ),
            (
                "lattice/spec/check.py",
                '    if step.expect_db:\n        problems += _expect_db(spec, db, step.expect_db)\n        problems += unestablished(scenario, i, step.expect_db, problems)\n    verdict = "correct"\n    if problems:\n        verdict = "loud" if answer.code in LOUD_CODES',
                '    verdict = "correct"\n    if problems:\n        verdict = "loud" if answer.code in LOUD_CODES',
            ),
        ],
        "tests": [RUNTIME, "-k", "layer_c or deliberate_fault"],
        "killer": "PG-C",
    },
    "GX15": {
        "what": "the bridge dials a non-loopback target",
        "edits": [
            (
                f"{BR}/call.go",
                '\tif host != "127.0.0.1" && host != "::1" {',
                '\tif host != "127.0.0.1" && host != "::1" && false {',
            )
        ],
        "tests": [RUNTIME, "-k", "bridge"],
        "killer": "PG-BR",
    },
    "GX16": {
        "what": "no process-group stop after a ready:grpc startup failure",
        "edits": [
            (
                "lattice/spec/process.py",
                '                    in_use = "address already in use" in str(err).lower()\n                    self._stop_locked()',
                '                    in_use = "address already in use" in str(err).lower()\n                    if self.ready == "http":\n                        self._stop_locked()',
            )
        ],
        "tests": [RUNTIME, "-k", "nothing_is_left"],
        "killer": "PG-LEAK",
    },
}


def copy_tree(dst: Path) -> None:
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(
        REPO,
        dst,
        symlinks=True,
        ignore=shutil.ignore_patterns(
            ".git", ".venv", "__pycache__", "*.pyc", ".pytest_cache", ".ruff_cache", "node_modules"
        ),
    )


def apply(tree: Path, edits: list[tuple[str, str, str]]) -> str | None:
    """Apply the edits; None when all applied, else why not."""
    for rel, old, new in edits:
        path = tree / rel
        text = path.read_text()
        if text.count(old) < 1:
            return f"{rel}: the text to replace is not there: {old[:60]!r}"
        path.write_text(text.replace(old, new, 1))
    return None


def builds(tree: Path, edits: list[tuple[str, str, str]]) -> str | None:
    """None when every Go package a mutant touched still builds (a mutant that does not
    compile is invalid, not killed), else the compiler's complaint."""
    dirs = sorted({(tree / rel).parent for rel, _, _ in edits if rel.endswith(".go")})
    for d in dirs:
        p = subprocess.run(
            ["go", "build", "-o", os.devnull, "."],
            cwd=d,
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "GOFLAGS": "-mod=readonly"},
        )
        if p.returncode != 0:
            return f"{d.relative_to(tree)} does not build: {p.stderr.strip()[:200]}"
    return None


def run_tests(tree: Path, scratch: Path, label: str, tests: list[str]) -> dict[str, Any]:
    nodes = [t for t in tests if t.startswith("tests/")]
    extra = [t for t in tests if not t.startswith("tests/")]
    env = {
        **os.environ,
        "PYTHONPATH": str(tree),
        "LATTICE_GOCHECK_CACHE": str(scratch / f"gocheck-{label}"),
        "LATTICE_GRPCBRIDGE_CACHE": str(scratch / f"bridge-{label}"),
    }
    # the notes server is built from fixtures a mutant does not touch: build it once
    env.setdefault("LATTICE_GRPC_NOTES_BIN", str(scratch / "notesapp"))
    t0 = time.perf_counter()
    p = subprocess.run(
        [sys.executable, "-m", "pytest", *nodes, *extra, "-q", "-x", "-p", "no:cacheprovider"],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=1800,
    )
    text = p.stdout + p.stderr
    return {
        "returncode": p.returncode,
        "seconds": round(time.perf_counter() - t0, 1),
        "summary": text.strip().splitlines()[-1] if text.strip() else "",
        "failed": sorted(set(re.findall(r"^FAILED (\S+)", text, re.M))),
        "build_error": (
            "go build" in text and "failed" in text and p.returncode != 0 and "FAILED" not in text
        ),
        "tail": text[-800:],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("ids", nargs="+")
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=REPO / "docs/results/data/2026-10-17-grpc")
    args = ap.parse_args(argv)
    args.scratch.mkdir(parents=True, exist_ok=True)
    # the notes server: built once from the worktree's fixtures, shared by every run
    sys.path.insert(0, str(REPO))
    os.environ.setdefault("LATTICE_GRPCBRIDGE_CACHE", str(args.scratch / "bridge-shared"))
    from tests.grpc.fixtures.notes import harness

    shutil.copyfile(harness.binary(), args.scratch / "notesapp")
    (args.scratch / "notesapp").chmod(0o755)

    tree = args.scratch / "tree"
    rows: list[dict[str, Any]] = []
    copy_tree(tree)
    control = run_tests(
        tree,
        args.scratch,
        "control",
        [
            "tests/grpc/test_proto_reader.py",
            "tests/grpc/test_extract_notes.py",
            "tests/grpc/test_bc.py",
            "tests/grpc/test_runtime.py",
        ],
    )
    print(f"control: {control['summary']} ({control['seconds']}s)")
    if control["returncode"] != 0:
        print(control["tail"])
        print("the control does not pass: no mutant is run")
        return 2
    for gid in args.ids:
        m = MUTANTS[gid]
        copy_tree(tree)
        why = apply(tree, m["edits"]) or builds(tree, m["edits"])
        row: dict[str, Any] = {
            "id": gid,
            "mutant": m["what"],
            "killer": m["killer"],
            "tests": " ".join(m["tests"]),
        }
        if why:
            row |= {"result": "INVALID", "why": why}
        else:
            r = run_tests(tree, args.scratch, gid.lower(), m["tests"])
            if r["returncode"] == 0:
                row |= {"result": "SURVIVED", **r}
            elif not r["failed"]:
                row |= {
                    "result": "INVALID",
                    "why": "no test failed but the run did not pass: " + r["tail"][-300:],
                    **r,
                }
            else:
                row |= {"result": "KILLED", "failed": r["failed"][:3], "seconds": r["seconds"]}
        rows.append(row)
        print(
            f"{row['result']:9} {gid:5} {m['what']}  {row.get('failed', [''])[0] if row.get('failed') else row.get('why', '')[:80]}"
        )
    killed = sum(r["result"] == "KILLED" for r in rows)
    print(f"{killed}/{len(rows)} killed (control passed)")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "mutants.json").write_text(
        json.dumps(
            {
                "schema": "lattice.grpc-mutants/1",
                "control": {k: control[k] for k in ("summary", "seconds")},
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
