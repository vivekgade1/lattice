"""Mutants of gRPC round 2 (docs/results/2026-10-17-grpc-round2-prereg.md, section 8): seventeen
deliberate faults in the round-2 code, each of which the probes or the named unit tests must catch.
The run works on a scratch copy of the worktree -- never on the worktree itself -- and the control
(the copy unmodified) runs every check first.

    uv run python dogfood/grpc_mutants2.py --scratch "$SCRATCH/m" --deskd "$SCRATCH/../heldout/deskd" \\
        --base "$SCRATCH/base" $(seq -f GR%g 1 17)

A *check* is a pytest selection of the copy, or one probe of ``grpc_probes2.py --check <name>`` run
against the copy (its library through ``PYTHONPATH``). A mutant is KILLED when any of its checks
gives a different verdict than the control's (a failing test, or a probe whose status changed), and
INVALID when the patch did not apply. Every listed check is run, so the table says which of them
(the pre-registration's named killer, or the unit tests that were also named) killed it.
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

GRPC = "lattice/spec/grpc.py"
WF = "lattice/importer/workflows.py"
SC = "dogfood/grpc_flow_scorer.py"
UNIT = ["pytest", "tests/grpc/test_reconcile2.py", "tests/grpc/test_flows2.py"]
CASES = ["probe", "p2-score"]
HTTP_WF = ["pytest", "tests/importer/test_workflows.py"]

# id -> what, edits [(file, old, new)], checks [(kind, ...)], the pre-registration's killer
MUTANTS: dict[str, dict[str, Any]] = {
    "GR1": {
        "what": "reconcile reuses one token (round 1's behaviour)",
        "edits": [
            (
                GRPC,
                "            token = runtime.auth.token(transport, first_user, login)\n",
                "            memo = _call_fresh.__dict__.setdefault('memo', {})\n"
                "            if id(transport) not in memo:\n"
                "                memo[id(transport)] = (transport, runtime.auth.token(transport, first_user, login))\n"
                "            token = memo[id(transport)][1]\n",
            )
        ],
        "checks": [["probe", "pr2-sess"], UNIT],
        "killer": "PR2-SESS, PR2-DESK",
    },
    "GR2": {
        "what": "a fresh token per service, not per call",
        "edits": [
            (
                GRPC,
                "            token = runtime.auth.token(transport, first_user, login)\n",
                "            memo = _call_fresh.__dict__.setdefault('memo', {})\n"
                "            if (id(transport), service) not in memo:\n"
                "                memo[(id(transport), service)] = (transport, runtime.auth.token(transport, first_user, login))\n"
                "            token = memo[(id(transport), service)][1]\n",
            )
        ],
        "checks": [["probe", "pr2-sess"], UNIT],
        "killer": "PR2-SESS",
    },
    "GR3": {
        "what": "the reverse pass is skipped (RC2 off)",
        "edits": [
            (
                GRPC,
                "                for key in sorted(by_key, reverse=True):\n"
                "                    s, m, pub = by_key[key]\n"
                "                    reverse[key] = _call_fresh(\n"
                "                        rpc, transport, runtime, first_user, login, s, m, pub\n"
                "                    )\n",
                "                reverse.update(forward)\n",
            )
        ],
        "checks": [["probe", "pr2-order"], UNIT],
        "killer": "PR2-ORDER (sessions), P2-UNIT",
    },
    "GR4": {
        "what": "a disagreement is reported without the isolated rerun (RC3 off)",
        "edits": [(GRPC, "    for key in disagreed:\n", "    for key in ():\n")],
        "checks": [["probe", "pr2-order"], UNIT],
        "killer": "PR2-ORDER, P2-UNIT",
    },
    "GR5": {
        "what": "a failed login is recorded as UNAUTHENTICATED answered:true",
        "edits": [
            (
                GRPC,
                'return {"code": None, "answered": None, "reason": "login_failed"}',
                'return {"code": "UNAUTHENTICATED", "answered": True}',
            )
        ],
        "checks": [UNIT],
        "killer": "P2-UNIT",
    },
    "GR6": {
        "what": "the contract's order instead of the sorted order in the forward pass",
        "edits": [
            (
                GRPC,
                "                for key in sorted(by_key):\n",
                "                for key in by_key:\n",
            )
        ],
        "checks": [UNIT],
        "killer": "P2-DET (order key in output), P2-UNIT",
    },
    "GR7": {
        "what": "F2 off: natural keys filled by example_value (round 1)",
        "edits": [
            (
                WF,
                "def natural_fields(view: RpcView) -> list[tuple[str, str, str]]:\n",
                "def natural_fields(view: RpcView) -> list[tuple[str, str, str]]:\n    return []\n\n\n"
                "def _natural_fields_off(view: RpcView) -> list[tuple[str, str, str]]:\n",
            )
        ],
        "checks": [["probe", "pf2-desk"], UNIT],
        "killer": "PF2-DESK",
    },
    "GR8": {
        "what": "F3 off: bool -> true (round 1)",
        "edits": [
            (
                WF,
                '    if t == "bool":\n        return False\n',
                '    if t == "bool":\n        return True\n',
            )
        ],
        "checks": [["probe", "pf2-desk"], UNIT],
        "killer": "PF2-DESK",
    },
    "GR9": {
        "what": "F1 binds an id to a field of another table",
        "edits": [
            (
                WF,
                '                    if f == "id" or f == f"{_singular(t)}_id" or f == f"{t}_id":\n',
                "                    if True:\n",
            )
        ],
        "checks": [["probe", "pf2-n"], UNIT],
        "killer": "P2-UNIT, PF2-N",
    },
    "GR10": {
        "what": "the lookup picks the first element, unsorted",
        "edits": [
            (WF, "                    return min(vals)\n", "                    return vals[0]\n")
        ],
        "checks": [UNIT],
        "killer": "P2-DET",
    },
    "GR11": {
        "what": "owner_guard / owner_chain verify on UNAUTHENTICATED",
        "edits": [
            (
                WF,
                'GUARD_REFUSALS = frozenset({"NOT_FOUND", "PERMISSION_DENIED"})',
                'GUARD_REFUSALS = frozenset({"NOT_FOUND", "PERMISSION_DENIED", "UNAUTHENTICATED"})',
            )
        ],
        "checks": [["probe", "pf2-desk"], UNIT],
        "killer": "P2-UNIT, PF2-DESK",
    },
    "GR12": {
        "what": "verified is set from the live run without the layer-D replay",
        "edits": [
            (
                GRPC,
                "        scenario = _scenario_of(plan.id, recorded)\n",
                '        flow.update(status="verified", reason=None)\n        return flow\n'
                "        scenario = _scenario_of(plan.id, recorded)\n",
            )
        ],
        "checks": [["probe", "pf2-replay"], UNIT],
        "killer": "PF2-REPLAY, P2-UNIT",
    },
    "GR13": {
        "what": "a fault code on a refuse step is counted as a refusal",
        "edits": [
            (
                WF,
                '    if code in RPC_FAULTS:\n        return False\n    if intent == "ok":\n',
                '    if intent == "ok" and code in RPC_FAULTS:\n        return False\n    if intent == "ok":\n',
            ),
            (
                WF,
                "        return code in (GUARD_REFUSALS if kind in GUARD_KINDS else RPC_REFUSALS)\n",
                "        return code in ((GUARD_REFUSALS if kind in GUARD_KINDS else RPC_REFUSALS) | RPC_FAULTS)\n",
            ),
        ],
        "checks": [UNIT, ["probe", "pf2-n"]],
        "killer": "P2-UNIT, PF2-N",
    },
    "GR14": {
        "what": "scorer: verified == 0 passes (round 1's rule)",
        "edits": [(SC, "    if len(verified) < min_verified:\n", "    if False:\n")],
        "checks": [CASES],
        "killer": "P2-SCORE",
    },
    "GR15": {
        "what": "scorer: an empty denominator scores 1.0",
        "edits": [
            (
                SC,
                '        out.update({"pass": False, "reason": "vacuous"})\n',
                '        out.update({"pass": True, "value": 1.0})\n',
            ),
            (
                SC,
                '            cover["reason"] = "vacuous"\n            cover["pass"] = False\n',
                '            cover["pass"] = True\n',
            ),
        ],
        "checks": [CASES],
        "killer": "P2-SCORE",
    },
    "GR16": {
        "what": "scorer: edge matching ignores the consumer's code",
        "edits": [
            (
                SC,
                'if step.get("rpc") != edge["consumer"] or step.get("code") != edge["code"]:',
                'if step.get("rpc") != edge["consumer"]:',
            )
        ],
        "checks": [CASES],
        "killer": "P2-SCORE",
    },
    "GR17": {
        "what": "the HTTP workflow path touched (the rpc helper's summary shape in the HTTP summary)",
        "edits": [
            (
                WF,
                '    count = {"verified": 0, "unverified": 0, "failed": 0}\n',
                '    count = {"verified": 0, "unverified": 0, "failed": 0, "unbound": 0}\n',
            )
        ],
        "checks": [["probe", "p2-bc-ast"], HTTP_WF],
        "killer": "P2-BC",
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
    for rel, old, new in edits:
        path = tree / rel
        text = path.read_text()
        if text.count(old) < 1:
            return f"{rel}: the text to replace is not there: {old[:70]!r}"
        path.write_text(text.replace(old, new, 1))
    return None


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
        cmd = [sys.executable, "-m", "pytest", *check[1:], "-q", "-p", "no:cacheprovider"]
    else:
        cmd = [
            sys.executable,
            str(tree / "dogfood" / "grpc_probes2.py"),
            "--check",
            check[1],
            "--scratch",
            str(args.scratch / f"s-{label}"),
            "--base",
            str(args.base),
            "--deskd",
            str(args.deskd),
        ]
    p = subprocess.run(
        cmd, cwd=tree, env=env, capture_output=True, text=True, check=False, timeout=3600
    )
    text = p.stdout + p.stderr
    return {
        "check": " ".join(check[1:]) if check[0] == "probe" else " ".join(check[1:]),
        "kind": check[0],
        "returncode": p.returncode,
        "seconds": round(time.perf_counter() - t0, 1),
        "failed": sorted(set(re.findall(r"^FAILED (\S+)", text, re.M)))[:4],
        "summary": (text.strip().splitlines() or [""])[-1][:200],
        "tail": text[-600:],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("ids", nargs="+")
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--base", type=Path, required=True)
    ap.add_argument("--deskd", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=REPO / "docs/results/data/2026-10-17-grpc-round2")
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
    copy_tree(tree)
    # the control: every distinct check on the unmodified copy
    distinct: dict[str, list[str]] = {}
    for gid in args.ids:
        for c in MUTANTS[gid]["checks"]:
            distinct.setdefault(" ".join(c), c)
    control: dict[str, dict[str, Any]] = {}
    for key, c in distinct.items():
        control[key] = run_check(tree, args, c, "control")
        print(
            f"control {key}: rc={control[key]['returncode']} {control[key]['summary'][:90]} ({control[key]['seconds']}s)"
        )
    rows: list[dict[str, Any]] = []
    for gid in args.ids:
        m = MUTANTS[gid]
        copy_tree(tree)
        why = apply(tree, m["edits"])
        row: dict[str, Any] = {"id": gid, "mutant": m["what"], "killer": m["killer"], "checks": []}
        if why:
            row |= {"result": "INVALID", "why": why}
        else:
            killers = []
            for c in m["checks"]:
                r = run_check(tree, args, c, gid.lower())
                base = control[" ".join(c)]
                differs = r["returncode"] != base["returncode"]
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
            f"{row['result']:9} {gid:5} {m['what'][:60]:60} {row.get('killed_by', row.get('why', ''))}"
        )
    killed = sum(r["result"] == "KILLED" for r in rows)
    print(
        f"{killed}/{len(rows)} killed (control checks: "
        + ", ".join(f"{k}={v['returncode']}" for k, v in control.items())
        + ")"
    )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "mutants2.json").write_text(
        json.dumps(
            {
                "schema": "lattice.grpc-mutants2/1",
                "control": {
                    k: {x: v[x] for x in ("returncode", "summary", "seconds")}
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
