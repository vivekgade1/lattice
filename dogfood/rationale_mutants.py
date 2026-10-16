"""Mutants of Spike G (section 4 of ``docs/results/2026-10-16-rationale-glossary-prereg.md``).

Each is a text patch of a scratch copy of the code; the probes of ``rationale_probes.py`` run
against the patched copy. GM0 is the control (no change) and runs first: it must pass every gated
probe.

A mutant is **killed** when a gated probe that passes on the control fails on it, or any PS, PA, PR
or PB output differs from the control's. Outputs of the other probes that differ are listed beside
(they do not decide). Run::

    "$PY" dogfood/rationale_mutants.py --scratch "$RG/m" --out "$OUT" --world-scratch "$RG/p" \\
        --workflows-dyn "$WF" --inv-s4stat "$S4STAT" $(seq -f GM%g 0 19)

``--world-scratch`` is the scratch directory of an earlier full run of the probes (the worlds, the
base archive).
"""

# ruff: noqa: E501  (the patch tables hold verbatim source lines)

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parent

RAT = "lattice/spec/rationale.py"
GLO = "lattice/spec/glossary.py"
ASK = "lattice/spec/ask.py"
SRV = "lattice/studio/server.py"
QUICK = "PS,PB,PBp,PR,PC,PL,PG,PV,PAP,PDR,PA,PI,PM"
DECIDING = ("PS", "PA", "PR", "PB")

MUTANTS: dict[str, dict[str, Any]] = {
    "GM0": {"what": "control (no change)", "predicted": "passes all", "patch": []},
    "GM1": {
        "what": "proposed notes count as approved (coverage, digest, attachment)",
        "predicted": "PC (RC), PA (R26), PR",
        "patch": [
            (
                RAT,
                "        notes,\n        proposals,\n        resolve(trace, notes),\n",
                "        notes,\n        proposals,\n        resolve(\n            trace,\n"
                "            [\n                *notes,\n                *[\n"
                '                    __import__("dataclasses").replace(\n'
                '                        p, status="approved", owner="x", approved_by="x",\n'
                '                        approved_sha=approved_sha(p.why, p.tradeoff, "x"),\n'
                "                    )\n                    for p in proposals\n"
                '                    if p.status == "proposed"\n                ],\n            ],\n'
                "        ),\n",
            )
        ],
    },
    "GM2": {
        "what": "a note matched by id only (anchor ignored)",
        "predicted": "PR K2 and K4 (stale read as current)",
        "patch": [
            (
                RAT,
                "        elif anchors.get(n.rule) == n.anchor:\n",
                "        elif n.rule in anchors:\n",
            )
        ],
    },
    "GM3": {
        "what": "no re-anchoring (an id mismatch makes a note stale)",
        "predicted": "PR K1 (5 moved -> stale)",
        "patch": [(RAT, "        elif len(hits) == 1:\n", "        elif False:\n")],
    },
    "GM4": {
        "what": "duplicate anchors resolve to the first match",
        "predicted": "PR K1 (R88 ambiguous -> moved)",
        "patch": [(RAT, "        elif len(hits) == 1:\n", "        elif len(hits) >= 1:\n")],
    },
    "GM5": {
        "what": "approved_sha not checked",
        "predicted": "PR K3",
        "patch": [(RAT, "        if not n.sha_ok:\n", "        if False:\n")],
    },
    "GM6": {
        "what": "enum values matched only in identifier form (no `quiet hours`)",
        "predicted": "PS SR1 (R132)",
        "patch": [
            (
                GLO,
                "        for k, text in _match(_segments(prose), cands, longest):\n"
                "            found.setdefault(k, Use(r.id, k, text, False))\n",
                "        for k, text in _match(_segments(prose), cands, longest):\n"
                '            if cands[k].sources == {"C2"} and len(k) > 1:\n                continue\n'
                "            found.setdefault(k, Use(r.id, k, text, False))\n",
            )
        ],
    },
    "GM7": {
        "what": "C3/C6 phrases not propagated across rules",
        "predicted": "PS SR2, SR6, SR7, SP4 (R150)",
        "patch": [
            (
                GLO,
                "    parts = {}\n    for r in trace.rules:\n",
                "    parts = {}\n    own: dict[str, set] = {}\n    for r in trace.rules:\n",
            ),
            (
                GLO,
                '            _add(cands, phrase, "C3")\n',
                '            _add(cands, phrase, "C3")\n'
                "            own.setdefault(r.id, set()).add(norm_phrase(phrase))\n",
            ),
            (
                GLO,
                '            _add(cands, p, "C6", r.id)\n',
                '            _add(cands, p, "C6", r.id)\n'
                "            own.setdefault(r.id, set()).add(norm_phrase(p))\n",
            ),
            (
                GLO,
                "        for k, text in _match(_segments(prose), cands, longest):\n"
                "            found.setdefault(k, Use(r.id, k, text, False))\n",
                "        for k, text in _match(_segments(prose), cands, longest):\n"
                "            if not (cands[k].sources & CODE_SOURCES) and k not in own.get(r.id, ()):\n"
                "                continue\n"
                "            found.setdefault(k, Use(r.id, k, text, False))\n",
            ),
        ],
    },
    "GM8": {
        "what": "code (backtick) tokens not scanned for C1/C2/C5",
        "predicted": "PS SR3, SR4, SR5, SR8",
        "patch": [
            (
                GLO,
                "        for k, text in _match_code(code, code_keys):\n",
                "        for k, text in []:\n",
            )
        ],
    },
    "GM9": {
        "what": "no plural or possessive normalisation",
        "predicted": "PS SR4 (R26), SR6 (R146), SR7 (R122, R127), SN6, SN7",
        "patch": [
            (
                GLO,
                '    """The possessive stripped and a plural stemmed (the last word of a phrase)."""\n',
                '    """The possessive stripped and a plural stemmed (the last word of a phrase)."""\n'
                "    return word\n",
            )
        ],
    },
    "GM10": {
        "what": "aliases ignored",
        "predicted": "PS SN6",
        "patch": [
            (
                GLO,
                "        return [norm_phrase(self.term), *(norm_phrase(a) for a in self.aliases)]\n",
                "        return [norm_phrase(self.term)]\n",
            )
        ],
    },
    "GM11": {
        "what": "`ignore` list ignored",
        "predicted": "PS SN5",
        "patch": [(GLO, "        for i in self.ignore:\n", "        for i in []:\n")],
    },
    "GM12": {
        "what": "JSON-bracket exclusion off",
        "predicted": "PS SN2",
        "patch": [
            (
                GLO,
                "            if any(a < m.start() and m.end() <= b + 1 for a, b in pairs):\n",
                "            if False:\n",
            )
        ],
    },
    "GM13": {
        "what": "shortest-first matching (overlaps counted)",
        "predicted": "PS B0 and SN4 (extra pairs), PL",
        "patch": [
            (
                GLO,
                "        for n in range(min(longest, len(ws)), 0, -1):\n",
                "        for n in range(1, min(longest, len(ws)) + 1):\n",
            )
        ],
    },
    "GM14": {
        "what": "the answer attachment carries proposal or stale text",
        "predicted": "PA (R26, R8)",
        "patch": [
            (
                ASK,
                '        if status == "approved":\n'
                "            n = rs.approved_for(rid).note  # type: ignore[union-attr]\n",
                '        if status != "none":\n'
                "            hit = rs.approved_for(rid)\n"
                "            n = hit.note if hit else next(\n"
                "                x for x in [*rs.proposals, *rs.notes] if x.rule == rid\n"
                "            )\n",
            )
        ],
    },
    "GM15": {
        "what": "`rationale:` citations accepted without the quote check, and counted as grounding",
        "predicted": "PA (bad quote; rationale-only `yes`)",
        "patch": [
            (ASK, "        return _check_note_quote(el, quote)\n", "        return None\n"),
            (
                ASK,
                "    return [\n        c for c in cites\n"
                '        if getattr(digest.elements.get(str(c.get("target"))), "kind", "") not in NOTE_KINDS\n'
                "    ]",
                "    return list(cites)",
            ),
        ],
    },
    "GM16": {
        "what": "an invalid file silently ignored",
        "predicted": "PV",
        "patch": [
            (
                RAT,
                "def load_notes_file(path: Path | None, kind: str) -> list[Note]:\n",
                "def load_notes_file(path: Path | None, kind: str) -> list[Note]:\n"
                "    try:\n        return _load_notes_file(path, kind)\n"
                "    except NotesError:\n        return []\n\n\n"
                "def _load_notes_file(path: Path | None, kind: str) -> list[Note]:\n",
            ),
            (
                GLO,
                "def load_terms_file(path: Path | None, kind: str) -> tuple[list[Term], list[str]]:\n",
                "def load_terms_file(path: Path | None, kind: str) -> tuple[list[Term], list[str]]:\n"
                "    try:\n        return _load_terms_file(path, kind)\n"
                "    except NotesError:\n        return [], []\n\n\n"
                "def _load_terms_file(path: Path | None, kind: str) -> tuple[list[Term], list[str]]:\n",
            ),
        ],
    },
    "GM17": {
        "what": "`approve` allowed without a TTY or with `CLAUDECODE` set",
        "predicted": "PAP",
        "patch": [
            (
                RAT,
                '    approving by accident, not a security boundary (D4 b)."""\n'
                '    if os.environ.get("CLAUDECODE"):\n',
                '    approving by accident, not a security boundary (D4 b)."""\n'
                "    return\n"
                '    if os.environ.get("CLAUDECODE"):\n',
            )
        ],
    },
    "GM18": {
        "what": "files_hash changed while the files are absent",
        "predicted": "PB",
        "patch": [
            (
                SRV,
                "    for p in notes.existing() if notes is not None else ():\n",
                '    h.update(b"notes\\0")\n'
                "    for p in notes.existing() if notes is not None else ():\n",
            )
        ],
    },
    "GM19": {
        "what": "the drafter keeps a model-supplied `owner` or `status`",
        "predicted": "PDR",
        "patch": [
            (
                RAT,
                '        trade,\n        None,\n        "proposed",\n        f"llm:{model}",\n',
                '        trade,\n        data.get("owner"),\n        data.get("status", "proposed"),\n'
                '        f"llm:{model}",\n',
            )
        ],
    },
}


def apply_patch(work: Path, patch: list[tuple[str, str, str]]) -> None:
    for rel, old, new in patch:
        p = work / rel
        text = p.read_text()
        if text.count(old) != 1:
            raise SystemExit(f"{rel}: patch target found {text.count(old)} times: {old[:80]!r}")
        p.write_text(text.replace(old, new))


def copy_tree(dest: Path) -> None:
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    for name in ("lattice", "dogfood", "tests"):
        shutil.copytree(
            REPO / name,
            dest / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "fixtures_big", "goldens"),
            symlinks=True,
        )
    shutil.copy(REPO / "pyproject.toml", dest / "pyproject.toml")


def run_probes(work: Path, args: argparse.Namespace, label: str) -> dict[str, Any]:
    out = Path(args.scratch) / f"{label}-out"
    shutil.rmtree(out, ignore_errors=True)
    env = dict(os.environ, PYTHONPATH=str(work), PYTHONDONTWRITEBYTECODE="1")
    world = Path(args.world_scratch).resolve()
    cmd = [
        sys.executable, str(work / "dogfood/rationale_probes.py"),
        "--seeded", str(REPO / "dogfood/glossary_seeded.json"),
        "--world", str(REPO / "dogfood/rationale_agent_world.json"),
        "--workflows-dyn", args.workflows_dyn, "--inv-s4stat", args.inv_s4stat,
        "--scratch", str(world), "--reuse-world", "--base-tree", str(world / "base"),
        "--out", str(out), "--only", QUICK,
    ]  # fmt: skip
    t0 = time.perf_counter()
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=work, check=False)
    summary = {}
    if (out / "summary.json").is_file():
        summary = json.loads((out / "summary.json").read_text())
    rows = {}
    for f in (out / "probes").glob("*.json") if (out / "probes").is_dir() else ():
        rows[f.stem] = json.loads(f.read_text())
    return {
        "rc": p.returncode,
        "seconds": round(time.perf_counter() - t0, 1),
        "summary": summary,
        "rows": rows,
        "stderr_tail": p.stderr.strip()[-600:],
    }


def verdict(res: dict[str, Any]) -> dict[str, Any]:
    s = res["summary"]
    return {
        "pass": s.get("pass", {}),
        "sha": s.get("output_sha256", {}),
        "failed": s.get("gated_failed", None),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--world-scratch", required=True)
    ap.add_argument("--workflows-dyn", required=True)
    ap.add_argument("--inv-s4stat", required=True)
    ap.add_argument("ids", nargs="*")
    args = ap.parse_args(argv)
    ids = args.ids or sorted(MUTANTS, key=lambda s: int(s[2:]))
    scratch, out = Path(args.scratch), Path(args.out)
    scratch.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    # the control is GM0: an unmodified copy
    work = scratch / "control"
    copy_tree(work)
    control = run_probes(work, args, "GM0")
    cv = verdict(control)
    results: dict[str, Any] = {
        "GM0": {
            "what": MUTANTS["GM0"]["what"],
            "pass": cv["pass"],
            "gated_failed": cv["failed"],
            "output_sha256": cv["sha"],
            "seconds": control["seconds"],
            "rc": control["rc"],
            "stderr_tail": control["stderr_tail"] if not control["summary"] else "",
        }
    }
    print("GM0 control:", json.dumps(cv["pass"]), "failed:", cv["failed"], flush=True)
    gated_ok = [pid for pid, v in cv["pass"].items() if v is True]
    if cv["failed"] or not control["summary"]:
        print("the control does not pass its gated probes: mutants are not run", flush=True)
        (out / "mutants.json").write_text(json.dumps(results, indent=2, default=str) + "\n")
        return 1
    killed = 0
    n = 0
    for mid in [i for i in ids if i != "GM0"]:
        m = MUTANTS[mid]
        work = scratch / mid
        copy_tree(work)
        apply_patch(work, m["patch"])
        res = run_probes(work, args, mid)
        mv = verdict(res)
        by: list[str] = []
        if not mv["pass"]:
            by.append(f"the probes did not run: {res['stderr_tail'][-200:]}")
        by += [f"{pid} fails" for pid in gated_ok if mv["pass"].get(pid) is not True]
        by += [
            f"{pid} output differs"
            for pid in DECIDING
            if pid in cv["sha"] and mv["sha"].get(pid) != cv["sha"].get(pid)
        ]
        other = sorted(
            pid
            for pid in cv["sha"]
            if pid not in DECIDING and cv["sha"][pid] and mv["sha"].get(pid) != cv["sha"][pid]
        )
        results[mid] = {
            "what": m["what"],
            "predicted_catcher": m["predicted"],
            "caught_by": by,
            "killed": bool(by),
            "gated": {pid: mv["pass"].get(pid) for pid in gated_ok},
            "other_outputs_differ": other,
            "seconds": res["seconds"],
            "rc": res["rc"],
        }
        n += 1
        killed += bool(by)
        print(
            mid, "KILLED" if by else "SURVIVED", by, "| other:", other, "|", m["what"], flush=True
        )
        shutil.rmtree(work, ignore_errors=True)
        (out / "mutants.json").write_text(
            json.dumps(results | {"summary": {"killed": killed, "of": n}}, indent=2,
                       sort_keys=True, default=str) + "\n"
        )  # fmt: skip
    results["summary"] = {"killed": killed, "of": n}
    (out / "mutants.json").write_text(
        json.dumps(results, indent=2, sort_keys=True, default=str) + "\n"
    )
    print(json.dumps(results["summary"]))
    return 0 if killed == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
