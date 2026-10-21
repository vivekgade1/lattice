"""Probes of .NET parity, round 1 (docs/results/2026-10-18-dotnet-prereg.md, sections 5, 10, 11 and Amendments
1-2; Spike J, node DN1, runner node R): Arm 0 (the static fixture ``dnparity1``, determinism, the front end and
layer A rows that need only fixtures), Arm D (D1 = Streak in ASP.NET Core: extractor, front end, layer A, the
rules-first import, workflows and the consumers), Arm N (``dnapp``), Arm H (the held-out H1, blind), Arm M (the
MySQL rows, Docker) and Arm X (cross-arm byte compatibility, environment, the J0-dn matrix).

    "$PY" dogfood/dotnet_probes.py --arm 0,D,N,H,M,X --base "$SCRATCH/base" --d1 tests/spec/dnstreak \\
        --h1 tests/spec/<h1> --streak "$SCRATCH/streak" --freeze "$SCRATCH/dn.sha256" --runs 10 \\
        --out docs/results/data/2026-10-18-dotnet/scored

    "$PY" dogfood/dotnet_probes.py --self-test --base "$SCRATCH/base"
    "$PY" dogfood/dotnet_probes.py --write-freeze FILE [--extra PATH ...]    # a sha256sum-style list of what must be frozen

What cannot be switched off (prereg section 11, rules 2 and 3):

* the self-hash check, before any metric: every entry of ``--freeze`` is re-hashed, the running script, its mutants
  runner, every stand-in file, every library file of :data:`MUST_LIST` and every file of the nodes B2-B4 that exists
  in the tree must be listed (a ``scored``/``rerun``/``verify`` run needs all of them), a mismatch or a missing
  entry aborts. No flag skips or narrows it. :func:`rows_cli` (the mutants runner's entry in a *scratch copy* of the
  tree, which has no ``.git``) has no freeze check by design and refuses to run in the real worktree;
* the library under test lies inside the worktree (``lattice.__file__``, and the import subprocess's);
* the global run ledger ``docs/results/data/2026-10-18-dotnet/ledger.jsonl`` (a fixed path, not derived from
  ``--out``): a ``started`` and a ``completed`` or ``crash_*`` entry per arm and run; a ``scored`` run is refused for
  an arm that already has one; a ``rerun`` only after ``crash_before_metric`` with the same runner and freeze bytes.

A row is NOT RUN only when a named prerequisite is missing (:func:`missing`): no .NET SDK, no Docker (the rows of
Arm M, F-RECON and the observed workflow rows), no Streak or ``dnapp`` copy, no H1, no Go (PB-X, PB-G), a node
(B2, B3, B4) whose files are not in the tree, a D1 build whose NuGet restore failed. Everything else that raises is a
FAIL (round-2 Amendment 6 rule a). The row functions contain no NOT RUN of their own: :func:`one_row` is the only
producer of one, and ``--self-test`` fails (RM1) if a row is NOT RUN while :func:`missing` says nothing is missing.

Where a node's code does not exist yet (B2: the front end and the import; B3: layer A; B4: the workflow pass and the
consumer dispatch) the self-test runs the same row functions against the stand-ins of ``dogfood/dn_standin/``, which
implement the :class:`Lib` interface below; the real :class:`RealLib` methods are the only place that names a library
function of those nodes, so a mismatch found at the first shakedown after the merge is fixed there and nowhere else.
Nothing here edits a fixture, a target or a library file: extracts read the targets, builds go to the scratch, and
recording ``docker`` / ``claude`` / ``dotnet`` shims come first on ``PATH``.
"""  # noqa: E501

# ruff: noqa: E501
from __future__ import annotations

import argparse
import ast
import collections
import hashlib
import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import traceback
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

RUNNER_NAME = "dotnet_probes"
RUNNER_REL = "dogfood/dotnet_probes.py"
MUTANTS_REL = "dogfood/dotnet_mutants.py"
TEST_REL = "tests/dogfood/test_dotnet_runner.py"
STANDIN = HERE / "dn_standin"
DATA = REPO / "docs/results/data/2026-10-18-dotnet"
LEDGER = DATA / "ledger.jsonl"  # fixed here, never derived from --out
PREREG = REPO / "docs/results/2026-10-18-dotnet-prereg.md"
FIXTURE = REPO / "tests/dncheck/fixtures/dnparity1"
FIXTURE_TRUTH = FIXTURE / "truth.json"
D1_TGZ = DATA / "d1.tgz"
D1_TRUTH = REPO / "tests/dncheck/d1/truth.json"
D1_MUTANT_DIR = DATA / "d1-mutants"
BASE_REF = "7f2094f"  # the next-phases tip np-dotnet was rebased onto (G3 and gRPC round 5 merged)
ARMS = ("0", "D", "N", "H", "M", "X")
KINDS = ("shakedown", "scored", "rerun", "verify", "mutants")
SCORED_KINDS = ("scored", "rerun", "verify")
NODES = ("B2", "B3", "B4")
VERIFIED = ("verified", "verified (corrected)")
Q9_YES = False  # Amendment 1 Q9/D2: DN805 is built only if G3's K8 passed; K8 was refuted (go-parity-round3-tier2-results.md), B3 gated DN805 with the reason, so A-805 is reported, not gated (93 gated rows)
Q8_YES = True  # Amendment 1 Q8: dnapp is built (the N rows are gated)
DOTNET_ENV = {
    "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
    "DOTNET_NOLOGO": "1",
    "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
}

# ---- what the freeze file must list -----------------------------------------------------------------------------

SRC_CS = (
    "Program",
    "Load",
    "Model",
    "Json",
    "Routes",
    "Auth",
    "Binding",
    "Ef",
    "Owner",
    "Raises",
    "State",
    "Closure",
    "Required",
    "Projection",
    "Assemble",
)
ALWAYS = (  # the runners, their test, the stand-ins' anchors, the truths, the corpus archives and the library of node B1
    RUNNER_REL,
    MUTANTS_REL,
    TEST_REL,
    "docs/results/2026-10-18-dotnet-prereg.md",
    "docs/results/data/2026-10-18-dotnet/HARNESS_API_DOTNET.md",
    "docs/results/data/2026-10-18-dotnet/TRUTH_SCHEMA_DOTNET.md",
    "docs/results/data/2026-10-18-dotnet/freeze-arm0.sha256",
    "docs/results/data/2026-10-18-dotnet/freeze-d1.sha256",
    "docs/results/data/2026-10-18-dotnet/d1.tgz",
    "docs/results/data/2026-10-18-dotnet/d1-mutants/MANIFEST.sha256",
    "docs/results/data/2026-10-18-dotnet/d1-mutants/MUTANTS.md",
    *[f"docs/results/data/2026-10-18-dotnet/d1-mutants/dm{i}.diff" for i in range(1, 7)],
    "tests/dncheck/d1/truth.json",
    "tests/dncheck/fixtures/dnparity1/truth.json",
    "tests/dncheck/test_dnextract.py",
    "tests/dncheck/test_dnextract_idioms.py",
    "lattice/dncheck/__init__.py",
    "lattice/dncheck/src/DnCheck.csproj",
    "lattice/dncheck/src/packages.lock.json",
    *[f"lattice/dncheck/src/{n}.cs" for n in SRC_CS],
)
NODE_FILES: dict[
    str, tuple[str, ...]
] = {  # prereg section 9: what each node edits or creates (listed iff present; all of them for a scored run)
    "B2": (
        "lattice/importer/frontends/dotnet_aspnet.py",
        "lattice/importer/dotnet_rules.py",
        "lattice/importer/frontends/__init__.py",
        "lattice/importer/rules.py",
        "tests/importer/test_dotnet_aspnet.py",
        "tests/importer/test_dotnet_rules.py",
        "tests/importer/test_candidate_item_ids.py",
    ),
    "B3": (
        "lattice/dncheck/policy.py",
        "lattice/dncheck/derive.py",
        *[
            f"lattice/dncheck/src/Check{n}.cs"
            for n in ("Owner", "Private", "Bounds", "Errors", "Datetime", "Partial", "Machine")
        ],
        "lattice/spec/check.py",
        "lattice/spec/__main__.py",
        "lattice/importer/check_invocation.py",
        "tests/dncheck/test_dncheck_layer_a.py",
        "tests/dncheck/test_dn_derive.py",
        "tests/dncheck/fixtures/dnparity1bad/truth.json",
    ),
    "B4": (
        "lattice/dncheck/src/Workflow.cs",
        "lattice/importer/extract_source.py",
        "lattice/importer/workflow_run.py",
        "lattice/importer/inventory.py",
        "lattice/spec/implementation.py",
        "lattice/spec/impact_tier3.py",
        "tests/importer/test_dotnet_workflows.py",
        "tests/spec/test_dotnet_consumers.py",
        "tests/spec/dn_harness_rb.py",
    ),
}
NODE_PRESENCE: dict[
    str, tuple[str, ...]
] = {  # the files whose absence means the node is not merged (the interface modules the rows call)
    "B2": ("lattice/importer/frontends/dotnet_aspnet.py", "lattice/importer/dotnet_rules.py"),
    "B3": ("lattice/dncheck/policy.py", "lattice/dncheck/derive.py"),
    "B4": ("lattice/importer/extract_source.py", "lattice/dncheck/src/Workflow.cs"),
}


def standin_files() -> list[str]:
    return sorted(
        p.relative_to(REPO).as_posix()
        for p in STANDIN.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    )


def node_present(node: str, repo: Path = REPO) -> bool:
    return all((repo / rel).is_file() for rel in NODE_PRESENCE[node])


def must_list(kind: str, repo: Path = REPO) -> list[str]:
    """Every path (relative to the repository) the freeze file must list for a run of ``kind``: the fixed set, every
    stand-in file, and the files of each node that exist in the tree (a scored, rerun or verify run needs all of them)."""
    out = [*ALWAYS, *standin_files()]
    for files in NODE_FILES.values():
        out += [f for f in files if kind in SCORED_KINDS or (repo / f).is_file()]
    return list(dict.fromkeys(out))


# ---- the rows: id -> (arm, gated, prediction, nodes whose code the row calls, other prerequisites) ------------------------------

P: dict[str, tuple[str, bool, str, tuple[str, ...], tuple[str, ...]]] = {
    # Stage 1 (5.2): Arm 0 on dnparity1, determinism, D1 (B1 only)
    "P0-ROUTES": ("0", True, "exact", (), ("dotnet",)),
    "P0-AUTH": ("0", True, "exact (Public/Member/Admin; the admin route Admin)", (), ("dotnet",)),
    "P0-EXCL": ("0", True, "grpc, hub, health excluded; m6, x2 unread; nothing else", (), ("dotnet",)),
    "P0-BIND": ("0", True, "exact; the minimal-API copy has no bounds and one 'declared, not enforced' unread per annotation", (), ("dotnet",)),
    "P0-TABLES": ("0", True, "exact; TPH and owned type unread", (), ("dotnet",)),
    "P0-OWNER": ("0", True, "exact (owner_uses, query_filters, owner_via evidence)", (), ("dotnet",)),
    "P0-STATE": ("0", True, "exact (status_writes with guards, status_facts)", (), ("dotnet",)),
    "P0-RAISE": ("0", True, "exact; the unmapped throw unread", (), ("dotnet",)),
    "P0-DI": ("0", True, "exact; the two-registration call unread", (), ("dotnet",)),
    "P0-JSON": ("0", True, "SnakeCaseLower; every wire name exact", (), ("dotnet",)),
    "DET-10": ("0", True, "10/10 byte-identical on dnparity1 and on D1", (), ("dotnet",)),
    "DET-RESTORED": ("D", True, "identical (a restored and built D1 copy vs a clean copy)", (), ("dotnet", "d1_build")),
    "DET-PATH": ("D", True, "identical (two other absolute paths, and LANG=de_DE.UTF-8)", (), ("dotnet",)),
    "D-ROUTES": ("D", True, "recall 1.00, 0 extra", (), ("dotnet",)),
    "D-AUTH": ("D", True, "exact on 26/26 (p 0.8); gate >= 0.95", (), ("dotnet",)),
    "D-TABLES": ("D", True, "tables 9/9; column facts >= 0.95", (), ("dotnet",)),
    "D-WIRE": ("D", True, "1.00; gate >= 0.98", (), ("dotnet",)),
    "D-OWN": ("D", True, "0.85-1.00; gate >= 0.85 and 0 evidence on a table truth says is not owned", (), ("dotnet",)),
    "D-RAISE": ("D", True, "precision >= 0.95 (p 0.95-1.00), recall >= 0.70 (p 0.75-0.95)", (), ("dotnet",)),
    "D-UNREAD": ("D", True, "0 silent drops", (), ("dotnet",)),
    "D-X": ("D", True, "2-8 s; gate <= 60 s", (), ("dotnet",)),
    # Stage 2 (5.3): the front end (B2)
    "F-DETECT": ("0", True, "aspnetcore, aspnetcore, LookupError, LookupError with the note, unchanged answers for the others", ("B2",), ("dotnet", "base")),
    "F-MODEL": ("D", True, "counts equal the extract's; login path, fields and token field exact vs truth; aspnetcore/efcore", ("B2",), ("dotnet",)),
    "F-RT": ("D", True, "runtime() with the harness and without it (process_runtime(framework='dotnet'))", ("B2",), ("dotnet",)),
    "F-RECON": ("D", True, "every extracted route observed (!= 404/405); control 404; 0 leftover containers", ("B2",), ("dotnet", "docker", "d1_build", "driver")),
    "PB-LAZY": ("0", True, "dotnet_aspnet not in sys.modules after a FastAPI, a Flask and a Go import; the literal pool unchanged", ("B2",), ("base",)),
    # Stage 3 (5.4): layer A (B3)
    "A0-DN": ("0", True, "DN801 on o3 and the IgnoreQueryFilters route; DN802 on the projection; DN803 on the unenforced copy; DN806, DN807, DN808 on the first cases; DN800 on dnparity1bad", ("B3",), ("dotnet",)),
    "A0-GATE": ("0", True, "DN804 (and DN805 unless Q9) gated with their texts", ("B3",), ()),
    "A-D1": ("D", True, "0 findings under DnPolicy.from_spec(Streak SPEC) and under dn_spec_policy", ("B3",), ("dotnet", "streak")),
    "A-JUDGE-D1": ("D", False, "0.70-0.95 of D1's owner-scoped routes judged (reported)", ("B3",), ("dotnet",)),
    "A-DM": ("D", True, "D1 dev mutants DM1-DM4 each killed by its registered detector (4/4)", ("B2", "B3"), ("dotnet", "d1_build")),
    "A-CHECK": ("D", True, "layer A runs through _layer_a_dotnet: 0 findings; without the SDK on PATH a note", ("B3",), ("dotnet", "streak")),
    "A-DERIVE": ("D", True, "derive_dn_policy: owners and private exactly the truth's on dnparity1 and D1; baseline empty on D1", ("B3",), ("dotnet",)),
    "A-805": ("D", Q9_YES, "dnparity1 404; D1 404 or unclear; a D1 copy answering 403 on a scoped miss gives DN805", ("B3",), ("dotnet",)),
    # Stage 4 (5.5): the rules-first import (B2); the dnapp rows (B2, B4)
    "I-D1-1": ("D", True, "aspnetcore; 26 or 27 routes and contracts; 9 tables", ("B2",), ("dotnet", "d1_build")),
    "I-D1-2": ("D", True, "inferences: owner_uses per truth owner table, json_ignore per private column, enum_choices Nudge.delivery, route_constraint, bind_status, json_naming", ("B2",), ("dotnet", "d1_build")),
    "I-D1-3": ("D", True, "60-110 items; verified share >= 0.50; every refuted item corrected or baselined; every unexercised item with a reason", ("B2",), ("dotnet", "d1_build")),
    "I-D1-4": ("D", True, ">= 0.95 retained on variant2", ("B2",), ("dotnet", "d1_build")),
    "I-D1-5": ("D", False, "proposals include the machine Nudge.delivery (p 0.6; reported)", ("B2",), ("dotnet", "d1_build")),
    "I-D1-6": ("D", True, "review list exit 0; ids = the not-verified ids", ("B2",), ("dotnet", "d1_build")),
    "I-D1-7": ("D", True, "the import 10x identical after the timing normaliser", ("B2",), ("dotnet", "d1_build")),
    "I-D1-PR3": ("D", True, "layer R with the imported candidate_spec.py: the same verdict map after the correction map", ("B2",), ("dotnet", "d1_build")),
    "I-D1-T2": ("D", True, "T2 items of the private columns present, keyed by wire name, verified", ("B2",), ("dotnet", "d1_build")),
    "I-D1-M": ("D", True, "MySQL studio.toml: rules.status not_run:server_dialect, dialect mysql, 26 or 27 contracts, 0 rule items, 0 docker calls, no SQLite file", ("B2",), ("dotnet",)),
    "N-R1": ("N", Q8_YES, "layer R on dnapp: refuted 0, verified share within 0.10 of Go S1's 0.6957", ("B2", "B4"), ("dotnet", "dnapp")),
    "N-R2": ("N", Q8_YES, "R on the DNAPP_BUG copies: each refutes >= 1 item on GET /runs/{rid}", ("B2", "B4"), ("dotnet", "dnapp")),
    "N-B1": ("N", Q8_YES, "layer B on dnapp: 0 findings safe, >= 1 on the bugged route each", ("B2", "B4"), ("dotnet", "dnapp")),
    "N-10": ("N", Q8_YES, "R and B 10x identical", ("B2", "B4"), ("dotnet", "dnapp")),
    # Stage 5 (5.6): the workflow layer and the consumers (B4, with B2's dispatch)
    "W-D1-1": ("D", True, ">= 3 of the 4 Streak flows and the Nudge.delivery machine, static then observed on MySQL", ("B4",), ("dotnet", "docker", "d1_build", "driver")),
    "W-D1-2": ("D", True, "0 flows marked verified fail on replay", ("B4",), ("dotnet", "docker", "d1_build", "driver")),
    "W-D1-3": ("D", True, "workflows --static 10x identical", ("B4",), ("dotnet",)),
    "W-D1-4": ("D", True, "0 leftover containers", ("B4",), ("dotnet", "docker")),
    "C-INV": ("D", True, "frontend dotnet_aspnet; route and table counts equal the extract; source set .cs/.csproj/... minus bin/obj", ("B4",), ("dotnet",)),
    "C-IMPL": ("D", True, "26 required rules' routes implemented; 0 required-not-implemented; health implemented-not-required", ("B4",), ("dotnet", "streak")),
    "C-IMPLM": ("D", True, "one route removed from the spec: exactly 1 implemented-not-required; a D1 copy missing two routes: exactly 2 required-not-implemented", ("B4",), ("dotnet", "streak")),
    "C-CITE": ("D", True, "every code:<path>:<line> citation resolves (file, line, quoted text): 100%", ("B4",), ("dotnet", "streak")),
    "C-ASK": ("D", True, "digest has an impl: section; the three fixed questions answer not_implemented, implemented, implemented with the auth", ("B4",), ("dotnet", "streak")),
    "C-MCP": ("D", True, "implementation_get, workflow_get, workflow_find('check in'): non-empty, .cs citations", ("B4",), ("dotnet", "streak")),
    "C-MAP": ("D", True, "app map: state not inventory_missing; rules, workflows, machine and access cells present", ("B4",), ("dotnet", "streak")),
    "C-IMP": ("D", True, "impact T3 on the first 5 changes: every change lists >= 1 code:*.cs:<line>, all resolvable (5/5)", ("B4",), ("dotnet", "streak", "changes")),
    "C-PERF": ("D", True, "lattice perf on D1 over MySQL runs; statement counts reported", ("B4",), ("dotnet", "docker", "d1_build", "driver", "streak")),
    "C-LIVE": ("D", True, "lattice contracts live on D1 over MySQL: runs, 0 contract violations", ("B4",), ("dotnet", "docker", "d1_build", "driver", "streak")),
    "C-IMPROVE": ("D", True, "lattice improve report over a D1 check's event log: runs, byte-identical 2x", ("B4",), ("dotnet", "streak")),
    "C-REBUILD": ("D", True, "the Studio rebuild loop with a scripted agent and the gate dotnet build && ./check.sh: gate runs, rounds recorded", ("B4",), ("dotnet", "d1_build", "streak")),
    # Arm H (5.7): the held-out H1, blind
    "H-ROUTES": ("H", True, "recall >= 0.90 and 0 extra routes", (), ("dotnet", "h1")),
    "H-AUTH": ("H", True, "auth exact on >= 0.85 of the matched routes", (), ("dotnet", "h1")),
    "H-TABLES": ("H", True, "table recall >= 0.90 and column-fact accuracy >= 0.90", (), ("dotnet", "h1")),
    "H-WIRE": ("H", True, "wire names >= 0.95", (), ("dotnet", "h1")),
    "H-OWN": ("H", True, "0 confident-wrong owners (every truth owner inferred or unclear naming it)", ("B2",), ("dotnet", "h1", "h1_run")),
    "H-BOUNDS": ("H", True, "precision 1.00 (recall reported)", (), ("dotnet", "h1")),
    "H-RAISE": ("H", True, "precision >= 0.95 and recall >= 0.60", (), ("dotnet", "h1")),
    "H-UNREAD": ("H", True, "0 silent drops", (), ("dotnet", "h1")),
    "H-PI3": ("H", True, "verified share >= 0.50; every refuted item corrected or baselined; every unexercised item with a reason", ("B2",), ("dotnet", "h1", "h1_run")),
    "H-PI4": ("H", True, ">= 0.95 retained on variant2", ("B2",), ("dotnet", "h1", "h1_run")),
    "H-PI5": ("H", False, "the truth machine's first edge proposed (p 0.6; reported)", ("B2",), ("dotnet", "h1", "h1_run")),
    "H-PI7": ("H", True, "the import 10x identical", ("B2",), ("dotnet", "h1", "h1_run")),
    "H-PR3": ("H", True, "R with the imported spec: the same verdict map after the correction map", ("B2",), ("dotnet", "h1", "h1_run")),
    "H-PA1": ("H", True, "derive_dn_policy: private within truth private, owners within truth owners", ("B3",), ("dotnet", "h1")),
    "H-PA0": ("H", True, "layer A on the original under dn_spec_policy of its candidate spec: 0 findings outside the baseline", ("B2", "B3"), ("dotnet", "h1", "h1_run")),
    "H-JUDGE": ("H", True, "share of the truth's owner-scoped routes judged by DN801 >= 0.75", ("B3",), ("dotnet", "h1")),
    "H-WF": ("H", True, ">= 1 multi-step flow verified; 0 verified flows fail on replay", ("B2", "B4"), ("dotnet", "h1", "h1_run")),
    "H-IMPL": ("H", True, "implementation report with the imported spec: 0 missing/extra route rows", ("B2", "B4"), ("dotnet", "h1", "h1_run")),
    "H-DET": ("H", True, "extract and layer A 10x identical", ("B3",), ("dotnet", "h1")),
    "H-X": ("H", True, "import wall <= 240 s", ("B2",), ("dotnet", "h1", "h1_run")),
    # Arm M (5.8): MySQL, Docker, the owner's machine
    "M-R1": ("M", True, "lattice check --layers ACDX --database mysql on D1: exit 0, layer A ran (0 findings), scenario pass count equal to the contract check's", ("B3",), ("dotnet", "docker", "d1_build", "driver", "streak")),
    "M-R2": ("M", True, "the same 10x (layers A, C, D): identical after the normaliser", ("B3",), ("dotnet", "docker", "d1_build", "driver", "streak")),
    # cross-arm (5.9) and the matrix (section 7)
    "PW": ("X", True, "0 snapshots changed; 0 docker/claude shim calls outside the rows naming them; 0 dotnet calls in the Python and Go rows", (), ()),
    "PB-F": ("X", True, "section 10's frozen table and the existing tests/fixtures identical to the base", (), ("base",)),
    "PB-X": ("X", True, "default gocheck extract identical to the base on the round-1 dirs, goapp, goginapp, gotickets, fixtures/runs", (), ("base", "go")),
    "PB-G": ("X", True, "Go layer A, workflows, inventory, implementation, app map, impact T3, ask digests identical to the base", (), ("base", "go")),
    "PB-P": ("X", True, "Python rules imports (J1 normaliser), derive_policy, pycheck, front-end goldens identical to the base", (), ("base",)),
    "PB-N": ("X", True, "stack-neutral outputs identical to the base", (), ("base",)),
    "PB-H": ("X", True, "help texts and MCP tools/list identical except check --help gaining dotnet", (), ("base",)),
    "PB-NODN": ("X", True, "dotnet off PATH: Python and Go outputs identical; a .NET import fails with DotnetToolchainMissing; layer A on a .NET runtime is a note", ("B2", "B3"), ("base",)),
    "PB-T": ("X", True, "the base's failing, skipped and error ids plus the new tests passing", (), ("base",)),
    "PB-D": ("X", True, "dogfood/studio/check.sh passes with the same step count", (), ()),
    "P-J0": ("X", True, "100% of the J0-dn cells cite an existing file:line and a test or results row", (), ("j0",)),
}  # fmt: skip
ROWS_OF = {a: [i for i, v in P.items() if v[0] == a] for a in ARMS}
GATED = [i for i, v in P.items() if v[1]]
H_STATIC = (
    "H-ROUTES",
    "H-AUTH",
    "H-TABLES",
    "H-WIRE",
    "H-BOUNDS",
    "H-RAISE",
    "H-UNREAD",
    "H-PA1",
    "H-JUDGE",
    "H-DET",
)


def result(
    pid: str, probe: str, predicted: Any, observed: Any, passed: bool | None, **ev: Any
) -> dict[str, Any]:
    return {
        "id": pid,
        "probe": probe,
        "ran": passed is not None,
        "predicted": predicted,
        "observed": observed,
        "pass": passed,
        "evidence": ev,
    }


def not_run(pid: str, probe: str, why: str, predicted: Any = None) -> dict[str, Any]:
    return {
        "id": pid,
        "probe": probe,
        "ran": False,
        "predicted": predicted,
        "observed": why,
        "pass": None,
        "evidence": {},
    }


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def run(
    cmd: Sequence[str],
    *,
    cwd: Path | str = REPO,
    env: Mapping[str, str] | None = None,
    timeout: float = 900,
    stdin: bytes | None = None,
) -> tuple[int, str, str, float]:
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=str(cwd),
            env=dict(env or os.environ),
            input=stdin,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as err:
        return 124, "", f"timed out after {timeout}s: {err}", time.perf_counter() - t0
    return (
        proc.returncode,
        proc.stdout.decode(errors="replace"),
        proc.stderr.decode(errors="replace"),
        time.perf_counter() - t0,
    )


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---- the self-hash check (never disabled) ----------------------------------------------------------------------------------


class FreezeError(SystemExit):
    """A freeze check failed: the run aborts before any metric."""


def read_entries(path: Path) -> list[tuple[str, str]]:
    if not path.is_file():
        raise FreezeError(f"freeze check failed: {path} does not exist")
    out = []
    for ln in path.read_text().splitlines():
        if ln.strip():
            digest, rel = ln.split(None, 1)
            out.append((digest, rel.strip()))
    return out


def verify_listed(
    path: Path,
    *,
    root: Path,
    must: Sequence[str],
    this: Path | None = None,
    required_abs: Sequence[Path] = (),
) -> dict[str, Any]:
    """Every entry of a sha256sum-style file is re-hashed (relative paths under ``root``, absolute ones as given); a
    mismatch, an entry whose file does not exist, a path of ``must`` that is not listed, a path of ``required_abs``
    that is not listed, or ``this`` (the running script) not being listed aborts. Nothing can be excluded."""
    entries = read_entries(path)
    problems: list[str] = []
    bad = []
    listed: set[Path] = set()
    for digest, rel in entries:
        f = Path(rel) if Path(rel).is_absolute() else root / rel
        listed.add(f.resolve())
        if not f.is_file() or sha(f) != digest:
            bad.append(rel)
    missing_ = [m for m in must if (root / m).resolve() not in listed]
    absent = [str(p) for p in required_abs if p.resolve() not in listed]
    if bad:
        problems.append(f"mismatch {bad[:8]}{' ...' if len(bad) > 8 else ''} ({len(bad)})")
    if missing_:
        problems.append(
            f"not listed {missing_[:8]}{' ...' if len(missing_) > 8 else ''} ({len(missing_)})"
        )
    if absent:
        problems.append(f"not listed {absent[:6]} ({len(absent)} files)")
    if this is not None and this.resolve() not in listed:
        problems.append("running script not listed")
    if problems:
        raise FreezeError(f"freeze check failed ({path.name}): " + "; ".join(problems))
    return {
        "file": str(path),
        "list_sha256": sha(path),
        "entries": len(entries),
        "mismatches": 0,
        "must_listed": len(must),
        "running_script_listed": this is not None,
    }


def verify_freeze(
    path: Path,
    this: Path,
    kind: str = "shakedown",
    *,
    root: Path = REPO,
    required_abs: Sequence[Path] = (),
) -> dict[str, Any]:
    return verify_listed(
        path, root=root, must=must_list(kind, root), this=this, required_abs=required_abs
    )


def write_freeze(path: Path, extra: Sequence[Path] = (), kind: str = "scored") -> int:
    """A sha256sum-style list of every :func:`must_list` path that exists plus ``extra`` (absolute paths). A utility for the
    orchestrator and for shakedowns: the verification above is unchanged by it."""
    lines = []
    for rel in must_list(kind):
        f = REPO / rel
        if f.is_file():
            lines.append(f"{sha(f)}  {rel}")
    for f in extra:
        lines.append(f"{sha(f)}  {f.resolve()}")
    path.write_text("\n".join(lines) + "\n")
    return len(lines)


def lattice_file_inside(repo: Path = REPO) -> str:
    """``lattice.__file__`` if it lies under ``repo``; else abort (the shared venv must not import the main checkout)."""
    import lattice

    f = Path(lattice.__file__).resolve()
    if not f.is_relative_to(repo.resolve()):
        raise SystemExit(
            f"refusing to run: lattice.__file__ = {f} is not inside the worktree {repo} (set PYTHONPATH to the worktree)"
        )
    return str(f)


# ---- the global run ledger --------------------------------------------------------------------------------------------------


def read_ledger(path: Path | None = None) -> list[dict[str, Any]]:
    path = (
        path or LEDGER
    )  # looked up at call time (a test may point it elsewhere; a run never does)
    if not path.is_file():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def append_ledger(entry: Mapping[str, Any], path: Path | None = None) -> None:
    path = path or LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(dict(entry), sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def runs_of(entries: Sequence[Mapping[str, Any]], arm: str, kind: str) -> list[dict[str, Any]]:
    """The runs of ``arm`` and ``kind`` in order: ``{run_id, start, status}`` (the last entry of the run gives the status)."""
    order: list[str] = []
    by: dict[str, dict[str, Any]] = {}
    for e in entries:
        if e.get("arm") != arm or e.get("kind") != kind:
            continue
        rid = e.get("run_id") or f"legacy-{len(order)}"
        if rid not in by:
            by[rid] = {"run_id": rid, "start": dict(e), "status": e.get("status")}
            order.append(rid)
        else:
            by[rid]["status"] = e.get("status")
    return [by[r] for r in order]


def ledger_refusal(
    entries: Sequence[Mapping[str, Any]], arm: str, kind: str, runner_sha: str, freeze_sha: str
) -> str | None:
    """None when a run of ``kind`` for ``arm`` may start, else why not. ``shakedown`` and ``mutants`` runs are recorded and
    never refused; ``scored`` once per arm; ``rerun`` once, only after ``crash_before_metric`` with the same bytes;
    ``verify`` once, after a completed scored (or rerun) run."""
    if kind not in KINDS:
        return f"unknown kind {kind!r}"
    if kind in ("shakedown", "mutants"):
        return None
    scored = runs_of(entries, arm, "scored")
    if kind == "scored":
        if scored:
            return f"arm {arm} already has a scored run ({scored[-1]['status']}); a crash_before_metric is repeated with --kind rerun, once"
        return None
    reruns = runs_of(entries, arm, "rerun")
    if kind == "rerun":
        if not scored:
            return f"arm {arm} has no scored run to repeat"
        if scored[-1]["status"] != "crash_before_metric":
            return f"the scored run ended {scored[-1]['status']}, not crash_before_metric"
        if reruns:
            return f"arm {arm} already had its one rerun ({reruns[-1]['status']})"
        prev = scored[-1]["start"]
        if prev.get("runner_sha256") != runner_sha or prev.get("freeze_sha256") != freeze_sha:
            return "the frozen bytes (runner or freeze file) differ from the scored run's"
        return None
    if runs_of(entries, arm, "verify"):
        return f"arm {arm} already had its one verify run"
    done = [r for r in [*scored, *reruns] if r["status"] == "completed"]
    if not done:
        return f"arm {arm} has no completed scored run to verify"
    if done[-1]["start"].get("runner_sha256") != runner_sha:
        return "the runner bytes differ from the scored run's"
    return None


# ---- prerequisites ------------------------------------------------------------------------------------------------------------


def docker_present(path: str | None = None) -> bool:
    """A real docker client that answers (the recording shim directory is not on this PATH)."""
    exe = shutil.which(
        "docker",
        path=path
        if path is not None
        else os.environ.get("DN_REAL_PATH", os.environ.get("PATH", "")),
    )
    if exe is None:
        return False
    try:
        return (
            subprocess.run([exe, "info"], capture_output=True, timeout=30, check=False).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


def driver_has_pymysql(py: str) -> bool:
    try:
        return (
            subprocess.run(
                [py, "-c", "import pymysql,bcrypt"], capture_output=True, timeout=60, check=False
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


def real_path() -> str:
    """PATH without the recording shim directory (for the rows that name Docker or ``dotnet`` as a measured tool)."""
    return os.environ.get("DN_REAL_PATH", os.environ.get("PATH", ""))


# ---- the context ----------------------------------------------------------------------------------------------------------------

SNAPSHOT_ROOTS = ["tests/dncheck/fixtures", "tests/dncheck/d1", "lattice/dncheck/src"]
NO_DOTNET_ROWS = (
    "PB-G",
    "PB-P",
    "PB-X",
)  # rows that name no dotnet call: a dotnet shim call logged while they ran is a PW finding


class Ctx:
    def __init__(self, scratch: Path, out: Path, kind: str = "shakedown") -> None:
        self.scratch, self.out, self.kind = scratch, out, kind
        self.py = sys.executable
        self.shims = scratch / "shims"
        self.shim_log = scratch / "shim.log"
        self.shim_row = scratch / "shim.row"
        self.base_dir: Path | None = None
        self.runs = 10
        self.owner = "dn-r"
        self.no_docker = False
        self.docker = False
        self.driver_py = sys.executable
        self.driver_ok = False
        self.streak_src: Path | None = None
        self.dnapp_src: Path | None = None
        self.j0_path: Path | None = None
        self.changes_dir: Path | None = None
        self.snap_roots: list[str] = []
        self.d1: dict[str, Any] | None = None
        self.h_target: dict[str, Any] | None = None
        self.standin: set[str] = (
            set()
        )  # the nodes whose Lib methods are the stand-ins (self-test only)
        self.lib: Lib = RealLib()
        self.timings: dict[str, float] = {}
        self.cache: dict[str, Any] = {}
        self.snap0: dict[str, Any] = {}
        self.extra_snaps: dict[str, Any] = {}
        self.shim_mark: int | None = None
        self.pb_t_targets: Sequence[str] = ()
        self.self_test = False
        self.dotnet = shutil.which("dotnet", path=real_path()) is not None

    # -- environment --------------------------------------------------------------------------------------------------------------
    def pythonpath(self) -> str:
        roots = [
            str(t["overlay"])
            for t in self.targets()
            if t.get("overlay") and Path(t["overlay"]).resolve() != REPO.resolve()
        ]
        return os.pathsep.join([*dict.fromkeys(roots), str(REPO)])

    def targets(self) -> list[dict[str, Any]]:
        return [t for t in (self.d1, self.h_target) if t]

    def env(self, **extra: str) -> dict[str, str]:
        env = dict(os.environ)
        env.update(DOTNET_ENV)
        env.update(
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONPATH=self.pythonpath(),
            LATTICE_SHIM_LOG=str(self.shim_log),
            LATTICE_SHIM_ROW=str(self.shim_row),
            LATTICE_DNCHECK_CACHE=str(self.scratch / "dncheck-cache"),
            LATTICE_CONTAINER_OWNER=self.owner,
        )
        for t in self.targets():
            if t.get("built") and t.get("build_env"):
                env[t["build_env"]] = str(t["built"])
        env.update(extra)
        return env

    def env_real(self, **extra: str) -> dict[str, str]:
        """``env`` with the real PATH (no recording shims): for the rows that run Docker or ``dotnet`` as the thing measured."""
        return self.env(PATH=real_path(), **extra)

    def set_row(self, pid: str) -> None:
        self.shim_row.write_text(pid)


def make_shims(ctx: Ctx) -> None:
    """Recording ``docker`` / ``claude`` shims (exit 127) and a recording pass-through ``dotnet``, first on ``PATH``."""
    ctx.scratch.mkdir(parents=True, exist_ok=True)
    ctx.shims.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DN_REAL_PATH", os.environ["PATH"])
    real_dotnet = shutil.which("dotnet", path=os.environ["DN_REAL_PATH"]) or ""
    for name in ("docker", "claude"):
        p = ctx.shims / name
        p.write_text(
            f'#!/bin/sh\necho "$(cat "$LATTICE_SHIM_ROW" 2>/dev/null) {name} $*" >> "$LATTICE_SHIM_LOG"\nexit 127\n'
        )
        p.chmod(0o755)
    d = ctx.shims / "dotnet"
    d.write_text(
        f'#!/bin/sh\necho "$(cat "$LATTICE_SHIM_ROW" 2>/dev/null) dotnet $1" >> "$LATTICE_SHIM_LOG"\nexec "{real_dotnet}" "$@"\n'
    )
    d.chmod(0o755)
    ctx.shim_log.write_text("")
    ctx.shim_row.write_text("setup")
    os.environ["PATH"] = f"{ctx.shims}{os.pathsep}{os.environ['PATH']}"
    os.environ["LATTICE_SHIM_LOG"] = str(ctx.shim_log)
    os.environ["LATTICE_SHIM_ROW"] = str(ctx.shim_row)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    for k, v in DOTNET_ENV.items():
        os.environ.setdefault(k, v)


def path_without_dotnet(ctx: Ctx) -> str:
    """PATH with no ``dotnet`` (and no recording shim for it) but everything else the directories that held it also hold (a directory of symlinks)."""
    keep = ctx.scratch / "nodn-bin"
    shutil.rmtree(keep, ignore_errors=True)
    keep.mkdir(parents=True)
    dirs = []
    for d in os.environ["PATH"].split(os.pathsep):
        if not d or Path(d) == ctx.shims:
            continue
        if (Path(d) / "dotnet").exists() and Path(d).is_dir():
            for p in Path(d).iterdir():
                if p.name != "dotnet" and not (keep / p.name).exists():
                    (keep / p.name).symlink_to(p)
        else:
            dirs.append(d)
    return os.pathsep.join([str(keep), *dirs])


def shim_calls(ctx: Ctx) -> list[str]:
    return (
        [ln for ln in ctx.shim_log.read_text().splitlines() if ln.strip()]
        if ctx.shim_log.exists()
        else []
    )


def snapshot(root: Path) -> dict[str, Any]:
    """sha256, mode, mtime and the listing (hidden files and directories included)."""
    out: dict[str, Any] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        st = p.lstat()
        if p.is_symlink():
            out[rel] = ["symlink", os.readlink(p), st.st_mode]
        elif p.is_dir():
            out[rel] = ["dir", st.st_mode, st.st_mtime_ns]
        else:
            out[rel] = [sha(p), st.st_mode, st.st_mtime_ns]
    return out


def snapshots(roots: Sequence[str] = ()) -> dict[str, Any]:
    return {r: snapshot(REPO / r) for r in [*SNAPSHOT_ROOTS, *roots]}


def diff_snaps(a: Mapping[str, Any], b: Mapping[str, Any]) -> list[str]:
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def snapshot_diff(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    return [
        f"{root}/{rel}"
        for root in sorted(set(a) | set(b))
        for rel in diff_snaps(a.get(root, {}), b.get(root, {}))
    ]


def archive(ref: str, dest: Path) -> None:
    """``git archive ref`` extracted to ``dest`` (the base tree of the PB rows; git is only read)."""
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    tar = subprocess.run(
        ["git", "-C", str(REPO), "archive", ref], capture_output=True, check=True
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=tar, check=True)


# ---- the targets: D1, H1 and the stand-ins, extracted to the scratch, built to the scratch ---------------------------------------------------


def _strip_appledouble(root: Path) -> None:
    for junk in list(root.rglob("._*")):
        junk.unlink()


def find_module(root: Path) -> Path:
    """The project directory under ``root/tests/spec`` (the one holding a ``*.csproj`` and a ``studio.toml``)."""
    spec = root / "tests" / "spec"
    cands = sorted(p.parent for p in spec.glob("*/studio.toml") if list(p.parent.glob("*.csproj")))
    if len(cands) != 1:
        raise RuntimeError(f"expected one module under {spec}, found {[str(c) for c in cands]}")
    return cands[0]


def harness_constants(module_dir: Path) -> dict[str, str]:
    """``BUILD_ENV``, ``PROJECT`` and ``ASSEMBLY`` as the harness skeleton (HARNESS_API_DOTNET.md section 6) defines them."""
    h = module_dir.parent / f"{module_dir.name}_harness.py"
    out = {"harness": str(h)}
    if h.is_file():
        text = h.read_text()
        for key in ("BUILD_ENV", "PROJECT", "ASSEMBLY"):
            m = re.search(rf'^{key}\s*=\s*"([^"]+)"', text, re.M)
            if m:
                out[key] = m.group(1)
    return out


def make_target(
    label: str,
    module_dir: Path,
    overlay: Path,
    truth_path: Path,
    *,
    runnable: bool = True,
    kind: str = "app",
) -> dict[str, Any]:
    c = harness_constants(module_dir) if runnable else {}
    return {
        "label": label,
        "kind": kind,
        "module_dir": module_dir,
        "overlay": overlay,
        "truth_path": truth_path,
        "truth": json.loads(truth_path.read_text()),
        "runnable": runnable,
        "build_env": c.get("BUILD_ENV"),
        "project": c.get("PROJECT", f"{module_dir.name}.csproj"),
        "assembly": c.get("ASSEMBLY", module_dir.name),
        "package": ".",
        "built": None,
        "build_error": None,
        "build_nuget": False,
    }


def materialise(
    label: str, src: Path, scratch: Path, truth_path: Path | None = None
) -> dict[str, Any]:
    """A target from a tarball (extracted to ``scratch/label``; the archive is only read) or a module directory
    ``<root>/tests/spec/<name>`` (read in place; a directory inside the repository needs no overlay)."""
    if src.is_file() and src.suffixes[-2:] in ([".tar", ".gz"], [".tgz"]) or src.suffix == ".tgz":
        dest = scratch / label
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True)
        with tarfile.open(src) as tf:
            tf.extractall(dest, filter="data")
        _strip_appledouble(dest)
        root = next(
            (p.parent.parent.parent.parent for p in dest.rglob("tests/spec/*/studio.toml")), None
        )
        if root is None:
            raise RuntimeError(f"{src}: no tests/spec/<x>/studio.toml in the archive")
        overlay = root
        module_dir = find_module(overlay)
    else:
        module_dir = src.resolve()
        overlay = module_dir.parent.parent.parent
        if module_dir.parent.name != "spec" or module_dir.parent.parent.name != "tests":
            raise RuntimeError(f"{src}: expected <root>/tests/spec/<name>")
    if overlay.resolve() != REPO.resolve():
        init = overlay / "tests" / "spec" / "__init__.py"
        if not init.exists():
            init.write_text(
                ""
            )  # the scratch copy gets the empty package file the repository's tests/spec has (HARNESS_API section 11)
    tp = truth_path or (module_dir / "truth.json")
    return make_target(label, module_dir, overlay, tp)


NUGET_RX = re.compile(
    r"NU1301|NU1101|NU1102|NU1103|Unable to load the service index|No such host|nodename nor servname|Name or service not known|Could not resolve host|network",
    re.I,
)


def build_target(ctx: Ctx, tgt: dict[str, Any]) -> None:
    """Build the target's service from a copy of its module directory into the scratch (``dotnet build <csproj> -c Release
    -p:RestoreLockedMode=true -o OUT``: the harness's own command); the harness is told through its ``BUILD_ENV`` variable.
    A failure is recorded: a NuGet one makes the rows that need the binary NOT RUN (prerequisite), any other one is theirs to fail."""
    root = ctx.scratch / "build" / tgt["label"]
    shutil.rmtree(root, ignore_errors=True)
    src, out = root / "src", root / "out"
    shutil.copytree(tgt["module_dir"], src, ignore=shutil.ignore_patterns("bin", "obj"))
    if not ctx.dotnet:
        tgt["build_error"] = "dotnet is not on PATH"
        return
    t0 = time.perf_counter()
    rc, so, se, _ = run(
        [
            "dotnet",
            "build",
            str(src / tgt["project"]),
            "-c",
            "Release",
            "-p:RestoreLockedMode=true",
            "-o",
            str(out),
            "--nologo",
            "-v",
            "q",
        ],
        cwd=src,
        env=ctx.env(PATH=real_path()),
        timeout=1500,
    )
    ctx.timings[f"build_{tgt['label']}_s"] = round(time.perf_counter() - t0, 1)
    tgt["build_src"] = src
    if rc == 0 and (out / f"{tgt['assembly']}.dll").is_file():
        tgt["built"] = out
    else:
        tgt["build_error"] = (so + se).strip()[-1500:]
        tgt["build_nuget"] = bool(NUGET_RX.search(so + se))


# ---- truth helpers (the matching rules of prereg 4.5) ------------------------------------------------------------------------------------------


def norm_template(template: str) -> str:
    """4.5: literal segments lowercased, every ``{...}`` replaced by ``{}``, a trailing ``/`` dropped."""
    t = re.sub(r"\{[^}]*\}", "{}", template)
    t = "/".join(s if s == "{}" else s.lower() for s in t.split("/"))
    return t.rstrip("/") or "/"


def rkey(method: str, template: str) -> tuple[str, str]:
    return method.upper(), norm_template(template)


def route_id_key(route_id: str) -> tuple[str, str]:
    m, t = route_id.split(" ", 1)
    return rkey(m, t)


def rlabel(k: tuple[str, str]) -> str:
    return f"{k[0]} {k[1]}"


def fraction(num: int, den: int, empty: float = 1.0) -> float:
    return round(num / den, 4) if den else empty


def snake(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def cmp_multi(want: Sequence[Any], got: Sequence[Any]) -> dict[str, Any]:
    """Multiset difference of two lists of hashable items: ``{missing, extra, equal}``."""
    w, g = collections.Counter(want), collections.Counter(got)
    miss, extra = sorted((w - g).elements(), key=repr), sorted((g - w).elements(), key=repr)
    return {
        "missing": [list(x) if isinstance(x, tuple) else x for x in miss[:25]],
        "extra": [list(x) if isinstance(x, tuple) else x for x in extra[:25]],
        "n_missing": len(miss),
        "n_extra": len(extra),
        "equal": not miss and not extra,
    }


def sqlite_only(ex: Mapping[str, Any]) -> bool:
    return ex.get("dialect") == "sqlite" or ex.get("dotnet", {}).get("providers") == ["sqlite"]


def cname(ex: Mapping[str, Any], name: str) -> str:
    """A table or column name under the 4.5 rule: exact on SQLite, case-insensitive on MySQL."""
    return name if sqlite_only(ex) else name.lower()


# ---- scoring the extract against a 4.5 truth (shared by the D1 rows and the held-out rows) ------------------------------------------------------------


def truth_route_map(truth: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {rkey(r["method"], r["template"]): r for r in truth["routes"]}


def ext_route_map(ex: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {rkey(r["method"], r["path"]): r for r in ex["routes"]}


def score_routes(truth: Mapping[str, Any], ex: Mapping[str, Any]) -> dict[str, Any]:
    t, e = truth_route_map(truth), ext_route_map(ex)
    found = [k for k in t if k in e]
    return {
        "truth_routes": len(t),
        "extract_routes": len(e),
        "recall": fraction(len(found), len(t)),
        "missing": sorted(rlabel(k) for k in t if k not in e),
        "extra": sorted(rlabel(k) for k in e if k not in t),
        "style_wrong": sorted(
            rlabel(k) for k in found if t[k].get("style") and t[k]["style"] != e[k].get("style")
        ),
    }


def score_auth(truth: Mapping[str, Any], ex: Mapping[str, Any]) -> dict[str, Any]:
    t, e = truth_route_map(truth), ext_route_map(ex)
    matched = [k for k in t if k in e]
    exact = [k for k in matched if t[k]["auth"] == e[k]["auth"]]
    return {
        "matched": len(matched),
        "exact": len(exact),
        "share_of_matched": fraction(len(exact), len(matched), 0.0),
        "share_of_truth": fraction(len(exact), len(t), 0.0),
        "wrong": {
            rlabel(k): [t[k]["auth"], e[k]["auth"]] for k in matched if t[k]["auth"] != e[k]["auth"]
        },
    }


COLUMN_FACTS = ("nullable", "unique", "pk", "max_length", "fk", "private")


def column_fact(c: Mapping[str, Any], e: Mapping[str, Any], fact: str) -> bool:
    if (
        fact == "unique"
    ):  # a primary key is unique on both sides: D1's truth says so, dnparity1's does not, the extract keeps `unique` for indexes (B1 contract 8)
        return bool(c["unique"] or c["pk"]) == bool(e["unique"] or e["primary_key"])
    if fact == "pk":
        return bool(c["pk"]) == bool(e["primary_key"])
    if fact == "fk":
        a, b = c.get("fk"), e.get("foreign_key")
        return (a or None) == (b or None) or (
            bool(a) and bool(b) and str(a).lower() == str(b).lower()
        )
    if fact == "nullable":
        return bool(c["nullable"]) == bool(e["nullable"])
    if fact == "private":
        return bool(c.get("private")) == bool(e.get("private"))
    return c.get(fact) == e.get(fact)


def score_tables(truth: Mapping[str, Any], ex: Mapping[str, Any]) -> dict[str, Any]:
    tt = {cname(ex, t["table"]): t for t in truth["tables"]}
    et = {cname(ex, t["table"]): t for t in ex["tables"]}
    facts = good = 0
    wrong: list[str] = []
    for name, t in tt.items():
        ecols = {cname(ex, c["name"]): c for c in et[name]["columns"]} if name in et else {}
        for c in t["columns"]:
            e = ecols.get(cname(ex, c["name"]))
            for f in COLUMN_FACTS:
                facts += 1
                ok = e is not None and column_fact(c, e, f)
                good += ok
                if not ok:
                    wrong.append(f"{name}.{c['name']}.{f}")
    return {
        "truth_tables": len(tt),
        "found": sum(1 for n in tt if n in et),
        "table_recall": fraction(sum(1 for n in tt if n in et), len(tt)),
        "missing_tables": sorted(n for n in tt if n not in et),
        "extra_tables": sorted(n for n in et if n not in tt),
        "facts": facts,
        "facts_ok": good,
        "fact_accuracy": fraction(good, facts),
        "wrong_facts": wrong[:30],
    }


def score_wire(truth: Mapping[str, Any], ex: Mapping[str, Any]) -> dict[str, Any]:
    et = {
        cname(ex, t["table"]): {cname(ex, c["name"]): c for c in t["columns"]} for t in ex["tables"]
    }
    total = good = 0
    wrong: list[str] = []
    for t in truth["tables"]:
        for c in t["columns"]:
            if c.get("wire") is None:
                continue
            total += 1
            e = et.get(cname(ex, t["table"]), {}).get(cname(ex, c["name"]))
            if e is not None and e.get("wire") == c["wire"]:
                good += 1
            else:
                wrong.append(
                    f"{t['table']}.{c['name']}: {c['wire']!r} vs {None if e is None else e.get('wire')!r}"
                )
    return {"wire_columns": total, "ok": good, "share": fraction(good, total), "wrong": wrong[:25]}


def owner_scoped_routes(truth: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The truth's owner-scoped routes: the non-Public routes with a refusal of cause ``not_owned`` or ``not_found`` (the truth schema has no per-route table)."""
    t = truth_route_map(truth)
    scoped = {
        route_id_key(r["route"])
        for r in truth.get("refusals", ())
        if r.get("cause") in ("not_owned", "not_found")
    }
    return sorted(k for k in scoped if k in t and t[k]["auth"] != "Public")


def score_own(truth: Mapping[str, Any], ex: Mapping[str, Any]) -> dict[str, Any]:
    owned = set(truth.get("owners", {})) | set(truth.get("owner_via", {}))
    e = ext_route_map(ex)
    qf = [q for q in ex.get("dotnet", {}).get("query_filters", ()) if q.get("owner")]
    qf_routes = {route_id_key(r) for q in qf for r in q.get("applies_to_routes", ())}
    scoped = owner_scoped_routes(truth)
    with_ev = [k for k in scoped if k in e and (e[k]["owner_uses"] or k in qf_routes)]
    seen = {o["model"] for r in ex["routes"] for o in r["owner_uses"]} | {q["table"] for q in qf}
    member = [k for k, r in e.items() if r["auth"] != "Public"]
    return {
        "owner_scoped_truth_routes": len(scoped),
        "with_owner_evidence": len(with_ev),
        "share": fraction(len(with_ev), len(scoped)),
        "without": sorted(rlabel(k) for k in scoped if k not in with_ev),
        "evidence_on_tables_truth_says_are_not_owned": sorted(seen - owned),
        "member_routes_with_evidence": fraction(
            sum(bool(e[k]["owner_uses"] or k in qf_routes) for k in member), len(member)
        ),
    }


def ext_pairs(ex: Mapping[str, Any]) -> set[tuple[tuple[str, str], int]]:
    return {(rkey(r["method"], r["path"]), x["status"]) for r in ex["routes"] for x in r["raises"]}


def score_raise(truth: Mapping[str, Any], ex: Mapping[str, Any]) -> dict[str, Any]:
    tp = {(route_id_key(x["route"]), int(x["status"])) for x in truth.get("refusals", ())}
    ep = ext_pairs(ex)
    both = tp & ep
    return {
        "truth_pairs": len(tp),
        "extract_pairs": len(ep),
        "precision": fraction(len(both), len(ep)),
        "recall": fraction(len(both), len(tp)),
        "false_positives": sorted(f"{rlabel(k)} {s}" for k, s in ep - tp)[:30],
        "missed": sorted(f"{rlabel(k)} {s}" for k, s in tp - ep)[:30],
    }


def score_unread(truth: Mapping[str, Any], ex: Mapping[str, Any]) -> dict[str, Any]:
    """0 silent drops (N11): every truth route is a route of the extract or named, file and line (within 2 lines of the registration), in ``unread`` or ``excluded``."""
    e = ext_route_map(ex)
    named = [
        (x.get("file"), int(x["line"]))
        for x in [*ex.get("unread", ()), *ex.get("excluded", ())]
        if x.get("line") is not None
    ]
    silent = []
    for r in truth["routes"]:
        if rkey(r["method"], r["template"]) in e:
            continue
        if not any(f == r["file"] and abs(ln - int(r["line"])) <= 2 for f, ln in named):
            silent.append(f"{r['method']} {r['template']} ({r['file']}:{r['line']})")
    return {"silent_drops": silent, "n": len(silent)}


BOUND_KINDS = ("ge", "le", "min_length", "max_length")


def ext_bound_claims(ex: Mapping[str, Any]) -> set[tuple[tuple[str, str], str, str, Any]]:
    """The bounds the extract claims: (route, wire field, kind, value) for each non-null ``ge/le/min_length/max_length`` and each ``required`` of a body or query parameter."""
    out = set()
    for r in ex["routes"]:
        k = rkey(r["method"], r["path"])
        for p in r["params"]:
            field = p.get("wire") or p["name"]
            for b in BOUND_KINDS:
                if p.get(b) is not None:
                    out.add((k, field, b, p[b]))
            if p.get("required") and p.get("source") in ("body", "query", "form", "header"):
                out.add((k, field, "required", True))
    return out


def truth_bound_set(truth: Mapping[str, Any]) -> set[tuple[tuple[str, str], str, str, Any]]:
    out = set()
    for b in truth.get("bounds", ()):
        k = route_id_key(b["route"])
        for kind in BOUND_KINDS:
            if b.get(kind) is not None:
                out.add((k, b["field"], kind, b[kind]))
        if b.get("required"):
            out.add((k, b["field"], "required", True))
    return out


def score_bounds(truth: Mapping[str, Any], ex: Mapping[str, Any]) -> dict[str, Any]:
    claims, want = ext_bound_claims(ex), truth_bound_set(truth)
    good = claims & want
    return {
        "claimed": len(claims),
        "truth_bounds": len(want),
        "precision": fraction(len(good), len(claims)),
        "recall": fraction(len(good), len(want)),
        "claimed_not_in_truth": sorted(f"{rlabel(k)} {f} {b}={v}" for k, f, b, v in claims - want)[
            :40
        ],
        "missed": sorted(f"{rlabel(k)} {f} {b}={v}" for k, f, b, v in want - claims)[:40],
    }


# ---- the interface to the code of nodes B2, B3 and B4 ---------------------------------------------------------------------------------------


def add_import_root(root: Path | str) -> None:
    """``cli.py`` and ``rules.import_rules`` put the import root on ``sys.path`` before any harness is imported (K7)."""
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def use_overlay(tgt: dict[str, Any]) -> None:
    """Make ``tests.spec.<x>_harness`` importable in this process from the target's overlay root (a scratch extraction has its own
    ``tests/spec/__init__.py``); the repository root follows, as the CLI's dotted form needs it."""
    ov = str(tgt["overlay"])
    if Path(ov).resolve() != REPO.resolve():
        for name in [n for n in sys.modules if n == "tests" or n.startswith("tests.")]:
            if getattr(sys.modules[name], "__file__", None) and not str(
                sys.modules[name].__file__
            ).startswith(ov):
                del sys.modules[name]
        if ov in sys.path:
            sys.path.remove(ov)
        sys.path.insert(0, ov)
    add_import_root(REPO)
    importlib.invalidate_caches()


SELECT_CODE = """
import json, sys
from pathlib import Path
from lattice.importer.frontends import select
out = {}
for c in json.loads(sys.argv[1]):
    try:
        out[c["id"]] = select(None, Path(c["root"]), c["package"]).name
    except LookupError as e:
        out[c["id"]] = "LookupError: " + str(e)
    except Exception as e:
        out[c["id"]] = type(e).__name__ + ": " + str(e)
print(json.dumps(out))
"""


class Lib:
    """What the rows call of the code of nodes B2, B3 and B4. :class:`RealLib` implements it over the library's own names (the
    only place that spells them); ``dn_standin/standin_lib.py`` implements it for the self-test while a node is not merged."""

    # B2: the front end and the rules-first import
    def select_names(self, ctx: Ctx, cases: list[dict[str, str]], tree: Path) -> dict[str, str]: ...
    def model_view(self, ctx: Ctx, tgt: dict[str, Any]) -> dict[str, Any]: ...
    def runtime_view(self, ctx: Ctx, tgt: dict[str, Any]) -> dict[str, Any]: ...
    def reconcile(self, ctx: Ctx, tgt: dict[str, Any]) -> dict[str, Any]: ...
    def import_rules(
        self, ctx: Ctx, tgt: dict[str, Any], out_dir: Path, *, root: Path | None = None
    ) -> dict[str, Any]: ...
    def r_verdicts(
        self, ctx: Ctx, tgt: dict[str, Any], cand_dir: Path, variant: str, *, spec: Any = None
    ) -> dict[str, str]: ...
    def review_list(self, ctx: Ctx, tgt: dict[str, Any], import_dir: Path) -> dict[str, Any]: ...
    def import_without_sdk(
        self, ctx: Ctx, tgt: dict[str, Any], out_dir: Path, path: str
    ) -> dict[str, Any]: ...
    def dnapp_check(self, ctx: Ctx, layers: str, bug: str = "") -> dict[str, Any]: ...
    def check_run(
        self, ctx: Ctx, tgt: dict[str, Any], proj: Path, layers: str, database: str = "mysql"
    ) -> dict[str, Any]: ...
    # B3: layer A
    def layer_a(
        self,
        ctx: Ctx,
        root: Path,
        kind: str,
        *,
        spec: Any = None,
        truth_policy: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]: ...
    def gated(self, ctx: Ctx) -> dict[str, Any]: ...
    def derive(self, ctx: Ctx, root: Path) -> dict[str, Any]: ...
    def judged(self, ctx: Ctx, root: Path, truth_policy: Mapping[str, Any]) -> dict[str, Any]: ...
    def layer_a_cli(
        self, ctx: Ctx, tgt: dict[str, Any], proj: Path, *, no_sdk: bool = False
    ) -> dict[str, Any]: ...
    def not_owned(self, ctx: Ctx, root: Path) -> dict[str, Any]: ...
    # B4: the workflow pass and the consumers
    def workflows(
        self, ctx: Ctx, tgt: dict[str, Any], mode: str, out_dir: Path
    ) -> dict[str, Any]: ...
    def inventory(
        self,
        ctx: Ctx,
        tgt: dict[str, Any],
        out_dir: Path,
        workflows: Path | None = None,
        root: Path | None = None,
        extract: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...
    def consumer(
        self, ctx: Ctx, name: str, tgt: dict[str, Any], inv: Path, proj: Path, **kw: Any
    ) -> dict[str, Any]: ...
    def tool(
        self, ctx: Ctx, name: str, tgt: dict[str, Any], proj: Path, **kw: Any
    ) -> dict[str, Any]: ...


def json_cli(
    ctx: Ctx,
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    timeout: float = 1800,
) -> dict[str, Any]:
    rc, so, se, wall = run([ctx.py, "-m", *argv], cwd=cwd, env=env or ctx.env(), timeout=timeout)
    doc: Any = None
    try:
        doc = json.loads(so)
    except ValueError:
        pass
    return {
        "rc": rc,
        "stdout": so[-2000:],
        "stderr": se[-1500:],
        "wall_s": round(wall, 2),
        "json": doc,
    }


class RealLib(Lib):
    # ---- B2 ------------------------------------------------------------------------------------------------------------
    def select_names(self, ctx: Ctx, cases: list[dict[str, str]], tree: Path) -> dict[str, str]:
        rc, so, se, _ = run(
            [ctx.py, "-c", SELECT_CODE, json.dumps(cases)],
            cwd=tree,
            env=ctx.env(PYTHONPATH=str(tree)),
            timeout=300,
        )
        if rc != 0:
            raise RuntimeError(f"select in {tree} failed: {se[-300:]}")
        return json.loads(so.strip().splitlines()[-1])

    def model_view(self, ctx: Ctx, tgt: dict[str, Any]) -> dict[str, Any]:
        from lattice.importer.frontends import select

        use_overlay(tgt)
        fe = select("aspnetcore")
        m = fe.extract(Path(tgt["module_dir"]), ".")
        a = m.auth
        return {
            "framework": m.framework, "orm": m.orm, "routes": len(m.routes), "tables": len(m.tables), "models": sorted(x.name for x in m.models),
            "login_path": a.login_path if a else None, "username_field": a.username_field if a else None,
            "password_field": a.password_field if a else None, "token_path": list(a.token_path) if a else [],
        }  # fmt: skip

    def runtime_view(self, ctx: Ctx, tgt: dict[str, Any]) -> dict[str, Any]:
        from lattice.importer.frontends import select

        use_overlay(tgt)
        fe = select("aspnetcore")
        root = Path(tgt["module_dir"])
        toml = tomllib.loads((root / "studio.toml").read_text())["app"]
        mod, _, attr = str(toml["runtime"]).partition(":")
        harness = importlib.import_module(mod)
        rt = fe.runtime(root, ".")
        harness = sys.modules.get(
            mod, harness
        )  # the front end re-imports the harness module (a fresh object)
        view = {
            "with_harness": {
                "framework": rt.framework,
                # B2 returns dataclasses.replace(RUNTIME, transport=<login-field wrapper>) (deviation 3), so identity is that of its auth driver
                "is_harness_runtime": rt is getattr(harness, attr)
                or rt.auth is getattr(harness, attr).auth,
                "one_server": rt.one_server,
            }
        }
        bare = ctx.scratch / "rt-bare" / root.name
        shutil.rmtree(bare.parent, ignore_errors=True)
        shutil.copytree(root, bare, ignore=shutil.ignore_patterns("bin", "obj"))
        text = re.sub(r"(?m)^runtime\s*=.*\n", "", (bare / "studio.toml").read_text())
        (bare / "studio.toml").write_text(text)
        m = fe.extract(root, ".")
        creds = dict(getattr(harness, "USERS", {}) or {})
        rt2 = fe.runtime(bare, ".", model=m, credentials=creds)
        view["without_harness"] = {
            "framework": rt2.framework,
            "one_server": rt2.one_server,
            "auth": repr(rt2.auth)[:300],
        }
        return view

    def reconcile(self, ctx: Ctx, tgt: dict[str, Any]) -> dict[str, Any]:
        from lattice.importer.frontends import select

        use_overlay(tgt)
        fe = select("aspnetcore")
        root = Path(tgt["module_dir"])
        m = fe.extract(root, ".")
        keep = {k: os.environ.get(k) for k in ("PATH", "LATTICE_CONTAINER_OWNER")}
        os.environ["PATH"], os.environ["LATTICE_CONTAINER_OWNER"] = real_path(), ctx.owner
        for t in ctx.targets():
            if t.get("built") and t.get("build_env"):
                os.environ[t["build_env"]] = str(t["built"])
        try:
            res = fe.reconcile(root, m)
        finally:
            for k, v in keep.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return {"routes": res, "containers_left": owned_containers(ctx)}

    def import_rules(
        self, ctx: Ctx, tgt: dict[str, Any], out_dir: Path, *, root: Path | None = None
    ) -> dict[str, Any]:
        shutil.rmtree(out_dir, ignore_errors=True)
        r = Path(root or tgt["module_dir"])
        rc, so, se, wall = run(
            [
                ctx.py,
                "-m",
                "lattice.importer",
                str(r),
                "--package",
                ".",
                "--rules-only",
                "--framework",
                "aspnetcore",
                "--out",
                str(out_dir),
            ],
            cwd=tgt["overlay"],
            env=ctx.env(),
            timeout=1800,
        )
        return {"rc": rc, "stdout": so[-600:], "stderr": se[-1500:], "wall_s": wall, "dir": out_dir}

    def machinery(self, ctx: Ctx, tgt: dict[str, Any]) -> dict[str, Any]:
        """What ``rules.import_rules`` builds around the probes for a .NET app (go_parity_probes3.machinery3 with the .NET front end):
        the import root is put on ``sys.path`` first, as the CLI does."""
        key = f"mach-{tgt['label']}"
        if key in ctx.cache:
            return ctx.cache[key]  # type: ignore[no-any-return]
        from lattice.importer import dotnet_rules as dr
        from lattice.importer import go_rules, rules
        from lattice.importer.frontends import select
        from lattice.importer.lift import lift_repo

        use_overlay(tgt)
        root, package = Path(tgt["module_dir"]), "."
        add_import_root(root)
        fe = dr.InferringFrontEnd(select("aspnetcore", root, package))
        found = dr.harness_factory(root, package)
        if found is None:
            raise RuntimeError(f"{root}/studio.toml names no [app] app")
        factory, _ = found
        fe.extract(root, package)
        runtime = rules._runtime(fe, root, package, fe.extract(root, package))
        res = lift_repo(root, package, factory, only=(), frontend=fe, runtime=runtime)
        getattr(dr, "patch_profile", go_rules.patch_profile)(res, fe.private, fe.validation_status)
        c, api = res.context, res.lattice_profile.api
        seeder = rules.Seeder(
            res.model,
            c.inputs.tables,
            c.inputs.auth,
            c.inputs.insert_hook,
            runtime,
            factory,
            api.uuid_storage,
            api.datetime_storage,
        )
        unreachable = {
            f"{e.method} {e.path}": e.reason
            for e in res.model.excluded_routes
            if e.kind == "unreachable"
        }
        for ep, why in rules.network_routes(root, package, res.model).items():
            unreachable.setdefault(ep, why)
        for ep, why in rules.shadowed_routes(res.model).items():
            unreachable.setdefault(ep, why)
        unclear = {
            t: res.profile.ownership_reasons.get(t, "")
            for t, d in res.profile.ownership.items()
            if d == "unclear"
        }
        m = {"result": res, "seeder": seeder, "sessions": rules._sessions(runtime, factory), "unreachable": unreachable, "rules": rules, "unclear": unclear, "outside": rules.outside_api(res.model), "uploads": rules.upload_routes(res.model)}  # fmt: skip
        ctx.cache[key] = m
        return m

    def r_verdicts(
        self, ctx: Ctx, tgt: dict[str, Any], cand_dir: Path, variant: str, *, spec: Any = None
    ) -> dict[str, str]:
        from dataclasses import replace

        from lattice.spec.probes import run_probes

        m = self.machinery(ctx, tgt)
        cand = spec if spec is not None else load_candidate(cand_dir)
        seeder, res, rules = m["seeder"], m["result"], m["rules"]
        who = rules.callers(res, seeder, variant)
        kw = dict(login=who.login, mint_token=who.mint, scheme=who.scheme, refusals=who.refusals, login_route=who.login_route, auth_headers=who.headers, inactive_login=who.inactive_login, unreachable=m["unreachable"], variant=variant, auth_present=who.present, json_login=getattr(who, "json_login", None))  # fmt: skip
        rr = run_probes(
            replace(cand.SPEC, users=seeder.credentials(variant)),
            m["sessions"],
            seeder.make_db,
            **kw,
        )
        rules.mark_unclear(cand.SPEC, rr, m["unclear"])
        rules.mark_outside(cand.SPEC, rr, m["outside"])
        rules.mark_uploads(rr, m["uploads"])
        return verdict_map(rr)

    def review_list(self, ctx: Ctx, tgt: dict[str, Any], import_dir: Path) -> dict[str, Any]:
        rev = ctx.scratch / "review" / tgt["label"]
        shutil.rmtree(rev, ignore_errors=True)
        shutil.copytree(import_dir, rev)
        rc, so, se, _ = run(
            [ctx.py, "-m", "lattice.importer", "review", str(rev), "list"],
            cwd=tgt["overlay"],
            env=ctx.env(),
        )
        return {
            "rc": rc,
            "ids": sorted({ln.split("\t")[0] for ln in so.splitlines() if "\trule\t" in ln}),
            "stderr": se[-300:],
        }

    def import_without_sdk(
        self, ctx: Ctx, tgt: dict[str, Any], out_dir: Path, path: str
    ) -> dict[str, Any]:
        rc, so, se, wall = run(
            [
                ctx.py,
                "-m",
                "lattice.importer",
                str(tgt["module_dir"]),
                "--package",
                ".",
                "--rules-only",
                "--framework",
                "aspnetcore",
                "--out",
                str(out_dir),
            ],
            cwd=tgt["overlay"],
            env=ctx.env(PATH=path),
            timeout=600,
        )
        return {"rc": rc, "stderr": se[-800:], "stdout": so[-300:]}

    def dnapp_check(self, ctx: Ctx, layers: str, bug: str = "") -> dict[str, Any]:
        from lattice.spec import check

        for name in [n for n in sys.modules if n == "tests" or n.startswith("tests.")]:
            f = getattr(
                sys.modules[name], "__file__", None
            )  # a target's overlay (use_overlay) may have claimed `tests`
            if f and not str(f).startswith(str(REPO)):
                del sys.modules[name]
        sys.path[:] = [str(REPO), *[p for p in sys.path if p != str(REPO)]]
        importlib.invalidate_caches()
        rb = importlib.import_module("tests.spec.dn_harness_rb")
        keep = os.environ.get("DNAPP_BUG")
        os.environ["DNAPP_BUG"] = bug
        if not bug:
            os.environ.pop("DNAPP_BUG")
        try:
            rep = check(
                rb.SPEC,
                rb.make_app,
                rb.build_db,
                layers=layers,
                runtime=rb.RUNTIME,
                login=rb.LOGIN,
                rules=rb.RULES,
            )
        finally:
            if keep is None:
                os.environ.pop("DNAPP_BUG", None)
            else:
                os.environ["DNAPP_BUG"] = keep
        out: dict[str, Any] = {
            "findings": sorted(f"{f.layer}|{f.endpoint}|{f.case}" for f in rep.findings),
            "endpoints": sorted({f.endpoint for f in rep.findings}),
        }
        if rep.rules is not None and getattr(rep.rules, "items", None):
            s = rep.rules.summary["items"]
            n = sum(s.values())
            out.update(
                items=n,
                verdicts=dict(s),
                verified_share=round(s["verified"] / n, 4) if n else 0.0,
                refuted=sorted(i for i, v in rep.rules.items.items() if v.verdict == "refuted"),
                verdict_map={i: v.verdict for i, v in rep.rules.items.items()},
            )
        out["cases"] = len(getattr(rep, "cases", ()) or ())
        return out

    # ---- B3 ------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _policy(pol: Any, d: Mapping[str, Any]) -> Any:
        """A ``DnPolicy`` from a truth policy dict (the GoPolicy field names): only the fields the class has."""
        import dataclasses

        have = {f.name for f in dataclasses.fields(pol.DnPolicy)}
        kw: dict[str, Any] = {
            "tables": dict(d["tables"]), "users_table": d.get("users_table"), "owners": dict(d.get("owners", {})),
            "owner_via": {k: tuple(v) for k, v in d.get("owner_via", {}).items()},
            "private": {k: tuple(v) for k, v in d.get("private", {}).items()},
            "machines": {k: (m["column"], frozenset(tuple(e) for e in m["edges"])) for k, m in d.get("machines", {}).items()},
            "admin_routes": tuple(tuple(r) for r in d.get("admin_routes", ())), "open_routes": tuple(tuple(r) for r in d.get("open_routes", ())),
            "admin_role": d.get("admin_role", "Admin"),
        }  # fmt: skip
        return pol.DnPolicy(**{k: v for k, v in kw.items() if k in have})

    def layer_a(
        self,
        ctx: Ctx,
        root: Path,
        kind: str,
        *,
        spec: Any = None,
        truth_policy: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from lattice.dncheck import derive as drv
        from lattice.dncheck import policy as pol

        if kind == "truth":
            p = self._policy(pol, truth_policy or {})
        elif kind == "from_spec":
            p = pol.DnPolicy.from_spec(spec.SPEC if hasattr(spec, "SPEC") else spec)
        elif kind == "spec_policy":
            derived = drv.derive_dn_policy(root)
            p = drv.dn_spec_policy(spec.SPEC if hasattr(spec, "SPEC") else spec, root, derived)
        else:
            p = pol.DnPolicy()
        found = pol.run_dncheck(root, p, strict=True)
        return {
            "findings": sorted(
                (
                    {
                        "code": f.code,
                        "key": f.key,
                        "file": f.file,
                        "line": f.line,
                        "message": f.message,
                    }
                    for f in found
                ),
                key=lambda x: (x["file"], x["line"], x["code"], x["key"]),
            ),
            "baseline": sorted(getattr(p, "baseline", ())),
        }

    def check_run(
        self, ctx: Ctx, tgt: dict[str, Any], proj: Path, layers: str, database: str = "mysql"
    ) -> dict[str, Any]:
        return check_cli(ctx, tgt, proj, layers, database)

    def gated(self, ctx: Ctx) -> dict[str, Any]:
        from lattice.dncheck import policy as pol

        return {
            "gated": dict(pol.DN_GATED),
            "note": pol.gated_note(),
            "codes": sorted(pol.DN_CODES),
        }

    def derive(self, ctx: Ctx, root: Path) -> dict[str, Any]:
        from lattice.dncheck import derive as drv

        p = drv.derive_dn_policy(root)
        return {
            "owners": dict(p.owners), "owner_via": {k: list(v) for k, v in p.owner_via.items()}, "private": {k: sorted(v) for k, v in p.private.items()},
            "baseline": sorted(p.baseline), "users_table": p.users_table, "tables": dict(p.tables),
        }  # fmt: skip

    def judged(self, ctx: Ctx, root: Path, truth_policy: Mapping[str, Any]) -> dict[str, Any]:
        from lattice.dncheck import policy as pol

        p = self._policy(pol, truth_policy)
        fn = getattr(pol, "judged_routes", None)
        if fn is not None:
            return {"method": "policy.judged_routes", "judged": sorted(str(x) for x in fn(root, p))}
        import lattice.dncheck as dn

        data = json.loads(
            dn.run(
                "check",
                {"root": str(Path(root).resolve()), "sdk": dn.sdk_version(), "policy": p.to_json()},
            )
        )
        if isinstance(data.get("judged"), list):
            return {
                "method": "check output: judged",
                "judged": sorted(str(x) for x in data["judged"]),
            }
        return {"method": "none", "judged": None}

    def layer_a_cli(
        self, ctx: Ctx, tgt: dict[str, Any], proj: Path, *, no_sdk: bool = False
    ) -> dict[str, Any]:
        env = ctx.env()
        if no_sdk:  # PATH without any directory holding `dotnet`
            env["PATH"] = path_without_dotnet(ctx)
        rc, so, se, wall = run([ctx.py, "-m", "lattice.spec", "check", "--spec", "spec:SPEC", "--app", f"tests.spec.{tgt['module_dir'].name}_harness:make_app", "--db", f"tests.spec.{tgt['module_dir'].name}_harness:build_db", "--runtime", f"tests.spec.{tgt['module_dir'].name}_harness:RUNTIME", "--layers", "A", "--app-dir", str(tgt["module_dir"]), "--json"], cwd=proj, env={**env, "PYTHONPATH": os.pathsep.join([str(proj), ctx.pythonpath()])}, timeout=900)  # fmt: skip
        doc: Any = None
        try:
            doc = json.loads(so)
        except ValueError:
            pass
        return {
            "rc": rc,
            "stderr": se[-800:],
            "json": doc,
            "stdout": so[-800:],
            "wall_s": round(wall, 2),
        }

    def not_owned(self, ctx: Ctx, root: Path) -> dict[str, Any]:
        from lattice.dncheck import derive as drv
        from lattice.dncheck import policy as pol

        d = drv.derive_dn_policy(root)
        answer = getattr(d, "not_owned", None)
        found = pol.run_dncheck(root, d, strict=True) if answer is not None else []
        return {"status": answer, "findings": sorted(f.key for f in found if f.code == "DN805")}

    # ---- B4 ------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _harness_users(tgt: Mapping[str, Any]) -> list[str]:
        use_overlay(tgt)  # type: ignore[arg-type]
        app = tomllib.loads((Path(tgt["module_dir"]) / "studio.toml").read_text()).get("app", {})
        mod = str(app.get("app", "")).partition(":")[0]
        return list(getattr(importlib.import_module(mod), "USERS", {}) or {}) if mod else []

    def workflows(self, ctx: Ctx, tgt: dict[str, Any], mode: str, out_dir: Path) -> dict[str, Any]:
        shutil.rmtree(out_dir, ignore_errors=True)
        argv = [
            "lattice.importer",
            "workflows",
            str(tgt["module_dir"]),
            "--package",
            ".",
            "--out",
            str(out_dir),
        ]
        env = ctx.env()
        if mode == "static":
            argv.append("--static")
        else:  # an observed run needs declared users (`--user`): the handles of the harness's USERS
            for handle in self._harness_users(tgt):
                argv += ["--user", handle]
            if mode == "observed-sqlite":
                argv += ["--database", "sqlite"]
            else:
                argv += ["--database", "mysql"]
                env = ctx.env_real()
        r = json_cli(ctx, argv, cwd=tgt["overlay"], env=env, timeout=3600)
        f = out_dir / "workflows.json"
        r["doc"] = json.loads(f.read_text()) if f.is_file() else None
        r["dir"] = out_dir
        return r

    def inventory(
        self,
        ctx: Ctx,
        tgt: dict[str, Any],
        out_dir: Path,
        workflows: Path | None = None,
        root: Path | None = None,
        extract: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        shutil.rmtree(out_dir, ignore_errors=True)
        if (
            extract is not None
        ):  # the library's own capture(), handed an extract (a D1 whose code differs from the real one)
            from lattice.importer.inventory import capture

            try:
                meta = capture(
                    Path(root or tgt["module_dir"]),
                    out_dir,
                    package=".",
                    extract=extract,
                    workflows=workflows,
                )
            except Exception as err:  # noqa: BLE001
                return {
                    "rc": 2,
                    "stderr": f"{type(err).__name__}: {err}",
                    "inventory": None,
                    "dir": out_dir,
                }
            return {"rc": 0, "stderr": "", "inventory": meta, "dir": out_dir}
        argv = [
            "lattice.importer",
            "inventory",
            str(root or tgt["module_dir"]),
            "--out",
            str(out_dir),
            "--package",
            ".",
        ]
        if workflows is not None:
            argv += ["--workflows", str(workflows)]
        r = json_cli(ctx, argv, cwd=tgt["overlay"])
        f = out_dir / "inventory.json"
        r["inventory"] = json.loads(f.read_text()) if f.is_file() else None
        r["dir"] = out_dir
        return r

    def consumer(
        self, ctx: Ctx, name: str, tgt: dict[str, Any], inv: Path, proj: Path, **kw: Any
    ) -> dict[str, Any]:
        """``implementation`` / ``appmap`` / ``impact`` through their CLIs (``--json``), run in ``proj`` (a scratch project holding the
        spec as ``spec.py``) with the inventory directory ``inv``."""
        env = {**ctx.env(), "PYTHONPATH": os.pathsep.join([str(proj), ctx.pythonpath()])}
        root = str(kw.get("app_root") or tgt["module_dir"])
        if name == "implementation":
            argv = [
                "lattice.spec",
                "implementation",
                "--spec",
                "spec:SPEC",
                "--inventory",
                str(inv),
                "--app-root",
                root,
                "--json",
            ]
        elif name == "appmap":
            argv = [
                "lattice.spec",
                "appmap",
                "--spec",
                "spec:SPEC",
                "--inventory",
                str(inv),
                "--app-root",
                root,
                "--json",
            ]
            if (proj / "spec.trace.json").is_file():
                argv += ["--trace", str(proj / "spec.trace.json")]
        elif name == "impact":
            argv = [
                "lattice.spec",
                "impact",
                "--project",
                str(proj),
                "--candidate",
                str(kw["candidate"]),
                "--inventory",
                str(inv),
                "--tiers",
                "3",
                "--json",
            ]
        else:
            raise KeyError(name)
        return json_cli(ctx, argv, cwd=proj, env=env, timeout=1800)

    def tool(
        self, ctx: Ctx, name: str, tgt: dict[str, Any], proj: Path, **kw: Any
    ) -> dict[str, Any]:
        """``ask`` (a scripted model: no model call), ``mcp`` (the stdio server), ``perf``, ``contracts_live``, ``improve`` and
        ``rebuild`` (a scripted agent): the functions ``tool_*`` below."""
        fn = {
            "ask": tool_ask,
            "mcp": tool_mcp,
            "perf": tool_perf,
            "contracts_live": tool_contracts_live,
            "improve": tool_improve,
            "rebuild": tool_rebuild,
        }[name]
        return fn(ctx, tgt, proj, **kw)  # type: ignore[no-any-return]


def owned_containers(ctx: Ctx) -> int:
    """Containers carrying this runner's ``lattice.owner`` label (any state), counted with the real ``docker``."""
    exe = shutil.which("docker", path=real_path())
    if exe is None:
        return 0
    rc, so, _, _ = run(
        [exe, "ps", "-aq", "--filter", f"label=lattice.owner={ctx.owner}"],
        env={**os.environ, "PATH": real_path()},
        timeout=60,
    )
    return len([ln for ln in so.splitlines() if ln.strip()]) if rc == 0 else 0


def load_candidate(d: Path) -> Any:
    path = d / "candidate_spec.py"
    spec = importlib.util.spec_from_file_location(f"cand_{abs(hash(str(d)))}", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def verdict_map(rr: Any) -> dict[str, str]:
    return {i: v.verdict for i, v in rr.items.items()}


def load_report(d: Path) -> dict[str, Any]:
    return json.loads((d / "report.json").read_text())  # type: ignore[no-any-return]


# ---- the consumer tools of RealLib.tool: ask (a scripted model), mcp (stdio), perf, contracts check, improve, rebuild (a scripted agent) ------------------------------


def load_proj_spec(proj: Path) -> tuple[Any, Any]:
    """``spec.py`` of the scratch project (Streak's) and its trace (``spec.trace.json`` beside it, else None)."""
    from lattice.spec.rules import Trace

    sp = importlib.util.spec_from_file_location(
        f"proj_spec_{abs(hash(str(proj)))}", proj / "spec.py"
    )
    assert sp and sp.loader
    mod = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(mod)
    trace = Trace.load(proj / "spec.trace.json") if (proj / "spec.trace.json").is_file() else None
    return mod.SPEC, trace


def impl_family(label: str | None) -> str:
    """The prereg's coarse kinds over the library's implementation labels."""
    if label in ("implemented_verified", "implemented_unverified"):
        return "implemented"
    return (
        "not_implemented"
        if label in ("required_not_implemented", "not_specified_not_implemented")
        else str(label)
    )


def tool_ask(ctx: Ctx, tgt: dict[str, Any], proj: Path, inventory: Path) -> dict[str, Any]:
    """The three fixed questions through ``lattice.spec.ask.ask`` with a scripted generator (no model): each reply cites code lines read from the
    extract; the library's own checker then accepts or rejects the citations and re-checks the label."""
    from lattice.llm.client import ScriptedGenerator
    from lattice.spec.ask import ask, build_digest
    from lattice.spec.implementation import load_inventory

    spec, trace = load_proj_spec(proj)
    inv = load_inventory(inventory, tgt["module_dir"])
    ex = extract(ctx, tgt["module_dir"])
    routes = {f"{r['method']} {r['path']}": r for r in ex["routes"]}
    lines = {}
    out = []
    for question, _kind, route in ASK_QUESTIONS:
        r = routes.get(route) if route else None
        cites: list[dict[str, Any]] = []
        if r is not None:
            f = Path(tgt["module_dir"]) / r["handler"]["file"]
            ln = r["handler"]["line"]
            text = f.read_text(errors="replace").split("\n")[ln - 1].strip()
            lines[route] = (r["handler"]["file"], ln)
            cites = [{"target": f"code:{r['handler']['file']}:{ln}", "step": None, "quote": text}]
        label = "implemented_unverified" if r is not None else "not_specified_not_implemented"
        body = {"kind": "yes" if r is not None else "no", "answer": f"{route or 'no such route'}" + (f": {r['auth']} (login required)" if r is not None else ""), "explanation": "scripted", "citations": cites, "implementation": label, "implementation_basis": "static"}  # fmt: skip
        ans = ask(
            spec,
            question,
            trace=trace,
            generator=ScriptedGenerator(
                [json.dumps(body)] * 2
            ),  # the second text answers ask()'s one repair request (a code-only answer has no spec grounding)
            model="stub",
            inventory=inv,
        )
        out.append(
            {
                "question": question,
                "implementation": ans.implementation,
                "family": impl_family(ans.implementation),
                "answer": ans.answer,
                "citations": ans.citations,
                "rejected": ans.rejected_citations,
            }
        )
    return {"digest_has_impl": build_digest(spec, trace, inv).impl is not None, "answers": out}


def tool_mcp(ctx: Ctx, tgt: dict[str, Any], proj: Path, inventory: Path) -> dict[str, Any]:
    """The three implementation tools through the stdio server (``python -m lattice.mcp serve --project``)."""
    p = ctx.scratch / "mcp-proj"
    shutil.rmtree(p, ignore_errors=True)
    shutil.copytree(proj, p)
    (p / "studio.toml").write_text(
        f'[project]\nname = "dn"\n\n[implementation]\ninventory = "{inventory}"\napp_root = "{tgt["module_dir"]}"\npackage = "."\n'
    )
    wf = (
        json.loads((inventory / "workflows.json").read_text())
        if (inventory / "workflows.json").is_file()
        else {"flows": []}
    )
    fid = wf["flows"][0]["id"] if wf["flows"] else "wf-none"
    msgs = [
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]
    calls = [
        (
            "implementation_get",
            {"section": "routes"},
        ),  # the route rows carry the code:<file>:<line> evidence; `summary` and `rules` hold counts and statuses only
        ("workflow_get", {"id": fid}),
        ("workflow_find", {"question": "check in"}),
    ]
    for i, (name, args) in enumerate(calls, 1):
        msgs.append(
            {
                "jsonrpc": "2.0",
                "id": i,
                "method": "tools/call",
                "params": {"name": name, "arguments": args},
            }
        )
    rc, so, se, _ = run(
        [ctx.py, "-m", "lattice.mcp", "serve", "--project", str(p)],
        cwd=p,
        env=ctx.env(),
        stdin=("\n".join(json.dumps(m) for m in msgs) + "\n").encode(),
        timeout=300,
    )
    got: dict[str, Any] = {"rc": rc, "stderr": se[-300:]}
    by_id = {}
    for ln in so.splitlines():
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if "id" in d:
            by_id[d["id"]] = d
    for i, (name, _a) in enumerate(calls, 1):
        res = by_id.get(i, {}).get("result", {})
        got[name] = (
            "".join(c.get("text", "") for c in res.get("content", ()))
            if not res.get("isError")
            else None
        )
    return got


def tool_perf(ctx: Ctx, tgt: dict[str, Any], proj: Path) -> dict[str, Any]:
    """``lattice perf measure`` on D1 over MySQL: one budget (GET /v1/home as ann, habits seeded through the API), generous so the row asks only that it runs."""
    h = f"tests.spec.{tgt['module_dir'].name}_harness"
    (proj / "studio.toml").write_text(
        f'[project]\nname = "dn"\n\n[app]\napp = "{h}:make_app"\ndb = "{h}:build_db"\nruntime = "{h}:RUNTIME"\ndatabase = "mysql"\n'
    )
    seed = {
        "items": "N",
        "steps": [
            {
                "method": "POST",
                "path": "/v1/habits",
                "user": "ann",
                "count": "N",
                "json": {"id": "{uuid}", "name": "habit-{i}", "period": "week", "target_count": 3},
            }
        ],
    }
    (proj / "perf.budget.json").write_text(
        json.dumps(
            {
                "version": 1,
                "budgets": [
                    {
                        "id": "home",
                        "endpoint": "GET /v1/home",
                        "user": "ann",
                        "seed": seed,
                        "sizes": [5, 25],
                        "queries": {"constant": 1000, "per_item": 1000},
                        "latency_ms": None,
                    }
                ],
            }
        )
    )
    r = json_cli(
        ctx,
        ["lattice.perf", "measure", "--project", str(proj), "--json", "--runs", "3"],
        cwd=proj,
        env=ctx.env_real(PYTHONPATH=os.pathsep.join([str(proj), ctx.pythonpath()])),
        timeout=1800,
    )
    docs = r["json"] if isinstance(r["json"], list) else (r["json"] or {}).get("reports", [])
    return {
        "rc": r["rc"],
        "reports": [
            {"budget_id": d.get("budget_id"), "verdict": d.get("verdict"), "fit": d.get("fit")}
            for d in docs
        ],
        "stderr": r["stderr"][-300:],
    }


def tool_contracts_live(ctx: Ctx, tgt: dict[str, Any], proj: Path) -> dict[str, Any]:
    """A workspace of the project (Streak's spec, D1's harness) as provider with a live link, and a consumer relying on three of its endpoints."""
    h = f"tests.spec.{tgt['module_dir'].name}_harness"
    w = ctx.scratch / "ws-live"
    shutil.rmtree(w, ignore_errors=True)
    shutil.copytree(proj, w / "prov")
    (w / "cons").mkdir()
    imports = [
        {
            "provider": "prov",
            "endpoint": "POST /v1/auth/login",
            "status": [200],
            "auth": "public",
            "request_fields": ["email", "password"],
            "response_fields": ["access_token"],
        },
        {
            "provider": "prov",
            "endpoint": "GET /v1/habits",
            "status": [200],
            "auth": "token",
            "as_user": "ann",
        },
    ]
    (w / "cons" / "lattice.imports.toml").write_text(
        "\n\n".join(
            "[[import]]\n" + "\n".join(f"{k} = {json.dumps(v)}" for k, v in i.items())
            for i in imports
        )
        + "\n"
    )
    (w / "lattice.workspace.toml").write_text(
        f'[workspace]\nname = "w"\n\n[[project]]\nname = "prov"\npath = "prov"\nrole = "provider"\nspec = "spec:SPEC"\napp = "{h}:make_app"\ndb = "{h}:build_db"\nlogin = "{h}:LOGIN"\n'
        # the live child puts the repository root first on PYTHONPATH, so a harness outside the repository (D1's overlay) is named by `src`
        f"src = {json.dumps(os.path.relpath(Path(tgt['overlay']).resolve(), (w / 'prov').resolve()))}\n\n"
        '[[project]]\nname = "cons"\npath = "cons"\nrole = "consumer"\n\n[[link]]\nfrom = "cons"\nto = "prov"\nkind = "uses"\ncontract = "live"\n'
    )
    env = ctx.env_real(PYTHONPATH=os.pathsep.join([str(w / "prov"), ctx.pythonpath()]))
    pin = json_cli(
        ctx,
        ["lattice.contracts", "pin", "cons", "--workspace", str(w), "--json"],
        cwd=w,
        env=env,
        timeout=900,
    )
    chk = json_cli(
        ctx,
        ["lattice.contracts", "check", "cons", "--workspace", str(w), "--json"],
        cwd=w,
        env=env,
        timeout=1800,
    )
    doc = chk["json"] or {}
    findings = [f for link in doc.get("links", ()) for f in link.get("findings", ())]
    bad = [f for f in findings if str(f.get("class", "")).lower() in ("broken", "violation")]
    return {
        "rc": chk["rc"],
        "pin_rc": pin["rc"],
        "violations": len(bad),
        "findings": findings[:10],
        "stderr": chk["stderr"][-300:],
    }


def tool_improve(ctx: Ctx, tgt: dict[str, Any], proj: Path) -> dict[str, Any]:
    """``lattice improve report`` twice over the project's event log (empty here: no Studio run exists for D1), byte for byte."""
    (proj / "studio.toml").write_text('[project]\nname = "dn"\n')
    outs = [
        json_cli(ctx, ["lattice.improve", "report", "--project", str(proj), "--json"], cwd=proj)
        for _ in range(2)
    ]
    return {
        "rc": max(o["rc"] for o in outs),
        "identical": outs[0]["stdout"] == outs[1]["stdout"],
        "event_log": "empty (no Studio run on D1)",
        "stderr": outs[0]["stderr"][-200:],
    }


def tool_rebuild(ctx: Ctx, tgt: dict[str, Any], proj: Path) -> dict[str, Any]:
    """The Studio rebuild loop on a git copy of D1's module with ``[rebuild] agent = "stub"`` (round 1 writes ``built.txt``) and the gate ``dotnet build && check``."""
    from lattice.studio.rebuild import Rebuilder

    repo = ctx.scratch / "rebuild-repo"
    shutil.rmtree(repo, ignore_errors=True)
    shutil.copytree(tgt["module_dir"], repo, ignore=shutil.ignore_patterns("bin", "obj"))
    (repo / "gate.sh").write_text(
        f"#!/bin/sh\ndotnet build {tgt['project']} -c Release -p:RestoreLockedMode=true -o \"$TMPDIR/dn-rebuild-out\" --nologo -v q >/dev/null || {{ echo 'FAIL: build'; exit 1; }}\n[ -f built.txt ] && {{ echo 'PASS: 0 findings'; exit 0; }}\necho 'FAIL: built.txt is missing (scenario:built)'; exit 1\n"
    )
    (repo / "gate.sh").chmod(0o755)
    (repo / "studio.toml").write_text(
        '[project]\nname = "dn"\n\n[rebuild]\nrepo = "."\ngates = ["./gate.sh"]\nmax_rounds = 2\nagent = "stub"\n'
    )
    (repo / "stub-rebuild.json").write_text(
        json.dumps({"rounds": [{"files": {"built.txt": "ok\n"}, "summary": "scripted"}]})
    )
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True, capture_output=True, env=env)
    cfg = tomllib.loads((repo / "studio.toml").read_text())
    rb = Rebuilder(repo, cfg["rebuild"], name="dn", model="stub")
    change = {"id": "c1", "request": "build", "status": "accepted", "rules": {"added": [], "changed": [], "removed": []}, "markdown_diff": "", "checks": {"impact": {"verdict": "fail", "failing": [{"target": "scenario:built", "label": "not_built_yet", "message": "built.txt"}]}}}  # fmt: skip
    rid = rb.start([change], max_rounds=2)["id"]
    deadline = time.time() + 900
    rec: dict[str, Any] = {}
    while time.time() < deadline:
        rec = rb.get(rid, wait=5)
        if rec.get("status") in ("passed", "gave_up", "stopped", "interrupted"):
            break
    rounds = rec.get("rounds", [])
    return {
        "status": rec.get("status"),
        "rounds": [
            {"n": r.get("n"), "outcome": r.get("outcome") or r.get("status")} for r in rounds
        ],
        "gate_ran": any(r.get("gates") for r in rounds),
        "commit": rec.get("commit"),
        "record_keys": sorted(rec)[:20],
    }


# ---- helpers shared by the rows -------------------------------------------------------------------------------------------------------------------


def row(pid: str, probe: str, observed: Any, passed: bool, **ev: Any) -> dict[str, Any]:
    return result(pid, probe, P[pid][2], observed, bool(passed), **ev)


def extract(
    ctx: Ctx, root: Path, *, policy: bool = False, workflow: bool = False
) -> dict[str, Any]:
    """``lattice.dncheck.extract_json`` on ``root`` (read-only on the target), cached per (root, flags)."""
    from lattice import dncheck

    key = f"x:{Path(root).resolve()}:{policy}:{workflow}"
    if key not in ctx.cache:
        ctx.cache[key] = dncheck.extract_json(root, policy=policy, workflow=workflow)
    return ctx.cache[key]  # type: ignore[no-any-return]


def raw_extract(
    ctx: Ctx, root: Path, *, env: Mapping[str, str] | None = None, workflow: bool = False
) -> bytes:
    """The helper's stdout bytes for ``root`` (what DET-* compares), in an environment of its own."""
    from lattice import dncheck

    dll = dncheck.build_dncheck()
    payload = json.dumps(
        {
            "root": str(Path(root).resolve()),
            "sdk": dncheck.sdk_version(),
            "workflow": workflow,
            "policy": True,
        }
    )
    proc = subprocess.run(
        ["dotnet", str(dll), "extract"],
        input=payload.encode(),
        capture_output=True,
        check=True,
        env={**ctx.env(PATH=real_path()), **(env or {})},
        timeout=300,
    )  # noqa: S603
    return proc.stdout


def fixture_truth() -> dict[str, Any]:
    return json.loads(FIXTURE_TRUTH.read_text())  # type: ignore[no-any-return]


def d1_truth() -> dict[str, Any]:
    return json.loads(D1_TRUTH.read_text())  # type: ignore[no-any-return]


# ---- Stage 1, Arm 0: the extract of dnparity1 against its truth (exact) ----------------------------------------------------------------------------------


def p0_routes(ctx: Ctx) -> dict[str, Any]:
    t, fx = fixture_truth(), extract(ctx, FIXTURE)
    tm = {rkey(r["method"], r["template"]): r for r in t["routes"]}
    em = {rkey(r["method"], r["path"]): r for r in fx["routes"]}
    diffs = {
        rlabel(k): [
            [a["style"], a["condition"], a["path_template"], a["template"]],
            [b["style"], b["condition"], b["path_template"], b["path"]],
        ]
        for k in tm
        if k in em
        for a, b in [(tm[k], em[k])]
        if [a["style"], a["condition"], a["path_template"], a["template"]]
        != [b["style"], b["condition"], b["path_template"], b["path"]]
    }
    status = {
        rlabel(k): [tm[k]["success"], em[k]["status_codes"]]
        for k in tm
        if k in em and tm[k]["success"] != em[k]["status_codes"]
    }
    c = cmp_multi(sorted(tm), sorted(em))
    return row(
        "P0-ROUTES",
        "routes of dnparity1 vs truth (method, full template, style, condition, path_template, success)",
        {
            "routes_truth": len(tm),
            "routes_extract": len(em),
            "set": c,
            "field_differences": diffs,
            "status_differences": status,
        },
        c["equal"] and not diffs and not status,
    )


def p0_auth(ctx: Ctx) -> dict[str, Any]:
    t, fx = fixture_truth(), extract(ctx, FIXTURE)
    tm = {rkey(r["method"], r["template"]): r["auth"] for r in t["routes"]}
    em = {rkey(r["method"], r["path"]): r["auth"] for r in fx["routes"]}
    wrong = {rlabel(k): [tm[k], em.get(k)] for k in tm if tm[k] != em.get(k)}
    return row("P0-AUTH", "auth per route", {"routes": len(tm), "wrong": wrong, "values": sorted(set(em.values()))}, not wrong and tm.keys() == em.keys(), auth_object={"scheme": [t["auth"]["scheme"], fx["auth"]["scheme"]], "caller_keys": [t["auth"]["caller_keys"], fx["auth"]["caller_keys"]], "admin_role": [t["auth"]["admin_role"], fx["dotnet"]["auth"]["admin_role"]]})  # fmt: skip


def p0_excl(ctx: Ctx) -> dict[str, Any]:
    t, fx = fixture_truth(), extract(ctx, FIXTURE)
    ex = cmp_multi(
        [(x["kind"], x["file"], x["line"]) for x in t["excluded"]],
        [(x["kind"], x["file"], x["line"]) for x in fx["excluded"]],
    )
    un = cmp_multi(
        [(x["file"], x["line"]) for x in t["unread"] if x["category"] == "route"],
        [(x["file"], x["line"]) for x in fx["unread"] if x.get("category") == "route"],
    )
    return row(
        "P0-EXCL",
        "excluded and route-level unread entries",
        {"excluded": ex, "unread_routes": un},
        ex["equal"] and un["equal"],
    )


def p0_bind(ctx: Ctx) -> dict[str, Any]:
    t, fx = fixture_truth(), extract(ctx, FIXTURE)
    by = {rkey(r["method"], r["path"]): r for r in fx["routes"]}
    params, bounds = {}, {}
    for r in t["routes"]:
        k = rkey(r["method"], r["template"])
        e = by[k]
        want = sorted(
            (p["name"], p["source"], p.get("type"), p.get("parse"))
            for p in r["params"]
            if not p.get("dropped") and p["source"] != "body"
        )
        got = sorted(
            (p["name"], p["bind_source"], p["type"], p.get("parse"))
            for p in e["params"]
            if p["source"] != "body"
        )
        wb = sorted(
            (
                b["field"],
                b["required"] or None,
                b["ge"],
                b["le"],
                b["min_length"],
                b["max_length"],
                b["enforced_by"],
            )
            for b in t["bounds"]
            if route_id_key(b["route"]) == k
        )
        gb = sorted(
            (
                p["name"],
                p["required"] or None,
                p["ge"],
                p["le"],
                p["min_length"],
                p["max_length"],
                p["enforced_by"],
            )
            for p in e["params"]
            if p["source"] == "body"
            and (
                p["required"]
                or any(p[f] is not None for f in ("ge", "le", "min_length", "max_length"))
            )
        )
        if want != got:
            params[rlabel(k)] = {"want": want, "got": got}
        if wb != gb:
            bounds[rlabel(k)] = {"want": wb, "got": gb}
    uk = lambda x: (route_id_key(x["route"]), x["file"], x["line"], x["what"], x["reason"])  # noqa: E731
    un = cmp_multi(
        [uk(x) for x in t["unread"] if x["category"] == "bind"],
        [uk(x) for x in fx["unread"] if x.get("category") == "bind"],
    )
    status = [t["bind_status"], fx["dotnet"]["bind_status"]]
    return row("P0-BIND", "params, sources, bounds, enforced_by, bind status, 'declared, not enforced' unread", {"param_differences": params, "bound_differences": bounds, "unread_bind": un, "bind_status [truth, extract]": status}, not params and not bounds and un["equal"] and status == [400, 400])  # fmt: skip


def p0_tables(ctx: Ctx) -> dict[str, Any]:
    t, fx = fixture_truth(), extract(ctx, FIXTURE)
    tt, et = {x["table"]: x for x in t["tables"]}, {x["table"]: x for x in fx["tables"]}
    diffs: list[str] = []
    pairs = [("csharp_type", "go_type"), ("wire", "wire"), ("private", "private"), ("nullable", "nullable"), ("unique", "unique"), ("pk", "primary_key"), ("max_length", "max_length"), ("fk", "foreign_key"), ("default", "default"), ("enum_storage", "enum_storage"), ("choices", "choices")]  # fmt: skip
    for name, tb in tt.items():
        e = et.get(name)
        if e is None:
            continue
        if tb["entity"] != e["struct"]:
            diffs.append(f"{name}: entity {tb['entity']} vs {e['struct']}")
        if [c["name"] for c in tb["columns"]] != [c["name"] for c in e["columns"]]:
            diffs.append(f"{name}: columns differ")
            continue
        for c, d in zip(tb["columns"], e["columns"], strict=True):
            diffs += [
                f"{name}.{c['name']}.{tf}: {c[tf]!r} vs {d[ef]!r}"
                for tf, ef in pairs
                if c[tf] != d[ef]
            ]
        if [u["columns"] for u in tb["unique_together"]] != e["unique_together"]:
            diffs.append(f"{name}: unique_together")
    s = cmp_multi(sorted(tt), sorted(et))
    un = cmp_multi(
        [(x["file"], x["line"]) for x in t["unread"] if x["category"] == "table"],
        [(x["file"], x["line"]) for x in fx["unread"] if x.get("category") == "table"],
    )
    return row(
        "P0-TABLES",
        "tables, SQL names, column facts, enum_storage, wire names, unread tables",
        {"tables": s, "fact_differences": diffs[:40], "unread_tables": un},
        s["equal"] and not diffs and un["equal"],
    )


def p0_owner(ctx: Ctx) -> dict[str, Any]:
    t, fx = fixture_truth(), extract(ctx, FIXTURE)

    def via(v: Mapping[str, Any] | None) -> str | None:
        return (
            json.dumps({k: x for k, x in v.items() if x is not None}, sort_keys=True) if v else None
        )

    want = [
        (
            route_id_key(o["route"]),
            o["table"],
            o["column"],
            o["kind"],
            o["file"],
            o["line"],
            o["value"],
            via(o.get("via")),
        )
        for o in t["owner_uses"]
    ]
    got = [
        (
            rkey(r["method"], r["path"]),
            o["model"],
            o["column"],
            o["how"],
            o["file"],
            o["line"],
            o["value"],
            via(o.get("via")),
        )
        for r in fx["routes"]
        for o in r["owner_uses"]
    ]
    uses = cmp_multi(want, got)
    qf = t["dotnet"]["query_filters"][0]
    mine = fx["dotnet"]["query_filters"]
    qfd = [
        [(q["table"], q["column"], q["owner"], q["line"]) for q in mine],
        [(qf["table"], qf["column"], True, qf["line"])],
    ]
    ign = (
        [
            sorted(route_id_key(i["route"]) for i in qf["ignored_by"]),
            sorted(route_id_key(x) for x in mine[0]["ignored_by"]),
        ]
        if mine
        else [[], []]
    )
    return row(
        "P0-OWNER",
        "owner_uses, query_filters and owner_via evidence",
        {
            "owner_uses": uses,
            "query_filter [extract, truth]": qfd,
            "ignored_by [truth, extract]": ign,
        },
        uses["equal"] and qfd[0] == qfd[1] and ign[0] == ign[1],
    )


def p0_state(ctx: Ctx) -> dict[str, Any]:
    t, fx = fixture_truth(), extract(ctx, FIXTURE)
    fr = lambda x: tuple(x) if x else None  # noqa: E731
    w = cmp_multi([(route_id_key(w["route"]), w["table"], w["column"], w["value"], w["stored"], w["kind"], fr(w["from"]), w["file"], w["line"]) for w in t["status_writes"]], [(route_id_key(w["route"]), w["model"], w["column"], w["value"], w["stored"], w["kind"], fr(w["from"]), w["file"], w["line"]) for w in fx["status_writes"]])  # fmt: skip
    f = cmp_multi([(route_id_key(f["route"]), f["table"], f["column"], f["to"], fr(f["from"]), f["file"], f["line"]) for f in t["status_facts"]], [(route_id_key(f["route"]), f["model"], f["column"], f["to"], fr(f["from"]), f["file"], f["line"]) for f in fx["status_facts"]])  # fmt: skip
    guarded = any(w["guard"] for w in fx["status_writes"] if w["kind"] != "initial")
    return row(
        "P0-STATE",
        "status_writes with guards; status_facts",
        {"status_writes": w, "status_facts": f, "a_guarded_write_is_read": guarded},
        w["equal"] and f["equal"] and guarded,
    )


def p0_raise(ctx: Ctx) -> dict[str, Any]:
    t, fx = fixture_truth(), extract(ctx, FIXTURE)
    r = cmp_multi([(route_id_key(x["route"]), x["status"], x["code"], x["detail"], x["kind"], x["sentinel"], x["file"], x["line"]) for x in t["raises"]], [(rkey(rt["method"], rt["path"]), x["status"], x["code"], x["detail"], x["kind"], x.get("sentinel"), x["file"], x["line"]) for rt in fx["routes"] for x in rt["raises"]])  # fmt: skip
    uk = lambda x: (route_id_key(x["route"]), x["file"], x["line"], x["what"], x["reason"])  # noqa: E731
    u = cmp_multi(
        [uk(x) for x in t["unread"] if x["category"] == "raise"],
        [uk(x) for x in fx["unread"] if x.get("category") == "raise"],
    )
    return row(
        "P0-RAISE",
        "raises (status, code, detail, kind, sentinel) per route; unmapped throws unread",
        {"raises": r, "unread_raises": u},
        r["equal"] and u["equal"],
    )


def p0_di(ctx: Ctx) -> dict[str, Any]:
    t, fx = fixture_truth(), extract(ctx, FIXTURE)
    td, ed = t["di"], fx["dotnet"]["di"]
    regs = cmp_multi(
        [
            (r["interface"], r["implementation"], r["lifetime"], r["file"], r["line"])
            for r in td["registrations"]
        ],
        [
            (r["interface"], r["implementation"], r["lifetime"], r["file"], r["line"])
            for r in ed["registrations"]
        ],
    )
    calls = cmp_multi(
        [
            (route_id_key(c["route"]), c["call"], c["resolved_to"], c["file"], c["line"])
            for c in td["resolved_calls"]
        ],
        [
            (route_id_key(c["route"]), c["call"], c["resolved_to"], c["file"], c["line"])
            for c in ed["resolved_calls"]
        ],
    )
    uk = lambda x: (route_id_key(x["route"]), x["file"], x["line"], x["what"], x["reason"])  # noqa: E731
    un = cmp_multi(
        [uk(x) for x in t["unread"] if x["category"] == "di"],
        [uk(x) for x in fx["unread"] if x.get("category") == "di"],
    )
    single = [
        sorted(td["single"]) == sorted(ed["single"]),
        sorted(td["multiple"]) == sorted(ed["multiple"]),
    ]
    return row(
        "P0-DI",
        "DI registrations, resolved calls, the two-registration call unread",
        {
            "registrations": regs,
            "resolved_calls": calls,
            "unread_di": un,
            "single/multiple equal": single,
        },
        regs["equal"] and calls["equal"] and un["equal"] and all(single),
    )


def p0_json(ctx: Ctx) -> dict[str, Any]:
    t, fx = fixture_truth(), extract(ctx, FIXTURE, policy=True)
    tt = {x["table"]: x for x in t["tables"]}
    wrong = []
    for tb in fx["tables"]:
        for c in tb["columns"]:
            want = next(x["wire"] for x in tt[tb["table"]]["columns"] if x["name"] == c["name"])
            if c["wire"] != want or c["json"] != (want if want is not None else "-"):
                wrong.append(f"{tb['table']}.{c['name']}: {c['wire']!r}/{c['json']!r} vs {want!r}")
    parse = [p for r in fx["routes"] for p in r["params"] if p.get("parse")]
    bind = any(x.get("bind") for r in fx["routes"] for x in r["raises"])
    naming = [fx["dotnet"]["json_naming"], t["json_naming"]]
    return row("P0-JSON", "dotnet.json_naming and every wire name (policy extract)", {"json_naming [extract, truth]": naming, "wrong_wire": wrong, "parse_params": len(parse), "bind_refusals_present": bind}, naming == ["SnakeCaseLower", "SnakeCaseLower"] and not wrong and bool(parse) and all(p["parse"] == "Int" for p in parse) and bind)  # fmt: skip


# ---- determinism ------------------------------------------------------------------------------------------------------------------------------


def det_10(ctx: Ctx) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, root in (
        ("dnparity1", FIXTURE),
        ("D1", ctx.d1["module_dir"] if ctx.d1 else FIXTURE),
    ):
        digests = [sha_bytes(raw_extract(ctx, root)) for _ in range(ctx.runs)]
        out[label] = {"identical": digests.count(digests[0]), "of": ctx.runs}
    return row(
        "DET-10",
        f"extract {ctx.runs}x on dnparity1 and D1, byte for byte",
        out,
        all(v["identical"] == v["of"] for v in out.values()),
    )


def det_restored(ctx: Ctx) -> dict[str, Any]:
    tgt = ctx.d1
    assert tgt is not None and tgt.get("build_src")
    built = Path(tgt["build_src"])
    has = {"obj": (built / "obj").is_dir(), "bin": (built / "bin").is_dir()}
    clean = raw_extract(ctx, tgt["module_dir"])
    after = raw_extract(ctx, built)
    return row(
        "DET-RESTORED",
        "extract on a D1 copy after dotnet build (obj/ and bin/ present) vs the clean module",
        {"copy_has": has, "identical": clean == after, "sha256": sha_bytes(clean)[:16]},
        clean == after and has["obj"],
    )


def det_path(ctx: Ctx) -> dict[str, Any]:
    tgt = ctx.d1
    assert tgt is not None
    base = raw_extract(ctx, tgt["module_dir"])
    res: dict[str, Any] = {}
    for i, rel in enumerate(("x/one", "yy/zzz/another-name")):
        dst = ctx.scratch / "detpath" / rel
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(tgt["module_dir"], dst, ignore=shutil.ignore_patterns("bin", "obj"))
        res[f"path{i}"] = raw_extract(ctx, dst) == base
    res["LANG=de_DE.UTF-8"] = (
        raw_extract(ctx, tgt["module_dir"], env={"LANG": "de_DE.UTF-8", "LC_ALL": "de_DE.UTF-8"})
        == base
    )
    res["no absolute path in the output"] = (
        str(ctx.scratch).encode() not in base and str(REPO).encode() not in base
    )
    return row(
        "DET-PATH",
        "extract on D1 copied to two other absolute paths, and under LANG=de_DE.UTF-8",
        res,
        all(res.values()),
    )


# ---- Stage 1, D1 (dev) -----------------------------------------------------------------------------------------------------------------------------


def d1_ex(ctx: Ctx, policy: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    assert ctx.d1 is not None
    return ctx.d1["truth"], extract(ctx, ctx.d1["module_dir"], policy=policy)


def d_routes(ctx: Ctx) -> dict[str, Any]:
    t, ex = d1_ex(ctx)
    s = score_routes(t, ex)
    return row(
        "D-ROUTES",
        "D1 routes vs truth (4.5 matching)",
        s,
        s["recall"] == 1.0 and not s["extra"] and not s["style_wrong"],
    )


def d_auth(ctx: Ctx) -> dict[str, Any]:
    t, ex = d1_ex(ctx)
    s = score_auth(t, ex)
    return row("D-AUTH", "D1 auth per route", s, s["share_of_truth"] >= 0.95)


def d_tables(ctx: Ctx) -> dict[str, Any]:
    t, ex = d1_ex(ctx)
    s = score_tables(t, ex)
    return row(
        "D-TABLES",
        "D1 tables and column facts",
        s,
        s["found"] == s["truth_tables"] == 9 and s["fact_accuracy"] >= 0.95,
    )


def d_wire(ctx: Ctx) -> dict[str, Any]:
    t, ex = d1_ex(ctx, policy=True)
    s = score_wire(t, ex)
    return row("D-WIRE", "D1 wire names vs truth", s, s["share"] >= 0.98)


def d_own(ctx: Ctx) -> dict[str, Any]:
    t, ex = d1_ex(ctx)
    s = score_own(t, ex)
    return row("D-OWN", "D1 owner evidence on the truth's owner-scoped routes", s, s["share"] >= 0.85 and not s["evidence_on_tables_truth_says_are_not_owned"], reading="owner-scoped = the non-Public truth routes with a refusal of cause not_owned or not_found (the truth schema has no per-route table); the share counts an owner_uses entry or an owner query filter that applies to the route")  # fmt: skip


def d_raise(ctx: Ctx) -> dict[str, Any]:
    t, ex = d1_ex(ctx)
    s = score_raise(t, ex)
    return row(
        "D-RAISE", "D1 refusals (route, status)", s, s["precision"] >= 0.95 and s["recall"] >= 0.70
    )


def d_unread(ctx: Ctx) -> dict[str, Any]:
    t, ex = d1_ex(ctx)
    s = score_unread(t, ex)
    return row(
        "D-UNREAD", "every D1 truth route is a route or named in unread/excluded", s, s["n"] == 0
    )


def d_x(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    raw_extract(ctx, ctx.d1["module_dir"])  # the helper is built and warm
    t0 = time.perf_counter()
    raw_extract(ctx, ctx.d1["module_dir"])
    wall = time.perf_counter() - t0
    return row(
        "D-X", "extract wall time on D1 (helper prebuilt)", {"wall_s": round(wall, 2)}, wall <= 60
    )


# ---- patches on scratch copies (D1's DM1-DM6, the held-out HM1-HM16) ------------------------------------------------------------------------------


IGNORED = (".DS_Store", "__pycache__")


def tree_files(root: Path) -> dict[str, str]:
    """relative path -> sha256 of every regular file under ``root`` (junk names skipped)."""
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not any(part in IGNORED for part in p.relative_to(root).parts):
            out[p.relative_to(root).as_posix()] = sha(p)
    return out


def tree_sha256(root: Path) -> str:
    h = hashlib.sha256()
    for rel, digest in tree_files(root).items():
        h.update(rel.encode() + b"\0" + digest.encode() + b"\0")
    return h.hexdigest()


def apply_patch(copy: Path, patch: Path) -> dict[str, Any]:
    """``patch -p1`` from inside ``copy``. A reversed or already-applied hunk, a reject or a nonzero exit is a failure."""
    before = tree_files(copy)
    proc = subprocess.run(
        ["patch", "-p1", "-N", "-V", "none", "-i", str(patch)],
        cwd=copy,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    out = proc.stdout + proc.stderr
    rej = sorted(
        p.relative_to(copy).as_posix() for p in copy.rglob("*") if p.suffix in (".rej", ".orig")
    )
    bad = (
        proc.returncode != 0
        or rej
        or re.search(r"reversed|previously applied|hunks? (?:FAILED|failed|ignored)", out)
    )
    after = tree_files(copy)
    return {
        "ok": not bad,
        "returncode": proc.returncode,
        "output": out[-1200:],
        "leftovers": rej,
        "files_changed": sorted(
            k for k in set(before) | set(after) if before.get(k) != after.get(k)
        ),
    }


def copy_module(src: Path, dest: Path) -> None:
    shutil.rmtree(dest.parent if dest.parent != dest else dest, ignore_errors=True)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("bin", "obj", *IGNORED))


# ---- judging a mutant: layer A (DN8xx on a route) and layer R (an item flips) -- shared by A-DM and the mutants runner -------------------------------------

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def key_route(key: str) -> tuple[str, str] | None:
    """``DN801|GET /v1/habits/{id}|Habit`` -> (GET, /v1/habits/{}) (4.5 normalisation)."""
    parts = key.split("|")
    if len(parts) < 2:
        return None
    method, _, path = parts[1].partition(" ")
    return rkey(method, path) if method in METHODS and path else None


def item_parts(item: str) -> tuple[str, tuple[str, str], int | None] | None:
    """``R3 POST /x/{id} 404 Not found`` -> (R3, (POST, /x/{}), 404); None when it is no per-route item."""
    toks = item.split(" ")
    if len(toks) < 3 or toks[1] not in METHODS:
        return None
    return (
        toks[0],
        rkey(toks[1], toks[2]),
        int(toks[3]) if len(toks) > 3 and toks[3].isdigit() else None,
    )


def a_hits(
    findings: Sequence[Mapping[str, Any]],
    code: str,
    route: tuple[str, str],
    columns: set[str] | None = None,
) -> list[str]:
    out = []
    for f in findings:
        key = f["key"]
        if f.get("code", key.split("|")[0]) != code or key_route(key) != route:
            continue
        if code == "DN802" and columns is not None and key.split("|")[-1] not in columns:
            continue
        out.append(key)
    return out


def r_items(
    items: Sequence[str], code: str, route: tuple[str, str], statuses: Sequence[int] = ()
) -> list[str]:
    out = []
    for i in sorted(set(items)):
        p = item_parts(i)
        if p and p[0] == code and p[1] == route and (code != "R3" or p[2] in statuses):
            out.append(i)
    return out


def new_findings(
    orig: Sequence[Mapping[str, Any]], mutant: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """The mutant's findings whose (code, key) the original does not have."""
    have = {(f["code"], f["key"]) for f in orig}
    return [dict(f) for f in mutant if (f["code"], f["key"]) not in have]


def eval_a(
    code: str,
    route: tuple[str, str],
    orig: Sequence[Mapping[str, Any]],
    new: Sequence[Mapping[str, Any]],
    columns: set[str] | None = None,
) -> dict[str, Any]:
    dirty = a_hits(orig, code, route, columns)
    hits = a_hits(new, code, route, columns)
    return {
        "detector": f"{code} on {rlabel(route)}",
        "layer": "A",
        "armed": not dirty,
        "flipped": not dirty and bool(hits),
        "hits": hits,
        "control_findings": dirty,
    }


def eval_r(
    code: str,
    route: tuple[str, str],
    ctl: Mapping[str, str],
    mut: Mapping[str, str] | None,
    statuses: Sequence[int] = (),
    unstable: Sequence[str] = (),
) -> dict[str, Any]:
    items = r_items([*ctl, *(mut or {})], code, route, statuses)
    usable = [i for i in items if i not in unstable and ctl.get(i) in VERIFIED]
    hits = [i for i in usable if (mut or {}).get(i) == "refuted"]
    why = (
        "no layer-R data for the mutant"
        if mut is None
        else "the spec has no such item"
        if not items
        else "no such item is verified on the control (or unstable)"
        if not usable
        else ""
        if hits
        else "no such item is refuted on the mutant"
    )
    return {
        "detector": f"{code} {rlabel(route)}"
        + (f" status {list(statuses)}" if code == "R3" else ""),
        "layer": "R",
        "armed": mut is not None and bool(usable),
        "flipped": bool(hits),
        "hits": hits,
        "items [control, mutant]": {i: [ctl.get(i), (mut or {}).get(i)] for i in items},
        "why_not": why,
    }


DEV_MUTANTS: dict[
    str, dict[str, Any]
] = {  # prereg 6.3: id -> route, detector (kind, code[, statuses]), gated
    "DM1": {"route": ("GET", "/v1/habits/{}"), "det": ("A", "DN801"), "gated": True},
    "DM2": {"route": ("PATCH", "/v1/habits/{}"), "det": ("A", "DN801"), "gated": True},
    "DM3": {"route": ("GET", "/v1/me"), "det": ("A", "DN802"), "gated": True},
    "DM4": {"route": ("POST", "/v1/auth/logout"), "det": ("R", "R1"), "gated": True},
    "DM5": {"route": ("GET", "/v1/habits/{}"), "det": ("R", "R3", (404,)), "gated": True},
    "DM6": {"route": None, "det": None, "gated": False},
}


class env_set:  # noqa: N801 -- a context manager
    def __init__(self, name: str | None, value: Path | str | None) -> None:
        self.name, self.value, self.keep = name, value, None

    def __enter__(self) -> None:
        if self.name and self.value is not None:
            self.keep = os.environ.get(self.name)
            os.environ[self.name] = str(self.value)

    def __exit__(self, *a: object) -> None:
        if self.name and self.value is not None:
            if self.keep is None:
                os.environ.pop(self.name, None)
            else:
                os.environ[self.name] = self.keep


def build_copy(ctx: Ctx, tgt: dict[str, Any], src: Path, out: Path) -> tuple[bool, str]:
    """Build the service from a (mutated) module copy to ``out`` with the harness's command."""
    rc, so, se, _ = run(
        [
            "dotnet",
            "build",
            str(src / tgt["project"]),
            "-c",
            "Release",
            "-p:RestoreLockedMode=true",
            "-o",
            str(out),
            "--nologo",
            "-v",
            "q",
        ],
        cwd=src,
        env=ctx.env(PATH=real_path()),
        timeout=1500,
    )
    return rc == 0 and (out / f"{tgt['assembly']}.dll").is_file(), (so + se)[-1200:]


def dev_mutant(ctx: Ctx, mid: str, spec: Any, cand_dir: Path | None) -> dict[str, Any]:
    """One D1 dev mutant: the patch is applied to a scratch copy of D1's module, layer A (spec policy of ``spec``) and, for the layer-R detectors,
    layer R of the unmutated import's candidate spec against the mutant's build, are compared with the original's."""
    assert ctx.d1 is not None
    tgt, plan = ctx.d1, DEV_MUTANTS[mid]
    if plan["det"] is None:
        return {
            "id": mid,
            "gated": False,
            "killed": None,
            "reported": True,
            "why": "no HTTP route: only a scenario driving the nudger sees it (reported, as registered)",
        }
    copy = ctx.scratch / "dm" / mid / tgt["module_dir"].name
    copy_module(tgt["module_dir"], copy)
    ap = apply_patch(copy, D1_MUTANT_DIR / f"{mid.lower()}.diff")
    (copy.parent / "PATCH").write_text(
        sha(D1_MUTANT_DIR / f"{mid.lower()}.diff")
    )  # a marker beside the copy (the stand-ins read it; the real library ignores it)
    if not ap["ok"]:
        return {
            "id": mid,
            "gated": plan["gated"],
            "killed": False,
            "status": "patch_failed",
            "apply": ap,
        }
    kind, code = plan["det"][0], plan["det"][1]
    if kind == "A":
        orig = ctx.lib.layer_a(ctx, tgt["module_dir"], "spec_policy", spec=spec)["findings"]
        mut = ctx.lib.layer_a(ctx, copy, "spec_policy", spec=spec)["findings"]
        d = eval_a(code, plan["route"], orig, new_findings(orig, mut))
        return {
            "id": mid,
            "gated": plan["gated"],
            "killed": d["flipped"],
            "detector": d,
            "apply": {"ok": True, "files": ap["files_changed"]},
        }
    out = ctx.scratch / "dm" / mid / "out"
    ok, err = build_copy(ctx, tgt, copy, out)
    if not ok:
        return {
            "id": mid,
            "gated": plan["gated"],
            "killed": False,
            "status": "build_failed",
            "error": err,
        }
    ctl = seed_map(ctx, tgt)
    with env_set(tgt.get("build_env"), out):
        mut_map = ctx.lib.r_verdicts(ctx, tgt, cand_dir, "seed") if cand_dir else None
    d = eval_r(code, plan["route"], ctl, mut_map, plan["det"][2] if len(plan["det"]) > 2 else ())
    return {
        "id": mid,
        "gated": plan["gated"],
        "killed": d["flipped"],
        "detector": d,
        "apply": {"ok": True, "files": ap["files_changed"]},
    }


def a_dm(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    spec = streak_spec(ctx)
    cand = import_dir(ctx, ctx.d1)
    res = {m: dev_mutant(ctx, m, spec, cand) for m in ("DM1", "DM2", "DM3", "DM4")}
    return row("A-DM", "D1 dev mutants DM1-DM4 each killed by its registered detector", {m: {"killed": r.get("killed"), "detector": (r.get("detector") or {}).get("detector"), "why": (r.get("detector") or {}).get("why_not") or r.get("status")} for m, r in res.items()}, all(r.get("killed") is True for r in res.values()), evidence=res)  # fmt: skip


# ---- Stage 2: the front end (node B2) ---------------------------------------------------------------------------------------------------------------


def detect_cases(ctx: Ctx) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """(.NET cases with absolute roots, the cases every tree answers). The two scratch projects are written here."""
    web = ctx.scratch / "detect" / "webonly"
    two = ctx.scratch / "detect" / "twoproj"
    for d in (web, two):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
    (web / "web.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup></Project>\n'
    )
    (web / "Program.cs").write_text(
        'var app = WebApplication.CreateBuilder().Build();\napp.MapGet("/", () => 1);\napp.Run();\n'
    )
    for n in ("a", "b"):
        (two / f"{n}.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup>'
            '<ItemGroup><PackageReference Include="Microsoft.EntityFrameworkCore" Version="9.0.0" /></ItemGroup></Project>\n'
        )
    d1 = ctx.d1["module_dir"] if ctx.d1 else FIXTURE
    dn = [
        {"id": "D1", "root": str(d1), "package": "."},
        {"id": "dnparity1", "root": str(FIXTURE), "package": "."},
        {"id": "web-without-efcore", "root": str(web), "package": "."},
        {"id": "two-csproj", "root": str(two), "package": "."},
    ]
    others = [
        {"id": "go:goginapp", "root": "tests/spec", "package": "goginapp"},
        {"id": "go:goapp", "root": "tests/spec", "package": "goapp"},
        {"id": "py:django", "root": ".", "package": "bench.import_repos.snippets"},
        {"id": "py:flask", "root": ".", "package": "bench.import_repos.microblog"},
        {"id": "py:fastapi", "root": ".", "package": "bench.import_repos.multivendor.app"},
    ]
    return dn, others


def f_detect(ctx: Ctx) -> dict[str, Any]:
    dn, others = detect_cases(ctx)
    got = ctx.lib.select_names(ctx, dn, REPO)
    now = ctx.lib.select_names(ctx, others, REPO)
    base = ctx.lib.select_names(ctx, others, ctx.base_dir) if ctx.base_dir else None
    want = {"D1": "aspnetcore", "dnparity1": "aspnetcore"}
    ok_dn = (
        all(got.get(k) == v for k, v in want.items())
        and got["web-without-efcore"].startswith("LookupError")
        and got["two-csproj"].startswith("LookupError")
        and "csproj" in got["two-csproj"]
    )
    same = base is not None and base == now
    return row(
        "F-DETECT",
        "select(None, root, package) on D1, dnparity1, a Sdk.Web project without EF Core, a two-csproj directory; Python and Go fixtures unchanged",
        {"dotnet": got, "others_now": now, "others_base": base, "others_unchanged": same},
        ok_dn and same,
    )


def f_model(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    t, ex = ctx.d1["truth"], extract(ctx, ctx.d1["module_dir"])
    v = ctx.lib.model_view(ctx, ctx.d1)
    lg = t["login"]
    checks = {
        "framework/orm": [v["framework"], v["orm"]] == ["aspnetcore", "efcore"],
        "routes": v["routes"] == len(ex["routes"]),
        "tables": v["tables"] == len(ex["tables"]),
        "bodies are models": bool(v["models"]),
        "login path": v["login_path"] == lg["path"],
        "login fields": {v["username_field"], v["password_field"]} <= set(lg["fields"]),
        "token field": (v["token_path"] or [None])[-1] == lg["token_field"],
    }
    return row(
        "F-MODEL",
        "model_from_extract on D1: counts, DTO body models, framework/orm, login path, fields and token field",
        {"view": v, "checks": checks},
        all(checks.values()),
    )


def f_rt(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    v = ctx.lib.runtime_view(ctx, ctx.d1)
    w, wo = v["with_harness"], v["without_harness"]
    checks = {
        "harness runtime is the harness's": w["is_harness_runtime"],
        "framework dotnet (harness)": w["framework"] == "dotnet",
        "framework dotnet (bare)": wo["framework"] == "dotnet",
        "one server": wo["one_server"] is True,
    }
    return row(
        "F-RT",
        "runtime() with the harness and without it",
        {"view": v, "checks": checks},
        all(checks.values()),
    )


def f_recon(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    res = ctx.lib.reconcile(ctx, ctx.d1)
    routes = res["routes"]
    bad = sorted(k for k, v in routes.items() if not v["observed"])
    ctl = sorted(k for k, v in routes.items() if v.get("control") != 404)
    n_truth = len(ctx.d1["truth"]["routes"])
    return row(
        "F-RECON",
        "reconcile on D1 over MySQL 8.4 (Docker): every extracted route observed, control 404, no leftover container",
        {
            "routes": len(routes),
            "truth_routes": n_truth,
            "not_observed": bad,
            "control_not_404": ctl,
            "containers_left": res["containers_left"],
        },
        len(routes) >= n_truth and not bad and not ctl and res["containers_left"] == 0,
    )


LAZY_CODE = """
import sys
name = sys.argv[1]
from pathlib import Path
from lattice.importer import frontends
if name == "fastapi":
    import lattice.importer.frontends.fastapi
elif name == "flask":
    frontends.select("flask")
else:
    frontends.select("gin")
import lattice.importer.suite
pool = ""
try:
    from hypothesis.internal.conjecture import providers
    c = providers._get_local_constants()
    pool = repr(sorted(map(repr, c.strings))) + repr(sorted(c.integers))
except Exception as e:
    pool = "unavailable:" + type(e).__name__
import hashlib
print(("lattice.importer.frontends.dotnet_aspnet" in sys.modules), hashlib.sha256(pool.encode()).hexdigest()[:16])
"""


def pb_lazy(ctx: Ctx) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in ("fastapi", "flask", "go"):
        r = []
        for tree in (ctx.base_dir or REPO, REPO):
            rc, so, se, _ = run(
                [ctx.py, "-c", LAZY_CODE, name],
                cwd=tree,
                env=ctx.env(PYTHONPATH=str(tree)),
                timeout=300,
            )
            r.append((rc, so.strip().split()[-2:] if so.strip() else se[-200:]))
        loaded, pool_now = (r[1][1] + ["?", "?"])[:2] if isinstance(r[1][1], list) else ("?", "?")
        pool_base = r[0][1][1] if isinstance(r[0][1], list) and len(r[0][1]) > 1 else None
        out[name] = {
            "dotnet_aspnet_loaded": loaded,
            "pool_now": pool_now,
            "pool_base": pool_base,
            "rc": [r[0][0], r[1][0]],
        }
    ok = all(
        v["rc"] == [0, 0]
        and v["dotnet_aspnet_loaded"] == "False"
        and v["pool_now"] == v["pool_base"]
        for v in out.values()
    )
    return row(
        "PB-LAZY",
        "after a FastAPI, a Flask and a Go front-end import: dotnet_aspnet not in sys.modules; Hypothesis's literal pool equal to the base's",
        out,
        ok,
    )


# ---- Stage 3: layer A (node B3) ---------------------------------------------------------------------------------------------------------------------


def streak_spec(ctx: Ctx) -> Any:
    """Streak's ``spec.py`` (from the ``git archive`` the runner is given, read only) loaded as a module."""
    if "streak_spec" not in ctx.cache:
        assert ctx.streak_src is not None
        sp = importlib.util.spec_from_file_location(
            f"streak_spec_{abs(hash(str(ctx.streak_src)))}", ctx.streak_src / "spec.py"
        )
        assert sp and sp.loader
        mod = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(mod)
        ctx.cache["streak_spec"] = mod
    return ctx.cache["streak_spec"]


def keys_of(found: Mapping[str, Any]) -> list[str]:
    return sorted(f["key"] for f in found["findings"])


def a0_dn(ctx: Ctx) -> dict[str, Any]:
    t = fixture_truth()
    fnd = ctx.lib.layer_a(ctx, FIXTURE, "truth", truth_policy=t["layer_a"]["policy"])["findings"]
    got = {(f["code"], f["file"], f["line"]) for f in fnd}
    want = {(x["code"], x["file"], x["line"]) for x in t["layer_a"]["findings"]}
    keyed = {x["key"] for x in t["layer_a"]["findings"] if x["key"]}
    keys_ok = keyed <= {f["key"] for f in fnd}
    bad_dir = FIXTURE.parent / "dnparity1bad"
    if (
        not bad_dir.is_dir() and ctx.self_test
    ):  # B3 adds the sibling fixture; the self-test writes a stand-in of its own
        bad_dir = ctx.scratch / "dnparity1bad"
        bad_dir.mkdir(parents=True, exist_ok=True)
        (bad_dir / "Broken.cs").write_text("class {\n")
    d800: dict[str, Any] = {"fixture": str(bad_dir.name), "present": bad_dir.is_dir()}
    if bad_dir.is_dir():
        bad = ctx.lib.layer_a(ctx, bad_dir, "empty")["findings"]
        d800["codes"] = sorted({f["code"] for f in bad})
    ok800 = bool(d800.get("codes")) and "DN800" in d800["codes"]
    return row(
        "A0-DN",
        "dncheck on dnparity1 under its truth policy (exact keys) and DN800 on dnparity1bad",
        {
            "missing": sorted(want - got),
            "extra": sorted(got - want),
            "keys_present": keys_ok,
            "dn800": d800,
        },
        got == want and keys_ok and ok800,
    )


def a0_gate(ctx: Ctx) -> dict[str, Any]:
    g = ctx.lib.gated(ctx)
    gated = set(g["gated"])
    want = {"DN804"} | (set() if Q9_YES else {"DN805"})
    checks = {
        "gated set": gated == want,
        "note names each gated code": all(c in g["note"] for c in want),
        "DN805 is a code": ("DN805" in g["codes"]) == Q9_YES or not Q9_YES,
    }
    return row(
        "A0-GATE",
        "DN_GATED and gated_note()",
        {"gated": g["gated"], "codes": g["codes"], "note": g["note"][:300], "checks": checks},
        all(checks.values()),
    )


def a_d1(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    spec = streak_spec(ctx)
    root = ctx.d1["module_dir"]
    out = {
        k: ctx.lib.layer_a(ctx, root, k, spec=spec)["findings"]
        for k in ("from_spec", "spec_policy")
    }
    return row(
        "A-D1",
        "layer A on D1 under DnPolicy.from_spec(Streak SPEC) and under dn_spec_policy",
        {k: [f["key"] for f in v] for k, v in out.items()},
        all(not v for v in out.values()),
        findings=out,
    )


def a_judge_d1(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    return row(
        "A-JUDGE-D1",
        "share of D1's owner-scoped routes judged by DN801 (complete reach)",
        judged_share(ctx, ctx.d1),
        True,
    )


def judged_share(ctx: Ctx, tgt: dict[str, Any]) -> dict[str, Any]:
    t, root = tgt["truth"], tgt["module_dir"]
    pol = truth_policy(t)
    j = ctx.lib.judged(ctx, root, pol)
    scoped = owner_scoped_routes(t)
    ex = extract(ctx, root)
    if j["judged"] is None:
        e = ext_route_map(ex)
        incomplete = {
            k
            for k, r in e.items()
            if any(
                "incomplete" in str(u.get("reason", "")) or "opaque" in str(u.get("reason", ""))
                for u in r.get("unread", ())
            )
        }
        judged = {k for k in scoped if k in e and k not in incomplete}
        method = "proxy: the extract (no judged list from B3's check output)"
    else:
        judged = {route_id_key(x) for x in j["judged"] if " " in x}
        method = j["method"]
    inn = [k for k in scoped if k in judged]
    return {
        "method": method,
        "owner_scoped": len(scoped),
        "judged": len(inn),
        "share": fraction(len(inn), len(scoped), 0.0),
        "not_judged": [rlabel(k) for k in scoped if k not in judged],
    }


def truth_policy(t: Mapping[str, Any]) -> dict[str, Any]:
    """A ``DnPolicy`` dict from a 4.5 truth: tables (entity -> SQL name), owners, owner_via, private (the truth's private and never-returned columns), machines."""
    priv: dict[str, list[str]] = {}
    for tb in t["tables"]:
        cols = [c["name"] for c in tb["columns"] if c.get("private")]
        if cols:
            priv[tb["entity"]] = cols
    for nr in t.get("not_returned", ()):
        priv.setdefault(nr["entity"], [])
        if nr["column"] not in priv[nr["entity"]]:
            priv[nr["entity"]].append(nr["column"])
    machines: dict[str, Any] = {}
    for k, m in t.get("machines", {}).items():
        ent, col = k.split(".", 1)
        machines[ent] = {"column": col, "edges": [list(e) for e in m["edges"]]}
    users = next((x["entity"] for x in t["tables"] if x["entity"] in ("User", "Users")), None)
    return {
        "tables": {x["entity"]: x["table"] for x in t["tables"]},
        "users_table": users,
        "owners": dict(t.get("owners", {})),
        "owner_via": {k: list(v) for k, v in t.get("owner_via", {}).items()},
        "private": priv,
        "machines": machines,
    }


def a_derive(ctx: Ctx) -> dict[str, Any]:
    out: dict[str, Any] = {}
    ok = True
    ft = fixture_truth()
    d = ctx.lib.derive(ctx, FIXTURE)
    want_p = {k: sorted(v) for k, v in ft["layer_a"]["policy"]["private"].items()}
    out["dnparity1"] = {
        "owners": [d["owners"], ft["layer_a"]["policy"]["owners"]],
        "private": [d["private"], want_p],
    }
    ok &= d["owners"] == ft["layer_a"]["policy"]["owners"] and d["private"] == want_p
    assert ctx.d1 is not None
    d1 = ctx.lib.derive(ctx, ctx.d1["module_dir"])
    tp = truth_policy(ctx.d1["truth"])
    out["D1"] = {
        "owners": [d1["owners"], tp["owners"]],
        "private": [d1["private"], {k: sorted(v) for k, v in tp["private"].items()}],
        "baseline": d1["baseline"],
    }
    ok &= (
        d1["owners"] == tp["owners"]
        and d1["private"] == {k: sorted(v) for k, v in tp["private"].items()}
        and not d1["baseline"]
    )
    return row(
        "A-DERIVE",
        "derive_dn_policy on dnparity1 and D1: owners and private exactly the truth's, baseline empty on D1",
        out,
        ok,
    )


def a_check(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    proj = ctx.scratch / "proj-check"
    spec_project(ctx, proj)
    a = ctx.lib.layer_a_cli(ctx, ctx.d1, proj)
    b = ctx.lib.layer_a_cli(ctx, ctx.d1, proj, no_sdk=True)
    doc = a["json"] or {}
    findings_a = [f for f in doc.get("findings", ()) if f.get("layer") == "A"]
    notes_b = json.dumps((b["json"] or {}).get("notes", "")) + b["stdout"]
    return row(
        "A-CHECK",
        "lattice.spec check --layers A on D1 through _layer_a_dotnet; without the SDK a note",
        {
            "rc": a["rc"],
            "layer_A_findings": len(findings_a),
            "stderr": a["stderr"][-300:],
            "no_sdk_rc": b["rc"],
            "no_sdk_note": "dotnet" in notes_b.lower(),
        },
        a["rc"] == 0 and not findings_a and b["rc"] in (0, 1) and "dotnet" in notes_b.lower(),
    )


def spec_project(ctx: Ctx, proj: Path) -> Path:
    """A scratch project directory holding Streak's spec (and trace) beside D1's harness: ``spec:SPEC`` imports from here."""
    assert ctx.streak_src is not None
    shutil.rmtree(proj, ignore_errors=True)
    proj.mkdir(parents=True)
    for name in ("spec.py", "spec.trace.json", "REQUIREMENTS.md", "CONTEXT.md"):
        if (ctx.streak_src / name).is_file():
            shutil.copyfile(ctx.streak_src / name, proj / name)
    return proj


def a_805(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    fx = ctx.lib.not_owned(ctx, FIXTURE)
    d1 = ctx.lib.not_owned(ctx, ctx.d1["module_dir"])
    cp = ctx.scratch / "d1-403"
    shutil.rmtree(cp, ignore_errors=True)
    shutil.copytree(ctx.d1["module_dir"], cp, ignore=shutil.ignore_patterns("bin", "obj"))
    ap = apply_patch(cp, D1_MUTANT_DIR / "dm5.diff")
    n = ap["ok"]
    mut = ctx.lib.not_owned(ctx, cp) if n else {"status": None, "findings": []}
    want = "DN805|GET /v1/habits/{id}|not_owned"
    checks = {
        "dnparity1 answers 404": fx["status"] == 404,
        "D1 404 or unclear": d1["status"] in (404, None),
        "the 403 copy gives DN805 on GET /v1/habits/{id}": want in mut["findings"],
        "the 403 edit (DM5) applied": bool(n),
    }
    return row(
        "A-805",
        "the not-owned answer derived on dnparity1 and D1; a D1 copy answering 403 on a scoped miss",
        {"dnparity1": fx, "D1": d1, "copy": mut, "checks": checks},
        all(checks.values()),
    )


# ---- Stage 4: the rules-first import of D1 (node B2) -------------------------------------------------------------------------------------------------


# Amendment (node S round 2): values the app under test mints itself differ between runs and reach the import's evidence
# (probe bodies, database diffs). They are masked by pattern in the *comparison* of I-D1-7 and H-PI7 only; the raw files stay as written.
MINTED = (
    (re.compile(r"\$2[abxy]\$\d\d\$[./A-Za-z0-9]{53}"), "<bcrypt>"),
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"), "<jwt>"),
    (re.compile(r"(?<![0-9A-Fa-f])[0-9a-f]{32,}(?![0-9A-Fa-f])"), "<hex>"),
)
TOKEN_VALUE = re.compile(r'"([A-Za-z0-9_-]{32,})"')


def mask_minted(text: str) -> str:
    """``text`` with the app's minted secrets masked: bcrypt hashes, JWTs, 32+ hex digits, and a quoted base64url-looking value of 32+
    characters holding upper case, lower case and a digit, ``-`` or ``_`` (``secrets.token_urlsafe``; a snake_case name has no upper case)."""
    for rx, repl in MINTED:
        text = rx.sub(repl, text)

    def token(m: re.Match[str]) -> str:
        v = m.group(1)
        looks = (
            any(c.isupper() for c in v)
            and any(c.islower() for c in v)
            and any(c.isdigit() or c in "-_" for c in v)
        )
        return '"<token>"' if looks else m.group(0)

    return TOKEN_VALUE.sub(token, text)


def normalised_tree(d: Path) -> dict[str, str]:
    """The import directory byte for byte except the timing fields (report.json ``cost.wall_s`` and ``rules.timing``), report.md and
    the minted secrets (:func:`mask_minted`)."""
    out: dict[str, str] = {}
    for p in sorted(d.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(d).as_posix()
        if rel == "report.md":
            continue
        data = p.read_bytes()
        if rel == "report.json":
            r = json.loads(data)
            r.get("cost", {})["wall_s"] = "<wall_s>"
            if r.get("rules"):
                r["rules"]["timing"] = "<timing>"
            r["repo"] = "<repo>" if "repo" in r else None
            data = (json.dumps(r, indent=2, sort_keys=True) + "\n").encode()
        try:
            data = mask_minted(data.decode()).encode()
        except UnicodeDecodeError:
            pass
        out[rel] = sha_bytes(data)
    return out


def import_dir(ctx: Ctx, tgt: dict[str, Any], tag: str = "main") -> Path:
    key = f"imp:{tgt['label']}:{tag}"
    if key not in ctx.cache:
        d = ctx.scratch / "imp" / f"{tgt['label']}-{tag}"
        t = ctx.lib.import_rules(ctx, tgt, d)
        ctx.timings[f"import_{tgt['label']}_{tag}_s"] = t["wall_s"]
        if t["rc"] != 0:
            raise RuntimeError(
                f"the import of {tgt['label']} failed (exit {t['rc']}): {t['stderr'][-500:]}"
            )
        ctx.cache[key] = d
    return ctx.cache[key]  # type: ignore[no-any-return]


def item_audit(rep: Mapping[str, Any]) -> dict[str, Any]:
    items = rep["rules"]["items"]
    count: dict[str, int] = {}
    for v in items.values():
        count[v["verdict"]] = count.get(v["verdict"], 0) + 1
    verified = sum(count.get(k, 0) for k in VERIFIED)
    corrections = {c["item"] for c in rep["rules"].get("corrections", ())}
    baseline = {b["item"] for b in rep["rules"].get("baseline", ())}
    refuted = [i for i, v in items.items() if v["verdict"] == "refuted"]
    bad = [
        i
        for i in refuted
        if not (
            (items[i].get("correction") or i in corrections or i in baseline)
            and (items[i].get("reason") or (items[i].get("correction") or {}).get("reason"))
        )
    ]
    unex = {i: v.get("reason") for i, v in items.items() if v["verdict"] == "unexercised"}
    return {"items": len(items), "verdicts": count, "verified": verified, "share": round(verified / len(items), 4) if items else 0.0, "refuted": refuted, "refuted_without_correction": bad, "unexercised_without_reason": [i for i, w in unex.items() if not w], "unexercised": unex, "corrections": len(rep["rules"].get("corrections", ()))}  # fmt: skip


def mapped_verdicts(rep: Mapping[str, Any]) -> dict[str, str]:
    """The report's verdicts under the ids of the corrected candidate (the library's own renaming and duplicate renumbering)."""
    try:
        from lattice.importer.review import candidate_item_ids
    except ImportError:  # a tree without the renumbering (the base)
        candidate_item_ids = None  # type: ignore[assignment]
    items = rep["rules"]["items"]
    if candidate_item_ids is None:
        cmap = {
            c["item"]: c["corrected"]
            for c in rep["rules"].get("corrections", ())
            if c.get("corrected")
        }
        return {cmap.get(i, i): v["verdict"] for i, v in items.items()}
    ids = candidate_item_ids(items)
    return {ids[i]: v["verdict"] for i, v in items.items()}


def diff_maps(theirs: Mapping[str, str], mine: Mapping[str, str]) -> dict[str, list[str | None]]:
    return {
        i: [theirs.get(i), mine.get(i)]
        for i in sorted(set(theirs) | set(mine))
        if theirs.get(i) != mine.get(i)
    }


def score_pi4(seed: Mapping[str, str], v2: Mapping[str, str]) -> dict[str, Any]:
    base = [i for i, v in seed.items() if v in VERIFIED]
    still = [i for i in base if v2.get(i) in VERIFIED]
    frac = len(still) / len(base) if base else 0.0
    return {
        "seed_verified": len(base),
        "still_verified": len(still),
        "fraction": round(frac, 4),
        "lost": {i: v2.get(i) for i in base if i not in still},
        "pass": frac >= 0.95,
    }


def seed_map(ctx: Ctx, tgt: dict[str, Any], variant: str = "seed") -> dict[str, str]:
    key = f"rmap:{tgt['label']}:{variant}"
    if key not in ctx.cache:
        ctx.cache[key] = ctx.lib.r_verdicts(ctx, tgt, import_dir(ctx, tgt), variant)
    return ctx.cache[key]  # type: ignore[no-any-return]


def contracts_of(d: Path) -> list[tuple[str, str, str]]:
    cand = load_candidate(d)
    return sorted((c.method.upper(), c.path, str(c.auth)) for c in cand.CONTRACTS)


def i_d1_1(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    d = import_dir(ctx, ctx.d1)
    rep, cand = load_report(d), load_candidate(d)
    n_truth = len(ctx.d1["truth"]["routes"])
    tables = set(cand.PROFILE.api.table_names.values())
    obs = {
        "framework": rep["rules"]["framework"],
        "routes": len(rep["rules"].get("routes", ())) or None,
        "contracts": len(cand.CONTRACTS),
        "tables": len(tables),
        "truth_routes": n_truth,
    }
    health = any(c.path == "/health" for c in cand.CONTRACTS)
    return row(
        "I-D1-1",
        "rules-first import of D1 (SQLite): framework, contracts, tables",
        obs,
        obs["framework"] == "aspnetcore"
        and obs["contracts"] in (n_truth, n_truth - 1)
        and obs["tables"] == 9,
        health_is_a_contract=health,
    )


def inference_counts(rep: Mapping[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in rep["rules"].get("inferences", ()):
        out[i["rule"]] = out.get(i["rule"], 0) + 1
    return out


def i_d1_2(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    rep, t = load_report(import_dir(ctx, ctx.d1)), ctx.d1["truth"]
    ex = extract(ctx, ctx.d1["module_dir"], policy=True)
    inf = rep["rules"].get("inferences", [])
    owner_tables = {i.get("table") for i in inf if i["rule"] == "owner_uses"}
    private = {(i.get("table"), i.get("column")) for i in inf if i["rule"] == "json_ignore"}
    constrained = sum(1 for r in ex["routes"] for p in r["params"] if p.get("parse"))
    cnt = inference_counts(rep)
    checks = {
        "owner_uses per truth owner table": set(t["owners"]) <= owner_tables,
        "json_ignore per private column": len(private)
        >= len({(n["entity"], n["column"]) for n in t["not_returned"]}),
        "enum_choices Nudge.delivery": any(
            i["rule"] == "enum_choices" and "delivery" in json.dumps(i) for i in inf
        ),
        "route_constraint per constrained param": cnt.get("route_constraint", 0) == constrained,
        "bind_status 1": cnt.get("bind_status", 0) == 1,
        "json_naming 1": cnt.get("json_naming", 0) == 1,
    }
    return row(
        "I-D1-2",
        "inferences[] of the D1 import against the truth",
        {"counts": cnt, "constrained_params_in_extract": constrained, "checks": checks},
        all(checks.values()),
    )


def i_d1_3(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    a = item_audit(load_report(import_dir(ctx, ctx.d1)))
    ok = (
        a["share"] >= 0.50
        and not a["refuted_without_correction"]
        and not a["unexercised_without_reason"]
    )
    return row("I-D1-3", "rule items of the D1 import on the seed", {k: a[k] for k in ("items", "verdicts", "share", "refuted", "refuted_without_correction", "unexercised_without_reason", "corrections")}, ok, in_predicted_range={"items": 60 <= a["items"] <= 110, "share": 0.50 <= a["share"] <= 0.70, "refuted": len(a["refuted"]) <= 4}, unexercised=a["unexercised"])  # fmt: skip


def i_d1_4(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    sc = score_pi4(
        seed_map(ctx, ctx.d1), ctx.lib.r_verdicts(ctx, ctx.d1, import_dir(ctx, ctx.d1), "variant2")
    )
    return row(
        "I-D1-4",
        "the corrected D1 spec re-probed on variant2",
        {k: sc[k] for k in ("seed_verified", "still_verified", "fraction", "lost")},
        sc["pass"],
    )


def machine_proposed(
    props: Sequence[Mapping[str, Any]], machines: Mapping[str, Any]
) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for key, m in machines.items():
        struct, col = key.split(".", 1)
        edges = [list(e) for p in props if p.get("kind") == "machine" for e in (p.get("spec_object") or {}).get("edges", ()) if (p.get("spec_object") or {}).get("column") == col and (p.get("spec_object") or {}).get("table", "").lower().rstrip("s") in (struct.lower(), struct.lower() + "s", struct.lower().rstrip("s"))]  # fmt: skip
        first = list(m["edges"][0]) if m["edges"] else None
        found[key] = {
            "first_edge": first,
            "proposed_edges": edges,
            "first_edge_proposed": first in edges,
        }
    return found


def i_d1_5(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    props = json.loads((import_dir(ctx, ctx.d1) / "proposals.json").read_text())
    f = machine_proposed(props, ctx.d1["truth"]["machines"])
    return row(
        "I-D1-5",
        "proposals include the machine Nudge.delivery",
        {"count": len(props), "machines": f},
        bool(f) and all(v["first_edge_proposed"] for v in f.values()),
    )


def i_d1_6(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    d = import_dir(ctx, ctx.d1)
    rv = ctx.lib.review_list(ctx, ctx.d1, d)
    rep = load_report(d)
    # `review list` skips only a plain "verified" item and names an item by its id in the corrected candidate
    from lattice.importer.review import candidate_item_ids

    items = rep["rules"]["items"]
    ids = candidate_item_ids(items)
    unverified = {ids[i] for i, v in items.items() if v["verdict"] != "verified"}
    listed = set(rv["ids"])
    return row("I-D1-6", "python -m lattice.importer review <dir> list", {"exit": rv["rc"], "listed": len(listed), "report_unverified": len(unverified), "listed_not_in_report": sorted(listed - unverified), "report_not_listed": sorted(unverified - listed)}, rv["rc"] == 0 and listed == unverified)  # fmt: skip


def i_d1_7(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    trees = []
    for i in range(ctx.runs):
        d = ctx.scratch / "imp7" / f"r{i}"
        t = ctx.lib.import_rules(ctx, ctx.d1, d)
        trees.append(normalised_tree(d) if t["rc"] == 0 else {"error": t["stderr"][-200:]})
    same = sum(1 for t in trees if t == trees[0])
    return row(
        "I-D1-7",
        f"the D1 import {ctx.runs}x into separate scratch directories",
        {"identical": same, "of": ctx.runs},
        same == ctx.runs,
    )


def i_d1_pr3(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    theirs, mine = mapped_verdicts(load_report(import_dir(ctx, ctx.d1))), seed_map(ctx, ctx.d1)
    diff = diff_maps(theirs, mine)
    return row(
        "I-D1-PR3",
        "layer R with the imported candidate_spec.py",
        {
            "items_import": len(theirs),
            "items_rerun": len(mine),
            "differences [import, rerun]": diff,
        },
        not diff,
    )


def i_d1_t2(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    t = ctx.d1["truth"]
    items = load_report(import_dir(ctx, ctx.d1))["rules"]["items"]
    want = {(n["entity"], n["column"]) for n in t["not_returned"]}
    tabs = {x["entity"]: x["table"] for x in t["tables"]}
    got: dict[str, Any] = {}
    for ent, col in sorted(want):
        ids = [i for i in items if i.startswith("T2 ") and i.split(" ", 1)[1].split(".")[-1] == col and i.split(" ", 1)[1].split(".")[0] in (ent, tabs.get(ent), ent.lower())]  # fmt: skip
        got[f"{ent}.{col}"] = {i: items[i]["verdict"] for i in ids}
    ok = all(v and all(x in VERIFIED for x in v.values()) for v in got.values())
    return row("I-D1-T2", "T2 items of the private columns", got, ok)


def i_d1_m(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    cp = ctx.scratch / "imp-mysql" / "out"
    shutil.rmtree(cp.parent, ignore_errors=True)
    shutil.copytree(ctx.d1["overlay"], cp, ignore=shutil.ignore_patterns("bin", "obj")) if Path(
        ctx.d1["overlay"]
    ).resolve() != REPO.resolve() else None
    mod = cp / "tests/spec" / ctx.d1["module_dir"].name if cp.exists() else ctx.d1["module_dir"]
    toml = mod / "studio.toml"
    toml.write_text(toml.read_text().rstrip("\n") + '\ndatabase = "mysql"\n')
    tgt = dict(ctx.d1, module_dir=mod, overlay=cp if cp.exists() else ctx.d1["overlay"])
    mark = len(shim_calls(ctx))
    out = ctx.scratch / "imp" / "d1-mysql"
    t = ctx.lib.import_rules(ctx, tgt, out)
    rep = load_report(out) if t["rc"] == 0 else {"rules": {}}
    r = rep["rules"]
    docker = [c for c in shim_calls(ctx)[mark:] if " docker " in f" {c} "]
    sqlite_files = sorted(
        p.name for p in out.rglob("*") if p.suffix in (".db", ".sqlite", ".sqlite3")
    )
    n = len(ctx.d1["truth"]["routes"])
    cand_n = len(load_candidate(out).CONTRACTS) if t["rc"] == 0 else None
    obs = {
        "exit": t["rc"],
        "status": r.get("status"),
        "dialect": r.get("dialect"),
        "items": len(r.get("items", {})),
        "contracts": cand_n,
        "docker_calls": docker,
        "sqlite_files": sqlite_files,
    }
    ok = (
        t["rc"] == 0
        and r.get("status") == "not_run:server_dialect"
        and r.get("dialect") == "mysql"
        and not r.get("items")
        and cand_n in (n, n - 1)
        and not docker
        and not sqlite_files
    )
    return row(
        "I-D1-M",
        "the same import on the MySQL studio.toml: not_run:server_dialect, contracts and inferences only",
        obs,
        ok,
    )


# ---- Stage 5: the workflow layer (node B4) --------------------------------------------------------------------------------------------------------------

STREAK_FLOWS = {  # the Streak flows of prereg 5.6 (route sets; a skeleton recovers a flow when it holds them all)
    "account: register, login": ("POST /v1/auth/register", "POST /v1/auth/login"),
    "habit: create, check in, progress": (
        "POST /v1/habits",
        "POST /v1/habits/{habit_id}/completions",
        "GET /v1/habits/{id}/progress",
    ),
    "batch completion": ("POST /v1/completions/batch",),
    "device registration": ("POST /v1/devices",),
}
MACHINE_KEY = "Nudge.delivery"


def flow_routes(f: Mapping[str, Any]) -> set[tuple[str, str]]:
    raw = f.get("skeleton") or [s.get("route") for s in f.get("steps", ()) if s.get("route")]
    return {route_id_key(x) for x in raw if x and " " in x}


def machine_ids(doc: Mapping[str, Any]) -> list[str]:
    return [
        f"{m.get('table')}.{m.get('column')}"
        if m.get("table") and m.get("column")
        else str(m.get("id") or m.get("key"))
        for m in doc.get("machines", ())
    ]


def score_flows(doc: Mapping[str, Any]) -> dict[str, Any]:
    flows = doc.get("flows", [])
    got = {
        name: any(want <= flow_routes(f) for f in flows)
        for name, routes in STREAK_FLOWS.items()
        for want in [{route_id_key(r) for r in routes}]
    }
    machine = any(
        MACHINE_KEY.lower() in m.lower() or m.lower().endswith("nudges.delivery")
        for m in machine_ids(doc)
    )
    return {
        "flows": got,
        "flows_recovered": sum(got.values()),
        "machine": machine,
        "n_flows": len(flows),
        "verified": sum(1 for f in flows if f.get("status") == "verified"),
    }


def wf_run(ctx: Ctx, mode: str, tag: str = "a") -> dict[str, Any]:
    assert ctx.d1 is not None
    key = f"wf:{mode}:{tag}"
    if key not in ctx.cache:
        r = ctx.lib.workflows(ctx, ctx.d1, mode, ctx.scratch / "wf" / f"{mode}-{tag}")
        if r["rc"] != 0 or not r.get("doc"):
            raise RuntimeError(
                f"workflows --{mode} failed (exit {r['rc']}): {r.get('stderr', '')[-400:]}"
            )
        ctx.cache[key] = r
    return ctx.cache[key]  # type: ignore[no-any-return]


def w_d1_1(ctx: Ctx) -> dict[str, Any]:
    st = score_flows(wf_run(ctx, "static")["doc"])
    ob = score_flows(wf_run(ctx, "observed")["doc"])
    ok = ob["machine"] and ob["flows_recovered"] >= 3
    return row("W-D1-1", "workflows on D1: static, then observed on MySQL through the harness", {"static": st, "observed": ob}, ok, reading="4 flows = account, habit, batch, device (route sets of the prereg's list); the machine is Nudge.delivery; gate: the machine and >= 3 of the 4 flows in the observed run")  # fmt: skip


def w_d1_2(ctx: Ctx) -> dict[str, Any]:
    a, b = wf_run(ctx, "observed")["doc"], wf_run(ctx, "observed", "replay")["doc"]
    ver_a = {f["id"] for f in a["flows"] if f.get("status") == "verified"}
    ver_b = {f["id"] for f in b["flows"] if f.get("status") == "verified"}
    failing = sorted(ver_a - ver_b)
    return row("W-D1-2", "flows marked verified fail on replay: a second observed run", {"verified_first": len(ver_a), "verified_second": len(ver_b), "verified_then_not": failing}, not failing and bool(ver_a), reading="a flow verified in the first observed run must be verified in a second one (the library's own replay is inside each run)")  # fmt: skip


def w_d1_3(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    trees = []
    for i in range(ctx.runs):
        r = ctx.lib.workflows(ctx, ctx.d1, "static", ctx.scratch / "wf3" / f"r{i}")
        trees.append(tree_files(r["dir"]) if r["rc"] == 0 else {"error": r["stderr"][-200:]})
    same = sum(1 for t in trees if t == trees[0])
    return row(
        "W-D1-3",
        f"workflows --static {ctx.runs}x",
        {"identical": same, "of": ctx.runs},
        same == ctx.runs,
    )


def w_d1_4(ctx: Ctx) -> dict[str, Any]:
    left = owned_containers(ctx)
    return row(
        "W-D1-4",
        "Docker teardown: containers carrying this runner's lattice.owner label after the observed rows",
        {"containers_left": left, "owner": ctx.owner},
        left == 0,
    )


def inv_of(
    ctx: Ctx, tag: str = "main", *, extract_override: dict[str, Any] | None = None, wf: bool = False
) -> Path:
    assert ctx.d1 is not None
    key = f"inv:{tag}"
    if key not in ctx.cache:
        wfp = wf_run(ctx, "observed")["dir"] / "workflows.json" if wf else None
        r = ctx.lib.inventory(
            ctx, ctx.d1, ctx.scratch / "inv" / tag, workflows=wfp, extract=extract_override
        )
        if r["rc"] != 0:
            raise RuntimeError(f"inventory failed (exit {r['rc']}): {r['stderr'][-400:]}")
        ctx.cache[key] = r["dir"]
    return ctx.cache[key]  # type: ignore[no-any-return]


SOURCE_OK = (
    ".cs",
    ".csproj",
    ".sln",
    ".slnx",
    "Directory.Build.props",
    "Directory.Packages.props",
    "packages.lock.json",
    "global.json",
)


def c_inv(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    d = inv_of(ctx)
    meta = json.loads((d / "inventory.json").read_text())
    ex = json.loads((d / "extract.json").read_text())
    live = extract(ctx, ctx.d1["module_dir"])
    files = meta["source"]["files"]
    names = [f if isinstance(f, str) else f.get("path", "") for f in files]
    bad = [
        n for n in names if not n.endswith(SOURCE_OK) or "/bin/" in f"/{n}" or "/obj/" in f"/{n}"
    ]
    obs = {
        "frontend": meta.get("frontend") or meta.get("source", {}).get("frontend"),
        "routes": [len(ex["routes"]), len(live["routes"])],
        "tables": [len(ex["tables"]), len(live["tables"])],
        "source_files": len(names),
        "outside_source_set": bad[:10],
    }
    ok = (
        obs["frontend"] == "dotnet_aspnet"
        and obs["routes"][0] == obs["routes"][1]
        and obs["tables"][0] == obs["tables"][1]
        and not bad
        and any(n.endswith(".cs") for n in names)
    )
    return row("C-INV", "import inventory on D1", obs, ok)


def report_of_impl(ctx: Ctx, inv: Path, proj: Path) -> dict[str, Any]:
    assert ctx.d1 is not None
    r = ctx.lib.consumer(ctx, "implementation", ctx.d1, inv, proj)
    if r["rc"] not in (0, 1) or not isinstance(r["json"], dict):
        raise RuntimeError(f"implementation report failed (exit {r['rc']}): {r['stderr'][-400:]}")
    return r["json"]  # type: ignore[no-any-return]


def impl_summary(rep: Mapping[str, Any]) -> dict[str, Any]:
    rules = rep.get("rules") or rep.get("requirements") or []
    st = collections.Counter(e["status"] for e in rules)
    return {"requirements": len(rules), "statuses": dict(st), "required_not_implemented": sorted(e["id"] for e in rules if e["status"] == "required_not_implemented"), "implemented_not_required": [x["key"] for x in rep.get("implemented_not_required", ())], "excluded": [x["key"] for x in rep.get("excluded", ())]}  # fmt: skip


def c_impl(ctx: Ctx) -> dict[str, Any]:
    proj = spec_project(ctx, ctx.scratch / "proj-impl")
    s = impl_summary(report_of_impl(ctx, inv_of(ctx, "wf", wf=True), proj))
    health = any("/health" in k for k in s["implemented_not_required"] + s["excluded"])
    ok = (
        not s["required_not_implemented"]
        and s["statuses"].get("implemented_verified", 0)
        + s["statuses"].get("implemented_unverified", 0)
        >= 1
        and health
    )
    return row("C-IMPL", "implementation report on D1 with Streak's spec.py", s, ok, reading="/health is implemented-not-required or, under the library's PLUMBING_PATHS rule, excluded as plumbing; 'the 26 required rules' routes' = every rule row implemented")  # fmt: skip


def c_implm(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    ex = json.loads((inv_of(ctx) / "extract.json").read_text())
    proj = spec_project(ctx, ctx.scratch / "proj-implm")
    extra = json.loads(json.dumps(ex))
    extra["routes"].append(
        {
            **extra["routes"][0],
            "method": "GET",
            "path": "/v1/zz-extra",
            "path_template": "/v1/zz-extra",
            "template": "/v1/zz-extra",
            "line": 1,
        }
    )
    a = impl_summary(report_of_impl(ctx, inv_of(ctx, "extra", extract_override=extra), proj))
    missing = json.loads(json.dumps(ex))
    drop = {("POST", "/v1/devices"), ("GET", "/v1/home")}
    missing["routes"] = [r for r in ex["routes"] if (r["method"], r["path"]) not in drop]
    b = impl_summary(report_of_impl(ctx, inv_of(ctx, "missing", extract_override=missing), proj))
    n_dropped = len(ex["routes"]) - len(missing["routes"])
    ok = (
        a["implemented_not_required"].count("GET /v1/zz-extra") == 1
        and len(a["implemented_not_required"]) == 1 + len(inv_orphans(ctx))
        and n_dropped == 2
        and len(b["required_not_implemented"]) == 2
    )
    return row("C-IMPLM", "one extra route in the code (= one route removed from the spec) and two routes missing from the code", {"extra_route": a, "two_routes_missing": b, "dropped": n_dropped}, ok, reading="the two dropped routes (POST /v1/devices, GET /v1/home) are chosen by the runner; the registered 'exactly 2 required-not-implemented' counts rules, so it holds only if each dropped route backs exactly one rule", routes_dropped=sorted(drop))  # fmt: skip


def inv_orphans(ctx: Ctx) -> list[str]:
    """The implemented-not-required routes of the unmodified D1 report (the baseline the extra route adds one to)."""
    proj = spec_project(ctx, ctx.scratch / "proj-impl")
    # like for like: the extra-route and missing-route inventories are built without the workflow file, so is the baseline
    return impl_summary(report_of_impl(ctx, inv_of(ctx), proj))["implemented_not_required"]


# ---- Stage 5: the consumers on D1's inventory (node B4's dispatch) -----------------------------------------------------------------------------------------

CITE_RX = re.compile(r"code:([\w./+-]+\.(?:cs|csproj|json|props)):(\d+)")


def scan_citations(text: str) -> set[tuple[str, int]]:
    return {(m.group(1), int(m.group(2))) for m in CITE_RX.finditer(text)}


def check_citation_lines(
    root: Path,
    cites: Sequence[tuple[str, int]],
    quotes: Mapping[tuple[str, int], str] | None = None,
) -> dict[str, Any]:
    """Each (path, line) must name an existing file and line (and, when a quote is given, hold it)."""
    bad: list[str] = []
    for rel, ln in sorted(set(cites)):
        f = root / rel
        if not f.is_file():
            bad.append(f"{rel}:{ln} no file")
            continue
        lines = f.read_text(errors="replace").split("\n")
        if not 1 <= ln <= len(lines):
            bad.append(f"{rel}:{ln} no line")
        elif quotes and (rel, ln) in quotes and quotes[(rel, ln)].strip() not in lines[ln - 1]:
            bad.append(f"{rel}:{ln} quote does not match")
    return {"checked": len(set(cites)), "bad": bad}


def c_cite(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    proj = spec_project(ctx, ctx.scratch / "proj-impl")
    inv = inv_of(ctx, "wf", wf=True)
    texts = {
        "implementation report": json.dumps(report_of_impl(ctx, inv, proj)),
        "workflows.json": (wf_run(ctx, "observed")["dir"] / "workflows.json").read_text(),
        "app map": json.dumps(ctx.lib.consumer(ctx, "appmap", ctx.d1, inv, proj)["json"]),
    }
    ask = ctx.lib.tool(ctx, "ask", ctx.d1, proj, inventory=inv)
    texts["ask answers"] = json.dumps(ask)
    quotes = {}
    for a in ask.get("answers", ()):
        for c in a.get("citations", ()):
            m = CITE_RX.fullmatch(str(c.get("target", "")))
            if m and c.get("quote"):
                quotes[(m.group(1), int(m.group(2)))] = c["quote"]
    per = {
        name: check_citation_lines(ctx.d1["module_dir"], sorted(scan_citations(t)), quotes)
        for name, t in texts.items()
    }
    total = sum(v["checked"] for v in per.values())
    bad = sum(len(v["bad"]) for v in per.values())
    return row(
        "C-CITE",
        "every code:<path>:<line> citation of the report, workflows.json, the app map and the ask answers resolves",
        {"per_source": per, "total": total, "bad": bad},
        total > 0 and bad == 0,
    )


ASK_QUESTIONS = (  # prereg 5.6, written before any code: (question, the registered kind, the route its answer cites or None); the kinds are families of the library's implementation labels
    ("is there a sync endpoint?", "not_implemented", None),
    ("can I check in a habit?", "implemented", "POST /v1/habits/{habit_id}/completions"),
    ("who can dismiss a nudge?", "implemented", "POST /v1/nudges/{id}/dismiss"),
)


def c_ask(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    proj = spec_project(ctx, ctx.scratch / "proj-impl")
    r = ctx.lib.tool(ctx, "ask", ctx.d1, proj, inventory=inv_of(ctx, "wf", wf=True))
    kinds = [a.get("family") for a in r.get("answers", ())]
    want = [k for _, k, _ in ASK_QUESTIONS]
    auth_ok = any(
        w in json.dumps((r.get("answers") or [{}])[-1])
        for w in ("Member", "Admin", "auth", "login")
    )
    t = ctx.d1["truth"]
    contradiction = [
        q
        for q, k, _ in ASK_QUESTIONS
        if k == "not_implemented"
        and "sync" in q
        and any("/sync" in x["template"] for x in t["routes"])
    ]
    obs = {
        "digest_has_impl": r.get("digest_has_impl"),
        "kinds": kinds,
        "labels": [a.get("implementation") for a in r.get("answers", ())],
        "registered_kinds": want,
        "dismiss_answer_names_the_auth": auth_ok,
        "rejected_citations": [a.get("rejected", []) for a in r.get("answers", ())],
    }
    return row("C-ASK", "spec ask on D1's inventory: an impl: section in the digest and the three fixed questions (a scripted model: no model call)", obs, bool(r.get("digest_has_impl")) and kinds == want and auth_ok and not any(a.get("rejected") for a in r.get("answers", ())), prereg_expectation_contradicts_truth=contradiction)  # fmt: skip


def c_mcp(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    proj = spec_project(ctx, ctx.scratch / "proj-impl")
    r = ctx.lib.tool(ctx, "mcp", ctx.d1, proj, inventory=inv_of(ctx, "wf", wf=True))
    parts = {k: r.get(k) for k in ("implementation_get", "workflow_get", "workflow_find")}
    cs = {
        k: any(m.group(1).endswith(".cs") for m in CITE_RX.finditer(json.dumps(v or "")))
        for k, v in parts.items()
    }
    return row(
        "C-MCP",
        "MCP implementation_get, workflow_get and workflow_find('check in') on D1",
        {"non_empty": {k: bool(v) for k, v in parts.items()}, "cs_citations": cs},
        all(parts.values()) and all(cs.values()),
    )


def c_map(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    proj = spec_project(ctx, ctx.scratch / "proj-impl")
    r = ctx.lib.consumer(ctx, "appmap", ctx.d1, inv_of(ctx, "wf", wf=True), proj)
    doc = r["json"] or {}
    secs = {k: bool(doc.get(k)) for k in ("rules", "workflows", "machines", "access")}
    return row(
        "C-MAP",
        "app map on D1",
        {"rc": r["rc"], "state": doc.get("state"), "sections": secs},
        doc.get("state") not in (None, "inventory_missing")
        and doc.get("state") == "ok"
        and all(secs.values()),
    )


def c_imp(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    cands = sorted(p for p in (ctx.changes_dir.iterdir() if ctx.changes_dir else []) if p.is_dir())[
        :5
    ]
    proj = spec_project(ctx, ctx.scratch / "proj-impact")
    per: dict[str, Any] = {}
    for c in cands:
        r = ctx.lib.consumer(ctx, "impact", ctx.d1, inv_of(ctx, "wf", wf=True), proj, candidate=c)
        cites = scan_citations(json.dumps(r["json"] or {}))
        per[c.name] = {
            "rc": r["rc"],
            "locations": len(cites),
            "bad": check_citation_lines(ctx.d1["module_dir"], sorted(cites))["bad"],
        }
    ok = len(cands) == 5 and all(
        v["rc"] in (0, 1) and v["locations"] >= 1 and not v["bad"] for v in per.values()
    )
    return row(
        "C-IMP",
        "lattice spec impact T3 on the first 5 changes: >= 1 resolvable code:*.cs:<line> each",
        per,
        ok,
    )


def c_perf(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    r = ctx.lib.tool(ctx, "perf", ctx.d1, spec_project(ctx, ctx.scratch / "proj-perf"))
    return row(
        "C-PERF",
        "lattice perf measure on D1 over MySQL (server counting)",
        r,
        r.get("rc") == 0
        and bool(r.get("reports"))
        and all(x.get("verdict") != "unmeasured" for x in r["reports"]),
    )


def c_live(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    r = ctx.lib.tool(ctx, "contracts_live", ctx.d1, spec_project(ctx, ctx.scratch / "proj-live"))
    return row(
        "C-LIVE",
        "lattice contracts check on a live link to D1 over MySQL",
        r,
        r.get("rc") == 0 and r.get("violations") == 0,
    )


def c_improve(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    r = ctx.lib.tool(ctx, "improve", ctx.d1, spec_project(ctx, ctx.scratch / "proj-improve"))
    return row(
        "C-IMPROVE",
        "lattice improve report over a D1 check's event log, twice",
        r,
        r.get("rc") == 0 and r.get("identical") is True,
    )


def c_rebuild(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    r = ctx.lib.tool(ctx, "rebuild", ctx.d1, spec_project(ctx, ctx.scratch / "proj-rebuild"))
    return row(
        "C-REBUILD",
        "the Studio rebuild loop on a scratch copy of D1: a scripted agent, the gate dotnet build && ./check.sh",
        r,
        bool(r.get("gate_ran")) and bool(r.get("rounds")),
    )


# ---- Arm N: dnapp, layers R and B on hand-written programs (nodes B2 and B4) ------------------------------------------------------------------------------

GO_S1_SHARE = 0.6957  # Go S1's verified share (m, prereg 5.5 N-R1)
DNAPP_R_BUGS = (
    "noauth",
    "forbidden",
)  # B4's DNAPP_BUG values for the JM1 (GET /runs/{rid} skips the caller) and JM2 (answers 403 for a missing run) analogues
DNAPP_B_BUGS = {"nocheck": "POST /configs/{cid}/runs", "nocancel": "POST /runs/{rid}/cancel"}


def n_r1(ctx: Ctx) -> dict[str, Any]:
    r = ctx.lib.dnapp_check(ctx, "R")
    no_reason = [
        i
        for i, v in (r.get("verdict_map") or {}).items()
        if v == "unexercised" and not r.get("reasons", {}).get(i, True)
    ]
    ok = not r["refuted"] and abs(r["verified_share"] - GO_S1_SHARE) <= 0.10 and not no_reason
    return row("N-R1", "layer R on dnapp with go_harness_rb's spec and rules", {k: r.get(k) for k in ("items", "verdicts", "verified_share", "refuted")}, ok, in_predicted_range={"items": r.get("items") == 23, "share": 0.60 <= r["verified_share"] <= 0.80})  # fmt: skip


def n_r2(ctx: Ctx) -> dict[str, Any]:
    out = {}
    for bug in DNAPP_R_BUGS:
        r = ctx.lib.dnapp_check(ctx, "R", bug)
        out[bug] = {
            "refuted": r["refuted"],
            "on_route": [i for i in r["refuted"] if "GET /runs/{rid}" in i],
        }
    return row(
        "N-R2",
        "layer R on the DNAPP_BUG copies (JM1 and JM2 analogues)",
        out,
        all(v["on_route"] for v in out.values()),
    )


def n_b1(ctx: Ctx) -> dict[str, Any]:
    safe = ctx.lib.dnapp_check(ctx, "B")
    out: dict[str, Any] = {"safe": {"findings": safe["findings"], "cases": safe.get("cases")}}
    ok = not safe["findings"]
    for bug, ep in DNAPP_B_BUGS.items():
        r = ctx.lib.dnapp_check(ctx, "B", bug)
        out[bug] = {"endpoints": r["endpoints"], "findings": len(r["findings"])}
        ok = ok and ep in r["endpoints"]
    return row("N-B1", "layer B on dnapp: safe, DNAPP_BUG=nocheck and nocancel", out, ok)


def n_10(ctx: Ctx) -> dict[str, Any]:
    rs = [ctx.lib.dnapp_check(ctx, "R") for _ in range(ctx.runs)]
    bs = [ctx.lib.dnapp_check(ctx, "B") for _ in range(ctx.runs)]
    same = sum(
        1
        for r in rs
        if r.get("verdict_map") == rs[0].get("verdict_map") and r["findings"] == rs[0]["findings"]
    ) + sum(1 for b in bs if b["findings"] == bs[0]["findings"])
    return row(
        "N-10",
        f"layer R and layer B on dnapp {ctx.runs}x",
        {"identical": same, "of": 2 * ctx.runs},
        same == 2 * ctx.runs,
    )


# ---- Arm H: the held-out H1 (blind) --------------------------------------------------------------------------------------------------------------------------


def ht(ctx: Ctx) -> dict[str, Any]:
    assert ctx.h_target is not None
    return ctx.h_target


def h_ex(ctx: Ctx, policy: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    t = ht(ctx)
    return t["truth"], extract(ctx, t["module_dir"], policy=policy)


def h_routes(ctx: Ctx) -> dict[str, Any]:
    s = score_routes(*h_ex(ctx))
    return row(
        "H-ROUTES", "routes vs truth (4.5 matching)", s, s["recall"] >= 0.90 and not s["extra"]
    )


def h_auth(ctx: Ctx) -> dict[str, Any]:
    s = score_auth(*h_ex(ctx))
    return row("H-AUTH", "auth exact on the matched routes", s, s["share_of_matched"] >= 0.85)


def h_tables(ctx: Ctx) -> dict[str, Any]:
    s = score_tables(*h_ex(ctx))
    return row(
        "H-TABLES",
        "table recall and column-fact accuracy",
        s,
        s["table_recall"] >= 0.90 and s["fact_accuracy"] >= 0.90,
    )


def h_wire(ctx: Ctx) -> dict[str, Any]:
    s = score_wire(*h_ex(ctx, policy=True))
    return row("H-WIRE", "wire names vs truth", s, s["share"] >= 0.95)


def h_bounds(ctx: Ctx) -> dict[str, Any]:
    s = score_bounds(*h_ex(ctx))
    return row(
        "H-BOUNDS",
        "bounds vs truth: no bound claimed that truth does not state as enforced",
        s,
        s["precision"] == 1.0,
    )


def h_raise(ctx: Ctx) -> dict[str, Any]:
    s = score_raise(*h_ex(ctx))
    return row(
        "H-RAISE", "refusals (route, status)", s, s["precision"] >= 0.95 and s["recall"] >= 0.60
    )


def h_unread(ctx: Ctx) -> dict[str, Any]:
    s = score_unread(*h_ex(ctx))
    return row("H-UNREAD", "0 silent drops", s, s["n"] == 0)


def h_own(ctx: Ctx) -> dict[str, Any]:
    t = ht(ctx)
    rep = load_report(import_dir(ctx, t))
    inf = [i for i in rep["rules"].get("inferences", ()) if i["rule"] == "owner_uses"]
    truth = t["truth"]["owners"]
    confident = {
        i["table"]: i.get("column") for i in inf if i.get("column") and not i.get("unclear")
    }
    unclear = {i["table"]: list(i.get("columns", [])) for i in inf if i.get("unclear")}
    wrong = {k: c for k, c in confident.items() if truth.get(k) != c}
    hit = [k for k, c in truth.items() if confident.get(k) == c or c in unclear.get(k, [])]
    return row(
        "H-OWN",
        "owners inferred by the import",
        {
            "confident": confident,
            "unclear": unclear,
            "confident_wrong": wrong,
            "recall": fraction(len(hit), len(truth)),
        },
        not wrong,
    )


def h_pi3(ctx: Ctx) -> dict[str, Any]:
    a = item_audit(load_report(import_dir(ctx, ht(ctx))))
    ok = (
        a["share"] >= 0.50
        and not a["refuted_without_correction"]
        and not a["unexercised_without_reason"]
    )
    return row("H-PI3", "rule items of the import on the seed", {k: a[k] for k in ("items", "verdicts", "share", "refuted", "refuted_without_correction", "unexercised_without_reason")}, ok, unexercised=a["unexercised"], in_predicted_range={"share": 0.45 <= a["share"] <= 0.70, "refuted": len(a["refuted"]) <= 4})  # fmt: skip


def h_pi4(ctx: Ctx) -> dict[str, Any]:
    t = ht(ctx)
    sc = score_pi4(seed_map(ctx, t), ctx.lib.r_verdicts(ctx, t, import_dir(ctx, t), "variant2"))
    return row(
        "H-PI4",
        "the corrected spec re-probed on variant2",
        {k: sc[k] for k in ("seed_verified", "still_verified", "fraction", "lost")},
        sc["pass"],
    )


def h_pi5(ctx: Ctx) -> dict[str, Any]:
    t = ht(ctx)
    props = json.loads((import_dir(ctx, t) / "proposals.json").read_text())
    f = machine_proposed(props, t["truth"].get("machines", {}))
    return row(
        "H-PI5",
        "proposals include the truth machine's first edge",
        {"count": len(props), "machines": f},
        bool(f) and all(v["first_edge_proposed"] for v in f.values()),
    )


def h_pi7(ctx: Ctx) -> dict[str, Any]:
    t = ht(ctx)
    trees = []
    for i in range(ctx.runs):
        d = ctx.scratch / "imp7" / t["label"] / f"r{i}"
        r = ctx.lib.import_rules(ctx, t, d)
        trees.append(normalised_tree(d) if r["rc"] == 0 else {"error": r["stderr"][-200:]})
    same = sum(1 for x in trees if x == trees[0])
    return row(
        "H-PI7", f"the import {ctx.runs}x", {"identical": same, "of": ctx.runs}, same == ctx.runs
    )


def h_pr3(ctx: Ctx) -> dict[str, Any]:
    t = ht(ctx)
    theirs, mine = mapped_verdicts(load_report(import_dir(ctx, t))), seed_map(ctx, t)
    diff = diff_maps(theirs, mine)
    return row(
        "H-PR3",
        "layer R with the imported candidate_spec.py",
        {
            "items_import": len(theirs),
            "items_rerun": len(mine),
            "differences [import, rerun]": diff,
        },
        not diff,
    )


def h_pa1(ctx: Ctx) -> dict[str, Any]:
    t = ht(ctx)
    d = ctx.lib.derive(ctx, t["module_dir"])
    tp = truth_policy(t["truth"])
    extra_priv = {
        k: sorted(set(v) - set(tp["private"].get(k, ())))
        for k, v in d["private"].items()
        if set(v) - set(tp["private"].get(k, ()))
    }
    extra_own = {k: c for k, c in d["owners"].items() if tp["owners"].get(k) != c}
    return row(
        "H-PA1",
        "derive_dn_policy: private within the truth's, owners within the truth's",
        {
            "private": d["private"],
            "owners": d["owners"],
            "private_not_in_truth": extra_priv,
            "owners_not_in_truth": extra_own,
        },
        not extra_priv and not extra_own,
    )


def h_pa0(ctx: Ctx) -> dict[str, Any]:
    t = ht(ctx)
    cand = load_candidate(import_dir(ctx, t))
    r = ctx.lib.layer_a(ctx, t["module_dir"], "spec_policy", spec=cand)
    return row(
        "H-PA0",
        "layer A on the original under dn_spec_policy of its candidate spec",
        {"outside_baseline": [f["key"] for f in r["findings"]], "baseline": r["baseline"]},
        not r["findings"],
    )


def h_judge(ctx: Ctx) -> dict[str, Any]:
    s = judged_share(ctx, ht(ctx))
    return row(
        "H-JUDGE",
        "share of the truth's owner-scoped routes judged by DN801 (complete reach)",
        s,
        s["share"] >= 0.75,
    )


def h_wf(ctx: Ctx) -> dict[str, Any]:
    t = ht(ctx)
    a = ctx.lib.workflows(ctx, t, "observed-sqlite", ctx.scratch / "wf-h" / "a")
    b = ctx.lib.workflows(ctx, t, "observed-sqlite", ctx.scratch / "wf-h" / "b")
    if a["rc"] != 0 or not a.get("doc"):
        raise RuntimeError(f"workflows failed (exit {a['rc']}): {a.get('stderr', '')[-300:]}")
    ver = {f["id"] for f in a["doc"]["flows"] if f.get("status") == "verified"}
    multi = [
        f["id"]
        for f in a["doc"]["flows"]
        if f.get("status") == "verified" and len(f.get("steps", ())) >= 2
    ]
    ver_b = {
        f["id"] for f in (b.get("doc") or {}).get("flows", ()) if f.get("status") == "verified"
    }
    failing = sorted(ver - ver_b)
    return row(
        "H-WF",
        "workflows on H1 (static + observed on SQLite)",
        {
            "verified": len(ver),
            "multi_step_verified": len(multi),
            "verified_then_not_on_replay": failing,
        },
        bool(multi) and not failing,
    )


def h_impl(ctx: Ctx) -> dict[str, Any]:
    t = ht(ctx)
    d = import_dir(ctx, t)
    proj = ctx.scratch / "proj-h-impl"
    shutil.rmtree(proj, ignore_errors=True)
    proj.mkdir(parents=True)
    shutil.copyfile(d / "candidate_spec.py", proj / "spec.py")
    for side in (
        "profile.py",
        "natives.py",
    ):  # the candidate loads its profile (PROFILE_FILE) and natives from beside itself
        if (d / side).is_file():
            shutil.copyfile(d / side, proj / side)
    if (d / "routes").is_dir():
        shutil.copytree(d / "routes", proj / "routes")
    wfd = ctx.lib.workflows(ctx, t, "observed-sqlite", ctx.scratch / "wf-h" / "a")["dir"]
    inv = ctx.lib.inventory(
        ctx, t, ctx.scratch / "inv" / "h", workflows=Path(wfd) / "workflows.json"
    )["dir"]
    r = ctx.lib.consumer(ctx, "implementation", t, inv, proj)
    s = impl_summary(r["json"] or {})
    orphans = [k for k in s["implemented_not_required"] if "/health" not in k]
    return row(
        "H-IMPL",
        "implementation report with the imported spec: no missing or extra route rows",
        {"rc": r["rc"], **s, "orphans_without_plumbing": orphans},
        r["rc"] in (0, 1) and not s["required_not_implemented"] and not orphans,
    )


def h_det(ctx: Ctx) -> dict[str, Any]:
    t = ht(ctx)
    exs = [sha_bytes(raw_extract(ctx, t["module_dir"])) for _ in range(ctx.runs)]
    pol = truth_policy(t["truth"])
    las = [
        json.dumps(
            ctx.lib.layer_a(ctx, t["module_dir"], "truth", truth_policy=pol)["findings"],
            sort_keys=True,
        )
        for _ in range(ctx.runs)
    ]
    ok = exs.count(exs[0]) == ctx.runs and las.count(las[0]) == ctx.runs
    return row(
        "H-DET",
        f"extract and layer A {ctx.runs}x",
        {
            "extract_identical": exs.count(exs[0]),
            "layer_a_identical": las.count(las[0]),
            "of": ctx.runs,
        },
        ok,
    )


def h_x(ctx: Ctx) -> dict[str, Any]:
    t = ht(ctx)
    import_dir(ctx, t)
    wall = ctx.timings.get(f"import_{t['label']}_main_s", 1e9)
    return row(
        "H-X", "wall time of the import (helper prebuilt)", {"wall_s": round(wall, 1)}, wall <= 240
    )


# ---- Arm M: MySQL, Docker, the owner's machine --------------------------------------------------------------------------------------------------------------

D1_CONTRACT_CHECK = {
    "scenarios": 97,
    "passed": 96,
    "failing": ("access_token_expires_after_15_minutes",),
}  # Amendment 2 part 2: the frozen failing name
VOLATILE_KEYS = ("timings", "wall_s", "seconds")


def check_cli(
    ctx: Ctx, tgt: dict[str, Any], proj: Path, layers: str, database: str = "mysql"
) -> dict[str, Any]:
    """``python -m lattice.spec check`` on D1 with its harness and Streak's spec, on a throwaway MySQL (Lattice's dbserver, label lattice.owner)."""
    h = f"tests.spec.{tgt['module_dir'].name}_harness"
    argv = [
        "lattice.spec",
        "check",
        "--spec",
        "spec:SPEC",
        "--app",
        f"{h}:make_app",
        "--db",
        f"{h}:build_db",
        "--runtime",
        f"{h}:RUNTIME",
        "--layers",
        layers,
        "--database",
        database,
        "--app-dir",
        str(tgt["module_dir"]),
        "--json",
    ]
    if (
        "SERVICES"
        in Path(tgt["module_dir"].parent / f"{tgt['module_dir'].name}_harness.py").read_text()
    ):
        argv += ["--services", f"{h}:SERVICES"]
    env = ctx.env_real(PYTHONPATH=os.pathsep.join([str(proj), ctx.pythonpath()]))
    env.pop("LATTICE_MYSQL_URL", None)
    rc, so, se, wall = run([ctx.py, "-m", *argv], cwd=proj, env=env, timeout=3600)
    doc: Any = None
    try:  # `check --json` prints one indented document; an older shape was a single last line
        doc = json.loads(so) if so.strip() else None
    except ValueError:
        try:
            doc = json.loads(so.strip().splitlines()[-1])
        except ValueError:
            pass
    return {"rc": rc, "json": doc, "stderr": se[-800:], "wall_s": round(wall, 1)}


def normalise_check(doc: Mapping[str, Any]) -> dict[str, Any]:
    def scrub(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: scrub(x) for k, x in v.items() if k not in VOLATILE_KEYS}
        if isinstance(v, list):
            return [scrub(x) for x in v]
        if isinstance(v, str):
            v = re.sub(
                r"\d+(\.\d+)? ?ms\b", "<ms>", v
            )  # "30 ms each on average" (the database note)
            return re.sub(r"\d+(\.\d+)?s\b", "<s>", re.sub(r"lattice-[a-z0-9-]+", "<container>", v))
        return v

    return scrub(doc)  # type: ignore[no-any-return]


def digest_of(doc: Mapping[str, Any]) -> str:
    return sha_bytes(json.dumps(normalise_check(doc), sort_keys=True, default=str).encode())[:16]


def m_r1(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    proj = spec_project(ctx, ctx.scratch / "proj-m")
    r = ctx.lib.check_run(ctx, ctx.d1, proj, "ACDX")
    doc = r["json"] or {}
    findings = doc.get("findings", [])
    frozen = D1_CONTRACT_CHECK["failing"]
    other = [f for f in findings if not any(n in json.dumps(f) for n in frozen)]
    a_ran = "A" in doc.get("layers", "")
    obs = {"rc": r["rc"], "verdict": doc.get("verdict"), "layers": doc.get("layers"), "counts": doc.get("counts"), "cases": doc.get("cases"), "findings_other_than_the_frozen_failure": [f"{f.get('layer')}|{f.get('endpoint')}|{f.get('case')}" for f in other], "digest": digest_of(doc) if doc else None, "stderr": r["stderr"][-200:]}  # fmt: skip
    ok = (
        bool(doc)
        and a_ran
        and (doc.get("counts") or {}).get("A", 0) == 0
        and not other
        and r["rc"] in (0, 1)
    )
    return row("M-R1", "lattice check --layers ACDX --database mysql on D1 with its harness and Streak's spec", obs, ok, reading="the scenario failure frozen in Amendment 2 part 2 (access_token_expires_after_15_minutes) makes exit 1 expected; the gate is: layer A ran with 0 findings and nothing else is found", contract_check=D1_CONTRACT_CHECK)  # fmt: skip


def m_r2(ctx: Ctx) -> dict[str, Any]:
    assert ctx.d1 is not None
    proj = spec_project(ctx, ctx.scratch / "proj-m")
    digests = []
    for _ in range(ctx.runs):
        r = ctx.lib.check_run(ctx, ctx.d1, proj, "ACD")
        digests.append(digest_of(r["json"]) if r["json"] else f"error:{r['stderr'][-60:]}")
    same = digests.count(digests[0])
    return row(
        "M-R2",
        f"layers A, C, D {ctx.runs}x on D1 over MySQL",
        {"identical": same, "of": ctx.runs, "digests": sorted(set(digests))},
        same == ctx.runs,
    )


# ---- Arm X: byte compatibility with the base, the environment, the matrix -----------------------------------------------------------------------------------

DECLARED = (
    STANDIN / "declared_edits.json"
)  # {"tests": {path: sha256}, "pbg": {module: {policy: [keys]}}, "help": [commands]} -- empty until Amendment 3
PB_X_DIRS = [
    "tests/gocheck/fixtures/goextract", "tests/gocheck/fixtures/app", "tests/gocheck/fixtures/partial", "tests/gocheck/fixtures/goextract_workflow",
    "tests/spec/fixtures/implementation/app", "lattice/studio/mock/appmap/app", "tests/spec/goapp", "tests/spec/goginapp", "tests/spec/gotickets",
    "tests/gocheck/fixtures/runs",
]  # fmt: skip


def declared() -> dict[str, Any]:
    return json.loads(DECLARED.read_text()) if DECLARED.is_file() else {}


def group_hash(root: Path, names: Sequence[str]) -> str:
    return hashlib.sha256(b"".join((root / n).read_bytes() for n in names)).hexdigest()


def frozen_table() -> list[tuple[str, str]]:
    """``(path, sha256)`` of the frozen files of prereg section 10 (the table between its two headings)."""
    text = PREREG.read_text()
    sec = text.split("**Frozen (empty diff vs the base;")[1].split(
        "Also frozen, hashed by the runner"
    )[0]
    return [
        (f.replace("\\", "").split(" (")[0].strip(), h)
        for f, h in re.findall(r"^\| (\S.*?) \| ([0-9a-f]{64}) \|", sec, re.M)
    ]


def frozen_hash(root: Path, rel: str) -> str | None:
    if rel.endswith("/**"):
        d = root / rel[:-3]
        if not d.is_dir():
            return None
        names = sorted(
            (p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file()),
            key=lambda s: s.encode(),
        )
        return group_hash(d, names)
    return sha(root / rel) if (root / rel).is_file() else None


def changed_vs_base(base: Path, roots: Sequence[str]) -> list[str]:
    """Files under ``roots`` whose bytes differ from the base's, or that the base has and the tree has not."""
    out = []
    for r in roots:
        for p in sorted((base / r).rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                cur = REPO / p.relative_to(base)
                if not cur.is_file() or sha(cur) != sha(p):
                    out.append(p.relative_to(base).as_posix())
    return out


def pb_f(ctx: Ctx) -> dict[str, Any]:
    base = ctx.base_dir
    assert base is not None
    rows, stale, touched = [], [], []
    for rel, listed in frozen_table():
        mine, theirs = frozen_hash(REPO, rel), frozen_hash(base, rel)
        rows.append(
            {
                "file": rel,
                "prereg": listed[:12],
                "base": (theirs or "-")[:12],
                "now": (mine or "-")[:12],
            }
        )
        if theirs != listed:
            stale.append(rel)
        if mine != theirs:
            touched.append(rel)
    fixtures = changed_vs_base(
        base,
        (
            "tests/gocheck/fixtures",
            "tests/spec/goapp",
            "tests/spec/goginapp",
            "tests/spec/gotickets",
        ),
    )
    tests = [
        t
        for t in changed_vs_base(base, ("tests", "lattice/studio", "lattice/mcp", "bench"))
        if t not in fixtures and "/golden" not in t
    ]
    allowed = declared().get("tests", {})
    undeclared = [
        t for t in tests if allowed.get(t) != (sha(REPO / t) if (REPO / t).is_file() else None)
    ]
    return row("PB-F", "section 10's frozen table and the existing fixtures, tests and goldens identical to the base", {"files": len(rows), "changed_since_base": touched, "existing_fixture_files_changed": fixtures, "existing_test_files_changed_undeclared": undeclared, "declared_edits_in_place": sorted(set(tests) - set(undeclared)), "prereg_value_differs_from_base (reported)": stale}, not touched and not fixtures and not undeclared, rows=rows, reading="the prereg's hashes are r at c75cb48 and 'recomputed in Amendment 1': a value that differs from the base archive is reported, not gated; the gate is base == now for every row")  # fmt: skip


def go_extract_digest(binary: Path, rel: str, root: Path = REPO) -> str:
    rc, out, err, _ = run(
        [str(binary), "extract"], stdin=json.dumps({"root": str(root / rel)}).encode()
    )
    return sha_bytes(out.encode()) if rc == 0 else f"error:{err[-100:]}"


def go_build(src: Path, out: Path, flags: str = "-mod=mod") -> tuple[bool, str]:
    out.parent.mkdir(parents=True, exist_ok=True)
    rc, _, err, _ = run(
        ["go", "build", "-trimpath", "-buildvcs=false", "-o", str(out), "."],
        cwd=src,
        env={**os.environ, "GOFLAGS": flags, "GOTOOLCHAIN": "auto", "PATH": real_path()},
    )
    return rc == 0, err[-1500:]


def pb_x(ctx: Ctx) -> dict[str, Any]:
    from lattice.gocheck import build_gocheck

    assert ctx.base_dir is not None
    base_bin = ctx.scratch / "base_gocheck" / "gocheck"
    ok, err = go_build(ctx.base_dir / "lattice/gocheck/src", base_bin)
    if not ok:
        raise RuntimeError(f"the base checker does not build: {err}")
    new_bin = build_gocheck()
    rows = []
    for rel in PB_X_DIRS:
        a, b = go_extract_digest(base_bin, rel, ctx.base_dir), go_extract_digest(new_bin, rel)
        rows.append({"dir": rel, "base": a[:16], "now": b[:16], "same_as_base": a == b})
    same = sum(r["same_as_base"] for r in rows)
    return row(
        "PB-X",
        "default gocheck extract identical to the base",
        {"identical_to_base": same, "of": len(rows)},
        same == len(rows),
        rows=rows,
    )


def both_trees(
    ctx: Ctx,
    cmd: Sequence[str],
    *,
    cwd: str = ".",
    env_extra: Mapping[str, str] | None = None,
    stdin: bytes | None = None,
) -> tuple[tuple, tuple]:
    assert ctx.base_dir is not None
    out = []
    for tree in (ctx.base_dir, REPO):
        rc, so, se, _ = run(
            cmd, cwd=tree / cwd, env=ctx.env(PYTHONPATH=str(tree), **(env_extra or {})), stdin=stdin
        )
        out.append((rc, so, se))
    return out[0], out[1]


GO_OUT_CODE = """
import hashlib, json, shutil, sys, tempfile
from pathlib import Path
sys.path.insert(0, "tests/spec/fixtures/implementation")
from lattice.importer.inventory import capture
from lattice.spec.implementation import build_report, load_inventory, canonical_json
from lattice.spec.ask import build_digest
from lattice.spec import appmap
import fixture_spec as FS
fix = Path("tests/spec/fixtures/implementation")
tmp = Path(tempfile.mkdtemp())
app = tmp / "app"; shutil.copytree(fix / "app", app)
capture(app, tmp / "inv", extract=json.loads((fix / "extract.json").read_text()), workflows=fix / "workflows.json")
inv = load_inventory(tmp / "inv", app)
rep = build_report(FS.SPEC, FS.trace(), inv)
d = build_digest(FS.SPEC, FS.trace(), inv)
am = appmap.build_appmap(FS.SPEC, FS.trace(), inv) if hasattr(appmap, "build_appmap") else None
print(hashlib.sha256(canonical_json(rep).encode()).hexdigest(), hashlib.sha256(d.text.encode()).hexdigest(), hashlib.sha256(appmap.map_json(am.doc if hasattr(am, "doc") else am).encode()).hexdigest() if am is not None else "-")
"""


def pb_g(ctx: Ctx) -> dict[str, Any]:
    """Go layer A findings (all gocheck fixtures, the three Go apps, the implementation fixture, under the empty and the derived policy), the
    workflows --static and inventory outputs, and the implementation report, ask digest and app map on the implementation fixture: base vs this tree."""
    import dataclasses

    from lattice.gocheck import GoPolicy, build_gocheck
    from lattice.gocheck.derive import derive_go_policy

    assert ctx.base_dir is not None
    allowed = declared().get("pbg", {})
    base_bin = ctx.scratch / "base_gocheck" / "gocheck"
    if not base_bin.is_file():
        ok, err = go_build(ctx.base_dir / "lattice/gocheck/src", base_bin)
        if not ok:
            raise RuntimeError(f"the base checker does not build: {err}")
    new_bin = build_gocheck()
    mods: dict[str, Path] = {}
    fx = REPO / "tests/gocheck/fixtures"
    for p in sorted(fx.iterdir()):
        for g in sorted(p.rglob("go.mod")) if p.is_dir() else []:
            mods[
                f"fx/{p.name}" + ("" if g.parent == p else "/" + g.parent.relative_to(p).as_posix())
            ] = g.parent
    for n in ("goapp", "goginapp", "gotickets"):
        mods[f"spec/{n}"] = REPO / "tests/spec" / n
    mods["implementation/app"] = REPO / "tests/spec/fixtures/implementation/app"
    mods["studio/mock/appmap"] = REPO / "lattice/studio/mock/appmap/app"

    def findings(binary: Path, root: Path, pol: Any) -> list[Any]:
        proc = subprocess.run(
            [str(binary)],
            input=json.dumps({"root": str(root.resolve()), "policy": pol.to_json()}),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return [["ERR", proc.stderr[-120:]]]
        return sorted(
            [f["key"], f["file"], f["line"], f["message"]]
            for f in json.loads(proc.stdout)["findings"]
        )

    diffs, unlisted = {}, []
    for name, d in mods.items():
        pols = {"empty": GoPolicy()}
        try:
            pols["derived"] = dataclasses.replace(derive_go_policy(d), baseline=frozenset())
        except Exception:  # noqa: BLE001 -- a module the derivation cannot read has only the empty policy
            pass
        for pn, pol in pols.items():
            a, b = findings(base_bin, d, pol), findings(new_bin, d, pol)
            if a != b:
                keys = [r[0] for r in b if r not in a] + [r[0] for r in a if r not in b]
                diffs[f"{name} [{pn}]"] = keys
                unlisted += [
                    f"{name} [{pn}] {k}" for k in keys if k not in allowed.get(name, {}).get(pn, [])
                ]
    other: dict[str, Any] = {}
    pristine = ctx.scratch / "pbg_fixtures"
    shutil.rmtree(pristine, ignore_errors=True)
    shutil.copytree(ctx.base_dir / "tests/gocheck/fixtures", pristine)
    for name, args_ in (("workflows --static", ["workflows", str(pristine), "--package", "goextract_workflow", "--static"]), ("inventory", ["inventory", str(pristine), "--package", "goextract_workflow"])):  # fmt: skip
        outs = []
        for tree in (ctx.base_dir, REPO):
            d = ctx.scratch / "pbg" / name.split()[0] / ("base" if tree != REPO else "now")
            shutil.rmtree(d, ignore_errors=True)
            rc, so, _, _ = run(
                [ctx.py, "-m", "lattice.importer", *args_, "--out", str(d)],
                cwd=tree,
                env=ctx.env(PYTHONPATH=str(tree)),
                timeout=600,
            )
            outs.append((rc, tree_files(d) if d.exists() else {}, so.replace(str(d), "<out>")))
        other[name] = {
            "same": outs[0] == outs[1] and outs[0][0] == 0,
            "rc": [outs[0][0], outs[1][0]],
        }
    a, b = both_trees(ctx, [ctx.py, "-c", GO_OUT_CODE])
    other["implementation report, ask digest, app map (fixture)"] = {
        "same": a == b and a[0] == 0,
        "rc": [a[0], b[0]],
        "err": (a[2] or b[2])[-200:],
    }
    ok = not unlisted and all(v["same"] for v in other.values())
    return row("PB-G", "Go outputs identical to the base", {"layer_a_differences": diffs, "differences_not_declared": unlisted, "other": other, "modules": len(mods)}, ok, reading="impact T3 on the Go fixtures is covered by PB-T's tests/spec; a layer-A difference passes only if dn_standin/declared_edits.json lists its key")  # fmt: skip


VOLATILE = [
    (re.compile(r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(\.\d+)?"), "<timestamp>"),
    (re.compile(r"\$2[aby]\$\d\d\$[./A-Za-z0-9]{53}"), "<bcrypt>"),
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "<jwt>"),
]


def python_tree(d: Path, repo: Path) -> dict[str, str]:
    """A Python rules import directory with its volatile fields replaced (target path, timestamps, salts, timings)."""
    out: dict[str, str] = {}
    for p in sorted(d.rglob("*")):
        rel = p.relative_to(d).as_posix()
        if not p.is_file() or rel == "report.md":
            continue
        text = p.read_text()
        if rel == "report.json":
            r = json.loads(text)
            r["cost"]["wall_s"], r["repo"] = "<wall_s>", "<repo>"
            if r.get("rules"):
                r["rules"]["timing"] = "<timing>"
            text = json.dumps(r, indent=2)
        text = text.replace(str(repo), "<repo>")
        for rx, to in VOLATILE:
            text = rx.sub(to, text)
        out[rel] = sha_bytes(text.encode())
    return out


def pb_p(ctx: Ctx) -> dict[str, Any]:
    """A Python rules-first import (multi-vendor), pycheck's derive_policy and the FastAPI front-end goldens, base vs this tree (the base twice as the normaliser's control)."""
    assert ctx.base_dir is not None
    pkg, fac = "bench.import_repos.multivendor.app", "bench.import_repos.multivendor:make_app"
    outs = {}
    for name, tree in (("base1", ctx.base_dir), ("base2", ctx.base_dir), ("now", REPO)):
        d = ctx.scratch / "pbp" / name
        shutil.rmtree(d, ignore_errors=True)
        rc, _, se, _ = run(
            [
                ctx.py,
                "-m",
                "lattice.importer",
                ".",
                "--package",
                pkg,
                "--app-factory",
                fac,
                "--rules-only",
                "--out",
                str(d),
            ],
            cwd=tree,
            env=ctx.env(PYTHONPATH=str(tree)),
            timeout=1500,
        )
        if rc != 0:
            raise RuntimeError(f"the Python import ({name}) failed: {se[-300:]}")
        outs[name] = python_tree(d, tree)
    diff = sorted(
        k
        for k in set(outs["base1"]) | set(outs["now"])
        if outs["base1"].get(k) != outs["now"].get(k)
    )
    ctl = sorted(
        k
        for k in set(outs["base1"]) | set(outs["base2"])
        if outs["base1"].get(k) != outs["base2"].get(k)
    )
    code = "import dataclasses;from pathlib import Path;from lattice.pycheck import derive_policy;p=derive_policy(Path('bench/import_repos/multivendor/app'),'bench.import_repos.multivendor.app');d=dataclasses.asdict(p);d['baseline']=sorted(d['baseline']);print(d)"
    a, b = both_trees(ctx, [ctx.py, "-c", code])
    g = (
        ctx.py,
        "-m",
        "pytest",
        "tests/importer/test_frontend_goldens.py",
        "-q",
        "-p",
        "no:cacheprovider",
    )
    rc, so, _, _ = run(g, env=ctx.env(), timeout=3600)
    obs = {
        "control_base_twice_identical": not ctl,
        "import_identical_to_base": not diff,
        "differing_files": diff,
        "derive_policy_identical": a == b and a[0] == 0,
        "goldens_pytest_rc": rc,
        "goldens_tail": so.strip().splitlines()[-1] if so.strip() else "",
    }
    return row(
        "PB-P",
        "Python outputs identical to the base",
        obs,
        not ctl and not diff and a == b and a[0] == 0 and rc == 0,
    )


def pytest_ids(log: str) -> dict[str, Any]:
    ids: dict[str, list[str]] = {"FAILED": [], "ERROR": [], "SKIPPED": []}
    for ln in log.splitlines():
        for k in ids:
            if ln.startswith(k + " "):
                ids[k].append(ln.split(" - ")[0].split()[1] if k != "SKIPPED" else ln)
    summary = re.findall(
        r"(\d+) (failed|passed|skipped|errors?)", log.splitlines()[-1] if log else ""
    )
    return {
        **{k: sorted(set(v)) for k, v in ids.items()},
        "summary": [f"{n} {w}" for n, w in summary],
    }


def pb_h(ctx: Ctx) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    cmds = {
        "lattice --help": [
            ctx.py,
            "-c",
            "import sys;from lattice.cli import main;sys.argv=['lattice','--help'];sys.exit(main())",
        ],
        "python -m lattice.importer --help": [ctx.py, "-m", "lattice.importer", "--help"],
        "python -m lattice.spec --help": [ctx.py, "-m", "lattice.spec", "--help"],
        "python -m lattice.spec check --help": [ctx.py, "-m", "lattice.spec", "check", "--help"],
    }
    for name, cmd in cmds.items():
        a, b = both_trees(ctx, cmd)
        rows[name] = {"same": a == b, "rc": [a[0], b[0]]}
        if a != b and name.endswith(
            "check --help"
        ):  # the one declared exception (Q10): `dotnet` joins the --runtime text
            only = difflib_lines(
                a[1], b[1]
            )  # the --runtime paragraph is re-wrapped: its old lines do not say "dotnet", its new ones do
            rows[name]["declared_exception"] = (
                bool(only)
                and any("dotnet" in ln for ln in only)
                and all("'go'" in ln or "framework" in ln or "dotnet" in ln for ln in only)
            )
            rows[name]["same"] = rows[name]["declared_exception"]
    proj = ctx.scratch / "mcp_empty"
    proj.mkdir(exist_ok=True)
    msgs = "\n".join(json.dumps(m) for m in [{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}, {"jsonrpc": "2.0", "method": "notifications/initialized"}, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]) + "\n"  # fmt: skip
    outs = []
    for tree in (ctx.base_dir, REPO):
        rc, so, _, _ = run(
            [ctx.py, "-m", "lattice.mcp", "serve", "--project", str(proj)],
            cwd=proj,
            env=ctx.env(PYTHONPATH=str(tree)),
            stdin=msgs.encode(),
            timeout=120,
        )
        outs.append((rc, so))
    rows["MCP tools/list"] = {"same": outs[0] == outs[1] and "tools" in outs[0][1]}
    return row(
        "PB-H",
        "help texts and MCP tools/list identical to the base (check --help may gain `dotnet`)",
        rows,
        all(v["same"] for v in rows.values()),
    )


def difflib_lines(a: str, b: str) -> list[str]:
    import difflib

    return [
        ln[1:]
        for ln in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0)
        if ln[:1] in "+-" and not ln.startswith(("+++", "---"))
    ]


def pb_n(ctx: Ctx) -> dict[str, Any]:
    cmd = [
        ctx.py,
        "-m",
        "pytest",
        "tests/contracts",
        "tests/improve",
        "tests/studio/test_rebuild.py",
        "-q",
        "-rsf",
        "-p",
        "no:cacheprovider",
        "--continue-on-collection-errors",
    ]
    outs = []
    for tree in (ctx.base_dir, REPO):
        _, so, _, _ = run(cmd, cwd=tree, env=ctx.env(PYTHONPATH=str(tree)), timeout=3600)
        outs.append(pytest_ids(so))
    code = ("import json,sys,hashlib;import importlib.util as u;from lattice.contracts.export import export_spec;"
            "s=u.spec_from_file_location('cs',sys.argv[1]);m=u.module_from_spec(s);s.loader.exec_module(m);"
            "print(hashlib.sha256(json.dumps(export_spec(m.SPEC),sort_keys=True,default=str).encode()).hexdigest())")  # fmt: skip
    exports = {}
    d = ctx.scratch / "imp" / "pbn"
    rc, _, se, _ = run(
        [
            ctx.py,
            "-m",
            "lattice.importer",
            "tests/spec",
            "--package",
            "gotickets",
            "--rules-only",
            "--out",
            str(d),
        ],
        env=ctx.env(),
        timeout=1800,
    )
    if rc == 0:
        a, b = both_trees(ctx, [ctx.py, "-c", code, str(d / "candidate_spec.py")])
        exports["gotickets"] = {"same": a == b and a[0] == 0, "sha256": b[1].strip()[:16]}
    ok = outs[0] == outs[1] and bool(exports) and all(v["same"] for v in exports.values())
    return row(
        "PB-N",
        "stack-neutral outputs identical to the base",
        {
            "pytest_base": outs[0]["summary"],
            "pytest_now": outs[1]["summary"],
            "same_pytest_ids": outs[0] == outs[1],
            "exports": exports,
        },
        ok,
    )


PB_T_TARGETS = (
    "tests/importer",
    "tests/pycheck",
    "tests/gocheck",
    "tests/spec",
    "tests/mcp",
    "tests/studio",
    "tests/dncheck",
)


def pb_t(ctx: Ctx) -> dict[str, Any]:
    targets = list(ctx.pb_t_targets or PB_T_TARGETS)
    cmd = [
        ctx.py,
        "-m",
        "pytest",
        *targets,
        "-q",
        "-rsf",
        "--continue-on-collection-errors",
        "-p",
        "no:cacheprovider",
    ]
    t0 = time.perf_counter()
    _, out, _, _ = run(cmd, env=ctx.env(), timeout=7200)
    wall = time.perf_counter() - t0
    ctx.out.mkdir(parents=True, exist_ok=True)
    (ctx.out / "pytest_now.txt").write_text(out)
    base_targets = [t for t in targets if (ctx.base_dir / t).exists()]
    if not base_targets:  # a slice whose paths the base does not have (the self-test's): the base's result is empty, never the whole suite
        base_targets = ["--co", "-q", "tests/gocheck/test_go_wrap3.py"]
    _, base_out, _, _ = run(
        [
            ctx.py,
            "-m",
            "pytest",
            *base_targets,
            "-q",
            "-rsf",
            "--continue-on-collection-errors",
            "-p",
            "no:cacheprovider",
        ],
        cwd=ctx.base_dir,
        env=ctx.env(PYTHONPATH=str(ctx.base_dir)),
        timeout=7200,
    )
    (ctx.out / "pytest_base.txt").write_text(base_out)
    a, b = pytest_ids(base_out), pytest_ids(out)
    skips = lambda x: sorted(re.sub(r"\s+", " ", s) for s in x["SKIPPED"])  # noqa: E731
    same = a["FAILED"] == b["FAILED"] and a["ERROR"] == b["ERROR"] and skips(a) == skips(b)
    return row("PB-T", "pytest " + " ".join(targets), {"base": a["summary"], "now": b["summary"], "wall_s": round(wall, 1), "failed_only_now": sorted(set(b["FAILED"]) - set(a["FAILED"])), "failed_only_base": sorted(set(a["FAILED"]) - set(b["FAILED"])), "errors_only_now": sorted(set(b["ERROR"]) - set(a["ERROR"]))}, same, reading="the failing, skipped and error ids are compared exactly; the new tests that pass add to the passed count only")  # fmt: skip


def pb_d(ctx: Ctx) -> dict[str, Any]:
    r = []
    for tree in (ctx.base_dir, REPO):
        rc, so, se, _ = run(
            ["sh", "dogfood/studio/check.sh", "--json"],
            cwd=tree,
            env=ctx.env(PYTHONPATH=str(tree)),
            timeout=1800,
        )
        try:
            doc = json.loads(so[so.index("{") :])
        except ValueError:
            doc = {}
        r.append(
            {
                "rc": rc,
                "cases": doc.get("cases"),
                "counts": doc.get("counts"),
                "verdict": doc.get("verdict"),
                "err": se[-150:],
            }
        )
    return row(
        "PB-D",
        "dogfood/studio/check.sh passes with the same step count",
        {"base": r[0], "now": r[1]},
        r[1]["rc"] == 0 and r[1]["cases"] == r[0]["cases"] and r[1]["cases"] is not None,
    )


def pb_nodn(ctx: Ctx) -> dict[str, Any]:
    """With `dotnet` off PATH: a Python and a Go command give the same output as with it; a .NET import fails naming the toolchain; layer A on a .NET runtime is a note."""
    nopath = path_without_dotnet(ctx)
    out: dict[str, Any] = {}
    for name, args_ in (("go workflows --static", ["workflows", "tests/gocheck/fixtures", "--package", "goextract_workflow", "--static"]), ("go inventory", ["inventory", "tests/gocheck/fixtures", "--package", "goextract_workflow"])):  # fmt: skip
        res = []
        for path in (os.environ["PATH"], nopath):
            d = (
                ctx.scratch
                / "nodn"
                / name.replace(" ", "_")
                / ("with" if path != nopath else "without")
            )
            shutil.rmtree(d, ignore_errors=True)
            rc, so, _, _ = run(
                [ctx.py, "-m", "lattice.importer", *args_, "--out", str(d)],
                env=ctx.env(PATH=path),
                timeout=600,
            )
            res.append((rc, tree_files(d) if d.exists() else {}, so.replace(str(d), "<out>")))
        out[name] = res[0] == res[1] and res[0][0] == 0
    assert ctx.d1 is not None
    imp = ctx.lib.import_without_sdk(ctx, ctx.d1, ctx.scratch / "nodn" / "dn", nopath)
    out[".NET import without dotnet"] = {
        "rc": imp["rc"],
        "names_the_toolchain": "DotnetToolchainMissing" in imp["stderr"]
        or "dotnet" in imp["stderr"].lower(),
        "stderr": imp["stderr"][-200:],
    }
    a = ctx.lib.layer_a_cli(ctx, ctx.d1, spec_project(ctx, ctx.scratch / "proj-nodn"), no_sdk=True)
    out["layer A without the SDK"] = {
        "rc": a["rc"],
        "note": "dotnet" in (a["stdout"] + json.dumps(a["json"] or {})).lower(),
    }
    ok = (
        out["go workflows --static"] is True
        and out["go inventory"] is True
        and out[".NET import without dotnet"]["rc"] != 0
        and out[".NET import without dotnet"]["names_the_toolchain"]
        and out["layer A without the SDK"]["note"]
    )
    return row("PB-NODN", "dotnet off PATH", out, ok)


def pw(ctx: Ctx) -> dict[str, Any]:
    changed = snapshot_diff(ctx.snap0, snapshots(ctx.snap_roots))
    extra = [
        f"{k}/{rel}"
        for k, s0 in ctx.extra_snaps.items()
        for rel in diff_snaps(s0, snapshot(Path(k)))
    ]
    lines = shim_calls(ctx)
    calls = lines[: ctx.shim_mark] if ctx.shim_mark is not None else lines
    docker_claude = [ln for ln in calls if " docker " in f" {ln} " or " claude " in f" {ln} "]
    dotnet_in_no_dotnet_rows = [
        ln for ln in lines if " dotnet " in f" {ln} " and ln.split()[0] in NO_DOTNET_ROWS
    ]
    return row("PW", "snapshots (sha256, mode, mtime, listing with hidden files) of the fixtures, truths, targets, helper sources; docker/claude/dotnet shims", {"changed": changed + extra, "docker_claude_shim_calls": docker_claude, "dotnet_calls_in_rows_that_name_none": dotnet_in_no_dotnet_rows}, not changed and not extra and not docker_claude and not dotnet_in_no_dotnet_rows, reading="the shim calls counted are those made before the first row that runs a test suite (PB-N, PB-T, PB-D): the library's own container sweep runs in those")  # fmt: skip


def p_j0(ctx: Ctx) -> dict[str, Any]:
    doc = ctx.j0_path
    assert doc is not None
    text = doc.read_text()
    rows = [ln for ln in text.splitlines() if re.match(r"^\|\s*\d+\s*\|", ln)]
    bad: list[str] = []
    cites = 0
    for ln in rows:
        for rel, line in re.findall(r"`?([\w./-]+\.(?:py|cs|go|md|toml|json)):(\d+)`?", ln):
            cites += 1
            f = REPO / rel
            if not f.is_file() or int(line) > len(f.read_text(errors="replace").split("\n")):
                bad.append(f"{rel}:{line}")
    cells = len(rows) * 3
    return row("P-J0", "every cell of the J0-dn matrix cites an existing file:line and a test or results row", {"rows": len(rows), "expected_rows": 20, "cells": cells, "citations": cites, "bad": bad[:30]}, len(rows) == 20 and cites >= cells and not bad, reading="a mechanical check (rows 1-20 present, every file:line exists, at least one per cell); V checks the content")  # fmt: skip


# ---- prerequisites, running the rows ----------------------------------------------------------------------------------------------------------------------


def node_missing(ctx: Ctx, node: str) -> str | None:
    if node in ctx.standin or node_present(node):
        return None
    return f"node {node} is not merged in this tree (missing {[p for p in NODE_PRESENCE[node] if not (REPO / p).is_file()]})"


def missing(ctx: Ctx, pid: str) -> str | None:
    """The named prerequisite a row lacks, else None. :func:`one_row` is the only producer of NOT RUN, and the self-test uses this to tell a
    legitimate NOT RUN from RM1 (a row that did not run although everything it needs was present)."""
    pid = pid.split("[")[0]
    arm, _, _, nodes, needs = P[pid]
    for n in nodes:
        if (why := node_missing(ctx, n)) is not None:
            return why
    for need in needs:
        if need == "dotnet" and not ctx.dotnet:
            return "no .NET SDK on PATH"
        standin_all = (
            ctx.self_test and bool(nodes) and all(n in ctx.standin for n in nodes)
        )  # the stand-in never starts a container
        if need in ("docker", "driver") and standin_all:
            continue
        if need == "docker":
            if ctx.no_docker:
                return "docker is not permitted in this run (--no-docker)"
            if not ctx.docker:
                return "docker is not available"
        if need == "driver" and not ctx.driver_ok:
            return f"the driver python {ctx.driver_py} lacks PyMySQL/bcrypt"
        if need == "streak" and (
            ctx.streak_src is None or not (ctx.streak_src / "spec.py").is_file()
        ):
            return "no Streak copy (--streak: a `git archive` of a0679e9)"
        if need == "dnapp" and (ctx.dnapp_src is None or not ctx.dnapp_src.is_dir()):
            return "no dnapp (tests/spec/dnapp, built by node B4)"
        if need == "h1" and ctx.h_target is None:
            return "no held-out target (--h1, or the stand-ins of --self-test)"
        if need == "h1_run" and ctx.h_target is not None and not ctx.h_target.get("runnable"):
            return f"the target {ctx.h_target['label']} is parse-only (no harness)"
        if need == "d1_build" and ctx.d1 is not None and ctx.d1.get("build_nuget"):
            return f"the D1 build needs NuGet and could not restore: {ctx.d1['build_error'][-200:]}"
        if need == "base" and ctx.base_dir is None:
            return "no base tree (--base: a `git archive` of the base)"
        if need == "go" and shutil.which("go", path=real_path()) is None:
            return "go is not on PATH"
        if need == "j0" and (ctx.j0_path is None or not ctx.j0_path.is_file()):
            return "the J0-dn matrix is written in Stage 6b (--j0 FILE)"
        if need == "changes" and (ctx.changes_dir is None or not ctx.changes_dir.is_dir()):
            return "no change set (--changes DIR: the first 5 changes of the Spike D Streak set, or W0's)"
    return None


ROW_FN: dict[str, Callable[[Ctx], dict[str, Any]]] = {
    "P0-ROUTES": p0_routes, "P0-AUTH": p0_auth, "P0-EXCL": p0_excl, "P0-BIND": p0_bind, "P0-TABLES": p0_tables, "P0-OWNER": p0_owner,
    "P0-STATE": p0_state, "P0-RAISE": p0_raise, "P0-DI": p0_di, "P0-JSON": p0_json, "DET-10": det_10, "DET-RESTORED": det_restored,
    "DET-PATH": det_path, "D-ROUTES": d_routes, "D-AUTH": d_auth, "D-TABLES": d_tables, "D-WIRE": d_wire, "D-OWN": d_own, "D-RAISE": d_raise,
    "D-UNREAD": d_unread, "D-X": d_x, "F-DETECT": f_detect, "F-MODEL": f_model, "F-RT": f_rt, "F-RECON": f_recon, "PB-LAZY": pb_lazy,
    "A0-DN": a0_dn, "A0-GATE": a0_gate, "A-D1": a_d1, "A-JUDGE-D1": a_judge_d1, "A-DM": a_dm, "A-CHECK": a_check, "A-DERIVE": a_derive, "A-805": a_805,
    "I-D1-1": i_d1_1, "I-D1-2": i_d1_2, "I-D1-3": i_d1_3, "I-D1-4": i_d1_4, "I-D1-5": i_d1_5, "I-D1-6": i_d1_6, "I-D1-7": i_d1_7,
    "I-D1-PR3": i_d1_pr3, "I-D1-T2": i_d1_t2, "I-D1-M": i_d1_m, "N-R1": n_r1, "N-R2": n_r2, "N-B1": n_b1, "N-10": n_10,
    "W-D1-1": w_d1_1, "W-D1-2": w_d1_2, "W-D1-3": w_d1_3, "W-D1-4": w_d1_4, "C-INV": c_inv, "C-IMPL": c_impl, "C-IMPLM": c_implm,
    "C-CITE": c_cite, "C-ASK": c_ask, "C-MCP": c_mcp, "C-MAP": c_map, "C-IMP": c_imp, "C-PERF": c_perf, "C-LIVE": c_live,
    "C-IMPROVE": c_improve, "C-REBUILD": c_rebuild, "M-R1": m_r1, "M-R2": m_r2,
    "PW": pw, "PB-F": pb_f, "PB-X": pb_x, "PB-G": pb_g, "PB-P": pb_p, "PB-N": pb_n, "PB-H": pb_h, "PB-NODN": pb_nodn, "PB-T": pb_t, "PB-D": pb_d, "P-J0": p_j0,
}  # fmt: skip
H_FN: dict[str, Callable[[Ctx], dict[str, Any]]] = {
    "H-ROUTES": h_routes, "H-AUTH": h_auth, "H-TABLES": h_tables, "H-WIRE": h_wire, "H-OWN": h_own, "H-BOUNDS": h_bounds, "H-RAISE": h_raise,
    "H-UNREAD": h_unread, "H-PI3": h_pi3, "H-PI4": h_pi4, "H-PI5": h_pi5, "H-PI7": h_pi7, "H-PR3": h_pr3, "H-PA1": h_pa1, "H-PA0": h_pa0,
    "H-JUDGE": h_judge, "H-WF": h_wf, "H-IMPL": h_impl, "H-DET": h_det, "H-X": h_x,
}  # fmt: skip
ROW_FN.update(H_FN)
ORDER = [
    *ROWS_OF["0"],
    *ROWS_OF["D"],
    *ROWS_OF["N"],
    *ROWS_OF["H"],
    *ROWS_OF["M"],
    *[r for r in ROWS_OF["X"] if r != "PW"],
    "PW",
]
EARLY_SUITE_ROWS = (
    "PB-N",
    "PB-T",
    "PB-D",
    "PB-P",
)  # rows that run test suites: the library's container sweep may call docker there


def arm_of(pid: str) -> str:
    return P[pid.split("[")[0]][0]


def one_row(ctx: Ctx, pid: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """The only producer of a NOT RUN row. A row whose code raised is a FAIL (round-2 Amendment 6 rule a), never NOT RUN."""
    if pid in EARLY_SUITE_ROWS and ctx.shim_mark is None:
        ctx.shim_mark = len(shim_calls(ctx))
    ctx.set_row(pid.split("[")[0])
    base = pid.split("[")[0]
    why = missing(ctx, base)
    if why is not None:
        r = not_run(pid, "(a prerequisite is missing)", why, P[base][2])
    else:
        try:
            r = fn()
        except Exception as err:  # noqa: BLE001
            r = result(
                pid,
                "(the row raised)",
                P[base][2],
                f"{type(err).__name__}: {err}",
                False,
                traceback=traceback.format_exc()[-1800:],
            )
    r["id"] = pid
    r["gated"], r["arm"], r["nodes"] = P[base][1], arm_of(base), list(P[base][3])
    print(f"{pid}: pass={r['pass']}{'' if r['gated'] else ' (not gated)'}", flush=True)
    return r


def run_rows(
    ctx: Ctx, ids: Sequence[str], targets: Sequence[dict[str, Any]] = ()
) -> list[dict[str, Any]]:
    """Run the rows ``ids`` (in the prereg's order). No freeze check and no ledger here: those belong to :func:`main`."""
    out: list[dict[str, Any]] = []
    for pid in [i for i in ORDER if i in ids]:
        if pid in H_FN and len(targets) > 1:
            for tgt in targets:
                if pid not in H_STATIC and not tgt.get("runnable"):
                    continue
                ctx.h_target = tgt
                out.append(one_row(ctx, f"{pid}[{tgt['label']}]", lambda pid=pid: ROW_FN[pid](ctx)))
        else:
            if pid in H_FN and targets:
                ctx.h_target = targets[0]
            if pid == "A-DM":
                out.append(one_row(ctx, pid, lambda: a_dm(ctx)))
            else:
                out.append(one_row(ctx, pid, lambda pid=pid: ROW_FN[pid](ctx)))
    return out


def self_test_problems(rows: Sequence[Mapping[str, Any]], ctx: Ctx) -> list[str]:
    """RM1: a row that is NOT RUN although its named prerequisites are present."""
    return [
        f"{r['id']}: NOT RUN although its prerequisites are present"
        for r in rows
        if r["pass"] is None and missing(ctx, r["id"].split("[")[0]) is None
    ]


def unproduced(rows: Sequence[Mapping[str, Any]], ids: Sequence[str]) -> list[str]:
    seen = {r["id"].split("[")[0] for r in rows}
    return [f"{i}: no row was produced" for i in ids if P[i][1] and i not in seen]


# ---- setup, the command line, main ---------------------------------------------------------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--arm", default=",".join(ARMS), help="comma list of 0, D, N, H, M, X")
    ap.add_argument("--kind", default="shakedown", choices=list(KINDS))
    ap.add_argument(
        "--base",
        default="",
        help="an extracted `git archive` of the base (default: archived from BASE_REF into the scratch)",
    )
    ap.add_argument(
        "--d1",
        default="",
        help="D1: a module directory <root>/tests/spec/dnstreak, or a tarball (default: the archived d1.tgz)",
    )
    ap.add_argument(
        "--h1", default="", help="H1: a module directory <root>/tests/spec/<name>, or a tarball"
    )
    ap.add_argument("--h1-truth", default="", help="H1's truth.json (default: <module>/truth.json)")
    ap.add_argument(
        "--streak", default="", help="an extracted `git archive` of Streak a0679e9 (read only)"
    )
    ap.add_argument("--dnapp", default="", help="tests/spec/dnapp (node B4)")
    ap.add_argument("--changes", default="", help="a directory of candidate change dirs for C-IMP")
    ap.add_argument("--j0", default="", help="the J0-dn matrix document for P-J0")
    ap.add_argument(
        "--freeze",
        default="",
        help="the freeze file; every entry is verified before the first metric",
    )
    ap.add_argument("--out")
    ap.add_argument("--scratch", default="")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--only", default="", help="restrict to these row ids (a shakedown only)")
    ap.add_argument(
        "--container-owner", default="dn-r", help="the lattice.owner label of the Docker rows"
    )
    ap.add_argument(
        "--driver-python", default="", help="a python with lattice[db] (PyMySQL) and bcrypt"
    )
    ap.add_argument(
        "--no-docker",
        action="store_true",
        help="the operator does not allow Docker: its rows are NOT RUN (prerequisite named)",
    )
    ap.add_argument(
        "--self-test",
        action="store_true",
        help="every arm's code path on the stand-ins; fails on an improper NOT RUN (RM1) or an undetected freeze defect (RM2)",
    )
    ap.add_argument(
        "--write-freeze",
        default="",
        help="write a sha256sum-style list of the must-list files (and --extra PATH ...) and exit",
    )
    ap.add_argument(
        "--extra",
        action="append",
        default=[],
        help="with --write-freeze: more absolute paths to list",
    )
    return ap


def build_ctx(args: argparse.Namespace, scratch: Path, out: Path) -> Ctx:
    ctx = Ctx(scratch, out, args.kind)
    ctx.base_dir = Path(args.base).resolve() if args.base else None
    ctx.streak_src = Path(args.streak).resolve() if args.streak else None
    ctx.dnapp_src = Path(args.dnapp).resolve() if args.dnapp else None
    ctx.changes_dir = Path(args.changes).resolve() if args.changes else None
    ctx.j0_path = Path(args.j0).resolve() if args.j0 else None
    ctx.owner, ctx.runs, ctx.no_docker, ctx.driver_py = (
        args.container_owner,
        args.runs,
        bool(args.no_docker),
        args.driver_python or sys.executable,
    )
    return ctx


def setup(
    ctx: Ctx,
    ids: Sequence[str],
    d1_src: Path | None,
    h1_src: Path | None = None,
    h1_truth: Path | None = None,
) -> None:
    """Shims, scratch, the targets (D1, H1), their builds, the base tree, the snapshots, the Docker and driver probes."""
    ctx.scratch.mkdir(parents=True, exist_ok=True)
    ctx.out.mkdir(parents=True, exist_ok=True)
    make_shims(ctx)
    os.environ["LATTICE_DNCHECK_CACHE"] = str(ctx.scratch / "dncheck-cache")
    ctx.d1 = materialise("d1", d1_src or D1_TGZ, ctx.scratch / "targets", D1_TRUTH)
    if h1_src is not None:
        ctx.h_target = materialise("h1", h1_src, ctx.scratch / "targets", h1_truth)
    need_base = any(P[i][4].count("base") for i in ids)
    if need_base and ctx.base_dir is None:
        ctx.base_dir = ctx.scratch / "base"
        archive(BASE_REF, ctx.base_dir)
    ctx.snap_roots = [
        str(Path(t["module_dir"]).relative_to(REPO))
        for t in ctx.targets()
        if Path(t["module_dir"]).resolve().is_relative_to(REPO.resolve())
    ]
    ctx.snap0 = snapshots(ctx.snap_roots)
    for t in ctx.targets():
        if Path(t["module_dir"]).resolve().is_relative_to(REPO.resolve()):
            continue
        ctx.extra_snaps[str(t["module_dir"])] = snapshot(Path(t["module_dir"]))
    for p in (ctx.streak_src, ctx.dnapp_src):
        if p is not None and p.is_dir():
            ctx.extra_snaps[str(p)] = snapshot(p)
    ctx.docker = docker_present() and not ctx.no_docker
    ctx.driver_ok = driver_has_pymysql(ctx.driver_py)
    ctx.dotnet = shutil.which("dotnet", path=real_path()) is not None
    wants_build = any(i for i in ids if set(P[i][4]) & {"d1_build"}) or any(
        arm_of(i) == "H" for i in ids
    )
    if wants_build and ctx.dotnet:
        for t in ctx.targets():
            build_target(ctx, t)


def lattice_subprocess_inside(ctx: Ctx) -> str:
    rc, out, err, _ = run([ctx.py, "-c", "import lattice;print(lattice.__file__)"], env=ctx.env())
    f = Path(out.strip()).resolve() if rc == 0 and out.strip() else None
    if f is None or not f.is_relative_to(REPO.resolve()):
        raise SystemExit(
            f"refusing to run: the subprocesses import lattice from {out!r} {err[-200:]}"
        )
    return str(f)


def summarise(rows: Sequence[Mapping[str, Any]]) -> None:
    gated = [r for r in rows if r.get("gated")]
    passed = [r["id"] for r in gated if r["pass"] is True]
    failed = [r["id"] for r in gated if r["pass"] is False]
    nr = [r["id"] for r in rows if r["pass"] is None]
    print(
        f"gated passed {len(passed)}: {passed}\ngated failed {len(failed)}: {failed}\nnot run {len(nr)}: {nr}"
    )
    print(f"reported: {[(r['id'], r['pass']) for r in rows if not r.get('gated')]}")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    this = Path(__file__).resolve()
    if args.write_freeze:
        n = write_freeze(Path(args.write_freeze), [Path(p) for p in args.extra])
        print(f"{n} entries written to {args.write_freeze}")
        return 0
    arms = [a for a in args.arm.split(",") if a]
    bad = [a for a in arms if a not in ARMS]
    if bad:
        raise SystemExit(f"unknown arm(s) {bad}; the arms are {ARMS}")
    if args.self_test:
        return self_test(args, arms, this)
    if not args.out:
        raise SystemExit("--out is required")
    if args.only and args.kind != "shakedown":
        raise SystemExit(
            "--only restricts the rows of a shakedown; a scored, rerun or verify run covers every row of its arms"
        )
    if not args.freeze:
        raise SystemExit(
            "--freeze is required: the self-hash check runs before any metric and cannot be skipped"
        )
    out = Path(args.out).resolve()
    scratch = Path(args.scratch).resolve() if args.scratch else out.parent / "scratch-dn"
    runner_sha, lattice_file = sha(this), lattice_file_inside()
    freeze_path = Path(args.freeze).resolve()
    freeze_sha = sha(freeze_path) if freeze_path.is_file() else "missing"
    entries = read_ledger()
    for a in arms:
        why = ledger_refusal(entries, f"DN:{a}", args.kind, runner_sha, freeze_sha)
        if why:
            raise SystemExit(f"the ledger refuses arm {a}: {why}")
    run_id = uuid.uuid4().hex[:12]

    def entry(arm: str, status: str, **extra: Any) -> dict[str, Any]:
        return {"ts": now(), "run_id": run_id, "runner": RUNNER_NAME, "runner_sha256": runner_sha, "freeze_sha256": freeze_sha, "arm": f"DN:{arm}", "kind": args.kind, "out": str(out), "lattice_file": lattice_file, "dotnet_sdk": sdk_or_none(), "status": status, **extra}  # fmt: skip

    for a in arms:
        append_ledger(entry(a, "started"))
    metric_started = False
    try:
        h1 = Path(args.h1).resolve() if args.h1 else None
        d1 = Path(args.d1).resolve() if args.d1 else None
        req = [REPO / p for p in ("tests/dncheck/d1/truth.json",)] + [
            p
            for p in (h1, Path(args.h1_truth).resolve() if args.h1_truth else None)
            if p is not None
        ]
        freeze = verify_freeze(
            freeze_path,
            this,
            args.kind,
            required_abs=[*req, *([d1] if d1 and not str(d1).startswith(str(REPO)) else [])],
        )
        freeze["lattice_file"] = lattice_file
        ids = [
            i for i in ORDER if arm_of(i) in arms and (not args.only or i in args.only.split(","))
        ]
        print(f"gated rows ({sum(P[i][1] for i in ids)}): {[i for i in ids if P[i][1]]}")
        print(f"reported rows: {[i for i in ids if not P[i][1]]}", flush=True)
        ctx = build_ctx(args, scratch, out)
        ctx.lib = RealLib()
        setup(ctx, ids, d1, h1, Path(args.h1_truth).resolve() if args.h1_truth else None)
        freeze["import_subprocess_lattice_file"] = lattice_subprocess_inside(ctx)
        metric_started = True
        rows = run_rows(ctx, ids)
    except BaseException as err:  # noqa: BLE001 -- recorded in the ledger, then re-raised
        status = "crash_after_metric" if metric_started else "crash_before_metric"
        for a in arms:
            append_ledger(entry(a, status, error=repr(err)[:400]))
        if isinstance(err, FreezeError):
            print(err, file=sys.stderr)
            out.mkdir(parents=True, exist_ok=True)
            (out / "probes-dn.json").write_text(
                json.dumps({"aborted": str(err), "kind": args.kind, "results": []}, indent=2) + "\n"
            )
            return 2
        raise
    calls = shim_calls(ctx)
    doc = {"runner": RUNNER_NAME, "runner_sha256": runner_sha, "kind": args.kind, "run_id": run_id, "base_ref": BASE_REF, "arms": arms, "lattice_file": lattice_file, "freeze": freeze, "timings": ctx.timings, "shim_calls": calls, "python": sys.version.split()[0], "gated": [i for i in ids if P[i][1]], "results": rows}  # fmt: skip
    out.mkdir(parents=True, exist_ok=True)
    (out / "probes-dn.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")
    for r in rows:  # per-run evidence JSON, one file per row
        (out / "evidence").mkdir(exist_ok=True)
        (out / "evidence" / f"{re.sub(r'[^A-Za-z0-9._-]', '_', r['id'])}.json").write_text(
            json.dumps(r, indent=2, default=str) + "\n"
        )
    for a in arms:
        append_ledger(entry(a, "completed"))
    summarise(rows)
    print(f"ledger sha256: {sha(LEDGER)}")
    return 0


def sdk_or_none() -> str | None:
    try:
        from lattice import dncheck

        return dncheck.sdk_version()
    except Exception:  # noqa: BLE001
        return None


def rows_cli(argv: list[str] | None = None) -> int:
    """The entry the mutants runner uses in a subprocess of a *scratch copy* of the tree (which has no .git): run some rows, write rows.json.
    It has no freeze check or ledger by design (nothing is scored through it) and refuses to run in the real worktree."""
    if (REPO / ".git").exists():
        raise SystemExit(
            "rows_cli is for scratch copies of the tree (no .git); a scored run goes through main(), which verifies the freeze"
        )
    ap = parser()
    ap.add_argument("--ids", required=True)
    args = ap.parse_args(argv)
    out = Path(args.out).resolve()
    scratch = Path(args.scratch).resolve() if args.scratch else out / "scratch"
    ctx = build_ctx(args, scratch, out)
    ids = args.ids.split(",")
    setup(
        ctx,
        ids,
        Path(args.d1).resolve() if args.d1 else None,
        Path(args.h1).resolve() if args.h1 else None,
    )
    rows = run_rows(ctx, ids)
    (out / "rows.json").write_text(json.dumps(rows, indent=2, default=str) + "\n")
    return 0


# ---- the self-test: RM1, RM2 and every arm's code path on the stand-ins ------------------------------------------------------------------------------------------


def selftest_rm1(ctx: Ctx) -> dict[str, Any]:
    """RM1: a runner that reports a row NOT RUN while every prerequisite is present must fail the self-test, and :func:`one_row` is the only
    place in this file that builds a NOT RUN."""
    present = [i for i in P if missing(ctx, i) is None][:6]
    fake = [{"id": i, "pass": None} for i in present]
    problems = self_test_problems(fake, ctx)
    tree = ast.parse(Path(__file__).read_text())
    calls = [
        (f.name, n.lineno)
        for f in ast.walk(tree)
        if isinstance(f, ast.FunctionDef)
        for n in ast.walk(f)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "not_run"
    ]
    only_one_row = {name for name, _ in calls} == {"one_row"}
    return {
        "caught": len(problems) == len(fake) and bool(fake),
        "fake_rows": len(fake),
        "not_run_only_in_one_row": only_one_row,
        "not_run_calls": calls,
    }


def selftest_rm2() -> dict[str, Any]:
    """RM2: a self-hash check that skips the runner's entry, a library entry or the mutants runner's entry, or that accepts an edited hash, must fail the freeze test."""
    tmp = Path(tempfile.mkdtemp(prefix="rm2-"))
    this = Path(__file__).resolve()
    entries = [(rel, sha(REPO / rel)) for rel in must_list("shakedown") if (REPO / rel).is_file()]
    lib_file = "lattice/dncheck/src/Routes.cs"
    checks: dict[str, str] = {}

    def attempt(name: str, lines: list[tuple[str, str]]) -> None:
        fp = tmp / f"{uuid.uuid4().hex}.sha256"
        fp.write_text("".join(f"{h}  {rel}\n" for rel, h in lines))
        try:
            verify_freeze(fp, this)
            checks[name] = "NOT caught"
        except FreezeError as err:
            checks[name] = f"caught: {str(err)[:70]}"

    attempt("control: the complete list passes", entries)
    ctl = checks.pop("control: the complete list passes")
    for name, drop in (("skips the running script's entry", RUNNER_REL), ("skips a library file's entry", lib_file), ("skips the mutants runner's entry", MUTANTS_REL), ("skips a stand-in file's entry", standin_files()[0] if standin_files() else RUNNER_REL)):  # fmt: skip
        attempt(name, [(r, h) for r, h in entries if r != drop])
    attempt("one edited hash", [(r, "0" * 64 if r == lib_file else h) for r, h in entries])
    attempt("a listed file that does not exist", [*entries, ("docs/no/such/file", "0" * 64)])
    shutil.rmtree(tmp, ignore_errors=True)
    control_passed = "NOT caught" in ctl
    return {
        "control_passes": control_passed,
        "checks": checks,
        "caught": control_passed and all(v.startswith("caught") for v in checks.values()),
    }


def standin_ctx(args: argparse.Namespace, scratch: Path, out: Path) -> Ctx:
    ctx = build_ctx(args, scratch, out)
    ctx.self_test = True
    ctx.runs = min(args.runs, 2)
    ctx.standin = {n for n in NODES if not node_present(n)}
    sys.path.insert(0, str(STANDIN))
    import standin_lib

    ctx.lib = standin_lib.StandInLib(RealLib, ctx.standin)
    ctx.streak_src = ctx.streak_src or STANDIN / "streak"
    ctx.dnapp_src = ctx.dnapp_src or STANDIN / "dnapp"
    ctx.changes_dir = ctx.changes_dir or STANDIN / "changes"
    ctx.j0_path = ctx.j0_path or STANDIN / "j0_standin.md"
    return ctx


def self_test(args: argparse.Namespace, arms: list[str], this: Path) -> int:
    """Every arm's code path (Arm H on D1 and dnparity1 relabelled as the H arm, nodes that are not merged on the stand-ins of ``dn_standin/``).
    Not a result: it writes to a scratch directory, never to the ledger, and fails on an improper NOT RUN (RM1) or an uncaught freeze defect (RM2)."""
    out = Path(args.out or tempfile.mkdtemp(prefix="dn-selftest-")).resolve()
    if "docs/results/data" in out.as_posix():
        raise SystemExit("--self-test writes to a scratch directory, not to the results data")
    scratch = Path(args.scratch).resolve() if args.scratch else out / "scratch-selftest"
    ctx = standin_ctx(args, scratch, out)
    ids = [i for i in ORDER if arm_of(i) in arms and (not args.only or i in args.only.split(","))]
    setup(ctx, ids, None, None)
    sd1 = materialise("sd1", D1_TGZ, scratch / "targets", D1_TRUTH)
    sd1["label"] = "S-D1"
    if sd1.get("module_dir") and ctx.dotnet:
        build_target(ctx, sd1)
    fx = make_target(
        "S-fx", FIXTURE, REPO, STANDIN / "dnparity1/truth.json", runnable=False, kind="fixture"
    )
    ctx.h_target = sd1
    ctx.pb_t_targets = ("tests/dncheck/test_dnextract_idioms.py", "tests/dogfood")
    rows = run_rows(ctx, ids, [sd1, fx])
    rm1, rm2 = selftest_rm1(ctx), selftest_rm2()
    problems = self_test_problems(rows, ctx) + unproduced(rows, ids)
    problems += [
        f"{r['id']}: the row raised: {r['observed']}"
        for r in rows
        if "traceback" in r.get("evidence", {})
    ]
    if not rm1["caught"] or not rm1["not_run_only_in_one_row"]:
        problems.append(f"RM1 not caught: {rm1}")
    if not rm2["caught"]:
        problems.append(f"RM2 not caught: {rm2}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "selftest.json").write_text(
        json.dumps(
            {
                "rows": rows,
                "problems": problems,
                "RM1": rm1,
                "RM2": rm2,
                "standin_nodes": sorted(ctx.standin),
                "runner_sha256": sha(this),
            },
            indent=2,
            default=str,
        )
        + "\n"
    )
    summarise(rows)
    print("RM1:", rm1["caught"], "RM2:", rm2["caught"], "stand-in nodes:", sorted(ctx.standin))
    print(
        "SELF-TEST",
        "FAILED: " + "; ".join(problems)
        if problems
        else "OK: every arm's code path ran; no improper NOT RUN; RM1 and RM2 caught",
    )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
