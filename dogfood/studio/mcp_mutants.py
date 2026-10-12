"""The mutation run of API v1.13 (connected MCP servers in Studio, spike 4), pre-registered in
``docs/results/2026-10-12-spike4-prereg.md``.

Modelled on ``sync_mutants.py``: for each mutant, ``lattice/``, ``tests/`` and
``dogfood/studio/project_seed/`` are copied to a scratch directory and the mutant's edits are
applied to the copy (each edit's old text must occur exactly once), then (a)
``tests/studio/test_mcp_intake.py`` runs there with the copy first on ``PYTHONPATH`` and (b) the
dogfood check ``dogfood/studio/check.sh`` runs from the working tree with
``LATTICE_STUDIO_PYTHONPATH=<copy>`` (the served Studio imports the copy; the checker is the
working tree's). A mutant is caught when either fails. A control run of the unpatched copy
comes first. The working tree is never patched. ``--jobs N`` runs N mutants at a time.

MC-M6 (stub mode starts a process) makes the dogfood's served Studio run a real discovery
(``claude -p`` stopped at its init record, no API key) if the ``claude`` binary is installed.

Usage: ``uv run python dogfood/studio/mcp_mutants.py [--out DIR] [--jobs N] [ID ...]``.
Exit 0 when the control passes and every mutant is caught.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import perf_mutants  # noqa: E402
import sync_mutants  # noqa: E402

SUITE = ["tests/studio/test_mcp_intake.py"]
perf_mutants.SUITE = SUITE  # perf_mutants.run_pytest runs the module's SUITE
FILES = ["lattice/studio/mcp_intake.py", "lattice/studio/session.py",
         "lattice/studio/server.py", "lattice/studio/rebuild.py", "lattice/mcp/tools.py",
         "lattice/studio/mock_server.py", "dogfood/studio/spec.py", *SUITE,
         "dogfood/studio/mcp_mutants.py"]  # fmt: skip

IN, R, S = "lattice/studio/mcp_intake.py", "lattice/studio/rebuild.py", "lattice/studio/server.py"

# id -> (behaviour, [(file, old text, new text), ...])
# fmt: off
MUTANTS: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    "MC-M1": (
        "classify treats delete_* as read",
        [(IN,
          "    if words[0] in WRITE_VERBS or any(w in WRITE_VERBS for w in words):\n",
          '    if words[0] == "delete":  # MUTANT MC-M1\n'
          '        return "read"\n'
          "    if words[0] in WRITE_VERBS or any(w in WRITE_VERBS for w in words):\n")],
    ),
    "MC-M2": (
        "the allowlist includes unknown tools",
        [(IN,
          '        reads = list(tools.get("read") or [])\n'
          '        others = [*(tools.get("write") or []), *(tools.get("unknown") or [])]\n',
          '        reads = [*(tools.get("read") or []), *(tools.get("unknown") or [])]  # MUTANT\n'
          '        others = list(tools.get("write") or [])\n')],
    ),
    "MC-M3": (
        "the rebuild argv loses --strict-mcp-config",
        [(R,
          '        "--strict-mcp-config",\n        "--mcp-config",\n',
          '        "--mcp-config",  # MUTANT MC-M3\n')],
    ),
    "MC-M4": (
        "allow_writes ignored: writes allowed everywhere",
        [(IN,
          '        if mode == "change" and connected and _writes_ok(s, allow_writes):\n',
          "        if mode in CHAT_MODES and connected:  # MUTANT MC-M4\n")],
    ),
    "MC-M5": (
        "provenance not stored on the change record",
        [(S,
          "            self.changes[cid] = empty_change(cid, request, parent_id, source)\n",
          "            self.changes[cid] = empty_change(cid, request, parent_id)  # MUTANT\n")],
    ),
    "MC-M6": (
        "stub mode starts a discovery process",
        [(S,
          "            if self._mcp is None and not self.stub_llm:\n",
          "            if self._mcp is None:  # MUTANT MC-M6\n")],
    ),
}
# fmt: on

sync_mutants.MUTANTS = MUTANTS  # reuse its apply / one / main with this module's mutants
sync_mutants.SUITE = SUITE
sync_mutants.FILES = FILES

if __name__ == "__main__":
    sys.exit(sync_mutants.main())
