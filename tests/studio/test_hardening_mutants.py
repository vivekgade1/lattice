"""The mutation run of the hardening findings (HD-1 to HD-4), pre-registered in
``docs/results/2026-10-16-hardening-findings-prereg.md`` (section 4: mutants HD-M1 to HD-M16).

Not a test: pytest collects this file (its name matches ``test_*.py``) and finds nothing to
run, and importing it changes nothing. All the work is in :func:`main`.

Modelled on ``tests/studio/test_mcp_intake_mutants.py``: for each mutant, ``lattice/``,
``tests/``, ``docs/examples/`` and the dogfood seed are copied to a scratch directory and the
mutant's edit (its old text must occur exactly once) is applied to the copy; then SUITE (the
real-Studio HTTP module and the three new unit modules) runs there with the copy first on
``PYTHONPATH``. A mutant is caught when SUITE exits non-zero. Unlike the MH run, the dogfood
check is not part of the catcher (the stub backend runs no discovery and no ``claude``), as
section 4 of the pre-registration states: "catcher = the pytest SUITE". A control run of the
unpatched copy comes first; the working tree is never patched. A run over 600 s is recorded
TIMEOUT, a finding, never retried.

Usage: ``uv run python tests/studio/test_hardening_mutants.py [--out DIR] [--jobs N] [ID ...]``
with ``TMPDIR`` under the scratch directory. Exit 0 when the control passes and every mutant is
caught.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUITE = [
    "tests/studio/test_mcp_intake_http.py",
    "tests/llm/test_cli_env_scrub.py",
    "tests/modules/test_entry_summary.py",
    "tests/studio/test_mcp_allow_writes_warn.py",
]
CL, IN, SV = "lattice/llm/cli_client.py", "lattice/studio/mcp_intake.py", "lattice/studio/server.py"
MO = "lattice/modules/__init__.py"
FILES = [CL, IN, SV, MO, *SUITE, "tests/studio/test_hardening_mutants.py"]
TIMEOUT_S = 600

ENTRY_IF = "    if m.entry is not None:  # declared Python, announced as CODE (nothing imports it at install)\n"  # noqa: E501
ENTRY_SHOWN = "        shown = m.entry if _ENTRY_NAME.fullmatch(m.entry) else json.dumps(m.entry)\n"
ENTRY_APPEND = '        lines.append(\n            f"  entry:    {shown}'
WRITES = '                            "writes_allowed": s.get("status") == "connected"\n'

# id -> (behaviour, [(file, old text, new text), ...])
# fmt: off
MUTANTS: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    "HD-M1": (
        "the scrub is back to the three names and the CLAUDE_CODE_USE_ prefix",
        [(CL,
          '_SCRUBBED_ENV = ("AWS_BEARER_TOKEN_BEDROCK",)\n'
          '_SCRUBBED_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_USE_")\n',
          '_SCRUBBED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")'
          "  # MUTANT HD-M1\n"
          '_SCRUBBED_PREFIXES = ("CLAUDE_CODE_USE_",)\n')],
    ),
    "HD-M2": (
        "AWS_BEARER_TOKEN_BEDROCK is no longer scrubbed",
        [(CL,
          '_SCRUBBED_ENV = ("AWS_BEARER_TOKEN_BEDROCK",)\n',
          "_SCRUBBED_ENV = ()  # MUTANT HD-M2\n")],
    ),
    "HD-M3": (
        "over-scrub: every CLAUDE_CODE_ name goes (the OAuth token too)",
        [(CL,
          '_SCRUBBED_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_USE_")\n',
          '_SCRUBBED_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_")  # MUTANT HD-M3\n')],
    ),
    "HD-M4": (
        "over-scrub: every AWS_ name goes",
        [(CL,
          '_SCRUBBED_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_USE_")\n',
          '_SCRUBBED_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_USE_", "AWS_")  # MUTANT HD-M4\n')],
    ),
    "HD-M5": (
        "the scrub matches case-insensitively",
        [(CL,
          "not k.startswith(_SCRUBBED_PREFIXES)",
          "not k.upper().startswith(_SCRUBBED_PREFIXES)  # MUTANT HD-M5")],
    ),
    "HD-M6": (
        "describe() does not announce an entry",
        [(MO, ENTRY_IF, "    if False:  # MUTANT HD-M6\n")],
    ),
    "HD-M7": (
        "the entry line is printed for every custom module",
        [(MO, ENTRY_IF + ENTRY_SHOWN,
          '    if m.kind == "custom":  # MUTANT HD-M7\n'
          "        shown = str(m.entry)\n")],
    ),
    "HD-M8": (
        "the entry line is placed before provides",
        [(MO, ENTRY_APPEND,
          '        lines.insert(  # MUTANT HD-M8\n'
          '            -2,\n            f"  entry:    {shown}')],
    ),
    "HD-M9": (
        "the entry is printed raw (no JSON quoting)",
        [(MO, ENTRY_SHOWN, "        shown = m.entry  # MUTANT HD-M9\n")],
    ),
    "HD-M10": (
        "writes_allowed is back to the allow-list alone",
        [(IN,
          WRITES + "                            and _writes_ok(s, self.allow_writes)})"
          "  # fmt: skip\n",
          '                            "writes_allowed": _writes_ok(s, self.allow_writes)})'
          "  # MUTANT HD-M10\n")],
    ),
    "HD-M11": (
        "writes_allowed is true for every status but failed",
        [(IN, WRITES,
          '                            "writes_allowed": s.get("status") != "failed"'
          "  # MUTANT HD-M11\n")],
    ),
    "HD-M12": (
        "the server builds the intake without a warn callback",
        [(SV,
          "allow_writes=self.allow_writes, warn=log\n",
          "allow_writes=self.allow_writes  # MUTANT HD-M12\n")],
    ),
    "HD-M13": (
        "a name is unmatched unless it is a display name (the prefix form warns)",
        [(IN,
          "            if any(_writes_ok(s, [name]) for s in servers):\n",
          '            if any(str(s.get("name") or "") == name for s in servers):'
          "  # MUTANT HD-M13\n")],
    ),
    "HD-M14": (
        "the warning is not deduplicated",
        [(IN,
          "                if (name, listed) in self._warned:\n",
          "                if False:  # MUTANT HD-M14\n")],
    ),
    "HD-M15": (
        "the warning is emitted while a server is pending",
        [(IN,
          '        if any(s.get("status") == "pending" for s in servers):\n',
          "        if False:  # MUTANT HD-M15\n")],
    ),
    "HD-M16": (
        "_writes_ok compares names case-insensitively",
        [(IN,
          "    return name in names or server_prefix(name)[5:-2] in names\n",
          "    names = {n.casefold() for n in names}  # MUTANT HD-M16\n"
          "    return (name.casefold() in names\n"
          "            or server_prefix(name)[5:-2].casefold() in names)\n")],
    ),
}
# fmt: on


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(ROOT / "dogfood" / "studio"))
    import perf_mutants
    import sync_mutants

    perf_mutants.SUITE = SUITE  # perf_mutants.run_pytest runs the module's SUITE
    sync_mutants.MUTANTS = MUTANTS  # reuse its apply / main with this module's mutants
    sync_mutants.SUITE = SUITE
    sync_mutants.FILES = FILES

    def copy_tree(dst: Path) -> None:
        perf_mutants.copy_tree(dst)
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
        shutil.copytree(ROOT / "docs" / "examples", dst / "docs" / "examples", ignore=ignore)

    def one(mid: str) -> dict[str, object]:
        with tempfile.TemporaryDirectory(prefix=f"lattice-hd-mutant-{mid}-") as d:
            dst = Path(d)
            copy_tree(dst)
            if mid != "control":
                sync_mutants.apply(dst, mid)
            res: dict[str, object] = {"pytest": perf_mutants.run_pytest(dst)}
        py = res["pytest"]
        timed_out = py["exit"] == 124 or float(py["seconds"]) > TIMEOUT_S  # type: ignore[index]
        caught_by = ["pytest"] if py["exit"] != 0 else []  # type: ignore[index]
        if timed_out:
            verdict = "TIMEOUT"
        elif mid == "control":
            verdict = "PASS (control)" if not caught_by else "CONTROL FAILED"
        else:
            verdict = "caught" if caught_by else "SURVIVED"
        # sync_mutants.main prints res["dogfood"]; the dogfood check is not a catcher here
        res["dogfood"] = {
            "exit": 0,
            "seconds": 0,
            "summary": "not run (not a catcher)",
            "first_failures": [],
        }
        return {**res, "caught_by": caught_by, "verdict": verdict}

    sync_mutants.one = one
    return sync_mutants.main(argv)


if __name__ == "__main__":
    sys.exit(main())
