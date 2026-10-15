"""Probes of engine-agnostic statement counting for ``lattice perf`` (Postgres and ClickHouse
through the engine descriptors' ``[perf]`` table), pre-registered in
``docs/results/2026-10-15-perf-engines-prereg.md`` (sections 3 and 4, with Amendment 1).

Each probe is a deterministic repetition run ``--runs`` times (default 10); its canonical output
(the CLI's ``--json`` without ``latency_ms``, ``project`` and ``budget_file``, container names
as ``<container>``, run databases ``lattice_xxxxxxxx_`` as ``<run>_``) must hash to the same
sha256 every time. The predictions are written here from the pre-registration, before the new
code was run; a probe that differs is a finding, not something to tune.

**RAN and NOT RUN.** A probe whose environment is missing (the Docker daemon, ``psycopg`` /
``clickhouse-connect`` / ``pymysql`` in the probe interpreter, the Streak clone) is implemented
and listed as **NOT RUN** with its prediction and the reason: never PASS. Run it later with
``--only 'P-pg*'`` once Docker is running and ``--python`` names the scratch venv (steps in the
pre-registration, section 3). Where a probe's claim is about the *tool* (the SQL it sends and
binds, the rows it expands, the filter it applies, the two failure kinds), the same probe also
runs against the fake servers, drivers and ``docker`` of ``tests/perf/fakeengines.py`` (in the
CLI's own process, through ``--launch --fake``) as its ``/fake`` twin; a twin shows the tool's
logic, never what ``pg_stat_statements`` or ``system.query_log`` do.

Usage::

    python dogfood/perf_engines_probes.py [--runs 10] [--out DIR] [--only PATTERN ...]
        [--python PY] [--streak DIR] [--scratch DIR] [--measure-runs 10] [--list]
    python dogfood/perf_engines_probes.py --mutants [--python PY] [--out FILE] [--jobs N]

Prints ``id | status | predicted | observed | pass`` per probe and ends
``probes: P/T pass, N not run``; exit 0 when every probe passes and none is NOT RUN, 3 when none
failed but some are NOT RUN, 1 when a probe that ran failed. ``--mutants`` runs PX1-PX11 of
``dogfood/perf_mutants.py`` and ends ``mutants: K/N killed (M pending a server)``.
No LLM, no API key, no network.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import fnmatch
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
OLD_REV = "a4ab4a1"
STREAK_REV = "a0679e9"
STREAK_SRC = Path("/Users/vivekgade/Documents/streak")
ENGINES = ("postgres", "clickhouse")
SHAPE_PG = [
    (1, "SELECT * FROM items WHERE user_id = $? ORDER BY id"),
    (1, "SELECT id FROM users WHERE token = $?"),
    (3, "SELECT name FROM users WHERE id = $?"),
]
SHAPE_CH_PREFIX = {  # the app's text, literals as ?; the driver may add a suffix
    "SELECT id FROM users WHERE token = ?": 1,
    "SELECT * FROM items WHERE user_id = ? ORDER BY id": 1,
    "SELECT name FROM users WHERE id = ?": 3,
}
# budget id -> (endpoint, budget constant, budget per_item, counts at N=3 / N=7, fit, verdict)
TABLE = {
    "items": ("GET /items", 2, 0, (2, 2), (2, 0), "pass"),
    "n1-tight": ("GET /items_n1", 2, 0, (5, 9), (2, 1), "fail"),
    "n1": ("GET /items_n1", 2, 1, (5, 9), (2, 1), "pass"),
    "nodb": ("GET /nodb", 0, 0, (0, 0), (0, 0), "pass"),
}
SIZES = (3, 7)
SEED = {"items": "N", "steps": [{"method": "POST", "path": "/items", "user": "ann", "count": "N",
                                 "json": {"name": "item-{i}"}}]}  # fmt: skip

# the a4ab4a1 `docker run` argv of `lattice check` (name suffix fixed), written out literally
_BASE = ["run", "-d", "--name", "lattice-perf-{e}-00000000", "--label", "lattice.spec=throwaway",
         "-p", "127.0.0.1::{port}"]  # fmt: skip
ARGV = {
    "postgres": [*_BASE, "--tmpfs", "/var/lib/postgresql/data:rw", "-e", "POSTGRES_USER=lattice",
                 "-e", "POSTGRES_PASSWORD=lattice", "-e", "POSTGRES_DB=postgres",
                 "postgres:16-alpine", "-c", "fsync=off", "-c", "synchronous_commit=off",
                 "-c", "full_page_writes=off", "-c", "max_connections=300"],
    "mysql": [*_BASE, "--tmpfs", "/var/lib/mysql:rw", "-e", "MYSQL_ROOT_PASSWORD=lattice",
              "mysql:8.4", "--innodb-flush-log-at-trx-commit=0", "--skip-log-bin",
              "--sync-binlog=0", "--max-connections=300", "--innodb-doublewrite=OFF"],
    "clickhouse": [*_BASE, "--tmpfs", "/var/lib/clickhouse:rw", "-e", "CLICKHOUSE_USER=lattice",
                   "-e", "CLICKHOUSE_PASSWORD=lattice", "-e",
                   "CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1", "--ulimit", "nofile=262144:262144",
                   "clickhouse/clickhouse-server:24.8-alpine"],
}  # fmt: skip
PORTS = {"postgres": 5432, "mysql": 3306, "clickhouse": 8123}

# ---- canonical output ------------------------------------------------------------------------

_CONTAINER = re.compile(r"lattice-perf-[a-z]+-[0-9a-f]{8}")
_RUN = re.compile(r"lattice_[0-9a-f]{8}_")


def canon(obj: Any, top: bool = True) -> Any:
    """The CLI's ``--json`` without ``latency_ms``, ``project``, ``budget_file``; container
    names and run-database prefixes replaced (the runtime leaves no trace)."""
    if isinstance(obj, dict):
        drop = {"latency_ms"} | ({"project", "budget_file"} if top else set())
        return {k: canon(v, False) for k, v in obj.items() if k not in drop}
    if isinstance(obj, list):
        return [canon(v, False) for v in obj]
    if isinstance(obj, str):
        return _RUN.sub("<run>_", _CONTAINER.sub("<container>", obj))
    return obj


def chash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(canon(obj), sort_keys=True).encode()).hexdigest()[:16]


# ---- the context probes run in ---------------------------------------------------------------


@dataclass
class Run:
    rc: int
    out: str
    err: str
    seconds: float
    data: dict | None = None  # the stdout parsed as JSON
    world: dict | None = None  # fake mode: what the fake docker saw
    audit: list[list[str]] | None = None  # subprocess.Popen argvs (--audit)
    stmts: list[list[Any]] | None = None  # statements SqlCounter sent (--record)


@dataclass
class Ctx:
    scratch: Path
    python: str
    runs: int
    mruns: int
    fake: bool = False
    streak: Path | None = None
    old: Path | None = None
    owner: str = field(default_factory=lambda: f"perf-probe-{uuid.uuid4().hex[:8]}")
    _n: int = 0

    def next_dir(self, what: str) -> Path:
        self._n += 1
        d = self.scratch / f"{what}-{self._n}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def env(self, tree: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("ANTHROPIC", "LATTICE_"))}  # fmt: skip
        env["PYTHONPATH"] = str(tree)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["LATTICE_CONTAINER_OWNER"] = self.owner
        env.update(extra or {})
        return env

    def plain(self, project: Path, command: str, args: list[str], tree: Path | None = None,
              extra_env: dict[str, str] | None = None, python: str | None = None,
              timeout: float = 900) -> Run:  # fmt: skip
        """``python -m lattice.perf COMMAND --project P ARGS`` with ``tree`` on PYTHONPATH."""
        argv = [python or self.python, "-m", "lattice.perf", command, "--project", str(project),
                *args]  # fmt: skip
        return self._exec(argv, self.env(tree or ROOT, extra_env), timeout)

    def launch(self, project: Path, command: str, args: list[str], flags: list[str] | None = None,
               extra_env: dict[str, str] | None = None, timeout: float = 900) -> Run:  # fmt: skip
        """The same through this script's launcher (fake servers, hooks, records)."""
        d = self.next_dir("launch")
        files = {"world": d / "world.json", "audit": d / "audit.json", "stmts": d / "stmts.json"}
        flags = list(flags or [])
        if self.fake:
            flags += ["--fake", "--world", str(files["world"])]
        flags += ["--audit", str(files["audit"]), "--record", str(files["stmts"])]
        argv = [self.python, str(HERE), "--launch", *flags, "--", command, "--project",
                str(project), *args]  # fmt: skip
        run = self._exec(argv, self.env(ROOT, extra_env), timeout)
        for key, path in files.items():
            if path.is_file():
                setattr(run, key, json.loads(path.read_text()))
        return run

    def _exec(self, argv: list[str], env: dict[str, str], timeout: float) -> Run:
        t0 = time.monotonic()
        try:
            r = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout,
                               check=False)  # fmt: skip
            rc, out, err = r.returncode, r.stdout, r.stderr
        except subprocess.TimeoutExpired as e:
            rc, out, err = 124, str(e.stdout or ""), f"[timeout after {timeout:g}s]"
        run = Run(rc, out, err, round(time.monotonic() - t0, 2))
        with contextlib.suppress(json.JSONDecodeError):
            run.data = json.loads(out)
        return run

    # -- containers

    def left(self, run: Run | None = None) -> list[str]:
        """Containers of this probe's owner token that remain (fake: started, not removed)."""
        if self.fake:
            w = (run.world if run else None) or {}
            return sorted(set(w.get("started", [])) - set(w.get("removed", [])))
        out = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"label=lattice.owner={self.owner}", "--format",
             "{{.Names}}"], capture_output=True, text=True, check=False,
        )  # fmt: skip
        return out.stdout.split()


# ---- scratch projects ------------------------------------------------------------------------


def write_budgets(project: Path, ids: list[str] | None = None, sizes=SIZES,
                  seed: Any = SEED) -> None:  # fmt: skip
    budgets = []
    for bid, (endpoint, c, p, _counts, _fit, _v) in TABLE.items():
        if ids and bid not in ids:
            continue
        budgets.append({"id": bid, "endpoint": endpoint, "user": "ann", "seed": seed,
                        "sizes": list(sizes), "queries": {"constant": c, "per_item": p},
                        "latency_ms": None})  # fmt: skip
    (project / "perf.budget.json").write_text(json.dumps({"version": 1, "budgets": budgets}))


def server_project(
    ctx: Ctx,
    engine: str,
    process: bool = False,
    ids: list[str] | None = None,
    engines_file: Path | None = None,
    database: str | None = None,
) -> Path:
    d = ctx.next_dir("project")
    shutil.copy(ROOT / "tests/perf/serverapp.py", d / "serverapp.py")
    app = "make_process_app" if process else "make_app"
    toml = (f'[app]\napp = "serverapp:{app}"\ndb = "serverapp:build_db"\n'
            f'login = "serverapp:login"\ndatabase = "{database or engine}"\n')  # fmt: skip
    if engines_file is not None:
        (d / "engines").mkdir()
        shutil.copy(engines_file, d / "engines" / engines_file.name)
        toml += f'\n[engines]\npaths = ["engines/{engines_file.name}"]\n'
    (d / "studio.toml").write_text(toml)
    write_budgets(d, ids)
    return d


def sqlite_project(ctx: Ctx, process: bool = False, sizes=(2, 6)) -> Path:
    d = ctx.next_dir("project")
    shutil.copy(ROOT / "tests/perf/perfapp.py", d / "perfapp.py")
    toml = '[app]\napp = "perfapp:make_app"\ndb = "perfapp:build_db"\nlogin = "perfapp:login"\n'
    if process:
        toml += 'runtime = "process"\n'
    (d / "studio.toml").write_text(toml)
    budgets = [
        {"id": "items", "endpoint": "GET /items", "user": "ann", "seed": SEED,
         "sizes": list(sizes), "queries": {"constant": 2, "per_item": 0}, "latency_ms": None},
        {"id": "n1", "endpoint": "GET /items_n1", "user": "ann", "seed": SEED,
         "sizes": list(sizes), "queries": {"constant": 2, "per_item": 1}, "latency_ms": None},
    ]  # fmt: skip
    (d / "perf.budget.json").write_text(json.dumps({"version": 1, "budgets": budgets}))
    return d


SILENT_APP = '''\
"""An app that ignores the database it is given: its data is a SQLite file of its own."""
import os
import sqlite3
import tempfile

from fastapi import FastAPI

_FILE = os.path.join(tempfile.mkdtemp(prefix="silentapp-"), "own.db")
_CONN = sqlite3.connect(_FILE, check_same_thread=False, isolation_level=None)
_CONN.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT)")


def build_db(url):
    return url


def login(client, handle):
    return client.post("/login", json={"name": handle}).json()["token"]


def make_app(url):
    app = FastAPI()

    @app.post("/login")
    def do_login(body: dict):
        return {"token": "t"}

    @app.post("/items", status_code=201)
    def create(body: dict):
        _CONN.execute("INSERT INTO items (name) VALUES (?)", (str(body.get("name")),))
        return {"ok": True}

    @app.get("/items")
    def items():
        return [r[0] for r in _CONN.execute("SELECT name FROM items")]

    @app.get("/items_n1")
    def items_n1():
        return [r[0] for r in _CONN.execute("SELECT name FROM items")]

    return app
'''


def silent_project(ctx: Ctx, engine: str) -> Path:
    d = ctx.next_dir("project")
    (d / "silentapp.py").write_text(SILENT_APP)
    (d / "studio.toml").write_text(
        '[app]\napp = "silentapp:make_app"\ndb = "silentapp:build_db"\n'
        f'login = "silentapp:login"\ndatabase = "{engine}"\n'
    )
    write_budgets(d, ["items", "n1"])
    return d


# ---- judging the pg/ch table -------------------------------------------------------------------


def judge_reports(data: dict | None, engine: str, mruns: int) -> tuple[int, int, list[str]]:
    """(runs as predicted, runs, problems) of a ``--json`` result of the four budgets."""
    problems: list[str] = []
    if not data:
        return 0, 4 * 2 * mruns, ["no JSON report"]
    ok = total = 0
    reports = {r["budget_id"]: r for r in data.get("reports", [])}
    for bid, (_e, _c, _p, counts, fit, verdict) in TABLE.items():
        r = reports.get(bid)
        if r is None:
            problems.append(f"{bid}: no report")
            total += 2 * mruns
            continue
        if r["verdict"] != verdict:
            problems.append(f"{bid}: verdict {r['verdict']} (predicted {verdict})")
        got_fit = r.get("fit") or {}
        if (got_fit.get("constant"), got_fit.get("per_item"), got_fit.get("exact")) != (
            fit[0], fit[1], True,
        ):  # fmt: skip
            problems.append(f"{bid}: fit {got_fit} (predicted {fit[0]} + {fit[1]}N exact)")
        for size, want in zip(r["sizes"], counts, strict=False):
            for got in size["statements"]:
                total += 1
                ok += got == want
            total += max(0, mruns - len(size["statements"]))
        if len(r["sizes"]) != 2:
            problems.append(f"{bid}: {len(r['sizes'])} sizes (predicted 2)")
    n1 = reports.get("n1")
    shapes = ((n1 or {}).get("sizes") or [{}])[0].get("shapes")
    if shapes is None:
        problems.append("n1: no statement shapes")
    elif engine == "postgres":
        got = [(s["count"], s["sql"]) for s in shapes]
        if got != SHAPE_PG:
            problems.append(f"n1 shapes {got} (predicted {SHAPE_PG})")
    else:
        found: dict[str, int] = {}
        for s in shapes:
            for prefix in SHAPE_CH_PREFIX:
                if s["sql"].startswith(prefix):
                    found[prefix] = found.get(prefix, 0) + s["count"]
        if found != SHAPE_CH_PREFIX or sum(s["count"] for s in shapes) != 5:
            problems.append(f"n1 shapes {[(s['count'], s['sql']) for s in shapes]} "
                            f"(predicted counts 1, 1, 3 for {list(SHAPE_CH_PREFIX)})")  # fmt: skip
    return ok, total, problems


# ---- probes ------------------------------------------------------------------------------------


@dataclass
class Probe:
    id: str
    title: str
    predicted: str
    needs: tuple[str, ...]  # docker | psycopg | clickhouse_connect | pymysql | streak
    rep: Callable[[Ctx, int], dict]  # one repetition: {"hash": obj, ...facts}
    judge: Callable[[Ctx, list[dict]], tuple[bool, str]]
    fake: bool = False  # runs against the fake servers (the /fake twin)


def _git_show(path: str, rev: str = OLD_REV) -> str:
    return subprocess.run(["git", "show", f"{rev}:{path}"], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout  # fmt: skip


def _defs(source: str) -> dict[str, str]:
    """Top-level name -> ast dump of each definition and assignment."""
    out: dict[str, str] = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef | ast.ClassDef | ast.AsyncFunctionDef):
            out[node.name] = ast.dump(node)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = ast.dump(node)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = ast.dump(node)
    return out


def _all_same(reps: list[dict]) -> int:
    return len({chash(r["hash"]) for r in reps})


# -- P-regress (a)


MEASURE_UNCHANGED = ["MysqlCounter", "SqliteCounter", "app_statements", "shape", "shapes_of",
                     "prepare", "_in_process", "harness_from_project", "COUNTED_MYSQL",
                     "CONTAINER_PREFIX"]  # fmt: skip


def rep_regress_a(ctx: Ctx, i: int) -> dict:
    diffs: list[str] = []
    for path in ("lattice/perf/budget.py", "lattice/perf/__main__.py"):
        if _git_show(path) != (ROOT / path).read_text():
            diffs.append(f"{path}: bytes differ")
    old = _defs(_git_show("lattice/perf/measure.py"))
    new = _defs((ROOT / "lattice/perf/measure.py").read_text())
    for name in MEASURE_UNCHANGED:
        if name not in old or name not in new:
            diffs.append(f"{name}: missing")
        elif old[name] != new[name]:
            diffs.append(f"{name}: AST differs")
    changed = sorted(n for n in new if n in old and old[n] != new[n] and n not in diffs)
    return {"hash": {"diffs": diffs}, "diffs": diffs, "changed_elsewhere": changed,
            "added": sorted(set(new) - set(old))}  # fmt: skip


def judge_regress_a(ctx: Ctx, reps: list[dict]) -> tuple[bool, str]:
    r = reps[0]
    ok = not r["diffs"] and _all_same(reps) == 1
    items = 2 + len(MEASURE_UNCHANGED)
    return ok, (f"{len(r['diffs'])} differences in {items} compared items (2 files byte-identical,"
                f" {len(MEASURE_UNCHANGED)} definitions AST-identical); changed elsewhere: "
                f"{', '.join(r['changed_elsewhere'])}; new: {', '.join(r['added'])}")  # fmt: skip


# -- P-regress (b)

TEST_FILES = ["tests/perf/test_budget.py", "tests/perf/test_measure.py", "tests/perf/perfapp.py",
              "tests/perf/__init__.py"]  # fmt: skip


def rep_regress_b(ctx: Ctx, i: int) -> dict:
    diffs: list[str] = []
    n_funcs = 0
    for path in TEST_FILES:
        old, new = _defs(_git_show(path)), _defs((ROOT / path).read_text())
        n_funcs += len(old)
        for name in sorted(set(old) | set(new)):
            if old.get(name) != new.get(name):
                diffs.append(f"{path}::{name}")
    r = subprocess.run(
        [ctx.python, "-m", "pytest", "tests/perf/test_budget.py", "tests/perf/test_measure.py",
         "-q", "-p", "no:cacheprovider", "--no-header"],
        cwd=ROOT, env=ctx.env(ROOT), capture_output=True, text=True, check=False,
    )  # fmt: skip
    summary = next(
        (ln for ln in reversed(r.stdout.splitlines()) if "passed" in ln), r.stdout[-200:]
    )
    numstat = ["git", "diff", "--numstat", OLD_REV, "--", "tests/spec/test_engines.py"]
    spec = subprocess.run(numstat, cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.split()  # fmt: skip
    return {"hash": {"diffs": diffs, "pytest": re.sub(r" in [\d.]+s", "", summary.strip())},
            "diffs": diffs, "defs": n_funcs, "pytest": summary.strip(), "rc": r.returncode,
            "engines_test_numstat": spec[:2]}  # fmt: skip


def judge_regress_b(ctx: Ctx, reps: list[dict]) -> tuple[bool, str]:
    r = reps[0]
    ok = (not r["diffs"] and all(x["rc"] == 0 and x["pytest"].startswith("68 passed")
                                 for x in reps)  # fmt: skip
          and r["engines_test_numstat"] == ["1", "1"] and _all_same(reps) == 1)  # fmt: skip
    return ok, (f"{len(r['diffs'])} changed definitions of {r['defs']} in 4 files; pytest: "
                f"{r['pytest']}; tests/spec/test_engines.py: +{r['engines_test_numstat'][0]} "
                f"-{r['engines_test_numstat'][1]} line(s)")  # fmt: skip


# -- P-regress (c): SQLite in-process, old vs new


def _old_tree(ctx: Ctx) -> Path:
    if ctx.old is None:
        d = ctx.scratch / "old"
        d.mkdir(exist_ok=True)
        tar = subprocess.run(["git", "archive", "--format=tar", OLD_REV, "lattice"], cwd=ROOT,
                             capture_output=True, check=True).stdout  # fmt: skip
        import io
        import tarfile

        with tarfile.open(fileobj=io.BytesIO(tar)) as t:
            t.extractall(d, filter="data")
        ctx.old = d
    return ctx.old


def rep_regress_c(ctx: Ctx, i: int) -> dict:
    project = ctx.scratch / "p-regress-c"
    if not project.is_dir():
        project = sqlite_project(ctx)
        shutil.move(str(project), str(ctx.scratch / "p-regress-c"))
        project = ctx.scratch / "p-regress-c"
    args = ["--json", "--runs", str(ctx.mruns), "--sizes", "2,6", "--statements"]
    old = ctx.plain(project, "measure", args, tree=_old_tree(ctx))
    new = ctx.plain(project, "measure", args)
    counts = {}
    for side, r in (("old", old), ("new", new)):
        counts[side] = {x["budget_id"]: [s["statements"] for s in x["sizes"]]
                        for x in (r.data or {}).get("reports", [])}  # fmt: skip
    return {"hash": {"old": canon(old.data), "new": canon(new.data), "rc": [old.rc, new.rc]},
            "same": chash(old.data) == chash(new.data), "rc": [old.rc, new.rc],
            "hashes": [chash(old.data), chash(new.data)], "counts": counts,
            "raw": new.data, "stderr": [old.err[-300:], new.err[-300:]]}  # fmt: skip


def judge_regress_c(ctx: Ctx, reps: list[dict]) -> tuple[bool, str]:
    m = ctx.mruns
    want = {"items": [[2] * m, [2] * m], "n1": [[4] * m, [8] * m]}
    distinct = len({h for r in reps for h in r["hashes"]})
    counts_ok = sum(r["counts"][s] == want for r in reps for s in ("old", "new"))
    ok = distinct == 1 and all(r["rc"] == [0, 0] for r in reps) and counts_ok == 2 * len(reps)
    return ok, (f"{distinct} distinct hash over {2 * len(reps)} invocations (old a4ab4a1 and "
                f"new); exit codes {reps[0]['rc']}; counts 2/2 and 4/8 as predicted in "
                f"{counts_ok}/{2 * len(reps)} invocations")  # fmt: skip


# -- P-regress (d): a process app on SQLite stays unmeasured


def rep_regress_d(ctx: Ctx, i: int) -> dict:
    project = ctx.scratch / "p-regress-d"
    if not project.is_dir():
        shutil.move(str(sqlite_project(ctx, process=True)), str(project))
    args = ["--json", "--runs", "1"]
    old = ctx.plain(project, "measure", args, tree=_old_tree(ctx))
    new = ctx.plain(project, "measure", args)
    notes = {s: [x["notes"] for x in (r.data or {}).get("reports", [])]
             for s, r in (("old", old), ("new", new))}  # fmt: skip
    return {"hash": {"old": canon(old.data), "new": canon(new.data), "rc": [old.rc, new.rc]},
            "rc": [old.rc, new.rc], "notes": notes, "same": chash(old.data) == chash(new.data),
            "raw": new.data, "stderr": [old.err[-300:], new.err[-300:]]}  # fmt: skip


def judge_regress_d(ctx: Ctx, reps: list[dict]) -> tuple[bool, str]:
    r = reps[0]
    note_ok = all(any("process app on SQLite is not supported" in n for n in ns)
                  for s in ("old", "new") for ns in r["notes"][s])  # fmt: skip
    ok = (all(x["rc"] == [2, 2] and x["same"] for x in reps) and note_ok
          and r["notes"]["old"] == r["notes"]["new"] and _all_same(reps) == 1)  # fmt: skip
    return ok, (f"exit {r['rc']}; the unmeasured note is the same old and new ({note_ok}); "
                f"{_all_same(reps)} distinct hash over {2 * len(reps)}")  # fmt: skip


# -- P-regress (e): MySQL on Streak, old vs new (Docker)

STREAK_BUDGETS = {"version": 1, "prepare": "go build -o bin/streak-api ./cmd/server", "budgets": [
    {"id": "home", "endpoint": "GET /v1/home", "user": "ann", "sizes": [5, 25],
     "queries": {"constant": 6, "per_item": 1}, "latency_ms": None,
     "seed": {"items": "N", "steps": [{"method": "POST", "path": "/v1/habits", "user": "ann",
              "count": "N", "json": {"id": "{uuid}", "name": "habit-{i}", "period": "week",
                                     "target_count": 3, "start_date": "2026-03-02"}}]}},
    {"id": "habits", "endpoint": "GET /v1/habits", "user": "ann", "sizes": [5, 25],
     "queries": {"constant": 4, "per_item": 0}, "latency_ms": None,
     "seed": {"items": "N", "steps": [{"method": "POST", "path": "/v1/habits", "user": "ann",
              "count": "N", "json": {"id": "{uuid}", "name": "habit-{i}", "period": "week",
                                     "target_count": 3, "start_date": "2026-03-02"}}]}},
]}  # fmt: skip


def _streak_clone(ctx: Ctx) -> Path:
    if ctx.streak is not None:
        return ctx.streak
    dst = ctx.scratch / "streak-perf"
    if not dst.is_dir():
        subprocess.run(["git", "clone", "--no-hardlinks", str(STREAK_SRC), str(dst)], check=True,
                       capture_output=True)  # fmt: skip
        subprocess.run(["git", "checkout", STREAK_REV], cwd=dst, check=True, capture_output=True)
    (dst / "perf.budget.json").write_text(json.dumps(STREAK_BUDGETS))
    ctx.streak = dst
    return dst


def rep_regress_e(ctx: Ctx, i: int) -> dict:
    project = _streak_clone(ctx)
    args = ["--json", "--runs", str(ctx.mruns), "--statements"]
    old = ctx.plain(project, "check", args, tree=_old_tree(ctx))
    new = ctx.plain(project, "check", args)
    counts: dict[str, dict] = {}
    fits: dict[str, dict] = {}
    for side, r in (("old", old), ("new", new)):
        reps_ = (r.data or {}).get("reports", [])
        counts[side] = {x["budget_id"]: [s["statements"] for s in x["sizes"]] for x in reps_}
        fits[side] = {x["budget_id"]: x["fit"] for x in reps_}
    return {"hash": {"old": canon(old.data), "new": canon(new.data), "rc": [old.rc, new.rc]},
            "rc": [old.rc, new.rc], "hashes": [chash(old.data), chash(new.data)],
            "counts": counts, "fits": fits, "stderr": [old.err[-300:], new.err[-300:]]}  # fmt: skip


def judge_regress_e(ctx: Ctx, reps: list[dict]) -> tuple[bool, str]:
    m = ctx.mruns
    want = {"home": [[11] * m, [31] * m], "habits": [[4] * m, [4] * m]}
    fit = {"home": {"constant": 6, "per_item": 1, "exact": True},
           "habits": {"constant": 4, "per_item": 0, "exact": True}}  # fmt: skip
    distinct = len({h for r in reps for h in r["hashes"]})
    n = sum(r["counts"][s] == want and r["fits"][s] == fit for r in reps for s in ("old", "new"))
    ok = distinct == 1 and all(r["rc"] == [0, 0] for r in reps) and n == 2 * len(reps)
    return ok, (f"{distinct} distinct hash over {2 * len(reps)}; exit 0 in "
                f"{sum(r['rc'] == [0, 0] for r in reps) * 2}/{2 * len(reps)}; counts and fits as "
                f"predicted in {n}/{2 * len(reps)} invocations")  # fmt: skip


# -- P-regress (f): the docker run argv is unchanged without extra_args

ARGV_SNIPPET = textwrap.dedent("""
    import json, os, secrets, shutil, subprocess
    os.environ.pop("LATTICE_CONTAINER_OWNER", None)
    from lattice.spec import dbserver
    shutil.which = lambda c, *a, **k: "/usr/bin/docker"
    secrets.token_hex = lambda n: "00000000"
    seen = []
    def fake(*a, timeout=0):
        seen.append(list(a))
        return subprocess.CompletedProcess(a, 1, "", "stop")
    dbserver._docker = fake
    out = {}
    for e in ("postgres", "mysql", "clickhouse"):
        seen.clear()
        try:
            dbserver.start_container(e, name_prefix="lattice-perf-")
        except dbserver.DbServerError:
            pass
        out[e] = list(seen[0])
    print(json.dumps(out))
""")


def rep_regress_f(ctx: Ctx, i: int) -> dict:
    res = {}
    for side, tree in (("old", _old_tree(ctx)), ("new", ROOT)):
        r = subprocess.run([ctx.python, "-c", ARGV_SNIPPET], env=ctx.env(tree),
                           capture_output=True, text=True, check=False)  # fmt: skip
        res[side] = json.loads(r.stdout) if r.returncode == 0 else {"error": r.stderr[-300:]}
    literal = {e: [a.format(e=e, port=PORTS[e]) for a in ARGV[e]] for e in ARGV}
    match = {e: res["old"].get(e) == res["new"].get(e) == literal[e] for e in literal}
    return {"hash": {"match": match}, "match": match, "new": res["new"], "old": res["old"]}


def judge_regress_f(ctx: Ctx, reps: list[dict]) -> tuple[bool, str]:
    m = reps[0]["match"]
    return all(m.values()) and _all_same(reps) == 1, (
        f"{sum(m.values())}/{len(m)} engines: old a4ab4a1 argv == new argv == literal "
        f"({', '.join(f'{e}={v}' for e, v in m.items())})"
    )


# -- U-desc and P-trust (a)


def _descriptor_dict(engine: str = "postgres") -> dict:
    sys.path.insert(0, str(ROOT))
    import copy

    from lattice.spec import engines

    return copy.deepcopy(engines.engine(engine).to_dict())


def rep_trust_a(ctx: Ctx, i: int) -> dict:
    sys.path.insert(0, str(ROOT))
    from lattice.spec.engines import EngineError, parse_descriptor

    sql_ok = {"counter": "sql", "source": "s", "read": "SELECT q, c FROM t WHERE d = {db}"}
    cases = {
        "counter = evil:Code": ({**sql_ok, "counter": "evil:Code"},
                                "E: perf.counter: must be \"general_log\" or \"sql\", not "
                                "'evil:Code'"),
        "[perf] docker_args": ({**sql_ok, "docker_args": ["--privileged"]},
                               "E: [perf] unknown field 'docker_args' (known: counter, source, "
                               "server_args, setup, check, begin, mark, flush, read)"),
        "read without {db}": ({**sql_ok, "read": "SELECT 1"},
                              "E: perf.read: must use {db} (the measured database; the tool's "
                              "own statements are excluded by it)"),
        "setup with {db}": ({**sql_ok, "setup": ["SELECT {db}"]},
                            "E: perf.setup: unknown placeholder {db} (known: none)"),
        "general_log with read": ({"counter": "general_log", "read": "SELECT {db}"},
                                  'E: perf.read: only for counter = "sql"'),
        "{mark} without mark": ({**sql_ok, "read": "SELECT {db}, {mark}"},
                                "E: perf.read: {mark} needs perf.mark"),
    }  # fmt: skip
    got: dict[str, str] = {}
    for name, (perf, _want) in cases.items():
        data = _descriptor_dict()
        data["perf"] = perf
        try:
            parse_descriptor(data, "E")
            got[name] = "ACCEPTED"
        except EngineError as err:
            got[name] = str(err)
    return {"hash": got, "got": got, "want": {k: v[1] for k, v in cases.items()}}


def judge_trust_a(ctx: Ctx, reps: list[dict]) -> tuple[bool, str]:
    r = reps[0]
    ok_n = sum(r["got"][k] == w for k, w in r["want"].items())
    bad = {k: r["got"][k] for k, w in r["want"].items() if r["got"][k] != w}
    return ok_n == 6 and _all_same(reps) == 1, (
        f"{ok_n}/6 refused with the exact message" + (f"; differing: {bad}" if bad else "")
    )


# -- U-desc: the built-in descriptors carry the pre-registered [perf]


def rep_desc(ctx: Ctx, i: int) -> dict:
    sys.path.insert(0, str(ROOT))
    from lattice.spec import engines

    out: dict[str, Any] = {}
    for name in ("postgres", "mysql", "clickhouse"):
        e = engines.engine(name)
        out[name] = {"perf": e.to_dict().get("perf"),
                     "round_trip": engines.parse_descriptor(e.to_dict()) == e}  # fmt: skip
    return {"hash": out, "descriptors": out}


def judge_desc(ctx: Ctx, reps: list[dict]) -> tuple[bool, str]:
    d = reps[0]["descriptors"]
    pg, ch, my = d["postgres"]["perf"], d["clickhouse"]["perf"], d["mysql"]["perf"]
    checks = {
        "pg counter": pg["counter"] == "sql",
        "pg preload": pg["server_args"]
        == [
            "-c",
            "shared_preload_libraries=pg_stat_statements",
            "-c",
            "pg_stat_statements.track=top",
            "-c",
            "pg_stat_statements.track_utility=on",
        ],
        "pg setup": pg["setup"] == ["CREATE EXTENSION IF NOT EXISTS pg_stat_statements"],
        "pg check": pg["check"] == ["SELECT count(*) FROM pg_stat_statements"],
        "pg begin": len(pg["begin"]) == 1 and "pg_stat_statements_reset(0::oid" in pg["begin"][0],
        "pg read": pg["read"].endswith("AND toplevel ORDER BY query, queryid")
        and "{db}" in pg["read"],  # fmt: skip
        "pg no mark/flush": pg["mark"] == "" and pg["flush"] == [],
        "ch counter": ch["counter"] == "sql" and ch["server_args"] == [] and ch["setup"] == [],
        "ch check": ch["check"]
        == ["SYSTEM FLUSH LOGS", "SELECT count() FROM system.query_log WHERE 0"],
        "ch mark": ch["mark"] == "SELECT toUnixTimestamp64Micro(now64(6))",
        "ch flush": ch["flush"] == ["SYSTEM FLUSH LOGS"],
        "ch read": "SETTINGS final = 0" in ch["read"]
        and "{db}" in ch["read"]
        and "{mark}" in ch["read"],  # fmt: skip
        "mysql": my == {"counter": "general_log"},
        "round trips": all(v["round_trip"] for v in d.values()),
    }
    bad = [k for k, v in checks.items() if not v]
    return not bad and _all_same(reps) == 1, (
        f"{sum(checks.values())}/{len(checks)} descriptor facts as in decisions 4, 5 and 2" +
        (f"; wrong: {bad}" if bad else ""))  # fmt: skip


# -- U-noperf: an engine whose descriptor has no [perf] starts no server


def rep_noperf(ctx: Ctx, i: int) -> dict:
    mariadb = ROOT / "docs/examples/engines/mariadb.toml"
    project = server_project(ctx, "mariadb", ids=["items"], database="mariadb")
    # the engine is registered by the launcher (--engine-file): `lattice perf` itself does not
    # read a project's [engines] paths (recorded below, not judged)
    r = ctx.launch(project, "measure", ["--json", "--runs", "1"],
                   flags=["--engine-file", str(mariadb)])  # fmt: skip
    bare = ctx.plain(project, "measure", ["--json", "--runs", "1"])
    notes = [x["notes"] for x in (r.data or {}).get("reports", [])]
    return {"hash": {"rc": r.rc, "data": canon(r.data)}, "rc": r.rc, "notes": notes,
            "popen": r.audit, "stderr": r.err[-300:],
            "without_engine_file": {"rc": bare.rc, "stderr": bare.err.strip()[-240:]}}  # fmt: skip


def judge_noperf(ctx: Ctx, reps: list[dict]) -> tuple[bool, str]:
    r = reps[0]
    want = ["unmeasured: statement counting on MariaDB is not supported (its engine descriptor "
            "has no [perf] section)"]  # fmt: skip
    ok = r["rc"] == 2 and r["notes"] == [want] and r["popen"] == [] and _all_same(reps) == 1
    return ok, (f"exit {r['rc']}; notes {r['notes']}; {len(r['popen'] or [])} subprocess "
                f"(no server started)")  # fmt: skip


# -- P-pg / P-ch


def rep_counted(engine: str) -> Callable[[Ctx, int], dict]:
    def rep(ctx: Ctx, i: int) -> dict:
        variants = ["inproc"] if ctx.fake else ["inproc", "process"]
        out: dict[str, Any] = {"variants": {}}
        hashes: dict[str, Any] = {}
        left: list[str] = []
        for v in variants:
            project = server_project(ctx, engine, process=(v == "process"))
            common = ["--runs", str(ctx.mruns), "--sizes", ",".join(map(str, SIZES))]
            run = ctx.launch if ctx.fake else ctx.plain
            chk = run(project, "check", ["--json", *common, "--statements"])
            mea = run(project, "measure", ["--json", *common])
            ok, total, problems = judge_reports(chk.data, engine, ctx.mruns)
            left += ctx.left(chk) + ctx.left(mea)
            err = (chk.err + mea.err)[-300:]
            out["variants"][v] = {"check_rc": chk.rc, "measure_rc": mea.rc, "ok": ok,
                                  "total": total, "problems": problems, "stderr": err,
                                  "raw": chk.data}  # fmt: skip
            hashes[v] = canon(chk.data)
        out["hash"] = hashes
        out["same_across_variants"] = len({chash(h) for h in hashes.values()}) == 1
        out["left"] = left
        return out

    return rep


def judge_counted(engine: str) -> Callable[[Ctx, list[dict]], tuple[bool, str]]:
    def judge(ctx: Ctx, reps: list[dict]) -> tuple[bool, str]:
        ok = total = 0
        problems: list[str] = []
        exits_ok = 0
        n_inv = 0
        for r in reps:
            for v in r["variants"].values():
                n_inv += 1
                ok += v["ok"]
                total += v["total"]
                problems += v["problems"]
                exits_ok += (v["check_rc"], v["measure_rc"]) == (1, 0)
        distinct = _all_same(reps)
        left = sum(len(r["left"]) for r in reps)
        passed = (ok == total and not problems and exits_ok == n_inv and distinct == 1
                  and all(r["same_across_variants"] for r in reps) and left == 0)  # fmt: skip
        return passed, (f"{ok}/{total} runs as predicted; exits check 1 / measure 0 in "
                        f"{exits_ok}/{n_inv}; {distinct} distinct canonical hash over {n_inv}; "
                        f"{left} container(s) left" + (f"; problems: {problems[:3]}" if problems
                                                       else ""))  # fmt: skip

    return judge


# -- P-excl


def rep_excl_a(engine: str) -> Callable[[Ctx, int], dict]:
    def rep(ctx: Ctx, i: int) -> dict:
        variants = ["inproc"] if ctx.fake else ["inproc", "process"]
        zeros = runs = 0
        hashes = {}
        for v in variants:
            project = server_project(ctx, engine, process=(v == "process"), ids=["nodb"])
            run = ctx.launch if ctx.fake else ctx.plain
            r = run(project, "check", ["--json", "--runs", str(ctx.mruns), "--sizes", "3,7"])
            for rep_ in (r.data or {}).get("reports", []):
                for s in rep_["sizes"]:
                    runs += len(s["statements"])
                    zeros += sum(c == 0 for c in s["statements"])
            hashes[v] = canon(r.data)
        return {"hash": hashes, "zeros": zeros, "runs": runs}

    return rep


def judge_excl_a(ctx: Ctx, reps: list[dict]) -> tuple[bool, str]:
    z, n = sum(r["zeros"] for r in reps), sum(r["runs"] for r in reps)
    return z == n and n > 0 and _all_same(reps) == 1, f"nodb = 0 in {z}/{n} runs"


def rep_excl_b(engine: str) -> Callable[[Ctx, int], dict]:
    def rep(ctx: Ctx, i: int) -> dict:
        project = server_project(ctx, engine, ids=["items"])
        run = ctx.launch(project, "check", ["--json", "--runs", str(ctx.mruns), "--sizes", "3,7"],
                         flags=["--noisy"])  # fmt: skip
        counts = [c for r in (run.data or {}).get("reports", []) for s in r["sizes"]
                  for c in s["statements"]]  # fmt: skip
        return {"hash": canon(run.data), "counts": counts, "rc": run.rc}

    return rep


def judge_excl_b(ctx: Ctx, reps: list[dict]) -> tuple[bool, str]:
    n = sum(len(r["counts"]) for r in reps)
    two = sum(c == 2 for r in reps for c in r["counts"])
    return two == n and n > 0 and _all_same(reps) == 1, (
        f"GET /items = 2 in {two}/{n} runs (5 + 5 noise statements per window)"
    )


def rep_excl_c(ctx: Ctx, i: int) -> dict:
    out = {}
    for engine in ENGINES:
        project = server_project(ctx, engine, ids=["items"])
        r = ctx.launch(project, "measure", [], flags=["--window-probe", engine])
        out[engine] = (r.data or {}).get("rows")
    return {"hash": {k: (v is not None and v >= 1) for k, v in out.items()}, "rows": out}


def judge_excl_c(ctx: Ctx, reps: list[dict]) -> tuple[bool, str]:
    rows = [r["rows"] for r in reps]
    ok = all(v is not None and v >= 1 for r in rows for v in r.values())
    return ok and _all_same(reps) == 1, (
        f"admin-database window returned >= 1 row on both engines: {rows[0]}"
    )


# -- P-down


def rep_down_a(ctx: Ctx, i: int) -> dict:
    out = {}
    path = "/usr/bin:/bin"
    seen = {b: shutil.which(b, path=path) for b in ("docker", "postgres", "clickhouse")}
    for engine in ENGINES:
        project = server_project(ctx, engine, ids=["items"])
        r = ctx.plain(project, "check", ["--json"], extra_env={"PATH": path})
        out[engine] = {"rc": r.rc, "stderr": r.err.strip().splitlines()[-1:] or [""]}
    return {"hash": out, "out": out, "binaries_on_path": seen}


def judge_down_a(ctx: Ctx, reps: list[dict]) -> tuple[bool, str]:
    r = reps[0]
    found = {k: v for k, v in r["binaries_on_path"].items() if v}
    ok = (not found and all(v["rc"] == 2 and "needs Docker" in " ".join(v["stderr"])
                            for x in reps for v in x["out"].values())
          and _all_same(reps) == 1)  # fmt: skip
    hits = sum("needs Docker" in " ".join(v["stderr"]) for x in reps for v in x["out"].values())
    return ok, (
        f"exit {[v['rc'] for v in r['out'].values()]}, stderr contains 'needs Docker' in "
        f"{hits}/{2 * len(reps)}; no docker CLI on PATH, so 0 containers can have "
        f"started" + (f"; BINARIES FOUND {found}" if found else "")
    )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def rep_down_b(ctx: Ctx, i: int) -> dict:
    project = server_project(ctx, "postgres", ids=["items"])
    url = f"postgresql://lattice:lattice@127.0.0.1:{_free_port()}/postgres?sslmode=disable"
    r = ctx.plain(project, "check", ["--json"], extra_env={"LATTICE_POSTGRES_URL": url},
                  timeout=120)  # fmt: skip
    return {"hash": {"rc": r.rc, "err": r.err.strip().splitlines()[-1:]},
            "rc": r.rc, "seconds": r.seconds, "stderr": r.err.strip()[-300:]}  # fmt: skip


def judge_down_b(ctx: Ctx, reps: list[dict]) -> tuple[bool, str]:
    ok = all(r["rc"] == 2 and r["seconds"] < 40 and "did not answer within 30s" in r["stderr"]
             for r in reps)  # fmt: skip
    return ok, (f"exit {reps[0]['rc']} in {max(r['seconds'] for r in reps)} s; stderr: "
                f"{reps[0]['stderr'][-120:]!r}")  # fmt: skip


def rep_down_c(ctx: Ctx, i: int) -> dict:
    """A given Postgres without pgss: unmeasured, and no CREATE EXTENSION was run on it."""
    project = server_project(ctx, "postgres", ids=["items"])
    out: dict[str, Any] = {}
    if ctx.fake:
        flags = ["--given", "postgres:nopgss"]
        extra: dict[str, str] = {}
        cleanup: Callable[[], None] = lambda: None  # noqa: E731
        ext_rows: Callable[[Run], int] = lambda r: (r.world or {}).get("extension_rows", -1)  # noqa: E731
    else:
        flags, extra, cleanup, ext_rows = _plain_pg(ctx)
    runs = {}
    try:
        for cmd in ("measure", "check"):
            runs[cmd] = ctx.launch(project, cmd, ["--json", "--runs", "1"], flags=flags,
                                   extra_env=extra)  # fmt: skip
        for cmd, r in runs.items():
            reports = (r.data or {}).get("reports", [])
            out[cmd] = {"rc": r.rc, "verdicts": [x["verdict"] for x in reports],
                        "notes": [x["notes"] for x in reports]}  # fmt: skip
        out["extension_rows"] = ext_rows(runs["check"])
    finally:
        cleanup()
    return {"hash": out, **out}


def _plain_pg(ctx: Ctx):  # pragma: no cover -- needs Docker and psycopg
    """Start a plain postgres:16-alpine as lattice-probe-pg-<hex>; (flags, env, cleanup, rows)."""
    name = f"lattice-probe-pg-{uuid.uuid4().hex[:8]}"
    subprocess.run(["docker", "run", "-d", "--name", name, "--label",
                    f"lattice.owner={ctx.owner}", "-p", "127.0.0.1::5432", "-e",
                    "POSTGRES_USER=lattice", "-e", "POSTGRES_PASSWORD=lattice", "--tmpfs",
                    "/var/lib/postgresql/data:rw", "postgres:16-alpine"],
                   check=True, capture_output=True)  # fmt: skip
    port = subprocess.run(["docker", "port", name, "5432/tcp"], capture_output=True, text=True,
                          check=True).stdout.strip().rsplit(":", 1)[1]  # fmt: skip
    url = f"postgresql://lattice:lattice@127.0.0.1:{port}/postgres?sslmode=disable"
    snippet = (
        "import sys, time, psycopg\nfor _ in range(120):\n    try:\n"
        "        c = psycopg.connect(sys.argv[1], autocommit=True); break\n"
        "    except Exception:\n        time.sleep(0.5)\n"
        'print(c.execute("SELECT count(*) FROM pg_extension WHERE extname = '
        "'pg_stat_statements'\").fetchone()[0])\n"
    )

    def rows(_r: Run) -> int:
        r = subprocess.run([ctx.python, "-c", snippet, url], capture_output=True, text=True,
                           check=False)  # fmt: skip
        return int(r.stdout.strip()) if r.returncode == 0 else -1

    def cleanup() -> None:
        subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, check=False)

    # wait until it answers before the runs (rows() waits too)
    rows(Run(0, "", "", 0))
    return [], {"LATTICE_POSTGRES_URL": url}, cleanup, rows


def judge_down_c(ctx: Ctx, reps: list[dict]) -> tuple[bool, str]:
    ok = True
    for r in reps:
        for cmd in ("measure", "check"):
            c = r[cmd]
            notes = " ".join(" ".join(n) for n in c["notes"])
            ok = (
                ok
                and c["rc"] == 2
                and c["verdicts"]
                and set(c["verdicts"]) == {"unmeasured"}
                and "not available on this server" in notes
                and "pg_stat_statements" in notes
            )
        ok = ok and r["extension_rows"] == 0
    r0 = reps[0]
    return bool(ok) and _all_same(reps) == 1, (
        f"measure exit {r0['measure']['rc']}, check exit {r0['check']['rc']}, verdicts "
        f"{r0['check']['verdicts']}, pg_extension rows afterwards {r0['extension_rows']}"
    )


def rep_down_d(ctx: Ctx, i: int) -> dict:
    project = server_project(ctx, "postgres", ids=["items"])
    r = ctx.launch(project, "check", ["--json", "--runs", "2"], flags=["--kill-after-first-size"])
    return {"hash": {"rc": r.rc, "stderr": r.err.strip().splitlines()[:1]}, "rc": r.rc,
            "stderr": r.err.strip()[:300], "left": ctx.left(r)}  # fmt: skip


def judge_down_d(ctx: Ctx, reps: list[dict]) -> tuple[bool, str]:
    ok = all(r["rc"] == 2 and r["stderr"].startswith("lattice perf: error:") and not r["left"]
             for r in reps)  # fmt: skip
    return ok, (f"exit {reps[0]['rc']}; stderr {reps[0]['stderr'][:80]!r}; containers left "
                f"{sum(len(r['left']) for r in reps)}")  # fmt: skip


def rep_down_e(ctx: Ctx, i: int) -> dict:
    out = {}
    left: list[str] = []
    for engine in ENGINES:
        project = silent_project(ctx, engine)
        r = ctx.launch(project, "check", ["--json", "--runs", "2", "--sizes", "3,7"])
        reports = (r.data or {}).get("reports", [])
        out[engine] = {"rc": r.rc, "verdicts": [x["verdict"] for x in reports],
                       "notes": [x["notes"] for x in reports],
                       "sizes": [x["sizes"] for x in reports]}  # fmt: skip
        left += ctx.left(r)
    return {"hash": out, "out": out, "left": left}


def judge_down_e(ctx: Ctx, reps: list[dict]) -> tuple[bool, str]:
    ok = True
    for r in reps:
        ok = ok and not r["left"]
        for v in r["out"].values():
            ok = ok and v["rc"] == 2 and v["verdicts"] == ["unmeasured", "unmeasured"] \
                and all(s == [] for s in v["sizes"]) and all(
                    any(n.startswith("unmeasured: no statement of the app was seen in database ")
                        and "while seeding (N=3, 3 seed requests)" in n for n in ns)
                    for ns in v["notes"])  # fmt: skip
    v0 = reps[0]["out"]
    return bool(ok) and _all_same(reps) == 1, (
        f"every budget unmeasured with the liveness note on both engines (exit "
        f"{[v['rc'] for v in v0.values()]}); {_all_same(reps)} distinct hash over {len(reps)}"
    )


# -- P-trust (b), (c)

DOCKER_VERBS = {"run", "port", "inspect", "logs", "rm"}


def rep_trust_b(engine: str) -> Callable[[Ctx, int], dict]:
    def rep(ctx: Ctx, i: int) -> dict:
        project = server_project(ctx, engine, ids=["items"])
        r = ctx.launch(project, "check", ["--json", "--runs", "2", "--sizes", "3,7"])
        calls = [c[1:] if c and c[0] == "docker" else c for c in (r.audit or [])]
        if ctx.fake:
            calls = [list(c) for c in (r.world or {}).get("docker_calls", [])]
        return {"hash": {"verbs": [c[0] for c in calls if c], "rc": r.rc}, "calls": calls,
                "rc": r.rc, "non_docker": [c for c in (r.audit or []) if c and c[0] != "docker"]
                if not ctx.fake else []}  # fmt: skip

    return rep


def judge_trust_b(engine: str) -> Callable[[Ctx, list[dict]], tuple[bool, str]]:
    def judge(ctx: Ctx, reps: list[dict]) -> tuple[bool, str]:
        sys.path.insert(0, str(ROOT))
        from lattice.spec import engines

        e = engines.engine(engine)
        want_args = [*e.server.args, *e.perf.server_args]
        ok = True
        detail = ""
        for r in reps:
            calls = r["calls"]
            verbs = [c[0] for c in calls if c]
            runs = [c for c in calls if c and c[0] == "run"]
            ok = ok and set(verbs) <= DOCKER_VERBS and "exec" not in verbs and len(runs) == 1
            ok = ok and not r["non_docker"]
            if runs:
                run = runs[0]
                at = run.index(e.server.image) if e.server.image in run else -1
                name = run[run.index("--name") + 1]
                ok = (
                    ok
                    and at >= 0
                    and run[at + 1 :] == want_args
                    and re.fullmatch(rf"lattice-perf-{engine}-[0-9a-f]{{8}}", name) is not None
                )
                detail = f"image {run[at] if at >= 0 else None}, args after image == descriptor"
        verbs0 = sorted({c[0] for c in reps[0]["calls"] if c})
        return ok and _all_same(reps) == 1, (
            f"verbs {verbs0} (no exec), 1 run, {detail}"
            + ("; via the fake docker's recorded argv" if ctx.fake else "")
        )

    return judge


def rep_trust_c(engine: str) -> Callable[[Ctx, int], dict]:
    def rep(ctx: Ctx, i: int) -> dict:
        project = server_project(ctx, engine)
        r = ctx.launch(project, "check", ["--json", "--runs", str(ctx.mruns), "--sizes", "3,7"])
        return {"hash": {"n": len(r.stmts or []), "rc": r.rc}, "stmts": r.stmts or [], "rc": r.rc}

    return rep


def judge_trust_c(engine: str) -> Callable[[Ctx, list[dict]], tuple[bool, str]]:
    def judge(ctx: Ctx, reps: list[dict]) -> tuple[bool, str]:
        sys.path.insert(0, str(ROOT))
        from lattice.perf.measure import bind
        from lattice.spec import engines

        e = engines.engine(engine)
        p = e.perf
        ph = e.sql.placeholder
        texts = [*p.setup, *p.check, *p.begin, *p.flush, p.read, *([p.mark] if p.mark else [])]
        allowed = {bind(t, ph, {"db": "x", "mark": 1})[0] for t in texts}
        windows = len(TABLE) * len(SIZES) * (ctx.mruns + 1)
        per_window = len(p.begin) + (1 if p.mark else 0) + len(p.flush) + 1
        want = len(p.setup) + len(p.check) + windows * per_window
        ok = True
        for r in reps:
            ok = ok and len(r["stmts"]) == want and all(s[0] in allowed for s in r["stmts"])
            ok = ok and all(
                re.fullmatch(r"lattice_[0-9a-f]{8}_c\d+", a) is not None
                for s in r["stmts"]
                for a in s[1]
                if isinstance(a, str)
            )  # the bound db is always a clone
        return ok and _all_same(reps) == 1, (
            f"{len(reps[0]['stmts'])} statements sent (predicted {want} = setup "
            f"{len(p.setup)} + check {len(p.check)} + {windows} windows x {per_window}); every "
            f"one a descriptor statement; every {{db}} a clone"
        )

    return judge


# ---- the probe list ------------------------------------------------------------------------------

ALL_PROBES: list[Probe] = []


def _add(id_: str, title: str, predicted: str, needs: tuple[str, ...], rep: Any, judge: Any,
         fake: bool = False) -> None:  # fmt: skip
    ALL_PROBES.append(Probe(id_, title, predicted, needs, rep, judge, fake))


def _build() -> None:
    A = _add
    A("P-regress(a)", "file and AST identity vs a4ab4a1",
      "0 differences: budget.py and __main__.py byte-identical, 10 definitions AST-identical",
      (), rep_regress_a, judge_regress_a)  # fmt: skip
    A("P-regress(b)", "existing tests/perf unchanged and passing",
      "0 changed test definitions, 68 passed, test_engines.py +1/-1 line",
      (), rep_regress_b, judge_regress_b)  # fmt: skip
    A(
        "P-regress(c)",
        "SQLite in-process, old vs new, 10x each",
        "1 distinct hash over 20; counts 2/2 and 4/8",
        (),
        rep_regress_c,
        judge_regress_c,
    )
    A(
        "P-regress(d)",
        "process app on SQLite stays unmeasured",
        "same unmeasured note old and new, exit 2",
        (),
        rep_regress_d,
        judge_regress_d,
    )
    A("P-regress(e)", "MySQL on Streak, old vs new, 10x each",
      "1 distinct hash over 20, exit 0 x20, counts 11/31 and 4/4, fits 6+1N and 4+0N exact",
      ("docker", "pymysql", "streak"), rep_regress_e, judge_regress_e)  # fmt: skip
    A(
        "P-regress(f)",
        "start_container argv unchanged without extra_args",
        "3/3 engines equal the a4ab4a1 argv",
        (),
        rep_regress_f,
        judge_regress_f,
    )
    A(
        "U-desc",
        "built-in descriptors carry the pre-registered [perf]",
        "14/14 descriptor facts, round trips",
        (),
        rep_desc,
        judge_desc,
    )
    A(
        "U-noperf",
        "an engine without [perf] is unmeasured, no server started",
        "exit 2, the decision-3 note, 0 subprocess",
        (),
        rep_noperf,
        judge_noperf,
    )
    A(
        "P-trust(a)",
        "[perf] cannot name code or docker run options",
        "6/6 refused with the exact message",
        (),
        rep_trust_a,
        judge_trust_a,
    )
    pg_pred = ("counts 2/2, 5/9, 5/9, 0/0 in every run; fits 2+0N, 2+1N, 2+1N, 0+0N exact; "
               "verdicts pass, fail, pass, pass; exits check 1, measure 0; 1 distinct hash; "
               "0 containers left")  # fmt: skip
    for engine, tag in (("postgres", "P-pg"), ("clickhouse", "P-ch")):
        drv = "psycopg" if engine == "postgres" else "clickhouse_connect"
        A(tag, f"{engine} counted exactly (in-process and process app)", pg_pred,
          ("docker", drv), rep_counted(engine), judge_counted(engine))  # fmt: skip
        A(f"{tag}/fake", f"{engine} counted exactly on a simulated server (tool logic only)",
          pg_pred, (), rep_counted(engine), judge_counted(engine), fake=True)  # fmt: skip
    A("P-excl(a)", "reset/mark/flush/read never counted (nodb = 0)",
      "nodb = 0 in every run on both engines", ("docker", "psycopg", "clickhouse_connect"),
      lambda c, i: _both(c, i, rep_excl_a), judge_excl_a)  # fmt: skip
    A(
        "P-excl(a)/fake",
        "nodb = 0 on simulated servers (tool logic only)",
        "nodb = 0 in every run",
        (),
        lambda c, i: _both(c, i, rep_excl_a),
        judge_excl_a,
        fake=True,
    )
    A("P-excl(b)", "a noisy tool's statements are not counted",
      "GET /items = 2 in every run on both engines", ("docker", "psycopg", "clickhouse_connect"),
      lambda c, i: _both(c, i, rep_excl_b, merge=True), judge_excl_b)  # fmt: skip
    A("P-excl(b)/fake", "a noisy tool on simulated servers (tool logic only)",
      "GET /items = 2 in every run", (), lambda c, i: _both(c, i, rep_excl_b, merge=True),
      judge_excl_b, fake=True)  # fmt: skip
    A("P-excl(c)", "the tool's statements are logged and filtered by {db}",
      ">= 1 row for the admin database on both engines",
      ("docker", "psycopg", "clickhouse_connect"), rep_excl_c, judge_excl_c)  # fmt: skip
    A("P-down(a)", "no docker on PATH", "exit 2, stderr 'needs Docker', 10 runs identical",
      (), rep_down_a, judge_down_a)  # fmt: skip
    A("P-down(b)", "a given URL at a closed port",
      "exit 2 within 40 s, stderr 'did not answer within 30s'", ("psycopg",),
      rep_down_b, judge_down_b)  # fmt: skip
    A("P-down(c)", "a given Postgres without pgss is unmeasured, no setup",
      "measure and check exit 2, all unmeasured with 'not available on this server' and "
      "pg_stat_statements, 0 pg_extension rows", ("docker", "psycopg"), rep_down_c,
      judge_down_c)  # fmt: skip
    A("P-down(c)/fake", "a given simulated Postgres without pgss (tool logic only)",
      "measure and check exit 2, all unmeasured with the note, no CREATE EXTENSION sent", (),
      rep_down_c, judge_down_c, fake=True)  # fmt: skip
    A("P-down(d)", "the container is docker-killed after the first size",
      "exit 2, 'lattice perf: error:', 0 containers left", ("docker", "psycopg"),
      rep_down_d, judge_down_d)  # fmt: skip
    A("P-down(d)/fake", "the simulated server dies after the first size (tool logic only)",
      "exit 2, 'lattice perf: error:', the container removed", (), rep_down_d, judge_down_d,
      fake=True)  # fmt: skip
    A("P-down(e)", "an app that ignores its URL is unmeasured (liveness guard)",
      "every budget unmeasured with the decision-8 note, exit 2, identical",
      ("docker", "psycopg", "clickhouse_connect"), rep_down_e, judge_down_e)  # fmt: skip
    A("P-down(e)/fake", "liveness guard on simulated servers (tool logic only)",
      "every budget unmeasured with the decision-8 note, exit 2, identical", (), rep_down_e,
      judge_down_e, fake=True)  # fmt: skip
    for engine, drv in (("postgres", "psycopg"), ("clickhouse", "clickhouse_connect")):
        short = "pg" if engine == "postgres" else "ch"
        A(f"P-trust(b)-{short}", f"{engine}: only docker run/port/inspect/logs/rm, no exec",
          "verbs within the set, no exec, 1 run, image and args as the descriptor",
          ("docker", drv), rep_trust_b(engine), judge_trust_b(engine))  # fmt: skip
        A(f"P-trust(b)-{short}/fake", f"{engine}: the same on the fake docker's argv",
          "verbs within the set, no exec, 1 run, image and args as the descriptor", (),
          rep_trust_b(engine), judge_trust_b(engine), fake=True)  # fmt: skip
        want = 178 if engine == "postgres" else 266
        A(f"P-trust(c)-{short}", f"{engine}: every statement SqlCounter sends is the descriptor's",
          f"{want} statements, all descriptor statements, every {{db}} a clone",
          ("docker", drv), rep_trust_c(engine), judge_trust_c(engine))  # fmt: skip
        A(f"P-trust(c)-{short}/fake", f"{engine}: the same on a simulated server",
          f"{want} statements, all descriptor statements, every {{db}} a clone", (),
          rep_trust_c(engine), judge_trust_c(engine), fake=True)  # fmt: skip


def _both(ctx: Ctx, i: int, make: Callable[[str], Callable[[Ctx, int], dict]],
          merge: bool = False) -> dict:  # fmt: skip
    """One repetition of ``make(engine)`` for each engine, merged for one judge."""
    parts = {e: make(e)(ctx, i) for e in ENGINES}
    out: dict[str, Any] = {"hash": {e: p["hash"] for e, p in parts.items()}}
    if merge:
        out["counts"] = [c for p in parts.values() for c in p["counts"]]
    else:
        out["zeros"] = sum(p["zeros"] for p in parts.values())
        out["runs"] = sum(p["runs"] for p in parts.values())
    return out


# ---- the launcher ------------------------------------------------------------------------------


def launch(argv: list[str]) -> int:
    """``--launch [flags] -- COMMAND ...``: ``python -m lattice.perf`` with hooks. ``--fake``
    puts the fake servers, drivers and docker of tests/perf/fakeengines.py in this process."""
    sep = argv.index("--")
    flags, rest = argv[:sep], argv[sep + 1 :]
    ap = argparse.ArgumentParser(prog="--launch")
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--given", default=None, help="fake: ENGINE[:nopgss], a given server")
    ap.add_argument("--noisy", action="store_true")
    ap.add_argument("--kill-after-first-size", action="store_true")
    ap.add_argument("--window-probe", default=None)
    ap.add_argument("--world", default=None)
    ap.add_argument("--audit", default=None)
    ap.add_argument("--record", default=None)
    ap.add_argument("--engine-file", action="append", default=[])
    ns = ap.parse_args(flags)
    sys.path.insert(0, str(ROOT))
    from lattice.perf import measure as measure_mod
    from lattice.spec import db as spec_db
    from lattice.spec import dbserver

    if ns.engine_file:
        from lattice.spec.engines import load_engine_file

        for path in ns.engine_file:
            load_engine_file(path, "project")
    popen: list[list[str]] = []

    def hook(event: str, args: tuple) -> None:
        if event == "subprocess.Popen":
            popen.append([str(a) for a in args[1]])

    sys.addaudithook(hook)
    stmts: list[list[Any]] = []

    real_open = spec_db.open_db

    class Recorder:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        def _note(self, sql: str, params: Any) -> None:
            stmts.append([sql, list(params) if params else []])

        def execute(self, sql: str, params: Any = ()) -> Any:
            self._note(sql, params)
            return self.inner.execute(sql, params)

        def query(self, sql: str, params: Any = ()) -> Any:
            self._note(sql, params)
            return self.inner.query(sql, params)

        def scalar(self, sql: str, params: Any = ()) -> Any:
            self._note(sql, params)
            return self.inner.scalar(sql, params)

        def close(self) -> None:
            self.inner.close()

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

    if ns.record:
        spec_db.open_db = lambda ref: Recorder(real_open(ref))  # type: ignore[assignment]

    if ns.noisy:
        base = measure_mod.SqlCounter

        class Noisy(base):  # type: ignore[misc, valid-type]
            def begin(self) -> None:
                super().begin()
                for _ in range(5):
                    self.conn.query("SELECT 1")
                    self.server.admin().query("SELECT 1")

        measure_mod.SqlCounter = Noisy  # type: ignore[misc]

    world = None
    stack = contextlib.ExitStack()
    given = None
    if ns.fake:
        from tests.perf import fakeengines as fe

        world = stack.enter_context(fe.installed())
        if ns.given:
            kind, _, mode = ns.given.partition(":")
            given = world.add_server(kind, preloaded=(mode != "nopgss"))
            os.environ[f"LATTICE_{kind.upper()}_URL"] = given.url()

    if ns.kill_after_first_size:
        real_size = measure_mod.measure_size
        done: list[bool] = []

        def killing(*a: Any, **k: Any) -> Any:
            result = real_size(*a, **k)
            if not done:
                done.append(True)
                for name in dbserver.live_containers():
                    if world is not None:
                        world.kill(name)
                    else:
                        subprocess.run(["docker", "kill", name], capture_output=True, check=False)
            return result

        measure_mod.measure_size = killing  # type: ignore[assignment]

    try:
        if ns.window_probe:
            rc = _window_probe(ns.window_probe, measure_mod, dbserver)
        else:
            from lattice.perf.__main__ import main

            rc = main(rest)
    finally:
        # what the hooks saw is written before the fakes are removed
        if ns.audit:
            Path(ns.audit).write_text(json.dumps(popen))
        if ns.record:
            Path(ns.record).write_text(json.dumps(stmts))
        if ns.world and world is not None:
            rows = -1
            if given is not None:
                created = bool(given.extensions) or any("CREATE EXTENSION" in e[2]
                                                        for e in given.events)  # fmt: skip
                rows = 1 if created else 0
            Path(ns.world).write_text(json.dumps({
                "started": sorted(world.containers), "removed": list(world.removed),
                "docker_calls": [list(c) for c in world.docker_calls],
                "extension_rows": rows}))  # fmt: skip
        stack.close()
    return rc


def _window_probe(engine: str, measure_mod: Any, dbserver: Any) -> int:
    """P-excl(c): in a window on the admin database, the read returns the tool's statements."""
    from lattice.spec import engines

    e = engines.engine(engine)
    server, owned = dbserver.server_for(engine, None, name_prefix="lattice-perf-",
                                        extra_args=e.perf.server_args)  # fmt: skip
    counter = measure_mod.SqlCounter(server, e)
    try:
        counter.start(owned)
        counter.watch(server.url)
        counter.begin()
        server.admin().query("SELECT 1")
        rows = counter.end()
        print(json.dumps({"rows": len(rows), "engine": engine}))
    finally:
        counter.stop()
        server.stop()
    return 0


# ---- the runner --------------------------------------------------------------------------------


def env_status(python: str) -> dict[str, bool]:
    sys.path.insert(0, str(ROOT))
    from lattice.spec.dbserver import docker_available

    have = {"docker": docker_available()}
    for mod in ("psycopg", "clickhouse_connect", "pymysql"):
        r = subprocess.run([python, "-c", f"import {mod}"], capture_output=True, check=False)
        have[mod] = r.returncode == 0
    have["streak"] = shutil.which("go") is not None and STREAK_SRC.is_dir()
    return have


def missing_reason(p: Probe, have: dict[str, bool]) -> str | None:
    lacking = [n for n in p.needs if not have.get(n)]
    if not lacking:
        return None
    what = {
        "docker": "a running Docker daemon",
        "psycopg": "psycopg in the probe python",
        "clickhouse_connect": "clickhouse-connect in the probe python",
        "pymysql": "pymysql in the probe python",
        "streak": "go and the Streak repository",
    }
    return "needs " + ", ".join(what[n] for n in lacking)


def run_probe(p: Probe, ctx: Ctx) -> dict[str, Any]:
    t0 = time.monotonic()
    ctx.fake = p.fake
    ctx.owner = f"perf-probe-{uuid.uuid4().hex[:8]}"
    reps: list[dict] = []
    error = None
    try:
        for i in range(ctx.runs):
            reps.append(p.rep(ctx, i))
        hashes = [chash(r["hash"]) for r in reps]
        ok, observed = p.judge(ctx, reps)
    except Exception:  # noqa: BLE001 -- a probe script bug is reported, not hidden
        error = traceback.format_exc()
        hashes, ok, observed = (
            [],
            False,
            "ERROR in the probe script: " + error.strip().splitlines()[-1],
        )
    return {"id": p.id, "status": "RAN", "title": p.title, "predicted": p.predicted,
            "observed": observed, "pass": bool(ok), "repetitions": len(reps),
            "hashes": hashes, "distinct_hashes": len(set(hashes)),
            "seconds": round(time.monotonic() - t0, 1), "error": error,
            "facts": reps[0] if reps else None}  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--launch":
        return launch(argv[1:])
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", type=int, default=10, help="repetitions per probe (default 10)")
    ap.add_argument("--measure-runs", type=int, default=10, help="--runs of each CLI invocation")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--only", nargs="*", default=None, help="probe ids (fnmatch patterns)")
    ap.add_argument("--python", default=sys.executable, help="probe interpreter (scratch venv)")
    ap.add_argument("--streak", type=Path, default=None, help="a prepared Streak clone")
    ap.add_argument("--scratch", type=Path, default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="run probes whose environment is missing anyway (script debugging: "
                    "status FORCED, never RAN, never a pass)")  # fmt: skip
    ap.add_argument("--mutants", action="store_true", help="run PX1-PX11 (perf_mutants.py)")
    ap.add_argument("--jobs", type=int, default=2)
    args = ap.parse_args(argv)
    if args.mutants:
        return run_mutants(args)
    _build()
    patterns = args.only or ["*"]
    probes = [p for p in ALL_PROBES if any(fnmatch.fnmatchcase(p.id, pat) for pat in patterns)]
    if args.list:
        for p in probes:
            print(f"{p.id} | needs {', '.join(p.needs) or '-'} | {p.title}")
        return 0
    scratch = args.scratch or Path(tempfile.mkdtemp(prefix="perf-engines-probes-"))
    scratch.mkdir(parents=True, exist_ok=True)
    have = env_status(args.python)
    print("environment:", json.dumps(have), flush=True)
    ctx = Ctx(scratch, args.python, args.runs, args.measure_runs, streak=args.streak)
    results: list[dict[str, Any]] = []
    for p in probes:
        reason = missing_reason(p, have)
        if reason and args.force:
            res = run_probe(p, ctx)
            res.update(status="FORCED", pass_=res["pass"], pass_note=f"({reason})")
            res["pass"] = None
            res["observed"] = f"FORCED without {reason[6:]}: {res['observed']}"
        elif reason:
            res = {"id": p.id, "status": "NOT RUN", "title": p.title, "predicted": p.predicted,
                   "observed": f"not run: {reason}", "pass": None, "reason": reason}  # fmt: skip
        else:
            res = run_probe(p, ctx)
        results.append(res)
        passed = {True: "PASS", False: "FAIL", None: "-"}[res["pass"]]
        print(f"{res['id']} | {res['status']} | {res['predicted']} | {res['observed']} | {passed}",
              flush=True)  # fmt: skip
    n_pass = sum(1 for r in results if r["pass"] is True)
    n_fail = sum(1 for r in results if r["pass"] is False)
    n_not = sum(1 for r in results if r["status"] == "NOT RUN")
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        for r in results:
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", r["id"]).strip("_")
            (args.out / f"{safe}.json").write_text(json.dumps(r, indent=1, default=str))
        summary = {"environment": have, "runs": args.runs, "measure_runs": args.measure_runs,
                   "probes": [{k: r[k] for k in ("id", "status", "predicted", "observed", "pass",
                                                  "distinct_hashes", "seconds")
                               if k in r} | ({"reason": r["reason"]} if "reason" in r else {})
                              for r in results],
                   "passed": n_pass, "failed": n_fail, "not_run": n_not,
                   "total": len(results)}  # fmt: skip
        (args.out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(f"probes: {n_pass}/{len(results)} pass, {n_not} not run", flush=True)
    return 1 if n_fail else (3 if n_not else 0)


def run_mutants(args: argparse.Namespace) -> int:
    """The PX mutants of dogfood/perf_mutants.py (control first); ends with its summary."""
    sys.path.insert(0, str(HERE.parent))
    import perf_mutants  # type: ignore[import-not-found]

    ids = [m for m in perf_mutants.MUTANTS if m.startswith("PX")]
    argv = [*ids, "--jobs", str(args.jobs)]
    if args.out:
        argv += ["--out", str(args.out)]
    if args.python != sys.executable:
        argv += ["--python", args.python]
    return int(perf_mutants.main(argv))


if __name__ == "__main__":
    sys.exit(main())
