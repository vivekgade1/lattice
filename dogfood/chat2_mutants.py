"""Mutants of the chat round 2 build (docs/results/2026-10-18-goto-chat-round2-prereg.md, section 4):
CM0 (the control, unmutated, must pass every killer) and CM1..CM19, each one logical edit (a list of
asserted text replacements, each of which must occur exactly once) in a scratch copy of the package
and its tests.

    "$DRV" dogfood/chat2_mutants.py --scratch "$SCRATCH/m" --dev-scratch "$SCRATCH/p" \\
        --out docs/results/data/2026-10-18-goto-chat-round2 CM0 CM1 ... CM19

The killers, run against the mutant's own copy (``PYTHONPATH`` and the working directory are the
copy; the repository is never touched):

* ``unit``: the four new test files (tests/spec/test_claim_check.py, test_chat_route.py,
  tests/studio/test_chat_check_session.py, test_chat_check_api.py), exact output bytes included;
* ``replay``: PC1, PC2 and PC3 of ``chat2_probes.py --stage replay`` (round 1's answers through the
  checker, scored by the frozen scorer) on the dev world of a dev run (``--dev-scratch``);
* ``pw``: PW2-lite, a Studio over the committed fixture world with a recording ``claude`` shim on
  PATH: an ask, a checked answer and a routed question must leave the shim log empty and the project,
  inventory and state directories as they were.

A kill by a named unit test is disclosed as such (round-1 practice). The mutants carry the texts of
the build as it is frozen; an edit of a module that changes a replaced line makes the run say so
(the replacement does not occur exactly once) instead of passing.
"""

# ruff: noqa: E501 -- mutant texts are data lines
from __future__ import annotations

import argparse
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

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parent

CC = "lattice/spec/claim_check.py"
CR = "lattice/spec/chat_route.py"
SS = "lattice/studio/session.py"

# (id, description, [(file, old, new)], killers in order)
MUTANTS: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    "CM0": ("control: the build as it is", []),
    "CM1": (
        "any well-formed t1-/t2- id counts as support, even from another change's report (E2 off for t ids)",
        [
            (
                CC,
                "        bad = [i for i in ANY_TID.findall(u) if i not in self.known and i not in self.q_ids]\n",
                "        bad = []\n",
            ),
            (CC, "    if any(i in u for i in p.support):\n", "    if T12_ID.search(u):\n"),
        ],
    ),
    "CM2": (
        "a t3- id counts as support",
        [
            (
                CC,
                "    if any(i in u for i in p.support):\n",
                '    if any(i in u for i in p.support) or re.search(r"\\bt3-[0-9a-f]{10}\\b", u):\n',
            )
        ],
    ),
    "CM3": (
        "may, might and could are accepted as an owner label",
        [
            (
                CC,
                'OWNER_LABEL_RE = re.compile(\n    r"\\b(?:not decided|undecid\\w*|static\\w*|not run|no change\\w*)\\b", re.IGNORECASE\n)\n',
                "OWNER_LABEL_RE = HEDGE_RE\n",
            )
        ],
    ),
    "CM4": (
        "negations are exempted (the lenient rule used as the checker rule)",
        [
            (
                CC,
                "    spec = bool(HEDGE_RE.search(u)) and not OWNER_LABEL_RE.search(u)\n",
                '    spec = bool(HEDGE_RE.search(u)) and not OWNER_LABEL_RE.search(u)\n    if re.search(r"\\b(?:nothing|no|not|never|nobody|without|cannot|\\w+n\'t)\\b", u, re.I):\n        return _Disp("labelled", "D1", None, True, False)\n',
            )
        ],
    ),
    "CM5": (
        "the sentence splitter splits on lines only",
        [(CC, '_SPLIT = re.compile(r"(?<=[.!?])\\s+")\n', '_SPLIT = re.compile(r"(?!x)x")\n')],
    ),
    "CM6": (
        "label laundering: the model's sentence is kept and a label appended instead of replaced",
        [
            (
                CC,
                "            return _Disp(name, reason, repl, True, spec)\n",
                '            return _Disp(name, reason, u + " (static: not decided)", True, spec)\n',
            )
        ],
    ),
    "CM7": (
        "E1 off: no Lattice record when the text does not state the ids",
        [(CC, "            if v.t12_ids - stated:\n", "            if False:\n")],
    ),
    "CM8": (
        "E2 off: a fabricated t2- / wf- / Rn id is kept",
        [
            (
                CC,
                "    def unknown_ids(self, u: str) -> list[str]:\n",
                "    def unknown_ids(self, u: str) -> list[str]:\n        return []\n",
            )
        ],
    ),
    "CM9": (
        "a flagged table row is kept",
        [
            (
                CC,
                "            table_rows.setdefault(u.line, []).append(d)\n            continue\n",
                "            continue\n",
            )
        ],
    ),
    "CM10": (
        "text blocks before a tool call are shown unchecked (only the later ones are checked)",
        [
            (
                SS,
                "        shown, counts = tc.block(raw)\n",
                "        shown, counts = tc.block(raw) if self._tool_ids else (raw, {})\n",
            )
        ],
    ),
    "CM11": (
        "partial text events are emitted in check mode",
        [
            (
                SS,
                "        if self._tc is not None:\n            return  # API v1.26: no text reaches the UI before its check (no partial events)\n",
                "        if False:\n            return\n",
            )
        ],
    ),
    "CM12": (
        "_CHECK_RULES is appended without [chat]",
        [
            (
                SS,
                "        if check:  # API v1.26: only with `[chat] check = true`\n",
                "        if True:\n",
            )
        ],
    ),
    "CM13": (
        "a routed session turn spawns claude after answering",
        [
            (
                SS,
                '                    s.transcript.routed_turn(routed[0], routed[1], tc)\n                    return {"turn": s.transcript.turns}\n',
                "                    s.transcript.routed_turn(routed[0], routed[1], tc)\n",
            )
        ],
    ),
    "CM14": (
        "MR1 answers 'built' from a route-prefix heuristic instead of the endpoint status",
        [
            (
                CR,
                "    ep, rt = _route_row(am, key)\n    first = _status_sentence(key, ep)\n",
                '    ep, rt = _route_row(am, key)\n    ep = next((e for e in am.doc["endpoints"] if e["status"] == "implemented" and e["key"].split()[1].split("/")[1] == key.split()[1].split("/")[1]), ep)\n    first = _status_sentence(key, ep)\n',
            )
        ],
    ),
    "CM15": (
        "the relevant report is built with tier 1 (a process is started)",
        [
            (
                CC,
                "                v = view_of_body(cid, self._lookup(cid))\n",
                '                import subprocess\n\n                subprocess.run(["lattice-impact-observe"], capture_output=True, check=False)\n                v = view_of_body(cid, self._lookup(cid))\n',
            )
        ],
    ),
    "CM16": (
        "not idempotent: the checker re-labels its own output",
        [
            (
                CC,
                '        return _Disp("labelled", "D1", None, True, False)\n',
                '        return _Disp("replaced_item", "D1", u + " (static)", True, False)\n',
            )
        ],
    ),
    "CM17": (
        "D6 drops the static neighbourhood",
        [(CC, '    return head + " " + touches_line(am, key)\n', '    return head + " Static."\n')],
    ),
    "CM18": (
        "D4 renders a paraphrase instead of the ReviewData text",
        [(CC, "    body = parts[0]\n", '    body = "the rule concerns " + parts[0].lower()\n')],
    ),
    "CM19": (
        "synonym evasion: 'fails' is rewritten to 'does not pass' and the unit kept",
        [
            (
                CC,
                '    return _Disp("withheld", "D7", None, True, spec)\n',
                '    return _Disp("replaced_item", "D7", re.sub(r"\\b(fails|failed|fail)\\b", "does not pass", u), True, spec)\n',
            )
        ],
    ),
}

UNIT_TESTS = [
    "tests/spec/test_claim_check.py",
    "tests/spec/test_chat_route.py",
    "tests/studio/test_chat_check_session.py",
    "tests/studio/test_chat_check_api.py",
]
COPY_FILES = [
    "docs/results/data/2026-10-18-goto-chat-round2/frozen-r1/goto_score.py",
    "docs/results/2026-10-18-goto-chat-round2-prereg.md",
]

PW_SCRIPT = r"""
import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.getcwd())
from tests.studio.test_appmap_api import make_world
from tests.studio.test_chat_check_api import ModelSays, set_chat
from lattice.studio.server import Studio
import hashlib
def tree(root):
    out = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out
tmp = Path(tempfile.mkdtemp(prefix="cm-pw-"))
w = make_world(tmp)
set_chat(w, "route = true\ncheck = true\n")
st = Studio(w["project"], generator=ModelSays())
st.chat_context("What breaks if c1 is applied?").report("c1")
before = {k: tree(w[k]) for k in ("project", "inventory", "app")}
st.ask({"question": "Is a project limited?"})
st.ask({"question": "Which rules are required by the spec but not built yet?"})
st.chat_pre_route("What breaks if c1 is applied?")
after = {k: tree(w[k]) for k in ("project", "inventory", "app")}
log = Path(os.environ["CM_SHIM_LOG"])
calls = log.read_text().splitlines() if log.exists() else []
ok = before == after and not calls
print(json.dumps({"changed": before != after, "calls": len(calls)}))
sys.exit(0 if ok else 1)
"""


def apply_edits(dst: Path, edits: list[tuple[str, str, str]]) -> list[str]:
    problems = []
    for rel, old, new in edits:
        p = dst / rel
        text = p.read_text()
        n = text.count(old)
        if n != 1:
            problems.append(f"{rel}: the text to replace occurs {n} times")
            continue
        p.write_text(text.replace(old, new, 1))
    return problems


def make_copy(scratch: Path, mid: str) -> Path:
    dst = scratch / mid
    shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True)
    shutil.copytree(REPO / "lattice", dst / "lattice", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(REPO / "tests", dst / "tests", ignore=shutil.ignore_patterns("__pycache__"))
    for rel in COPY_FILES:
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, dst / rel)
    for extra in ("pyproject.toml",):
        if (REPO / extra).is_file():
            shutil.copy2(REPO / extra, dst / extra)
    return dst


def env_of(dst: Path, shim: Path | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(dst)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if shim is not None:
        env["PATH"] = f"{shim}{os.pathsep}{env['PATH']}"
        env["CM_SHIM_LOG"] = str(shim / "calls.log")
    return env


def run_unit(dst: Path) -> dict[str, Any]:
    p = subprocess.run(
        [sys.executable, "-m", "pytest", *UNIT_TESTS, "-q", "--no-header", "-p", "no:cacheprovider", "-rf"],
        capture_output=True, text=True, cwd=dst, env=env_of(dst), timeout=900, check=False,
    )  # fmt: skip
    failed = sorted(set(re.findall(r"^FAILED (\S+)", p.stdout, re.M)))
    return {
        "rc": p.returncode,
        "failed": failed,
        "summary": (p.stdout.strip().splitlines() or [p.stderr[-200:]])[-1],
    }


def run_replay(dst: Path, dev_scratch: Path, args: argparse.Namespace) -> dict[str, Any]:
    p = subprocess.run(
        [sys.executable, str(REPO / "dogfood/chat2_probes.py"), "--stage", "replay", "--scratch", str(dev_scratch), "--out", str(dst / "out"),
         "--round1", str(REPO / "docs/results/data/2026-10-18-goto-chat-round2/frozen-r1"),
         "--round1-answers", str(REPO / "docs/results/data/2026-10-16-goto-place")],
        capture_output=True, text=True, cwd=dst, env=env_of(dst), timeout=900, check=False,
    )  # fmt: skip
    last = (p.stdout.strip().splitlines() or [""])[-1]
    try:
        rows = json.loads(last)
    except ValueError:
        rows = {"error": (p.stderr or p.stdout)[-300:]}
    return {"rc": p.returncode, "probes": rows}


def run_pw(dst: Path, scratch: Path) -> dict[str, Any]:
    shim = scratch / f"shim-{dst.name}"
    shutil.rmtree(shim, ignore_errors=True)
    shim.mkdir(parents=True)
    for name in ("claude", "lattice-impact-observe"):
        sh = shim / name
        sh.write_text(f'#!/bin/sh\necho "{name} $@" >> "{shim / "calls.log"}"\nexit 1\n')
        sh.chmod(0o755)
    p = subprocess.run(
        [sys.executable, "-c", PW_SCRIPT],
        capture_output=True,
        text=True,
        cwd=dst,
        env=env_of(dst, shim),
        timeout=600,
        check=False,
    )
    last = (p.stdout.strip().splitlines() or [""])[-1]
    return {"rc": p.returncode, "out": last or p.stderr[-300:]}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ids", nargs="*", help="CM0 CM1 ... (default: all)")
    ap.add_argument("--scratch", required=True)
    ap.add_argument(
        "--dev-scratch", required=True, help="the --scratch of a dev run of chat2_probes.py"
    )
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    scratch = Path(args.scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    ids = args.ids or list(MUTANTS)
    rows: dict[str, Any] = {}
    t0 = time.perf_counter()
    for mid in ids:
        desc, edits = MUTANTS[mid]
        dst = make_copy(scratch, mid)
        problems = apply_edits(dst, edits)
        row: dict[str, Any] = {"mutant": desc, "edits": len(edits)}
        if problems:
            row.update(status="ERROR", problems=problems)
            rows[mid] = row
            print(f"{mid:<5} ERROR    {problems}")
            continue
        unit, replay, pw = (
            run_unit(dst),
            run_replay(dst, Path(args.dev_scratch), args),
            run_pw(dst, scratch),
        )
        killers = []
        if unit["rc"] != 0:
            killers.append({"kind": "unit", "tests": unit["failed"][:8] or [unit["summary"]]})
        if replay["rc"] != 0:
            killers.append({"kind": "replay", "probes": replay["probes"]})
        if pw["rc"] != 0:
            killers.append({"kind": "pw", "out": pw["out"]})
        killed = bool(killers)
        control = mid == "CM0"
        row.update(status=("PASS" if not killed else "FAIL") if control else ("KILLED" if killed else "SURVIVED"), killers=killers,
                   unit=unit["summary"], replay=replay["probes"], pw=pw["out"])  # fmt: skip
        rows[mid] = row
        print(f"{mid:<5} {row['status']:<9} {[k['kind'] for k in killers]} {desc[:70]}", flush=True)
        shutil.rmtree(dst, ignore_errors=True)
    control_ok = rows.get("CM0", {}).get("status", "PASS") == "PASS"
    killed = [m for m, r in rows.items() if r["status"] == "KILLED"]
    survived = [m for m, r in rows.items() if r["status"] in ("SURVIVED", "ERROR")]
    summary = {"seconds": round(time.perf_counter() - t0, 1), "control_passes": control_ok, "killed": len(killed), "survived": survived,
               "all_killed": control_ok and not survived and len(killed) == len([i for i in ids if i != "CM0"]), "mutants": rows}  # fmt: skip
    (out / "mutants.json").write_text(json.dumps(summary, indent=1))
    print(
        "control passes:", control_ok, "| killed:", len(killed), "| survived:", survived or "none"
    )
    return 0 if summary["all_killed"] or ids == ["CM0"] and control_ok else 1


if __name__ == "__main__":
    sys.exit(main())
