from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path

import pytest

from bench import sandbox
from bench.sandbox import run_python_script


def test_stdout_is_captured(tmp_path: Path) -> None:
    result = run_python_script("print(1)\n", tmp_path)

    assert result.returncode == 0
    assert result.stdout.strip() == "1"
    assert not result.timed_out


def test_nonzero_returncode_for_system_exit(tmp_path: Path) -> None:
    result = run_python_script("raise SystemExit(3)\n", tmp_path)

    assert result.returncode == 3
    assert not result.timed_out


def test_timeout_is_fast_and_flagged(tmp_path: Path) -> None:
    result = run_python_script("while True:\n    pass\n", tmp_path, timeout_s=1.0)

    assert result.timed_out
    assert result.returncode != 0


def test_script_cannot_see_parent_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "super-secret")

    code = "import os\nprint(os.environ.get('ANTHROPIC_API_KEY'))\n"
    result = run_python_script(code, tmp_path)

    assert result.stdout.strip() == "None"


def test_solution_file_is_written_to_workdir(tmp_path: Path) -> None:
    run_python_script("print('hi')\n", tmp_path)

    assert (tmp_path / "solution.py").read_text() == "print('hi')\n"


def test_stderr_is_captured_on_failure(tmp_path: Path) -> None:
    result = run_python_script("raise ValueError('boom')\n", tmp_path)

    assert result.returncode != 0
    assert "boom" in result.stderr


# --- Regression tests for review finding M5 / minor 3 -----------------------

_HAS_SANDBOX_EXEC = sys.platform == "darwin" and sandbox.sandbox_exec_available()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_timeout_kills_whole_process_group_including_grandchildren(tmp_path: Path) -> None:
    code = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "open('grandchild.pid', 'w').write(str(p.pid))\n"
        "time.sleep(60)\n"
    )
    start = time.monotonic()
    result = run_python_script(code, tmp_path, timeout_s=2.0)
    elapsed = time.monotonic() - start

    assert result.timed_out
    # The grandchild inherits the stdout/stderr pipes; if it survived, reading
    # them to EOF would block for the full 60 s.
    assert elapsed < 15
    pid = int((tmp_path / "grandchild.pid").read_text())
    deadline = time.monotonic() + 5
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _pid_alive(pid), "grandchild survived the sandbox timeout"


def test_cpu_limit_is_applied(tmp_path: Path) -> None:
    code = "import resource\nprint(resource.getrlimit(resource.RLIMIT_CPU)[0])\n"
    result = run_python_script(code, tmp_path, timeout_s=10.0)

    assert result.returncode == 0, result.stderr
    assert int(result.stdout.strip()) == 15


def test_file_size_limit_is_applied(tmp_path: Path) -> None:
    code = "open('big.bin', 'wb').write(b'x' * (2 * 1024 * 1024))\n"
    result = run_python_script(code, tmp_path, max_file_bytes=1024 * 1024)

    assert result.returncode != 0
    assert (tmp_path / "big.bin").stat().st_size <= 1024 * 1024


def test_default_file_size_limit_allows_normal_output(tmp_path: Path) -> None:
    code = "open('ok.bin', 'wb').write(b'x' * (2 * 1024 * 1024))\n"
    result = run_python_script(code, tmp_path)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "ok.bin").stat().st_size == 2 * 1024 * 1024


@pytest.mark.skipif(not _HAS_SANDBOX_EXEC, reason="needs macOS sandbox-exec")
def test_sandbox_blocks_writes_outside_workdir(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    escape = tmp_path / "escaped.txt"
    code = f"open({str(escape)!r}, 'w').write('x')\n"
    result = run_python_script(code, workdir)

    assert result.returncode != 0
    assert not escape.exists()


@pytest.mark.skipif(not _HAS_SANDBOX_EXEC, reason="needs macOS sandbox-exec")
def test_sandbox_allows_writes_inside_workdir(tmp_path: Path) -> None:
    code = (
        "import os, tempfile\n"
        "open('inside.txt', 'w').write('x')\n"
        "os.makedirs('sub', exist_ok=True)\n"
        "open('sub/deep.txt', 'w').write('y')\n"
        "with tempfile.NamedTemporaryFile() as f:\n"
        "    f.write(b'tmp')\n"
        "open(os.devnull, 'w').write('z')\n"
    )
    result = run_python_script(code, tmp_path)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "inside.txt").read_text() == "x"
    assert (tmp_path / "sub" / "deep.txt").read_text() == "y"


@pytest.mark.skipif(not _HAS_SANDBOX_EXEC, reason="needs macOS sandbox-exec")
def test_sandbox_blocks_network(tmp_path: Path) -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        code = (
            "import socket\n"
            "s = socket.socket()\n"
            "s.settimeout(3)\n"
            f"s.connect(('127.0.0.1', {port}))\n"
            "print('connected')\n"
        )
        result = run_python_script(code, tmp_path, timeout_s=20.0)
    finally:
        server.close()

    assert result.returncode != 0
    assert "connected" not in result.stdout


@pytest.mark.skipif(not _HAS_SANDBOX_EXEC, reason="needs macOS sandbox-exec")
def test_sandbox_runs_a_normal_pandas_solution(tmp_path: Path) -> None:
    from bench.datasets import write_fixtures

    write_fixtures(tmp_path)
    code = (
        "import pandas as pd\n"
        "orders = pd.read_csv('orders.csv', parse_dates=['order_date'])\n"
        "paid = orders[orders['status'] == 'paid']\n"
        "paid.to_parquet('out.parquet', index=False)\n"
    )
    result = run_python_script(code, tmp_path, timeout_s=60.0)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "out.parquet").exists()


def test_long_stderr_keeps_the_tail_where_the_error_is(tmp_path: Path) -> None:
    code = (
        "import sys\n"
        "sys.stderr.write('noise line\\n' * 2000)\n"
        "raise ValueError('the-real-error')\n"
    )
    result = run_python_script(code, tmp_path)

    assert result.returncode != 0
    assert len(result.stderr) == 4000
    assert "the-real-error" in result.stderr


def test_env_extra_is_passed_without_inheriting_the_parent_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "super-secret")
    code = (
        "import os\n"
        "print(os.environ.get('EXTRA_VAR'), os.environ.get('ANTHROPIC_API_KEY'), "
        "os.environ['HOME'] == os.getcwd())\n"
    )
    result = run_python_script(code, tmp_path, env_extra={"EXTRA_VAR": "v1"})
    assert result.stdout.split() == ["v1", "None", "True"]


def test_env_extra_cannot_override_the_isolation_variables(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="HOME"):
        run_python_script("print(1)\n", tmp_path, env_extra={"HOME": "/"})


# ---- read confinement: earlier runs' outputs (<repo>/results) ------------------------------


def test_read_denied_paths_cover_the_results_tree() -> None:
    denied = sandbox.read_denied_paths()
    assert (sandbox._REPO / "results").resolve() in denied
    main = sandbox._main_checkout(sandbox._REPO)
    if main is not None:  # a worktree: the main checkout's earlier runs too
        assert (main / "results").resolve() in denied


@pytest.mark.skipif(not _HAS_SANDBOX_EXEC, reason="needs macOS sandbox-exec")
def test_sandbox_denies_earlier_runs_under_results_but_not_the_own_workdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run under ``<repo>/results/<run>`` cannot read another run's outputs in
    ``<repo>/results`` (programs, outcomes -- nothing registered with
    ``deny_reads_under``), yet can read its own workdir inside the same tree."""
    repo = tmp_path / "repo"
    earlier = repo / "results" / "earlier-run" / "outcomes.jsonl"
    earlier.parent.mkdir(parents=True)
    earlier.write_text("secret\n")
    workdir = repo / "results" / "this-run" / "work" / "m" / "P" / "o01" / "rep0"
    workdir.mkdir(parents=True)
    (workdir / "mine.txt").write_text("mine\n")
    monkeypatch.setattr(sandbox, "_REPO", repo)
    monkeypatch.setattr(sandbox, "_registered_read_denies", set())
    script = (
        "import os\n"
        "results = []\n"
        f"for path in [{str(earlier)!r}, 'mine.txt']:\n"
        "    try:\n"
        "        open(path).read()\n"
        "        results.append('read')\n"
        "    except PermissionError:\n"
        "        results.append('denied')\n"
        "try:\n"
        f"    os.listdir({str(repo / 'results')!r})\n"
        "    results.append('listed')\n"
        "except PermissionError:\n"
        "    results.append('denied')\n"
        "print(','.join(results))\n"
    )
    result = run_python_script(script, workdir, timeout_s=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "denied,read,denied"
