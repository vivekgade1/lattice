"""The mutation run of the connected-MCP rules over HTTP (R80/R81), pre-registered in
``docs/results/2026-10-15-mcp-http-prereg.md`` (mutants MH-1 to MH-14).

Not a test: pytest collects this file (its name matches ``test_*.py``) and finds nothing to
run, and importing it changes nothing. All the work is in :func:`main`.

Like ``dogfood/studio/mcp_mutants.py`` it plugs its ``MUTANTS``, ``SUITE`` and ``FILES`` into
``dogfood/studio/sync_mutants.py``: for each mutant, ``lattice/``, ``tests/`` and the seed are
copied to a scratch directory and the mutant's edit (its old text must occur exactly once) is
applied to the copy; then (a) ``tests/studio/test_mcp_intake_http.py`` runs there with the copy
first on ``PYTHONPATH`` (the module starts a REAL Studio from the copy, with a fake claude) and
(b) ``dogfood/studio/check.sh`` runs from the working tree with
``LATTICE_STUDIO_PYTHONPATH=<copy>``. A mutant is caught when either exits non-zero. A control
run of the unpatched copy comes first; the working tree is never patched. A run (pytest or
dogfood) that exceeds 600 s is recorded TIMEOUT (Amendment 1.D), a finding, never retried.

Usage: ``uv run python tests/studio/test_mcp_intake_mutants.py [--out DIR] [--jobs N] [ID ...]``
with ``TMPDIR`` under the scratch directory. Exit 0 when the control passes and every mutant is
caught.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUITE = ["tests/studio/test_mcp_intake_http.py"]
IN, SE, SV = "lattice/studio/mcp_intake.py", "lattice/studio/session.py", "lattice/studio/server.py"
FILES = [IN, SE, SV, "dogfood/studio/spec.py", "dogfood/studio/spec.trace.json", *SUITE,
         "tests/studio/test_mcp_intake_mutants.py"]  # fmt: skip
TIMEOUT_S = 600

# id -> (behaviour, [(file, old text, new text), ...])
# fmt: off
MUTANTS: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    "MH-1": (
        "allow_writes is honoured in ask and explain",
        [(IN,
          '        if mode == "change" and connected and _writes_ok(s, allow_writes):\n',
          "        if mode in CHAT_MODES and connected and _writes_ok(s, allow_writes):"
          "  # MUTANT MH-1\n")],
    ),
    "MH-2": (
        "an unknown verb is treated as read",
        [(IN,
          '    if words[0] in READ_VERBS:\n        return "read"\n    return "unknown"\n',
          '    if words[0] in READ_VERBS:\n        return "read"\n'
          '    return "read"  # MUTANT MH-2\n')],
    ),
    "MH-3": (
        "the cache is not refreshed",
        [(IN,
          "        if hit is not None and not refresh and time.monotonic() - hit[0] < ttl:\n",
          "        if hit is not None and time.monotonic() - hit[0] < ttl:  # MUTANT MH-3\n")],
    ),
    "MH-4": (
        "the cache is never used",
        [(IN,
          "        if hit is not None and not refresh and time.monotonic() - hit[0] < ttl:\n",
          "        if False and hit is not None:  # MUTANT MH-4\n")],
    ),
    "MH-5": (
        "the route ignores ?refresh",
        [(SV,
          '            return self._dispatch(lambda: st.get_mcp(raw.lower() in ("1", "true")))\n',
          "            return self._dispatch(lambda: st.get_mcp(False))  # MUTANT MH-5\n")],
    ),
    "MH-6": (
        "allow_writes never opens a server",
        [(IN,
          "    return name in names or server_prefix(name)[5:-2] in names\n",
          "    return False  # MUTANT MH-6\n")],
    ),
    "MH-7": (
        "servers that are not connected count as connected",
        [(IN,
          '        connected = s.get("status") == "connected"\n',
          "        connected = True  # MUTANT MH-7\n")],
    ),
    "MH-8": (
        "write and unknown tools are left off --disallowedTools",
        [(SE,
          "    denied = [*prof.disallowed, *external_denied]\n",
          "    denied = list(prof.disallowed)  # MUTANT MH-8\n")],
    ),
    "MH-9": (
        "the sessions are wired without the intake",
        [(SV,
          "                    mcp=self.mcp_intake,\n",
          "                    mcp=None,  # MUTANT MH-9\n")],
    ),
    "MH-10": (
        "a turn's environment keeps the credentials",
        [(SE,
          "        env = cli_env(base, self.max_tokens)\n",
          "        env = dict(base)  # MUTANT MH-10\n")],
    ),
    "MH-11": (
        "a discovery's environment keeps the credentials",
        [(IN,
          "    out = cli_env(env if env is not None else os.environ, None)\n",
          "    out = dict(env if env is not None else os.environ)  # MUTANT MH-11\n")],
    ),
    "MH-12": (
        "a chat argv regains --strict-mcp-config",
        [(SE,
          '        "--setting-sources", "", "--disable-slash-commands",\n',
          '        "--setting-sources", "", "--disable-slash-commands",\n'
          '        "--strict-mcp-config",  # MUTANT MH-12\n')],
    ),
    "MH-13": (
        "change mode loses the multi-project tools",
        [(SE,
          "        (*_READ_TOOLS, *_MODEL_TOOLS, *_MULTI_TOOLS, *_CHANGE_TOOLS),\n",
          "        (*_READ_TOOLS, *_MODEL_TOOLS, *_CHANGE_TOOLS),  # MUTANT MH-13\n")],
    ),
    "MH-14": (
        "chat mode rebuild is enabled",
        [(SE,
          '        True,\n        False,\n        "Mode: rebuild.",\n',
          '        True,\n        True,  # MUTANT MH-14\n        "Mode: rebuild.",\n')],
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
    plain_one = sync_mutants.one

    def one(mid: str) -> dict[str, object]:
        res = plain_one(mid)
        slow = [
            k for k in ("pytest", "dogfood")
            if res[k]["exit"] == 124 or float(res[k]["seconds"]) > TIMEOUT_S  # type: ignore[index]
        ]  # fmt: skip
        if slow:
            res["verdict"] = "TIMEOUT"
            res["timeout"] = slow
        return res

    sync_mutants.one = one
    return sync_mutants.main(argv)


if __name__ == "__main__":
    sys.exit(main())
