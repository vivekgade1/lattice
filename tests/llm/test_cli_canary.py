"""Canary for the ``claude`` CLI contract that Lattice depends on (``scripts/cli_canary.py``
runs it; ``uv run pytest -m live tests/llm/test_cli_canary.py`` does too).

Why: the CLI changes under us (2.1.283 -> 2.1.309 in the repo's own results), and some of what
we rely on is undocumented (``--max-turns`` is accepted but absent from ``claude --help``).
These tests check, against the installed binary, that

* the version is one a result in this repo was produced with (an unknown version FAILS: re-run
  the canary, then add the version to ``KNOWN_GOOD_VERSIONS``);
* every flag the repo passes is still listed by ``claude --help`` (except ``--max-turns``);
* one headless haiku turn with the full rebuild flag set yields the stream shape Studio parses;
* ``--max-turns`` is accepted and ends ``error_max_turns``, a tiny ``--max-budget-usd`` ends
  ``error_max_budget_usd`` (recipe: ``docs/results/2026-10-30-preflights.md`` P1).

Spend: every live call carries ``--max-budget-usd`` (the ``*_CAP_USD`` constants below), always
``env -u ANTHROPIC_API_KEY``-equivalent (``cli_env``), haiku, no resume. When
``LATTICE_CANARY_LEDGER`` is set, each call appends one JSONL row there (node from
``LATTICE_CANARY_NODE``). The guard-deny round trip and the Stop-hook continuation are not
repeated here: ``docs/results/2026-10-30-preflights.md`` P2/P3 and node H1 cover them.

The one offline test (``test_every_flag_the_repo_builds_is_declared``) runs by default: it pins
the flags the repo's argv builders emit against the list below, so a new flag cannot enter
without being added here (and so to the live ``--help`` check).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from lattice.llm.cli_client import cli_env

# ---- the contract this file pins -------------------------------------------------------------

#: Claude Code versions a result in this repo was produced with (2.1.283: cli_client.py
#: isolation checks; 2.1.296/2.1.299: docs/results/2026-10-12-k-r-mcp-visibility.md and
#: 2026-10-14-agent-run-prereg.md; 2.1.309: H2 and the 2026-10-30 pre-flights). A version not
#: in this list makes the canary fail loudly on purpose.
KNOWN_GOOD_VERSIONS: tuple[str, ...] = ("2.1.283", "2.1.296", "2.1.299", "2.1.309")

#: Flags the repo passes to ``claude`` that ``claude --help`` must list. Derived from the argv
#: builders (rebuild.build_rebuild_argv, session.build_argv, plan.build_planner_argv,
#: mcp_intake.discovery_argv, ClaudeCliClient._argv) and
#: docs/design/harness-facts-for-spike-briefs.md
#: section 1 (``--session-id`` is used by the H2 dispatch script, ``--effort`` by cli_client).
REQUIRED_HELP_FLAGS: tuple[str, ...] = (
    "--setting-sources", "--strict-mcp-config", "--mcp-config", "--settings",
    "--disable-slash-commands", "--tools", "--allowedTools", "--disallowedTools",
    "--permission-mode", "--max-budget-usd", "--output-format", "--append-system-prompt",
    "--system-prompt", "--model", "--effort", "--resume", "--session-id",
    "--no-session-persistence", "--include-partial-messages", "--verbose",
)  # fmt: skip

#: Accepted by the CLI but absent from ``claude --help`` (2026-10-30 pre-flight P1); the repo
#: uses it in mcp_intake.discovery_argv and the dispatch scripts.
HIDDEN_ACCEPTED_FLAGS: tuple[str, ...] = ("--max-turns",)

#: A haiku turn costs about USD 0.001-0.002 with the full tool set; each cap is far above that.
TURN_CAP_USD = 0.03
BUDGET_PROBE_USD = 0.0001  # P1: 0.001 and 0.0005 completed under budget; 0.0001 did not
MAX_TURNS_CAP_USD = 0.03
CALL_TIMEOUT_S = 120

_RECORD_TEXT = (
    "Read note.txt with the Read tool, then tell me its second line, "
    "then read it again and confirm."
)


# ---- helpers ---------------------------------------------------------------------------------


def _claude() -> str:
    path = shutil.which("claude")
    if path is None:
        pytest.skip("no `claude` binary on PATH")
    return path


def _version(binary: str) -> str:
    out = subprocess.run(
        [binary, "--version"], capture_output=True, text=True, timeout=30, check=True
    ).stdout
    m = re.match(r"\s*(\d+\.\d+\.\d+)", out)
    assert m, f"cannot parse `claude --version` output: {out!r}"
    return m.group(1)


def _ledger(row: dict[str, Any]) -> None:
    path = os.environ.get("LATTICE_CANARY_LEDGER")
    if not path:
        return
    row = {"node": os.environ.get("LATTICE_CANARY_NODE", "canary"), **row}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _run(argv: Sequence[str], cwd: Path, *, label: str, cap: float) -> tuple[list[dict], int]:
    """Run one stream-json call and return its parsed records and exit code. The environment
    is the library client's (no ``ANTHROPIC_*``), stdin is closed, the call is time-limited."""
    t0 = time.monotonic()
    proc = subprocess.run(
        list(argv), cwd=cwd, env=cli_env(os.environ, None), stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=CALL_TIMEOUT_S,
    )  # fmt: skip
    records = []
    for line in proc.stdout.splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    result = next((r for r in reversed(records) if r.get("type") == "result"), {})
    _ledger(
        {
            "question": "C1-canary", "label": label, "cap_usd": cap,
            "cost_usd": result.get("total_cost_usd"), "session_id": result.get("session_id"),
            "subtype": result.get("subtype"), "num_turns": result.get("num_turns"),
            "exit_code": proc.returncode, "wall_s": round(time.monotonic() - t0, 1),
        }
    )  # fmt: skip
    assert result, (
        f"{label}: no result record; exit {proc.returncode}; stderr: {proc.stderr[-500:]}"
    )
    return records, proc.returncode


def _result(records: Sequence[dict]) -> dict:
    return next(r for r in reversed(records) if r.get("type") == "result")


def _scratch(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A project dir with note.txt, plus the mcp and settings files of a real rebuild turn."""
    from lattice.studio.session import write_configs

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "note.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    mcp, settings = write_configs(tmp_path / "cfg", "rebuild", proj, sys.executable)
    return proj, mcp, settings


def _rebuild_argv(
    binary: str, proj: Path, mcp: Path, settings: Path, extra: Sequence[str]
) -> list[str]:
    """The Studio rebuild argv (the real builder) with the caps inserted before the prompt."""
    from lattice.studio.rebuild import build_rebuild_argv

    argv = build_rebuild_argv(
        claude_bin=binary,
        text=_RECORD_TEXT,
        model="haiku",
        mcp_config=mcp,
        settings=settings,
        gates=["./check.sh"],
    )
    cut = argv.index("--")
    return [*argv[:cut], *extra, *argv[cut:]]


# ---- offline pin (runs in the default suite) -------------------------------------------------


def _flags(argv: Sequence[str]) -> set[str]:
    out = set()
    for a in argv:
        if a == "--":  # the prompt follows
            break
        if re.fullmatch(r"--[A-Za-z][A-Za-z-]*", a):
            out.add(a)
    return out


def test_every_flag_the_repo_builds_is_declared(tmp_path):
    """The flags emitted by every claude argv builder are all in the canary's lists, so the
    live ``--help`` check covers them; an undeclared (new or renamed) flag fails here."""
    from lattice.llm.cli_client import ClaudeCliClient
    from lattice.studio import mcp_intake, plan, session
    from lattice.studio.rebuild import build_rebuild_argv

    cfg = tmp_path / "x.json"
    argvs = [
        build_rebuild_argv(
            claude_bin="claude", text="t", model="haiku", mcp_config=cfg, settings=cfg,
            gates=["./check.sh"], resume="sid",
        ),
        session.build_argv(
            claude_bin="claude", mode="rebuild", text="t", model="haiku", mcp_config=cfg,
            settings=cfg, prompt="p", resume="sid",
        ),
        plan.build_planner_argv(claude_bin="claude", text="t", model="haiku", system="s"),
        mcp_intake.discovery_argv("claude", cfg),
        ClaudeCliClient(effort="low", full_text=True, cwd=tmp_path)._argv("s"),
        ClaudeCliClient(cwd=tmp_path)._argv("s"),
    ]  # fmt: skip
    declared = set(REQUIRED_HELP_FLAGS) | set(HIDDEN_ACCEPTED_FLAGS)
    emitted = set().union(*(_flags(a) for a in argvs))
    assert emitted <= declared, (
        "flags the repo passes to claude but the canary does not know: "
        f"{sorted(emitted - declared)}; "
        "add them to REQUIRED_HELP_FLAGS (or HIDDEN_ACCEPTED_FLAGS) in tests/llm/test_cli_canary.py"
    )


# ---- live ------------------------------------------------------------------------------------


@pytest.mark.live
class TestCliCanary:
    """Runs against the installed ``claude`` (USD ~0.003 in total, each call capped)."""

    @pytest.fixture(scope="class")
    def binary(self) -> str:
        return _claude()

    def test_version_is_a_known_good_one(self, binary):
        version = _version(binary)
        print(f"\nclaude CLI version: {version}")
        assert version in KNOWN_GOOD_VERSIONS, (
            f"UNKNOWN claude CLI version {version}: known good are "
            f"{', '.join(KNOWN_GOOD_VERSIONS)}. "
            "Re-run this canary (scripts/cli_canary.py) against this version; "
            "if every case passes, "
            "add the version to KNOWN_GOOD_VERSIONS in tests/llm/test_cli_canary.py and record it "
            "in the next results document (a3-claude-version.txt style). Do not just add it."
        )

    def test_every_flag_the_repo_uses_is_in_help(self, binary):
        help_text = subprocess.run(
            [binary, "--help"], capture_output=True, text=True, timeout=30, check=True
        ).stdout
        listed = set(re.findall(r"--[A-Za-z][A-Za-z-]*", help_text))
        missing = [f for f in REQUIRED_HELP_FLAGS if f not in listed]
        assert not missing, f"`claude --help` no longer lists: {missing} (CLI {_version(binary)})"
        # the one flag that is accepted yet undocumented: if --help starts listing it, good news
        for hidden in HIDDEN_ACCEPTED_FLAGS:
            print(f"\n{hidden} listed in --help: {hidden in listed} (documented hidden flag)")

    def test_a_full_rebuild_flag_turn_has_the_stream_shape(self, binary, tmp_path):
        proj, mcp, settings = _scratch(tmp_path)
        argv = _rebuild_argv(
            binary, proj, mcp, settings,
            ["--max-turns", "6", "--max-budget-usd", str(TURN_CAP_USD)],
        )  # fmt: skip
        records, code = _run(
            argv, proj, label="stream shape (full rebuild flags)", cap=TURN_CAP_USD
        )
        assert code == 0, f"exit {code}"

        init = next(r for r in records if r.get("type") == "system" and r.get("subtype") == "init")
        for key in (
            "session_id",
            "model",
            "tools",
            "mcp_servers",
            "plugins",
            "cwd",
            "permissionMode",
        ):
            assert key in init, f"init record lost {key!r}: {sorted(init)}"
        assert isinstance(init["tools"], list) and "Read" in init["tools"]
        assert isinstance(init["mcp_servers"], list)
        assert "lattice" in {s.get("name") for s in init["mcp_servers"]}
        assert isinstance(init["plugins"], list)  # --setting-sources "": empty on a plain account
        assert init["permissionMode"] == "acceptEdits"

        assistants = [r for r in records if r.get("type") == "assistant"]
        assert assistants and all("usage" in r["message"] for r in assistants)  # StreamUsage input

        res = _result(records)
        assert res["subtype"] == "success" and res["is_error"] is False
        assert (
            isinstance(res["total_cost_usd"], (int, float))
            and 0 < res["total_cost_usd"] < TURN_CAP_USD
        )
        assert isinstance(res["result"], str) and "beta" in res["result"]
        assert isinstance(res["usage"], dict) and res["usage"]["output_tokens"] > 0
        assert isinstance(res["modelUsage"], dict) and res["modelUsage"]
        assert all("costUSD" in v for v in res["modelUsage"].values())
        assert res["num_turns"] >= 2 and res["session_id"] == init["session_id"]
        print(
            f"\nmodel {init['model']}, mcp_servers "
            f"{[(s.get('name'), s.get('status')) for s in init['mcp_servers']]}, "
            f"cost {res['total_cost_usd']}, turns {res['num_turns']}"
        )

    def test_max_turns_is_accepted_and_ends_error_max_turns(self, binary, tmp_path):
        proj, mcp, settings = _scratch(tmp_path)
        argv = _rebuild_argv(
            binary, proj, mcp, settings,
            ["--max-turns", "1", "--max-budget-usd", str(MAX_TURNS_CAP_USD)],
        )  # fmt: skip
        records, code = _run(argv, proj, label="--max-turns 1", cap=MAX_TURNS_CAP_USD)
        res = _result(records)
        assert res["subtype"] == "error_max_turns", res.get("subtype")
        assert res["is_error"] is True and code == 1
        assert res.get("terminal_reason") == "max_turns"
        assert "result" not in res and res["errors"]  # error results carry errors, not result
        assert isinstance(res["total_cost_usd"], (int, float))
        assert "modelUsage" in res and "usage" in res

    def test_a_tiny_budget_ends_error_max_budget_usd(self, binary, tmp_path):
        proj, mcp, settings = _scratch(tmp_path)
        argv = _rebuild_argv(
            binary, proj, mcp, settings, ["--max-budget-usd", str(BUDGET_PROBE_USD)]
        )
        # the cap is checked after a model call, so the first call may overshoot it (P1: ~2x);
        # the whole call is therefore bounded by one turn, far below TURN_CAP_USD
        records, code = _run(argv, proj, label=f"budget {BUDGET_PROBE_USD}", cap=BUDGET_PROBE_USD)
        res = _result(records)
        assert res["subtype"] == "error_max_budget_usd", (
            f"{res.get('subtype')}: a haiku turn may have fit under {BUDGET_PROBE_USD}; "
            "if the CLI got cheaper, lower BUDGET_PROBE_USD, otherwise the subtype was renamed"
        )
        assert res["is_error"] is True and code == 1
        assert res.get("terminal_reason") == "budget_exhausted"
        assert "result" not in res and res["errors"]
        assert res["total_cost_usd"] > 0 and res["total_cost_usd"] < TURN_CAP_USD
        assert res["modelUsage"]  # P1: top-level usage is all zeros here, modelUsage is real

    @pytest.mark.skip(
        reason="guard-deny round trip is covered by pre-flight P2 and node H1/PF evidence "
        "(docs/results/2026-10-30-preflights.md); a live repeat needs a tool-using turn per deny"
    )
    def test_guard_deny_round_trip(self):
        pass

    @pytest.mark.skip(
        reason="Stop-hook exit-2 continuation is covered by pre-flight P3 and node H1 "
        "(docs/results/2026-10-30-preflights.md); a live repeat needs a plugin dir and two turns"
    )
    def test_stop_hook_exit_2_continuation(self):
        pass
