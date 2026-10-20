"""Mutants of .NET parity, round 1 (docs/results/2026-10-18-dotnet-prereg.md, sections 6.1-6.3 and 11; Spike J, node DN1, runner node R):
the 24 library mutants LM1-LM24 (control first), the 16 held-out mutants HM1-HM16 (the class gates and the strict control rule), the 6 D1 dev
mutants DM1-DM6 and the two runner mutants RM1 and RM2.

    "$PY" dogfood/dotnet_mutants.py --suite LM --scratch "$SCRATCH/m" --freeze "$SCRATCH/dn.sha256" --out docs/results/data/2026-10-18-dotnet/mutants
    "$PY" dogfood/dotnet_mutants.py --suite HM --h1 tests/spec/<h1> --patches "$PATCHES" --kind scored --freeze ... --out .../hmut --scratch ...
    "$PY" dogfood/dotnet_mutants.py --suite DM --freeze ... --out ...      # DM1-DM6 on D1
    "$PY" dogfood/dotnet_mutants.py --suite RM --freeze ...                # RM1, RM2
    "$PY" dogfood/dotnet_mutants.py --self-test                            # every suite's code path on the stand-ins

What cannot be switched off (prereg section 11): the self-hash check of ``dotnet_probes.verify_freeze`` (this runner, the probes runner, the
stand-ins, the library, and for HM the patches, MUTANTS.md, the truth and H1's sources) runs first; ``lattice.__file__`` must lie inside the
worktree; every run is recorded in the global ledger ``docs/results/data/2026-10-18-dotnet/ledger.jsonl`` (arms ``DN:LM``, ``DN:DM``, ``DN:HM``,
``DN:RM``), and a ``scored`` HM run is refused when the arm already has one.

How a library mutant is judged: the edits (``dn_standin/lm_edits.json``, a freeze entry; each must match exactly once) are applied to a scratch
copy of the tree (``lattice``, ``tests``, ``bench``, ``dogfood`` and the data files), whose probe rows (``dotnet_probes.rows_cli`` in a
subprocess of the copy, with a helper cache of its own) and named unit tests are compared with the control's: killed = a detector that passes on the
control stops passing. A mutant of a node that is not merged and has no edits is NOT RUN with that prerequisite named; with the node merged and no
edit, or an edit that does not apply, it is a FAIL.

How a held-out mutant is judged (the operator is read from the ``operator`` cell of MUTANTS.md and matched against the registered names and keys
EXACTLY; the text of the line is never read for an operator -- Go round 3's classifier read "removes the 403 owner check" as the 403 operator):

    HM1-4   owner condition dropped (read, write)  gated    layer A DN801 on the route                       class gate >= 3 of 4
    HM5-6   never-returned column exposed          gated    layer A DN802 on the route and the column        2 of 2
    HM7-8   login requirement removed              gated    layer R ``R1 <route>`` verified -> refuted       2 of 2
    HM9-10  403 where truth says 404 not_found     gated    layer R the route's 404 ``R3`` item refuted      2 of 2
    HM11    parent-ownership check dropped         reported DN801 on the route, or ``R2 <route>`` refuted
    HM12-13 state guard (conflict) dropped         reported ``R3 <route> <conflict status>`` refuted
    HM14    validation bound removed               reported ``R3 <route> <invalid status>`` refuted
    HM15    owner taken from the request body      reported DN801 on the route, or ``R2 <route>`` refuted
    HM16    list owner condition widened           reported DN801 on the route, or ``R2 <route>`` refuted

The overall gate is >= 13 of 16 killed. The control comes first and is strict: no DN801/DN802 on any HM route of the original, no registered R item
refuted on it, a pristine copy adds no finding, the copy is H1 byte for byte, and the harness runs the binary it is handed; if it does not pass, no
mutant is judged.
"""  # noqa: E501

# ruff: noqa: E501
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import dotnet_probes as G  # noqa: E402

REPO = G.REPO
RUNNER_NAME = "dotnet_mutants"
LM_EDITS = G.STANDIN / "lm_edits.json"
TREE_DIRS = ("lattice", "tests", "bench", "dogfood")
DATA_FILES = (
    "docs/results/2026-10-18-dotnet-prereg.md",
    "docs/results/data/2026-10-18-dotnet/d1.tgz",
    "docs/results/data/2026-10-18-dotnet/HARNESS_API_DOTNET.md",
)
SUITES = ("LM", "HM", "DM", "RM")
IDS = tuple(range(1, 17))
GATE_OVERALL = 13
PREDICTED_KILLED = (12, 15)
CLASS_GATES: tuple[tuple[str, tuple[int, ...], int], ...] = (
    ("HM1-4", (1, 2, 3, 4), 3),
    ("HM5-6", (5, 6), 2),
    ("HM7-8", (7, 8), 2),
    ("HM9-10", (9, 10), 2),
)
LM_IDS = [f"LM{i}" for i in range(1, 25)]
CODE = "import sys; sys.path.insert(0, 'dogfood'); import dotnet_probes as G; sys.exit(G.rows_cli(sys.argv[1:]))"


# ---- the operators of prereg 6.2 and their detectors ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Detector:
    layer: str  # A: a new finding with ``code`` on the route; R: a verified ``code`` item of the route that becomes refuted
    code: str
    cause: str | None = None  # R3: the truth refusal cause whose status the item carries


@dataclass(frozen=True)
class Operator:
    key: str
    name: str  # the registered text of prereg 6.2's operator column
    gated: bool
    detectors: tuple[Detector, ...]


A801, A802, R1, R2 = (
    Detector("A", "DN801"),
    Detector("A", "DN802"),
    Detector("R", "R1"),
    Detector("R", "R2"),
)
OPERATORS: dict[str, Operator] = {o.key: o for o in (
    Operator("owner_read", "drop the owner condition of an owner-scoped read", True, (A801,)),
    Operator("owner_write", "drop the owner condition of an owner-scoped write or state change", True, (A801,)),
    Operator("expose_column", "expose a never-returned column on a route that answers its row", True, (A802,)),
    Operator("drop_login", "remove the login requirement from a Member route", True, (R1,)),
    Operator("status_403", "answer 403 where truth says 404 not_found", True, (Detector("R", "R3", "not_found"),)),
    Operator("drop_parent", "drop the parent-ownership check of an owner_via table", False, (A801, R2)),
    Operator("drop_guard", "drop a state guard (conflict) of a transition", False, (Detector("R", "R3", "conflict"),)),
    Operator("loosen_bound", "remove an upper bound from a validated field", False, (Detector("R", "R3", "invalid"),)),
    Operator("owner_body", "take the owner from the request body on create", False, (A801, R2)),
    Operator("widen_list", "widen a list route's owner condition to every row", False, (A801, R2)),
)}  # fmt: skip
INDEX_OPERATOR = {1: "owner_read", 2: "owner_read", 3: "owner_write", 4: "owner_write", 5: "expose_column", 6: "expose_column", 7: "drop_login", 8: "drop_login", 9: "status_403", 10: "status_403", 11: "drop_parent", 12: "drop_guard", 13: "drop_guard", 14: "loosen_bound", 15: "owner_body", 16: "widen_list"}  # fmt: skip
DEFAULT_STATUS = {"not_found": (404,), "conflict": (409,), "invalid": (422, 400)}


def norm_name(text: str) -> str:
    """A registered operator key or name, normalised for an exact comparison: case, backticks and runs of spaces, hyphens and underscores."""
    return re.sub(r"[\s_\-]+", " ", re.sub(r"[`'\"*]+", "", text.lower())).strip()


OPERATOR_LOOKUP: dict[str, str] = {}
for _o in OPERATORS.values():
    OPERATOR_LOOKUP[norm_name(_o.key)] = _o.key
    OPERATOR_LOOKUP[norm_name(_o.name)] = _o.key


def classify_operator(cell: str) -> str | None:
    """The operator named by ``cell`` -- the whole cell, compared with the registered keys and names, EXACTLY. Never a keyword in the text."""
    c = norm_name(cell)
    c = re.sub(r"^operator\s*[=:]\s*", "", c)
    return OPERATOR_LOOKUP.get(c)


# ---- MUTANTS.md ------------------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MutRow:
    num: int
    line: str
    operator_cell: (
        str | None
    )  # the cell the operator was read from (None: the list names none, the id's operator is used)
    method: str
    path: str
    file: str | None
    file_line: int | None
    substitutions: str


ID_RE = re.compile(r"^[\s|*\->`#.]*(?:HM|M)?0*(\d{1,2})\b", re.I)
ROUTE_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[^\s'\"`|,;)]*)")
FILE_RE = re.compile(r"([\w./-]+\.cs):(\d+)")


def parse_mutants_md(text: str) -> dict[int, MutRow]:
    """One MutRow per ``|``-separated line that starts with an id and carries a route. The operator is the cell between the id and the route cell
    (``HMn | <operator> | METHOD /path | file:line | substitutions``); a line without that cell names none. A line without a route is a header."""
    rows: dict[int, MutRow] = {}
    for ln in text.splitlines():
        m, r = ID_RE.match(ln), ROUTE_RE.search(ln)
        if not m or not r:
            continue
        num = int(m.group(1))
        if not 1 <= num <= 16:
            continue
        if num in rows:
            raise ValueError(f"MUTANTS.md: id {num} appears twice")
        cells = [c.strip(" `*") for c in ln.strip().strip("|").split("|")]
        route_idx = next(
            (
                i
                for i, c in enumerate(cells)
                if ROUTE_RE.fullmatch(c.strip("`' "))
                or c.strip("`' ").startswith(f"{r.group(1)} /")
            ),
            None,
        )
        cell = cells[route_idx - 1] if route_idx is not None and route_idx >= 2 else None
        f = FILE_RE.search(ln, r.end())
        rows[num] = MutRow(
            num,
            ln.strip(),
            cell,
            r.group(1),
            r.group(2).rstrip(".,"),
            f.group(1) if f else None,
            int(f.group(2)) if f else None,
            ln[(f.end() if f else r.end()) :].strip(" |,;:-*`"),
        )
    return rows


def operator_of(num: int, cell: str | None) -> dict[str, Any]:
    """The operator used for mutant ``num``: the list's when its operator cell names a registered operator; the id's when the list names none; an
    operator cell that names no registered operator is an input error (checked before any metric, :func:`validate_inputs`)."""
    by_id = INDEX_OPERATOR[num]
    by_list = classify_operator(cell) if cell is not None else None
    key = by_list or by_id
    return {
        "key": key,
        "from_list": by_list,
        "from_id": by_id,
        "cell": cell,
        "shifted": by_list is not None
        and OPERATORS[by_list].detectors != OPERATORS[by_id].detectors,
        "source": "list" if by_list else "id",
    }


def patch_name(num: int) -> str:
    return f"m{num:02d}.diff"


# ---- the truth: what each detector looks for ---------------------------------------------------------------------------------------------------------------


def refusal_statuses(
    truth: dict[str, Any], route: tuple[str, str], cause: str
) -> tuple[list[int], bool]:
    got: list[int] = []
    for r in truth.get("refusals", ()):
        if (
            r.get("cause") == cause
            and G.route_id_key(r["route"]) == route
            and int(r["status"]) not in got
        ):
            got.append(int(r["status"]))
    return (got, True) if got else (list(DEFAULT_STATUS[cause]), False)


def private_columns(truth: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(entity, SQL table, column) of every private or never-returned column of the truth."""
    out = [
        (t["entity"], t["table"], c["name"])
        for t in truth.get("tables", ())
        for c in t["columns"]
        if c.get("private")
    ]
    tabs = {t["entity"]: t["table"] for t in truth.get("tables", ())}
    out += [
        (n["entity"], tabs.get(n["entity"], ""), n["column"]) for n in truth.get("not_returned", ())
    ]
    return list(dict.fromkeys(out))


def expected_columns(truth: dict[str, Any], text: str) -> tuple[set[str], bool]:
    """The ``Entity.column`` / ``table.column`` spellings a DN802 finding of an exposing mutant may carry: EVERY private or never-returned column of
    the truth. The text of MUTANTS.md or of the patch is not read to narrow them (Go round 3: a word in a description is not a classification);
    the second value is always False."""
    del text
    cols = private_columns(truth)
    return {f"{e}.{c}" for e, _, c in cols} | {f"{t}.{c}" for _, t, c in cols if t}, False


# ---- judging (pure: the unit tests feed these synthetic inputs) ------------------------------------------------------------------------------------------


def judge_mutant(
    num: int,
    operator: str,
    route: tuple[str, str],
    *,
    a_control: list[dict[str, Any]],
    a_new: list[dict[str, Any]],
    r_control: dict[str, str],
    r_mutant: dict[str, str] | None,
    unstable: list[str],
    truth: dict[str, Any],
    text: str,
) -> dict[str, Any]:
    """The verdict of one mutant: killed iff a registered detector of its operator flips. ``r_mutant`` None means layer R could not be fed the
    mutant's binary: then only layer-A detectors can kill it, and a mutant whose detectors are all layer R is NOT RUN (killed None)."""
    op = OPERATORS[operator]
    cols, resolved = expected_columns(truth, text) if operator == "expose_column" else (None, True)
    details, notes = [], {}
    for det in op.detectors:
        if det.layer == "A":
            details.append(G.eval_a(det.code, route, a_control, a_new, cols))
            continue
        statuses: list[int] = []
        if det.code == "R3" and det.cause:
            statuses, from_truth = refusal_statuses(truth, route, det.cause)
            notes[f"status_of_{det.cause}"] = {"statuses": statuses, "from_truth": from_truth}
        details.append(G.eval_r(det.code, route, r_control, r_mutant, statuses, unstable))
    killed: bool | None = any(d["flipped"] for d in details)
    if not killed and r_mutant is None and all(d.layer == "R" for d in op.detectors):
        killed = None
    by = next(d["detector"] for d in details if d["flipped"]) if killed else ("NOT RUN: layer R cannot be fed the mutant's binary" if killed is None else "survived: " + "; ".join(f"{d['detector']}: {d.get('why_not') or ('not armed' if not d['armed'] else 'no new finding')}" for d in details))  # fmt: skip
    return {"operator_key": operator, "gated": op.gated, "route": G.rlabel(route), "detectors": details, "killed": killed, "killed_by": by, "expose_column": {"columns": sorted(cols or ()), "named_by_mutants_md": resolved} if operator == "expose_column" else None, **notes}  # fmt: skip


def judge_control(
    routes: list[tuple[str, str]],
    registered_r: list[str],
    *,
    a_control: list[dict[str, Any]],
    r_control: dict[str, str],
    pristine_new: list[dict[str, Any]],
    copy_equals_h1: bool,
    injection_works: bool,
) -> dict[str, Any]:
    """Prereg 6.2's strict control: no DN801/DN802 on any HM route of the original, no registered R item refuted, the pristine copy adds no
    finding, the copy is H1 byte for byte, and the harness really starts the binary it is handed."""
    rs = set(routes)
    dirty = [
        f["key"]
        for f in a_control
        if f["code"] in ("DN801", "DN802") and G.key_route(f["key"]) in rs
    ]
    refuted = sorted(i for i in set(registered_r) if r_control.get(i) == "refuted")
    ok = not dirty and not refuted and not pristine_new and copy_equals_h1 and injection_works
    return {"passes": ok, "dn801_dn802_on_hm_routes": dirty, "registered_r_items_refuted": refuted, "pristine_copy_new_findings": [f"{x['code']} {x['key']}" for x in pristine_new], "pristine_copy_equals_h1": copy_equals_h1, "binary_injection_works": injection_works}  # fmt: skip


def gate_report(killed: dict[int, bool | None]) -> dict[str, Any]:
    """Prereg 6.2's gates: the four class gates and >= 13 of 16. A mutant not run makes the gate ``incomplete`` (never a pass)."""
    classes = {
        name: {
            "killed": sum(1 for i in ids if killed.get(i) is True),
            "of": len(ids),
            "need": need,
            "pass": sum(1 for i in ids if killed.get(i) is True) >= need,
        }
        for name, ids, need in CLASS_GATES
    }
    total = sum(1 for i in IDS if killed.get(i) is True)
    not_run = [f"HM{i}" for i in IDS if killed.get(i) is None]
    ok = total >= GATE_OVERALL and all(c["pass"] for c in classes.values())
    return {"killed": total, "of": 16, "need": GATE_OVERALL, "predicted": list(PREDICTED_KILLED), "classes": classes, "not_run": not_run, "verdict": "incomplete" if not_run else ("pass" if ok else "fail"), "survivors": [f"HM{i}" for i in IDS if killed.get(i) is False]}  # fmt: skip


# ---- the held-out mutants: one run ------------------------------------------------------------------------------------------------------------------------


def validate_inputs(
    patches: Path, truth_path: Path
) -> tuple[dict[str, Any], dict[int, MutRow], dict[int, Path]]:
    """Before any metric: the patch set is m01..m16 + MUTANTS.md, every line parses with a route that is in the truth, and every operator cell
    names a registered operator."""
    problems = []
    truth = json.loads(truth_path.read_text())
    files = {i: patches / patch_name(i) for i in IDS}
    problems += [f"{p} does not exist" for p in files.values() if not p.is_file()]
    md, rows = patches / "MUTANTS.md", {}
    if not md.is_file():
        problems.append(f"{md} does not exist")
    else:
        try:
            rows = parse_mutants_md(md.read_text())
        except ValueError as err:
            problems.append(str(err))
        problems += [
            f"MUTANTS.md has no line with a route for id {i}" for i in IDS if i not in rows
        ]
    routes = G.truth_route_map(truth) if "routes" in truth else {}
    for i, r in rows.items():
        if G.rkey(r.method, r.path) not in routes:
            problems.append(f"HM{i}: {r.method} {r.path} is not a route of truth.json")
        if r.operator_cell is not None and classify_operator(r.operator_cell) is None:
            problems.append(
                f"HM{i}: the operator cell {r.operator_cell!r} names no registered operator (keys {sorted(OPERATORS)})"
            )
    if problems:
        raise SystemExit("input check failed: " + "; ".join(problems))
    return truth, rows, files


def run_hmut(
    ctx: G.Ctx,
    tgt: dict[str, Any],
    patches: Path,
    truth: dict[str, Any],
    rows: dict[int, MutRow],
    files: dict[int, Path],
    out: Path,
    *,
    only: tuple[int, ...] = IDS,
    on_control: Any = None,
) -> dict[str, Any]:
    """The control, then the mutants. No freeze check and no ledger here (they belong to :func:`main`); each mutant's evidence JSON is written to ``out/evidence`` as it is judged."""
    t_start = time.perf_counter()
    src = Path(tgt["module_dir"])
    ev = out / "evidence"
    ev.mkdir(parents=True, exist_ok=True)
    doc: dict[str, Any] = {"h1": str(src), "h1_tree_sha256": G.tree_sha256(src), "mutants_md_sha256": G.sha(patches / "MUTANTS.md"), "truth_sha256": G.sha(Path(tgt["truth_path"])), "patch_sha256": {patch_name(i): G.sha(files[i]) for i in IDS}}  # fmt: skip
    if not ctx.dotnet:
        doc.update(
            control={"passes": None, "not_run": "no .NET SDK on PATH"},
            rows=[],
            gate=gate_report({}),
            not_run="no .NET SDK on PATH",
        )
        return doc
    ctl_dir = ctx.scratch / "mut" / "control" / src.name
    G.copy_module(src, ctl_dir)
    copy_hash = G.tree_sha256(ctl_dir)  # before the build writes obj/ and bin/ into the copy
    cbin = ctx.scratch / "mut" / "control" / "out"
    ok, err = G.build_copy(ctx, tgt, ctl_dir, cbin)
    if not ok:
        doc.update(
            control={"passes": False, "build_error": err[-600:]}, rows=[], gate=gate_report({})
        )
        return doc
    cand_dir = G.import_dir(ctx, tgt)
    cand = G.load_candidate(cand_dir)
    a_control = ctx.lib.layer_a(ctx, src, "spec_policy", spec=cand)["findings"]
    pristine = ctx.lib.layer_a(ctx, ctl_dir, "spec_policy", spec=cand)["findings"]
    with G.env_set(tgt.get("build_env"), cbin):
        r1 = ctx.lib.r_verdicts(ctx, tgt, cand_dir, "seed")
        r2 = ctx.lib.r_verdicts(ctx, tgt, cand_dir, "seed")
    injection, why_inj = binary_injected(ctx, tgt, cbin)
    unstable = sorted(i for i in {*r1, *r2} if r1.get(i) != r2.get(i))
    plans: dict[int, dict[str, Any]] = {}
    for i in IDS:
        plans[i] = {
            "num": i,
            "route": G.rkey(rows[i].method, rows[i].path),
            "operator": operator_of(i, rows[i].operator_cell),
        }
    registered: list[str] = []
    for p in plans.values():
        for det in OPERATORS[p["operator"]["key"]].detectors:
            if det.layer == "R":
                sts = (
                    refusal_statuses(truth, p["route"], det.cause)[0]
                    if det.code == "R3" and det.cause
                    else []
                )
                registered += G.r_items(list(r1), det.code, p["route"], sts)
    control = judge_control([p["route"] for p in plans.values()], registered, a_control=a_control, r_control=r1, pristine_new=G.new_findings(a_control, pristine), copy_equals_h1=copy_hash == doc["h1_tree_sha256"], injection_works=injection)  # fmt: skip
    control.update({"layer_a_original_findings": [f["key"] for f in a_control], "r_items": len(r1), "r_verified": sum(1 for v in r1.values() if v in G.VERIFIED), "r_items_unstable_between_two_runs": unstable, "binary_injection": why_inj, "operators": {f"HM{i}": plans[i]["operator"] for i in IDS}})  # fmt: skip
    doc["control"] = control
    (ev / "control.json").write_text(json.dumps(control, indent=2, default=str) + "\n")
    if on_control:
        on_control(control)
    mrows: list[dict[str, Any]] = []
    if not control["passes"]:
        doc.update(
            rows=mrows,
            gate=gate_report({}),
            judged=False,
            reading="the control does not pass: no mutant is judged",
        )
        doc["wall_s"] = round(time.perf_counter() - t_start, 1)
        return doc
    for i in IDS:
        if i not in only:
            continue
        p, row = plans[i], rows[i]
        rec: dict[str, Any] = {"id": f"HM{i}", "patch": patch_name(i), "patch_sha256": doc["patch_sha256"][patch_name(i)], "mutants_md_line": row.line, "operator": p["operator"], "route": G.rlabel(p["route"]), "file_line": [row.file, row.file_line], "substitutions": row.substitutions}  # fmt: skip
        t0 = time.perf_counter()
        try:
            rec.update(
                judge_one(
                    ctx,
                    tgt,
                    truth,
                    i,
                    p,
                    files[i],
                    cand,
                    cand_dir,
                    a_control,
                    r1,
                    unstable,
                    rows[i].line + "\n" + changed_lines(files[i]),
                )
            )
        except Exception as err:  # noqa: BLE001 -- a mutant whose code raised is a FAIL (round-2 Amendment 6 rule a), never NOT RUN
            rec.update(
                killed=False,
                status="crashed",
                killed_by=f"FAIL: {type(err).__name__}: {err}"[:400],
                traceback=traceback.format_exc()[-1500:],
            )
        rec["wall_s"] = round(time.perf_counter() - t0, 1)
        (ev / f"hm{i:02d}.json").write_text(json.dumps(rec, indent=2, default=str) + "\n")
        print(f"HM{i}: killed={rec.get('killed')} {rec.get('killed_by', '')[:140]}", flush=True)
        mrows.append(rec)
    killed = {int(r["id"][2:]): r["killed"] for r in mrows}
    doc.update(
        rows=mrows,
        judged=True,
        gate=gate_report(killed)
        if len(mrows) == len(IDS)
        else {
            "partial": True,
            "killed": sum(1 for v in killed.values() if v is True),
            "of": len(mrows),
        },
    )
    doc["wall_s"] = round(time.perf_counter() - t_start, 1)
    return doc


def changed_lines(patch: Path) -> str:
    return "\n".join(
        ln[1:]
        for ln in patch.read_text().splitlines()
        if ln[:1] in "+-" and not ln.startswith(("+++", "---"))
    )


def judge_one(
    ctx: G.Ctx,
    tgt: dict[str, Any],
    truth: dict[str, Any],
    num: int,
    plan: dict[str, Any],
    patch: Path,
    cand: Any,
    cand_dir: Path,
    a_control: list[dict[str, Any]],
    r_control: dict[str, str],
    unstable: list[str],
    text: str,
) -> dict[str, Any]:
    """Apply, build and judge one mutant on a scratch copy of H1."""
    src = Path(tgt["module_dir"])
    copy = ctx.scratch / "mut" / f"hm{num:02d}" / src.name
    G.copy_module(src, copy)
    ap = G.apply_patch(copy, patch)
    if not ap["ok"]:
        return {
            "killed": False,
            "status": "patch_failed",
            "killed_by": "FAIL: the patch does not apply: " + ap["output"][-200:],
            "apply": ap,
        }
    (copy.parent / "PATCH").write_text(
        G.sha(patch)
    )  # a marker beside the copy (the stand-ins read it; the real library ignores it)
    out = copy.parent / "out"
    ok, err = G.build_copy(ctx, tgt, copy, out)
    if not ok:
        return {
            "killed": False,
            "status": "build_failed",
            "killed_by": "FAIL: the mutant does not build: " + err[-300:],
            "apply": ap,
        }
    mutant_findings = ctx.lib.layer_a(ctx, copy, "spec_policy", spec=cand)["findings"]
    new = G.new_findings(a_control, mutant_findings)
    with G.env_set(tgt.get("build_env"), out):
        r_mut = ctx.lib.r_verdicts(ctx, tgt, cand_dir, "seed")
    v = judge_mutant(
        num,
        plan["operator"]["key"],
        plan["route"],
        a_control=a_control,
        a_new=new,
        r_control=r_control,
        r_mutant=r_mut,
        unstable=unstable,
        truth=truth,
        text=text,
    )
    flips = {i: [r_control.get(i), x] for i, x in r_mut.items() if r_control.get(i) != x}
    return {**v, "status": "judged", "apply": ap, "layer_a": {"new_findings": [f"{x['code']} {x['key']}" for x in new]}, "layer_r": {"ran": True, "flipped_items [control, mutant]": flips}}  # fmt: skip


def binary_injected(ctx: G.Ctx, tgt: dict[str, Any], out: Path) -> tuple[bool, str]:
    """Does the harness's make_app start the binary its BUILD_ENV variable names? (layer R on a mutant is only as good as this.)"""
    name = tgt.get("build_env")
    if not name:
        return ctx.self_test, "the harness defines no BUILD_ENV" + (
            " (self-test stand-in)" if ctx.self_test else ""
        )
    try:
        G.use_overlay(tgt)
        mod = G.importlib.import_module(f"tests.spec.{Path(tgt['module_dir']).name}_harness")
        with G.env_set(name, out):
            app = mod.make_app(ctx.scratch / "inject-check.db")
            used = " ".join(str(x) for x in list(app.argv))
    except Exception as err:  # noqa: BLE001 -- reported: layer R then cannot judge the mutants
        return False, f"make_app raised {type(err).__name__}: {err}"
    return str(out) in used, f"make_app starts {used[:200]}"


# ---- the library mutants LM1-LM24 -----------------------------------------------------------------------------------------------------------------------


def load_lm() -> dict[str, dict[str, Any]]:
    return json.loads(LM_EDITS.read_text())  # type: ignore[no-any-return]


def make_tree(dest: Path) -> Path:
    """A scratch copy of the tree (no .git: :func:`dotnet_probes.rows_cli` accepts only such a copy) with the data the probe rows read."""
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    for d in TREE_DIRS:
        shutil.copytree(
            REPO / d,
            dest / d,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "bin", "obj", ".DS_Store"),
        )
    shutil.copyfile(REPO / "pyproject.toml", dest / "pyproject.toml")
    for rel in DATA_FILES:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, dest / rel)
    return dest


class EditError(ValueError):
    """An edit that does not match exactly once."""


def apply_edits(tree: Path, edits: list[list[str]]) -> None:
    for rel, old, new in edits:
        f = tree / rel
        text = f.read_text()
        if text.count(old) != 1:
            raise EditError(f"{rel}: the edit matches {text.count(old)} times: {old[:70]!r}")
        f.write_text(text.replace(old, new))


def run_rows_in(
    tree: Path, ids: list[str], scratch: Path, out: Path, extra: list[str]
) -> dict[str, Any]:
    """The probe rows ``ids`` of the runner in ``tree`` (a subprocess, with a helper cache of its own); {row id: pass}."""
    env = dict(
        os.environ,
        PYTHONPATH=str(tree),
        PYTHONDONTWRITEBYTECODE="1",
        LATTICE_DNCHECK_CACHE=str(scratch / "cache"),
    )
    for k in [k for k in env if k.endswith("_OUT")]:
        env.pop(k)
    out.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-c",
        CODE,
        "--scratch",
        str(scratch),
        "--out",
        str(out),
        "--ids",
        ",".join(ids),
        "--runs",
        "2",
        "--no-docker",
        *extra,
    ]
    proc = subprocess.run(cmd, cwd=tree, env=env, capture_output=True, text=True, check=False)
    f = out / "rows.json"
    if not f.exists():
        return {"error": (proc.stdout + proc.stderr)[-600:]}
    return {r["id"]: r["pass"] for r in json.loads(f.read_text())}


def run_tests_in(tree: Path, ids: list[str]) -> dict[str, bool]:
    if not ids:
        return {}
    env = dict(os.environ, PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider", *ids],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    got = {
        m.group(2): m.group(1) == "PASSED"
        for ln in proc.stdout.splitlines()
        if (m := re.match(r"(PASSED|FAILED|ERROR) (\S+)", ln))
    }
    return {i: got.get(i, False) for i in ids}


def detectors_in(
    tree: Path, spec: dict[str, Any], scratch: Path, out: Path, extra: list[str]
) -> dict[str, Any]:
    d: dict[str, Any] = {}
    if spec["probes"]:
        d["probes"] = run_rows_in(tree, spec["probes"], scratch, out, extra)
    d["tests"] = run_tests_in(tree, spec["tests"])
    return d


def flat(d: dict[str, Any]) -> dict[str, Any]:
    out = {f"probe:{k}": v for k, v in d.get("probes", {}).items()}
    out.update({f"test:{k.split('::')[-1]}": v for k, v in d.get("tests", {}).items()})
    return out


def lm_suite(ids: list[str], scratch: Path, out: Path, extra: list[str]) -> list[dict[str, Any]]:
    """The control (every detector on an unmutated copy) and then each library mutant."""
    lm = load_lm()
    ids = [i for i in ids if i in lm]
    rows: list[dict[str, Any]] = []
    probes = sorted({p for i in ids for p in lm[i]["probes"]})
    tests = sorted({t for i in ids for t in lm[i]["tests"]})
    if (
        any("base" in G.P[p][4] for p in probes if p in G.P) and "--base" not in extra
    ):  # the base tree for the rows that compare with it (git is read only)
        G.archive(G.BASE_REF, scratch / "base")
        extra = [*extra, "--base", str(scratch / "base")]
    t0 = time.perf_counter()
    ctl_tree = make_tree(scratch / "tree-control")
    ctl = detectors_in(
        ctl_tree, {"probes": probes, "tests": tests}, scratch / "ctl", out / "control", extra
    )
    control = flat(ctl)
    bad_tests = [k for k, v in control.items() if k.startswith("test:") and v is not True]
    ctl_row = {"id": "control", "detectors": control, "tests_failing_on_control": bad_tests, "probes_not_passing_on_control": sorted(k for k, v in control.items() if k.startswith("probe:") and v is not True), "passes": not bad_tests and "error" not in ctl.get("probes", {}), "wall_s": round(time.perf_counter() - t0, 1)}  # fmt: skip
    print(
        "control:", json.dumps({k: v for k, v in ctl_row.items() if k != "detectors"}), flush=True
    )
    rows.append(ctl_row)
    if not ctl_row["passes"]:
        print("the control does not pass: no mutant is judged", file=sys.stderr)
        return rows
    for mid in ids:
        spec = lm[mid]
        r: dict[str, Any] = {
            "id": mid,
            "mutant": spec["what"],
            "killed_by": spec["killed_by"],
            "node": spec.get("node"),
        }
        node_in_tree = spec.get("node") is None or G.node_present(spec["node"])
        if not spec["edits"]:
            if node_in_tree:
                r.update(
                    killed=False,
                    status="no_edits",
                    why=f"{mid} has no edits in dn_standin/lm_edits.json although its node is merged: a FAIL",
                )
            else:
                r.update(
                    killed=None,
                    not_run=f"node {spec['node']} is not merged in this tree and {mid} has no edits yet (dn_standin/lm_edits.json)",
                )
        else:
            tree = make_tree(scratch / f"tree-{mid.lower()}")
            try:
                apply_edits(tree, spec["edits"])
                got = flat(
                    detectors_in(tree, spec, scratch / mid.lower(), out / mid.lower(), extra)
                )
                flips = {
                    k: [control.get(k), v]
                    for k, v in got.items()
                    if control.get(k) is True and v is not True
                }
                r.update(detectors=got, flipped=flips, killed=bool(flips))
            except EditError as err:
                r.update(killed=False, status="edit_failed", why=str(err)[:300])
            finally:
                shutil.rmtree(tree, ignore_errors=True)
        print(
            f"{mid}: killed={r.get('killed')} {r.get('flipped', r.get('not_run', r.get('why', '')))}",
            flush=True,
        )
        rows.append(r)
    return rows


# ---- the D1 dev mutants DM1-DM6 --------------------------------------------------------------------------------------------------------------------------


def dm_suite(ctx: G.Ctx) -> list[dict[str, Any]]:
    spec = G.streak_spec(ctx)
    cand = G.import_dir(ctx, ctx.d1)
    rows = []
    for mid in sorted(G.DEV_MUTANTS):
        r = G.dev_mutant(ctx, mid, spec, cand)
        print(f"{mid}: killed={r.get('killed')} gated={r.get('gated')}", flush=True)
        rows.append(r)
    return rows


# ---- the runner mutants RM1 and RM2 ------------------------------------------------------------------------------------------------------------------------


def rm_suite(ctx: G.Ctx) -> list[dict[str, Any]]:
    rm1, rm2 = G.selftest_rm1(ctx), G.selftest_rm2()
    return [
        {
            "id": "RM1",
            "mutant": "the runner reports a row NOT RUN with every prerequisite present",
            "killed": bool(rm1["caught"] and rm1["not_run_only_in_one_row"]),
            **rm1,
        },
        {
            "id": "RM2",
            "mutant": "the self-hash check skips an entry or accepts an edited hash",
            "killed": bool(rm2["caught"]),
            **rm2,
        },
    ]


# ---- the command line --------------------------------------------------------------------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Mutants of .NET parity, round 1: LM, HM, DM, RM")
    ap.add_argument("--suite", default="LM,DM,RM", help="comma list of LM, HM, DM, RM")
    ap.add_argument("--kind", default="mutants", choices=list(G.KINDS))
    ap.add_argument(
        "--freeze",
        default="",
        help="the freeze file; every entry is verified before the first metric",
    )
    ap.add_argument("--out")
    ap.add_argument("--scratch", default="")
    ap.add_argument("--d1", default="")
    ap.add_argument(
        "--h1", default="", help="H1: a module directory <root>/tests/spec/<name> or a tarball"
    )
    ap.add_argument("--h1-truth", default="")
    ap.add_argument(
        "--patches", default="", help="a directory with m01.diff .. m16.diff and MUTANTS.md"
    )
    ap.add_argument("--streak", default="")
    ap.add_argument("--container-owner", default="dn-r")
    ap.add_argument(
        "--only",
        default="",
        help="mutant ids (LM1,LM5 / 1,5,9) of a shakedown or mutants run; a scored HM run covers all 16",
    )
    ap.add_argument(
        "--self-test",
        action="store_true",
        help="every suite's code path on the stand-ins; fails on an improper NOT RUN",
    )
    return ap


def mutants_ctx(args: argparse.Namespace, scratch: Path, out: Path) -> G.Ctx:
    ns = argparse.Namespace(arm="D", kind=args.kind, base="", d1=args.d1, h1=args.h1, h1_truth=args.h1_truth, streak=args.streak, dnapp="", changes="", j0="", freeze=args.freeze, out=str(out), scratch=str(scratch), runs=1, only="", container_owner=args.container_owner, driver_python="", no_docker=True, self_test=False, write_freeze="", extra=[])  # fmt: skip
    ctx = G.build_ctx(ns, scratch, out)
    ctx.runs = 1
    return ctx


def summarise(rows: list[dict[str, Any]], label: str) -> None:
    mut = [r for r in rows if r["id"] != "control"]
    killed = [r["id"] for r in mut if r.get("killed") is True]
    survived = [r["id"] for r in mut if r.get("killed") is False]
    nr = [r["id"] for r in mut if r.get("killed") is None]
    print(f"{label}: killed {len(killed)}/{len(mut)}; survived {survived}; not run {nr}")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    this = Path(__file__).resolve()
    suites = [s for s in args.suite.split(",") if s]
    if bad := [s for s in suites if s not in SUITES]:
        raise SystemExit(f"unknown suite(s) {bad}; the suites are {SUITES}")
    if args.self_test:
        return self_test(args, this)
    if not (args.freeze and args.out):
        raise SystemExit(
            "--freeze and --out are required: the self-hash check runs before any metric and cannot be skipped"
        )
    if "HM" in suites and args.kind == "mutants":
        raise SystemExit("the held-out mutants are blind: --kind shakedown|scored|rerun|verify")
    if args.only and args.kind in G.SCORED_KINDS:
        raise SystemExit(
            "--only restricts the mutants of a shakedown or mutants run; a scored, rerun or verify run covers all of them"
        )
    out, freeze_path = Path(args.out).resolve(), Path(args.freeze).resolve()
    scratch = Path(args.scratch).resolve() if args.scratch else out.parent / "scratch-mut"
    runner_sha, lattice_file = G.sha(this), G.lattice_file_inside()
    freeze_sha = G.sha(freeze_path) if freeze_path.is_file() else "missing"
    entries = G.read_ledger()
    for s in suites:
        why = G.ledger_refusal(entries, f"DN:{s}", args.kind, runner_sha, freeze_sha)
        if why:
            raise SystemExit(f"the ledger refuses suite {s}: {why}")
    run_id = uuid.uuid4().hex[:12]

    def entry(suite: str, status: str, **extra: Any) -> dict[str, Any]:
        return {"ts": G.now(), "run_id": run_id, "runner": RUNNER_NAME, "runner_sha256": runner_sha, "freeze_sha256": freeze_sha, "arm": f"DN:{suite}", "kind": args.kind, "out": str(out), "lattice_file": lattice_file, "dotnet_sdk": G.sdk_or_none(), "status": status, **extra}  # fmt: skip

    for s in suites:
        G.append_ledger(entry(s, "started"))
    metric_started = False
    doc: dict[str, Any] = {}
    try:
        patches = Path(args.patches).resolve() if args.patches else None
        h1 = Path(args.h1).resolve() if args.h1 else None
        h1_truth = Path(args.h1_truth).resolve() if args.h1_truth else None
        req = [REPO / "tests/dncheck/d1/truth.json"] + [p for p in (h1, h1_truth) if p is not None]
        if "HM" in suites:
            if not (h1 and patches):
                raise SystemExit("--suite HM needs --h1 and --patches")
            req += [patches / "MUTANTS.md", *[patches / patch_name(i) for i in IDS]]
        freeze = G.verify_freeze(freeze_path, this, args.kind, required_abs=req)
        freeze["lattice_file"] = lattice_file
        ctx = mutants_ctx(args, scratch, out)
        ctx.lib = G.RealLib()
        out.mkdir(parents=True, exist_ok=True)
        ids = [i.strip() for i in args.only.split(",") if i.strip()]
        for s in suites:
            if s == "LM":
                metric_started = True
                rows = lm_suite(
                    [i for i in LM_IDS if not ids or i in ids],
                    scratch,
                    out,
                    ["--d1", args.d1] if args.d1 else [],
                )
                doc["LM"] = rows
                summarise(rows, "LM")
            elif s in ("DM", "HM"):
                G.setup(
                    ctx,
                    ["D-ROUTES"] if s == "DM" else ["H-ROUTES"],
                    Path(args.d1).resolve() if args.d1 else None,
                    h1,
                    h1_truth,
                )
                ctx.streak_src = ctx.streak_src or None
                freeze["import_subprocess_lattice_file"] = G.lattice_subprocess_inside(ctx)
                metric_started = True
                if s == "DM":
                    doc["DM"] = dm_suite(ctx)
                    summarise(doc["DM"], "DM")
                else:
                    truth, rows_, files = validate_inputs(patches, Path(ctx.h_target["truth_path"]))  # type: ignore[arg-type]
                    only = tuple(int(x) for x in ids) if ids else IDS
                    doc["HM"] = run_hmut(
                        ctx, ctx.h_target, patches, truth, rows_, files, out, only=only
                    )  # type: ignore[arg-type]
                    print(json.dumps(doc["HM"].get("gate", {}), default=str)[:600])
            else:
                metric_started = True
                ctx.d1 = ctx.d1 or G.materialise("d1", G.D1_TGZ, scratch / "targets", G.D1_TRUTH)
                doc["RM"] = rm_suite(ctx)
                summarise(doc["RM"], "RM")
    except BaseException as err:  # noqa: BLE001 -- recorded in the ledger, then re-raised
        for s in suites:
            G.append_ledger(
                entry(
                    s,
                    "crash_after_metric" if metric_started else "crash_before_metric",
                    error=repr(err)[:400],
                )
            )
        if isinstance(err, G.FreezeError):
            print(err, file=sys.stderr)
            out.mkdir(parents=True, exist_ok=True)
            (out / "mutants-dn.json").write_text(
                json.dumps({"aborted": str(err), "kind": args.kind}, indent=2) + "\n"
            )
            return 2
        raise
    doc.update(
        runner=RUNNER_NAME,
        runner_sha256=runner_sha,
        kind=args.kind,
        run_id=run_id,
        lattice_file=lattice_file,
        freeze=freeze,
        suites=suites,
    )
    (out / "mutants-dn.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")
    for s in suites:
        G.append_ledger(entry(s, "completed"))
    print(f"ledger sha256: {G.sha(G.LEDGER)}")
    return 0


# ---- the self-test ---------------------------------------------------------------------------------------------------------------------------------------


def standin_patches(dest: Path) -> tuple[Path, Path]:
    """Sixteen stand-in held-out patches (D1's DM1-DM5 patches under the HM operators) with a MUTANTS.md in the format of this runner."""
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    use = {
        1: "dm1",
        2: "dm1",
        3: "dm2",
        4: "dm2",
        5: "dm3",
        6: "dm3",
        7: "dm4",
        8: "dm4",
        9: "dm5",
        10: "dm5",
        11: "dm1",
        12: "dm1",
        13: "dm1",
        14: "dm1",
        15: "dm2",
        16: "dm1",
    }
    route = {
        "dm1": "GET /v1/habits/{id}",
        "dm2": "PATCH /v1/habits/{id}",
        "dm3": "GET /v1/me",
        "dm4": "POST /v1/auth/logout",
        "dm5": "GET /v1/habits/{id}",
    }
    lines = [
        "# stand-in held-out mutants (self-test only)",
        "",
        "| id | operator | route | file:line | substitutions |",
        "|---|---|---|---|---|",
    ]
    for i in IDS:
        shutil.copyfile(G.D1_MUTANT_DIR / f"{use[i]}.diff", dest / patch_name(i))
        lines.append(f"| HM{i} | {INDEX_OPERATOR[i]} | {route[use[i]]} | x.cs:1 | none |")
    (dest / "MUTANTS.md").write_text("\n".join(lines) + "\n")
    return dest, dest / "MUTANTS.md"


def self_test(args: argparse.Namespace, this: Path) -> int:
    out = Path(args.out or tempfile.mkdtemp(prefix="dnm-selftest-")).resolve()
    if "docs/results/data" in out.as_posix():
        raise SystemExit("--self-test writes to a scratch directory, not to the results data")
    scratch = Path(args.scratch).resolve() if args.scratch else out / "scratch"
    ns = argparse.Namespace(**{**vars(args), "kind": "shakedown"})
    ctx = G.standin_ctx(argparse.Namespace(arm="D", kind="shakedown", base="", d1="", h1="", h1_truth="", streak="", dnapp="", changes="", j0="", freeze="", out=str(out), scratch=str(scratch), runs=1, only="", container_owner="dn-r", driver_python="", no_docker=True, self_test=True, write_freeze="", extra=[]), scratch, out)  # fmt: skip
    del ns
    problems: list[str] = []
    G.setup(ctx, ["D-ROUTES", "H-ROUTES"], None, None)
    sd1 = G.materialise("sd1", G.D1_TGZ, scratch / "targets", G.D1_TRUTH)
    sd1["label"] = "S-D1"
    G.build_target(ctx, sd1)
    ctx.h_target = sd1
    pdir, _ = standin_patches(scratch / "patches")
    truth, rows, files = validate_inputs(pdir, G.D1_TRUTH)
    sd1["truth_path"] = G.D1_TRUTH
    hm = run_hmut(ctx, sd1, pdir, truth, rows, files, out)
    if not hm.get("control", {}).get("passes"):
        problems.append(f"HM control: {hm.get('control')}")
    if len(hm.get("rows", ())) != 16:
        problems.append("HM: not all 16 mutants were judged")
    dm = dm_suite(ctx)
    if [r["id"] for r in dm] != sorted(G.DEV_MUTANTS):
        problems.append("DM: not all six")
    rm = rm_suite(ctx)
    problems += [f"{r['id']} not caught" for r in rm if not r["killed"]]
    lm = lm_suite(["LM1", "LM13", "LM20"], scratch / "lm", out / "lm", [])
    if not lm[0]["passes"]:
        problems.append(
            f"LM control did not pass: {lm[0].get('probes_not_passing_on_control')} {lm[0].get('tests_failing_on_control')}"
        )
    problems += [
        f"{r['id']}: {r.get('why')}"
        for r in lm[1:]
        if r.get("status") in ("edit_failed", "no_edits")
    ]
    problems += [
        f"{r['id']}: NOT RUN although its node is in the tree"
        for r in lm[1:]
        if r.get("killed") is None and (r.get("node") is None or G.node_present(r["node"]))
    ]
    (out / "selftest-mutants.json").write_text(
        json.dumps(
            {
                "HM": hm,
                "DM": dm,
                "RM": rm,
                "LM": lm,
                "problems": problems,
                "runner_sha256": G.sha(this),
            },
            indent=2,
            default=str,
        )
        + "\n"
    )
    print("HM gate (stand-in patches):", json.dumps(hm.get("gate", {}), default=str)[:400])
    print("DM:", {r["id"]: r.get("killed") for r in dm})
    print("LM:", {r["id"]: r.get("killed") for r in lm})
    print(
        "SELF-TEST",
        "FAILED: " + "; ".join(problems)
        if problems
        else "OK: every suite's code path ran; RM1 and RM2 caught",
    )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
