"""Probe runner for the layer 3 experiment (contracts between projects).

Implements the probes of ``docs/results/2026-10-13-contracts-prereg.md`` (section 5): B0, D1-D10,
C1-C4, L1-L5, V1-V6, W1-W6, A1, S1 and T1. Predictions and pass rules are copied from that
document; a probe that does not match is reported as a finding with its raw output, never adapted.

    python dogfood/contracts/probes.py --runs 10 --scratch DIR \
        --out docs/results/data/2026-10-13-contracts [--only B0,D1,...]

Every probe works on ``git clone --no-hardlinks`` scratch copies of job-scheduler at ef17cf8 under
``DIR``; the real repository is only read (S1 proves it). No LLM and no API key are used. Every
deterministic probe runs ``--runs`` times (default 10) and all canonical outputs (``elapsed_s``
excluded) must be identical. Raw per-run outputs go to ``OUT/<ID>.json`` and ``OUT/summary.json``.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
PROVIDER = Path("/Users/vivekgade/Documents/job-scheduler")
OLD_PRIMARY = Path("/Users/vivekgade/Documents/lattice")  # the checkout the prereg names for W5
OLD_BASE = Path("/Users/vivekgade/Documents/lattice-contracts-base")  # detached at c0cdefc
OLD_HEAD = "c0cdefc"
REF = "ef17cf8"
CI_BOT = REPO / "dogfood" / "contracts" / "ci-bot"
GOLDEN = REPO / "dogfood" / "contracts" / "golden" / "lattice.contracts.lock.json"
MANIFEST = "lattice.workspace.toml"
LOCK = "lattice.contracts.lock.json"
T = "spec/tickets.py"
S = "spec/spec.py"
RUNS_PY = "src/job_scheduler/routers/runs.py"
SCHEMAS_PY = "src/job_scheduler/schemas.py"
RUN_EP = "POST /applications/{}/jobs/{}/runs"
LOGIN = "POST /auth/login"
RUN_GET = "GET /applications/{}/runs/{}"
CANCEL = "POST /applications/{}/runs/{}/cancel"
LIST_RUNS = "GET /applications/{}/jobs/{}/runs"
GAP_TOKEN = ("response_field_unobserved", LOGIN, "access_token")

# R1's expect_status per template (r1-job-scheduler-surface.json, templates, ids as `{}`); the
# PATCH template (405 only) has no 2xx step, so the export excludes it (prereg B0).
R1_STATUSES: dict[str, list[int]] = {
    "DELETE /applications/{}/members/{}": [204, 404],
    "GET /applications": [200, 401],
    "GET /applications/{}": [200, 404],
    "GET /applications/{}/jobs": [200, 404],
    "GET /applications/{}/jobs/{}": [200, 404],
    "GET /applications/{}/jobs/{}/runs": [200],
    "GET /applications/{}/members": [200, 403],
    "GET /applications/{}/runs/{}": [200, 404],
    "GET /users/me": [200, 401],
    "POST /applications": [201, 403, 409, 422],
    "POST /applications/{}/jobs": [201, 401, 403, 404, 409, 422],
    "POST /applications/{}/jobs/{}/activate": [200, 403],
    "POST /applications/{}/jobs/{}/deactivate": [200],
    "POST /applications/{}/jobs/{}/runs": [201, 401, 404, 409],
    "POST /applications/{}/runs/{}/cancel": [200, 404, 409],
    "POST /auth/login": [200, 401, 403],
    "POST /users": [201, 403, 409, 422],
    "POST /worker/run-once": [200, 403],
    "POST /worker/schedule-tick": [200, 403],
    "PUT /applications/{}/members/{}": [200, 403, 404, 422],
}
B0_TOKEN = {"GET /applications", "POST /applications/{}/jobs", RUN_EP, "GET /users/me"}

# Disclosed post hoc notes, written to summary.json (the orchestrator copies them to the results).
POST_HOC_SCRIPT_CORRECTIONS: list[str] = []
POST_HOC_PROCEDURE: list[str] = [
    "W5 old code: the prereg names /Users/vivekgade/Documents/lattice; the orchestrator said that "
    "checkout has no workspace.py. At run time it is on next-phases at c0cdefc with "
    "lattice/workspace.py present and unmodified, so it is the primary reference (checked at "
    "run time); the detached worktree lattice-contracts-base is compared as well, both at c0cdefc.",
    "Item notation: the prereg writes the auth item as 'as_user eve'; the code writes "
    "'as_user:eve'. The comparison normalises ':' after as_user to a space, nothing else.",
    "D1 item: the prereg gives no item for endpoint_missing; the expected item is the empty "
    "string (the claim 'endpoint' has none).",
    "A CHANGED item's `relied` flag is compared only where the prereg states it "
    "(D6 false, D8 true, D9 true, C4 false); otherwise it is only recorded.",
    "The scratch workspace manifests use absolute provider and consumer paths and sit in their "
    "own directory so one probe can hold several manifest variants over the same clones.",
    "C2's extra blank line is placed right after the changed RUNS line (prereg: 'one extra "
    "blank line'); W1's clone gets the raise line appended and committed.",
    "W and V probes that need a lock use the one pinned on the clean clone, as the D/C/V rule "
    "says; W1's static variant has it so that no_pin cannot mask export_failed.",
    "A1 uses a minimal scratch project (REQUIREMENTS.md, spec.py, spec.trace.json) as the "
    "`--project` of the MCP server and Studio; the contract surfaces are workspace-level.",
    "C3 is a slice swap at asserted line numbers (173-190 login, 191-201 me), not a "
    "str.replace; the whole-line deletes (D1, D9) assert the count of the quoted fragment, then "
    "delete the lines that hold it; L4 deletes the line including its newline.",
    "T1 wall times are whole subprocess times (interpreter start included); the check's own "
    "elapsed_s is recorded beside them. T1 runs its own 10 static and 10 live checks.",
    "S1's git calls use --no-optional-locks so that `git status` cannot refresh the real "
    "repository's index.",
    "A1 reports one extra, non-counted comparison on a live link (CLI --static-only, MCP, API): "
    "the only difference allowed is the live reason.",
    "W5 and W6 also record which lattice/workspace.py a child imports under each PYTHONPATH "
    "and that lattice.contracts is absent in the old code (a guard that the old run is old). "
    "This check was added after a first full run (35/35) and before the final run.",
    "L5 'no request sent': a listener on 127.0.0.1:8000 (when bindable) counts connections; "
    "for hosts that cannot be observed the evidence is the exit code, the error code and the "
    "wall time (no connection attempt can complete in well under a second).",
]


# ---- low-level helpers ---------------------------------------------------------------------


@dataclass
class Call:
    argv: list[str]
    rc: int
    out: str
    err: str
    wall: float

    def json(self) -> Any:
        return json.loads(self.out)


def strip_elapsed(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: strip_elapsed(v) for k, v in x.items() if k != "elapsed_s"}
    if isinstance(x, list):
        return [strip_elapsed(v) for v in x]
    return x


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canon_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def norm_item(item: str) -> str:
    return item.replace("as_user:", "as_user ")


@dataclass
class Edit:
    """One `str.replace` on a file of the clone, applied after asserting its occurrence count."""

    path: str
    old: str
    new: str
    count: int = 1
    whole_lines: bool = False  # `old` is a fragment of whole lines that are deleted entirely

    def apply(self, root: Path) -> dict[str, Any]:
        p = root / self.path
        text = p.read_text()
        n = text.count(self.old)
        assert n == self.count, f"{self.path}: expected {self.count} of {self.old!r}, found {n}"
        old, new = self.old, self.new
        if self.whole_lines:
            i = text.index(old)
            start = text.rfind("\n", 0, i) + 1
            end = text.index("\n", i + len(old)) + 1
            old, new = text[start:end], ""
        p.write_text(text.replace(old, new))
        return {"path": self.path, "old": old, "new": new, "count": n}


@dataclass
class Result:
    id: str
    prediction: str
    observed: str = ""
    passed: bool = False
    runs: list[Any] = field(default_factory=list)
    identical: bool = False
    checks: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


class Ctx:
    def __init__(self, runs: int, scratch: Path, out: Path) -> None:
        self.runs = runs
        self.out = out
        self.root = scratch / f"contracts-probes-{os.getpid()}"
        self.work = self.root / "w"
        self.tmp = self.root / "tmp"
        for d in (self.work, self.tmp):
            d.mkdir(parents=True)
        self.env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("ANTHROPIC") and k not in ("LATTICE_WORKSPACE", "PYTHONPATH")
        }
        self.env.update(
            PYTHONPATH=str(REPO),
            PYTHONDONTWRITEBYTECODE="1",
            TMPDIR=str(self.tmp),
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_CONFIG_SYSTEM=os.devnull,
            GIT_AUTHOR_NAME="probe",
            GIT_AUTHOR_EMAIL="probe@example.com",
            GIT_COMMITTER_NAME="probe",
            GIT_COMMITTER_EMAIL="probe@example.com",
            GIT_AUTHOR_DATE="2026-10-13T00:00:00+00:00",
            GIT_COMMITTER_DATE="2026-10-13T00:00:00+00:00",
        )
        self.base: Path | None = None
        self.last_edits: list[dict[str, Any]] = []
        self._lock: bytes | None = None
        self._gaps: list[dict[str, Any]] | None = None
        self.static_walls: list[float] = []
        self.live_walls: list[float] = []

    # -- processes
    def run(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout: float = 600,
    ) -> Call:
        t0 = time.perf_counter()
        r = subprocess.run(
            argv,
            cwd=cwd or self.work,
            env=env or self.env,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return Call(argv, r.returncode, r.stdout, r.stderr, time.perf_counter() - t0)

    def git(self, repo: Path, *args: str) -> str:
        r = self.run(["git", "-c", "commit.gpgsign=false", "-C", str(repo), *args])
        assert r.rc == 0, f"git {args}: {r.err}"
        return r.out

    def contracts(self, manifest: Path, *args: str, root: Path | None = None) -> Call:
        env = dict(self.env)
        if root is not None:
            env["PYTHONPATH"] = str(root)
        argv = [sys.executable, "-m", "lattice.contracts", *args, "--workspace", str(manifest)]
        return self.run(argv, env=env)

    def check(self, manifest: Path, *args: str) -> Call:
        c = self.contracts(manifest, "check", "ci-bot", "--json", *args)
        (self.live_walls if _has_live_run(c, args) else self.static_walls).append(c.wall)
        return c

    def lattice_cli(self, root: Path, *args: str) -> Call:
        env = dict(self.env)
        env["PYTHONPATH"] = str(root)
        return self.run([sys.executable, "-m", "lattice.cli", *args], env=env)

    # -- scratch clones and workspaces
    def prepare_base(self) -> Path:
        """The clean clone at ef17cf8 (a `git clone --no-hardlinks` of the real repo)."""
        if self.base is None:
            dst = self.root / "base-clone"
            self.run(["git", "clone", "-q", "--no-hardlinks", str(PROVIDER), str(dst)])
            self.git(dst, "checkout", "-q", REF)
            head = self.git(dst, "rev-parse", "HEAD").strip()
            assert head.startswith(REF), head
            assert self.git(dst, "status", "--porcelain", "--untracked-files=no") == ""
            self.base = dst
        return self.base

    def clone(self, name: str, edits: list[Edit] | None = None, commit: bool = True) -> Path:
        """A fresh clone of the clean clone with `edits` applied (and committed)."""
        d = self.work / name
        d.mkdir(parents=True)
        dst = d / "scheduler"
        self.run(["git", "clone", "-q", "--no-hardlinks", str(self.prepare_base()), str(dst)])
        self.last_edits = [e.apply(dst) for e in (edits or [])]
        if edits and commit:
            self.git(dst, "add", *sorted({e.path for e in edits}))
            self.git(dst, "commit", "-q", "-m", f"probe {name}")
        return dst

    def workspace(
        self,
        name: str,
        clone: Path,
        *,
        contract: str | None = "static",
        live_url: str | None = None,
        lock: bytes | None | str = "clean",
        variant: str = "m",
    ) -> Path:
        """Manifest `name/<variant>/lattice.workspace.toml` over `clone` and the consumer copy."""
        d = self.work / name
        bot = d / "ci-bot"
        if not bot.exists():
            shutil.copytree(CI_BOT, bot, ignore=shutil.ignore_patterns("__pycache__", LOCK))
        data = self.clean_lock() if lock == "clean" else lock
        if isinstance(data, bytes):
            (bot / LOCK).write_bytes(data)
        md = d / variant
        md.mkdir(exist_ok=True)
        link = 'from = "ci-bot"\nto = "scheduler"\nkind = "uses"\n'
        if contract is not None:
            link += f"contract = {json.dumps(contract) if isinstance(contract, str) else 'true'}\n"
        if live_url is not None:
            link += f"live_url = {json.dumps(live_url)}\n"
        (md / MANIFEST).write_text(
            '[workspace]\nname = "probe"\n\n'
            f'[[project]]\nname = "scheduler"\npath = "{clone}"\nrole = "provider"\n'
            'spec = "spec.tickets:t5"\napp = "job_scheduler:make_app"\n'
            'db = "spec.seed:build_seed_db"\nlogin = "spec.seed:login"\nsrc = "src"\n'
            'env = { JOB_SCHEDULER_REPO_ROOT = "$PROJECT" }\n\n'
            f'[[project]]\nname = "ci-bot"\npath = "{bot}"\nrole = "consumer"\n\n'
            f"[[link]]\n{link}"
        )
        return md / MANIFEST

    def clean_lock(self) -> bytes:
        """The lock pinned on the clean clone (the D/C/V rule); computed once by a plain pin."""
        if self._lock is None:
            clone = self.clone("pin-clean")
            m = self.workspace("pin-clean", clone, lock=None)
            c = self.contracts(m, "pin", "ci-bot", "--json")
            assert c.rc == 0, c.out + c.err
            self._lock = (m.parent.parent / "ci-bot" / LOCK).read_bytes()
        return self._lock

    def clean_gaps(self) -> list[dict[str, Any]]:
        if self._gaps is None:
            clone = self.clone("gaps-clean")
            m = self.workspace("gaps-clean", clone)
            c = self.contracts(m, "check", "ci-bot", "--json")
            self._gaps = c.json()["links"][0]["gaps"]
        return self._gaps

    # -- repeated execution
    def repeat(self, fn: Callable[[int], Any]) -> tuple[list[Any], bool]:
        runs = [fn(i) for i in range(self.runs)]
        return runs, all(r == runs[0] for r in runs)


def _has_live_run(c: Call, args: tuple[str, ...]) -> bool:
    if "--static-only" in args:
        return False
    try:
        live = c.json()["links"][0].get("live")
    except Exception:  # noqa: BLE001 - a non-JSON answer is a static-time failure
        return False
    return bool(live and live.get("status") not in (None, "not_run", "not_built"))


def canon_call(c: Call, root: Path | None = None) -> dict[str, Any]:
    """The canonical record of one call: exit code, JSON answer (no elapsed_s) or text, stderr."""
    rec: dict[str, Any] = {"rc": c.rc, "err": c.err}
    try:
        rec["doc"] = strip_elapsed(c.json())
    except ValueError:
        rec["out"] = c.out
    return rec


def link0(rec: dict[str, Any]) -> dict[str, Any]:
    return rec["doc"]["links"][0]


def findings(link: dict[str, Any], cls: str | None = None) -> list[dict[str, Any]]:
    return [f for f in link["findings"] if cls is None or f["class"] == cls]


def broken_set(link: dict[str, Any]) -> list[tuple[str, str, str]]:
    return sorted(
        (f["kind"], f["endpoint"], norm_item(f["item"])) for f in findings(link, "BROKEN")
    )


def changed_set(link: dict[str, Any]) -> list[tuple[str, str, str, str, bool]]:
    return sorted(
        (f["kind"], f.get("where", ""), f["endpoint"], norm_item(f["item"]), f.get("relied"))
        for f in findings(link, "CHANGED")
    )


def codes_in(doc: dict[str, Any]) -> list[str]:
    out = [lk["error"]["code"] for lk in doc.get("links", []) if lk.get("error")]
    return sorted(out + [p["code"] for p in doc.get("problems", [])])


def gap_set(link: dict[str, Any]) -> list[tuple[str, str, str]]:
    return sorted((g["kind"], g["endpoint"], g["item"]) for g in link["gaps"])


def fmt_b(items: list[tuple[str, ...]]) -> str:
    return "; ".join(" ".join(x for x in it if x != "") for it in items) or "none"


def fmt_c(items: list[tuple[str, str, str, str, Any]]) -> str:
    parts = []
    for kind, where, ep, item, relied in items:
        rel = {True: " relied", False: " unrelied"}.get(relied, "")
        parts.append(" ".join(x for x in (kind, where, item, "@", ep) if x) + rel)
    return "; ".join(parts) or "none"


# ---- the drift and control tables (prereg section 5; exact strings and counts) -------------

I16 = " " * 16
I21 = " " * 21

DRIFTS: list[dict[str, Any]] = [
    {
        "id": "D1",
        "edits": [
            Edit(
                T,
                'Step("GET", RUNS, "eve", expect_status=200,\n'
                f'{I21}expect_body=[{{"id": 1, "status": "queued"}}]),',
                "",
                1,
                whole_lines=True,
            )
        ],
        "worst": "BROKEN",
        "broken": [("endpoint_missing", LIST_RUNS, "")],
        "changed": [],
    },
    {
        "id": "D2",
        "edits": [
            Edit(
                T,
                'Step("POST", RUNS, "eve", expect_status=201',
                'Step("POST", RUNS, "eve", expect_status=200',
                5,
            ),
            Edit(
                T,
                'Step("POST", RUNS, "ann", expect_status=201',
                'Step("POST", RUNS, "ann", expect_status=200',
                6,
            ),
            Edit(
                T,
                'Step("POST", f"{JOBS}/2/runs", "eve", expect_status=201',
                'Step("POST", f"{JOBS}/2/runs", "eve", expect_status=200',
                1,
            ),
        ],
        "worst": "BROKEN",
        "broken": [("status_not_observed", RUN_EP, "201")],
        "changed": [("status_added", "", RUN_EP, "200", None)],
    },
    {
        "id": "D3",
        "edits": [
            Edit(
                T,
                'expect_body={"id": 1, "status": "cancelled"}),',
                'expect_body={"id": 1, "state": "cancelled"}),',
                1,
            )
        ],
        "worst": "BROKEN",
        "broken": [("response_field_missing", CANCEL, "status")],
        "changed": [("field_added", "response", CANCEL, "state", None)],
    },
    {
        "id": "D4",
        "edits": [
            Edit(
                S,
                f'json={{"email": "{who}", "password": "{pw}"}},',
                f'json={{"username": "{who}", "password": "{pw}"}},',
                1,
            )
            for who, pw in (
                ("ann@example.com", "pw-ann"),
                ("ann@example.com", "wrong"),
                ("nobody@example.com", "pw-x"),
                ("ina@example.com", "pw-ina"),
            )
        ],
        "worst": "BROKEN",
        "broken": [("request_field_missing", LOGIN, "email")],
        "changed": [("field_added", "request", LOGIN, "username", None)],
    },
    {
        "id": "D5",
        "edits": [
            Edit(
                T,
                f'Step("GET", "/applications/1/runs/{n}", "eve", expect_status=200,',
                f'Step("GET", "/applications/1/runs/{n}", "eve", expect_status=403,',
                1,
            )
            for n in (1, 2)
        ],
        "worst": "BROKEN",
        "broken": [("auth_changed", RUN_GET, "as_user eve")],
        "changed": [("status_added", "", RUN_GET, "403", None)],
    },
    {
        "id": "D6",
        "edits": [
            Edit(
                S,
                'Step("GET", "/users/me", None, expect_status=401),',
                'Step("GET", "/users/me", None, expect_status=401),\n'
                '            Step("GET", "/users/2", "root", expect_status=200, '
                'expect_body={"id": 2, "email": "ann@example.com"}),',
                1,
            )
        ],
        "worst": "CHANGED",
        "broken": [],
        "changed": [("endpoint_added", "", "GET /users/{}", "", False)],
    },
    {
        "id": "D7",
        "edits": [
            Edit(
                T,
                'expect_body={"id": 1, "status": "succeeded", "exit_code": 0}),',
                'expect_body={"id": 1, "status": "succeeded", "exit_code": "0"}),',
                1,
            )
        ],
        "worst": "none",
        "broken": [],
        "changed": [],
    },
    {
        "id": "D8",
        "edits": [
            Edit(
                S,
                'json={"email": "ann@example.com", "password": "pw-ann"},',
                'json={"email": "ann@example.com", "password": "pw-ann", "otp": "000000"},',
                1,
            )
        ],
        "worst": "CHANGED",
        "broken": [],
        "changed": [("field_added", "request", LOGIN, "otp", True)],
    },
    {
        "id": "D9",
        "edits": [Edit(T, 'Step("GET", JOBS, "bob", expect_status=404),', "", 1, whole_lines=True)],
        "worst": "CHANGED",
        "broken": [],
        "changed": [("status_removed", "", "GET /applications/{}/jobs", "404", True)],
    },
    {
        "id": "D10",
        "edits": [Edit(S, '"x" * 101}', '"x" * 51}', 1)],
        "worst": "none",
        "broken": [],
        "changed": [],
    },
]
DRIFT_TITLES = {
    "D1": "endpoint removed",
    "D2": "status 201->200",
    "D3": "response field renamed",
    "D4": "request field renamed",
    "D5": "role tightened",
    "D6": "endpoint added",
    "D7": "field type (MISS)",
    "D8": "required field added (under-classified)",
    "D9": "status removed on a relied endpoint",
    "D10": "bound tightened (MISS)",
}

C2_EDIT = Edit(
    T, 'RUNS = f"{JOBS}/1/runs"', 'RUNS = f"{JOBS}/1/runs"  # job 1\'s runs in alpha\n', 1
)
C4_EDIT = Edit(
    S,
    'expect_status=201, expect_body={"id": 3, "name": "gamma"},',
    'expect_status=201, expect_body={"id": 3, "name": "gamma", "created_by": 1},',
    1,
)
D2_EDITS: list[Edit] = next(d["edits"] for d in DRIFTS if d["id"] == "D2")  # type: ignore[misc]


def c3_edit(root: Path) -> dict[str, Any]:
    """C3: lines 173-201 of spec/spec.py, the "login" Scenario then "me", swapped byte for byte."""
    p = root / S
    text = p.read_text()
    a = '    Scenario(\n        "login",\n'
    b = '    Scenario(\n        "me",\n'
    c = '    Scenario(\n        "only_the_super_admin_creates_users",\n'
    for s in (a, b, c):
        assert text.count(s) == 1, s
    i, j, k = text.index(a), text.index(b), text.index(c)
    assert text[:i].count("\n") == 172 and text[:k].count("\n") == 201 and i < j < k
    p.write_text(text[:i] + text[j:k] + text[i:j] + text[k:])
    return {"path": S, "swap": "lines 173-190 (login) and 191-201 (me)", "count": 1}


L_EDITS: dict[str, list[Edit]] = {
    "L2": [Edit(RUNS_PY, "    status_code=201,", "    status_code=200,", 1)],
    "L3": [
        Edit(
            RUNS_PY,
            "    require_membership,\n)",
            "    require_app_admin,\n    require_membership,\n)",
            1,
        ),
        Edit(
            RUNS_PY,
            "    require_membership(session, application, current_user)\n"
            "    return get_job_run_or_404(session, application, run_id)",
            "    require_app_admin(session, application, current_user)\n"
            "    return get_job_run_or_404(session, application, run_id)",
            1,
        ),
    ],
    "L4": [Edit(SCHEMAS_PY, "    exit_code: int | None\n", "", 1)],
}


# ---- the probes -----------------------------------------------------------------------------

PROBES: dict[str, Callable[[Ctx], Result]] = {}


def probe(pid: str) -> Callable[[Callable[[Ctx], Result]], Callable[[Ctx], Result]]:
    def deco(fn: Callable[[Ctx], Result]) -> Callable[[Ctx], Result]:
        PROBES[pid] = fn
        return fn

    return deco


@probe("B0")
def b0(ctx: Ctx) -> Result:
    pred = (
        "export 20 endpoints; auth public 1 / token 4 / unknown 15; error_body fastapi; statuses "
        "= R1; no 'detail'; pin exit 0; check exit 0, 0 findings, 1 gap (access_token @ login)"
    )
    clone = ctx.clone("B0")
    m = ctx.workspace("B0", clone, lock=None)
    lock_path = m.parent.parent / "ci-bot" / LOCK

    def one(_: int) -> dict[str, Any]:
        lock_path.unlink(missing_ok=True)
        e = ctx.contracts(m, "export", "scheduler")
        p = ctx.contracts(m, "pin", "ci-bot", "--json")
        c = ctx.check(m)
        return {
            "export": {"rc": e.rc, "doc": json.loads(e.out), "err": e.err},
            "pin": canon_call(p),
            "check": canon_call(c),
            "lock_sha256": sha(lock_path.read_bytes()),
        }

    runs, ident = ctx.repeat(one)
    r0 = runs[0]
    ex = r0["export"]["doc"]["export"]
    eps = ex["endpoints"]
    auth = {k: sorted(e for e, v in eps.items() if v["auth"] == k) for k in ("public", "token")}
    n_unknown = sum(1 for v in eps.values() if v["auth"] == "unknown")
    stat_mismatch = {
        k: [v["statuses"], R1_STATUSES.get(k)]
        for k, v in eps.items()
        if v["statuses"] != R1_STATUSES.get(k)
    }
    missing_r1 = sorted(set(R1_STATUSES) - set(eps))
    detail = sorted(k for k, v in eps.items() if "detail" in v["response_fields"])
    lk = link0(r0["check"])
    checks = {
        "endpoints_20": len(eps) == 20,
        "auth_public_1_login": auth["public"] == [LOGIN],
        "auth_token_4": set(auth["token"]) == B0_TOKEN and len(auth["token"]) == 4,
        "auth_unknown_15": n_unknown == 15,
        "error_body_fastapi": ex.get("error_body") == "fastapi",
        "statuses_equal_r1": not stat_mismatch and not missing_r1,
        "no_detail_field": not detail,
        "pin_exit_0": r0["pin"]["rc"] == 0,
        "check_exit_0": r0["check"]["rc"] == 0,
        "check_0_findings": lk["findings"] == [],
        "check_1_gap": gap_set(lk) == [GAP_TOKEN],
        "export_exit_0": r0["export"]["rc"] == 0,
    }
    obs = (
        f"{len(eps)} endpoints; auth public {len(auth['public'])} token {len(auth['token'])} "
        f"unknown {n_unknown}; error_body {ex.get('error_body')}; statuses==R1 "
        f"{not stat_mismatch and not missing_r1}; detail {detail or 'none'}; pin exit "
        f"{r0['pin']['rc']}; check exit {r0['check']['rc']}, {len(lk['findings'])} findings, "
        f"gaps {fmt_b(gap_set(lk))}"
    )
    return Result(
        "B0",
        pred,
        obs,
        ident and all(checks.values()),
        runs,
        ident,
        checks,
        {
            "statuses_mismatch": stat_mismatch,
            "missing_vs_r1": missing_r1,
            "auth_token": auth["token"],
            "git_ref": r0["export"]["doc"]["version"],
        },
    )


def drift_probe(ctx: Ctx, spec: dict[str, Any]) -> Result:
    pid = spec["id"]
    worst, broken, changed = spec["worst"], sorted(spec["broken"]), spec["changed"]
    label = worst if worst != "none" else "MISS (0 findings, spec_hash equal, exit 0)"
    pred = (
        f"{DRIFT_TITLES[pid]}: {label}; BROKEN {fmt_b(broken)}; CHANGED {fmt_c(sorted(changed))};"
        " version_changed true; gaps unchanged"
    )
    clone = ctx.clone(pid, spec["edits"])
    edits = ctx.last_edits
    m = ctx.workspace(pid, clone)
    runs, ident = ctx.repeat(lambda _: canon_call(ctx.check(m)))
    r0 = runs[0]
    lk = link0(r0)
    got_b, got_c = broken_set(lk), changed_set(lk)
    got_worst = "BROKEN" if got_b else ("CHANGED" if got_c else "none")
    want_exit = 1 if worst != "none" else 0

    def changed_ok() -> bool:
        if len(got_c) != len(changed):
            return False
        want = sorted(changed, key=lambda t: t[:4])
        for g, w in zip(sorted(got_c, key=lambda t: t[:4]), want, strict=True):
            if g[:4] != w[:4] or (w[4] is not None and g[4] != w[4]):
                return False
        return True

    checks = {
        "worst_class": got_worst == worst,
        "broken_set": got_b == broken,
        "changed_set": changed_ok(),
        "exit": r0["rc"] == want_exit,
        "version_changed": lk["version_changed"] is True,
        "gaps_unchanged": lk["gaps"] == ctx.clean_gaps(),
        "spec_hash_equal_iff_no_diff": lk["spec_hash_changed"] is (worst != "none"),
    }
    if worst == "none":
        checks["no_findings"] = lk["findings"] == []
    core = ident and checks["worst_class"] and checks["broken_set"]
    obs = (
        f"{got_worst}; BROKEN {fmt_b(got_b)}; CHANGED {fmt_c(got_c)}; exit {r0['rc']}; "
        f"version_changed {lk['version_changed']}; spec_hash_changed {lk['spec_hash_changed']}; "
        f"gaps {fmt_b(gap_set(lk))}"
    )
    res = Result(
        pid,
        pred,
        obs,
        ident and all(checks.values()),
        runs,
        ident,
        checks,
        {"edits": edits, "pass_core": core, "git_ref": lk["current"]["git_ref"]},
    )
    return res


for _d in DRIFTS:
    PROBES[_d["id"]] = lambda ctx, _s=_d: drift_probe(ctx, _s)  # type: ignore[misc]


def control_result(
    pid: str,
    pred: str,
    ctx: Ctx,
    runs: list[Any],
    ident: bool,
    checks: dict[str, Any],
    obs: str,
    extra: dict[str, Any],
) -> Result:
    return Result(pid, pred, obs, ident and all(checks.values()), runs, ident, checks, extra)


@probe("C1")
def c1(ctx: Ctx) -> Result:
    pred = "exit 0, 0 findings, 1 gap, version_changed false"
    clone = ctx.clone("C1")
    m = ctx.workspace("C1", clone)
    runs, ident = ctx.repeat(lambda _: canon_call(ctx.check(m)))
    r0, lk = runs[0], link0(runs[0])
    checks = {
        "exit_0": r0["rc"] == 0,
        "0_findings": lk["findings"] == [],
        "1_gap": len(lk["gaps"]) == 1,
        "version_changed_false": lk["version_changed"] is False,
    }
    obs = (
        f"exit {r0['rc']}, {len(lk['findings'])} findings, {len(lk['gaps'])} gap(s), "
        f"version_changed {lk['version_changed']}"
    )
    return control_result("C1", pred, ctx, runs, ident, checks, obs, {})


@probe("C2")
def c2(ctx: Ctx) -> Result:
    pred = "tickets.py sha256 changes; spec_hash equal; exit 0, 0 findings; version_changed true"
    before = sha((ctx.prepare_base() / T).read_bytes())
    clone = ctx.clone("C2", [C2_EDIT])
    after = sha((clone / T).read_bytes())
    m = ctx.workspace("C2", clone)
    runs, ident = ctx.repeat(lambda _: canon_call(ctx.check(m)))
    r0, lk = runs[0], link0(runs[0])
    checks = {
        "file_sha_changes": before != after,
        "spec_hash_equal": lk["spec_hash_changed"] is False
        and lk["current"]["spec_hash"] == lk["pinned"]["spec_hash"],
        "exit_0": r0["rc"] == 0,
        "0_findings": lk["findings"] == [],
        "version_changed_true": lk["version_changed"] is True,
    }
    obs = (
        f"file sha256 changed {before != after}; spec_hash_changed {lk['spec_hash_changed']}; "
        f"exit {r0['rc']}, {len(lk['findings'])} findings; version_changed "
        f"{lk['version_changed']}"
    )
    extra = {"sha256_before": before, "sha256_after": after, "edits": ctx.last_edits}
    return control_result("C2", pred, ctx, runs, ident, checks, obs, extra)


@probe("C3")
def c3(ctx: Ctx) -> Result:
    pred = "spec_hash equal; exit 0, 0 findings"
    clone = ctx.clone("C3")
    edit = c3_edit(clone)
    ctx.git(clone, "add", S)
    ctx.git(clone, "commit", "-q", "-m", "probe C3")
    m = ctx.workspace("C3", clone)
    runs, ident = ctx.repeat(lambda _: canon_call(ctx.check(m)))
    r0, lk = runs[0], link0(runs[0])
    checks = {
        "spec_hash_equal": lk["spec_hash_changed"] is False,
        "exit_0": r0["rc"] == 0,
        "0_findings": lk["findings"] == [],
    }
    obs = (
        f"spec_hash_changed {lk['spec_hash_changed']}; exit {r0['rc']}, "
        f"{len(lk['findings'])} findings"
    )
    return control_result("C3", pred, ctx, runs, ident, checks, obs, {"edits": [edit]})


@probe("C4")
def c4(ctx: Ctx) -> Result:
    pred = "exit 1; exactly 1 CHANGED field_added response created_by @ POST /applications, unrelied; 0 BROKEN"  # noqa: E501
    clone = ctx.clone("C4", [C4_EDIT])
    m = ctx.workspace("C4", clone)
    runs, ident = ctx.repeat(lambda _: canon_call(ctx.check(m)))
    r0, lk = runs[0], link0(runs[0])
    want = [("field_added", "response", "POST /applications", "created_by", False)]
    checks = {
        "exit_1": r0["rc"] == 1,
        "changed_exactly": changed_set(lk) == want,
        "0_broken": broken_set(lk) == [],
        "findings_total_1": len(lk["findings"]) == 1,
    }
    obs = f"exit {r0['rc']}; CHANGED {fmt_c(changed_set(lk))}; BROKEN {fmt_b(broken_set(lk))}"
    return control_result("C4", pred, ctx, runs, ident, checks, obs, {"edits": ctx.last_edits})


# ---- live ---------------------------------------------------------------------------------


def live_probe(ctx: Ctx, pid: str, edits: list[Edit] | None) -> Result:
    if pid == "L1":
        pred = (
            "static 0 findings; live 0 findings, 7/7 imports replayed, access_token verified_live"
        )
    else:
        want = {
            "L2": f"BROKEN status_not_observed 201 @ {RUN_EP} (answered 200)",
            "L3": f"BROKEN auth_changed as_user eve @ {RUN_GET} (answered 403)",
            "L4": f"BROKEN response_field_missing exit_code @ {RUN_GET}",
        }[pid]
        pred = f"static 0 findings, exit 0; live exactly 1 {want}"
    clone = ctx.clone(pid, edits)
    m = ctx.workspace(pid, clone, contract="live")

    def one(_: int) -> dict[str, Any]:
        s = ctx.check(m, "--static-only")
        f = ctx.check(m)
        return {"static_only": canon_call(s), "full": canon_call(f)}

    runs, ident = ctx.repeat(one)
    r0 = runs[0]
    st, full = link0(r0["static_only"]), link0(r0["full"])
    live = full["live"] or {}
    lf = [(f["kind"], f["endpoint"], norm_item(f["item"])) for f in live.get("findings", [])]
    answered = {r["endpoint"]: r["answered"] for r in live.get("replays", [])}
    checks: dict[str, Any] = {
        "static_0_findings": st["findings"] == [] and full["findings"] == [],
        "static_exit_0": r0["static_only"]["rc"] == 0,
        "live_ran": live.get("status") == "ok",
    }
    if pid == "L1":
        checks |= {
            "live_0_findings": lf == [],
            "replayed_7_of_7": live.get("replayed") == 7 and live.get("not_replayed") == [],
            "access_token_verified_live": any(
                (v["kind"], v["endpoint"], v["item"]) == GAP_TOKEN
                for v in live.get("verified_live", [])
            ),
            "full_exit_0": r0["full"]["rc"] == 0,
        }
    else:
        item_ep = {
            "L2": ("status_not_observed", RUN_EP, "201", 200),
            "L3": ("auth_changed", RUN_GET, "as_user eve", 403),
            "L4": ("response_field_missing", RUN_GET, "exit_code", None),
        }[pid]
        checks |= {
            "live_exactly_1_broken": lf == [item_ep[:3]]
            and all(f["class"] == "BROKEN" for f in live.get("findings", [])),
            "full_exit_1": r0["full"]["rc"] == 1,
        }
        if item_ep[3] is not None:
            checks["answered"] = answered.get(item_ep[1]) == item_ep[3]
    obs = (
        f"static findings {len(st['findings'])} (exit {r0['static_only']['rc']}); live "
        f"{live.get('status')}, replayed {live.get('replayed')}, findings {fmt_b(lf)}; "
        f"answered {answered.get(RUN_EP if pid == 'L2' else RUN_GET)}; verified_live "
        f"{len(live.get('verified_live', []))}; full exit {r0['full']['rc']}"
    )
    extra = {
        "edits": ctx.last_edits,
        "git_ref": full["current"]["git_ref"],
        "dirty": full["current"]["dirty"],
    }
    return control_result(pid, pred, ctx, runs, ident, checks, obs, extra)


for _p in ("L1", "L2", "L3", "L4"):
    PROBES[_p] = lambda ctx, _p=_p: live_probe(ctx, _p, L_EDITS.get(_p))  # type: ignore[misc]


class Listener:
    """A loopback TCP listener that counts connections (evidence for 'no request sent')."""

    def __init__(self, port: int) -> None:
        self.count = 0
        self.ok = False
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind(("127.0.0.1", port))
            self.sock.listen(8)
            self.sock.settimeout(0.2)
            self.ok = True
        except OSError:
            return
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        while not self.stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except (TimeoutError, OSError):
                continue
            self.count += 1
            conn.close()

    def close(self) -> None:
        if self.ok:
            self.stop.set()
            self.thread.join(2)
        self.sock.close()


@probe("L5")
def l5(ctx: Ctx) -> Result:
    cases = [
        ("http://10.0.0.1:8000", "not_loopback"),
        ("http://example.com", "not_loopback"),
        ("http://0.0.0.0:8000", "not_loopback"),
        ("http://127.0.0.1:8000", "live_url_unsupported"),
    ]
    pred = "exit 2 not_loopback for 10.0.0.1, example.com, 0.0.0.0; 127.0.0.1:8000 exit 2 live_url_unsupported; no request sent"  # noqa: E501
    clone = ctx.clone("L5")
    ms = [
        ctx.workspace("L5", clone, contract="live", live_url=u, variant=f"u{i}")
        for i, (u, _) in enumerate(cases)
    ]
    listener = Listener(8000)
    try:

        def one(_: int) -> dict[str, Any]:
            recs = []
            for (url, _code), m in zip(cases, ms, strict=True):
                c = ctx.check(m)
                recs.append({"live_url": url, **canon_call(c)})
            return {"cases": recs}

        runs, ident = ctx.repeat(one)
        connections = listener.count
        listener_ok = listener.ok
    finally:
        listener.close()
    r0 = runs[0]
    got = []
    for (url, code), rec in zip(cases, r0["cases"], strict=True):
        got.append((url, rec["rc"], codes_in(rec["doc"]), code))
    checks = {
        "four_of_four": all(rc == 2 and codes == [code] for _u, rc, codes, code in got),
        "no_connection_to_127.0.0.1:8000": connections == 0,
    }
    obs = "; ".join(f"{u} exit {rc} {','.join(c)}" for u, rc, c, _ in got) + (
        f"; listener {'on' if listener_ok else 'unavailable'}, connections {connections}"
    )
    extra = {"listener_bound": listener_ok, "connections": connections}
    return control_result("L5", pred, ctx, runs, ident, checks, obs, extra)


# ---- version -------------------------------------------------------------------------------


@probe("V1")
def v1(ctx: Ctx) -> Result:
    pred = "README-only commit: exit 0, 0 findings, version_changed true, same spec_hash, different git_ref"  # noqa: E501
    clone = ctx.clone("V1")
    readme = clone / "README.md"
    readme.write_text(readme.read_text() + "\nA probe note.\n")
    ctx.git(clone, "add", "README.md")
    ctx.git(clone, "commit", "-q", "-m", "probe V1 readme")
    m = ctx.workspace("V1", clone)
    runs, ident = ctx.repeat(lambda _: canon_call(ctx.check(m)))
    r0, lk = runs[0], link0(runs[0])
    checks = {
        "exit_0": r0["rc"] == 0,
        "0_findings": lk["findings"] == [],
        "version_changed": lk["version_changed"] is True,
        "same_spec_hash": lk["current"]["spec_hash"] == lk["pinned"]["spec_hash"],
        "different_git_ref": lk["current"]["git_ref"] != lk["pinned"]["git_ref"],
    }
    obs = (
        f"exit {r0['rc']}, {len(lk['findings'])} findings, version_changed "
        f"{lk['version_changed']}, spec_hash_changed {lk['spec_hash_changed']}, git_ref "
        f"{lk['pinned']['git_ref'][:8]} -> {lk['current']['git_ref'][:8]}"
    )
    return control_result("V1", pred, ctx, runs, ident, checks, obs, {})


@probe("V2")
def v2(ctx: Ctx) -> Result:
    pred = "no lock, or a lock without this provider: exit 2, no_pin, message names the lock path and 'lattice contracts pin'"  # noqa: E501
    clone = ctx.clone("V2")
    m_none = ctx.workspace("V2", clone, lock=None, variant="none")
    lock_path = m_none.parent.parent / "ci-bot" / LOCK
    other = json.loads(ctx.clean_lock())
    other["providers"] = {"other": other["providers"]["scheduler"]}
    m_other = ctx.workspace(
        "V2", clone, lock=(json.dumps(other, indent=1) + "\n").encode(), variant="other"
    )
    # `none` runs first (the lock file is absent), then `other` (the lock is written by workspace)
    lock_other = lock_path.read_bytes()
    lock_path.unlink()

    def one(_: int) -> dict[str, Any]:
        lock_path.unlink(missing_ok=True)
        a = canon_call(ctx.contracts(m_none, "check", "ci-bot", "--json"))
        lock_path.write_bytes(lock_other)
        b = canon_call(ctx.contracts(m_other, "check", "ci-bot", "--json"))
        lock_path.unlink()
        return {"no_lock": a, "lock_without_provider": b}

    runs, ident = ctx.repeat(one)
    r0 = runs[0]
    checks: dict[str, Any] = {}
    parts = []
    for k, rec in r0.items():
        lk = link0(rec)
        msg = (lk["error"] or {}).get("message", "")
        checks[f"{k}_exit_2"] = rec["rc"] == 2
        checks[f"{k}_no_pin"] = (lk["error"] or {}).get("code") == "no_pin"
        checks[f"{k}_names_lock_path"] = str(lock_path) in msg
        checks[f"{k}_names_pin_command"] = "lattice contracts pin" in msg
        parts.append(f"{k}: exit {rec['rc']} {(lk['error'] or {}).get('code')}")
    ctx_msg = link0(r0["no_lock"])["error"]["message"]
    return control_result(
        "V2", pred, ctx, runs, ident, checks, "; ".join(parts), {"message": ctx_msg}
    )


@probe("V3")
def v3(ctx: Ctx) -> Result:
    pred = "10 pins: locks byte-identical to each other and to the golden; spec_hash == sha256 of the embedded export"  # noqa: E501
    clone = ctx.clone("V3")
    m = ctx.workspace("V3", clone, lock=None)
    lock_path = m.parent.parent / "ci-bot" / LOCK

    def one(_: int) -> dict[str, Any]:
        lock_path.unlink(missing_ok=True)
        c = ctx.contracts(m, "pin", "ci-bot", "--json")
        data = lock_path.read_bytes()
        return {
            "pin_rc": c.rc,
            "pin": canon_call(c),
            "lock_sha256": sha(data),
            "lock_text": data.decode(),
        }

    runs, ident = ctx.repeat(one)
    doc = json.loads(runs[0]["lock_text"])
    prov = doc["providers"]["scheduler"]
    recomputed = sha(canon_json(prov["export"]).encode("ascii"))
    golden = GOLDEN.read_bytes()
    checks = {
        "all_pins_exit_0": all(r["pin_rc"] == 0 for r in runs),
        "locks_identical": len({r["lock_sha256"] for r in runs}) == 1,
        "equals_golden": runs[0]["lock_sha256"] == sha(golden),
        "spec_hash_recomputed": recomputed == prov["version"]["spec_hash"],
    }
    obs = (
        f"{len({r['lock_sha256'] for r in runs})} distinct lock(s); equals golden "
        f"{checks['equals_golden']}; spec_hash {prov['version']['spec_hash'][:12]} == "
        f"recomputed {recomputed[:12]}"
    )
    extra = {
        "lock_sha256": runs[0]["lock_sha256"],
        "golden_sha256": sha(golden),
        "recomputed_spec_hash": recomputed,
    }
    for r in runs:
        r.pop("lock_text")
    return control_result("V3", pred, ctx, runs, ident, checks, obs, extra)


@probe("V4")
def v4(ctx: Ctx) -> Result:
    pred = "after D2: pin exit 1, lock bytes unchanged; pin --force writes; next check: 201 as gap status_unobserved, not BROKEN"  # noqa: E501
    clone = ctx.clone("V4", D2_EDITS)
    m = ctx.workspace("V4", clone)
    lock_path = m.parent.parent / "ci-bot" / LOCK
    clean = ctx.clean_lock()

    def one(_: int) -> dict[str, Any]:
        lock_path.write_bytes(clean)
        p1 = ctx.contracts(m, "pin", "ci-bot", "--json")
        after1 = lock_path.read_bytes()
        p2 = ctx.contracts(m, "pin", "ci-bot", "--force", "--json")
        after2 = lock_path.read_bytes()
        c = ctx.check(m)
        return {
            "pin": canon_call(p1),
            "lock_unchanged_after_refused_pin": after1 == clean,
            "pin_force": canon_call(p2),
            "lock_written_by_force": after2 != clean and sha(after2) != sha(clean),
            "check": canon_call(c),
        }

    runs, ident = ctx.repeat(one)
    r0 = runs[0]
    lk = link0(r0["check"])
    gap = ("status_unobserved", RUN_EP, "201")
    checks = {
        "pin_exit_1": r0["pin"]["rc"] == 1,
        "lock_unchanged": r0["lock_unchanged_after_refused_pin"],
        "force_exit_0": r0["pin_force"]["rc"] == 0,
        "force_wrote": r0["lock_written_by_force"],
        "gap_status_unobserved_201": gap in gap_set(lk),
        "no_broken": broken_set(lk) == [],
    }
    obs = (
        f"pin exit {r0['pin']['rc']}, lock unchanged {r0['lock_unchanged_after_refused_pin']}; "
        f"--force exit {r0['pin_force']['rc']}, wrote {r0['lock_written_by_force']}; check "
        f"exit {r0['check']['rc']}, BROKEN {fmt_b(broken_set(lk))}, gaps {fmt_b(gap_set(lk))}"
    )
    return control_result("V4", pred, ctx, runs, ident, checks, obs, {})


@probe("V5")
def v5(ctx: Ctx) -> Result:
    pred = "uncommitted export-neutral edit (C2's text): dirty true, version_changed true, exit 0"
    clone = ctx.clone("V5", [C2_EDIT], commit=False)
    m = ctx.workspace("V5", clone)
    runs, ident = ctx.repeat(lambda _: canon_call(ctx.check(m)))
    r0, lk = runs[0], link0(runs[0])
    checks = {
        "dirty_true": lk["current"]["dirty"] is True,
        "version_changed_true": lk["version_changed"] is True,
        "exit_0": r0["rc"] == 0,
        "0_findings": lk["findings"] == [],
    }
    obs = (
        f"dirty {lk['current']['dirty']}, version_changed {lk['version_changed']}, exit "
        f"{r0['rc']}, {len(lk['findings'])} findings"
    )
    return control_result("V5", pred, ctx, runs, ident, checks, obs, {"edits": ctx.last_edits})


@probe("V6")
def v6(ctx: Ctx) -> Result:
    pred = "one hex digit of spec_hash flipped: exit 2 bad_pin"
    clone = ctx.clone("V6")
    text = ctx.clean_lock().decode()
    h = json.loads(text)["providers"]["scheduler"]["version"]["spec_hash"]
    assert text.count(h) == 1
    flipped = ("0" if h[0] != "0" else "1") + h[1:]
    m = ctx.workspace("V6", clone, lock=text.replace(h, flipped).encode())
    runs, ident = ctx.repeat(lambda _: canon_call(ctx.check(m)))
    r0, lk = runs[0], link0(runs[0])
    code = (lk["error"] or {}).get("code")
    checks = {"exit_2": r0["rc"] == 2, "bad_pin": code == "bad_pin"}
    obs = f"exit {r0['rc']} {code}; {(lk['error'] or {}).get('message', '')[:90]}"
    return control_result(
        "V6", pred, ctx, runs, ident, checks, obs, {"flipped": [h[:8], flipped[:8]]}
    )


# ---- workspace opt-in ----------------------------------------------------------------------


@probe("W1")
def w1(ctx: Ctx) -> Result:
    pred = "contract absent + spec raising: exit 0, 0 links checked; the same link 'static': exit 2 export_failed"  # noqa: E501
    clone = ctx.clone("W1")
    p = clone / T
    p.write_text(p.read_text() + '\nraise RuntimeError("not loaded")\n')
    ctx.git(clone, "add", T)
    ctx.git(clone, "commit", "-q", "-m", "probe W1 raise")
    absent = ctx.workspace("W1", clone, contract=None, variant="absent")
    static = ctx.workspace("W1", clone, contract="static", variant="static")

    def one(_: int) -> dict[str, Any]:
        a = ctx.contracts(absent, "check", "ci-bot", "--json")
        t = ctx.contracts(absent, "check", "ci-bot")
        s = ctx.contracts(static, "check", "ci-bot", "--json")
        return {
            "absent": canon_call(a),
            "absent_text": {"rc": t.rc, "out": t.out, "err": t.err},
            "static": canon_call(s),
        }

    runs, ident = ctx.repeat(one)
    r0 = runs[0]
    lk = link0(r0["static"])
    checks = {
        "absent_exit_0": r0["absent"]["rc"] == 0,
        "absent_0_links_checked": r0["absent"]["doc"]["checked"] == 0
        and "0 link(s) checked" in r0["absent_text"]["out"],
        "static_exit_2": r0["static"]["rc"] == 2,
        "static_export_failed": (lk["error"] or {}).get("code") == "export_failed",
    }
    obs = (
        f"absent: exit {r0['absent']['rc']}, checked {r0['absent']['doc']['checked']}; "
        f"static: exit {r0['static']['rc']} {(lk['error'] or {}).get('code')}"
    )
    return control_result("W1", pred, ctx, runs, ident, checks, obs, {})


@probe("W2")
def w2(ctx: Ctx) -> Result:
    pred = "'static' link: live is null although the project has app, db and login"
    clone = ctx.clone("W2")
    m = ctx.workspace("W2", clone)
    runs, ident = ctx.repeat(lambda _: canon_call(ctx.check(m)))
    r0, lk = runs[0], link0(runs[0])
    show = ctx.lattice_cli(REPO, "workspace", "show", "--json", "--workspace", str(m))
    proj = next(p for p in show.json()["projects"] if p["name"] == "scheduler")
    checks = {
        "live_null": lk["live"] is None,
        "project_has_app_db_login": all(k in proj for k in ("app", "db", "login")),
        "contract_static": lk["contract"] == "static",
    }
    obs = f"live {lk['live']}; project keys app/db/login set {checks['project_has_app_db_login']}"
    return control_result("W2", pred, ctx, runs, ident, checks, obs, {"exit": r0["rc"]})


@probe("W3")
def w3(ctx: Ctx) -> Result:
    pred = "'live' link: the static result and the live result are both present"
    clone = ctx.clone("W3")
    m = ctx.workspace("W3", clone, contract="live")
    runs, ident = ctx.repeat(lambda _: canon_call(ctx.check(m)))
    r0, lk = runs[0], link0(runs[0])
    live = lk["live"] or {}
    checks = {
        "static_part": all(k in lk for k in ("pinned", "current", "findings", "gaps"))
        and lk["pinned"] is not None
        and lk["current"] is not None,
        "live_part": live.get("status") == "ok" and live.get("replayed") == 7,
        "contract_live": lk["contract"] == "live",
    }
    obs = (
        f"static: pinned/current/findings/gaps present {checks['static_part']}; live "
        f"{live.get('status')}, replayed {live.get('replayed')}; exit {r0['rc']}"
    )
    return control_result("W3", pred, ctx, runs, ident, checks, obs, {})


@probe("W4")
def w4(ctx: Ctx) -> Result:
    pred = "contract = 'yes' and = true: workspace validate exit 1 with bad_contract (each); contracts check exit 2"  # noqa: E501
    clone = ctx.clone("W4")
    variants = {"yes": "yes", "true": True}
    ms = {
        k: ctx.workspace("W4", clone, contract=v, variant=f"c-{k}")  # type: ignore[arg-type]
        for k, v in variants.items()
    }

    def one(_: int) -> dict[str, Any]:
        rec = {}
        for k, m in ms.items():
            v = ctx.lattice_cli(REPO, "workspace", "validate", "--json", "--workspace", str(m))
            c = ctx.contracts(m, "check", "ci-bot", "--json")
            rec[k] = {"validate": canon_call(v), "check": canon_call(c)}
        return rec

    runs, ident = ctx.repeat(one)
    checks: dict[str, Any] = {}
    parts = []
    for k, rec in runs[0].items():
        codes = [p["code"] for p in rec["validate"]["doc"]["problems"]]
        checks[f"{k}_validate_exit_1"] = rec["validate"]["rc"] == 1
        checks[f"{k}_bad_contract"] = codes == ["bad_contract"]
        checks[f"{k}_check_exit_2"] = rec["check"]["rc"] == 2
        parts.append(
            f"{k}: validate exit {rec['validate']['rc']} {codes}, check exit {rec['check']['rc']}"
        )
    return control_result("W4", pred, ctx, runs, ident, checks, "; ".join(parts), {})


GOOD_MANIFEST = """[workspace]
name = "shop"

[[project]]
name = "billing-api"
path = "billing"
role = "provider"

[[project]]
name = "web-app"
path = "web"
role = "consumer"

[[project]]
name = "shared-auth"
path = "auth"
role = "library"

[[link]]
from = "web-app"
to = "billing-api"
kind = "uses"
note = "POST /invoices"

[[link]]
from = "web-app"
to = "shared-auth"
kind = "depends_on"

[[link]]
from = "billing-api"
to = "shared-auth"
kind = "depends_on"
"""

W5_COMMANDS: list[list[str]] = [
    ["show"],
    ["show", "--json"],
    ["validate"],
    ["validate", "--json"],
    ["impact", "shared-auth"],
    ["impact", "shared-auth", "--json"],
]


def old_checkouts() -> dict[str, dict[str, Any]]:
    """The old code references and what was confirmed about each (HEAD, workspace.py clean)."""
    out: dict[str, dict[str, Any]] = {}
    for name, path in (("primary", OLD_PRIMARY), ("base", OLD_BASE)):
        info: dict[str, Any] = {"path": str(path)}
        try:
            head = subprocess.run(
                ["git", "-C", str(path), "rev-parse", "HEAD"],
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            st = subprocess.run(
                ["git", "-C", str(path), "status", "--porcelain", "--", "lattice/workspace.py"],
                text=True,
                capture_output=True,
                check=True,
            ).stdout
            br = subprocess.run(
                ["git", "-C", str(path), "branch", "--show-current"], text=True, capture_output=True
            ).stdout.strip()
            info |= {
                "head": head,
                "branch": br or "(detached)",
                "workspace_py_status": st,
                "workspace_py_exists": (path / "lattice" / "workspace.py").exists(),
            }
            info["usable"] = head.startswith(OLD_HEAD) and st == "" and info["workspace_py_exists"]
        except Exception as e:  # noqa: BLE001
            info |= {"usable": False, "error": str(e)}
        out[name] = info
    return out


def which_code(ctx: Ctx, root: Path) -> dict[str, Any]:
    """Which `lattice.workspace` a child process imports under PYTHONPATH=root, and whether the
    new `lattice.contracts` package exists there (it must not in the old code)."""
    env = dict(ctx.env)
    env["PYTHONPATH"] = str(root)
    code = (
        "import importlib.util as u, lattice.workspace as w\n"
        "print(w.__file__)\nprint(u.find_spec('lattice.contracts') is not None)\n"
    )
    c = ctx.run([sys.executable, "-c", code], env=env)
    lines = c.out.split()
    return {"workspace_py": lines[0] if lines else c.err, "has_contracts": lines[-1] == "True"}


@probe("W5")
def w5(ctx: Ctx) -> Result:
    pred = "show, show --json, validate, validate --json, impact shared-auth (+ --json): byte-identical (6 of 6) to the old code (c0cdefc)"  # noqa: E501
    d = ctx.work / "W5"
    for x in ("billing", "web", "auth"):
        (d / x).mkdir(parents=True, exist_ok=True)
    m = d / MANIFEST
    m.write_text(GOOD_MANIFEST)
    olds = old_checkouts()
    usable = {k: Path(v["path"]) for k, v in olds.items() if v["usable"]}

    def one(_: int) -> dict[str, Any]:
        rec: dict[str, Any] = {}
        for cmd in W5_COMMANDS:
            argv = ["workspace", "--workspace", str(m), *cmd]
            row: dict[str, Any] = {}
            for name, root in {"new": REPO, **{f"old_{k}": v for k, v in usable.items()}}.items():
                c = ctx.lattice_cli(root, *argv)
                row[name] = {"rc": c.rc, "out": c.out, "err": c.err}
            rec[" ".join(cmd)] = row
        return rec

    runs, ident = ctx.repeat(one)
    equal = {}
    for row in runs[0].values():
        for name in row:
            if name != "new":
                equal.setdefault(name, []).append(row["new"] == row[name])
    which = {"new": which_code(ctx, REPO), **{k: which_code(ctx, v) for k, v in usable.items()}}
    checks = {
        "old_reference_usable": bool(usable),
        "primary_old_usable": "primary" in usable,
        "new_is_new": which["new"]["has_contracts"]
        and which["new"]["workspace_py"].startswith(str(REPO)),
        "old_is_old": all(
            not w["has_contracts"] and w["workspace_py"].startswith(str(usable[k]))
            for k, w in which.items()
            if k != "new"
        ),
        **{f"{name}_6_of_6_identical": len(v) == 6 and all(v) for name, v in equal.items()},
    }
    obs = "; ".join(f"{n}: {sum(v)}/6 byte-identical" for n, v in equal.items()) or "no old code"
    obs += "; exits " + ",".join(str(r["new"]["rc"]) for r in runs[0].values())
    return control_result(
        "W5", pred, ctx, runs, ident, checks, obs, {"old_checkouts": olds, "imports": which}
    )


@probe("W6")
def w6(ctx: Ctx) -> Result:
    pred = "(reported) the old code validates a manifest with contract keys: exit 0, no problems"
    d = ctx.work / "W6"
    for x in ("billing", "web", "auth"):
        (d / x).mkdir(parents=True, exist_ok=True)
    m = d / MANIFEST
    text = GOOD_MANIFEST.replace(
        'kind = "uses"\nnote', 'kind = "uses"\ncontract = "static"\nnote'
    ).replace(
        'kind = "depends_on"\n\n[[link]]\nfrom = "billing-api"',
        'kind = "depends_on"\ncontract = "live"\n\n[[link]]\nfrom = "billing-api"',
    )
    assert text.count("contract =") == 2
    m.write_text(text)
    olds = old_checkouts()
    usable = {k: Path(v["path"]) for k, v in olds.items() if v["usable"]}

    def one(_: int) -> dict[str, Any]:
        rec = {}
        for name, root in usable.items():
            v = ctx.lattice_cli(root, "workspace", "validate", "--json", "--workspace", str(m))
            t = ctx.lattice_cli(root, "workspace", "validate", "--workspace", str(m))
            rec[name] = {"json": canon_call(v), "text": {"rc": t.rc, "out": t.out, "err": t.err}}
        return rec

    runs, ident = ctx.repeat(one)
    which = {k: which_code(ctx, v) for k, v in usable.items()}
    checks = {
        "old_reference_usable": bool(usable),
        "old_is_old": all(
            not w["has_contracts"] and w["workspace_py"].startswith(str(usable[k]))
            for k, w in which.items()
        ),
    }
    parts = []
    for name, rec in runs[0].items():
        doc = rec["json"]["doc"]
        checks[f"{name}_exit_0"] = rec["json"]["rc"] == 0 and rec["text"]["rc"] == 0
        checks[f"{name}_no_problems"] = doc["problems"] == [] and doc["ok"] is True
        parts.append(f"{name}: exit {rec['json']['rc']}, problems {len(doc['problems'])}")
    return control_result(
        "W6",
        pred,
        ctx,
        runs,
        ident,
        checks,
        "; ".join(parts),
        {"old_checkouts": olds, "imports": which},
    )


# ---- parity, safety, cost -------------------------------------------------------------------


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port: int = s.getsockname()[1]
    s.close()
    return port


def mcp_contract_check(ctx: Ctx, solo: Path, manifest: Path) -> dict[str, Any]:
    msgs = [
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "probes", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "contract_check", "arguments": {}},
        },
    ]
    c = ctx.run(
        [
            sys.executable,
            "-m",
            "lattice.mcp",
            "serve",
            "--project",
            str(solo),
            "--workspace",
            str(manifest),
            "--readonly",
        ],
        stdin="\n".join(json.dumps(x) for x in msgs) + "\n",
        timeout=300,
    )
    resp = {
        x["id"]: x for x in (json.loads(ln) for ln in c.out.splitlines() if ln.strip()) if "id" in x
    }
    res = resp[2]["result"]
    assert not res.get("isError"), res
    names = [t["name"] for t in resp[1]["result"]["tools"]]
    return {
        "doc": json.loads(res["content"][0]["text"]),
        "tools_have_contract_check": "contract_check" in names,
    }


def http_get(url: str, timeout: float = 120) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - loopback only
        return json.loads(r.read())


@contextlib.contextmanager
def background(ctx: Ctx, argv: list[str], ready_url: str) -> Any:
    log = (ctx.root / f"bg-{abs(hash(tuple(argv)))}.log").open("w")
    p = subprocess.Popen(
        argv, cwd=ctx.work, env=ctx.env, stdout=log, stderr=subprocess.STDOUT, text=True
    )
    try:
        for _ in range(300):
            if p.poll() is not None:
                raise RuntimeError(f"{argv[:3]} exited {p.returncode}")
            try:
                urllib.request.urlopen(ready_url, timeout=2).close()  # noqa: S310
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError(f"{argv[:3]} did not come up")
        yield p
    finally:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(10)
        log.close()


def solo_project(root: Path) -> Path:
    from lattice.spec.rules import Trace, extract_rules

    root.mkdir(parents=True, exist_ok=True)
    md = "# Probe project\n\n- A request without a token answers 401.\n"
    (root / "REQUIREMENTS.md").write_text(md)
    (root / "spec.py").write_text(
        "from lattice.profile import ApiProfile, Profile\n"
        "from lattice.spec import Scenario, Spec, Step\n\n"
        "SPEC = Spec(\n"
        "    Profile(schemas={}, api=ApiProfile(tables={}, table_names={},\n"
        '                                       roles=frozenset({"Public"}),\n'
        '                                       error_body="fastapi")),\n'
        '    scenarios=(Scenario("no_token", steps=(Step("GET", "/x", None,\n'
        "                                            expect_status=401),)),),\n"
        '    users={"ann": ("ann@x.io", "pw")},\n'
        ")\n"
    )
    trace = Trace("REQUIREMENTS.md", extract_rules(md), {"R1": ["scenario:no_token"]})
    (root / "spec.trace.json").write_text(trace.to_json())
    (root / "studio.toml").write_text('[llm]\nmodel = "scripted"\n')
    return root


def types_of(d: dict[str, Any]) -> dict[str, str]:
    return {k: type(v).__name__ for k, v in d.items()}


def shape_diff(mock: dict[str, Any], real: dict[str, Any]) -> list[str]:
    """Differences between the mock's answer and a real one in keys and types (not values)."""
    bad = []
    if set(mock) != set(real):
        bad.append(f"top-level keys {sorted(set(mock) ^ set(real))}")
    if types_of(mock) != types_of(real):
        bad.append("top-level types")
    real_links = real["links"]
    for row in mock["links"]:
        if real_links and set(row) != set(real_links[0]):
            bad.append(f"link keys {sorted(set(row) ^ set(real_links[0]))}")
        if real_links and row["contract"] == real_links[0]["contract"]:
            want = types_of(real_links[0])
            got = types_of(row)
            if row["live"] is None or real_links[0]["live"] is None:
                if got != want:
                    bad.append("link types")
    mock_f = {f["class"]: set(f) for lk in mock["links"] for f in lk["findings"]}
    real_f = {f["class"]: set(f) for lk in real_links for f in lk["findings"]}
    for cls in set(mock_f) & set(real_f):
        if mock_f[cls] != real_f[cls]:
            bad.append(f"{cls} finding keys")
    gm = [set(g) for lk in mock["links"] for g in lk["gaps"]]
    gr = [set(g) for lk in real_links for g in lk["gaps"]]
    if gm and gr and gm[0] != gr[0]:
        bad.append("gap keys")
    return bad


@probe("A1")
def a1(ctx: Ctx) -> Result:
    pred = "on B0 and D2, MCP contract_check and GET /api/contracts == `contracts check --static-only --json` (4 of 4); mock has the same keys and types"  # noqa: E501
    solo = solo_project(ctx.work / "A1-solo")
    targets = {
        "B0": ctx.workspace("A1-B0", ctx.clone("A1-B0")),
        "D2": ctx.workspace("A1-D2", ctx.clone("A1-D2", D2_EDITS)),
    }
    live_manifest = ctx.workspace("A1-live", ctx.clone("A1-live"), contract="live")
    mock_port = free_port()
    mock_argv = [sys.executable, "-m", "lattice.studio.mock_server", "--port", str(mock_port)]

    def api(manifest: Path) -> dict[str, Any]:
        port = free_port()
        argv = [
            sys.executable,
            "-m",
            "lattice.studio",
            str(solo),
            "--port",
            str(port),
            "--quiet",
            "--stub-llm",
            "--workspace",
            str(manifest),
        ]
        with background(ctx, argv, f"http://127.0.0.1:{port}/api/projects"):
            doc: dict[str, Any] = http_get(f"http://127.0.0.1:{port}/api/contracts")
            return doc

    with background(ctx, mock_argv, f"http://127.0.0.1:{mock_port}/api/projects"):

        def one(_: int) -> dict[str, Any]:
            rec: dict[str, Any] = {}
            mock = http_get(f"http://127.0.0.1:{mock_port}/api/contracts")
            for name, m in targets.items():
                cli = canon_call(ctx.contracts(m, "check", "--static-only", "--json"))
                mcp = mcp_contract_check(ctx, solo, m)
                srv = api(m)
                rec[name] = {
                    "cli": cli,
                    "mcp": strip_elapsed(mcp["doc"]),
                    "api": strip_elapsed(srv),
                    "mcp_lists_tool": mcp["tools_have_contract_check"],
                    "mock_shape_diff": shape_diff(mock, srv),
                }
            # informational: a live link; the only allowed difference is its reason
            cli = canon_call(ctx.contracts(live_manifest, "check", "--static-only", "--json"))
            mcp = mcp_contract_check(ctx, solo, live_manifest)
            srv = api(live_manifest)
            rec["live_link"] = {
                "cli": cli,
                "mcp": strip_elapsed(mcp["doc"]),
                "api": strip_elapsed(srv),
            }
            return rec

        runs, ident = ctx.repeat(one)
    r0 = runs[0]
    checks: dict[str, Any] = {}
    parts = []
    for name in targets:
        t = r0[name]
        checks[f"{name}_mcp_equals_cli"] = t["mcp"] == t["cli"]["doc"]
        checks[f"{name}_api_equals_cli"] = t["api"] == t["cli"]["doc"]
        checks[f"{name}_mock_same_keys_types"] = t["mock_shape_diff"] == []
        checks[f"{name}_mcp_lists_contract_check"] = t["mcp_lists_tool"]
        parts.append(
            f"{name}: mcp==cli {checks[f'{name}_mcp_equals_cli']}, api==cli "
            f"{checks[f'{name}_api_equals_cli']}, mock shape {t['mock_shape_diff'] or 'ok'}"
        )
    ll = r0["live_link"]
    lk_cli, lk_mcp, lk_api = link0(ll["cli"]), ll["mcp"]["links"][0], ll["api"]["links"][0]
    extra = {
        "live_link_reasons": {"cli": lk_cli["live"], "mcp": lk_mcp["live"], "api": lk_api["live"]},
        "live_link_only_reason_differs": {
            "mcp": {**ll["mcp"], "links": [{**lk_mcp, "live": lk_cli["live"]}]} == ll["cli"]["doc"],
            "api": {**ll["api"], "links": [{**lk_api, "live": lk_cli["live"]}]} == ll["cli"]["doc"],
        },
    }
    parts.append(f"live link reasons {extra['live_link_reasons']}")
    return control_result("A1", pred, ctx, runs, ident, checks, "; ".join(parts), extra)


class S1State:
    """The safety record of the real job-scheduler checkout, taken before the whole run."""

    def __init__(self, ctx: Ctx) -> None:
        self.marker = ctx.root / "s1-marker"
        self.marker.write_text("start\n")
        time.sleep(1.1)  # a clear mtime gap for find -newer
        self.before = self.snapshot(ctx)

    def snapshot(self, ctx: Ctx) -> dict[str, Any]:
        # --no-optional-locks: `git status` must not even refresh the real repo's index
        head = ctx.git(PROVIDER, "rev-parse", "HEAD").strip()
        status = ctx.git(PROVIDER, "--no-optional-locks", "status", "--short").rstrip("\n")
        return {"head": head, "status": status}

    def newer(self, ctx: Ctx) -> list[str]:
        c = ctx.run(
            [
                "find",
                str(PROVIDER),
                "-newer",
                str(self.marker),
                "-not",
                "-path",
                "*/.git/*",
                "-not",
                "-path",
                "*/.venv/*",
                "-not",
                "-path",
                "*/.git",
                "-not",
                "-path",
                "*/.venv",
            ]
        )
        return sorted(ln for ln in c.out.splitlines() if ln)


S1_STATE: dict[str, S1State] = {}


@probe("S1")
def s1(ctx: Ctx) -> Result:
    pred = f"job-scheduler HEAD {REF} and ' M uv.lock' before and after the whole run; find -newer finds no file; Streak never opened"  # noqa: E501
    st = S1_STATE["s"]
    after = st.snapshot(ctx)
    newer = st.newer(ctx)
    checks = {
        "head_before": st.before["head"].startswith(REF),
        "head_after": after["head"].startswith(REF),
        "status_before": st.before["status"] == " M uv.lock",
        "status_after": after["status"] == " M uv.lock",
        "no_newer_file": newer == [],
        "identical": after == st.before,
    }
    obs = (
        f"before {st.before['head'][:7]} {st.before['status']!r}; after {after['head'][:7]} "
        f"{after['status']!r}; files newer than the start marker: {len(newer)} {newer[:5]}"
    )
    return Result(
        "S1",
        pred,
        obs,
        all(checks.values()),
        [{"before": st.before, "after": after, "newer": newer}],
        True,
        checks,
        {},
    )


@probe("T1")
def t1(ctx: Ctx) -> Result:
    pred = "static check of one link: median <= 10 s (predicted <= 3 s); live (7 imports): median <= 60 s (predicted <= 15 s)"  # noqa: E501
    clone = ctx.clone("T1")
    ms = ctx.workspace("T1", clone, variant="static")
    ml = ctx.workspace("T1", clone, contract="live", variant="live")
    static, live, el_s, el_l = [], [], [], []
    for _ in range(ctx.runs):
        c = ctx.contracts(ms, "check", "ci-bot", "--json")
        static.append(c.wall)
        el_s.append(c.json()["elapsed_s"])
        c = ctx.contracts(ml, "check", "ci-bot", "--json")
        live.append(c.wall)
        el_l.append(c.json()["elapsed_s"])
        assert c.json()["links"][0]["live"]["replayed"] == 7
    ms_, ml_ = statistics.median(static), statistics.median(live)
    checks = {
        "static_median_le_10s": ms_ <= 10,
        "live_median_le_60s": ml_ <= 60,
        "static_prediction_le_3s": ms_ <= 3,
        "live_prediction_le_15s": ml_ <= 15,
    }
    obs = (
        f"static median {ms_:.2f} s wall ({statistics.median(el_s):.2f} s elapsed_s); live "
        f"median {ml_:.2f} s wall ({statistics.median(el_l):.2f} s elapsed_s)"
    )
    extra = {
        "static_wall_s": [round(x, 3) for x in static],
        "live_wall_s": [round(x, 3) for x in live],
        "static_elapsed_s": el_s,
        "live_elapsed_s": el_l,
        "median_static_wall_s": round(ms_, 3),
        "median_live_wall_s": round(ml_, 3),
        "all_static_check_calls_median_wall_s": round(statistics.median(ctx.static_walls), 3)
        if ctx.static_walls
        else None,
        "all_static_check_calls": len(ctx.static_walls),
        "all_live_check_calls_median_wall_s": round(statistics.median(ctx.live_walls), 3)
        if ctx.live_walls
        else None,
        "all_live_check_calls": len(ctx.live_walls),
        "note": "timing is not deterministic: 'identical' is not applicable",
    }
    # the pass rule is the hard threshold; the prediction columns are reported
    passed = checks["static_median_le_10s"] and checks["live_median_le_60s"]
    return Result("T1", pred, obs, passed, [{"static": static, "live": live}], True, checks, extra)


# ---- driver ---------------------------------------------------------------------------------

ORDER = (
    ["B0"]
    + [d["id"] for d in DRIFTS]
    + [
        "C1",
        "C2",
        "C3",
        "C4",
        "L1",
        "L2",
        "L3",
        "L4",
        "L5",
        "V1",
        "V2",
        "V3",
        "V4",
        "V5",
        "V6",
        "W1",
        "W2",
        "W3",
        "W4",
        "W5",
        "W6",
        "A1",
        "S1",
        "T1",
    ]
)


def scrub(x: Any, root: Path) -> Any:
    if isinstance(x, str):
        return x.replace(str(root), "$WORK")
    if isinstance(x, list):
        return [scrub(v, root) for v in x]
    if isinstance(x, tuple):
        return [scrub(v, root) for v in x]
    if isinstance(x, dict):
        return {k: scrub(v, root) for k, v in x.items()}
    return x


def leftover_processes(ctx: Ctx) -> list[str]:
    out = subprocess.run(["ps", "-axo", "pid=,command="], text=True, capture_output=True).stdout
    mine = os.getpid()
    return [
        ln.strip()
        for ln in out.splitlines()
        if (
            str(ctx.root) in ln
            or "lattice.studio.mock_server" in ln
            or "lattice.studio " in ln
            or "lattice.mcp serve" in ln
        )
        and str(mine) != ln.split()[0]
        and "ps -axo" not in ln
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--scratch", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--only", default="", help="comma-separated probe ids")
    ap.add_argument("--keep", action="store_true", help="keep the scratch clones (default: delete)")
    ns = ap.parse_args(argv)
    ids = [i for i in ns.only.split(",") if i] or ORDER
    unknown = [i for i in ids if i not in PROBES]
    if unknown:
        print(f"unknown probe ids: {unknown}", file=sys.stderr)
        return 2
    ids = [i for i in ORDER if i in ids]
    ns.out.mkdir(parents=True, exist_ok=True)
    ns.scratch.mkdir(parents=True, exist_ok=True)
    ctx = Ctx(ns.runs, ns.scratch.resolve(), ns.out)
    S1_STATE["s"] = S1State(ctx)
    t_start = time.time()
    results: list[Result] = []
    try:
        for pid in ids:
            if pid == "S1":
                continue  # last, after everything else has run
            print(f"... {pid}", file=sys.stderr, flush=True)
            res = PROBES[pid](ctx)
            results.append(res)
            write_probe(ctx, res)
        if "S1" in ids:
            res = PROBES["S1"](ctx)
            results.append(res)
            write_probe(ctx, res)
        results.sort(key=lambda r: ORDER.index(r.id))
    finally:
        procs = leftover_processes(ctx)
        tmp_left = sorted(p.name for p in ctx.tmp.iterdir()) if ctx.tmp.exists() else []
        if not ns.keep:
            shutil.rmtree(ctx.root, ignore_errors=True)
    passed = sum(1 for r in results if r.passed)
    print(f"{'id':<4} | {'predicted':<60} | {'observed':<80} | pass")
    for r in results:
        print(
            f"{r.id:<4} | {r.prediction:<60} | {r.observed:<80} | {'PASS' if r.passed else 'FAIL'}"
        )
    print(f"probes: {passed}/{len(results)} pass")
    summary = {
        "experiment": "2026-10-13-contracts",
        "command": "python dogfood/contracts/probes.py " + " ".join(sys.argv[1:]),
        "runs": ns.runs,
        "code_root": str(REPO),
        "code_head": subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True, capture_output=True
        ).stdout.strip(),
        "wall_s": round(time.time() - t_start, 1),
        "probes": [
            {
                "id": r.id,
                "prediction": r.prediction,
                "observed": r.observed,
                "pass": r.passed,
                "runs": len(r.runs),
                "identical": r.identical,
                "checks": r.checks,
            }
            for r in results
        ],
        "totals": {"pass": passed, "total": len(results)},
        "d_series": {
            "core_pass": sum(
                1 for r in results if r.id.startswith("D") and r.extra.get("pass_core")
            ),
            "full_pass": sum(1 for r in results if r.id.startswith("D") and r.passed),
        },
        "s1": {"before": S1_STATE["s"].before},
        "leftover_processes": procs,
        "leftover_tmp_entries": tmp_left,
        "scratch_removed": not ns.keep,
        "post_hoc": {
            "script_corrections": POST_HOC_SCRIPT_CORRECTIONS,
            "procedure": POST_HOC_PROCEDURE,
        },
    }
    (ns.out / "summary.json").write_text(json.dumps(scrub(summary, ctx.root), indent=1) + "\n")
    return 0 if passed == len(results) and not procs else 1


def write_probe(ctx: Ctx, res: Result) -> None:
    doc = {
        "id": res.id,
        "prediction": res.prediction,
        "observed": res.observed,
        "pass": res.passed,
        "identical": res.identical,
        "n_runs": len(res.runs),
        "checks": res.checks,
        "extra": res.extra,
        "runs": res.runs,
    }
    (ctx.out / f"{res.id}.json").write_text(json.dumps(scrub(doc, ctx.root), indent=1) + "\n")


if __name__ == "__main__":
    sys.exit(main())
