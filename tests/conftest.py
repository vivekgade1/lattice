"""Shared test fixtures."""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

_FAKE_CLAUDE_IMPL = '''\
"""Fake `claude` CLI for tests: pops the next canned response off a queue file and
prints it, logging every invocation's argv and stdin for the test to inspect."""
import json
import sys
import time

responses_path = sys.argv[1]
log_path = sys.argv[2]
argv = sys.argv[3:]

with open(responses_path) as f:
    responses = json.load(f)

state_path = responses_path + ".state"
try:
    with open(state_path) as f:
        idx = int(f.read().strip() or "0")
except FileNotFoundError:
    idx = 0
spec = responses[min(idx, len(responses) - 1)]
with open(state_path, "w") as f:
    f.write(str(idx + 1))

stdin_data = sys.stdin.read()
with open(log_path, "a") as f:
    f.write(json.dumps({"argv": argv, "stdin": stdin_data}) + "\\n")

delay = spec.get("delay", 0)
if delay:
    time.sleep(delay)

stdout = spec.get("stdout")
if stdout is not None:
    sys.stdout.write(stdout if isinstance(stdout, str) else json.dumps(stdout))
stderr = spec.get("stderr", "")
if stderr:
    sys.stderr.write(stderr)
sys.exit(int(spec.get("exit_code", 0)))
'''


@dataclass
class FakeClaudeCli:
    """Controls a fake `claude` executable placed first on PATH. ``queue`` sets the
    canned responses (one per invocation, the queue's last entry repeats once
    exhausted); ``calls`` is every invocation's argv and stdin, in order."""

    bin_dir: Path
    responses_path: Path
    log_path: Path

    def queue(self, responses: list[dict[str, Any]]) -> None:
        self.responses_path.write_text(json.dumps(responses))
        state = Path(str(self.responses_path) + ".state")
        state.unlink(missing_ok=True)
        self.log_path.write_text("")

    @property
    def calls(self) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text().splitlines() if line]


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeClaudeCli:
    """A `claude` executable on PATH that pops canned JSON responses off a queue
    (``fake_claude.queue([...])``) instead of calling the real CLI or API."""
    bin_dir = tmp_path / "fake_claude_bin"
    bin_dir.mkdir()
    impl_path = bin_dir / "claude_impl.py"
    impl_path.write_text(_FAKE_CLAUDE_IMPL)
    responses_path = bin_dir / "responses.json"
    responses_path.write_text("[]")
    log_path = bin_dir / "calls.jsonl"
    log_path.write_text("")

    launcher = bin_dir / "claude"
    launcher.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{impl_path}" "{responses_path}" '
        f'"{log_path}" "$@"\n'
    )
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return FakeClaudeCli(bin_dir=bin_dir, responses_path=responses_path, log_path=log_path)
