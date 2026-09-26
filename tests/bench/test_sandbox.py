from __future__ import annotations

from pathlib import Path

import pytest

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
