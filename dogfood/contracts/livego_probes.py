"""Probe runner for the live contract check on a Go + MySQL provider (Streak).

Implements the probes of ``docs/results/2026-10-15-live-contract-go-mysql-prereg.md`` (section 3
and Amendment 1): P0 (preconditions), G0 and the 21 drift configurations (D1, D2, D3, D4, D6, D7,
D9 in the three modes a = spec and code, b = code only, c = spec only), A1-A5 (absence and
failures), K1-K3 (leaks), Z1 (never the user's database), B1 (SQLite byte compatibility) and T
(cost). Predictions and pass rules are copied from that document; a probe that does not match is
reported as a finding with its raw output, never adapted.

    python dogfood/contracts/livego_probes.py --runs 10 --streak-ref a0679e9 \
        --scratch DIR --out docs/results/data/2026-10-15-live-go-mysql [--only G0,G-D1a,A1,...]

Run it with an interpreter that has PyMySQL (the live check runs in that interpreter), BCrypt and
httpx; the Lattice venv has no PyMySQL on purpose (probe A2 is that venv, through ``uv run``).
Every variant is a ``git clone --no-hardlinks`` scratch copy of Streak at ``build/streak``
(a0679e9) with its edits committed and ``go build`` run (``GOPROXY=off``); the real repository is
only read. No LLM and no API key are used. The Docker daemon is never started, stopped or
reconfigured and no image is ever pulled. Raw per-run outputs go to ``OUT/<ID>.json`` and
``OUT/summary.json``. The helpers (Ctx, Edit, canonical outputs) are those of ``probes.py`` and
``streak_probes.py``, imported from the files next to this one.

``--code-root DIR`` runs every check with ``DIR`` first on ``PYTHONPATH`` (the mutation run:
``--mutants LG1,...`` builds the mutated tree itself and runs the probes that the prereg names).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses look the module up by name
    spec.loader.exec_module(mod)
    return mod


base = _load("lattice_contracts_first_probes", HERE / "probes.py")
sp = _load("lattice_contracts_streak_probes", HERE / "streak_probes.py")
Edit = base.Edit
Result = base.Result
canon_call = base.canon_call
link0 = base.link0
canon_json = base.canon_json
norm_item = base.norm_item

STREAK = Path("/Users/vivekgade/Documents/streak")
STREAK_REF = "a0679e9"  # build/streak: the built app, full gate 0 findings
MAIN_REF = "a0db033"  # main: spec.py, spec.trace.json, studio.toml, harness.py, check.sh equal
CODE_BASE = "30c9120"  # np-integ before this change: the byte-compat reference
PREREG = "docs/results/2026-10-15-live-contract-go-mysql-prereg.md"
CONSUMER = sp.CONSUMER
LOGIN, ME, HOME, HABITS = sp.LOGIN, sp.ME, sp.HOME, sp.HABITS
PROGRESS, COMPLETIONS, DISMISS = sp.PROGRESS, sp.COMPLETIONS, sp.DISMISS
IMPORT_ENDPOINTS = sp.IMPORT_ENDPOINTS
TOKEN_ENDPOINTS = [e for e in IMPORT_ENDPOINTS if e != LOGIN]
GAP_TOKEN = ("response_field_unobserved", LOGIN, "access_token")
GO = shutil.which("go") or "/opt/homebrew/bin/go"
DOCKER = shutil.which("docker") or "docker"
IMAGE = "mysql:8.4"
LIVE_PREFIX = "lattice-live-"
LATTICE_VENV_PY = "/Users/vivekgade/Documents/lattice/.venv/bin/python"

POST_HOC_SCRIPT_CORRECTIONS: list[str] = [
    "classify(): in the --runs 1 dry run the six LIVE-ONLY cells (S empty, L non-empty) were "
    "classified SUPERSET, because the SUPERSET test (S strictly in L) ran before the LIVE-ONLY "
    "test and an empty set is a proper subset. A script bug: SUPERSET now needs a non-empty S "
    "(the LIVE-ONLY test runs first). The prediction table is untouched.",
    "A2 and K1 TMPDIR check: `uv run` itself leaves `uv-<hash>.lock` in the call's TMPDIR (3 "
    "calls of A2 in the dry run); that file is uv's, not the check's. It is recorded under "
    "`uv_lock_entries` and excluded from `tmp_entries` (a script artefact, found in the dry "
    "run).",
    "K3a offsets: registered as 3 s and 6 s (the prereg predicted an 8 s check). The check "
    "takes about 6.1 to 6.7 s here, so a SIGKILL at 6 s can find no child (dry run: 2 of 3). "
    "The registered probe is run unchanged and reports what it observes; K3c is a post hoc "
    "supplement that kills the child the moment its first app process has been recorded "
    "(`live-pids` non-empty), which is the case LG10 needs.",
]
POST_HOC_PROCEDURE: list[str] = [
    "Containers created by a call are counted from `docker events --since/--until` (create "
    "events whose `lattice.owner` label starts `live-`: every container of the live check, the "
    "throwaway server of a mutant that uses another prefix included), not from `docker ps` alone.",
    "K1 'processes whose command starts with a clone's bin/streak-api' is a `ps -axo` scan for "
    "command lines holding '/bin/streak-api' under the scratch root, after each call.",
    "Item notation: the code writes the auth item as 'as_user:ann'; comparisons normalise ':' "
    "after as_user to a space, nothing else.",
    "The verified_live prediction is evaluated as the rule says (login answered 2xx), which "
    "is not the sentence's 'every a and c row but D7': c-D4 predicts login 422, so it is False.",
    "Mode b static is compared with G0's (0 findings, version_changed true, spec_hash_changed "
    "false); modes a and c static BROKEN and CHANGED are compared with streak_probes.DRIFTS "
    "(the committed 2026-10-13 Dn.json sets).",
]


# ---- the configurations (prereg section 3, "Drift cases" and the class table) -----------------

BROKEN_S = {d["id"]: [(k, e, i) for k, e, i in d["broken"]] for d in sp.DRIFTS}


def _go(path: str, old: str, new: str, count: int = 1) -> Any:
    return Edit(path, old, new, count)


CODE_EDITS: dict[str, list[Any]] = {
    "D1": [
        _go(
            "internal/home/routes.go", 'r.Member.GET("/home", httpapi.Handle(h.home))', "_ = h.home"
        )
    ],  # fmt: skip
    "D2": [
        _go(
            "internal/nudges/routes.go",
            "c.JSON(http.StatusOK, db.NudgeBody(out))",
            "c.JSON(http.StatusNoContent, db.NudgeBody(out))",
        )
    ],  # fmt: skip
    "D3": [_go("internal/auth/routes.go", '`json:"expires_in"`', '`json:"expires_in_s"`')],
    "D4": [
        _go(
            "internal/auth/routes.go",
            'b.Str("password")\n\tif err != nil || !ok {\n\t\treturn '
            'httpapi.Invalid("password", "password is required")',
            'b.Str("passphrase")\n\tif err != nil || !ok {\n\t\treturn '
            'httpapi.Invalid("passphrase", "passphrase is required")',
        )
    ],  # fmt: skip
    "D6": [_go("internal/httpapi/pagination.go", '`json:"next_cursor"`', '`json:"next"`')],
    "D7": [_go("internal/httpapi/router.go", 'v1 := r.Group("/v1",', 'v1 := r.Group("/v2",')],
    "D9": [_go("internal/auth/routes.go", '`json:"expires_in"`', '`json:"expires_in,string"`')],
}
HARNESS_EDITS: dict[str, list[Any]] = {  # mode a only (the harness's own login)
    "D4": [
        _go("harness.py", '"password": f"password-{handle}"', '"passphrase": f"password-{handle}"')
    ],  # fmt: skip
    "D7": [_go("harness.py", '"/v1/auth/login"', '"/v2/auth/login"')],
}
DRIFT_IDS = ["D1", "D2", "D3", "D4", "D6", "D7", "D9"]
SPEC_EDITS = {d["id"]: d["edits"] for d in sp.DRIFTS}

# per drift and mode: L (live BROKEN), N (endpoints not replayed), class; None L means "= a"
Item = tuple[str, str, str]
ST200 = lambda ep: ("status_not_observed", ep, "200")  # noqa: E731
PRED_LIVE: dict[str, dict[str, tuple[list[Item], list[str], str]]] = {
    "D1": {
        "a": ([], [HOME], "UNREPLAYED"),
        "b": ([ST200(HOME)], [], "LIVE-ONLY"),
        "c": ([], [HOME], "UNREPLAYED"),
    },
    "D2": {
        "a": ([ST200(DISMISS), ("response_field_missing", DISMISS, "id")], [], "SUPERSET"),
        "b": ([ST200(DISMISS), ("response_field_missing", DISMISS, "id")], [], "LIVE-ONLY"),
        "c": ([], [], "DISAGREE"),
    },
    "D3": {
        "a": ([("response_field_missing", LOGIN, "expires_in")], [], "EXACT"),
        "b": ([("response_field_missing", LOGIN, "expires_in")], [], "LIVE-ONLY"),
        "c": ([], [], "DISAGREE"),
    },
    "D4": {
        "a": ([], [], "DISAGREE"),
        "b": ([ST200(LOGIN)], TOKEN_ENDPOINTS, "LIVE-ONLY"),
        "c": ([ST200(LOGIN)], [], "DISAGREE"),
    },
    "D6": {
        "a": ([("response_field_missing", HABITS, "next_cursor")], [], "EXACT"),
        "b": ([("response_field_missing", HABITS, "next_cursor")], [], "LIVE-ONLY"),
        "c": ([], [], "DISAGREE"),
    },
    "D7": {
        "a": ([], IMPORT_ENDPOINTS, "UNREPLAYED"),
        "b": ([ST200(LOGIN)], TOKEN_ENDPOINTS, "LIVE-ONLY"),
        "c": ([], IMPORT_ENDPOINTS, "UNREPLAYED"),
    },
    "D9": {"a": ([], [], "EXACT"), "b": ([], [], "EXACT"), "c": ([], [], "EXACT")},
}
# the login answered 2xx (so access_token is verified live); D7 a/c: nothing replayed
LOGIN_OK = {("D1", m): True for m in "abc"} | {("D2", m): True for m in "abc"} | {
    ("D3", m): True for m in "abc"} | {("D4", "a"): True, ("D4", "b"): False, ("D4", "c"): False,
    ("D6", "a"): True, ("D6", "b"): True, ("D6", "c"): True, ("D7", "a"): False,
    ("D7", "b"): False, ("D7", "c"): False} | {("D9", m): True for m in "abc"}  # fmt: skip


@dataclass
class Cfg:
    id: str  # "G-D1a"
    drift: str
    mode: str
    spec_edits: list[Any]
    code_edits: list[Any]
    S: list[Item]
    L: list[Item]
    N: list[str]
    klass: str
    exit: int
    containers: int
    verified: bool
    runs: int = 10


def configs(runs: int) -> list[Cfg]:
    out = []
    for d in DRIFT_IDS:
        for m in "abc":
            L, N, klass = PRED_LIVE[d][m]
            S = list(BROKEN_S[d]) if m in "ac" else []
            broken_any = bool(S or L)
            spec = list(SPEC_EDITS[d]) if m in "ac" else []
            code = list(CODE_EDITS[d]) if m in "ab" else []
            if m == "a":
                code += HARNESS_EDITS.get(d, [])
            out.append(Cfg(f"G-{d}{m}", d, m, spec, code, S, L, N, klass, 1 if broken_any else 0,
                            0 if (d == "D7" and m in "ac") else 1, LOGIN_OK[(d, m)],
                            runs if m == "a" else min(runs, 3)))  # fmt: skip
    return out


def classify(S: set[Item], L: set[Item], N: set[str]) -> str:
    """The prereg's classes: EXACT S = L; SUPERSET S strictly in L; UNREPLAYED S non-empty, L
    empty and every endpoint of S in N; LIVE-ONLY S empty, L non-empty; DISAGREE otherwise."""
    if S == L:
        return "EXACT"
    if not S and L:
        return "LIVE-ONLY"
    if S < L:  # S is non-empty here: an empty S is LIVE-ONLY (post hoc order, see the notes)
        return "SUPERSET"
    if S and not L and all(e in N for _k, e, _i in S):
        return "UNREPLAYED"
    return "DISAGREE"


# ---- context --------------------------------------------------------------------------------


def _is_uv_lock(name: str) -> bool:
    """`uv run` (probe A2) leaves its own `uv-<hash>.lock` in TMPDIR: not the check's."""
    return name.startswith("uv-") and name.endswith(".lock")


def _ps_lines() -> list[str]:
    out = subprocess.run(["ps", "-axo", "pid=,command="], text=True, capture_output=True).stdout
    return [ln.strip() for ln in out.splitlines() if "ps -axo" not in ln]


class LCtx(sp.SCtx):
    """The Streak context on build/streak (a0679e9) with a `go build` per variant and live
    calls that watch Docker, the process table and their own TMPDIR."""

    def __init__(self, runs: int, scratch: Path, out: Path, code_root: Path, ref: str) -> None:
        super().__init__(runs, scratch, out)
        self.ref = ref
        self.code_root = code_root
        self.env["PYTHONPATH"] = str(code_root)
        self.env["GOCACHE"] = str(scratch / "gocache")
        self.env["GOPROXY"] = "off"
        self.env["PATH"] = f"{Path(GO).parent}{os.pathsep}{self.env.get('PATH', '')}"
        self.env.pop("LATTICE_MYSQL_URL", None)
        self.env.pop("LATTICE_CONTAINER_OWNER", None)
        self.n_tmp = 0
        self.leaks: list[dict[str, Any]] = []  # K1: one record per live call
        self.created_total: list[str] = []
        self.builds: list[dict[str, Any]] = []
        self.live_walls: list[float] = []
        self.started: list[str] = []  # containers (any prefix) this runner started itself

    # -- clones
    def prepare_base(self) -> Path:
        if self.base is None:
            dst = self.root / "streak-clean"
            self.run(["git", "clone", "-q", "--no-hardlinks", str(STREAK), str(dst)])
            self.git(dst, "checkout", "-q", self.ref)
            head = self.git(dst, "rev-parse", "HEAD").strip()
            assert head.startswith(self.ref), head
            assert self.git(dst, "status", "--porcelain", "--untracked-files=no") == ""
            self.base = dst
        return self.base

    def build(self, clone: Path) -> None:
        t0 = time.perf_counter()
        r = self.run([GO, "build", "-o", "bin/streak-api", "./cmd/server"], cwd=clone)
        assert r.rc == 0, f"go build failed in {clone}: {r.err}"
        self.builds.append({"clone": clone.parent.name, "s": round(time.perf_counter() - t0, 2)})

    def variant(self, name: str, edits: list[Any] | None = None, build: bool = True) -> Path:
        clone = self.clone(name, edits)
        if build:
            self.build(clone)
            assert (clone / "bin" / "streak-api").is_file()
        return clone

    def live_workspace(self, name: str, clone: Path, **kw: Any) -> Path:
        return self.workspace(name, clone, contract="live", **kw)

    # -- one live call, with everything K1 looks at
    def tmpdir(self) -> Path:
        self.n_tmp += 1
        d = self.root / f"tmp-{self.n_tmp:04d}"
        d.mkdir()
        return d

    def containers_alive(self) -> list[str]:
        r = subprocess.run([DOCKER, "ps", "-a", "--filter", f"name={LIVE_PREFIX}", "--format",
                            "{{.Names}}"], text=True, capture_output=True)  # fmt: skip
        return sorted(r.stdout.split())

    def apps_alive(self) -> list[str]:
        root = str(self.root)
        return [ln for ln in _ps_lines() if "/bin/streak-api" in ln and root in ln]

    def children_alive(self) -> list[str]:
        root = str(self.root)
        return [ln for ln in _ps_lines() if "lattice.contracts.live" in ln and root in ln]

    def events_created(self, t0: float) -> list[str]:
        """Names of the containers created since t0 that carry a `lattice.owner=live-*` label."""
        r = subprocess.run(
            [DOCKER, "events", "--since", f"{t0 - 0.3:.3f}", "--until", f"{time.time():.3f}",
             "--filter", "type=container", "--filter", "event=create", "--format", "{{json .}}"],
            text=True, capture_output=True, timeout=60)  # fmt: skip
        names = []
        for ln in r.stdout.splitlines():
            try:
                attrs = json.loads(ln)["Actor"]["Attributes"]
            except (ValueError, KeyError):
                continue
            if str(attrs.get("lattice.owner", "")).startswith("live-"):
                names.append(str(attrs.get("name", "?")))
        return sorted(names)

    def record_leaks(self, label: str, tmp: Path, t0: float, wall: float) -> dict[str, Any]:
        rec = {
            "call": label,
            "containers_alive": self.containers_alive(),
            "apps_alive": self.apps_alive(),
            "tmp_entries": sorted(p.name for p in tmp.iterdir() if not _is_uv_lock(p.name))
            if tmp.exists()
            else [],
            "uv_lock_entries": sorted(p.name for p in tmp.iterdir() if _is_uv_lock(p.name))
            if tmp.exists()
            else [],
            "containers_created": self.events_created(t0),
            "wall_s": round(wall, 3),
        }
        self.created_total += rec["containers_created"]
        self.leaks.append(rec)
        return rec

    def live(
        self,
        label: str,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        path_prefix: Path | None = None,
        timeout: float = 600,
    ) -> tuple[Any, dict[str, Any]]:
        """Run `argv` once with a fresh TMPDIR; its Call and the leak record."""
        tmp = self.tmpdir()
        e = dict(env or self.env)
        e["TMPDIR"] = str(tmp)
        if path_prefix is not None:
            e["PATH"] = f"{path_prefix}{os.pathsep}{e['PATH']}"
        t0 = time.time()
        c = self.run(argv, env=e, timeout=timeout)
        rec = self.record_leaks(label, tmp, t0, c.wall)
        shutil.rmtree(tmp, ignore_errors=True)
        return c, rec

    def cli(self, manifest: Path, *args: str, label: str = "check", **kw: Any) -> tuple[Any, Any]:
        argv = [sys.executable, "-m", "lattice.contracts", "check", CONSUMER, "--json", *args,
                "--workspace", str(manifest)]  # fmt: skip
        return self.live(label, argv, **kw)

    def shim_dir(self, name: str, script: str) -> tuple[Path, Path]:
        """A directory with a `docker` shim running `script` (shell, $LOG is the log file)."""
        d = self.root / f"shim-{name}"
        d.mkdir()
        log = self.root / f"shim-{name}.log"
        log.write_text("")
        sh = d / "docker"
        sh.write_text(f'#!/bin/sh\nLOG="{log}"\n{script}\n')
        sh.chmod(0o755)
        return d, log


def summarise_call(c: Any, rec: dict[str, Any]) -> dict[str, Any]:
    """The comparable facts of one live check call (JSON)."""
    out: dict[str, Any] = {"rc": c.rc, "wall_s": round(c.wall, 3), "stderr": c.err.strip()[-300:]}
    try:
        doc = base.strip_elapsed(c.json())
    except ValueError:
        out["not_json"] = c.out[-400:]
        return out
    lk = doc["links"][0] if doc.get("links") else {}
    live = lk.get("live") or {}
    out.update(
        status=lk.get("status"),
        error=lk.get("error"),
        static_broken=sorted((f["kind"], f["endpoint"], norm_item(f["item"]))
                             for f in lk.get("findings", []) if f["class"] == "BROKEN"),
        static_changed=base.changed_set(lk) if lk else [],
        gaps=sp.gaps_ordered(lk) if lk else [],
        version_changed=lk.get("version_changed"),
        spec_hash_changed=lk.get("spec_hash_changed"),
        live_status=live.get("status"),
        live_reason=live.get("reason"),
        replayed=live.get("replayed"),
        live_broken=sorted((f["kind"], f["endpoint"], norm_item(f["item"]))
                           for f in live.get("findings", [])),
        not_replayed=sorted((n["endpoint"], n["reason"]) for n in live.get("not_replayed", [])),
        answered={r["endpoint"]: r["answered"] for r in live.get("replays", [])},
        verified_live=sorted((v["kind"], v["endpoint"], v["item"])
                             for v in live.get("verified_live", [])),
        fields_unobservable=live.get("fields_unobservable"),
        database=live.get("database"),
        live_keys=sorted(live),
    )  # fmt: skip
    out["leak"] = rec
    return out


def identical(runs: list[dict[str, Any]]) -> bool:
    """All runs equal once the timing and the per-call leak record (names, walls) are dropped."""
    keep = [{k: v for k, v in r.items() if k not in ("wall_s", "leak")} for r in runs]
    return all(k == keep[0] for k in keep)


# ---- probes ------------------------------------------------------------------------------------

PROBES: dict[str, Callable[[LCtx], Any]] = {}
ARGS: dict[str, Any] = {}


def probe(pid: str) -> Callable[[Callable[[LCtx], Any]], Callable[[LCtx], Any]]:
    def deco(fn: Callable[[LCtx], Any]) -> Callable[[LCtx], Any]:
        PROBES[pid] = fn
        return fn

    return deco


def result(
    pid: str,
    pred: str,
    runs: list[Any],
    ident: bool,
    checks: dict[str, Any],
    obs: str,
    extra: dict[str, Any] | None = None,
) -> Any:
    return Result(pid, pred, obs, ident and all(checks.values()), runs, ident, checks, extra or {})


@probe("P0")
def p0(ctx: LCtx) -> Any:
    pred = (
        "the provider clone is build/streak a0679e9; spec.py, spec.trace.json, studio.toml, "
        "harness.py and check.sh equal main a0db033; the pin on the clean clone has "
        "golden-streak's export and spec_hash d44b738522ea; go builds the clone; mysql:8.4 is local"
    )
    base_clone = ctx.prepare_base()
    head = ctx.git(base_clone, "rev-parse", "HEAD").strip()
    diff = ctx.run(
        [
            "git",
            "-C",
            str(STREAK),
            "diff",
            "--stat",
            MAIN_REF,
            STREAK_REF,
            "--",
            "spec.py",
            "spec.trace.json",
            "studio.toml",
            "harness.py",
            "check.sh",
        ]
    )
    golden = json.loads(sp.GOLDEN.read_text())["providers"][sp.PROVIDER_NAME]
    lock = json.loads(ctx.clean_lock())["providers"][sp.PROVIDER_NAME]
    clean = ctx.variant("P0", None)
    img = ctx.run([DOCKER, "image", "inspect", IMAGE, "--format", "{{.Id}}"])
    ver = ctx.run([GO, "version"])
    checks = {
        "clone_head_is_a0679e9": head.startswith(STREAK_REF),
        "five_files_equal_main": diff.rc == 0 and diff.out.strip() == "",
        "pin_export_equals_golden": lock["export"] == golden["export"],
        "pin_spec_hash_d44b738522ea": lock["version"]["spec_hash"].startswith("d44b738522ea")
        and lock["version"]["spec_hash"] == golden["version"]["spec_hash"],
        "go_build_ok": (clean / "bin" / "streak-api").is_file(),
        "image_local": img.rc == 0,
    }
    obs = (
        f"head {head[:7]}; diff vs main {diff.out.strip() or 'empty'}; spec_hash "
        f"{lock['version']['spec_hash'][:12]}; build {ctx.builds[-1]['s']} s; {ver.out.strip()}"
    )
    extra = {"streak_head": head, "go_version": ver.out.strip(), "image_id": img.out.strip(),
             "builds": ctx.builds, "diff_stat": diff.out}  # fmt: skip
    return result("P0", pred, [], True, checks, obs, extra)


@probe("G0")
def g0(ctx: LCtx) -> Any:
    pred = (
        "static exit 0, 0 findings, gaps 2 (request_field_unobserved cursor @ GET /v1/habits, "
        "response_field_unobserved access_token @ POST /v1/auth/login); live replayed 7, 201 on "
        "completions and 200 on the other 6, findings [], not_replayed [], verified_live = "
        "access_token, database block (ignored []); exactly 1 lattice-live- container created; "
        "exit 0; x10 identical"
    )
    clone = ctx.variant("G0", None)
    m = ctx.live_workspace("G0", clone)
    runs = []
    for _ in range(ctx.runs):
        c, rec = ctx.cli(m, label="G0")
        runs.append(summarise_call(c, rec))
    r0 = runs[0]
    created = [n for r in runs for n in r["leak"]["containers_created"]]
    ans = r0["answered"]
    checks = {
        "identical": identical(runs),
        "exit_0": all(r["rc"] == 0 for r in runs),
        "static_0_findings": r0["static_broken"] == [] and r0["static_changed"] == [],
        "gaps_2": r0["gaps"] == sp.B0_GAPS,
        "replayed_7": r0["replayed"] == 7 and r0["not_replayed"] == [],
        "answered_201_completions_200_others": ans.get(COMPLETIONS) == 201
        and all(ans.get(e) == 200 for e in IMPORT_ENDPOINTS if e != COMPLETIONS),
        "live_findings_empty": r0["live_broken"] == [],
        "verified_live_access_token": r0["verified_live"] == [GAP_TOKEN],
        "database_block": r0["database"] == {"engine": "mysql", "image": IMAGE, "throwaway": True,
                                             "ignored": []},  # fmt: skip
        "one_container_per_call": all(len(r["leak"]["containers_created"]) == 1 for r in runs)
        and all(n.startswith(f"{LIVE_PREFIX}mysql-") for n in created),
        "no_leaks": all(not r["leak"]["containers_alive"] and not r["leak"]["apps_alive"]
                        and not r["leak"]["tmp_entries"] for r in runs),
    }  # fmt: skip
    walls = [r["wall_s"] for r in runs]
    obs = (
        f"exit {r0['rc']}; static findings {len(r0['static_broken'])}; replayed "
        f"{r0['replayed']}; live findings {len(r0['live_broken'])}; verified "
        f"{[v[2] for v in r0['verified_live']]}; containers/call "
        f"{sorted({len(r['leak']['containers_created']) for r in runs})}; wall median "
        f"{statistics.median(walls):.1f} s (max {max(walls):.1f})"
    )
    ARGS["g0_walls"] = walls
    ARGS["g0_live"] = r0
    return result(
        "G0",
        pred,
        runs,
        identical(runs),
        checks,
        obs,
        {"wall_s": walls, "median_wall_s": round(statistics.median(walls), 2)},
    )


def run_config(ctx: LCtx, cfg: Cfg) -> Any:
    pred = (
        f"{cfg.drift} mode {cfg.mode}: static BROKEN {cfg.S or 'none'}; live BROKEN "
        f"{cfg.L or 'none'}; not replayed {len(cfg.N)} endpoint(s); class {cfg.klass}; exit "
        f"{cfg.exit}; containers {cfg.containers}; access_token verified {cfg.verified}"
    )
    clone = ctx.variant(cfg.id, [*cfg.spec_edits, *cfg.code_edits])
    m = ctx.live_workspace(cfg.id, clone)
    runs = []
    for _ in range(cfg.runs):
        c, rec = ctx.cli(m, label=cfg.id)
        runs.append(summarise_call(c, rec))
    r0 = runs[0]
    S, L = {tuple(x) for x in r0["static_broken"]}, {tuple(x) for x in r0["live_broken"]}
    N = {ep for ep, _why in r0["not_replayed"]}
    klass = classify(S, L, N)
    want_S, want_L = set(cfg.S), set(cfg.L)
    drift = next(d for d in sp.DRIFTS if d["id"] == cfg.drift)
    checks: dict[str, Any] = {
        "identical": identical(runs),
        "S_as_predicted": S == want_S,
        "L_as_predicted": L == want_L,
        "N_as_predicted": N == set(cfg.N),
        "class_as_predicted": klass == cfg.klass,
        "exit_as_predicted": all(r["rc"] == cfg.exit for r in runs),
        "containers_as_predicted": all(
            len(r["leak"]["containers_created"]) == cfg.containers for r in runs),
        "verified_as_predicted": (GAP_TOKEN in [tuple(v) for v in r0["verified_live"]])
        == cfg.verified,
        "no_leaks": all(not r["leak"]["containers_alive"] and not r["leak"]["apps_alive"]
                        and not r["leak"]["tmp_entries"] for r in runs),
    }  # fmt: skip
    if cfg.mode in "ac":  # static equals the committed 2026-10-13 sets (BROKEN and CHANGED)
        changed = drift["changed"]
        if changed == "d7":
            changed = sp.d7_changed(ctx.clean_export())
        checks["static_changed_as_committed"] = sp.changed_norm(
            [tuple(x) for x in r0["static_changed"]]
        ) == sp.changed_norm(changed)
    else:  # mode b: static is G0's
        checks["static_is_g0"] = (r0["static_broken"] == [] and r0["static_changed"] == []
                                  and r0["gaps"] == sp.B0_GAPS)  # fmt: skip
        checks["version_changed_true_spec_hash_same"] = (
            r0["version_changed"] is True and r0["spec_hash_changed"] is False
        )
    obs = (f"S {sorted(S)}; L {sorted(L)}; N {sorted(N)}; class {klass}; exit {r0['rc']}; "
           f"containers {[len(r['leak']['containers_created']) for r in runs][:3]}; replayed "
           f"{r0['replayed']}; answered {r0['answered']}; verified "
           f"{GAP_TOKEN in [tuple(v) for v in r0['verified_live']]}")  # fmt: skip
    extra = {
        "drift": cfg.drift, "mode": cfg.mode, "S": sorted(S), "L": sorted(L), "N": sorted(N),
        "class": klass, "predicted_class": cfg.klass,
        "spec_edits": [(e.path, e.old, e.new, e.count) for e in cfg.spec_edits],
        "code_edits": [(e.path, e.old, e.new, e.count) for e in cfg.code_edits],
        "wall_s": [r["wall_s"] for r in runs],
        "not_replayed_reasons": r0["not_replayed"],
    }  # fmt: skip
    return result(cfg.id, pred, runs, identical(runs), checks, obs, extra)


def _register_configs() -> None:
    for cfg in configs(10):
        PROBES[cfg.id] = lambda ctx, _id=cfg.id: run_config(
            ctx, next(c for c in configs(ctx.runs) if c.id == _id))  # fmt: skip


_register_configs()


# ---- A: absence and failures ------------------------------------------------------------------


def _absence(ctx: LCtx, pid: str, pred: str, want_code: str, make: Callable[[], dict[str, Any]],
             *, wall_max: float = 25.0, rc: int = 2, extra_checks: Callable[..., dict] | None = None
             ) -> Any:  # fmt: skip
    runs = [make() for _ in range(3)]
    r0 = runs[0]
    code = (r0.get("error") or {}).get("code")
    checks = {
        "identical": identical(runs),
        "exit": all(r["rc"] == rc for r in runs),
        "error_code": code == want_code if want_code else True,
        "findings_empty": r0["static_broken"] == [] and r0["live_broken"] == [],
        "no_streak_api_process": all(not r["leak"]["apps_alive"] for r in runs),
        "no_live_container": all(not r["leak"]["containers_alive"] for r in runs),
        "tmpdir_empty": all(not r["leak"]["tmp_entries"] for r in runs),
        "wall_le_25s": all(r["wall_s"] <= wall_max for r in runs),
    }
    if extra_checks is not None:
        checks |= extra_checks(runs)
    obs = (
        f"exit {r0['rc']}; error {code}: {(r0.get('error') or {}).get('message', '')[:140]}; "
        f"wall {[r['wall_s'] for r in runs]}; created {r0['leak']['containers_created']}"
    )
    return result(pid, pred, runs, identical(runs), checks, obs,
                  {"error": r0.get("error"), "wall_s": [r["wall_s"] for r in runs]})  # fmt: skip


def _log_lines(log: Path) -> list[str]:
    return [ln for ln in log.read_text().splitlines() if ln]


@probe("A1")
def a1(ctx: LCtx) -> Any:
    pred = (
        "a docker shim first on PATH that logs and exits 1: docker_unavailable, log = `info` "
        "only; exit 2, no streak-api, wall <= 25 s (predicted <= 3 s)"
    )
    clone = ctx.variant("A1", None)
    m = ctx.live_workspace("A1", clone)
    shim, log = ctx.shim_dir("a1", 'echo "$@" >> "$LOG"\nexit 1')

    def one() -> dict[str, Any]:
        log.write_text("")
        c, rec = ctx.cli(m, label="A1", path_prefix=shim)
        s = summarise_call(c, rec)
        s["shim_log"] = _log_lines(log)
        return s

    return _absence(ctx, "A1", pred, "docker_unavailable", one, extra_checks=lambda runs: {
        "shim_log_is_info_only": all(len(r["shim_log"]) == 1 and r["shim_log"][0].startswith(
            "info") for r in runs)})  # fmt: skip


@probe("A1p")
def a1p(ctx: LCtx) -> Any:
    pred = (
        "the real docker CLI with DOCKER_HOST=unix://<scratch>/none.sock: docker_unavailable; "
        "exit 2, wall <= 25 s"
    )
    clone = ctx.variant("A1p", None)
    m = ctx.live_workspace("A1p", clone)
    env = dict(ctx.env, DOCKER_HOST=f"unix://{ctx.root}/none.sock")

    def one() -> dict[str, Any]:
        c, rec = ctx.cli(m, label="A1p", env=env)
        return summarise_call(c, rec)

    return _absence(ctx, "A1p", pred, "docker_unavailable", one)


@probe("A2")
def a2(ctx: LCtx) -> Any:
    pred = (
        "the Lattice venv (`uv run python -m lattice.contracts`, no PyMySQL): driver_missing "
        "naming lattice[db]; 0 Docker calls (a logging shim forwarding to the real CLI)"
    )
    clone = ctx.variant("A2", None)
    m = ctx.live_workspace("A2", clone)
    shim, log = ctx.shim_dir("a2", f'echo "$@" >> "$LOG"\nexec "{DOCKER}" "$@"')
    env = dict(
        ctx.env, UV_NO_SYNC="1", UV_PROJECT_ENVIRONMENT=str(Path(LATTICE_VENV_PY).parents[1])
    )
    argv = ["uv", "run", "--project", str(ctx.code_root), "python", "-m", "lattice.contracts",
            "check", CONSUMER, "--json", "--workspace", str(m)]  # fmt: skip

    def one() -> dict[str, Any]:
        log.write_text("")
        c, rec = ctx.live("A2", argv, env=env, path_prefix=shim)
        s = summarise_call(c, rec)
        s["shim_log"] = _log_lines(log)
        s["child_python"] = None
        return s

    def extra(runs: list[dict[str, Any]]) -> dict[str, Any]:
        msg = (runs[0].get("error") or {}).get("message", "")
        return {
            "zero_docker_calls": all(r["shim_log"] == [] for r in runs),
            "message_names_lattice_db": "lattice[db]" in msg and "pymysql" in msg,
        }

    return _absence(ctx, "A2", pred, "driver_missing", one, extra_checks=extra)


@probe("A3")
def a3(ctx: LCtx) -> Any:
    pred = (
        "a shim answering `info` 0 and `image inspect` 1: docker_image_missing naming "
        "`docker pull mysql:8.4`; no `run` and no `pull` logged"
    )
    clone = ctx.variant("A3", None)
    m = ctx.live_workspace("A3", clone)
    script = ('echo "$@" >> "$LOG"\ncase "$1" in\n  info) exit 0;;\n'
              '  image) exit 1;;\nesac\nexit 1')  # fmt: skip
    shim, log = ctx.shim_dir("a3", script)

    def one() -> dict[str, Any]:
        log.write_text("")
        c, rec = ctx.cli(m, label="A3", path_prefix=shim)
        s = summarise_call(c, rec)
        s["shim_log"] = _log_lines(log)
        return s

    def extra(runs: list[dict[str, Any]]) -> dict[str, Any]:
        msg = (runs[0].get("error") or {}).get("message", "")
        return {
            "no_run_no_pull": all(
                not any(ln.split()[0] in ("run", "pull") for ln in r["shim_log"]) for r in runs
            ),
            "log_is_info_then_image_inspect": all(
                [ln.split()[0] for ln in r["shim_log"]] == ["info", "image"] for r in runs
            ),
            "message_has_docker_pull": f"docker pull {IMAGE}" in msg,
        }

    return _absence(ctx, "A3", pred, "docker_image_missing", one, extra_checks=extra)


@probe("A4")
def a4(ctx: LCtx) -> Any:
    pred = (
        "a clone without bin/: provider_start_failed, the message names bin/streak-api, its "
        "container is removed; exit 2, wall <= 25 s (predicted <= 9 s)"
    )
    clone = ctx.variant("A4", None, build=False)
    assert not (clone / "bin").exists()
    m = ctx.live_workspace("A4", clone)

    def one() -> dict[str, Any]:
        c, rec = ctx.cli(m, label="A4")
        return summarise_call(c, rec)

    def extra(runs: list[dict[str, Any]]) -> dict[str, Any]:
        msg = (runs[0].get("error") or {}).get("message", "")
        return {
            "message_names_bin_streak_api": "bin/streak-api" in msg,
            "a_container_was_started_and_removed": all(
                len(r["leak"]["containers_created"]) == 1 for r in runs
            ),
            "wall_le_9s": all(r["wall_s"] <= 9 for r in runs),
        }

    return _absence(ctx, "A4", pred, "provider_start_failed", one, extra_checks=extra)


@probe("A5")
def a5(ctx: LCtx) -> Any:
    pred = (
        "--static-only under A1's shim: exit 0, live not_run/static-only, the shim log "
        "empty; no streak-api; wall <= 25 s (static-only <= 1.5 s)"
    )
    clone = ctx.variant("A5", None)
    m = ctx.live_workspace("A5", clone)
    shim, log = ctx.shim_dir("a5", 'echo "$@" >> "$LOG"\nexit 1')

    def one() -> dict[str, Any]:
        log.write_text("")
        c, rec = ctx.cli(m, "--static-only", label="A5", path_prefix=shim)
        s = summarise_call(c, rec)
        s["shim_log"] = _log_lines(log)
        return s

    def extra(runs: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "live_not_run_static_only": all(
                r["live_status"] == "not_run" and r["live_reason"] == "static-only" for r in runs
            ),
            "shim_log_empty": all(r["shim_log"] == [] for r in runs),
            "wall_le_1_5s": all(r["wall_s"] <= 1.5 for r in runs),
        }

    return _absence(ctx, "A5", pred, "", one, rc=0, extra_checks=extra)


# ---- K: leaks ----------------------------------------------------------------------------------


@probe("K2")
def k2(ctx: LCtx) -> Any:
    pred = (
        "live.TIMEOUT_S = 2, 5, 8 s, run_check in-process (x3): 2 and 5 live_timeout, 8 "
        "recorded either way; return <= TIMEOUT_S + 20 s; 0 leaks 5 s after return"
    )
    from lattice import workspace as wsmod
    from lattice.contracts import check as ck
    from lattice.contracts import live

    clone = ctx.variant("K2", None)
    m = ctx.live_workspace("K2", clone)
    runs = []
    saved = (live.TIMEOUT_S, tempfile.tempdir, dict(os.environ))
    try:
        for secs in (2.0, 5.0, 8.0):
            for _ in range(3):
                tmp = ctx.tmpdir()
                tempfile.tempdir = str(tmp)
                os.environ.update(ctx.env)
                os.environ["TMPDIR"] = str(tmp)
                live.TIMEOUT_S = secs
                t0w, t0 = time.time(), time.monotonic()
                rep = ck.run_check(wsmod.load(m), CONSUMER)
                wall = time.monotonic() - t0
                time.sleep(5)
                lk = rep.links[0]
                runs.append(
                    {
                        "timeout_s": secs,
                        "wall_s": round(wall, 2),
                        "status": lk["status"],
                        "code": (lk["error"] or {}).get("code"),
                        "live_status": (lk.get("live") or {}).get("status"),
                        "leak": ctx.record_leaks(f"K2-{secs:g}", tmp, t0w, wall),
                    }
                )
                shutil.rmtree(tmp, ignore_errors=True)
    finally:
        live.TIMEOUT_S, tempfile.tempdir = saved[0], saved[1]
        os.environ.clear()
        os.environ.update(saved[2])
    by = {s: [r for r in runs if r["timeout_s"] == s] for s in (2.0, 5.0, 8.0)}
    checks = {
        "2s_and_5s_live_timeout": all(
            r["code"] == "live_timeout" for s in (2.0, 5.0) for r in by[s]
        ),
        "return_le_timeout_plus_20": all(r["wall_s"] <= r["timeout_s"] + 20 for r in runs),
        "no_leaks_5s_after": all(
            not r["leak"]["containers_alive"]
            and not r["leak"]["apps_alive"]
            and not ctx.children_alive()
            for r in runs
        ),
    }
    obs = "; ".join(f"{s:g} s -> {sorted({r['code'] or r['status'] for r in by[s]})} "
                    f"wall {[r['wall_s'] for r in by[s]]}" for s in by)  # fmt: skip
    return result("K2", pred, runs, True, checks, obs, {})


def _k3(ctx: LCtx, pid: str, kill: str, offsets: list[float]) -> list[dict[str, Any]]:
    clone = ctx.variant(pid, None)
    m = ctx.live_workspace(pid, clone)
    runs = []
    for off in offsets:
        for _ in range(3):
            tmp = ctx.tmpdir()
            env = dict(ctx.env, TMPDIR=str(tmp))
            argv = [sys.executable, "-m", "lattice.contracts", "check", CONSUMER, "--json",
                    "--workspace", str(m)]  # fmt: skip
            t0w, t0 = time.time(), time.monotonic()
            p = subprocess.Popen(
                argv,
                env=env,
                cwd=ctx.work,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            killed: dict[str, Any] = {"target": kill, "at_s": off, "pid": None}
            while time.monotonic() - t0 < off:
                time.sleep(0.02)
            if kill in ("child", "child_app"):
                if kill == "child_app":  # post hoc K3c: once an app process has been recorded
                    while time.monotonic() - t0 < 60 and not any(
                        f.read_text().strip() for f in tmp.glob("lattice-live-*/live-pids")
                    ):
                        time.sleep(0.005)
                    killed["at_s"] = round(time.monotonic() - t0, 2)
                pids = [
                    ln.split()[0]
                    for ln in _ps_lines()
                    if "lattice.contracts.live" in ln and str(tmp) in ln
                ]
                if pids:
                    killed["pid"] = int(pids[0])
                    os.kill(int(pids[0]), signal.SIGKILL)
            else:
                killed["pid"] = p.pid
                p.kill()
            out, err = p.communicate(timeout=300)
            wall = time.monotonic() - t0
            rec: dict[str, Any] = {
                "killed": killed,
                "rc": p.returncode,
                "wall_s": round(wall, 2),
                "stderr": err.strip()[-200:],
            }
            try:
                lk = json.loads(out)["links"][0]
                rec["error"] = lk.get("error")
                rec["status"] = lk.get("status")
            except (ValueError, KeyError, IndexError):
                rec["error"] = None
            if kill in ("child", "child_app"):
                rec["leak"] = ctx.record_leaks(f"{pid}@{off:g}", tmp, t0w, wall)
            else:  # the orphaned child finishes on its own: wait for it, <= 60 s
                dl = time.monotonic() + 60
                while time.monotonic() < dl and (
                    ctx.containers_alive() or ctx.apps_alive() or ctx.children_alive()
                ):
                    time.sleep(1)
                rec["settled_after_s"] = round(time.monotonic() - t0 - wall, 1)
                rec["leak"] = ctx.record_leaks(f"{pid}@{off:g}", tmp, t0w, wall)
                rec["children_alive"] = ctx.children_alive()
            shutil.rmtree(tmp, ignore_errors=True)
            runs.append(rec)
    return runs


@probe("K3a")
def k3a(ctx: LCtx) -> Any:
    pred = (
        "SIGKILL of the child (lattice.contracts.live) at 3 s and 6 s (x3): live_failed, 0 "
        "containers and 0 streak-api after the parent returned"
    )
    runs = _k3(ctx, "K3a", "child", [3.0, 6.0])
    checks = {
        "child_was_found_and_killed": all(r["killed"]["pid"] is not None for r in runs),
        "live_failed": all((r.get("error") or {}).get("code") == "live_failed" for r in runs),
        "no_container_left": all(not r["leak"]["containers_alive"] for r in runs),
        "no_streak_api_left": all(not r["leak"]["apps_alive"] for r in runs),
    }
    obs = "; ".join(
        f"@{r['killed']['at_s']:g}s {(r.get('error') or {}).get('code')} "
        f"containers {r['leak']['containers_alive']} apps {len(r['leak']['apps_alive'])}"
        for r in runs
    )
    return result("K3a", pred, runs, True, checks, obs, {})


@probe("K3c")
def k3c(ctx: LCtx) -> Any:
    pred = (
        "POST HOC supplement: SIGKILL of the child the moment its first app process is recorded "
        "(x3): live_failed, 0 containers and 0 streak-api after the parent returned (the case "
        "LG10 needs: an app of a killed child)"
    )
    runs = _k3(ctx, "K3c", "child_app", [0.0])
    checks = {
        "child_was_found_and_killed": all(r["killed"]["pid"] is not None for r in runs),
        "live_failed": all((r.get("error") or {}).get("code") == "live_failed" for r in runs),
        "no_container_left": all(not r["leak"]["containers_alive"] for r in runs),
        "no_streak_api_left": all(not r["leak"]["apps_alive"] for r in runs),
    }
    obs = "; ".join(
        f"@{r['killed']['at_s']:g}s {(r.get('error') or {}).get('code')} "
        f"containers {r['leak']['containers_alive']} apps {len(r['leak']['apps_alive'])}"
        for r in runs
    )
    return result("K3c", pred, runs, True, checks, obs, {})


@probe("K3b")
def k3b(ctx: LCtx) -> Any:
    pred = (
        "SIGKILL of the parent at 4 s (x3): within 60 s 0 containers and 0 processes; 1 "
        "TMPDIR left per call (the known leak, reported)"
    )
    runs = _k3(ctx, "K3b", "parent", [4.0])
    checks = {
        "parent_killed": all(r["killed"]["pid"] is not None and r["rc"] == -9 for r in runs),
        "no_container_within_60s": all(not r["leak"]["containers_alive"] for r in runs),
        "no_process_within_60s": all(
            not r["leak"]["apps_alive"] and not r["children_alive"] for r in runs
        ),
    }
    tmp_left = [r["leak"]["tmp_entries"] for r in runs]
    obs = (
        f"rc {[r['rc'] for r in runs]}; settled after {[r['settled_after_s'] for r in runs]} s; "
        f"containers {[r['leak']['containers_alive'] for r in runs]}; tmp entries left "
        f"{tmp_left} (predicted: 1 each, reported)"
    )
    return result("K3b", pred, runs, True, checks, obs,
                  {"tmp_entries_left_per_call": tmp_left, "tmp_prediction_1_each":
                   all(len(t) == 1 for t in tmp_left)})  # fmt: skip


@probe("K1")
def k1(ctx: LCtx) -> Any:
    pred = (
        "after every live call (G, A, K, Z, T): 0 lattice-live- containers, 0 streak-api "
        "processes of a clone, 0 entries in the call's TMPDIR (K3b's tmp entries excepted, "
        "the known leak)"
    )
    recs = [r for r in ctx.leaks if not r["call"].startswith("K3b")]
    bad = [r for r in recs if r["containers_alive"] or r["apps_alive"] or r["tmp_entries"]]
    checks = {"calls_counted": len(recs) > 0, "zero_leaks": not bad}
    obs = f"{len(recs)} calls checked, {len(bad)} with a leak: {[r['call'] for r in bad][:5]}"
    return result("K1", pred, [], True, checks, obs, {"calls": len(recs), "bad": bad})


# ---- Z1: never the user's database -----------------------------------------------------------


def _decoy_reads(url: str) -> dict[str, Any]:
    from lattice.spec.db import open_db

    db = open_db(url)
    try:
        conn = int(db.query("SHOW GLOBAL STATUS LIKE 'Connections'")[0][1])
        dbs = sorted(r[0] for r in db.query("SHOW DATABASES"))
        chk = [list(r) for r in db.query("CHECKSUM TABLE streak_real.sentinel")]
        rows = [list(r) for r in db.query("SELECT id, v FROM streak_real.sentinel ORDER BY id")]
        mode = db.query("SELECT @@GLOBAL.sql_mode")[0][0]
    finally:
        db.close()
    return {"connections": conn, "databases": dbs, "checksum": chk, "rows": rows,
            "global_sql_mode": mode}  # fmt: skip


@probe("Z1")
def z1(ctx: LCtx) -> Any:
    pred = (
        "a decoy mysql (database streak_real, a sentinel row) named by LATTICE_MYSQL_URL, "
        "HABIT_DB_DSN, DATABASE_URL and [app] database_url: the live result = G0's but "
        "ignored [LATTICE_MYSQL_URL, database_url]; decoy Connections +1 (the runner's own "
        "second read); SHOW DATABASES, the sentinel checksum and GLOBAL sql_mode unchanged"
    )
    from lattice.spec import dbserver
    from lattice.spec.db import url_for

    server = dbserver.start_container("mysql", name_prefix="lattice-decoy-")
    ctx.started.append(server.container or "")
    try:
        server.create("streak_real")
        admin = server.admin()
        admin.execute("CREATE TABLE streak_real.sentinel (id INT PRIMARY KEY, v VARCHAR(64))")
        admin.execute("INSERT INTO streak_real.sentinel VALUES (1, 'DO-NOT-TOUCH')")
        url = server.url_of("streak_real")
        edit = Edit(
            "studio.toml",
            'database = "mysql"\n',
            f'database = "mysql"\ndatabase_url = "{url}"\n',
            1,
        )
        clone = ctx.variant("Z1", [edit])
        m = ctx.live_workspace("Z1", clone)
        txt = m.read_text()
        env_line = (
            f'env = {{ HABIT_DB_DSN = "{url_for(url, "go-mysql")}", DATABASE_URL = "{url}" }}\n'
        )
        marker = f'path = "{clone}"\nrole = "provider"\n'
        assert txt.count(marker) == 1
        m.write_text(txt.replace(marker, marker + env_line))
        env = dict(ctx.env, LATTICE_MYSQL_URL=url)
        runs = []
        for _ in range(3):
            before = _decoy_reads(url)
            c, rec = ctx.cli(m, label="Z1", env=env)
            after = _decoy_reads(url)
            s = summarise_call(c, rec)
            s["decoy_before"], s["decoy_after"] = before, after
            runs.append(s)
    finally:
        server.stop()
    g0 = ARGS.get("g0_live")
    r0 = runs[0]
    want_db = {"engine": "mysql", "image": IMAGE, "throwaway": True,
               "ignored": ["LATTICE_MYSQL_URL", "database_url"]}  # fmt: skip
    same_as_g0 = None
    if g0 is not None:
        keys = ("replayed", "answered", "live_broken", "not_replayed", "verified_live", "rc")
        same_as_g0 = all(r0[k] == g0[k] for k in keys)
    checks = {
        "identical": identical(
            [{k: v for k, v in r.items() if not k.startswith("decoy_")} for r in runs]
        ),
        "exit_0_replayed_7": all(r["rc"] == 0 and r["replayed"] == 7 for r in runs),
        "database_block_ignored_both": all(r["database"] == want_db for r in runs),
        "result_equals_g0": same_as_g0 if same_as_g0 is not None else True,
        "connections_plus_1": all(
            r["decoy_after"]["connections"] == r["decoy_before"]["connections"] + 1 for r in runs
        ),
        "databases_unchanged": all(
            r["decoy_after"]["databases"] == r["decoy_before"]["databases"] for r in runs
        ),
        "sentinel_unchanged": all(
            r["decoy_after"]["checksum"] == r["decoy_before"]["checksum"]
            and r["decoy_after"]["rows"] == [[1, "DO-NOT-TOUCH"]]
            for r in runs
        ),
        "global_sql_mode_unchanged": all(
            r["decoy_after"]["global_sql_mode"] == r["decoy_before"]["global_sql_mode"]
            for r in runs
        ),
        "no_lattice_database_on_decoy": all(
            not any(d.startswith("lattice_") for d in r["decoy_after"]["databases"]) for r in runs
        ),
    }
    obs = (
        f"exit {r0['rc']}; replayed {r0['replayed']}; ignored "
        f"{(r0['database'] or {}).get('ignored')}; decoy connections "
        f"{r0['decoy_before']['connections']} -> {r0['decoy_after']['connections']}; "
        f"databases {r0['decoy_after']['databases']}; sql_mode unchanged "
        f"{checks['global_sql_mode_unchanged']}"
    )
    ident = checks.pop("identical")
    return result("Z1", pred, runs, ident, checks, obs, {"vs_g0_checked": g0 is not None})


# ---- B1: SQLite byte compatibility -----------------------------------------------------------


def _old_tree(root: Path) -> Path:
    dst = root / "old"
    dst.mkdir(parents=True)
    arc = subprocess.run(["git", "-C", str(REPO), "archive", CODE_BASE, "lattice"],
                         capture_output=True, check=True)  # fmt: skip
    subprocess.run(["tar", "-x", "-C", str(dst)], input=arc.stdout, check=True)
    return dst


@probe("B1")
def b1(ctx: LCtx) -> Any:
    pred = (
        "job-scheduler (ef17cf8 + ci-bot): L1-L5 and W3 through the CLI, --json and text, new "
        "code vs `git archive 30c9120 lattice`: canonical outputs byte-identical 10/10, a "
        "docker shim sees 0 calls; tests/contracts/test_live.py unchanged"
    )
    old = _old_tree(ctx.root)
    bctx = base.Ctx(ctx.runs, ctx.root / "b1", ctx.out)
    shim, log = ctx.shim_dir("b1", 'echo "$@" >> "$LOG"\nexit 127')
    bctx.env["PATH"] = f"{shim}{os.pathsep}{bctx.env['PATH']}"
    bctx.env["PYTHONPATH"] = str(ctx.code_root)
    cases: list[tuple[str, Path]] = []
    for pid in ("L1", "L2", "L3", "L4"):
        clone = bctx.clone(pid, base.L_EDITS.get(pid))
        cases.append((pid, bctx.workspace(pid, clone, contract="live")))
    clone = bctx.clone("W3")
    cases.append(("W3", bctx.workspace("W3", clone, contract="live")))
    clone = bctx.clone("L5")
    for i, url in enumerate(
        [
            "http://10.0.0.1:8000",
            "http://example.com",
            "http://0.0.0.0:8000",
            "http://127.0.0.1:8000",
        ]
    ):
        cases.append((f"L5-{i}", bctx.workspace("L5", clone, contract="live", live_url=url,
                                                variant=f"u{i}")))  # fmt: skip

    def canon(c: Any, as_json: bool) -> dict[str, Any]:
        if as_json:
            try:
                return {"rc": c.rc, "doc": base.strip_elapsed(c.json()), "err": c.err}
            except ValueError:
                pass
        return {"rc": c.rc, "out": c.out, "err": c.err}

    rows = []
    for name, m in cases:
        for as_json in (True, False):
            args = ["check", "ci-bot", *(["--json"] if as_json else [])]
            same, new_out = [], None
            for _ in range(ctx.runs):
                new = canon(bctx.contracts(m, *args), as_json)
                oldc = canon(bctx.contracts(m, *args, root=old), as_json)
                same.append(canon_json(new) == canon_json(oldc))
                new_out = new_out or new
            rows.append(
                {
                    "case": name,
                    "format": "json" if as_json else "text",
                    "identical_runs": sum(same),
                    "runs": len(same),
                    "rc": new_out["rc"],
                }
            )
    diff_cmd = ["git", "-C", str(REPO), "diff", CODE_BASE, "--", "tests/contracts/test_live.py"]
    diff = subprocess.run(diff_cmd, capture_output=True, text=True).stdout
    calls = _log_lines(log)
    checks = {
        "all_byte_identical": all(r["identical_runs"] == r["runs"] for r in rows),
        "docker_calls_0": calls == [],
        "test_live_py_unchanged": diff.strip() == "",
    }
    obs = (
        f"{sum(r['identical_runs'] == r['runs'] for r in rows)}/{len(rows)} case/format "
        f"cells identical over {ctx.runs} runs; docker calls {len(calls)}; test_live.py diff "
        f"{'empty' if not diff.strip() else 'NOT empty'}"
    )
    return result("B1", pred, rows, True, checks, obs, {"docker_calls": calls})


# ---- T: cost ----------------------------------------------------------------------------------


@probe("T")
def t_cost(ctx: LCtx) -> Any:
    pred = (
        "G0 live CLI wall: median of 10 predicted 8 s, threshold <= 30 s, max <= 60 s; a-D7 "
        "<= 3 s; Streak static-only <= 1.5 s"
    )
    walls = ARGS.get("g0_walls") or []
    d7 = ARGS.get("d7a_walls") or []
    clone = ctx.variant("T", None)
    m = ctx.live_workspace("T", clone)
    st = []
    for _ in range(5):
        c, rec = ctx.cli(m, "--static-only", label="T-static")
        st.append(round(c.wall, 3))
    med = statistics.median(walls) if walls else None
    checks = {
        "g0_median_le_30": med is not None and med <= 30,
        "g0_max_le_60": bool(walls) and max(walls) <= 60,
        "d7a_le_3": bool(d7) and max(d7) <= 3,
        "static_only_le_1_5": max(st) <= 1.5,
    }
    prediction_notes = {
        "g0_median_8s_prediction_met": med is not None and med <= 8,
    }
    obs = (
        f"G0 median {med if med is None else round(med, 2)} s, max "
        f"{max(walls) if walls else None}; a-D7 walls {d7}; static-only {st}"
    )
    return result("T", pred, [], True, checks, obs,
                  {"g0_walls": walls, "d7a_walls": d7, "static_only_walls": st,
                   **prediction_notes})  # fmt: skip


# ---- driver ---------------------------------------------------------------------------------

ORDER = (
    ["P0", "G0"]
    + [f"G-{d}{m}" for d in DRIFT_IDS for m in "abc"]
    + ["A1", "A1p", "A2", "A3", "A4", "A5", "Z1", "K2", "K3a", "K3c", "K3b", "B1", "T", "K1"]
)

# mutant -> the probes the prereg names (LG13: its unit test only)
MUTANT_PROBES = {
    "LG1": ["P0", "G0"], "LG2": ["P0", "G0"], "LG3": ["P0", "G0"], "LG4": ["P0", "Z1"],
    "LG5": ["P0", "Z1"], "LG6": ["P0", "A2"], "LG7": ["P0", "A1"], "LG8": ["P0", "A3"],
    "LG9": ["P0", "K3a"], "LG10": ["P0", "K3a", "K3c"], "LG11": ["P0", "G-D7a", "G-D7c"],
    "LG12": ["B1"], "LG13": [],
}  # fmt: skip


# the checks that are the evidence of a kill (None: any failing check of that probe counts)
MUTANT_EVIDENCE: dict[str, dict[str, set[str] | None]] = {
    "LG1": {"G0": None},
    "LG2": {"G0": {"one_container_per_call"}},
    "LG3": {"G0": None},
    "LG4": {"Z1": None},
    "LG5": {"Z1": None},
    "LG6": {"A2": {"error_code", "zero_docker_calls", "message_names_lattice_db"}},
    "LG7": {"A1": {"error_code", "shim_log_is_info_only"}},
    "LG8": {"A3": {"error_code", "no_run_no_pull", "log_is_info_then_image_inspect"}},
    "LG9": {"K3a": {"no_container_left"}, "K3c": {"no_container_left"}},
    "LG10": {"K3a": {"no_streak_api_left"}, "K3c": {"no_streak_api_left"}},
    "LG11": {"G-D7a": {"containers_as_predicted"}, "G-D7c": {"containers_as_predicted"}},
    "LG12": {"B1": {"all_byte_identical"}},
}


def _drift_summary(results: list[Any]) -> list[dict[str, Any]]:
    rows = []
    for r in results:
        if r.id.startswith("G-"):
            rows.append({"id": r.id, "pass": r.passed, "class": r.extra.get("class"),
                         "predicted_class": r.extra.get("predicted_class"),
                         "S": r.extra.get("S"), "L": r.extra.get("L"), "N": r.extra.get("N"),
                         "checks_failed": [k for k, v in r.checks.items() if not v]})  # fmt: skip
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--streak-ref", default=STREAK_REF)
    ap.add_argument("--scratch", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--only", default="", help="comma-separated probe ids (G = every G-*)")
    ap.add_argument("--keep", action="store_true", help="keep the scratch clones")
    ap.add_argument("--code-root", type=Path, default=REPO, help="the tree whose lattice/ runs")
    ap.add_argument(
        "--mutants",
        default="",
        help="comma-separated LG ids: run the named probes "
        "on each mutated copy (needs dogfood/contracts_mutants.py)",
    )
    ns = ap.parse_args(argv)
    if ns.mutants:
        return run_mutants(ns)
    ids = [i for i in ns.only.split(",") if i] or ORDER
    if "G" in ids:
        ids = [i for i in ids if i != "G"] + [i for i in ORDER if i.startswith("G-")]
    unknown = [i for i in ids if i not in PROBES]
    if unknown:
        print(f"unknown probe ids: {unknown}", file=sys.stderr)
        return 2
    ids = [i for i in ORDER if i in ids]
    if "P0" not in ids and any(i != "B1" for i in ids):
        ids = ["P0", *ids]
    ns.out.mkdir(parents=True, exist_ok=True)
    ns.scratch.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ns.code_root))
    ctx = LCtx(ns.runs, ns.scratch.resolve(), ns.out, ns.code_root.resolve(), ns.streak_ref)
    t_start = time.time()
    results: list[Any] = []
    ran: list[str] = []
    try:
        for pid in ids:
            print(f"... {pid}", file=sys.stderr, flush=True)
            t0 = time.time()
            try:
                res = PROBES[pid](ctx)
            except Exception as e:  # noqa: BLE001 -- a probe that cannot run is reported
                res = Result(pid, "(probe did not run)", f"ERROR {type(e).__name__}: {e}", False,
                             [], False, {"ran": False}, {"error": repr(e)})  # fmt: skip
            res.extra["probe_wall_s"] = round(time.time() - t0, 1)
            if pid == "G-D7a":
                ARGS["d7a_walls"] = res.extra.get("wall_s", [])
            results.append(res)
            ran.append(pid)
            base.write_probe(ctx, res)
    finally:
        procs = base.leftover_processes(ctx)
        alive = ctx.containers_alive()
        for name in alive:  # never leave one behind
            subprocess.run([DOCKER, "rm", "-f", "-v", name], capture_output=True)
        tmp_left = sorted(p.name for p in ctx.tmp.iterdir()) if ctx.tmp.exists() else []
        if not ns.keep:
            shutil.rmtree(ctx.root, ignore_errors=True)
    passed = sum(1 for r in results if r.passed)
    print(f"{'id':<7} | observed | pass")
    for r in results:
        print(f"{r.id:<7} | {r.observed} | {'PASS' if r.passed else 'FAIL'}")
    print(f"probes: {passed}/{len(results)} pass")
    g = [r for r in results if r.id.startswith("G-") or r.id == "G0"]
    summary = {
        "experiment": "2026-10-15-live-go-mysql",
        "command": "python dogfood/contracts/livego_probes.py " + " ".join(sys.argv[1:]),
        "runs": ns.runs,
        "code_root": str(ns.code_root),
        "streak_ref": ns.streak_ref,
        "wall_s": round(time.time() - t_start, 1),
        "probes": [
            {
                "id": r.id,
                "status": "RAN",
                "prediction": r.prediction,
                "observed": r.observed,
                "pass": r.passed,
                "runs": len(r.runs),
                "identical": r.identical,
                "checks": r.checks,
                "probe_wall_s": r.extra.get("probe_wall_s"),
            }
            for r in results  # fmt: skip
        ],
        "not_run": [i for i in ORDER if i not in ran],
        "totals": {"pass": passed, "total": len(results)},
        "g_configurations": {
            "pass": sum(1 for r in g if r.passed),
            "total": len(g),
            "rows": _drift_summary(results),
        },  # fmt: skip
        "containers_created_by_checks": len(ctx.created_total),
        "live_calls": len(ctx.leaks),
        "builds": ctx.builds,
        "leftover_processes": procs,
        "containers_removed_by_runner_at_end": alive,
        "leftover_tmp_entries": tmp_left,
        "scratch_removed": not ns.keep,
        "post_hoc": {
            "script_corrections": POST_HOC_SCRIPT_CORRECTIONS,
            "procedure": POST_HOC_PROCEDURE,
        },  # fmt: skip
    }
    (ns.out / "summary.json").write_text(json.dumps(base.scrub(summary, ctx.root), indent=1) + "\n")
    return 0 if passed == len(results) and not procs and not alive else 1


def run_mutants(ns: argparse.Namespace) -> int:
    """Each mutant: the tree copied and patched by contracts_mutants, then this runner on it
    (--code-root), `--runs 1`, only the probes the prereg names. A mutant is KILLED when one of
    them fails (or reports an error)."""
    cm = _load("lattice_contracts_mutants", REPO / "dogfood" / "contracts_mutants.py")
    ids = [i for i in ns.mutants.split(",") if i]
    ns.out.mkdir(parents=True, exist_ok=True)
    table = []
    for mid in ids:
        named = MUTANT_PROBES[mid]
        if not named:
            table.append({"id": mid, "probes": [], "verdict": "NOT RUN (unit test only)"})
            continue
        with tempfile.TemporaryDirectory(prefix=f"lattice-livego-{mid}-") as d:
            dst = Path(d)
            cm.copy_tree(dst)
            cm.apply(dst, mid)
            out = ns.out / f"mutant-{mid}"
            cmd = [
                sys.executable,
                str(HERE / "livego_probes.py"),
                "--runs",
                "1",
                "--streak-ref",
                ns.streak_ref,
                "--scratch",
                str(ns.scratch / mid),
                "--out",
                str(out),
                "--code-root",
                str(dst),
                "--only",
                ",".join(named),
            ]
            r = subprocess.run(cmd, capture_output=True, text=True)
            try:
                summ = json.loads((out / "summary.json").read_text())
                fails = {
                    p["id"]: sorted(k for k, v in p["checks"].items() if not v)
                    for p in summ["probes"]
                    if not p["pass"] and p["id"] != "P0"
                }
                p0 = next((p for p in summ["probes"] if p["id"] == "P0"), None)
                evidence = MUTANT_EVIDENCE.get(mid, {})
                hit = {
                    pid: [c for c in cs if evidence.get(pid) is None or c in evidence[pid]]
                    for pid, cs in fails.items()
                    if pid in evidence
                }
                hit = {k: v for k, v in hit.items() if v}
                verdict = (
                    "ERROR (P0 failed)"
                    if p0 and not p0["pass"]
                    else "KILLED"
                    if hit
                    else "FAILED OTHER CHECKS ONLY"
                    if fails
                    else "SURVIVED"
                )
            except (OSError, ValueError):
                fails, hit, verdict = {}, {}, "ERROR (no summary)"
            table.append(
                {
                    "id": mid,
                    "probes": named,
                    "failed_checks": fails,
                    "kill_evidence": hit,
                    "verdict": verdict,
                    "rc": r.returncode,
                }
            )
            print(f"{mid}: {verdict} {hit}", flush=True)
    (ns.out / "mutants-probes.json").write_text(json.dumps(table, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
