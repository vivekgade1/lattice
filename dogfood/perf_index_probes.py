"""Probes of the missing-index check of ``lattice perf --explain`` and of seeded-id capture,
pre-registered in ``docs/results/2026-10-15-perf-missing-index-prereg.md`` (section 3, with
Amendment 1).

Each probe repeats ``--runs`` times (default 10); its canonical output (the CLI's ``--json``
without ``latency_ms``, ``project``, ``budget_file``; container and run-database names replaced)
must hash to one sha256. The predictions are written here from the pre-registration, before the
code ran; a probe that differs is a finding with the raw output, not something to tune.

**RAN and NOT RUN.** A probe whose environment is missing (Docker, ``psycopg`` / ``pymysql`` in
the server-arm python, ``go`` and the Streak repository) is listed as NOT RUN with the reason,
never PASS.

Usage::

    python dogfood/perf_index_probes.py [--runs 10] [--measure-runs 10] [--out DIR]
        [--only ID ...] [--python PY] [--streak DIR] [--scratch DIR] [--list]

``--python`` is the interpreter for the server arms (a scratch venv with ``psycopg``,
``pymysql``; default: the one of the perf-engines work if present, else this one). Prints
``id | predicted | observed | pass`` per probe and ends ``probes: P/T pass, N not run``; exit
0 when every probe passes and none is NOT RUN, 3 when none failed but some are NOT RUN, 1
otherwise. No LLM, no API key.
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
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
OLD_REV = "30c9120"
STREAK_REV = "a0679e9"
STREAK_SRC = Path("/Users/vivekgade/Documents/streak")
SCRATCH_DEFAULT = Path(
    "/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/"
    "ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad"
)
SERVER_PY = SCRATCH_DEFAULT / "perfengines/perfvenv/bin/python"
VARIANTS = {"clean": "build_db", "drop_items": "drop_items", "drop_notes": "drop_notes"}

ITEMS_STEP = {"method": "POST", "path": "/items", "user": "ann", "count": "N",
              "json": {"name": "item-{i}"}}  # fmt: skip
NOTES_CAPTURE = [
    {"method": "POST", "path": "/items", "user": "ann", "count": 1, "json": {"name": "item"},
     "capture": {"item": "id"}},
    {"method": "POST", "path": "/items/{item}/notes", "user": "ann", "count": "N",
     "json": {"body": "note-{i}"}},
]  # fmt: skip
NOTES_LITERAL = [  # the same without capture: the first item of a fresh database is id 1
    {"method": "POST", "path": "/items", "user": "ann", "count": 1, "json": {"name": "item"}},
    {"method": "POST", "path": "/items/1/notes", "user": "ann", "count": "N",
     "json": {"body": "note-{i}"}},
]  # fmt: skip


def budget(bid: str, endpoint: str, constant: int, steps: list, sizes=(2, 6)) -> dict:
    return {"id": bid, "endpoint": endpoint, "user": "ann",
            "seed": {"items": "N", "steps": steps}, "sizes": list(sizes),
            "queries": {"constant": constant, "per_item": 0}, "latency_ms": None}  # fmt: skip


BUDGETS = [budget("items", "GET /items", 2, [ITEMS_STEP]),
           budget("notes", "GET /items/{item}/notes", 3, NOTES_CAPTURE)]  # fmt: skip
BUDGETS_OLD = [budget("items", "GET /items", 2, [ITEMS_STEP]),
               budget("notes", "GET /items/1/notes", 3, NOTES_LITERAL)]  # fmt: skip

# ---- canonical output ------------------------------------------------------------------------

_CONTAINER = re.compile(r"lattice-perf-[a-z]+-[0-9a-f]{8}")
_RUN = re.compile(r"lattice_[0-9a-f]{8}_")
_LAT = re.compile(r"\s+[\d.]+ / [\d.]+ / [\d.]+$", re.M)


def canon(obj: Any, top: bool = True) -> Any:
    """The CLI's ``--json`` without ``latency_ms``, ``project``, ``budget_file``; container
    names and run-database prefixes replaced."""
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


def thash(text: str) -> str:
    """A text output's hash: latency column replaced, container and run names replaced."""
    text = _LAT.sub(" <latency>", text)
    text = _RUN.sub("<run>_", _CONTAINER.sub("<container>", text))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ---- context -----------------------------------------------------------------------------------


@dataclass
class Run:
    rc: int
    out: str
    err: str
    seconds: float
    data: dict | None = None


@dataclass
class Ctx:
    scratch: Path
    python: str  # the Lattice venv: non-server probes
    server_python: str  # the scratch venv with the drivers: server arms
    runs: int
    mruns: int
    streak: Path | None = None
    old: Path | None = None
    owner: str = field(default_factory=lambda: f"missidx-probe-{uuid.uuid4().hex[:8]}")
    hashes: dict[str, list[str]] = field(default_factory=dict)
    _n: int = 0

    def next_dir(self, what: str) -> Path:
        self._n += 1
        d = self.scratch / f"{what}-{self._n}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def env(self, tree: Path) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("ANTHROPIC", "LATTICE_"))}  # fmt: skip
        env["PYTHONPATH"] = str(tree)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["LATTICE_CONTAINER_OWNER"] = self.owner
        env["TMPDIR"] = str(self.scratch)
        return env

    def cli(self, project: Path, command: str, args: list[str], tree: Path | None = None,
            python: str | None = None, timeout: float = 1200) -> Run:  # fmt: skip
        argv = [python or self.python, "-m", "lattice.perf", command, "--project", str(project),
                *args]  # fmt: skip
        return self.exec(argv, tree or ROOT, timeout)

    def exec(self, argv: list[str], tree: Path, timeout: float = 1200) -> Run:
        t0 = time.monotonic()
        try:
            # cwd is the tree: ``python -m`` puts the cwd first on sys.path, so an old tree
            # run from the worktree would otherwise import the worktree's lattice
            r = subprocess.run(argv, env=self.env(tree), capture_output=True, text=True,
                               timeout=timeout, check=False, cwd=str(tree))  # fmt: skip
            rc, out, err = r.returncode, r.stdout, r.stderr
        except subprocess.TimeoutExpired as e:
            rc, out, err = 124, str(e.stdout or ""), f"[timeout after {timeout:g}s]"
        run = Run(rc, out, err, round(time.monotonic() - t0, 2))
        with contextlib.suppress(json.JSONDecodeError):
            run.data = json.loads(out)
        return run

    def left(self) -> list[str]:
        out = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"label=lattice.owner={self.owner}", "--format",
             "{{.Names}}"], capture_output=True, text=True, check=False,
        )  # fmt: skip
        return out.stdout.split()

    def launch(self, project: Path, command: str, args: list[str], inject: str | None = None,
               python: str | None = None) -> tuple[Run, dict]:  # fmt: skip
        """The CLI through this script's launcher (the P-safe hooks); (run, what it saw)."""
        rec = self.next_dir("launch") / "record.json"
        argv = [python or self.python, str(HERE), "--launch", "--record", str(rec)]
        if inject:
            argv += ["--inject", inject]
        argv += ["--", command, "--project", str(project), *args]
        run = self.exec(argv, ROOT)
        seen = json.loads(rec.read_text()) if rec.is_file() else {}
        return run, seen


def index_project(ctx: Ctx, factory: str = "build_db", database: str | None = None,
                  runtime: str | None = None, budgets: list | None = None,
                  top: dict | None = None) -> Path:  # fmt: skip
    d = ctx.next_dir("project")
    shutil.copy(ROOT / "tests/perf/indexapp.py", d / "indexapp.py")
    toml = ('[app]\napp = "indexapp:make_app"\n'
            f'db = "indexapp:{factory}"\nlogin = "indexapp:login"\n')  # fmt: skip
    if database:
        toml += f'database = "{database}"\n'
    if runtime:
        toml += f'runtime = "{runtime}"\n'
    (d / "studio.toml").write_text(toml)
    (d / "perf.budget.json").write_text(
        json.dumps({"version": 1, "budgets": budgets or BUDGETS, **(top or {})})
    )
    return d


def perfapp_project(ctx: Ctx) -> Path:
    d = ctx.next_dir("project")
    shutil.copy(ROOT / "tests/perf/perfapp.py", d / "perfapp.py")
    (d / "studio.toml").write_text(
        '[app]\napp = "perfapp:make_app"\ndb = "perfapp:build_db"\nlogin = "perfapp:login"\n'
    )
    steps = [{"method": "POST", "path": "/items", "user": "ann", "count": "N",
              "json": {"name": "item-{i}"}}]  # fmt: skip
    budgets = [
        budget("items", "GET /items", 2, steps),
        {**budget("n1", "GET /items_n1", 2, steps), "queries": {"constant": 2, "per_item": 1}},
    ]
    (d / "perf.budget.json").write_text(json.dumps({"version": 1, "budgets": budgets}))
    return d


# ---- probes ----------------------------------------------------------------------------------


@dataclass
class Probe:
    id: str
    title: str
    predicted: str
    needs: tuple[str, ...]  # docker | server_drivers | streak
    body: Callable[[Ctx], tuple[bool, str, dict]]  # (pass, observed, facts)


def _plan(db: Path, sql: str) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        return [r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql)]
    finally:
        conn.close()


def _counts(data: dict | None) -> dict[str, list[list[int]]]:
    return {r["budget_id"]: [s["statements"] for s in r["sizes"]]
            for r in (data or {}).get("reports", [])}  # fmt: skip


def _variant_ok(data: dict | None, rc: int, variant: str, runs: int,
                min_rows0: bool = False) -> list[str]:  # fmt: skip
    """The problems of one ``check --explain --json`` against the P-sqlite table (empty: as
    predicted). Counts 2/2 and 3/3, verdict pass, status checked."""
    if data is None:
        return ["no JSON"]
    bad: list[str] = []
    reports = {r["budget_id"]: r for r in data["reports"]}
    want = {"items": [[2] * runs] * 2, "notes": [[3] * runs] * 2}
    if _counts(data) != want:
        bad.append(f"counts {_counts(data)}")
    for bid, r in reports.items():
        e = r.get("explain") or {}
        if r["verdict"] != "pass" or e.get("status") != "checked":
            bad.append(f"{bid}: verdict {r['verdict']} status {e.get('status')}")
        f = [(x["table"], x["rows"], x["access"]) for x in e.get("findings", [])]
        if min_rows0:
            exp: list = [("users", 2, "SCAN users")]
        else:
            exp = {"clean": {"items": [], "notes": []},
                   "drop_items": {"items": [("items", 6, "SCAN items")], "notes": []},
                   "drop_notes": {"items": [], "notes": [("notes", 6, "SCAN notes")]},
                   }[variant][bid]  # fmt: skip
        if f != exp:
            bad.append(f"{bid}: findings {f} (predicted {exp})")
    verdict = data.get("explain", {}).get("verdict")
    exp_v = "findings" if (min_rows0 or variant != "clean") else "clean"
    exp_rc = 1 if exp_v == "findings" else 0
    if verdict != exp_v or rc != exp_rc:
        bad.append(f"explain verdict {verdict} exit {rc} (predicted {exp_v}, {exp_rc})")
    return bad


def p_sqlite(ctx: Ctx) -> tuple[bool, str, dict]:
    facts: dict[str, Any] = {}
    problems: list[str] = []
    arms = [(v, False) for v in VARIANTS] + [("clean", True)]
    for variant, min0 in arms:
        key = f"{variant}{'+min0' if min0 else ''}"
        project = index_project(ctx, VARIANTS[variant])
        extra = ["--explain-min-rows", "0"] if min0 else []
        good = 0
        hashes: list[str] = []
        sample = None
        for _ in range(ctx.runs):
            r = ctx.cli(project, "check",
                        ["--runs", str(ctx.mruns), "--explain", "--json", *extra])  # fmt: skip
            bad = _variant_ok(r.data, r.rc, variant, ctx.mruns, min0)
            if not bad:
                good += 1
            else:
                problems.append(f"{key}: {bad}")
            if r.data is not None:
                hashes.append(chash(r.data))
                sample = sample or r.data
        ctx.hashes[f"sqlite/{key}"] = hashes
        facts[key] = {"as_predicted": good, "runs": ctx.runs, "distinct_hashes": len(set(hashes)),
                      "sample": sample}  # fmt: skip
        if good != ctx.runs or len(set(hashes)) != 1:
            problems.append(f"{key}: {good}/{ctx.runs} as predicted, {len(set(hashes))} hash(es)")
    # the clean plan rows (predicted by the pre-registration), shown directly
    d = ctx.next_dir("plan")
    sys.path.insert(0, str(ROOT))
    from tests.perf import indexapp

    db = indexapp.build_db(d / "clean.db")
    plans = {
        "GET /items": _plan(
            db, "SELECT id, user_id, name FROM items WHERE user_id = 1 ORDER BY id"
        ),
        "item by pk": _plan(db, "SELECT id FROM items WHERE id = 1 AND user_id = 1"),
        "notes": _plan(db, "SELECT id, item_id, body FROM notes WHERE item_id = 1 ORDER BY id"),
        "users": _plan(db, "SELECT id FROM users WHERE token = 'tok-ann'"),
    }
    facts["clean_plans"] = plans
    want = {"GET /items": ["SEARCH items USING INDEX items_user_id (user_id=?)"],
            "item by pk": ["SEARCH items USING INTEGER PRIMARY KEY (rowid=?)"],
            "notes": ["SEARCH notes USING INDEX notes_item_id (item_id=?)"],
            "users": ["SCAN users"]}  # fmt: skip
    if plans != want:
        problems.append(f"clean plan rows {plans} (predicted {want})")
    ok = not problems
    n = sum(
        v["as_predicted"]
        for k, v in facts.items()
        if k in {f"{a}{'+min0' if m else ''}" for a, m in arms}
    )
    total = len(arms) * ctx.runs
    return (
        ok,
        (
            f"{n}/{total} runs as predicted over {len(arms)} arms, 1 hash each"
            if ok
            else "; ".join(problems[:4])
        ),
        facts,
    )


def _git_show(path: str, rev: str = OLD_REV) -> str:
    return subprocess.run(["git", "show", f"{rev}:{path}"], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout  # fmt: skip


def _defs(source: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef | ast.ClassDef | ast.AsyncFunctionDef):
            out[node.name] = ast.dump(node)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = ast.dump(node)
    return out


def _old_tree(ctx: Ctx) -> Path:
    if ctx.old is None:
        old = ctx.scratch / "old"
        if old.exists():
            shutil.rmtree(old)
        old.mkdir(parents=True)
        tar = subprocess.run(["git", "archive", OLD_REV, "lattice"], cwd=ROOT, capture_output=True,
                             check=True).stdout  # fmt: skip
        subprocess.run(["tar", "-x", "-C", str(old)], input=tar, check=True)
        ctx.old = old
    return ctx.old


INVALID = {
    "not_json": "{not json",
    "version": json.dumps({"version": 2, "budgets": BUDGETS_OLD}),
    "top_key": json.dumps({"version": 1, "budgets": BUDGETS_OLD, "bogus": 1}),
    "budget_empty": json.dumps({"version": 1, "budgets": [{"id": "x"}]}),
    "one_size": json.dumps({"version": 1, "budgets": [budget("a", "GET /items", 2, [ITEMS_STEP],
                                                             sizes=(5,))]}),
    "bad_step": json.dumps({"version": 1, "budgets": [budget(
        "a", "GET /items", 2, [{**ITEMS_STEP, "count": -1, "colour": "red"}])]}),
}  # fmt: skip


def p_byte(ctx: Ctx) -> tuple[bool, str, dict]:
    facts: dict[str, Any] = {}
    problems: list[str] = []
    old = _old_tree(ctx)
    # (a) the existing tests are source-identical per function, and pass
    changed: list[str] = []
    tree = subprocess.run(
        ["git", "ls-tree", "--name-only", OLD_REV, "tests/perf/"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    listing = tree.stdout.split()
    for path in listing:
        if not path.endswith(".py"):
            continue
        new_src = (ROOT / path).read_text() if (ROOT / path).is_file() else ""
        old_d, new_d = _defs(_git_show(path)), _defs(new_src)
        for name, dump in old_d.items():
            if new_d.get(name) != dump:
                changed.append(f"{path}::{name}")
    facts["a_changed_defs"] = changed
    tests = [p for p in listing if re.search(r"test_\w+\.py$", p)]
    collect = subprocess.run([ctx.server_python, "-m", "pytest", *tests, "-q", "-p",
                              "no:cacheprovider", "--no-header"], cwd=ROOT, capture_output=True,
                             text=True, env=ctx.env(ROOT), check=False)  # fmt: skip
    summary = [ln for ln in collect.stdout.splitlines() if re.search(r"\d+ (passed|failed)", ln)]
    facts["a_pytest"] = {"files": tests, "rc": collect.returncode,
                         "summary": summary[-1] if summary else collect.stdout[-300:]}  # fmt: skip
    if changed or collect.returncode != 0:
        problems.append(f"(a) changed {changed}, pytest rc {collect.returncode}")
    # (b) perfapp project: every command, 10x old + 10x new, one hash over 20, same exits
    project = perfapp_project(ctx)
    b_facts: dict[str, Any] = {}
    for command in ("measure", "check"):
        for as_json in (False, True):
            for statements in (False, True):
                args = (
                    ["--runs", "3"]
                    + (["--json"] if as_json else [])
                    + (["--statements"] if statements else [])
                )
                key = " ".join(
                    [
                        command,
                        *(["--json"] if as_json else []),
                        *(["--statements"] if statements else []),
                    ]
                )
                hashes, exits = set(), set()
                for tree in (old, ROOT):
                    for _ in range(ctx.runs):
                        r = ctx.cli(project, command, args, tree=tree)
                        exits.add(r.rc)
                        hashes.add(chash(r.data) if as_json and r.data else thash(r.out))
                b_facts[key] = {"hashes": len(hashes), "exits": sorted(exits)}
                if len(hashes) != 1 or len(exits) != 1:
                    problems.append(f"(b) {key}: {len(hashes)} hashes, exits {sorted(exits)}")
    facts["b"] = b_facts
    # (c) six invalid files: stderr identical; the capture file is the one allowed difference
    c_facts: dict[str, Any] = {}
    c_capture_project = index_project(ctx, "build_db")
    for name, text in {
        **INVALID,
        "capture": json.dumps({"version": 1, "budgets": BUDGETS}),
    }.items():
        f = ctx.next_dir("invalid") / f"{name}.json"
        f.write_text(text)
        runs = {}
        for label, tree in (("old", old), ("new", ROOT)):
            # the capture file needs the indexapp (its /items/{id}/notes route)
            proj = c_capture_project if name == "capture" else project
            r = ctx.cli(proj, "check", ["--budget", str(f), "--runs", "1"], tree=tree)
            runs[label] = (r.rc, r.err.strip())
        c_facts[name] = {"old": runs["old"], "new_rc": runs["new"][0],
                         "same": runs["old"] == runs["new"]}  # fmt: skip
        if name == "capture":
            if not (runs["old"][0] == 2 and "unknown key(s) capture" in runs["old"][1]
                    and runs["new"][0] in (0, 1)):  # fmt: skip
                problems.append(f"(c) capture file: old {runs['old']}, new {runs['new']}")
        elif runs["old"] != runs["new"] or runs["old"][0] != 2:
            problems.append(f"(c) {name}: old {runs['old']} new {runs['new']}")
    facts["c"] = c_facts
    # (d) the indexapp project without --explain: old = new (notes names the literal id)
    d_project = index_project(ctx, "build_db", budgets=BUDGETS_OLD)
    d_facts: dict[str, Any] = {}
    for as_json in (False, True):
        hashes, exits = set(), set()
        for tree in (old, ROOT):
            for _ in range(ctx.runs):
                r = ctx.cli(d_project, "check", ["--runs", "3", *(["--json"] if as_json else [])],
                            tree=tree)  # fmt: skip
                exits.add(r.rc)
                hashes.add(chash(r.data) if as_json and r.data else thash(r.out))
        d_facts["json" if as_json else "text"] = {"hashes": len(hashes), "exits": sorted(exits)}
        if len(hashes) != 1 or exits != {0}:
            problems.append(f"(d) json={as_json}: {len(hashes)} hashes, exits {sorted(exits)}")
    facts["d"] = d_facts
    # (e) counts with --explain = counts without
    same = 0
    for _ in range(ctx.runs):
        a = ctx.cli(d_project, "check", ["--runs", "3", "--json"])
        b = ctx.cli(d_project, "check", ["--runs", "3", "--json", "--explain"])
        same += _counts(a.data) == _counts(b.data) and _counts(a.data) != {}
    facts["e"] = {"identical": same, "runs": ctx.runs}
    if same != ctx.runs:
        problems.append(f"(e) counts identical {same}/{ctx.runs}")
    ok = not problems
    return (
        ok,
        (
            "(a)-(e) as predicted: 8 commands x 20 runs, 1 hash each; 7 budget files; "
            f"{len(tests)} test files, {facts['a_pytest']['summary']}"
            if ok
            else "; ".join(problems[:5])
        ),
        facts,
    )


class FakeRuntime:
    """A transport double: records ``(method, path, json)``, answers ``{"id": answer}``."""

    def __init__(self, answer: Any) -> None:
        self.log: list[tuple[str, str, Any]] = []
        self.answer = answer
        self.framework = "fastapi"
        self.one_server = False

    def open(self, app: Any) -> Any:
        rt = self

        class Resp:
            def __init__(self, status: int, payload: Any) -> None:
                self.status_code, self.payload = status, payload
                self.text = json.dumps(payload)
                self.content = self.text.encode()

            def json(self) -> Any:
                return self.payload

        class Client:
            def request(self, method: str, path: str, json: Any = None, headers: Any = None):
                rt.log.append((method, path, json))
                return Resp(201 if method == "POST" else 200, {"id": rt.answer})

        @contextlib.contextmanager
        def cm():
            yield Client()

        return cm()

    def close(self, app: Any) -> None:
        return None

    class auth:  # noqa: N801
        @staticmethod
        def token(client: Any, user: str, login: Any) -> str:
            return "tok"

        @staticmethod
        def present(client: Any, token: str) -> dict:
            return {}


def _fake_run(ctx: Ctx, steps: list, endpoint: str, answer: Any, n: int = 2) -> FakeRuntime:
    sys.path.insert(0, str(ROOT))
    from lattice.perf.budget import parse
    from lattice.perf.measure import Harness, SqliteCounter, measure_size
    from lattice.spec.check import as_db_factory

    rt = FakeRuntime(answer)
    h = Harness(lambda db: object(), lambda path: path, rt, None, "sqlite")
    (b,) = parse({"version": 1, "budgets": [budget("b", endpoint, 3, steps)]}).budgets
    measure_size(b, n, h, as_db_factory(h.db_factory), SqliteCounter(), ctx.next_dir("fake"), 1)
    return rt


def p_seedid(ctx: Ctx) -> tuple[bool, str, dict]:
    facts: dict[str, Any] = {}
    problems: list[str] = []
    old = _old_tree(ctx)
    project = index_project(ctx, "build_db", budgets=[BUDGETS[1]])
    r = ctx.cli(project, "check", ["--runs", "2"], tree=old)
    facts["a_old"] = {"rc": r.rc, "stderr": r.err.strip()}
    if r.rc != 2 or "unknown key(s) capture" not in r.err:
        problems.append(f"(a) old tool exit {r.rc}: {r.err.strip()[:200]}")
    hashes, good = [], 0
    for _ in range(ctx.runs):
        r = ctx.cli(project, "check", ["--runs", str(ctx.mruns), "--json"])
        rep = ((r.data or {}).get("reports") or [{}])[0]
        notes = [n for n in rep.get("notes", []) if n.startswith("seeded id")]
        sizes = rep.get("sizes", [])
        ok = (r.rc == 0 and [s["n"] for s in sizes] == [2, 6]
              and all(s["statements"] == [3] * ctx.mruns for s in sizes)
              and rep.get("fit") == {"constant": 3, "per_item": 0, "exact": True}
              and notes == ["seeded id: {item} from seed step 1 (key id)"]
              and rep.get("endpoint") == "GET /items/{item}/notes")  # fmt: skip
        good += ok
        if r.data:
            hashes.append(chash(r.data))
    facts["b"] = {"as_predicted": good, "runs": ctx.runs, "distinct_hashes": len(set(hashes))}
    if good != ctx.runs or len(set(hashes)) != 1:
        problems.append(f"(b) {good}/{ctx.runs}, {len(set(hashes))} hashes")
    bad = json.loads(json.dumps(BUDGETS[1]))
    bad["seed"]["steps"][0]["capture"] = {"item": "nope"}
    p2 = index_project(ctx, "build_db", budgets=[bad])
    r = ctx.cli(p2, "check", ["--runs", "1"])
    want = (
        "lattice perf: budget 'notes' N=2: seed step POST /items #1: capture item: "
        "response has no nope"
    )
    facts["c"] = {"rc": r.rc, "stderr": r.err.strip()}
    if r.rc != 2 or r.err.strip() != want:
        problems.append(f"(c) exit {r.rc}: {r.err.strip()[:200]}")
    steps = [{"method": "POST", "path": "/items", "user": "ann", "count": 1,
              "json": {"name": "i"}, "capture": {"item": "id"}},
             {"method": "POST", "path": "/items/{item}/{x}/notes", "user": "ann", "count": "N",
              "json": {"body": "{x}"}}]  # fmt: skip
    rt = _fake_run(ctx, steps, "GET /items/{item}/{x}", 5)
    facts["d"] = rt.log
    if (
        rt.log[1] != ("POST", "/items/5/{x}/notes", {"body": "{x}"})
        or rt.log[-1][1] != "/items/5/{x}"
    ):
        problems.append(f"(d) {rt.log}")
    rt = _fake_run(ctx, NOTES_CAPTURE, "GET /items/{item}/notes", "a/b")
    facts["e"] = rt.log
    if rt.log[1][1] != "/items/a%2Fb/notes" or rt.log[-1][1] != "/items/a%2Fb/notes":
        problems.append(f"(e) {rt.log}")
    ok = not problems
    return (
        ok,
        (
            f"(a)-(e) as predicted; counts 3/3 at N=2 and 6 exact, note once, 1 hash in {ctx.runs}"
            if ok
            else "; ".join(problems)
        ),
        facts,
    )


def p_safe(ctx: Ctx) -> tuple[bool, str, dict]:
    facts: dict[str, Any] = {}
    problems: list[str] = []
    # (a) an injected multi-statement text, on a clean schema (exit 2) and with the finding
    inject = "SELECT 1; DELETE FROM items"
    project = index_project(ctx, "drop_items")
    run, seen = ctx.launch(project, "check", ["--runs", "2", "--explain", "--json"],
                           inject=inject)  # fmt: skip
    e = {r["budget_id"]: r["explain"] for r in (run.data or {}).get("reports", [])}
    facts["a"] = {"rc": run.rc, "explain": e, "seen": seen}
    for bid, x in e.items():
        sk = [s["reason"] for s in x["skipped"]]
        if sk != ["more than one statement"] or x["status"] != "partial":
            problems.append(f"(a) {bid}: skipped {sk} status {x['status']}")
    rows = seen.get("rows", [])
    if not rows or any(b != a for b, a in rows):
        problems.append(f"(a) rows before/after the explain {rows}")
    clean = index_project(ctx, "build_db")
    run2, _ = ctx.launch(clean, "check", ["--runs", "2", "--explain", "--json"], inject=inject)
    facts["a_clean_rc"] = run2.rc
    if run2.rc != 2:
        problems.append(f"(a) clean + injected statement: exit {run2.rc} (predicted 2)")
    # (b), (c) from what the launcher saw on the explain connection
    if seen.get("write") is None or "readonly" not in str(seen.get("write")).lower():
        problems.append(f"(b) the explain connection write attempt: {seen.get('write')}")
    sent = seen.get("sent", [])
    bad = [s for s in sent if not s.startswith(("EXPLAIN ", "SET ", "SELECT COUNT(*) FROM "))]
    facts["c_sent"] = sent
    if not sent or bad:
        problems.append(f"(c) sent {len(sent)} statements, not allowed: {bad[:3]}")
    # (d) a process app on SQLite
    proc = index_project(ctx, "build_db", runtime="process")
    r = ctx.cli(proc, "check", ["--runs", "1", "--explain", "--json"])
    reps = (r.data or {}).get("reports", [])
    facts["d"] = {"rc": r.rc, "reports": [{"verdict": x["verdict"], "notes": x["notes"],
                                           "explain": x["explain"]} for x in reps]}  # fmt: skip
    note = (
        "unmeasured: statement counting for a process app on SQLite is not supported "
        "(the app's SQLite library is in another process)"
    )
    if r.rc != 2 or not reps or any(
        x["verdict"] != "unmeasured" or note not in x["notes"]
        or x["explain"]["status"] != "unchecked" for x in reps):  # fmt: skip
        problems.append(f"(d) exit {r.rc} {facts['d']}")
    ok = not problems
    return (
        ok,
        (
            "(a)-(d) as predicted: injected text skipped and not run, rows unchanged, partial "
            "exit 2; read-only write refused; only EXPLAIN/SET/COUNT sent; process app "
            "unmeasured/unchecked exit 2"
            if ok
            else "; ".join(problems)
        ),
        facts,
    )


# ---- server arms -----------------------------------------------------------------------------


def _server_variants(ctx: Ctx, engine: str) -> tuple[bool, str, dict]:
    facts: dict[str, Any] = {}
    problems: list[str] = []
    for variant, factory in VARIANTS.items():
        project = index_project(ctx, factory, database=engine)
        good, hashes, sample = 0, [], None
        access: set[str] = set()
        for _ in range(ctx.runs):
            r = ctx.cli(project, "check", ["--runs", str(ctx.mruns), "--explain", "--json"],
                        python=ctx.server_python)  # fmt: skip
            data = r.data
            bad = []
            if data is None:
                bad = [f"no JSON (exit {r.rc}): {r.err.strip()[-300:]}"]
            else:
                bad = _server_ok(data, r.rc, variant, ctx.mruns)
                for rep in data["reports"]:
                    access |= {f["access"] for f in rep["explain"]["findings"]}
                hashes.append(chash(data))
                sample = sample or data
            if not bad:
                good += 1
            else:
                problems.append(f"{variant}: {bad}")
        ctx.hashes[f"{engine}/{variant}"] = hashes
        facts[variant] = {"as_predicted": good, "runs": ctx.runs, "access_seen": sorted(access),
                          "distinct_hashes": len(set(hashes)), "sample": sample}  # fmt: skip
        if good != ctx.runs or len(set(hashes)) != 1:
            problems.append(f"{variant}: {good}/{ctx.runs}, {len(set(hashes))} hash(es)")
    left = ctx.left()
    facts["containers_left"] = left
    if left:
        problems.append(f"containers left: {left}")
    n = sum(
        v["as_predicted"] for k, v in facts.items() if isinstance(v, dict) and "as_predicted" in v
    )
    ok = not problems
    acc = {
        k: v["access_seen"] for k, v in facts.items() if isinstance(v, dict) and "access_seen" in v
    }
    return (
        ok,
        (
            f"{n}/{3 * ctx.runs} runs as predicted, 1 hash per variant, 0 containers left; "
            f"access seen {acc}"
            if ok
            else "; ".join(problems[:4])
        ),
        facts,
    )


def _server_ok(data: dict, rc: int, variant: str, runs: int) -> list[str]:
    """As the SQLite table, judged on table and finding (not on which access the plan used)."""
    bad: list[str] = []
    reports = {r["budget_id"]: r for r in data["reports"]}
    want = {"items": [[2] * runs] * 2, "notes": [[3] * runs] * 2}
    if _counts(data) != want:
        bad.append(f"counts {_counts(data)}")
    exp = {"clean": {"items": [], "notes": []},
           "drop_items": {"items": [("items", 6)], "notes": []},
           "drop_notes": {"items": [], "notes": [("notes", 6)]}}[variant]  # fmt: skip
    for bid, r in reports.items():
        e = r["explain"]
        if r["verdict"] != "pass" or e["status"] != "checked":
            bad.append(f"{bid}: verdict {r['verdict']} status {e['status']} reason {e['reason']}")
        got = [(f["table"], f["rows"]) for f in e["findings"]]
        if got != exp[bid]:
            bad.append(f"{bid}: findings {got} (predicted {exp[bid]})")
    exp_rc = 0 if variant == "clean" else 1
    if rc != exp_rc:
        bad.append(f"exit {rc} (predicted {exp_rc})")
    return bad


def p_pg(ctx: Ctx) -> tuple[bool, str, dict]:
    return _server_variants(ctx, "postgres")


def p_mysql_i(ctx: Ctx) -> tuple[bool, str, dict]:
    return _server_variants(ctx, "mysql")


def p_safe_server(engine: str) -> Callable[[Ctx], tuple[bool, str, dict]]:
    def run(ctx: Ctx) -> tuple[bool, str, dict]:
        project = index_project(ctx, "drop_items", database=engine)
        injected = "SELECT 1; DELETE FROM items"
        r, seen = ctx.launch(
            project,
            "check",
            ["--runs", "2", "--explain", "--json"],
            inject=injected,
            python=ctx.server_python,
        )
        e = {x["budget_id"]: x["explain"] for x in (r.data or {}).get("reports", [])}
        problems: list[str] = []
        for bid, x in e.items():
            sk = [s["reason"] for s in x["skipped"]]
            if sk != ["more than one statement"] or x["status"] != "partial":
                problems.append(f"{bid}: skipped {sk} status {x['status']}")
        rows = seen.get("rows", [])
        if not rows or any(b != a for b, a in rows):
            problems.append(f"rows before/after {rows}")
        w = str(seen.get("write"))
        if not re.search(r"read[- ]only|READ ONLY", w):
            problems.append(f"write attempt: {w[:200]}")
        sent = seen.get("sent", [])
        bad = [s for s in sent if not s.startswith(("EXPLAIN ", "SET ", "SELECT COUNT(*) FROM "))]
        if not sent or bad:
            problems.append(f"sent {len(sent)}, not allowed {bad[:3]}")
        left = ctx.left()
        if left:
            problems.append(f"containers left {left}")
        return (not problems, ("injected text skipped, rows unchanged, write refused (" + w[:60]
                               + "), only EXPLAIN/SET/COUNT sent, 0 containers left"
                               if not problems else "; ".join(problems)),
                {"rc": r.rc, "seen": seen, "explain": e})  # fmt: skip

    return run


def streak_clone(ctx: Ctx) -> tuple[Path, str]:
    """The scratch clone of Streak (S3 not applied) and the sha its tracked files are at."""
    dst = ctx.streak or (ctx.scratch / "missidx" / "streak")
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--no-hardlinks", "-q", str(STREAK_SRC), str(dst)],
                       check=True)  # fmt: skip
        kind = subprocess.run(
            ["git", "-C", str(dst), "cat-file", "-t", STREAK_REV],
            capture_output=True,
            text=True,
            check=False,
        )
        have = kind.stdout.strip()
        if have == "commit":
            subprocess.run(["git", "-C", str(dst), "-c", "advice.detachedHead=false", "switch",
                            "--detach", STREAK_REV], check=True, capture_output=True)  # fmt: skip
    sha = subprocess.run(["git", "-C", str(dst), "rev-parse", "HEAD"], capture_output=True,
                         text=True, check=True).stdout.strip()  # fmt: skip
    return dst, sha


def p_mysql_ii(ctx: Ctx) -> tuple[bool, str, dict]:
    clone, sha = streak_clone(ctx)
    seed = {"items": "N", "steps": [{"method": "POST", "path": "/v1/habits", "user": "ann",
                                     "count": "N",
                                     "json": {"id": "{uuid}", "name": "habit-{i}",
                                              "period": "week", "target_count": 3,
                                              "start_date": "2026-03-02"}}]}  # fmt: skip
    budgets = {"version": 1, "prepare": "go build -o bin/streak-api ./cmd/server",
               "budgets": [{"id": "habits", "endpoint": "GET /v1/habits", "user": "ann",
                            "seed": seed, "sizes": [5, 25],
                            "queries": {"constant": 4, "per_item": 0},
                            "latency_ms": None}]}  # fmt: skip
    (clone / "perf.budget.json").write_text(json.dumps(budgets))
    models = clone / "internal/db/models.go"
    original = models.read_text()
    block = re.search(r"type Habit struct \{.*?\n\}", original, re.S)
    s3 = original
    if block is not None and "column:user_id;not null;index" in block.group(0):
        edited = block.group(0).replace(
            "column:user_id;not null;index", "column:user_id;not null", 1
        )
        s3 = original[: block.start()] + edited + original[block.end() :]
    if s3 == original:
        return False, "the S3 edit did not apply (Habit.UserID line not found)", {"sha": sha}
    facts: dict[str, Any] = {"streak_sha": sha, "wanted": STREAK_REV}
    problems: list[str] = []
    try:
        for label, text in (("clean", original), ("S3", s3)):
            models.write_text(text)
            good, hashes, sample = 0, [], None
            seen_access: set[str] = set()
            for _ in range(ctx.runs):
                r = ctx.cli(clone, "check", ["--runs", str(ctx.mruns), "--explain", "--json"],
                            python=ctx.server_python)  # fmt: skip
                data = r.data
                if data is None:
                    problems.append(f"{label}: no JSON (exit {r.rc}): {r.err.strip()[-300:]}")
                    continue
                rep = data["reports"][0]
                e = rep["explain"]
                f = [(x["table"], x["rows"]) for x in e["findings"]]
                counts = [s["statements"] for s in rep["sizes"]]
                seen_access |= {x["access"] for x in e["findings"]}
                ok_counts = counts == [[4] * ctx.mruns] * 2
                if label == "clean":
                    ok = r.rc == 0 and f == [] and ok_counts and e["status"] == "checked"
                else:
                    ok = r.rc == 1 and f == [("habits", 25)] and ok_counts
                good += ok
                if not ok:
                    problems.append(
                        f"{label}: exit {r.rc} findings {f} status {e['status']} "
                        f"skipped {e['skipped'][:2]} counts {counts}"
                    )
                hashes.append(chash(data))
                sample = sample or data
            ctx.hashes[f"streak/{label}"] = hashes
            facts[label] = {"as_predicted": good, "runs": ctx.runs, "access": sorted(seen_access),
                            "distinct_hashes": len(set(hashes)), "sample": sample}  # fmt: skip
            if good != ctx.runs or len(set(hashes)) != 1:
                problems.append(f"{label}: {good}/{ctx.runs}, {len(set(hashes))} hashes")
    finally:
        models.write_text(original)
    left = ctx.left()
    if left:
        problems.append(f"containers left {left}")
    ok = not problems
    return (
        ok,
        (
            f"clean 0 findings {facts['clean']['as_predicted']}/{ctx.runs}, S3 caught "
            f"{facts['S3']['as_predicted']}/{ctx.runs} (habits 25 rows), counts 4/4, "
            f"clone at {sha[:7]}"
            if ok
            else "; ".join(problems[:4])
        ),
        facts,
    )


def p_det(prefix: str) -> Callable[[Ctx], tuple[bool, str, dict]]:
    def run(ctx: Ctx) -> tuple[bool, str, dict]:
        keys = {k: v for k, v in ctx.hashes.items() if k.startswith(prefix + "/")}
        if not keys:
            return False, f"no repetitions recorded for {prefix}", {}
        facts = {k: {"runs": len(v), "distinct": len(set(v)), "hashes": sorted(set(v))}
                 for k, v in keys.items()}  # fmt: skip
        bad = {
            k: f["distinct"]
            for k, f in facts.items()
            if f["distinct"] != 1 or f["runs"] != ctx.runs
        }
        return (
            not bad,
            (
                f"{len(keys)} arms x {ctx.runs} runs: 1 sha256 each"
                if not bad
                else f"not identical: {bad}"
            ),
            facts,
        )

    return run


ALL_PROBES: list[Probe] = [
    Probe(
        "P-sqlite",
        "check --explain on indexapp: clean / drop_items / drop_notes / min-rows 0",
        "clean: 0 findings, exit 0; drop_items: items (6 rows) SCAN items, exit 1; drop_notes: "
        "notes (6 rows) SCAN notes, exit 1; min-rows 0 on clean: users (2 rows) SCAN users per "
        "budget; counts 2/2 and 3/3; 10/10, 1 hash per arm",
        (),
        p_sqlite,
    ),
    Probe(
        "P-byte",
        "outputs without --explain are byte-identical to 30c9120",
        "(a) 112 old tests source-identical and passing; (b) 8 commands x 20 runs: 1 hash, same "
        "exits; (c) six invalid files identical, capture file the one difference; (d) indexapp "
        "old = new; (e) counts with --explain = without, 10/10",
        (),
        p_byte,
    ),
    Probe(
        "P-seedid",
        "seeded-id capture",
        "(a) old tool exit 2 unknown key(s) capture; (b) new counts 3/3 at N=2,6, note once, "
        "1 hash; (c) key nope exit 2; (d) {x} literal; (e) a/b reaches the app as a%2Fb",
        (),
        p_seedid,
    ),
    Probe(
        "P-safe",
        "the check cannot change the database (SQLite arms)",
        "(a) injected 'SELECT 1; DELETE ...' skipped, rows unchanged, partial, exit 2; (b) "
        "explain connection refuses a write; (c) only EXPLAIN/SET/COUNT sent; (d) process app: "
        "unmeasured, unchecked, exit 2",
        (),
        p_safe,
    ),
    Probe(
        "P-pg",
        "indexapp on Postgres 16, in-process",
        "clean 0 findings; drop_items items (6 rows) Seq Scan or Index Scan with Filter; "
        "drop_notes notes (6 rows); statuses checked; 10/10 per variant; 0 containers",
        ("docker", "server_drivers"),
        p_pg,
    ),
    Probe(
        "P-safe-postgres",
        "P-safe (a)-(c) on Postgres",
        "injected text skipped, rows unchanged, write refused (read-only transaction), only "
        "EXPLAIN/SET/COUNT sent",
        ("docker", "server_drivers"),
        p_safe_server("postgres"),
    ),
    Probe(
        "P-mysql-i",
        "indexapp on MySQL 8.4, in-process",
        "clean 0 findings; drop_items items (6 rows) type=ALL key=NULL Using where (or "
        "type=index); drop_notes notes (6 rows); 10/10 per variant",
        ("docker", "server_drivers"),
        p_mysql_i,
    ),
    Probe(
        "P-safe-mysql",
        "P-safe (a)-(c) on MySQL",
        "injected text skipped, rows unchanged, write refused (READ ONLY transaction), only "
        "EXPLAIN/SET/COUNT sent",
        ("docker", "server_drivers"),
        p_safe_server("mysql"),
    ),
    Probe(
        "P-mysql-ii",
        "Streak (Go + MySQL), habits budget, clean and S3",
        "clean: 0 findings on tables >= 25 rows, exit 0; S3: a finding on habits (25 rows), "
        "exit 1; counts 4/4; 10/10 each",
        ("docker", "server_drivers", "streak"),
        p_mysql_ii,
    ),
    Probe(
        "P-det-sqlite",
        "determinism: SQLite arms",
        "1 sha256 per arm over 10 runs",
        (),
        p_det("sqlite"),
    ),
    Probe(
        "P-det-postgres",
        "determinism: Postgres arms",
        "1 sha256 per arm over 10 runs",
        ("docker", "server_drivers"),
        p_det("postgres"),
    ),
    Probe(
        "P-det-mysql",
        "determinism: MySQL arms (indexapp, Streak)",
        "1 sha256 per arm over 10 runs",
        ("docker", "server_drivers"),
        p_det("mysql"),
    ),
    Probe(
        "P-det-streak",
        "determinism: Streak arms",
        "1 sha256 per arm over 10 runs",
        ("docker", "server_drivers", "streak"),
        p_det("streak"),
    ),
]


# ---- the launcher (P-safe hooks) ----------------------------------------------------------------


def launch(argv: list[str]) -> int:
    """``--launch [--record FILE] [--inject SQL] -- COMMAND ...``: the perf CLI in this
    process, with (1) ``SQL`` appended to the statements to explain, (2) the explain
    connection's write attempt, its statements and the clone's row counts recorded."""
    rest = argv[argv.index("--") + 1 :] if "--" in argv else []
    own = argv[: argv.index("--")] if "--" in argv else argv
    record = Path(own[own.index("--record") + 1]) if "--record" in own else None
    inject = own[own.index("--inject") + 1] if "--inject" in own else None
    sys.path.insert(0, str(ROOT))
    from lattice.perf import explain as ex
    from lattice.perf.__main__ import main

    seen: dict[str, Any] = {"rows": [], "sent": [], "write": None}
    if inject:
        ex.EXTRA_STATEMENTS.append(inject)

    def rows(engine: str, ref: Any) -> int:
        if engine == "sqlite":
            c = sqlite3.connect(Path(ref).resolve().as_uri() + "?mode=ro", uri=True)
            try:
                return int(c.execute("SELECT COUNT(*) FROM items").fetchone()[0])
            finally:
                c.close()
        from lattice.spec.db import open_db

        db = open_db(str(ref))
        try:
            return int(db.scalar("SELECT COUNT(*) FROM items"))
        finally:
            db.close()

    real_open, real_run = ex.open_explainer, ex.explain_statements

    def open_explainer(engine: str, ref: Any) -> Any:
        c = real_open(engine, ref)
        try:  # the write attempt comes before the recording starts
            if engine == "sqlite":
                c.conn.execute("INSERT INTO items (user_id, name) VALUES (1, 'x')")
            else:
                c.db.execute("INSERT INTO items (user_id, name) VALUES (1, 'x')")
            seen["write"] = "WRITE SUCCEEDED"
        except Exception as err:  # noqa: BLE001
            seen["write"] = f"{type(err).__name__}: {err}"
        if engine == "sqlite":
            c.conn.set_trace_callback(seen["sent"].append)
        else:
            inner = c.db

            class Rec:
                def __getattr__(self, name: str) -> Any:
                    attr = getattr(inner, name)
                    if name in ("execute", "query", "query_dicts", "scalar"):

                        def call(sql: str, *a: Any, **k: Any) -> Any:
                            seen["sent"].append(sql)
                            return attr(sql, *a, **k)

                        return call
                    return attr

            c.db = Rec()
        return c

    def explain_statements(engine: str, ref: Any, *a: Any, **k: Any) -> dict:
        before = rows(engine, ref)
        out = real_run(engine, ref, *a, **k)
        seen["rows"].append([before, rows(engine, ref)])
        return out

    ex.open_explainer = open_explainer  # type: ignore[assignment]
    ex.explain_statements = explain_statements  # type: ignore[assignment]
    try:
        return int(main(rest))
    finally:
        if record:
            record.write_text(json.dumps(seen))


# ---- the runner ------------------------------------------------------------------------------


def env_status(server_python: str) -> dict[str, bool]:
    sys.path.insert(0, str(ROOT))
    from lattice.spec.dbserver import docker_available

    have = {"docker": docker_available()}
    ok = subprocess.run([server_python, "-c", "import psycopg, pymysql, bcrypt"],
                        capture_output=True, check=False).returncode == 0  # fmt: skip
    have["server_drivers"] = ok
    have["streak"] = shutil.which("go") is not None and STREAK_SRC.is_dir()
    return have


def missing_reason(p: Probe, have: dict[str, bool]) -> str | None:
    what = {"docker": "a running Docker daemon",
            "server_drivers": "psycopg, pymysql and bcrypt in the server-arm python",
            "streak": "go and the Streak repository"}  # fmt: skip
    lacking = [what[n] for n in p.needs if not have.get(n)]
    return ("needs " + ", ".join(lacking)) if lacking else None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--launch":
        return launch(argv[1:])
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", type=int, default=10, help="repetitions per arm (default 10)")
    ap.add_argument("--measure-runs", type=int, default=10, help="--runs of each CLI invocation")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--only", nargs="*", default=None, help="probe ids (prefix or fnmatch)")
    ap.add_argument("--python", default=None, help="interpreter for the server arms")
    ap.add_argument("--streak", type=Path, default=None, help="the scratch Streak clone")
    ap.add_argument("--scratch", type=Path, default=None)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)
    patterns = args.only or ["*"]

    def selected(pid: str) -> bool:
        return any(fnmatch.fnmatchcase(pid, pat) or pid.startswith(pat + "-") for pat in patterns)

    probes = [p for p in ALL_PROBES if selected(p.id)]
    if args.list:
        for p in probes:
            print(f"{p.id} | needs {', '.join(p.needs) or '-'} | {p.title}")
        return 0
    scratch = args.scratch or Path(tempfile.mkdtemp(prefix="perf-index-probes-"))
    scratch.mkdir(parents=True, exist_ok=True)
    server_python = args.python or (str(SERVER_PY) if SERVER_PY.is_file() else sys.executable)
    have = env_status(server_python)
    print("environment:", json.dumps(have), flush=True)
    ctx = Ctx(scratch, sys.executable, server_python, args.runs, args.measure_runs,
              streak=args.streak)  # fmt: skip
    results: list[dict[str, Any]] = []
    for p in probes:
        reason = missing_reason(p, have)
        if reason:
            res = {"id": p.id, "status": "NOT RUN", "title": p.title, "predicted": p.predicted,
                   "observed": f"not run: {reason}", "pass": None, "reason": reason}  # fmt: skip
        else:
            t0 = time.monotonic()
            try:
                ok, observed, facts = p.body(ctx)
                err = None
            except Exception:  # noqa: BLE001 -- a probe script bug is reported, not hidden
                err = traceback.format_exc()
                ok, observed, facts = (
                    False,
                    "ERROR in the probe script: " + err.strip().splitlines()[-1],
                    {},
                )
            res = {"id": p.id, "status": "RAN", "title": p.title, "predicted": p.predicted,
                   "observed": observed, "pass": bool(ok), "repetitions": args.runs,
                   "seconds": round(time.monotonic() - t0, 1), "error": err,
                   "facts": facts}  # fmt: skip
        results.append(res)
        shown = {True: "PASS", False: "FAIL", None: "NOT RUN"}[res["pass"]]
        print(f"{res['id']} | {res['predicted']} | {res['observed']} | {shown}", flush=True)
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
                                                 "seconds", "reason") if k in r}
                              for r in results],
                   "passed": n_pass, "failed": n_fail, "not_run": n_not,
                   "total": len(results)}  # fmt: skip
        (args.out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(f"probes: {n_pass}/{len(results)} pass, {n_not} not run", flush=True)
    return 1 if n_fail else (3 if n_not else 0)


if __name__ == "__main__":
    sys.exit(main())
