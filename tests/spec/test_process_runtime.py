"""``lattice.spec.check`` on an app that is not Python: a compiled Go binary
(``tests/spec/goapp``) speaking HTTP and keeping its state in a SQLite file, served by
the process runtime (``lattice.spec.process``). The whole file is skipped when ``go`` is
missing or the binary cannot be built (no module download)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from lattice.spec import CheckError, Report, check
from lattice.spec.__main__ import main
from lattice.spec.process import ProcessApp, live_processes, process_runtime
from tests.spec import go_harness as gh

GOAPP = Path(__file__).parent / "goapp"


@pytest.fixture(scope="module")
def binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    go = shutil.which("go")
    if go is None:
        pytest.skip("go is not installed")
    out = tmp_path_factory.mktemp("goapp") / "goapp"
    try:
        done = subprocess.run([go, "build", "-o", str(out), "."], cwd=GOAPP, capture_output=True,
                              text=True, timeout=600, check=False)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as err:
        pytest.skip(f"cannot build the Go app: {err}")
    if done.returncode != 0 or not out.exists():
        pytest.skip(f"cannot build the Go app (module download?): {done.stderr[-500:]}")
    return out


@pytest.fixture
def app(binary: Path, monkeypatch: pytest.MonkeyPatch):
    """``app(mode=..., bug=...)``: a recording factory of the Go app's variant."""
    monkeypatch.setenv(gh.BINARY_ENV, str(binary))
    handles: list[ProcessApp] = []

    def variant(mode: str = "safe", bug: str = "", **env: str):
        monkeypatch.setenv("GOAPP_MODE", mode)
        monkeypatch.setenv("GOAPP_BUG", bug)

        def factory(db_path: Path) -> ProcessApp:
            handle = gh.make_app(db_path)
            handle.env = {**handle.env, **env}
            handles.append(handle)
            return handle

        return factory

    variant.handles = handles  # type: ignore[attr-defined]
    yield variant
    assert live_processes() == [], "a process outlived its check"


def run(factory: Any, layers: str, **kw: Any) -> Report:
    kw.setdefault("runtime", gh.RUNTIME)
    return check(gh.SPEC, factory, gh.build_db, layers=layers, **kw)


def of(report: Report, layer: str) -> list:
    return [f for f in report.findings if f.layer == layer]


# ---- (a) scenarios pass on the correct app, fail on a wrong one --------------------------------


def test_a_scenarios_pass_on_the_correct_app(app):
    report = run(app(), "D")
    assert report.verdict == "pass", report.render()
    assert {c["verdict"] for c in report.cases.values()} == {"correct"}
    assert len(report.cases) == 7


def test_a_scenarios_fail_on_a_wrong_app(app):
    report = run(app(bug="nocancel"), "D")
    found = of(report, "D")
    assert len(found) == 1, report.render()
    f = found[0]
    assert f.case == "one_live_run_per_config / step 3"
    assert any("status is 'queued', expected 'cancelled'" in p for p in f.details["problems"])


# ---- (b) layer C catches an invariant a request breaks -----------------------------------------


def test_b_layer_c_catches_an_invariant_broken_by_a_request(app):
    report = run(app(bug="nocheck"), "CD")
    found = of(report, "C")
    assert [f.case for f in found] == ["one_live_run_per_config / step 2"], report.render()
    f = found[0]
    assert f.details["invariant"] == "one_live_run_per_config"
    assert f.endpoint == "POST /configs/1/runs"
    assert f.request["user"] == "ben"
    assert f.details["rows"][0]["count"] == 2
    assert not any("does not watch" in n for n in report.notes)


def test_b_layer_c_is_quiet_on_the_correct_app(app):
    report = run(app(), "CD")
    assert of(report, "C") == [], report.render()


# ---- (c) layer X refutes the racy variant, not the safe one ------------------------------------


def test_c_layer_x_refutes_the_racy_variant(app):
    factory = app(mode="racy")
    report = run(factory, "X", x_k=8)
    found = [f for f in of(report, "X") if f.details["rule_id"] == "one_live_run_per_config"]
    assert found, report.render()
    f = found[0]
    assert f.endpoint == "POST /configs/1/runs" and f.details["k"] == 8
    assert f.details["rows"][0]["count"] > 1
    # every copy of a race hit one server: per try, one process replays the prefix and
    # one serves all k copies (not one per copy); plus one per scenario's recording
    tries = sum(r["tries"] for r in report.races)
    assert len(app.handles) == len(gh.SPEC.scenarios) + 2 * tries


def test_c_layer_x_does_not_refute_the_safe_variant(app):
    report = run(app(mode="safe"), "X", x_k=8)
    assert of(report, "X") == [], report.render()
    assert report.races and all(r["outcome"] == "not refuted" for r in report.races)


def test_c_layer_c_watches_the_racing_requests_too(app):
    report = run(app(mode="racy"), "CX", x_k=8)
    assert any(f.case.startswith("X | race ") for f in of(report, "C")), report.render()


# ---- (d) nothing is left running ---------------------------------------------------------------


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def test_d_no_process_is_left_running(app):
    factory = app(mode="racy")
    run(factory, "CDX", x_k=4, x_repeats=1)
    pids = [h.pid for h in app.handles if h.pid is not None]
    assert len(pids) >= 3
    assert all(_gone(p) for p in pids)
    assert all(h.returncode is not None for h in app.handles if h.pid is not None)
    assert live_processes() == []


def test_d_nothing_is_left_running_when_the_check_fails(app):
    factory = app()

    def boom(client: Any, handle: str) -> str:
        raise KeyboardInterrupt  # not an Exception: escapes every handler of the check

    with pytest.raises(KeyboardInterrupt):
        run(factory, "D", runtime=process_runtime(), login=boom)
    assert app.handles and all(_gone(h.pid) for h in app.handles if h.pid)
    assert live_processes() == []


# ---- (e) a startup failure is a clear CheckError with the stderr --------------------------------


def test_e_a_startup_failure_is_a_check_error_with_stderr(app):
    with pytest.raises(CheckError) as err:
        run(app(STARTUP_FAIL="1"), "D")
    text = str(err.value)
    assert "refusing to start: STARTUP_FAIL is set" in text
    assert "exited with code 3" in text
    assert live_processes() == []


def test_e_a_service_that_never_answers_health_times_out(app, binary):
    def factory(db_path: Path) -> ProcessApp:
        handle = gh.make_app(db_path)
        handle.health_path = "/no-such-health"
        handle.startup_timeout = 1.0
        app.handles.append(handle)
        return handle

    with pytest.raises(CheckError, match="did not answer 200 on /no-such-health"):
        run(factory, "D")
    assert all(_gone(h.pid) for h in app.handles if h.pid)


# ---- layer A, auto-detection, the CLI ----------------------------------------------------------


def test_layer_a_reads_a_go_app_with_gocheck(app):
    """A runtime whose framework is ``go`` takes ``app_dir`` as the Go module root
    (lattice.gocheck); the toy app is plain net/http, so there is nothing to find."""
    report = run(app(), "AD", app_dir=GOAPP, package="goapp")
    assert any(n.startswith("layer A (gocheck): not available for Go") for n in report.notes)
    assert of(report, "A") == []


def test_layer_a_is_skipped_with_a_note_without_go_source(app):
    report = run(app(), "AD")
    assert "layer A skipped: no Go source (go_src, or --go-src)" in report.notes
    assert of(report, "A") == []


def test_layer_a_is_skipped_for_another_language(app):
    report = run(app(), "AD", app_dir=GOAPP, runtime=process_runtime(
        framework="rust", auth=gh.RUNTIME.auth))  # fmt: skip
    assert any("layer A: no static checker for this language" in n for n in report.notes)


def test_a_factory_returning_a_process_app_is_detected(app, monkeypatch):
    """No runtime given: the factory's ``-> ProcessApp`` annotation selects the process
    runtime (callers logged in by ``login``)."""
    app()  # the env

    def login(client: Any, handle: str) -> str:
        return f"tok-{handle}"

    report = check(gh.SPEC, gh.make_app, gh.build_db, layers="D", login=login)
    assert report.verdict == "pass", report.render()


def test_the_cli_checks_a_process_app(app, capsys):
    app(mode="racy")
    code = main(["check", "--spec", "tests.spec.go_harness:SPEC",
                 "--app", "tests.spec.go_harness:make_app",
                 "--db", "tests.spec.go_harness:build_db",
                 "--runtime", "tests.spec.go_harness:RUNTIME",
                 "--layers", "CDX", "--x-k", "6", "--json"])  # fmt: skip
    out = capsys.readouterr()
    assert code == 1, out.err
    assert '"one_live_run_per_config"' in out.out
    assert live_processes() == []


def test_timings_split_a_process_scenarios_start(app):
    """Each scenario's seconds name the process's spawn, health wait and stop; ``only``
    runs the one scenario selected."""
    report = run(app(), "CD", only=["a_run_is_read"])
    assert report.verdict == "pass", report.render()
    (s,) = report.timings["scenarios"]
    assert s["name"] == "a_run_is_read" and s["requests"] == 3
    assert s["health_wait_s"] > 0 and s["process_spawn_s"] > 0
    assert s["process_spawn_s"] + s["health_wait_s"] <= s["app_start_s"] + 1e-3
    assert 0 < s["process_stop_s"] <= s["app_stop_s"] + 1e-3
    assert s["seconds"] <= report.timings["layers"]["D"] + 1e-3
