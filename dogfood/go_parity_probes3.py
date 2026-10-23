"""Probes of Go parity, round 3 (docs/results/2026-10-18-go-parity-round3-prereg.md, sections 5.2-5.6,
5.8, 5.9 and Amendments 1-2; Spike J, node G3, builder B1): Arm 0 (static fixtures), Arm S (S1, S2,
S3), Arm M (Streak a0679e9), Arm J (job-scheduler-go 00dd1de), Arm 2 (Tier 2, section 5.6: K8's GO805 and
K10's J0' matrix; builder B2's rows, completed by the runner node), Arm H (the held-out S4, blind),
Arm P (cross-arm byte compatibility) and Arm X (the default extract).

    "$PY" dogfood/go_parity_probes3.py --arm 0,S,M,J,2,P,H,X --base "$SCRATCH/base" \\
        --streak "$SCRATCH/streak" --jobsched "$SCRATCH/jobsched" --freeze "$SCRATCH/g3.sha256" \\
        --runs 10 --out docs/results/data/2026-10-18-go-parity-round3/scored

    "$PY" dogfood/go_parity_probes3.py --self-test --base "$SCRATCH/base" --streak ... --jobsched ...

What cannot be switched off (prereg section 10, rules 2 and 3):

* the self-hash check, before any metric: every entry of ``--freeze`` is re-hashed, the running
  script and every file in :data:`MUST_LIST` must be listed, a mismatch aborts. There is no flag
  that skips or narrows it; the mutants runner calls :func:`run_rows` in a subprocess of a *scratch
  copy of the tree* (the library under mutation), never this ``main``;
* the library under test lies inside the worktree (``lattice.__file__``);
* the global run ledger ``docs/results/data/2026-10-18-go-parity-round3/ledger.jsonl`` (a fixed path):
  a start and an end entry per arm and run; a ``scored`` run is refused for an arm that already
  has a scored run.

A row is NOT RUN only when a named prerequisite is missing (:func:`missing`): no Go, no Docker (M-R1),
no Streak or job-scheduler copy, no S4. Everything else that raises is a FAIL. ``--self-test`` runs
every arm's code path, Arm H against the stand-ins of ``dogfood/g3_standin/`` (S2 and S3 with their
truths in S4's schema), and fails if any row is NOT RUN while its prerequisites are present (RM1).
Nothing here edits a fixture, a target or a library file: imports write to the scratch, Go apps are
built to the scratch, and recording ``docker`` / ``claude`` shims come first on ``PATH``.
"""  # noqa: E501

# ruff: noqa: E501
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import go_parity_probes as P1  # noqa: E402  (round 1, frozen: helpers and the S1 rows)
import go_parity_probes2 as P2  # noqa: E402  (round 2, frozen: the arm-0 and S2 rows, PB helpers)
import go_parity_s3 as S3M  # noqa: E402  (round 2, frozen: S3 mutants and layer A on copies)
import go_parity_s3r as S3R  # noqa: E402  (G3 Tier 0, frozen by the orchestrator: ledger, freeze, K7)

REPO = P1.REPO
SPEC_DIR = P1.SPEC_DIR
FIX = REPO / "tests/gocheck/fixtures"
GP3 = FIX / "goparity3"
STANDIN = HERE / "g3_standin"
RUNNER_REL = "dogfood/go_parity_probes3.py"
MUTANTS_REL = "dogfood/go_parity_mutants3.py"
R3_DATA = S3R.R3_DATA
LEDGER = S3R.LEDGER  # fixed, never derived from --out
BASE_REF = "978b468"
ARMS = ("0", "S", "M", "J", "2", "P", "H", "X")
KINDS = S3R.KINDS
VERIFIED = P1.VERIFIED
result, not_run, sha = P1.result, P1.not_run, P1.sha

# ---- what the freeze file must list (prereg section 7 and Amendment 3) --------------------------------

MUST_LIST = (
    RUNNER_REL,
    MUTANTS_REL,
    "dogfood/go_parity_s3r.py",
    "dogfood/g3_standin/s2/truth.json",
    "dogfood/g3_standin/s3/truth.json",
    "dogfood/g3_standin/pbg_allowed.json",
    "lattice/gocheck/src/routes.go",
    "lattice/gocheck/src/reach.go",
    "lattice/gocheck/src/owner.go",
    "lattice/gocheck/derive.py",
    "lattice/importer/go_rules.py",
    "lattice/importer/rules.py",
    "dogfood/g3_standin/k8_mutants.json",
    "lattice/gocheck/notowned.py",
    "docs/results/2026-10-18-parity-matrix-j0prime.md",
    "tests/gocheck/fixtures/goparity3/go.mod",
    "tests/gocheck/fixtures/goparity3/main.go",
    "tests/gocheck/fixtures/goparity3/truth.json",
    "tests/gocheck/test_go_wrap3.py",
    "tests/gocheck/test_go_derive3.py",
    "tests/gocheck/test_go805.py",
    "tests/importer/test_go_rules3.py",
    "tests/spec/test_go_parity_probes3.py",
)

# ---- the rows: id -> (arm, gated, prediction text) ----------------------------------------------------

P: dict[str, tuple[str, bool, Any]] = {
    # arm 0 (5.2)
    "P0-WRAP": ("0", True, "GO801 on w1; none on w2; none on w3 (silent, two levels)"),
    "P0-OPQ": ("0", True, "GO801 on the m1 route; none on the m2 route"),
    "P0-BODY": ("0", True, "contract param `title` body Str required max_len=40; one body_model inference"),
    "P0-CHOICES2": ("0", True, "c1 no choices; c2 two; S2 Run.status (queued, cancelled); S3 Ticket.status (open, closed)"),
    "P0-REG": ("0", True, "P0-PARSE, P0-OMIT, P0-BIND, P0-LIBMW, P0-PRIV, PA4 as round 2"),
    # arm S (5.3)
    "PS-PI3-3": ("S", True, "63-70 items; share 0.60-0.68; refuted 0-2 each with a correction; both R2 creates verified"),
    "PS-PI4-3": ("S", True, "every seed-verified item still verified (>= 0.95)"),
    "PS-PR3-3": ("S", True, "the same verdict map after the correction map"),
    "PS-JM31": ("S", True, "GO801|POST /tickets/:tid/close|Ticket; original 0 outside the baseline"),
    "PS-ST2": ("S", True, "GO801|GET /tickets/:tid|Ticket"),
    "PS-ST3": ("S", True, "GO801|POST /tickets/:tid/reopen|Ticket"),
    "PS-S3A": ("S", True, "0 findings under from_spec and under go_spec_policy"),
    "PS-S2": ("S", True, "PI1-PI7, PR3, PR-T2, PA1-PA3 as round 2; PI3 41-44 items, share 0.61-0.68"),
    "PS-S1": ("S", True, "PR1, PR2, PR4, PB1, PB1-CLI, PB2, PB3 as round 2"),
    # arm M (5.4)
    "M-A0": ("M", True, "0 findings (range 0-2) under GoPolicy.from_spec(SPEC)"),
    "M-A1": ("M", False, "978b468: 0-1 of 26 judged; G3: 18-24 of the 24 wrapped routes"),
    "M-SM1": ("M", True, "GO801|GET /v1/habits/:id|Habit"),
    "M-SM2": ("M", True, "GO801|PATCH /v1/habits/:id|Habit"),
    "M-SM3": ("M", True, "a GO801 on GET /v1/habits/{id}/completions, table Habit or Completion"),
    "M-SM4": ("M", False, "GO801 on POST /v1/nudges/{id}/dismiss (p 0.6)"),
    "M-I1": ("M", True, "exit 0; rules.status not_run:server_dialect, dialect mysql; 26 routes, 26 contracts, 0 rule items; empty proposals; 0 docker calls; no SQLite file; review list 0 items"),
    "M-I2": ("M", True, "4 owner_uses, 2 json_dash, status_facts only for Nudge.delivery; no choices on quiet_start, quiet_end, nudge_time"),
    "M-P1": ("M", True, "go_spec_policy == GoPolicy.from_spec in every field; undeclared_tables == [SchemaMigration]"),
    "M-R1": ("M", True, "exit 0, pass, 0 findings, 480 cases; normalised sha256 351c3104c613c54a"),
    # arm J (5.5)
    "J-0": ("J", True, "pass, 0 findings; normalised report identical to the 978b468 library"),
    "J-A": ("J", True, "layer A on the original: 0 findings"),
    "J-SM1": ("J", False, "layer A 0 (by design); B or D reports it (p 0.5)"),
    "J-SM2": ("J", False, "layer A 0 (by design); D reports it (p 0.8)"),
    # arm 2 (5.6): Tier 2, K8 (GO805) and K10 (J0')
    "P2-805A": ("2", True, "S2 404 (3 linked raises); S3 404 (6); Streak `unclear` or none (its refusals are error values, not constants)"),
    "P2-805B": ("2", True, "GO805|GET /tickets/:tid|not_owned on the S3 copy whose get answers 403"),
    "P2-805C": ("2", True, "0 GO805 on S1, S2, S3, Streak, job-scheduler"),
    "P2-805D": ("2", True, "GO805 out of GO_GATED and gated_note(); GO804 and GO806 text byte-identical to 978b468"),
    "P2-DEF": ("2", False, "K9 not built (Q6): reported; lattice/spec/check.py unchanged"),
    "P2-J0": ("2", True, "every Python and Go cell of J0' cites an existing file:line and a test or results row (100%)"),
    # arm H (5.8)
    "H-PI1": ("H", True, "route recall >= 0.90; auth exact on >= 0.85; 0 extra routes"),
    "H-PI2": ("H", True, "0 confident-wrong owners; every truth owner inferred or unclear naming it"),
    "H-PI3": ("H", True, "verified share >= 0.50; every refuted item has a correction or baseline entry; every unexercised item a reason"),
    "H-PI4": ("H", True, ">= 0.95 retained on variant2"),
    "H-PI5": ("H", False, "the truth machine's first edge proposed (p 0.7)"),
    "H-PI7": ("H", True, "the import 10x: identical (10/10)"),
    "H-PR3": ("H", True, "R with the imported spec: identical after the correction map"),
    "H-PA1": ("H", True, "private columns within truth private; owners within truth owners"),
    "H-PA0": ("H", True, "layer A on the original under go_spec_policy: 0 findings outside the baseline"),
    "H-JUDGE": ("H", True, "share of truth owner-scoped routes judged by layer A >= 0.75"),
    "H-X": ("H", True, "wall time of the import <= 180 s"),
    # cross-arm (5.9)
    "PW": ("P", True, "snapshots of every fixture and copy: 0 changed; 0 docker/claude shim calls outside M-R1"),
    "PB-F": ("P", True, "section 8's frozen table at 978b468: all identical"),
    "PB-X": ("X", True, "default gocheck extract on the 7 round-1 dirs, goapp and fixtures/runs: 9 of 9 identical"),
    "PB-G": ("P", True, "layer A findings and notes identical except the routes Amendment 2 lists"),
    "PB-P": ("P", True, "Python rules imports, no inferences, derive_policy identical"),
    "PB-P2": ("P", True, "decoy studio.toml: the Python make_app"),
    "PB-N": ("P", True, "stack-neutral outputs identical"),
    "PB-H": ("P", True, "help texts and MCP tools/list identical"),
    "PB-T": ("P", True, "the same failing, skipped and error ids as 978b468, plus the new tests passing"),
    "PB-D": ("P", True, "dogfood/studio check passes"),
}  # fmt: skip
ROWS_OF = {a: [i for i, (arm, _, _) in P.items() if arm == a] for a in ARMS}
GATED = [i for i, (_, g, _) in P.items() if g]


# ---- prerequisites: a row is NOT RUN only for one of these --------------------------------------------


def docker_present() -> bool:
    """A real docker client that answers (the recording shim directory is not on this PATH)."""
    path = os.environ.get("G3_REAL_PATH", os.environ.get("PATH", ""))
    exe = shutil.which("docker", path=path)
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


def missing(ctx: Ctx, row: str) -> str | None:
    """The named prerequisite a row lacks, else None. The self-test uses this to tell a legitimate
    NOT RUN from RM1 (a row that did not run although everything it needs was present)."""
    if shutil.which("go") is None:
        return "go is not on PATH"
    row = row.split("[")[0]  # H rows of a self-test carry their target: H-PI1[S2]
    arm = P[row][0]
    if arm == "M" and (ctx.streak_src is None or not (ctx.streak_src / "go.mod").is_file()):
        return "no Streak copy (--streak: a `git archive` of a0679e9)"
    if arm == "J" and (ctx.jobsched_src is None or not (ctx.jobsched_src / "go.mod").is_file()):
        return "no job-scheduler copy (--jobsched: a `git archive` of 00dd1de)"
    if row == "M-R1":
        if ctx.no_docker:
            return "docker is not permitted in this run (--no-docker)"
        if not ctx.docker:
            return "docker is not available"
        if not ctx.driver_ok:
            return f"the driver python {ctx.driver_py} lacks PyMySQL/bcrypt"
    if row in (
        "P2-805A",
        "P2-805C",
    ):  # the Streak and job-scheduler originals are rows of K8's registered probes
        if ctx.streak_src is None or not (ctx.streak_src / "go.mod").is_file():
            return "no Streak copy (--streak: a `git archive` of a0679e9)"
        if ctx.jobsched_src is None or not (ctx.jobsched_src / "go.mod").is_file():
            return "no job-scheduler copy (--jobsched: a `git archive` of 00dd1de)"
    if arm == "H" and ctx.h_target is None:
        return "no held-out target (--s4 DIR, or the stand-ins of --self-test)"
    return None


# ---- the self-hash check (never disabled) --------------------------------------------------------------


def verify_freeze(path: Path, this: Path) -> dict[str, Any]:
    """Every entry of the freeze file is re-hashed under the repository, the running script and every
    :data:`MUST_LIST` file must be listed. Aborts on any problem; nothing can be excluded."""
    return S3R.verify_listed(path, root=REPO, must_list=MUST_LIST, this=this)


def lattice_file_inside() -> str:
    return S3R.lattice_file_inside(REPO)


# ---- the context ---------------------------------------------------------------------------------------


class Ctx(S3M.Ctx):
    """Round 2's S3 context (fixture binary under its harness variable) with the G3 additions."""

    def __init__(self, scratch: Path, out: Path, base_ref: str = BASE_REF) -> None:
        super().__init__(scratch, out, base_ref)
        self.streak_src: Path | None = None
        self.jobsched_src: Path | None = None
        self.owner = "g3-b1"
        self.driver_py = sys.executable
        self.driver_ok = False
        self.no_docker = False
        self.docker = False
        self.runs = 10
        self.h_target: dict[str, Any] | None = None
        self.dump_bin: Path | None = None
        self.pytest_base_log: Path | None = None
        self.extra_snaps: dict[str, Any] = {}
        self.pb_t_targets: Sequence[str] = ()
        self.shim_mark: int | None = None  # shim-log lines when the first test-suite row started

    def env(self, **extra: str) -> dict[str, str]:
        env = super().env()
        env["LATTICE_CONTAINER_OWNER"] = self.owner
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.update(extra)
        return env


def real_path() -> str:
    """PATH without the recording shim directory (for M-R1 only)."""
    return os.environ.get("G3_REAL_PATH", os.environ.get("PATH", ""))


def setup(ctx: Ctx, builds: bool = True) -> None:
    """Snapshot roots, shims, the base link, and (``builds``) the S1, S2 and S3 binaries built to the scratch."""
    os.environ.setdefault("G3_REAL_PATH", os.environ["PATH"])
    for root in ("tests/spec/gotickets",):
        if root not in P1.SNAPSHOT_ROOTS:
            P1.SNAPSHOT_ROOTS.append(root)
    if ctx.h_target and ctx.h_target.get("snapshot_rel"):
        rel = ctx.h_target["snapshot_rel"]
        if rel not in P1.SNAPSHOT_ROOTS:
            P1.SNAPSHOT_ROOTS.append(rel)
    ctx.fx = S3M.Fx()
    ctx.fx_env = S3M.harness_env_name(ctx.fx.harness)
    if builds:
        P1.setup(ctx, "S")  # shims, the base link, snap0, S1 and S2 built
        ok, err, wall = P1.go_build(ctx.fx.module_dir, ctx.scratch / "fxbin" / ctx.fx.package)
        ctx.timings["build_s3_s"] = wall
        if ok:
            ctx.fx_bin = ctx.scratch / "fxbin" / ctx.fx.package
            os.environ[ctx.fx_env] = str(ctx.fx_bin)
        else:
            ctx.fx_error = err
    else:  # the static rows need no binary: P1.setup without its builds
        P1.make_shims(ctx)
        ctx.scratch.mkdir(parents=True, exist_ok=True)
        ctx.out.mkdir(parents=True, exist_ok=True)
        link = ctx.scratch / "base"
        if link.is_symlink():
            link.unlink()
        shutil.rmtree(link, ignore_errors=True)
        link.symlink_to(ctx.base_dir, target_is_directory=True)  # type: ignore[arg-type]
        ctx.snap0 = P1.snapshots()
    ctx.docker = docker_present() and not ctx.no_docker
    ctx.driver_ok = driver_has_pymysql(ctx.driver_py)


def ensure_dump(ctx: Ctx) -> Path:
    """A debug build of the checker (a scratch copy of the library's Go sources plus :data:`DUMP_GO`)
    that prints, per route, whether layer A judges it. The library is never edited."""
    if ctx.dump_bin is not None:
        return ctx.dump_bin
    d = ctx.scratch / "dump" / "src"
    shutil.rmtree(d.parent, ignore_errors=True)
    shutil.copytree(REPO / "lattice/gocheck/src", d)
    (d / "zz_g3dump.go").write_text(DUMP_GO)
    out = ctx.scratch / "dump" / "g3dump"
    ok, err, _ = P1.go_build(d, out, flags="-mod=mod")
    if not ok:
        raise RuntimeError(f"the route-dump build failed: {err[-400:]}")
    ctx.dump_bin = out
    return out


DUMP_GO = r"""package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"sort"
)

func init() {
	if len(os.Args) < 2 || os.Args[1] != "g3dump" {
		return
	}
	raw, _ := io.ReadAll(os.Stdin)
	var in input
	if err := json.Unmarshal(raw, &in); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	if in.Policy.HelperDepth <= 0 {
		in.Policy.HelperDepth = 3
	}
	m, err := Load(in.Root)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(3)
	}
	c := newChecker(m, &in.Policy)
	type row struct {
		Route           string `json:"route"`
		Handler         string `json:"handler"`
		Bound           bool   `json:"bound"`
		Unreadable      bool   `json:"unreadable"`
		HandlerComplete bool   `json:"handler_complete"`
		MwComplete      bool   `json:"mw_complete"`
		Judged          bool   `json:"judged"`
	}
	rows := []row{}
	for _, r := range c.routes {
		h, hc := "", true
		if r.Handler != nil {
			h = r.Handler.Name
			_, hc = c.reach(append([]*Func{r.Handler}, r.Wraps...), c.pol.HelperDepth, r.Wraps...)
		}
		_, mc := c.reach(append(append([]*Func{}, r.Middleware...), r.Wraps...), c.pol.HelperDepth, r.Wraps...)
		rows = append(rows, row{r.Key(), h, r.Bound, r.Unreadable, hc, mc, !r.Unreadable && hc && mc})
	}
	sort.SliceStable(rows, func(i, j int) bool { return rows[i].Route < rows[j].Route })
	out, _ := json.Marshal(rows)
	fmt.Println(string(out))
	os.Exit(0)
}
"""  # noqa: E501


def route_dump(ctx: Ctx, module_dir: Path, policy: Any) -> list[dict[str, Any]]:
    """Per route of ``module_dir``: handler, whether K1 bound it, and whether layer A judges it (its
    handler's and middleware's reach complete) under ``policy`` (a GoPolicy)."""
    binary = ensure_dump(ctx)
    proc = subprocess.run(
        [str(binary), "g3dump"],
        input=json.dumps({"root": str(module_dir.resolve()), "policy": policy.to_json()}),
        capture_output=True, text=True, check=False, timeout=300,
    )  # fmt: skip
    if proc.returncode != 0:
        raise RuntimeError(f"the route dump failed on {module_dir}: {proc.stderr[-300:]}")
    return json.loads(proc.stdout)  # type: ignore[no-any-return]


# a subprocess that loads a spec from a module copy (Streak's `spec`, job-scheduler's `spec.tickets`
# would otherwise shadow each other in this process) and runs layer A under a policy
LA_SCRIPT = r"""
import json, sys, importlib
from pathlib import Path
from lattice.gocheck import GoPolicy, run_gocheck
from lattice.gocheck.derive import go_spec_policy, undeclared_tables
mod, attr = sys.argv[1].split(":")
go_src = Path(sys.argv[2]); kinds = sys.argv[3].split(",")
spec = getattr(importlib.import_module(mod), attr)
if callable(spec) and not hasattr(spec, "contracts"):
    spec = spec()
out = {}
for kind in kinds:
    if kind == "from_spec":
        pol = GoPolicy.from_spec(spec)
    else:
        pol = go_spec_policy(spec, go_src)
    found = run_gocheck(go_src, pol, strict=True)
    out[kind] = {"findings": [{"code": f.code, "key": f.key, "file": f.file, "line": f.line, "message": f.message[:240]} for f in found],
                 "policy": pol.to_json()}
st = GoPolicy.from_spec(spec); ov = go_spec_policy(spec, go_src)
out["equal"] = ov == st
out["undeclared"] = undeclared_tables(spec, go_src)
print(json.dumps(out))
"""


def layer_a(
    ctx: Ctx, copy: Path, spec_ref: str, kinds: str = "from_spec", *, cwd: Path | None = None
) -> dict[str, Any]:
    """Layer A on ``copy`` under the named policies, in a subprocess started in ``cwd`` (default
    ``copy``) so ``spec_ref`` (``module:attr``) imports from the copy."""
    env = ctx.env(PYTHONPATH=os.pathsep.join([str(REPO), str(cwd or copy)]))
    rc, out, err, _ = P1.run(
        [ctx.py, "-c", LA_SCRIPT, spec_ref, str(copy), kinds], cwd=cwd or copy, env=env, timeout=600
    )
    if rc != 0:
        raise RuntimeError(f"layer A on {copy} failed: {err[-500:]}")
    return json.loads(out.strip().splitlines()[-1])  # type: ignore[no-any-return]


def front(root: Path, package: str = ".") -> Any:
    return P2.front(root, package)


def load_truth(path: Path = GP3 / "truth.json") -> dict[str, Any]:
    return json.loads(path.read_text())  # type: ignore[no-any-return]


# ---- arm 0 ---------------------------------------------------------------------------------------------


def gp3_policy() -> Any:
    from lattice.gocheck import GoPolicy

    pol = load_truth()["policy"]
    return GoPolicy(tables=pol["tables"], owners=pol["owners"])


def gp3_keys() -> list[str]:
    from lattice.gocheck import run_gocheck

    return sorted(f.key for f in run_gocheck(GP3, gp3_policy(), strict=True))


def route_keys(keys: Sequence[str], route: str) -> list[str]:
    return [k for k in keys if k.split("|")[1] == route]


def p0_wrap(ctx: Ctx) -> dict[str, Any]:
    keys = gp3_keys()
    got = {r: route_keys(keys, r) for r in ("GET /w1/:id", "GET /w2/:id", "GET /w3/:id")}
    want = {"GET /w1/:id": ["GO801|GET /w1/:id|Widget"], "GET /w2/:id": [], "GET /w3/:id": []}
    dump = {r["route"]: r for r in route_dump(ctx, GP3, gp3_policy())}
    return result("P0-WRAP", "layer A on goparity3: one-level wrapper unscoped / scoped / two levels", P["P0-WRAP"][2], got,
                  got == want, w4_reported=route_keys(keys, "GET /w4/:id"),
                  roots={r: {"handler": dump[r]["handler"], "bound": dump[r]["bound"]} for r in sorted(dump) if "/w" in r},
                  reading="w4 (HandleMsg: err.Error() on the error the parameter returns) is reported, not gated: the receiver cannot be inferred and stays opaque")  # fmt: skip


def p0_opq(ctx: Ctx) -> dict[str, Any]:
    keys = gp3_keys()
    got = {r: route_keys(keys, r) for r in ("GET /m1/w/:id", "GET /m2/w/:id")}
    want = {"GET /m1/w/:id": ["GO801|GET /m1/w/:id|Widget"], "GET /m2/w/:id": []}
    return result("P0-OPQ", "layer A on goparity3: hmac.New(...).Write/Sum guard vs a guard handing *gin.Context to an outside method",
                  P["P0-OPQ"][2], got, got == want)  # fmt: skip


def p0_body(ctx: Ctx) -> dict[str, Any]:
    from lattice.importer.emit_spec import build_spec
    from lattice.importer.lift import load_profile
    from lattice.importer.profile_gen import generate_profile

    fe = front(GP3)
    profile = load_profile(
        generate_profile(fe.model, None), f"lattice_import_profile_{uuid.uuid4().hex}"
    ).profile()
    spec = build_spec(fe.model, profile, users={}, programs={}, proposals=(), public={})
    c = [c for c in spec.contracts if (c.method, c.path) == ("POST", "/widgets")][0]
    inf = [i for i in fe.inferences if i["rule"] == "body_model"]
    p = c.params[0] if len(c.params) == 1 else None
    got = {"params": [(q.name, q.source, str(q.scalar), q.required, q.max_len) for q in c.params],
           "body_model_inferences": [(i["route"], i["model"], i["fields"]) for i in inf]}  # fmt: skip
    ok = (p is not None and (p.name, p.source, p.required, p.max_len) == ("title", "body", True, 40) and str(p.scalar).endswith("Str")
          and got["body_model_inferences"] == [("POST /widgets", "widgetBody", ["title"])])  # fmt: skip
    return result("P0-BODY", "K3 on goparity3 b1", P["P0-BODY"][2], got, ok)


def choices_of(fe: Any) -> dict[str, list[Any]]:
    return {
        f"{t.name}.{c.name}": list(c.choices)
        for t in fe.model.tables
        for c in t.columns
        if c.choices
    }


def p0_choices2(ctx: Ctx) -> dict[str, Any]:
    g = choices_of(front(GP3))
    s2 = choices_of(front(SPEC_DIR, "goginapp"))
    s3 = choices_of(front(SPEC_DIR, "gotickets"))
    got = {"goparity3": g, "S2": s2, "S3": s3}
    ok = (
        g == {"Gadget.kind": ["a", "b"]}
        and s2 == {"Run.status": ["queued", "cancelled"]}
        and s3 == {"Ticket.status": ["open", "closed"]}
    )
    return result("P0-CHOICES2", "K5 on goparity3, S2, S3", P["P0-CHOICES2"][2], got, ok)


def p0_reg(ctx: Ctx) -> dict[str, Any]:
    subs = {}
    for name, fn in (("P0-PARSE", P2.probe_p0_parse), ("P0-OMIT", P2.probe_p0_omit), ("P0-BIND", P2.probe_p0_bind),
                     ("P0-LIBMW", P2.probe_p0_libmw), ("P0-PRIV", P2.probe_p0_priv), ("PA4", P1.probe_pa4)):  # fmt: skip
        try:
            r = fn(ctx)
            subs[name] = {"pass": r["pass"], "observed": r["observed"]}
        except Exception as err:  # noqa: BLE001 -- a sub-row that raises is a FAIL
            subs[name] = {"pass": False, "observed": f"{type(err).__name__}: {err}"}
    return result("P0-REG", "round 2's static rows re-expressed on the G3 runner", P["P0-REG"][2],
                  {k: v["pass"] for k, v in subs.items()}, all(v["pass"] for v in subs.values()), sub_rows=subs)  # fmt: skip


ARM0: dict[str, Callable[[Ctx], dict[str, Any]]] = {
    "P0-WRAP": p0_wrap, "P0-OPQ": p0_opq, "P0-BODY": p0_body, "P0-CHOICES2": p0_choices2, "P0-REG": p0_reg,
}  # fmt: skip


# ---- arm S ---------------------------------------------------------------------------------------------

S3_MUTANTS_G3 = {
    "ST2": {"what": "gotickets copy: get drops AND reporter_id = ?", "op": "drop_owner", "method": "GET",
            "path": "/tickets/:tid", "column": "reporter_id"},
    "ST3": {"what": "gotickets copy: reopen drops AND reporter_id = ?", "op": "drop_owner", "method": "POST",
            "path": "/tickets/:tid/reopen", "column": "reporter_id"},
}  # fmt: skip


def s3_import(ctx: Ctx) -> Path:
    """The CLI rules-first import of S3 (once); the CLI puts the import root on sys.path itself."""
    if "s3_imp" not in ctx.cache:
        d = ctx.scratch / "imp" / "s3"
        t = S3R.import_cli(ctx, "tests/spec", d)
        ctx.timings["import_s3_s"] = t["wall_s"]
        if t["rc"] != 0:
            raise RuntimeError(f"the S3 import failed: {t['stderr'][-500:]}")
        ctx.cache["s3_imp"] = d
    return ctx.cache["s3_imp"]  # type: ignore[no-any-return]


def s3_seed_map(ctx: Ctx) -> dict[str, str]:
    if "s3_seed" not in ctx.cache:
        cand = P1.load_candidate(s3_import(ctx))
        ctx.cache["s3_seed"] = P1.verdict_map(
            S3R.r_on_spec(ctx, cand.SPEC, "seed", SPEC_DIR, "bare")
        )
    return ctx.cache["s3_seed"]  # type: ignore[no-any-return]


def item_audit(rep: Mapping[str, Any]) -> dict[str, Any]:
    items = rep["rules"]["items"]
    n = len(items)
    count: dict[str, int] = {}
    for v in items.values():
        count[v["verdict"]] = count.get(v["verdict"], 0) + 1
    verified = sum(count.get(k, 0) for k in VERIFIED)
    corrections = {c["item"] for c in rep["rules"]["corrections"]}
    baseline = {b["item"] for b in rep["rules"]["baseline"]}
    refuted = [i for i, v in items.items() if v["verdict"] == "refuted"]
    refuted_bad = [i for i in refuted if not ((items[i].get("correction") or i in corrections or i in baseline)
                                              and (items[i].get("reason") or items[i].get("correction", {}).get("reason")))]  # fmt: skip
    unex = {i: v["reason"] for i, v in items.items() if v["verdict"] == "unexercised"}
    return {"items": n, "verdicts": count, "verified": verified, "share": round(verified / n, 4) if n else 0.0,
            "refuted": refuted, "refuted_without_correction": refuted_bad,
            "unexercised_without_reason": [i for i, w in unex.items() if not w], "unexercised": unex,
            "corrections": len(rep["rules"]["corrections"])}  # fmt: skip


def ps_pi3(ctx: Ctx) -> dict[str, Any]:
    rep = P2.report_of(s3_import(ctx))
    a = item_audit(rep)
    r2 = {
        i: rep["rules"]["items"].get(i, {}).get("verdict")
        for i in ("R2 POST /queues/{qid}/tickets", "R2 POST /tickets/{tid}/handoffs")
    }
    ok = (
        a["share"] >= 0.60
        and all(v in VERIFIED for v in r2.values())
        and not a["refuted_without_correction"]
        and not a["unexercised_without_reason"]
    )
    return result("PS-PI3-3", "rule items of the S3 import on the seed (K3)", P["PS-PI3-3"][2],
                  {"items": a["items"], "verdicts": a["verdicts"], "verified_share": a["share"], "refuted": a["refuted"], "r2_creates": r2,
                   "refuted_without_correction": a["refuted_without_correction"], "unexercised_without_reason": a["unexercised_without_reason"]},
                  ok, in_predicted_range={"items": 63 <= a["items"] <= 70, "share": 0.60 <= a["share"] <= 0.68, "refuted": len(a["refuted"]) <= 2},
                  unexercised=a["unexercised"], corrections=a["corrections"],
                  reading="the gate is round 2's (share >= 0.60, not lowered) plus 'both R2 creates verified'; the 0.60-0.68 band is the prediction")  # fmt: skip


def ps_pi4(ctx: Ctx) -> dict[str, Any]:
    cand = P1.load_candidate(s3_import(ctx))
    seed = s3_seed_map(ctx)
    v2 = P1.verdict_map(S3R.r_on_spec(ctx, cand.SPEC, "variant2", SPEC_DIR, "bare"))
    sc = S3R.score_pi4(seed, v2)
    return result("PS-PI4-3", "the corrected S3 spec re-probed on variant2", P["PS-PI4-3"][2],
                  {k: sc[k] for k in ("seed_verified", "still_verified", "fraction", "lost")}, bool(sc["pass"]))  # fmt: skip


def ps_pr3(ctx: Ctx) -> dict[str, Any]:
    rep = P2.report_of(s3_import(ctx))
    theirs, mine = S3R.mapped_verdicts(rep), s3_seed_map(ctx)
    diff = S3R.diff_maps(theirs, mine)
    return result("PS-PR3-3", "layer R on S3 with the imported candidate_spec.py (in-process, the import root on sys.path: K7)",
                  P["PS-PR3-3"][2], {"items_import": len(theirs), "items_rerun": len(mine), "corrections": len(rep["rules"]["corrections"]),
                                     "differences [import, rerun]": diff}, not diff)  # fmt: skip


def s3_mutant_row(ctx: Ctx, pid: str, mid: str, want: str) -> dict[str, Any]:
    cand = P1.load_candidate(s3_import(ctx))
    if (
        mid in S3_MUTANTS_G3
    ):  # round 2's S3 mutator given G3's two more edits, only while this row runs
        S3M.MUTANTS[mid] = S3_MUTANTS_G3[mid]
    try:
        try:
            copy = S3M.mutate_fixture(ctx, mid)
        except (
            LookupError,
            ValueError,
        ) as err:  # the edit does not apply: a failed edit on a real fixture is a FAIL
            return result(
                pid,
                f"{mid} on a scratch copy of S3",
                P[pid][2],
                f"the edit did not apply: {err}",
                False,
            )
    finally:
        if mid in S3_MUTANTS_G3:
            S3M.MUTANTS.pop(mid, None)
    a = S3M.layer_a_new(cand, ctx.fx, copy)
    keys = [n["key"] for n in a["new"]]
    return result(pid, f"{mid} on a scratch copy of S3 under go_spec_policy of its candidate spec", P[pid][2],
                  {"new_keys": keys, "original_total": a["original_total"], "original_outside_baseline": a["outside"]},
                  want in keys and not a["outside"], expected=want)  # fmt: skip


def ps_s3a(ctx: Ctx) -> dict[str, Any]:
    from lattice.gocheck import GoPolicy, run_gocheck
    from lattice.gocheck.derive import go_spec_policy

    cand = P1.load_candidate(s3_import(ctx))
    mod = ctx.fx.module_dir
    got = {"from_spec": sorted(f.key for f in run_gocheck(mod, GoPolicy.from_spec(cand.SPEC), strict=True)),
           "go_spec_policy": sorted(f.key for f in run_gocheck(mod, go_spec_policy(cand.SPEC, mod), strict=True))}  # fmt: skip
    dump = route_dump(ctx, mod, go_spec_policy(cand.SPEC, mod))
    return result("PS-S3A", "layer A on the S3 original", P["PS-S3A"][2], got, not any(got.values()),
                  judged={"routes": len(dump), "judged": sum(r["judged"] for r in dump)})  # fmt: skip


def pi2_g3(ctx: Ctx, d: Path) -> dict[str, Any]:
    """Round 2's PI2 on S2, plus K3's one ``body_model`` inference (so 8 inferences, not 7)."""
    rep, cand = P2.report_of(d), P1.load_candidate(d)
    inf = rep["rules"].get("inferences", [])
    x = P1.s2_extract(ctx)
    hows = {(o["file"], o["line"]): o["how"] for r in x["routes"] for o in r["owner_uses"] if (o["model"], o["column"]) == ("Run", "user_id")}  # fmt: skip
    owner = [i for i in inf if i.get("rule") == "owner_uses"]
    ev = owner[0]["evidence"] if len(owner) == 1 else []
    got = sorted(
        hows.get((f, int(n)), "?") for f, n in (e.removeprefix("code:").rsplit(":", 1) for e in ev)
    )
    counts: dict[str, int] = {}
    for i in inf:
        counts[i["rule"]] = counts.get(i["rule"], 0) + 1
    bind = [i for i in inf if i["rule"] == "bind_status"]
    tables = cand.PROFILE.api.tables
    owners = {t: tables[t].owner for t in tables}
    ok = (len(owner) == 1 and (owner[0]["table"], owner[0]["column"]) == ("Run", "user_id") and len(ev) == 4
          and got == ["assign", "compare", "compare", "compare"] and owners == {"User": None, "Config": None, "Run": "user_id"}
          and len(inf) == 8 and counts == {"owner_uses": 1, "path_parse": 3, "bind_status": 1, "status_facts": 1, "json_dash": 1, "body_model": 1}
          and bind[0].get("status") == 422)  # fmt: skip
    return result("PI2", "owners and inferences[] of the S2 import (round 2's, plus K3's body_model)", "7 of round 2 + 1 body_model",
                  {"inferences": len(inf), "by_rule": counts, "how": got, "owners": owners}, ok)  # fmt: skip


def pi3_g3(ctx: Ctx, d: Path) -> dict[str, Any]:
    a = item_audit(P2.report_of(d))
    ok = (
        a["share"] >= 0.60
        and not a["refuted_without_correction"]
        and not a["unexercised_without_reason"]
    )
    return result("PI3", "rule items of the S2 import on the seed", "41-44 items, share 0.61-0.68, refuted 0-1",
                  {"items": a["items"], "verified_share": a["share"], "refuted": a["refuted"]}, ok,
                  in_predicted_range={"items": 41 <= a["items"] <= 44, "share": 0.61 <= a["share"] <= 0.68, "refuted": len(a["refuted"]) <= 1})  # fmt: skip


def ps_s2(ctx: Ctx) -> dict[str, Any]:
    if ctx.s2_error:
        raise RuntimeError(f"S2 does not build: {ctx.s2_error[:300]}")
    t = P1.import_s2(ctx, ctx.scratch / "imp" / "s2-lazy", lazy_build=True)
    ctx.timings["s2_import_lazy_build_s"] = t["wall_s"]
    d = ctx.scratch / "imp" / "s2"
    r = P1.import_s2(ctx, d)
    if r["rc"] != 0:
        raise RuntimeError(f"the S2 import failed: {r['stderr'][-400:]}")
    subs: dict[str, dict[str, Any]] = {}

    def run(name: str, fn: Callable[[], dict[str, Any]]) -> None:
        try:
            row = fn()
            subs[name] = {
                "pass": row["pass"],
                "observed": row["observed"],
                **(
                    {"in_predicted_range": row["evidence"].get("in_predicted_range")}
                    if row["evidence"].get("in_predicted_range") is not None
                    else {}
                ),
            }
        except Exception as err:  # noqa: BLE001 -- a sub-row that raises is a FAIL
            subs[name] = {"pass": False, "observed": f"{type(err).__name__}: {err}"}

    run("PI1", lambda: P2.probe_pi1(ctx, d))
    run("PI2", lambda: pi2_g3(ctx, d))
    run("PI3", lambda: pi3_g3(ctx, d))
    run("PI4", lambda: P1.probe_pi4(ctx, d))
    run("PI5", lambda: P2.probe_pi5(ctx, d))
    run("PI6", lambda: P2.probe_pi6(ctx, d))
    run("PI7", lambda: P1.probe_pi7(ctx, ctx.runs))
    run("PR3", lambda: P2.probe_pr3(ctx, d))
    run("PR-T2", lambda: P2.probe_pr_t2(ctx, d))
    run("PA1", lambda: P2.probe_pa1(ctx))
    run("PA2", lambda: P1.probe_pa2(ctx))
    run("PA3", lambda: P2.probe_pa3(ctx, d))
    ctx.cache["s2_imp"] = d
    return result("PS-S2", "S2 regressions PI1-PI7, PR3, PR-T2, PA1-PA3 (round 2's rules; PI2 and PI3 as G3 states them)", P["PS-S2"][2],
                  {k: v["pass"] for k, v in subs.items()}, all(v["pass"] for v in subs.values()), sub_rows=subs)  # fmt: skip


def ps_s1(ctx: Ctx) -> dict[str, Any]:
    if ctx.s1_error:
        raise RuntimeError(f"S1 does not build: {ctx.s1_error[:300]}")
    subs: dict[str, dict[str, Any]] = {}
    for name, fn in (("PR1", lambda: P1.probe_pr1(ctx)), ("PR2", lambda: P1.probe_pr2(ctx)), ("PR4", lambda: P1.probe_pr4(ctx, ctx.runs)),
                     ("PB1", lambda: P2.probe_pb1(ctx)), ("PB1-CLI", lambda: P2.probe_pb1_cli(ctx)), ("PB2", lambda: P1.probe_pb2(ctx)),
                     ("PB3", lambda: P1.probe_pb3(ctx, ctx.runs))):  # fmt: skip
        try:
            row = fn()
            subs[name] = {"pass": row["pass"], "observed": row["observed"]}
        except Exception as err:  # noqa: BLE001
            subs[name] = {"pass": False, "observed": f"{type(err).__name__}: {err}"}
    return result("PS-S1", "S1 regressions PR1, PR2, PR4, PB1, PB1-CLI, PB2, PB3 (round 2's rules)", P["PS-S1"][2],
                  {k: v["pass"] for k, v in subs.items()}, all(v["pass"] for v in subs.values()), sub_rows=subs)  # fmt: skip


ARMS_S: dict[str, Callable[[Ctx], dict[str, Any]]] = {
    "PS-PI3-3": ps_pi3, "PS-PI4-3": ps_pi4, "PS-PR3-3": ps_pr3,
    "PS-JM31": lambda c: s3_mutant_row(c, "PS-JM31", "JM31", "GO801|POST /tickets/:tid/close|Ticket"),
    "PS-ST2": lambda c: s3_mutant_row(c, "PS-ST2", "ST2", "GO801|GET /tickets/:tid|Ticket"),
    "PS-ST3": lambda c: s3_mutant_row(c, "PS-ST3", "ST3", "GO801|POST /tickets/:tid/reopen|Ticket"),
    "PS-S3A": ps_s3a, "PS-S2": ps_s2, "PS-S1": ps_s1,
}  # fmt: skip


# ---- arm M: Streak a0679e9 ----------------------------------------------------------------------------------

ANYHABIT = '''
// AnyHabit loads a non-deleted habit, whoever owns it.
func AnyHabit(tx *gorm.DB, id string) (Habit, error) {
	var h Habit
	err := tx.Where("id = ? AND deleted_at IS NULL", id).Take(&h).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return h, ErrNotFound
	}
	return h, err
}
'''  # fmt: skip
SM_EDITS: dict[str, list[tuple[str, str, str]]] = {
    "SM1": [
        (
            "internal/habits/routes.go",
            "x, err := db.OwnedHabit(h.d.Q(c), httpapi.CallerOf(c).UserID, id)",
            "x, err := db.AnyHabit(h.d.Q(c), id)",
        )
    ],
    "SM2": [
        (
            "internal/habits/routes.go",
            "\t\tcur, err := db.OwnedHabit(tx, who.UserID, id)\n\t\tif err != nil {\n\t\t\treturn err\n\t\t}\n\t\tupd := cur",
            "\t\tcur, err := db.AnyHabit(tx, id)\n\t\tif err != nil {\n\t\t\treturn err\n\t\t}\n\t\tupd := cur",
        )
    ],  # fmt: skip
    "SM3": [
        (
            "internal/completions/routes.go",
            "\tif _, err := db.OwnedHabit(h.d.Q(c), who.UserID, habitID); err != nil {",
            "\t_ = who\n\tif _, err := db.AnyHabit(h.d.Q(c), habitID); err != nil {",
        )
    ],
    "SM4": [
        (
            "internal/nudges/routes.go",
            "\t\tn, err := db.OwnedNudge(tx, who.UserID, id)\n",
            '\t\tvar n db.Nudge\n\t\terr := tx.Where("id = ?", id).Take(&n).Error\n',
        )
    ],  # fmt: skip
}
SM_WANT = {  # the registered detections (prereg 5.4): (method, route as the key writes it, tables)
    "M-SM1": ("GET /v1/habits/:id", ("Habit",)),
    "M-SM2": ("PATCH /v1/habits/:id", ("Habit",)),
    "M-SM3": ("GET /v1/habits/:id/completions", ("Habit", "Completion")),
    "M-SM4": ("POST /v1/nudges/:id/dismiss", ("Nudge", "Habit")),
}  # fmt: skip


def streak_work(ctx: Ctx) -> Path:
    """A scratch working copy of the Streak archive (the archive itself is never touched)."""
    if "streak_work" not in ctx.cache:
        dest = ctx.scratch / "streak-work"
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(ctx.streak_src, dest)  # type: ignore[arg-type]
        ctx.cache["streak_work"] = dest
    return ctx.cache["streak_work"]  # type: ignore[no-any-return]


def streak_mutant(ctx: Ctx, mid: str) -> Path:
    dest = ctx.scratch / "mut" / mid.lower() / "streak"
    shutil.rmtree(dest.parent, ignore_errors=True)
    shutil.copytree(streak_work(ctx), dest)
    owned = dest / "internal/db/owned.go"
    if mid != "SM4":
        owned.write_text(owned.read_text() + ANYHABIT)
    for rel, old, new in SM_EDITS[mid]:
        f = dest / rel
        text = f.read_text()
        if text.count(old) != 1:
            raise ValueError(f"{mid}: {rel}: the edit matches {text.count(old)} times")
        f.write_text(text.replace(old, new))
    return dest


def streak_layer_a(ctx: Ctx, copy: Path) -> dict[str, Any]:
    return layer_a(ctx, copy, "spec:SPEC", "from_spec")


def m_a0(ctx: Ctx) -> dict[str, Any]:
    res = streak_layer_a(ctx, streak_work(ctx))
    found = res["from_spec"]["findings"]
    return result("M-A0", "layer A on the Streak original under GoPolicy.from_spec(SPEC)", P["M-A0"][2],
                  {"findings": [f"{f['key']} ({f['file']}:{f['line']})" for f in found]}, not found,
                  classified=[{"key": f["key"], "message": f["message"]} for f in found],
                  reading="every finding is listed; a false positive fails the row (range 0-2 is the prediction)")  # fmt: skip


def m_a1(ctx: Ctx) -> dict[str, Any]:
    from lattice.gocheck import GoPolicy

    res = streak_layer_a(ctx, streak_work(ctx))
    pol = res["from_spec"]["policy"]

    class J:  # a policy given as JSON
        def to_json(self) -> dict[str, Any]:
            return pol

    dump = route_dump(ctx, streak_work(ctx), J())
    wrapped = [r for r in dump if r["bound"]]
    base_note = "the 978b468 count is in the data of Arm M (m4/route-dump): 1 of 26 (GET /health) was judged; every handler was Handle.func1"
    _ = GoPolicy
    return result("M-A1", "judged routes (root and middleware reach complete) on Streak", P["M-A1"][2],
                  {"routes": len(dump), "judged": sum(r["judged"] for r in dump), "wrapped_routes": len(wrapped),
                   "wrapped_judged": sum(r["judged"] for r in wrapped),
                   "not_judged": [r["route"] for r in dump if not r["judged"]]},
                  True, reading=base_note + "; informational, so always reported as pass=True")  # fmt: skip


def m_sm(ctx: Ctx, pid: str) -> dict[str, Any]:
    mid = pid.removeprefix("M-")
    want_key, tables = SM_WANT[pid]
    copy = streak_mutant(ctx, mid)
    ok_build, err, _ = P1.go_build(
        copy / "cmd/server", ctx.scratch / "mut" / mid.lower() / "streak-bin", flags="-mod=readonly"
    )
    res = streak_layer_a(ctx, copy)
    keys = [f["key"] for f in res["from_spec"]["findings"]]
    method, route = want_key.split(" ", 1)
    plain = route.replace("/v1", "", 1)
    exact = [f"GO801|{want_key}|{t}" for t in tables]
    hit_exact = [k for k in keys if k in exact]
    hit_unprefixed = [
        k
        for k in keys
        if k.split("|")[0] == "GO801"
        and k.split("|")[1] == f"{method} {plain}"
        and k.split("|")[2] in tables
    ]
    if pid in ("M-SM1", "M-SM2"):
        passed = bool(hit_exact)
    else:
        passed = bool(hit_exact or hit_unprefixed)
    return result(pid, f"Streak copy ({mid}) under GoPolicy.from_spec(SPEC)", P[pid][2],
                  {"new_keys": keys, "builds": ok_build, "detected_exact_key": bool(hit_exact), "detected_without_the_v1_prefix": hit_unprefixed},
                  passed if P[pid][1] else bool(passed), build_error=None if ok_build else err[-300:],
                  reading=("the key is written with the route as the checker reads it: a router handed through a struct field "
                           "(`r.Member.GET`) keeps its tail, so `/v1` is not in the key") if not hit_exact else "")  # fmt: skip


def streak_import(ctx: Ctx) -> tuple[Path, dict[str, Any]]:
    if "streak_imp" not in ctx.cache:
        d = ctx.scratch / "imp" / "streak"
        shutil.rmtree(d, ignore_errors=True)
        ctx.shim_log.write_text(ctx.shim_log.read_text())
        rc, out, err, wall = P1.run([ctx.py, "-m", "lattice.importer", str(streak_work(ctx)), "--package", ".", "--rules-only",
                                     "--framework", "gin", "--out", str(d)], env=ctx.env(), timeout=900)  # fmt: skip
        ctx.cache["streak_imp"] = (
            d,
            {"rc": rc, "stdout": out[-400:], "stderr": err[-1200:], "wall_s": wall},
        )
    return ctx.cache["streak_imp"]  # type: ignore[no-any-return]


def m_i1(ctx: Ctx) -> dict[str, Any]:
    d, run_ = streak_import(ctx)
    if run_["rc"] != 0:
        return result("M-I1", "the rules-first import of Streak", P["M-I1"][2], run_, False)
    rep = P2.report_of(d)
    rules = rep["rules"]
    cand = P1.load_candidate(d)
    props = json.loads((d / "proposals.json").read_text())
    docker_calls = [ln for ln in ctx.shim_log.read_text().splitlines() if ln.startswith("docker")]
    sqlite_files = [p.relative_to(ctx.scratch).as_posix() for p in ctx.scratch.rglob("*")
                    if p.is_file() and p.suffix in (".db", ".sqlite", ".sqlite3") and "fxbin" not in p.parts]  # fmt: skip
    rv_rc, rv_out, _, _ = P1.run(
        [ctx.py, "-m", "lattice.importer", "review", str(d), "list"], env=ctx.env()
    )
    listed = [ln for ln in rv_out.splitlines() if "\trule\t" in ln]
    verdict_listed = [ln for ln in listed if "[unprobed]" not in ln]
    got = {"rc": run_["rc"], "status": rules.get("status"), "dialect": rules.get("dialect"), "routes": len({(c.method, c.path) for c in cand.CONTRACTS}),
           "contracts": len(cand.CONTRACTS), "rule_items": len(rules["items"]), "proposals": props, "docker_calls": docker_calls,
           "sqlite_files": sqlite_files, "review_rc": rv_rc, "review_listed": len(listed), "review_listed_with_a_verdict": len(verdict_listed),
           "report_md_first_line": (d / "report.md").read_text().splitlines()[0]}  # fmt: skip
    n_routes = got["routes"]
    ok = (run_["rc"] == 0 and got["status"] == "not_run:server_dialect" and got["dialect"] == "mysql" and n_routes == 26 and got["contracts"] == 26
          and got["rule_items"] == 0 and props == [] and not docker_calls and not sqlite_files and rv_rc == 0 and not listed)  # fmt: skip
    return result("M-I1", "the rules-first import of Streak (K4)", P["M-I1"][2], got, ok,
                  reading=("the registered 'review list lists 0 items' does not hold: review.py (frozen) lists the candidate spec's rule items "
                           "as `pending [unprobed]` whatever the report holds; none of them carries a verdict (review_listed_with_a_verdict)"))  # fmt: skip


def m_i2(ctx: Ctx) -> dict[str, Any]:
    d, run_ = streak_import(ctx)
    if run_["rc"] != 0:
        return result("M-I2", "the inferences of the Streak import", P["M-I2"][2], run_, False)
    inf = P2.report_of(d)["rules"].get("inferences", [])
    by: dict[str, int] = {}
    for i in inf:
        by[i["rule"]] = by.get(i["rule"], 0) + 1
    owners = sorted((i["table"], i["column"]) for i in inf if i["rule"] == "owner_uses")
    facts = [i for i in inf if i["rule"] == "status_facts"]
    fe = front(streak_work(ctx))
    model_choices = choices_of(fe)
    got = {
        "by_rule": by,
        "owner_uses": owners,
        "status_facts": [(f["table"], f["column"], sorted(f["choices"])) for f in facts],
        "model_choices": model_choices,
    }
    ok = (by.get("owner_uses") == 4 and owners == [("Device", "user_id"), ("Habit", "user_id"), ("NotificationPrefs", "user_id"), ("RefreshToken", "user_id")]
          and by.get("json_dash") == 2 and len(facts) == 1 and (facts[0]["table"], facts[0]["column"]) == ("Nudge", "delivery")
          and sorted(facts[0]["choices"]) == ["failed", "pending", "sent", "suppressed"]
          and not any(k in model_choices for k in ("NotificationPrefs.quiet_start", "NotificationPrefs.quiet_end", "NotificationPrefs.nudge_time")))  # fmt: skip
    return result("M-I2", "the inferences of the Streak import (K5)", P["M-I2"][2], got, ok,
                  reading="the prediction lists owner_uses, json_dash, status_facts; K3's body_model inferences (one per route with bound body fields) are reported in by_rule, not scored")  # fmt: skip


def m_p1(ctx: Ctx) -> dict[str, Any]:
    res = layer_a(ctx, streak_work(ctx), "spec:SPEC", "from_spec")
    return result("M-P1", "go_spec_policy(SPEC, copy) against GoPolicy.from_spec(SPEC) (K6)", P["M-P1"][2],
                  {"equal_in_every_field": res["equal"], "undeclared_tables": res["undeclared"]},
                  bool(res["equal"]) and res["undeclared"] == ["SchemaMigration"])  # fmt: skip


def normalise_check(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Arm M's normaliser: drop ``timings``; in the notes, container names and durations."""
    d = json.loads(json.dumps(doc))
    d.pop("timings", None)
    d["notes"] = [
        re.sub(
            r"lattice-mysql-[0-9a-f]+", "lattice-mysql-X", re.sub(r"\b\d+(\.\d+)? ?m?s\b", "Ns", n)
        )
        for n in d.get("notes", [])
    ]
    return d  # type: ignore[no-any-return]


def digest(doc: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()[:16]


def base_gating(ctx: Ctx) -> tuple[dict[str, str], str]:
    """``(GO_GATED, gated_note())`` of the 978b468 library, read in a subprocess of the base archive (cached)."""
    if "base_gating" not in ctx.cache:
        base = ctx.scratch / "base"
        code = "import json;from lattice.gocheck import GO_GATED, gated_note;print(json.dumps([GO_GATED, gated_note()]))"
        proc = subprocess.run([sys.executable, "-c", code], cwd=base, env=dict(os.environ, PYTHONPATH=str(base), PYTHONDONTWRITEBYTECODE="1"),
                              capture_output=True, text=True, check=False)  # fmt: skip
        if proc.returncode != 0:
            raise RuntimeError(f"the 978b468 lattice.gocheck did not import: {proc.stderr[-300:]}")
        ctx.cache["base_gating"] = tuple(json.loads(proc.stdout))
    return ctx.cache["base_gating"]  # type: ignore[no-any-return]


def with_base_gated_note(ctx: Ctx, doc: Mapping[str, Any]) -> dict[str, Any]:
    """The one declared byte difference of a ``lattice check`` report (prereg section 8: ``gated_note()``, GO805 leaves it,
    K8): this tree's layer-A gating note put back to 978b468's text, so that the rest of the report is compared byte for byte."""
    from lattice.gocheck import gated_note

    now, then = gated_note(), base_gating(ctx)[1]
    d = json.loads(json.dumps(doc))
    d["notes"] = [n.replace(now, then) for n in d.get("notes", [])]
    return d  # type: ignore[no-any-return]


def m_r1_command(copy: Path, py: str) -> list[str]:
    """Arm M's command (prereg 5.4 M-R1), run in ``copy``."""
    return [py, "-m", "lattice.spec", "check", "--spec", "spec:SPEC", "--app", "harness:make_app", "--db", "harness:build_db",
            "--runtime", "harness:RUNTIME", "--login", "harness:login", "--services", "harness:SERVICES", "--database", "mysql",
            "--layers", "ACD", "--go-src", str(copy), "--x-repeats", "5", "--json"]  # fmt: skip


def m_r1(ctx: Ctx) -> dict[str, Any]:
    work = streak_work(ctx)
    ok, err, _ = P1.go_build(work / "cmd/server", work / "bin" / "streak-api")
    if not ok:
        raise RuntimeError(f"Streak does not build: {err[-300:]}")
    env = ctx.env(PYTHONPATH=os.pathsep.join([str(REPO), str(work)]), PATH=real_path())
    env.pop("LATTICE_MYSQL_URL", None)
    rc, out, err, wall = P1.run(m_r1_command(work, ctx.driver_py), cwd=work, env=env, timeout=1800)
    try:
        doc = json.loads(out)
    except ValueError:
        return result(
            "M-R1",
            "lattice check --layers ACD --database mysql on Streak (Docker)",
            P["M-R1"][2],
            {"rc": rc, "stderr": err[-500:]},
            False,
        )
    norm = digest(normalise_check(doc))
    findings = doc.get("findings", [])
    got = {
        "rc": rc,
        "verdict": doc.get("verdict"),
        "findings": len(findings),
        "cases": len(doc.get("cases", [])),
        "normalised_sha256": norm,
        "wall_s": round(wall, 1),
    }
    ok_ = rc == 0 and doc.get("verdict") == "pass" and not findings
    return result("M-R1", "lattice check --layers ACD --database mysql on Streak (Docker)", P["M-R1"][2], got, ok_,
                  cases_480=len(doc.get("cases", [])) == 480, normalised_matches_arm_m=norm == "351c3104c613c54a",
                  normalised_matches_arm_m_with_978b468_gated_note=digest(normalise_check(with_base_gated_note(ctx, doc))) == "351c3104c613c54a",
                  reading="pass = exit 0, verdict pass, 0 findings; a normalised-hash or case-count difference is listed and allowed only in layer A's notes")  # fmt: skip


ARM_M: dict[str, Callable[[Ctx], dict[str, Any]]] = {
    "M-A0": m_a0, "M-A1": m_a1, "M-SM1": lambda c: m_sm(c, "M-SM1"), "M-SM2": lambda c: m_sm(c, "M-SM2"),
    "M-SM3": lambda c: m_sm(c, "M-SM3"), "M-SM4": lambda c: m_sm(c, "M-SM4"), "M-I1": m_i1, "M-I2": m_i2,
    "M-P1": m_p1, "M-R1": m_r1,
}  # fmt: skip


# ---- arm J: job-scheduler-go 00dd1de (SQLite; Docker-free) ----------------------------------------------------

J_EDITS = {
    "JSM1": ("internal/app/runs.go", 'Where("id = ? AND application_id = ?", rid, app.ID)', 'Where("id = ?", rid)'),
    "JSM2": ("internal/app/handlers.go", 'if err := a.DB.Where("user_id = ? AND application_id = ?", u.ID, app.ID).First(&m).Error; err != nil {',
             "if err := error(nil); err != nil {"),
}  # fmt: skip
J_SPEC = "spec.tickets:t5"


def jobsched_work(ctx: Ctx) -> Path:
    """A scratch copy of the job-scheduler archive with ``bin/server`` built (check.sh minus its go build)."""
    if "js_work" not in ctx.cache:
        dest = ctx.scratch / "jobsched-work"
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(ctx.jobsched_src, dest)  # type: ignore[arg-type]
        ok, err, _ = P1.go_build(dest / "cmd/server", dest / "bin" / "server")
        if not ok:
            raise RuntimeError(f"job-scheduler does not build: {err[-300:]}")
        ctx.cache["js_work"] = dest
    return ctx.cache["js_work"]  # type: ignore[no-any-return]


def j_check_command(py: str, layers: str, go_src: Path) -> list[str]:
    """The owner's check.sh command (ticket 5) without its `go build`."""
    return [py, "-m", "lattice.spec", "check", "--spec", J_SPEC, "--app", "harness:make_app", "--db", "harness:build_seed_db",
            "--runtime", "harness:RUNTIME", "--login", "spec.seed:login", "--services", "harness:SERVICES", "--layers", layers,
            "--go-src", str(go_src), "--x-repeats", "5", "--json"]  # fmt: skip


def python_on_path(ctx: Ctx) -> str:
    """job-scheduler's jobs run `python ok.py`: a directory with a `python` (this interpreter) first on PATH."""
    d = ctx.scratch / "pybin"
    d.mkdir(parents=True, exist_ok=True)
    link = d / "python"
    if not link.exists():
        link.symlink_to(sys.executable)
    return f"{d}{os.pathsep}{os.environ['PATH']}"


def j_check(
    ctx: Ctx, copy: Path, tree: Path, layers: str
) -> tuple[int, dict[str, Any] | None, str]:
    env = ctx.env(PYTHONPATH=os.pathsep.join([str(tree), str(copy)]), PATH=python_on_path(ctx))
    rc, out, err, _ = P1.run(j_check_command(ctx.py, layers, copy), cwd=copy, env=env, timeout=1800)
    try:
        return rc, json.loads(out), err[-400:]
    except ValueError:
        return rc, None, err[-400:]


def j_0(ctx: Ctx) -> dict[str, Any]:
    work = jobsched_work(ctx)
    new = j_check(ctx, work, REPO, "ABCDX")
    old = j_check(ctx, work, ctx.scratch / "base", "ABCDX")
    if new[1] is None or old[1] is None:
        return result(
            "J-0",
            "the owner's check.sh command on job-scheduler",
            P["J-0"][2],
            {"new_rc": new[0], "old_rc": old[0], "stderr": new[2] or old[2]},
            False,
        )
    a, b = normalise_check(old[1]), normalise_check(new[1])
    b_declared = normalise_check(with_base_gated_note(ctx, new[1]))
    got = {
        "rc": [old[0], new[0]],
        "verdict": [old[1].get("verdict"), new[1].get("verdict")],
        "findings": [len(old[1].get("findings", [])), len(new[1].get("findings", []))],
        "cases": len(new[1].get("cases", [])),
        "normalised_sha256": [digest(a), digest(b)],
        "normalised_sha256_with_978b468_gated_note": digest(b_declared),
    }
    ok = (
        new[0] == 0
        and new[1].get("verdict") == "pass"
        and not new[1].get("findings")
        and digest(a) == digest(b_declared)
    )
    return result(
        "J-0",
        "the owner's check.sh command on job-scheduler (ABCDX, SQLite), 978b468 library vs this tree",
        P["J-0"][2],
        got,
        ok,
        reading="identical after putting 978b468's layer-A gating note back: the one difference is gated_note(), GO805 leaving it, which prereg section 8 declares (K8); normalised_sha256 are the raw digests",
    )


def j_a(ctx: Ctx) -> dict[str, Any]:
    work = jobsched_work(ctx)
    res = layer_a(ctx, work, J_SPEC, "from_spec")
    found = res["from_spec"]["findings"]
    pol = res["from_spec"]["policy"]

    class J:
        def to_json(self) -> dict[str, Any]:
            return pol

    dump = route_dump(ctx, work, J())
    return result("J-A", "layer A on the job-scheduler original (its spec declares no owner column)", P["J-A"][2],
                  {"findings": [f["key"] for f in found], "routes": len(dump), "judged": sum(r["judged"] for r in dump)}, not found)  # fmt: skip


def j_sm(ctx: Ctx, pid: str, mid: str) -> dict[str, Any]:
    work = jobsched_work(ctx)
    dest = ctx.scratch / "mut" / mid.lower() / "jobsched"
    shutil.rmtree(dest.parent, ignore_errors=True)
    shutil.copytree(work, dest)
    rel, old, new = J_EDITS[mid]
    f = dest / rel
    text = f.read_text()
    if text.count(old) != 1:
        raise ValueError(f"{mid}: {rel}: the edit matches {text.count(old)} times")
    f.write_text(text.replace(old, new))
    ok, err, _ = P1.go_build(dest / "cmd/server", dest / "bin" / "server")
    if not ok:
        raise RuntimeError(f"{mid} does not build: {err[-300:]}")
    a = layer_a(ctx, dest, J_SPEC, "from_spec")["from_spec"]["findings"]
    rc, doc, serr = j_check(ctx, dest, REPO, "ABD")
    findings = (doc or {}).get("findings", [])
    by_layer: dict[str, int] = {}
    for x in findings:
        by_layer[x.get("layer", "?")] = by_layer.get(x.get("layer", "?"), 0) + 1
    reported = {
        "A": len(a),
        "findings_by_layer": by_layer,
        "rc": rc,
        "first": [f"{x.get('layer')}|{x.get('endpoint')}|{x.get('case')}" for x in findings[:6]],
    }
    return result(pid, f"job-scheduler copy ({mid}): layer A and layers B, D", P[pid][2], reported, True,
                  detected=bool(a) or bool(findings), stderr=serr if doc is None else "",
                  reading="reported only (Amendment 1 Q10): the row is informational, pass=True means it ran")  # fmt: skip


ARM_J: dict[str, Callable[[Ctx], dict[str, Any]]] = {
    "J-0": j_0, "J-A": j_a, "J-SM1": lambda c: j_sm(c, "J-SM1", "JSM1"), "J-SM2": lambda c: j_sm(c, "J-SM2", "JSM2"),
}  # fmt: skip


# ---- arm 2: K8 (GO805, the not-owned answer) and K10 (the J0' matrix), prereg 5.6 -------------------------------

J0PRIME = REPO / "docs/results/2026-10-18-parity-matrix-j0prime.md"
K8_WANT = {
    "S2 goginapp": (404, 3),
    "S3 gotickets": (404, 6),
}  # prereg 5.6 P2-805A: (status, linked raises)
P2_FILE_REF = re.compile(r"`([A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+\.[a-z]+)(?::(\d+))?`")
P2_LINE_REF = re.compile(r"`[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+\.[a-z]+:\d+`")


def k8_modules(ctx: Ctx) -> dict[str, Path]:
    assert ctx.streak_src is not None and ctx.jobsched_src is not None
    return {"S1 goapp": SPEC_DIR / "goapp", "S2 goginapp": SPEC_DIR / "goginapp", "S3 gotickets": SPEC_DIR / "gotickets",
            "goparity3": GP3, "Streak": ctx.streak_src, "job-scheduler": ctx.jobsched_src}  # fmt: skip


def p2_805a(ctx: Ctx) -> dict[str, Any]:
    """The K8 derivation on S1, S2, S3, goparity3, Streak and job-scheduler: the answer and the linked refusals it rests on."""
    from lattice.gocheck.derive import derive_go_policy, policy_extract
    from lattice.gocheck.notowned import derive_not_owned, linked_uses

    rows: dict[str, Any] = {}
    for name, d in k8_modules(ctx).items():
        owners = dict(derive_go_policy(d).owners)
        ex = policy_extract(d)
        got, every = derive_not_owned(ex, owners), derive_not_owned(ex, None)
        linked = sorted(
            (f"{rt['method']} {rt['path']}", u["raise"]["status"], u["raise"]["line"])
            for rt, u in linked_uses(ex, owners)
        )
        rows[name] = {"owners": owners, "status": got.status, "linked": got.linked, "note": got.note, "every_column_status": every.status,
                      "every_column_linked": every.linked, "linked_refusals": [list(x) for x in linked]}  # fmt: skip
    status_ok = {n: rows[n]["status"] == s for n, (s, _) in K8_WANT.items()}
    count_ok = {n: rows[n]["linked"] == c for n, (_, c) in K8_WANT.items()}
    return result("P2-805A", "K8 derivation (GO805's not-owned answer) on the originals", P["P2-805A"][2],
                  {n: {k: v for k, v in r.items() if k in ("owners", "status", "linked", "note")} for n, r in rows.items()},
                  all(status_ok.values()) and all(count_ok.values()), rows=rows, status_exact=status_ok, linked_count_exact=count_ok,
                  reading="exact = the status and the linked-refusal count of S2 and S3 as registered; Streak and the others are reported; goparity3 has no owned table, so no K8 decoy lives there")  # fmt: skip


def p2_805b(ctx: Ctx) -> dict[str, Any]:
    """An S3 copy whose GET /tickets/:tid answers another user's ticket with 403 instead of 404: one GO805, no other code."""
    import dataclasses

    from lattice.gocheck import run_gocheck
    from lattice.gocheck.derive import derive_go_policy
    from lattice.gocheck.notowned import with_not_owned

    orig = SPEC_DIR / "gotickets"
    copy = ctx.scratch / "p2" / "gotickets"
    shutil.rmtree(copy.parent, ignore_errors=True)
    shutil.copytree(orig, copy)
    api = copy / "api/api.go"
    text = api.read_text()
    old = 'First(&t).Error; err != nil {\n\t\t\tdetail(c, http.StatusNotFound, "Not found")\n\t\t\treturn\n\t\t}\n\t\tc.JSON(http.StatusOK, t)'
    if text.count(old) != 1:
        raise ValueError(f"api/api.go: the GET /tickets/:tid edit matches {text.count(old)} times")
    api.write_text(text.replace(old, old.replace("StatusNotFound", "StatusForbidden")))
    pol = with_not_owned(dataclasses.replace(derive_go_policy(orig), baseline=frozenset()), orig)
    found = run_gocheck(copy, pol, strict=True)
    keys = [f.key for f in found if f.code == "GO805"]
    return result("P2-805B", "S3 copy: GET /tickets/:tid answers another user's ticket with 403", P["P2-805B"][2],
                  {"answer": pol.not_owned, "go805": keys, "findings": [f"{f.key} ({f.file}:{f.line})" for f in found],
                   "message": [f.message for f in found if f.code == "GO805"], "other_codes": sorted({f.code for f in found if f.code != "GO805"})},
                  keys == ["GO805|GET /tickets/:tid|not_owned"])  # fmt: skip


def p2_805c(ctx: Ctx) -> dict[str, Any]:
    """No GO805 on the five originals under the answer derived from each (baseline emptied)."""
    import dataclasses

    from lattice.gocheck import run_gocheck
    from lattice.gocheck.derive import derive_go_policy
    from lattice.gocheck.notowned import with_not_owned

    rows: dict[str, Any] = {}
    for name, d in k8_modules(ctx).items():
        if name == "goparity3":
            continue  # prereg 5.6 names S1, S2, S3, Streak and job-scheduler
        pol = with_not_owned(dataclasses.replace(derive_go_policy(d), baseline=frozenset()), d)
        found = run_gocheck(d, pol, strict=True)
        rows[name] = {"answer": pol.not_owned, "go805": [f.key for f in found if f.code == "GO805"], "other": sorted(f.key for f in found if f.code != "GO805")[:5]}  # fmt: skip
    return result("P2-805C", "GO805 on the originals S1, S2, S3, Streak, job-scheduler", P["P2-805C"][2],
                  {n: r["go805"] for n, r in rows.items()}, all(not r["go805"] for r in rows.values()), rows=rows)  # fmt: skip


def p2_805d(ctx: Ctx) -> dict[str, Any]:
    """GO805 is carried over (in GO_CODES, not in GO_GATED, not in gated_note); GO804 and GO806 keep their 978b468 text."""
    from lattice.gocheck import GO_CODES, GO_GATED, gated_note

    bg, bn = base_gating(ctx)
    now = gated_note()
    got = {"base_gated": sorted(bg), "now_gated": sorted(GO_GATED), "go805_in_codes": "GO805" in GO_CODES,
           "GO804_text_identical": bg["GO804"] == GO_GATED["GO804"], "GO806_text_identical": bg["GO806"] == GO_GATED["GO806"],
           "go805_in_now_note": "GO805" in now, "base_note": bn, "now_note": now}  # fmt: skip
    ok = (sorted(GO_GATED) == ["GO804", "GO806"] and sorted(bg) == ["GO804", "GO805", "GO806"] and "GO805" in GO_CODES and got["GO804_text_identical"]
          and got["GO806_text_identical"] and not got["go805_in_now_note"])  # fmt: skip
    return result("P2-805D", "gated_note() and GO_GATED against 978b468", P["P2-805D"][2], got, ok)


CHECK_PY_AFTER_Q10 = "7b81dc9c70be82e46c8cecc2ee1b4290f8649521ce4a65389c5b8c9b6d3e5a77"  # re-pinned 2026-10-23 for the declared Q10 edit to check.py (dotnet prereg Q10: layer A on a .NET runtime)


CHECK_PY_AFTER_F5 = "cb1c387e00bf34f7fa99fdd852bb482ca2361e64e8e53cf50e1576de76effc0d"  # re-pinned 2026-10-23 for the declared F5 edit to check.py (layer R passes `relogin` and `crash_admits` for a .NET runtime only; Python and Go call paths unchanged)


def p2_def(ctx: Ctx) -> dict[str, Any]:
    """K9 was not built (Amendment 1 Q6): lattice/spec/check.py is the frozen file, so the default is the unchanged `from_spec`.
    The only accepted changes are the declared Q10 edit (`CHECK_PY_AFTER_Q10`) and the declared F5 edit (`CHECK_PY_AFTER_F5`, 3 lines for a .NET runtime); the base (978b468 in a scored run, a copy of this tree in the probe's test) must hold one of those or the prereg hash."""
    frozen = dict(frozen_table())["lattice/spec/check.py"]
    want = CHECK_PY_AFTER_F5
    mine, theirs = (
        sha(REPO / "lattice/spec/check.py"),
        sha(ctx.scratch / "base" / "lattice/spec/check.py"),
    )
    return result("P2-DEF", "K9: the default policy of `lattice check` (not built: Q6)", P["P2-DEF"][2],
                  {"K9": "not built (Amendment 1 Q6)", "check_py_sha256": mine, "base_sha256": theirs, "prereg_sha256": frozen, "declared_sha256": want, "unchanged": mine == want and theirs in (frozen, CHECK_PY_AFTER_Q10, want)},
                  mine == want and theirs in (frozen, CHECK_PY_AFTER_Q10, want), reading="reported, not gated (Q6): with check.py unchanged the default is GoPolicy.from_spec, identical to 978b468 by construction")  # fmt: skip


def j0prime_check(doc: str, root: Path) -> dict[str, Any]:
    """The check of the J0' document's section 6 (every cited file exists and the line is inside it; every Python and Go cell
    cites a file:line and a test or results row), plus its grade counts read from the matrix."""
    bad: list[str] = []
    cited = set(P2_FILE_REF.findall(doc))
    for path, line in sorted(cited):
        p = root / path
        if not p.is_file():
            bad.append(f"missing file {path}")
        elif line and not 1 <= int(line) <= len(p.read_text().splitlines()):
            bad.append(f"{path}:{line} outside the file")
    rows = [
        ln
        for ln in doc.splitlines()
        if re.match(r"\| \d+ \| ", ln) and len(ln.strip("|").split("|")) == 5
    ]
    counts: dict[str, dict[str, int]] = {"Python": {}, "Go": {}}
    for ln in rows:
        cells = [c.strip() for c in ln.strip("|").split("|")]
        for name, cell in (("Python", cells[2]), ("Go", cells[3])):
            ev = re.search(r"(T|R): ", cell) and re.search(r"`(tests|docs/results)/", cell)
            if not (P2_LINE_REF.search(cell) and ev):
                bad.append(f"row {cells[0]} {name}: needs a file:line and a test or results row")
            grade = re.match(r"\*\*([SPA])\*\*", cell)
            g = grade.group(1) if grade else "?"
            counts[name][g] = counts[name].get(g, 0) + 1
    return {"rows": len(rows), "citations": len(cited), "problems": bad, "grades": counts}


def p2_j0(ctx: Ctx) -> dict[str, Any]:
    got = j0prime_check(J0PRIME.read_text(), REPO)
    return result("P2-J0", "J0': every Python and Go cell cites an existing file:line and a test or results row", P["P2-J0"][2],
                  {"rows": got["rows"], "citations": got["citations"], "problems": len(got["problems"]), "grades": got["grades"]},
                  got["rows"] == 20 and not got["problems"], problem_list=got["problems"],
                  reading="the script of the document's section 6, run on this tree; the independent re-run on the merged commit is V's")  # fmt: skip


ARM_2: dict[str, Callable[[Ctx], dict[str, Any]]] = {
    "P2-805A": p2_805a, "P2-805B": p2_805b, "P2-805C": p2_805c, "P2-805D": p2_805d, "P2-DEF": p2_def, "P2-J0": p2_j0,
}  # fmt: skip


# ---- arm P and X: cross-arm byte compatibility (5.9) ----------------------------------------------------------

PREREG = REPO / "docs/results/2026-10-18-go-parity-round3-prereg.md"
PBG_ALLOWED = (
    STANDIN / "pbg_allowed.json"
)  # layer-A differences Amendment 2 lists (K1, K2), by module


def group_hash(root: Path, names: Sequence[str]) -> str:
    return hashlib.sha256(b"".join((root / n).read_bytes() for n in names)).hexdigest()


def frozen_table() -> list[tuple[str, str]]:
    """``(path, sha256)`` of the frozen files of prereg section 8 (the table between its two headings)."""
    text = PREREG.read_text()
    sec = text.split("**Frozen (empty diff vs 978b468")[1].split(
        "Also frozen, hashed by the runner"
    )[0]
    out = []
    for f, h in re.findall(r"^\| (\S.*?) \| ([0-9a-f]{64}) \|", sec, re.M):
        out.append((f.replace("\\", "").split(" (")[0].strip(), h))
    return out


def frozen_hash(root: Path, rel: str) -> str:
    """The sha256 of a row of the table: a file; the `cat` of the 19 `lattice/gocheck/src` files the row names;
    or the `cat` of a directory's files in `LC_ALL=C` sorted order (`dir/**`)."""
    if rel.startswith("lattice/gocheck/src/*.go"):
        src = root / "lattice/gocheck/src"
        skip = {"routes.go", "reach.go", "owner.go", "goextract_owner.go", "goextract_raise.go"}
        return group_hash(
            src, sorted(p.name for p in src.iterdir() if p.suffix == ".go" and p.name not in skip)
        )
    if rel.endswith("/**"):
        d = root / rel[:-3]
        names = sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file())
        return group_hash(d, sorted(names, key=lambda s: s.encode()))
    return sha(root / rel)


# The existing test files whose edit the prereg declares (section 8: ``gated_note()`` and the policy extract's K8 key; Amendment 2
# ruling 2: the inferences list gains ``body_model``): path -> sha256 of the edited file. PB-F lets exactly these bytes differ
# from 978b468; any other byte in them, or any other test file, still fails it.
DECLARED_TEST_EDITS = {
    "tests/gocheck/test_go_derive.py": "bc66129984d1a125ad334d9f4bfa6878043663297eefa9de800fc16a7e634bbb",
    "tests/gocheck/test_gocheck_rules.py": "ffb2dc883c6f25a342e3de0bce49d7930b52abafc057eac6f1db8fa58cc3ec9a",
    "tests/importer/test_go_rules2.py": "7833667c0fddbf568086bba319f5659d65b8736dcece2f9ef4538c074ad7714c",
}


def undeclared_test_changes(
    changed: Sequence[str], root: Path = REPO
) -> tuple[list[str], list[str]]:
    """``(undeclared, declared)``: the changed test files not covered by :data:`DECLARED_TEST_EDITS` (a declared file whose bytes are
    not the declared ones is undeclared) and the declared edits that are in place."""
    undeclared, declared = [], []
    for t in changed:
        if DECLARED_TEST_EDITS.get(t) == (sha(root / t) if (root / t).is_file() else None):
            declared.append(t)
        else:
            undeclared.append(t)
    return undeclared, declared


def pb_f(ctx: Ctx) -> dict[str, Any]:
    base = ctx.scratch / "base"
    rows, stale, touched = [], [], []
    for rel, listed in frozen_table():
        mine, theirs = frozen_hash(REPO, rel), frozen_hash(base, rel)
        rows.append({"file": rel, "prereg": listed[:12], "base": theirs[:12], "now": mine[:12]})
        if theirs != listed:
            stale.append(rel)
        if mine != theirs:
            touched.append(rel)
    fixtures = P2.changed_vs_base(base, ("tests/gocheck/fixtures",))
    docs = P2.changed_vs_base(base, ("docs/results/data/2026-10-18-go-parity-gaps-round2",))
    tests, declared = undeclared_test_changes(
        [
            t
            for t in P2.changed_vs_base(base, ("tests", "lattice/studio", "lattice/mcp"))
            if t not in fixtures and "/golden" not in t
        ]
    )
    return result("PB-F", "section 8's frozen table at 978b468; existing fixtures, tests and round-2 freeze data unchanged", P["PB-F"][2],
                  {"files": len(rows), "prereg_value_differs_from_978b468": stale, "changed_since_978b468": touched,
                   "existing_fixture_files_changed": fixtures, "round2_data_changed": docs, "existing_test_files_changed": tests,
                   "declared_test_edits_in_place": declared},
                  not stale and not touched and not fixtures and not docs and not tests, rows=rows,
                  reading="a row whose prereg value differs from the 978b468 archive is listed in prereg_value_differs_from_978b468 and fails; files added by this round are not listed (changed_vs_base lists differing existing files only); the three declared test edits (DECLARED_TEST_EDITS, by sha256) are listed in declared_test_edits_in_place and do not fail the row")  # fmt: skip


def pb_x(ctx: Ctx) -> dict[str, Any]:
    from lattice.gocheck import build_gocheck

    base_bin = ctx.scratch / "base_gocheck" / "gocheck"
    ok, err, _ = P1.go_build(
        ctx.scratch / "base" / "lattice/gocheck/src", base_bin, flags="-mod=mod"
    )
    if not ok:
        raise RuntimeError(f"the 978b468 checker does not build: {err}")
    new_bin = build_gocheck()
    dirs = [*P1.PB_X_DIRS, "tests/gocheck/fixtures/runs", "tests/gocheck/fixtures/goparity2"]
    rows = []
    for rel in dirs:
        a, b = P1.extract_digest(base_bin, rel), P1.extract_digest(new_bin, rel)
        rows.append({"dir": rel, "base": a[:16], "now": b[:16], "same_as_base": a == b})
    same = sum(r["same_as_base"] for r in rows)
    return result("PB-X", "default gocheck extract byte-identical to 978b468", P["PB-X"][2], {"identical_to_base": same, "of": len(rows)}, same == len(rows),
                  rows=rows, reading="the registered 'the 7 round-1 dirs, goapp and fixtures/runs' counts goapp twice (it is the 7th round-1 dir): the 9 are the 7, fixtures/runs and fixtures/goparity2")  # fmt: skip


def finding_rows(binary: Path, root: Path, policy: Any) -> list[list[Any]]:
    proc = subprocess.run(
        [str(binary)],
        input=json.dumps({"root": str(root.resolve()), "policy": policy.to_json()}),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return [["ERR", proc.stderr[-120:]]]
    return sorted(
        [f["key"], f["file"], f["line"], f["message"]] for f in json.loads(proc.stdout)["findings"]
    )


def pb_g(ctx: Ctx) -> dict[str, Any]:
    """Layer A in the 978b468 tree against this one, per module and policy; workflows and inventory on a pristine copy of the
    978b468 fixtures. A layer-A difference is allowed only when pbg_allowed.json (Amendment 2's list) names its key."""
    import dataclasses

    from lattice.gocheck import GoPolicy, build_gocheck
    from lattice.gocheck.derive import derive_go_policy

    allowed = json.loads(PBG_ALLOWED.read_text()) if PBG_ALLOWED.is_file() else {}
    base_bin = ctx.scratch / "base_gocheck" / "gocheck"
    if not base_bin.is_file():
        ok, err, _ = P1.go_build(
            ctx.scratch / "base" / "lattice/gocheck/src", base_bin, flags="-mod=mod"
        )
        if not ok:
            raise RuntimeError(f"the 978b468 checker does not build: {err}")
    new_bin = build_gocheck()
    mods: dict[str, Path] = {}
    for p in sorted(FIX.iterdir()):
        for g in sorted(p.rglob("go.mod")) if p.is_dir() else []:
            mods[
                f"fx/{p.name}" + ("" if g.parent == p else "/" + g.parent.relative_to(p).as_posix())
            ] = g.parent
    for n in ("goapp", "goginapp", "gotickets"):
        mods[f"spec/{n}"] = SPEC_DIR / n
    mods["implementation/app"] = REPO / "tests/spec/fixtures/implementation/app"
    mods["studio/mock/appmap"] = REPO / "lattice/studio/mock/appmap/app"
    diffs: dict[str, Any] = {}
    unlisted: list[str] = []
    for name, d in mods.items():
        pols: dict[str, Any] = {"empty": GoPolicy()}
        try:
            pols["derived"] = dataclasses.replace(derive_go_policy(d), baseline=frozenset())
        except Exception:  # noqa: BLE001 -- a module the derivation cannot read has only the empty policy
            pass
        for pn, pol in pols.items():
            a, b = finding_rows(base_bin, d, pol), finding_rows(new_bin, d, pol)
            if a != b:
                only_new = [r[0] for r in b if r not in a]
                only_base = [r[0] for r in a if r not in b]
                diffs[f"{name} [{pn}]"] = {"only_base": only_base, "only_now": only_new}
                unlisted += [
                    f"{name} [{pn}] {k}"
                    for k in only_new + only_base
                    if k not in allowed.get(name, {}).get(pn, [])
                ]
    pristine = ctx.scratch / "pbg_fixtures"
    shutil.rmtree(pristine, ignore_errors=True)
    shutil.copytree(ctx.scratch / "base/tests/gocheck/fixtures", pristine)
    other: dict[str, Any] = {}
    for name, args_ in (("workflows --static", ["workflows", str(pristine), "--package", "goextract_workflow", "--static"]),
                        ("inventory", ["inventory", str(pristine), "--package", "goextract_workflow"])):  # fmt: skip
        outs = []
        for tree in (ctx.scratch / "base", REPO):
            d = ctx.scratch / "pbg3" / name.split()[0] / ("base" if tree != REPO else "now")
            shutil.rmtree(d, ignore_errors=True)
            rc, so, se, _ = P1.run(
                [ctx.py, "-m", "lattice.importer", *args_, "--out", str(d)],
                cwd=tree,
                env=ctx.env(PYTHONPATH=str(tree)),
                timeout=600,
            )
            outs.append((rc, P1.tree_files(d) if d.exists() else {}, so.replace(str(d), "<out>")))
        other[name] = {
            "same": outs[0] == outs[1] and outs[0][0] == 0,
            "rc": [outs[0][0], outs[1][0]],
        }
    ok = not unlisted and all(v["same"] for v in other.values())
    return result("PB-G", "Go layer A, workflows and inventory against 978b468", P["PB-G"][2],
                  {"layer_a_differences": diffs, "differences_not_in_pbg_allowed_json": unlisted, "workflows_and_inventory": other,
                   "modules": len(mods)}, ok,
                  reading="a layer-A difference passes only if dogfood/g3_standin/pbg_allowed.json (the routes K1/K2 turn complete, listed in Amendment 2) names its finding key; test_process_runtime.py's reports are PB-T's")  # fmt: skip


def pb_h(ctx: Ctx) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for name, cmd in {
        "lattice --help": [
            ctx.py,
            "-c",
            "import sys;from lattice.cli import main;sys.argv=['lattice','--help'];sys.exit(main())",
        ],
        "python -m lattice.importer --help": [ctx.py, "-m", "lattice.importer", "--help"],
        "python -m lattice.spec --help": [ctx.py, "-m", "lattice.spec", "--help"],
        "python -m lattice.spec check --help": [ctx.py, "-m", "lattice.spec", "check", "--help"],
    }.items():
        a, b = P1.both_trees(ctx, cmd)
        rows[name] = {"same": a == b, "rc": [a[0], b[0]]}
    proj = ctx.scratch / "mcp_empty"
    proj.mkdir(exist_ok=True)
    msgs = "\n".join(json.dumps(m) for m in [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},  # noqa: E501
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]) + "\n"  # fmt: skip
    outs = []
    for tree in (ctx.scratch / "base", REPO):
        rc, so, _, _ = P1.run(
            [ctx.py, "-m", "lattice.mcp", "serve", "--project", str(proj)],
            cwd=proj,
            env=ctx.env(PYTHONPATH=str(tree)),
            stdin=msgs.encode(),
            timeout=120,
        )
        outs.append((rc, so))
    rows["MCP tools/list"] = {"same": outs[0] == outs[1] and "tools" in outs[0][1]}
    return result(
        "PB-H",
        "help texts and MCP tools/list identical to 978b468",
        P["PB-H"][2],
        rows,
        all(v["same"] for v in rows.values()),
    )


def pb_n(ctx: Ctx) -> dict[str, Any]:
    """Stack-neutral outputs: the pytest outcomes of tests/contracts, tests/improve and
    tests/studio/test_rebuild.py in both trees, and the contracts exported from the same imported spec by both."""
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
    for tree in (ctx.scratch / "base", REPO):
        rc, so, _, _ = P1.run(cmd, cwd=tree, env=ctx.env(PYTHONPATH=str(tree)), timeout=3600)
        outs.append(P2.pytest_ids(so))
    same_tests = outs[0] == outs[1]
    code = ("import json,sys,hashlib;sys.path.insert(0,sys.argv[1]);import importlib.util as u;"
            "from lattice.contracts.export import export_spec;"
            "s=u.spec_from_file_location('cs',sys.argv[2]);m=u.module_from_spec(s);s.loader.exec_module(m);"
            "print(hashlib.sha256(json.dumps(export_spec(m.SPEC),sort_keys=True,default=str).encode()).hexdigest())")  # fmt: skip
    exports: dict[str, Any] = {}
    for name, d in (("goginapp", ctx.cache.get("s2_imp")), ("gotickets", ctx.cache.get("s3_imp"))):
        if d is None:
            continue
        a, b = P1.both_trees(ctx, [ctx.py, "-c", code, "", str(Path(d) / "candidate_spec.py")])
        exports[name] = {
            "same": a == b and a[0] == 0,
            "rc": [a[0], b[0]],
            "sha256": b[1].strip()[:16],
            "err": (a[2] or b[2])[-200:],
        }
    if not exports:
        d = ctx.scratch / "imp" / "pbn"
        t = S3R.import_cli(ctx, "tests/spec", d)
        if t["rc"] == 0:
            a, b = P1.both_trees(ctx, [ctx.py, "-c", code, "", str(d / "candidate_spec.py")])
            exports["gotickets"] = {
                "same": a == b and a[0] == 0,
                "rc": [a[0], b[0]],
                "sha256": b[1].strip()[:16],
                "err": (a[2] or b[2])[-200:],
            }
    ok = same_tests and bool(exports) and all(v["same"] for v in exports.values())
    return result("PB-N", "stack-neutral outputs identical to 978b468", P["PB-N"][2],
                  {"pytest_base": outs[0]["summary"], "pytest_now": outs[1]["summary"], "same_pytest_ids": same_tests, "exports": exports}, ok,
                  reading="the contracts exported for goginapp's and gotickets' imported specs are compared by exporting the same candidate_spec.py with both trees (K3 changes what the import itself writes: declared in prereg section 8)")  # fmt: skip


PB_T_TARGETS = (
    "tests/importer",
    "tests/pycheck",
    "tests/gocheck",
    "tests/spec",
    "tests/mcp",
    "tests/studio",
)


def pb_t(
    ctx: Ctx, targets: Sequence[str] = PB_T_TARGETS, base_log: Path | None = None
) -> dict[str, Any]:
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
    rc, out, _, _ = P1.run(cmd, env=ctx.env(), timeout=7200)
    wall = time.perf_counter() - t0
    (ctx.out / "pytest_now.txt").write_text(out)
    if base_log is not None and base_log.exists():
        base_out = base_log.read_text()
    else:
        _, base_out, _, _ = P1.run(
            cmd,
            cwd=ctx.scratch / "base",
            env=ctx.env(PYTHONPATH=str(ctx.scratch / "base")),
            timeout=7200,
        )
    (ctx.out / "pytest_base.txt").write_text(base_out)
    a, b = P2.pytest_ids(base_out), P2.pytest_ids(out)
    skips = lambda x: sorted(re.sub(r"\s+", " ", s) for s in x["SKIPPED"])  # noqa: E731

    def counts(x: dict[str, Any]) -> dict[str, int]:
        return {
            k.rstrip("s"): int(n)
            for n, k in (w.split() for w in x["summary"])
            if k.rstrip("s") in ("failed", "skipped", "error")
        }

    same = (
        a["FAILED"] == b["FAILED"]
        and a["ERROR"] == b["ERROR"]
        and skips(a) == skips(b)
        and counts(a) == counts(b)
    )
    return result("PB-T", "pytest " + " ".join(targets), P["PB-T"][2],
                  {"base": a["summary"], "now": b["summary"], "wall_s": round(wall, 1), "failed_only_now": sorted(set(b["FAILED"]) - set(a["FAILED"])),
                   "failed_only_base": sorted(set(a["FAILED"]) - set(b["FAILED"])), "errors_only_now": sorted(set(b["ERROR"]) - set(a["ERROR"])),
                   "errors_only_base": sorted(set(a["ERROR"]) - set(b["ERROR"])), "counts_base": counts(a), "counts_now": counts(b)},
                  same, reading="the failing ids are compared exactly; a new test that passes adds to the passed count only")  # fmt: skip


def pb_w(ctx: Ctx) -> dict[str, Any]:
    changed = P1.snapshot_diff(ctx.snap0, P1.snapshots())
    extra = [
        f"{k}/{rel}"
        for k, s0 in ctx.extra_snaps.items()
        for rel in diff_snaps(s0, P1.snapshot(Path(k)))
    ]
    lines = [ln for ln in ctx.shim_log.read_text().splitlines() if ln.strip()]
    calls = lines[: ctx.shim_mark] if ctx.shim_mark is not None else lines
    suites = lines[ctx.shim_mark :] if ctx.shim_mark is not None else []
    return result("PW", "snapshots (sha256, mode, mtime, listing with hidden files) of the fixtures and copies; docker/claude shims", P["PW"][2],
                  {"changed": changed + extra, "shim_calls": calls, "shim_calls_of_the_test_suites_reported": len(suites)},
                  not changed and not extra and not calls,
                  reading="the shim calls counted are those made before the first row that runs a test suite (PB-N, PB-T, PB-D): the library's own container sweep (`docker ps` for its lattice.owner label) runs in those, as in round 2, where PW ran before them", suite_calls=sorted(set(suites))[:6])  # fmt: skip


def diff_snaps(a: Mapping[str, Any], b: Mapping[str, Any]) -> list[str]:
    return [rel for rel in sorted(set(a) | set(b)) if a.get(rel) != b.get(rel)]


ARM_P: dict[str, Callable[[Ctx], dict[str, Any]]] = {
    "PB-F": pb_f, "PB-G": pb_g, "PB-P": lambda c: P1.probe_pb_p(c), "PB-P2": lambda c: P2.probe_pb_p2(c),
    "PB-N": pb_n, "PB-H": pb_h, "PB-T": lambda c: pb_t(c, c.pb_t_targets or PB_T_TARGETS, c.pytest_base_log), "PB-D": lambda c: P2.probe_pb_d(c),
    "PW": pb_w,
}  # fmt: skip
ARM_X: dict[str, Callable[[Ctx], dict[str, Any]]] = {"PB-X": pb_x}


# ---- arm H: the held-out S4 (blind; scored once) --------------------------------------------------------------

AUTH_FROM_IMPORT = {
    "Public": "Public",
    "Customer": "Member",
    "Admin": "Admin",
}  # the importer's role names -> truth's (the `Customer` role is not renamed: prereg 1.3)


def norm_path(p: str) -> str:
    """A route path in the importer's ``{x}`` form, without a trailing slash."""
    q = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", r"{\1}", p)
    return q if q == "/" else q.rstrip("/")


def gin_path(p: str) -> str:
    return re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", r":\1", norm_path(p))


def make_target(root: Path, package: str, truth_path: Path, label: str) -> dict[str, Any]:
    try:
        snap = (root / package).resolve().relative_to(REPO).as_posix()
    except ValueError:
        snap = None
    return {"root": root, "package": package, "module_dir": root / package, "truth": json.loads(truth_path.read_text()), "label": label,
            "snapshot_rel": snap, "root_arg": str(root)}  # fmt: skip


def machinery3(ctx: Ctx, root: Path, package: str, key: str) -> dict[str, Any]:
    """What ``rules.import_rules`` builds around the probes (S3R.machinery with the package as a parameter: the
    import root is put on sys.path first, as the CLI does: K7)."""
    if key in ctx.cache:
        return ctx.cache[key]  # type: ignore[no-any-return]
    from lattice.importer import go_rules, rules
    from lattice.importer.frontends import select
    from lattice.importer.lift import lift_repo

    S3R.add_import_root(root)
    S3R.add_import_root(REPO)
    fe = go_rules.InferringFrontEnd(select("gin", root, package))
    found = go_rules.harness_factory(root, package)
    if found is None:
        raise RuntimeError(f"{root}/{package}/studio.toml names no [app] app")
    factory, _ = found
    model = fe.extract(root, package)
    runtime = rules._runtime(fe, root, package, model)
    res = lift_repo(root, package, factory, only=(), frontend=fe, runtime=runtime)
    go_rules.patch_profile(res, fe.private, fe.validation_status)
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
    m = {"result": res, "seeder": seeder, "sessions": rules._sessions(runtime, factory), "unreachable": unreachable, "rules": rules, "unclear": unclear,
         "outside": rules.outside_api(res.model), "uploads": rules.upload_routes(res.model)}  # fmt: skip
    ctx.cache[key] = m
    return m


def r_on_spec3(ctx: Ctx, tgt: dict[str, Any], spec: Any, variant: str) -> Any:
    from dataclasses import replace

    from lattice.spec.probes import run_probes

    m = machinery3(ctx, tgt["root"], tgt["package"], f"mach-{tgt['label']}")
    seeder, res, rules = m["seeder"], m["result"], m["rules"]
    who = rules.callers(res, seeder, variant)
    kw = dict(login=who.login, mint_token=who.mint, scheme=who.scheme, refusals=who.refusals, login_route=who.login_route, auth_headers=who.headers,
              inactive_login=who.inactive_login, unreachable=m["unreachable"], variant=variant, auth_present=who.present)  # fmt: skip
    rr = run_probes(
        replace(spec, users=seeder.credentials(variant)), m["sessions"], seeder.make_db, **kw
    )
    rules.mark_unclear(spec, rr, m["unclear"])
    rules.mark_outside(spec, rr, m["outside"])
    rules.mark_uploads(rr, m["uploads"])
    return rr


def h_import(ctx: Ctx, tgt: dict[str, Any], d: Path) -> dict[str, Any]:
    shutil.rmtree(d, ignore_errors=True)
    rc, out, err, wall = P1.run(
        [
            ctx.py,
            "-m",
            "lattice.importer",
            tgt["root_arg"],
            "--package",
            tgt["package"],
            "--rules-only",
            "--out",
            str(d),
        ],
        env=ctx.env(),
        timeout=1800,
    )
    return {"rc": rc, "stdout": out[-400:], "stderr": err[-1500:], "wall_s": wall}


def h_dir(ctx: Ctx, tgt: dict[str, Any]) -> Path:
    key = f"h-imp-{tgt['label']}"
    if key not in ctx.cache:
        d = ctx.scratch / "imp" / f"h-{tgt['label']}"
        t = h_import(ctx, tgt, d)
        if t["rc"] != 0:
            raise RuntimeError(f"the import of {tgt['label']} failed: {t['stderr'][-500:]}")
        ctx.cache[key] = d
        ctx.timings[f"import_{tgt['label']}_s"] = t["wall_s"]
    return ctx.cache[key]  # type: ignore[no-any-return]


def h_seed_map(ctx: Ctx, tgt: dict[str, Any]) -> dict[str, str]:
    key = f"h-seed-{tgt['label']}"
    if key not in ctx.cache:
        cand = P1.load_candidate(h_dir(ctx, tgt))
        ctx.cache[key] = P1.verdict_map(r_on_spec3(ctx, tgt, cand.SPEC, "seed"))
    return ctx.cache[key]  # type: ignore[no-any-return]


def h_pi1(ctx: Ctx, tgt: dict[str, Any]) -> dict[str, Any]:
    cand = P1.load_candidate(h_dir(ctx, tgt))
    got = {
        (c.method.upper(), norm_path(c.path)): AUTH_FROM_IMPORT.get(c.auth, c.auth)
        for c in cand.CONTRACTS
    }
    want = {(r["method"].upper(), norm_path(r["path"])): r["auth"] for r in tgt["truth"]["routes"]}
    found = [k for k in want if k in got]
    exact = [k for k in want if got.get(k) == want[k]]
    extra = sorted(f"{m} {p}" for (m, p) in got if (m, p) not in want)
    recall = len(found) / len(want) if want else 0.0
    auth = len(exact) / len(want) if want else 0.0
    return result("H-PI1", "import: routes and auth against truth.json", P["H-PI1"][2],
                  {"recall": round(recall, 4), "auth_exact": round(auth, 4), "extra_routes": extra, "missing": sorted(f"{m} {p}" for (m, p) in want if (m, p) not in got),
                   "auth_wrong": {f"{m} {p}": [want[(m, p)], got.get((m, p))] for (m, p) in want if (m, p) in got and got[(m, p)] != want[(m, p)]}},
                  recall >= 0.90 and auth >= 0.85 and not extra,
                  reading="the importer's `Customer` role is read as truth's `Member` (renaming it is out of scope); auth is scored over all truth routes, a missing route counts as wrong")  # fmt: skip


def h_pi2(ctx: Ctx, tgt: dict[str, Any]) -> dict[str, Any]:
    rep = P2.report_of(h_dir(ctx, tgt))
    inf = [i for i in rep["rules"].get("inferences", []) if i.get("rule") == "owner_uses"]
    truth = tgt["truth"]["owners"]
    confident = {i["table"]: i["column"] for i in inf if i.get("column") and not i.get("unclear")}
    unclear = {i["table"]: list(i.get("columns", [])) for i in inf if i.get("unclear")}
    wrong = {t: c for t, c in confident.items() if truth.get(t) != c}
    hit = [t for t, c in truth.items() if confident.get(t) == c or c in unclear.get(t, [])]
    recall = len(hit) / len(truth) if truth else 1.0
    return result("H-PI2", "import: owners", P["H-PI2"][2], {"confident": confident, "unclear": unclear, "confident_wrong": wrong, "recall": round(recall, 4)},
                  not wrong and recall >= 0.5, reading="pass: 0 confident-wrong owners and recall >= 0.5 (the prediction's lower bound)")  # fmt: skip


def h_pi3(ctx: Ctx, tgt: dict[str, Any]) -> dict[str, Any]:
    a = item_audit(P2.report_of(h_dir(ctx, tgt)))
    ok = (
        a["share"] >= 0.50
        and not a["refuted_without_correction"]
        and not a["unexercised_without_reason"]
    )
    return result("H-PI3", "import: rule items on the seed", P["H-PI3"][2],
                  {"items": a["items"], "verdicts": a["verdicts"], "verified_share": a["share"], "refuted": a["refuted"], "refuted_without_correction": a["refuted_without_correction"],
                   "unexercised_without_reason": a["unexercised_without_reason"]}, ok, in_predicted_range={"share": 0.50 <= a["share"] <= 0.70, "refuted": len(a["refuted"]) <= 4}, unexercised=a["unexercised"])  # fmt: skip


def h_pi4(ctx: Ctx, tgt: dict[str, Any]) -> dict[str, Any]:
    cand = P1.load_candidate(h_dir(ctx, tgt))
    seed = h_seed_map(ctx, tgt)
    v2 = P1.verdict_map(r_on_spec3(ctx, tgt, cand.SPEC, "variant2"))
    sc = S3R.score_pi4(seed, v2)
    return result(
        "H-PI4",
        "the corrected spec re-probed on variant2",
        P["H-PI4"][2],
        {k: sc[k] for k in ("seed_verified", "still_verified", "fraction", "lost")},
        bool(sc["pass"]),
    )


def h_pi5(ctx: Ctx, tgt: dict[str, Any]) -> dict[str, Any]:
    props = json.loads((h_dir(ctx, tgt) / "proposals.json").read_text())
    machines = tgt["truth"].get("machines", {})
    found: dict[str, Any] = {}
    for key, m in machines.items():
        struct, col = key.split(".", 1)
        edges = [list(e) for p in props for e in (p.get("spec_object") or {}).get("edges", ())
                 if p.get("kind") == "machine" and (p.get("spec_object") or {}).get("table") in (struct, struct.lower(), f"{struct.lower()}s") and (p.get("spec_object") or {}).get("column") == col]  # fmt: skip
        first = list(m["edges"][0]) if m["edges"] else None
        found[key] = {
            "first_edge": first,
            "proposed_edges": edges,
            "first_edge_proposed": first in edges,
        }
    return result(
        "H-PI5",
        "import: proposals",
        P["H-PI5"][2],
        {"count": len(props), "machines": found},
        bool(found) and all(v["first_edge_proposed"] for v in found.values()),
    )


def h_pi7(ctx: Ctx, tgt: dict[str, Any]) -> dict[str, Any]:
    trees = []
    for i in range(ctx.runs):
        d = ctx.scratch / "imp7" / tgt["label"] / f"r{i}"
        t = h_import(ctx, tgt, d)
        trees.append(P1.normalised_tree(d) if t["rc"] == 0 else {"error": t["stderr"][-200:]})
    same = sum(1 for t in trees if t == trees[0])
    return result(
        "H-PI7",
        f"the import {ctx.runs}x into separate scratch dirs",
        P["H-PI7"][2],
        {"identical": same, "of": ctx.runs},
        same == ctx.runs,
    )


def h_pr3(ctx: Ctx, tgt: dict[str, Any]) -> dict[str, Any]:
    d = h_dir(ctx, tgt)
    rep = P2.report_of(d)
    theirs, mine = S3R.mapped_verdicts(rep), h_seed_map(ctx, tgt)
    diff = S3R.diff_maps(theirs, mine)
    return result(
        "H-PR3",
        "layer R with the imported candidate_spec.py",
        P["H-PR3"][2],
        {
            "items_import": len(theirs),
            "items_rerun": len(mine),
            "differences [import, rerun]": diff,
        },
        not diff,
    )


def truth_private(tgt: dict[str, Any]) -> dict[str, set[str]]:
    return {
        t["struct"]: {c["name"] for c in t["columns"] if c.get("private")}
        for t in tgt["truth"]["tables"]
    }


def h_pa1(ctx: Ctx, tgt: dict[str, Any]) -> dict[str, Any]:
    from lattice.gocheck.derive import derive_go_policy

    p = derive_go_policy(tgt["module_dir"])
    tp = truth_private(tgt)
    public_marked = {
        t: sorted(set(cols) - tp.get(t, set()))
        for t, cols in p.private.items()
        if set(cols) - tp.get(t, set())
    }
    owners_extra = {t: c for t, c in p.owners.items() if tgt["truth"]["owners"].get(t) != c}
    return result("H-PA1", "derive_go_policy: private columns and owners within the truth", P["H-PA1"][2],
                  {"private": {k: list(v) for k, v in p.private.items()}, "owners": dict(p.owners), "public_columns_marked_private": public_marked, "owners_not_in_truth": owners_extra},
                  not public_marked and not owners_extra)  # fmt: skip


def h_pa0(ctx: Ctx, tgt: dict[str, Any]) -> dict[str, Any]:
    from lattice.gocheck import run_gocheck
    from lattice.gocheck.derive import derive_go_policy, go_spec_policy

    cand = P1.load_candidate(h_dir(ctx, tgt))
    derived = derive_go_policy(tgt["module_dir"])
    orig = run_gocheck(
        tgt["module_dir"], go_spec_policy(cand.SPEC, tgt["module_dir"], derived), strict=True
    )
    outside = [f for f in orig if f.key not in derived.baseline]
    return result("H-PA0", "layer A on the original under go_spec_policy of its candidate spec", P["H-PA0"][2],
                  {"total": len(orig), "outside_baseline": [f.key for f in outside], "baseline": sorted(derived.baseline)}, not outside)  # fmt: skip


def h_judge(ctx: Ctx, tgt: dict[str, Any]) -> dict[str, Any]:
    from lattice.gocheck import GoPolicy
    from lattice.gocheck.derive import derive_go_policy

    t = tgt["truth"]
    derived = derive_go_policy(tgt["module_dir"])
    pol = GoPolicy(tables={x["struct"]: x["table"] for x in t["tables"]}, users_table=derived.users_table, owners=t["owners"],
                   owner_via={k: tuple(v) for k, v in t.get("owner_via", {}).items()}, private={k: tuple(sorted(v)) for k, v in truth_private(tgt).items() if v})  # fmt: skip
    dump = {r["route"]: r for r in route_dump(ctx, tgt["module_dir"], pol)}
    public = {
        (r["method"].upper(), gin_path(r["path"])) for r in t["routes"] if r["auth"] == "Public"
    }
    scoped = sorted(
        {
            (r["route"].split(" ", 1)[0].upper(), gin_path(r["route"].split(" ", 1)[1]))
            for r in t["refusals"]
            if r["cause"] in ("not_owned", "not_found")
        }
        - public
    )
    judged = [f"{m} {p}" for m, p in scoped if dump.get(f"{m} {p}", {}).get("judged")]
    unseen = [f"{m} {p}" for m, p in scoped if f"{m} {p}" not in dump]
    share = len(judged) / len(scoped) if scoped else 0.0
    return result("H-JUDGE", "share of the truth's owner-scoped routes judged by layer A (complete reach)", P["H-JUDGE"][2],
                  {"owner_scoped_routes": len(scoped), "judged": len(judged), "share": round(share, 4), "not_judged": [f"{m} {p}" for m, p in scoped if f"{m} {p}" not in judged], "not_in_the_checker's_route_list": unseen},
                  share >= 0.75, reading="owner-scoped = the non-Public truth routes with a refusal of cause not_owned or not_found (the truth schema has no per-route table)")  # fmt: skip


def h_x(ctx: Ctx, tgt: dict[str, Any]) -> dict[str, Any]:
    h_dir(ctx, tgt)
    wall = ctx.timings.get(f"import_{tgt['label']}_s", 1e9)
    return result(
        "H-X",
        "wall time of the import (binary prebuilt)",
        P["H-X"][2],
        {"wall_s": round(wall, 1)},
        wall <= 180,
    )


ARM_H: dict[str, Callable[[Ctx, dict[str, Any]], dict[str, Any]]] = {
    "H-PI1": h_pi1, "H-PI2": h_pi2, "H-PI3": h_pi3, "H-PI4": h_pi4, "H-PI5": h_pi5, "H-PI7": h_pi7, "H-PR3": h_pr3,
    "H-PA1": h_pa1, "H-PA0": h_pa0, "H-JUDGE": h_judge, "H-X": h_x,
}  # fmt: skip


# ---- running the rows ------------------------------------------------------------------------------------------

ROW_FN: dict[str, Callable[[Ctx], dict[str, Any]]] = {
    **ARM0,
    **ARMS_S,
    **ARM_M,
    **ARM_J,
    **ARM_2,
    **ARM_P,
    **ARM_X,
}
ORDER = [
    *ROWS_OF["0"],
    *ROWS_OF["S"],
    *ROWS_OF["M"],
    *ROWS_OF["J"],
    *ROWS_OF["2"],
    *ROWS_OF["H"],
    *ROWS_OF["X"],
    *[r for r in ROWS_OF["P"] if r != "PW"],
    "PW",
]


def arm_of(pid: str) -> str:
    return P[pid][0]


def one_row(ctx: Ctx, pid: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    if pid in ("PB-N", "PB-T", "PB-D") and ctx.shim_mark is None and ctx.shim_log.exists():
        ctx.shim_mark = len([ln for ln in ctx.shim_log.read_text().splitlines() if ln.strip()])
    why = missing(ctx, pid)
    if why is not None:
        r = not_run(pid, "(a prerequisite is missing)", why, P[pid][2])
    else:
        try:
            r = fn()
        except Exception as err:  # noqa: BLE001 -- a row whose code raised is a FAIL, never NOT RUN (round-2 Amendment 6 rule a)
            r = result(
                pid,
                "(the row raised)",
                P[pid][2],
                f"{type(err).__name__}: {err}",
                False,
                traceback=traceback.format_exc()[-1500:],
            )
    r["gated"] = P[pid][1]
    r["arm"] = arm_of(pid)
    print(f"{pid}: pass={r['pass']}{' (not gated)' if not r['gated'] else ''}", flush=True)
    return r


def run_rows(
    ctx: Ctx, ids: Sequence[str], targets: Sequence[dict[str, Any]] = ()
) -> list[dict[str, Any]]:
    """Run the rows ``ids`` (in the prereg's order) on ``ctx``. No freeze check and no ledger here: those belong to
    :func:`main`; the mutants runner calls this in a subprocess of a scratch copy of the tree under mutation."""
    out: list[dict[str, Any]] = []
    for pid in [i for i in ORDER if i in ids]:
        if arm_of(pid) == "H":
            tgts = list(targets) or ([ctx.h_target] if ctx.h_target else [])
            if not tgts:
                out.append(one_row(ctx, pid, lambda: {}))
                continue
            for tgt in tgts:
                ctx.h_target = tgt
                r = one_row(ctx, pid, lambda tgt=tgt, pid=pid: ARM_H[pid](ctx, tgt))
                if len(tgts) > 1:
                    r["id"] = f"{pid}[{tgt['label']}]"
                out.append(r)
        else:
            out.append(one_row(ctx, pid, lambda pid=pid: ROW_FN[pid](ctx)))
    return out


def self_test_problems(rows: Sequence[Mapping[str, Any]], ctx: Ctx) -> list[str]:
    """RM1: a row that is NOT RUN although its named prerequisite is present, and a gated row that is absent."""
    problems = []
    for r in rows:
        base = r["id"].split("[")[0]
        if r["pass"] is None and missing(ctx, base) is None:
            problems.append(f"{r['id']}: NOT RUN although its prerequisites are present")
    return problems


# ---- the command line -----------------------------------------------------------------------------------------


def build_ctx(args: argparse.Namespace, scratch: Path, out: Path) -> Ctx:
    ctx = Ctx(scratch, out, BASE_REF)
    ctx.base_dir = Path(args.base).resolve()
    ctx.streak_src = Path(args.streak).resolve() if args.streak else None
    ctx.jobsched_src = Path(args.jobsched).resolve() if args.jobsched else None
    ctx.owner = args.container_owner
    ctx.runs = args.runs
    ctx.no_docker = bool(args.no_docker)
    ctx.driver_py = args.driver_python or sys.executable
    ctx.pytest_base_log = Path(args.pytest_base_log) if args.pytest_base_log else None
    return ctx


def add_extra_snapshots(ctx: Ctx) -> None:
    for p in (ctx.streak_src, ctx.jobsched_src):
        if p is not None and p.is_dir():
            ctx.extra_snaps[str(p)] = P1.snapshot(p)
    if ctx.h_target and not ctx.h_target.get("snapshot_rel"):
        ctx.extra_snaps[str(ctx.h_target["module_dir"])] = P1.snapshot(ctx.h_target["module_dir"])


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--arm",
        default="0,S,M,J,2,P,H,X",
        help="comma list of 0, S, M, J, 2 (K8, K10), P (cross-arm), H, X (default extract)",
    )
    ap.add_argument("--kind", default="shakedown", choices=list(KINDS))
    ap.add_argument("--base", required=True, help="an existing `git archive 978b468`")
    ap.add_argument(
        "--streak", default="", help="an extracted `git archive` of Streak a0679e9 (read only)"
    )
    ap.add_argument(
        "--jobsched",
        default="",
        help="an extracted `git archive` of job-scheduler-go 00dd1de (read only)",
    )
    ap.add_argument("--s4", default="", help="the held-out Go module directory (tests/spec/<name>)")
    ap.add_argument("--s4-truth", default="", help="its truth.json (default: <s4>/truth.json)")
    ap.add_argument(
        "--freeze",
        default="",
        help="the freeze file; every entry is verified before the first metric",
    )
    ap.add_argument("--out", required=True)
    ap.add_argument("--scratch", default="")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--only", default="", help="restrict to these row ids (a shakedown only)")
    ap.add_argument(
        "--container-owner", default="g3-b1", help="the lattice.owner label of M-R1's container"
    )
    ap.add_argument(
        "--driver-python",
        default="",
        help="a python with lattice[db] (PyMySQL) and bcrypt, for M-R1",
    )
    ap.add_argument(
        "--no-docker",
        action="store_true",
        help="the operator does not allow Docker: M-R1 is NOT RUN (prerequisite named)",
    )
    ap.add_argument("--pytest-base-log", default="", help="an earlier PB-T run of the base")
    ap.add_argument(
        "--self-test",
        action="store_true",
        help="every arm's code path, Arm H on the stand-ins; fails on an improper NOT RUN (RM1)",
    )
    return ap


def now() -> str:
    return S3R._now()


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    this = Path(__file__).resolve()
    arms = [a for a in args.arm.split(",") if a]
    bad = [a for a in arms if a not in ARMS]
    if bad:
        raise SystemExit(f"unknown arm(s) {bad}; the arms are {ARMS}")
    if args.self_test:
        return self_test(args, arms, this)
    if args.only and args.kind != "shakedown":
        raise SystemExit(
            "--only restricts the rows of a shakedown; a scored, rerun or verify run covers every row of its arms"
        )
    if not args.freeze:
        raise SystemExit(
            "--freeze is required: the self-hash check runs before any metric and cannot be skipped"
        )
    out = Path(args.out).resolve()
    scratch = Path(args.scratch).resolve() if args.scratch else out.parent / "scratch-g3"
    runner_sha = sha(this)
    lattice_file = lattice_file_inside()
    freeze_path = Path(args.freeze).resolve()
    freeze_sha = sha(freeze_path) if freeze_path.is_file() else "missing"
    entries = S3R.read_ledger(LEDGER)
    for a in arms:
        why = S3R.ledger_refusal(entries, f"G3:{a}", args.kind, runner_sha, freeze_sha)
        if why:
            raise SystemExit(f"the ledger refuses arm {a}: {why}")
    run_id = uuid.uuid4().hex[:12]

    def entry(arm: str, status: str, **extra: Any) -> dict[str, Any]:
        return {"ts": now(), "run_id": run_id, "runner": "go_parity_probes3", "runner_sha256": runner_sha, "freeze_sha256": freeze_sha,
                "arm": f"G3:{arm}", "kind": args.kind, "out": str(out), "lattice_file": lattice_file, "status": status, **extra}  # fmt: skip

    for a in arms:
        S3R.append_ledger(entry(a, "started"), LEDGER)
    metric_started = False
    try:
        freeze = verify_freeze(freeze_path, this)  # before any metric, never skipped
        freeze["lattice_file"] = lattice_file
        ids = [
            i for i in ORDER if arm_of(i) in arms and (not args.only or i in args.only.split(","))
        ]
        print(f"gated rows ({sum(P[i][1] for i in ids)}): {[i for i in ids if P[i][1]]}")
        print(f"reported rows: {[i for i in ids if not P[i][1]]}", flush=True)
        ctx = build_ctx(args, scratch, out)
        tgt = None
        if "H" in arms and args.s4:
            s4 = Path(args.s4).resolve()
            tgt = make_target(
                s4.parent,
                s4.name,
                Path(args.s4_truth) if args.s4_truth else s4 / "truth.json",
                "S4",
            )
            ctx.h_target = tgt
        setup(ctx, builds=any(arm_of(i) in ("S", "P", "H") for i in ids))
        add_extra_snapshots(ctx)
        freeze["import_subprocess_lattice_file"] = S3R.subprocess_lattice_file(ctx)
        metric_started = True
        rows = run_rows(ctx, ids, [tgt] if tgt else [])
    except BaseException as err:  # noqa: BLE001 -- recorded in the ledger, then re-raised
        status = "crash_after_metric" if metric_started else "crash_before_metric"
        for a in arms:
            S3R.append_ledger(entry(a, status, error=repr(err)[:400]), LEDGER)
        if isinstance(err, S3R.FreezeError):
            print(err, file=sys.stderr)
            out.mkdir(parents=True, exist_ok=True)
            (out / "probes3.json").write_text(
                json.dumps({"aborted": str(err), "kind": args.kind, "results": []}, indent=2) + "\n"
            )
            return 2
        raise
    calls = [ln for ln in ctx.shim_log.read_text().splitlines() if ln.strip()]
    doc = {"runner": "go_parity_probes3", "runner_sha256": runner_sha, "kind": args.kind, "run_id": run_id, "base_ref": BASE_REF, "arms": arms,
           "lattice_file": lattice_file, "freeze": freeze, "timings": ctx.timings, "shim_calls": calls, "python": sys.version.split()[0],
           "gated": [i for i in ids if P[i][1]], "results": rows}  # fmt: skip
    out.mkdir(parents=True, exist_ok=True)
    (out / "probes3.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")
    for a in arms:
        S3R.append_ledger(entry(a, "completed"), LEDGER)
    summarise(rows)
    print(f"ledger sha256: {sha(LEDGER)}")
    return 0


def summarise(rows: Sequence[Mapping[str, Any]]) -> None:
    gated = [r for r in rows if r.get("gated")]
    passed = [r["id"] for r in gated if r["pass"] is True]
    failed = [r["id"] for r in gated if r["pass"] is False]
    nr = [r["id"] for r in rows if r["pass"] is None]
    print(
        f"gated passed {len(passed)}: {passed}\ngated failed {len(failed)}: {failed}\nnot run {len(nr)}: {nr}"
    )
    rep = [(r["id"], r["pass"]) for r in rows if not r.get("gated")]
    print(f"reported: {rep}")


def self_test(args: argparse.Namespace, arms: list[str], this: Path) -> int:
    """Every arm's code path (Arm H on the stand-ins S2 and S3 with truths in S4's schema). Not a result: it writes only to a
    scratch directory, never to the ledger, and fails on an improper NOT RUN (RM1)."""
    out = Path(args.out).resolve()
    if "docs/results/data" in out.as_posix():
        raise SystemExit("--self-test writes to a scratch directory, not to the results data")
    scratch = Path(args.scratch).resolve() if args.scratch else out / "scratch-selftest"
    args.runs = min(args.runs, 2)
    ctx = build_ctx(args, scratch, out)
    tgts = [
        make_target(SPEC_DIR, "goginapp", STANDIN / "s2/truth.json", "S2"),
        make_target(SPEC_DIR, "gotickets", STANDIN / "s3/truth.json", "S3"),
    ]
    ctx.h_target = tgts[0]
    setup(ctx)
    add_extra_snapshots(ctx)
    ids = [i for i in ORDER if arm_of(i) in arms]
    ctx.pb_t_targets = (
        "tests/gocheck",
        "tests/importer/test_go_rules3.py",
    )  # a slice of the suite; the code path is the same
    rows = run_rows(ctx, ids, tgts)
    problems = self_test_problems(rows, ctx)
    gated_ids = {i for i in ids if P[i][1]}
    seen = {r["id"].split("[")[0] for r in rows}
    problems += [f"{i}: no row was produced" for i in sorted(gated_ids - seen)]
    problems += [
        f"{r['id']}: the row raised: {r['observed']}"
        for r in rows
        if "traceback" in r.get("evidence", {})
    ]
    out.mkdir(parents=True, exist_ok=True)
    (out / "selftest.json").write_text(
        json.dumps(
            {"rows": rows, "problems": problems, "runner_sha256": sha(this)}, indent=2, default=str
        )
        + "\n"
    )
    summarise(rows)
    print(
        "SELF-TEST",
        "FAILED: " + "; ".join(problems)
        if problems
        else "OK: every arm's code path ran; no improper NOT RUN",
    )
    return 1 if problems else 0


def rows_cli(argv: list[str] | None = None) -> int:
    """The entry the mutants runner uses in a subprocess of a scratch copy of the tree: run some rows, print them as JSON.
    It has no freeze check or ledger by design (it is not ``main``; nothing is scored through it)."""
    ap = parser()
    ap.add_argument("--ids", required=True)
    args = ap.parse_args(argv)
    out = Path(args.out).resolve()
    scratch = Path(args.scratch).resolve() if args.scratch else out / "scratch"
    ctx = build_ctx(args, scratch, out)
    out.mkdir(parents=True, exist_ok=True)
    ids = args.ids.split(",")
    setup(ctx, builds=any(arm_of(i) in ("S", "P", "H") for i in ids))
    add_extra_snapshots(ctx)
    rows = run_rows(ctx, ids)
    (out / "rows.json").write_text(json.dumps(rows, indent=2, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
