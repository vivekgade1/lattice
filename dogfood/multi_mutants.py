"""The mutation run of the multi-project layers 1 and 2 (one MCP server for many projects, the
guard's nearest-trace owners, the workspace manifest), pre-registered with
``docs/results/2026-10-12-multi-prereg.md``.

Modelled on ``dogfood/improve_mutants.py``: for each mutant, ``lattice/``, ``tests/`` and
``dogfood/`` are copied to a scratch directory and the mutant's edit is applied to the copy (its
old text must occur exactly once in the CURRENT source), then the suite runs there with the copy
first on ``PYTHONPATH``. A mutant is CAUGHT when the suite fails. A control run of the unpatched
copy comes first and must pass. The working tree is never patched.

The six mutants and the tests predicted to catch each (the prereg named the files; the test
names were filled in after the tests were written, before this run):

* MM1 the guard protects the first explicit project only (no nearest-trace owner)
      -> test_multi_guard: test_each_project_protects_its_own_spec[none|first],
         test_relative_and_dotdot_paths..., test_symlink_aliases..., test_bash_forms_with_no_
         explicit_project, test_the_hook_names_the_project_in_its_reason
* MM2 the ``project`` argument is ignored (always the active project)
      -> test_multi_server: test_the_project_argument_picks_the_project,
         test_an_unknown_project_is_a_tool_error..., test_one_server_with_no_project_and_a_workspace
* MM3 ``project_set_active`` does not persist
      -> test_multi_server: test_project_set_active_persists_for_the_process,
         test_one_server_with_no_project_and_a_workspace
* MM4 a ``depends_on`` cycle is accepted
      -> test_workspace: test_each_error_class...[depends_on_cycle], test_the_cycle_message...,
         test_cli_validate_rejects_each_error_class[depends_on_cycle]; test_studio_api:
         test_errors_are_listed...[depends_on_cycle]
* MM5 impact is not transitive (direct dependents only)
      -> test_workspace: test_impact_follows_a_chain...; test_multi_server:
         test_workspace_impact_is_transitive... (test_impact_is_transitive_over_links does NOT
         catch it: both of shared-auth's dependents are direct)
* MM6 a single-project server adds the ``project`` field
      -> test_multi_server: test_a_single_project_router_is_the_old_toolbox,
         test_a_single_project_server_lists_the_old_tools_first

Usage: ``uv run python dogfood/multi_mutants.py [--out FILE] [--jobs N] [ID ...]``.
Exit 0 when the control passes and every mutant is caught.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITE = ["tests/mcp", "tests/workspace"]
GD, PR, WS = "lattice/mcp/guard.py", "lattice/mcp/projects.py", "lattice/workspace.py"
FILES = [GD, PR, WS, "lattice/mcp/server.py", "lattice/studio/hub.py", "dogfood/multi_mutants.py"]

# id -> (behaviour, file, old text, new text)
MUTANTS: dict[str, tuple[str, str, str, str]] = {
    "MM1": (
        "the guard protects the first explicit project only",
        GD,
        "    def _guards_for(self, resolved: str) -> list[Guard]:\n"
        "        out = list(self.static)\n"
        "        g = self._owner_guard(resolved)\n"
        "        if g is not None and g not in out:\n"
        "            out.append(g)\n"
        "        return out\n",
        "    def _guards_for(self, resolved: str) -> list[Guard]:\n"
        "        return list(self.static[:1])  # MUTANT MM1: the active project only\n",
    ),
    "MM2": (
        "the project argument is ignored (always the active project)",
        PR,
        '        entry = self.resolve(rest.pop("project", None))\n',
        '        rest.pop("project", None)  # MUTANT MM2: the argument is ignored\n'
        "        entry = self.projects[self.active]\n",
    ),
    "MM3": (
        "project_set_active does not persist",
        PR,
        "            previous, self.active = self.active, entry.name\n",
        "            previous = self.active  # MUTANT MM3: not persisted\n",
    ),
    "MM4": (
        "a depends_on cycle is accepted",
        WS,
        "    cyc = _cycle(links, taken)\n",
        "    cyc = None  # MUTANT MM4: cycles accepted\n",
    ),
    "MM5": (
        "impact is not transitive (direct dependents only)",
        WS,
        "                        order.append(d)\n                        nxt.append(d)\n",
        "                        order.append(d)  # MUTANT MM5: one level only\n",
    ),
    "MM6": (
        "a single-project server adds the project field",
        PR,
        "        if self.multi and isinstance(out, Mapping):\n",
        "        if isinstance(out, Mapping):  # MUTANT MM6: always tagged\n",
    ),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def copy_tree(dst: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".ruff_cache")
    for rel in ("lattice", "tests", "dogfood"):
        shutil.copytree(ROOT / rel, dst / rel, ignore=ignore)


def apply(dst: Path, mid: str) -> None:
    _what, rel, old, new = MUTANTS[mid]
    path = dst / rel
    text = path.read_text(encoding="utf-8")
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{mid}: the patched text occurs {n} times in {rel} (must be once)")
    path.write_text(text.replace(old, new), encoding="utf-8")


def _env(dst: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    env["PYTHONPATH"] = str(dst)
    return env


def run_pytest(dst: Path) -> dict[str, object]:
    t0 = time.monotonic()
    try:
        r = subprocess.run(
            ["uv", "run", "--project", str(ROOT), "pytest", *SUITE, "-q", "-p",
             "no:cacheprovider", "-rf", "--no-header"],
            cwd=str(dst), env=_env(dst), capture_output=True, text=True, timeout=900,
        )  # fmt: skip
        out, rc = r.stdout + r.stderr, r.returncode
    except subprocess.TimeoutExpired as e:
        out, rc = str(e.stdout or "") + "\n[timeout after 900s]", 124
    failed = sorted({m.group(1) for m in re.finditer(r"^FAILED (\S+)", out, re.M)})
    summary = next((ln for ln in reversed(out.splitlines())
                    if re.search(r"\d+ (passed|failed)", ln)), "").strip()  # fmt: skip
    return {"exit": rc, "failed": failed, "summary": summary,
            "seconds": round(time.monotonic() - t0, 1), "tail": out[-2000:]}  # fmt: skip


def one(mid: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix=f"lattice-mp-mutant-{mid}-") as d:
        dst = Path(d)
        copy_tree(dst)
        if mid != "control":
            apply(dst, mid)
        res = run_pytest(dst)
    caught = res["exit"] != 0
    if mid == "control":
        verdict = "PASS (control)" if not caught else "CONTROL FAILED"
    else:
        verdict = "caught" if caught else "SURVIVED"
    return {"pytest": res, "verdict": verdict}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ids", nargs="*", default=list(MUTANTS))
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--jobs", type=int, default=2)
    args = ap.parse_args(argv)
    hashes = {f: sha(ROOT / f) for f in FILES}
    print("hashes:", json.dumps(hashes), flush=True)
    with tempfile.TemporaryDirectory() as d:  # every patch applies cleanly before anything runs
        copy_tree(Path(d))
        for mid in args.ids:
            if mid not in MUTANTS:
                raise SystemExit(f"unknown mutant {mid}")
            apply(Path(d), mid)
    results: dict[str, object] = {"hashes": hashes, "runs": {}}
    control = one("control")
    results["runs"]["control"] = control  # type: ignore[index]
    print(f"control: {control['verdict']} ({control['pytest']['summary']})", flush=True)
    ok = control["verdict"] == "PASS (control)"
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        futs = {ex.submit(one, mid): mid for mid in args.ids}
        for fut in concurrent.futures.as_completed(futs):
            mid = futs[fut]
            res = fut.result()
            results["runs"][mid] = res  # type: ignore[index]
            failed = res["pytest"]["failed"]  # type: ignore[index]
            print(f"{mid}: {res['verdict']} (pytest failed {len(failed)}: "
                  f"{', '.join(f.split('::')[-1] for f in failed)})", flush=True)  # fmt: skip
            ok = ok and res["verdict"] == "caught"
    if args.out:
        args.out.write_text(json.dumps(results, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
