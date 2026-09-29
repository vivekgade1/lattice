"""The Go endpoint emitter and build (plan 5a Task 12): a checked Lattice API program
compiles to one Go binary that serves the same HTTP answers as the Python runtime.

The full differential (every case of every program) is Task 13; here each program that
checks for Go builds and answers one happy request exactly as the runtime does, the T2
programs (Row / Caller / records / scalar calls) answer several, and the build's
contract is pinned: LISTENING line, GoBuildError for a rejected program, the content
cache and its pruning, the CLI.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

import lattice.api.runtime as runtime
from bench.api_datasets import api_profile, build_db
from bench.api_model import HttpCase
from lattice.check import check_source
from lattice.gogen import build as gobuild
from lattice.gogen.build import GoBuildError, build_go_app
from tests.api import hardening, realapps, t2prog
from tests.api.test_codegen_differential import PROGRAMS, normalize, setup_of

ROOT = Path(__file__).resolve().parents[2]
SRC = PROGRAMS["c01"][0]  # PROGRAMS: pid -> (source, ApiTask)


# ---- running a built app ------------------------------------------------------------------


class GoApp:
    """A built binary serving a database, on a free port."""

    def __init__(self, binary: Path, db: Path) -> None:
        self.proc = subprocess.Popen(  # noqa: S603
            [str(binary), "--db", str(db), "--addr", "127.0.0.1:0"],
            stdout=subprocess.PIPE,
            text=True,
        )
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline().strip()
        assert line.startswith("LISTENING "), line
        self.client = httpx.Client(base_url="http://" + line.split()[1], timeout=30)

    def close(self) -> None:
        self.client.close()
        self.proc.terminate()
        self.proc.wait(5)


def _send(client: Any, case: HttpCase) -> tuple[int, object]:
    headers = {"Authorization": f"Bearer {case.token}"} if case.token is not None else {}
    r = client.request(
        case.method,
        case.path,
        params=dict(case.query) or None,
        json=case.json,
        headers=headers,
        follow_redirects=False,
    )
    return r.status_code, (r.json() if r.content else None)


def _compare(
    source: str, profile: Any, build: Any, cases: list[HttpCase], out: Path, work: Path
) -> list[tuple[int, object]]:
    """Each case on a fresh database: the Go app's (status, body) equals the runtime's
    (values generated per request normalized). Returns the Go answers."""
    built = build_go_app(source, profile, out)
    answers = []
    for i, case in enumerate(cases):
        py_db, go_db = work / f"py{i}.db", work / f"go{i}.db"
        build(py_db)
        shutil.copy(py_db, go_db)
        check, app = runtime.serve_source(source, profile, py_db)
        assert app is not None, [d.render() for d in check.diagnostics]
        with TestClient(app, base_url="http://testserver") as client:
            want = _send(client, case)
        go = GoApp(built.binary, go_db)
        try:
            got = _send(go.client, case)
        finally:
            go.close()
        assert normalize(list(got)) == normalize(list(want)), case.name
        answers.append(got)
    return answers


@pytest.fixture(scope="session")
def go_out(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One build directory (and so one cache) for the whole session."""
    return tmp_path_factory.mktemp("gobuild")


# ---- the brief's tests ------------------------------------------------------------------------


def test_builds_and_serves(tmp_path):
    built = build_go_app(SRC, api_profile(), tmp_path)
    db = tmp_path / "app.db"
    build_db(db)
    proc = subprocess.Popen(  # noqa: S603
        [str(built.binary), "--db", str(db), "--addr", "127.0.0.1:0"],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        line = proc.stdout.readline().strip()
        assert line.startswith("LISTENING ")
    finally:
        proc.terminate()
        proc.wait(5)
    assert built.check.ok
    assert (built.module_dir / "go.mod").read_text().startswith("module latticeapp\n")


@pytest.fixture
def realapps_source() -> str:
    return realapps.PROGRAM


@pytest.fixture
def realapps_profile() -> Any:
    return realapps.PROFILE


def test_rejected_program_raises(tmp_path, realapps_source, realapps_profile):
    with pytest.raises(GoBuildError) as e:
        build_go_app(realapps_source, realapps_profile, tmp_path)
    assert "L890" in str(e.value)
    assert e.value.check is not None and not e.value.check.ok


# ---- the build: module, cache, pruning ------------------------------------------------------


def test_module_files(go_out):
    built = build_go_app(SRC, api_profile(), go_out)
    mod = (built.module_dir / "go.mod").read_text()
    assert "require lattice.dev/rt v0.0.0" in mod
    assert f"replace lattice.dev/rt => {gobuild.RT}" in mod
    assert (built.module_dir / "go.sum").read_text() == (gobuild.RT / "go.sum").read_text()
    for name in ("handlers.go", "main.go", "app"):
        assert (built.module_dir / name).exists(), name
    assert built.binary == built.module_dir / "app"
    assert built.module_dir.parent == go_out / ".cache"


def test_cache_hit_does_not_rebuild(go_out, monkeypatch):
    first = build_go_app(SRC, api_profile(), go_out)

    def no_go(*_a: object, **_k: object) -> None:
        raise AssertionError("go build ran on a cache hit")

    monkeypatch.setattr(gobuild, "_go_build", no_go)
    again = build_go_app(SRC, api_profile(), go_out)
    assert again.binary == first.binary


def test_cache_key_changes_with_source_and_profile():
    other = SRC.replace('"name", "role"', '"name"')
    profile = api_profile()
    k1 = gobuild.cache_key(SRC, profile, {"a.go": "x"})
    assert k1 == gobuild.cache_key(SRC, api_profile(), {"a.go": "x"})
    assert k1 != gobuild.cache_key(other, profile, {"a.go": "x"})
    assert k1 != gobuild.cache_key(SRC, dataclasses.replace(profile, schemas={"x": 1}), {})


def test_cache_is_pruned(tmp_path):
    cache = tmp_path / ".cache"
    for i in range(25):
        d = cache / f"{i:02d}"
        d.mkdir(parents=True)
        (d / "app").write_text("")
        os.utime(d, (1000 + i, 1000 + i))
    gobuild.prune_cache(cache, keep=20)
    left = sorted(p.name for p in cache.iterdir())
    assert left == [f"{i:02d}" for i in range(5, 25)]


def test_prune_keeps_young_temporary_builds(tmp_path):
    """A `.tmp-*` directory is another process's build in progress while it is young
    (under 10 minutes): never counted among the kept builds, never removed; an old one
    is stale and removed."""
    cache = tmp_path / ".cache"
    for i in range(3):
        d = cache / f"{i:02d}"
        d.mkdir(parents=True)
        os.utime(d, (1000 + i, 1000 + i))
    young, old = cache / ".tmp-young-1", cache / ".tmp-old-2"
    young.mkdir()
    old.mkdir()
    os.utime(old, (1000, 1000))  # long ago
    gobuild.prune_cache(cache, keep=2)
    assert sorted(p.name for p in cache.iterdir()) == [".tmp-young-1", "01", "02"]


def test_cache_key_includes_the_go_version(monkeypatch):
    k1 = gobuild.cache_key(SRC, api_profile(), {"a.go": "x"})
    monkeypatch.setattr(gobuild, "go_version", lambda: "go version go9.99 test/arch")
    assert gobuild.cache_key(SRC, api_profile(), {"a.go": "x"}) != k1


def test_go_build_does_not_stamp_vcs(monkeypatch, tmp_path):
    """-buildvcs=false: a build inside a git checkout needs no git, and the binary does
    not depend on the checkout's state."""
    seen: list[list[str]] = []

    def run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(gobuild.subprocess, "run", run)
    gobuild._go_build(tmp_path)
    build = next(c for c in seen if c[:2] == ["go", "build"])
    assert "-buildvcs=false" in build


def test_gorun_cache_is_pruned(tmp_path, monkeypatch):
    from tests.semantics_v11 import gorun

    for i in range(13):
        d = tmp_path / f"{i:02d}"
        d.mkdir()
        os.utime(d, (1000 + i, 1000 + i))
    gorun.prune(tmp_path, keep=10)
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"{i:02d}" for i in range(3, 13)]


def test_cli_prints_the_binary(tmp_path):
    fixture = ROOT / "tests" / "fixtures" / "api" / "c01" / "lattice.py"
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "lattice.gogen", "build", str(fixture),
         "--profile", "bench.api_datasets:api_profile", "--out", str(tmp_path)],
        capture_output=True, text=True, check=False, cwd=ROOT,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    path = Path(proc.stdout.strip())
    assert path.is_file() and os.access(path, os.X_OK)


def test_cli_rejected_program_exits_nonzero(tmp_path):
    source = tmp_path / "prog.py"
    source.write_text(realapps.PROGRAM)
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "lattice.gogen", "build", str(source),
         "--profile", "tests.api.realapps:PROFILE", "--out", str(tmp_path)],
        capture_output=True, text=True, check=False, cwd=ROOT,
    )  # fmt: skip
    assert proc.returncode == 1
    assert "L890" in proc.stderr


# ---- one happy request per program that checks for Go -----------------------------------


def _go_programs() -> Iterator[str]:
    for pid, (source, _task) in PROGRAMS.items():
        if check_source(source, setup_of(pid).profile, target="go").ok:
            yield pid


GO_PROGRAMS = list(_go_programs())


def test_expected_programs_check_for_go():
    """Every task program and worked example checks for Go; the rejected ones use
    natives (L890)."""
    rejected = set(PROGRAMS) - set(GO_PROGRAMS)
    assert rejected == {
        "realapps", "partb", "effects", "gaps", "mine", "round2", "solo", "solo_runtime",
        "solo_answers", "t2", "n1b",
    }  # fmt: skip
    assert "hardening" in GO_PROGRAMS  # Caller (plan 5a Task 12)


def _happy(cases: tuple[HttpCase, ...]) -> HttpCase:
    return next(c for c in cases if c.kind == "happy" and c.expect_status < 400)


@pytest.mark.parametrize("pid", GO_PROGRAMS)
def test_smoke(pid, go_out, tmp_path):
    source, task = PROGRAMS[pid]
    setup = setup_of(pid)
    _compare(source, setup.profile, setup.build, [_happy(task.cases)], go_out, tmp_path)


# ---- T2 values at the boundary: Row, Caller, records, scalar calls ---------------------------


def test_hardening_program(go_out, tmp_path):
    """Caller (with stored spellings decoded), a list of records inserted, fuel and the
    value size cap."""
    cases = [c for c in PROGRAMS["hardening"][1].cases]
    answers = _compare(
        hardening.PROGRAM, hardening.PROFILE, hardening.build_db, cases, go_out, tmp_path
    )
    plan = next(a for c, a in zip(cases, answers, strict=True) if c.name == "plan pro")
    assert plan == (200, {"id": 2, "role": "Customer", "plan": "pro", "p": "pro", "q": "pro"})


# tests/api/t2prog.py without its natives (L890 on Go): t2prog.GO_PROGRAM.
T2_GO_PROGRAM = t2prog.GO_PROGRAM
T2_GO_PROFILE = t2prog.GO_PROFILE


def _t2case(name: str, method: str, path: str, who: str, **kw: Any) -> HttpCase:
    return HttpCase(
        name=name, kind="happy", family="control", method=method, path=path,
        token=t2prog.TOKENS[who], expect_status=kw.pop("status", 200), **kw,
    )  # fmt: skip


T2_GO_CASES = [
    _t2case("list (Row, Caller, now, Date)", "GET", "/items", "alice"),
    _t2case("list admin", "GET", "/items", "admin", query={"limit": 5, "offset": 1}),
    _t2case("long after page", "GET", "/items/long", "alice", query={"min_len": 6}),
    _t2case("one", "GET", "/items/1", "alice"),
    _t2case("require proc fails", "GET", "/items/2", "alice", status=403),
    _t2case("where proc -> none", "GET", "/items/2/long", "alice", status=404),
    _t2case("digest (list of Row -> records)", "GET", "/digest", "alice"),
    _t2case(
        "rescore (scalar call bound)", "PUT", "/items/1/rescore", "alice", query={"factor": 2}
    ),  # fmt: skip
    _t2case("expand (insert_many)", "POST", "/events/expand", "admin", json={"n": 3}),
    _t2case("replay (records)", "GET", "/logins/replay", "admin"),
    _t2case("spin: fuel R620", "GET", "/spin", "alice", status=500),
    _t2case("depth: R620", "GET", "/depth/5000", "alice", status=500),
]


def test_t2_program(go_out, tmp_path):
    assert check_source(T2_GO_PROGRAM, T2_GO_PROFILE, target="go").ok
    answers = _compare(T2_GO_PROGRAM, T2_GO_PROFILE, t2prog.build_db, T2_GO_CASES, go_out, tmp_path)
    statuses: Mapping[str, int] = {c.name: a[0] for c, a in zip(T2_GO_CASES, answers, strict=True)}
    assert statuses["spin: fuel R620"] == 500
    assert {k: v for k, v in statuses.items()} == {c.name: c.expect_status for c in T2_GO_CASES}


def test_build_time_is_cached(go_out):
    build_go_app(SRC, api_profile(), go_out)
    t0 = time.monotonic()
    build_go_app(SRC, api_profile(), go_out)
    assert time.monotonic() - t0 < 5
