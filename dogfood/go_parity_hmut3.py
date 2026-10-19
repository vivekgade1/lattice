"""The held-out mutants HM1-HM16 of Go parity, round 3 (docs/results/2026-10-18-go-parity-round3-prereg.md,
section 6.2; Spike J, node G3, build node HM): the runner that judges the 16 patches of the held-out mutant
writer W on a scratch copy of the held-out service S4. Section 6.2 is the only spec; nothing here changes a
gate, a detector or a threshold.

    "$PY" dogfood/go_parity_hmut3.py --s4 tests/spec/<x> --patches "$PATCHES" --truth "$TRUTH" \\
        --freeze "$FREEZE" --kind scored --out docs/results/data/2026-10-18-go-parity-round3/hmut \\
        --scratch "$SCRATCH/hmut" --base "$SCRATCH/base"

``--s4`` is the module directory of S4 (its harness is beside it, as ``tests/spec/<x>_harness.py``),
``--patches`` holds ``m01.diff`` .. ``m16.diff`` (unified diffs, applied with ``patch -p1`` from inside a copy of
the module directory) and ``MUTANTS.md`` (one line per patch: id, operator, the route ``METHOD /path``,
file:line, substitutions), ``--truth`` is ``truth.json`` (prereg section 4), ``--base`` an extracted
``git archive 978b468``. ``--only 1,5`` restricts the mutants of a *shakedown* (never of a scored, rerun or
verify run); it narrows nothing else.

How a row is judged (the same machinery Arm H uses on S4: ``go_parity_probes3`` ``h_dir`` / ``make_target`` /
``r_on_spec3``, and round 2's ``go_parity_s3.layer_a_new``; all imported, none edited):

* the candidate spec is the rules-first import of the *unmutated* S4 (Arm H's import);
* layer A: the checker under ``go_spec_policy`` of that spec runs on the mutant copy and on the original;
  the mutant's *new* findings are those whose ``(code, message)`` the original does not have
  (``layer_a_new``); a detector fires when a new finding has the registered code on the registered route
  (and, for GO802, a private column of the truth);
* layer R: the candidate spec is probed (variant ``seed``) on the control binary and on the mutant's binary
  (the harness's ``BINARY_ENV`` names it); a detector fires when its item is verified on the control and
  refuted on the mutant. The control is run twice; an item whose two verdicts differ is not used;
* the control comes first: the unmutated S4 must give no GO801/GO802 on any HM route, no registered R item
  may be refuted, and a pristine copy must give no new finding; if it does not, no mutant is judged.

Operator -> detector (prereg 6.2; the operator is read from ``MUTANTS.md`` and falls back to the id):

    HM1-4   owner filter dropped (read, write)   gated    layer A GO801 on the route          class gate >= 3 of 4
    HM5-6   never-returned column exposed        gated    layer A GO802 on the route + column  2 of 2
    HM7-8   login requirement removed            gated    layer R ``R1 <route>`` refuted       2 of 2
    HM9-10  403 where truth says 404 not_found   gated    layer R the route's 404 ``R3`` item   2 of 2
    HM11    parent-ownership check dropped       reported GO801 on the route, or ``R2 <route>`` refuted
    HM12-13 state guard (conflict) dropped       reported ``R3 <route> <conflict status>`` refuted
    HM14    validation bound loosened            reported ``R3 <route> <invalid status>`` refuted
    HM15    owner taken from the request body    reported GO801 on the route, or ``R2 <route>`` refuted
    HM16    list owner filter widened            reported GO801 on the route, or ``R2 <route>`` refuted

The overall gate is >= 13 of 16 killed; predicted 12-15.

What cannot be switched off (prereg section 10, rules 2 and 3): the self-hash check (``--freeze``: every entry
is re-hashed, this runner and its test must be listed, and so must the patches, ``MUTANTS.md``, ``truth.json``,
S4's sources and its harness); ``lattice.__file__`` must lie inside this worktree; the ledger
``docs/results/data/2026-10-18-go-parity-round3/ledger-hmut.jsonl`` (a fixed path) refuses a second scored run
and a rerun unless the scored run ended ``crash_before_metric``. No model is called, no Docker is used.
"""  # noqa: E501

# ruff: noqa: E501
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
import traceback
import uuid
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import go_parity_probes as P1  # noqa: E402  (round 1, frozen: helpers)
import go_parity_probes3 as P3  # noqa: E402  (G3, frozen: the Arm H machinery)
import go_parity_s3 as S3M  # noqa: E402  (round 2, frozen: layer A on copies)
import go_parity_s3r as S3R  # noqa: E402  (G3 Tier 0, frozen: freeze, ledger, K7)

REPO = P3.REPO
RUNNER_NAME = "go_parity_hmut3"
RUNNER_REL = "dogfood/go_parity_hmut3.py"
TEST_REL = "tests/spec/test_go_parity_hmut3.py"
LEDGER = S3R.R3_DATA / "ledger-hmut.jsonl"  # fixed here, never derived from --out
ARM = "G3:HMUT"
KINDS = S3R.KINDS
BASE_REF = P3.BASE_REF
VERIFIED = P1.VERIFIED
IDS = tuple(range(1, 17))
GATE_OVERALL = 13
PREDICTED_KILLED = (12, 15)
CLASS_GATES: tuple[tuple[str, tuple[int, ...], int], ...] = (
    ("HM1-4", (1, 2, 3, 4), 3),
    ("HM5-6", (5, 6), 2),
    ("HM7-8", (7, 8), 2),
    ("HM9-10", (9, 10), 2),
)
# the files this runner imports or relies on must be in the freeze file, on top of the G3 list
MUST_LIST = tuple(
    dict.fromkeys(
        (
            RUNNER_REL,
            TEST_REL,
            *P3.MUST_LIST,
            "dogfood/go_parity_probes.py",
            "dogfood/go_parity_probes2.py",
            "dogfood/go_parity_s3.py",
        )
    )
)
SOURCE_SUFFIXES = (".go",)
SOURCE_NAMES = ("go.mod", "go.sum", "studio.toml", "truth.json")
IGNORED = (".DS_Store", "__pycache__")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


# ---- the operators and their detectors (prereg 6.2) ---------------------------------------------------------


@dataclass(frozen=True)
class Detector:
    """One thing that can flip. ``layer`` A: a new finding with ``code`` on the route. Layer R: a verified
    ``code`` item of the route that becomes refuted (``cause``: the truth refusal cause whose status the R3 item
    carries)."""

    layer: str
    code: str
    cause: str | None = None


@dataclass(frozen=True)
class Operator:
    key: str
    gated: bool
    detectors: tuple[Detector, ...]
    what: str


A801, A802 = Detector("A", "GO801"), Detector("A", "GO802")
R1, R2 = Detector("R", "R1"), Detector("R", "R2")
OPERATORS: dict[str, Operator] = {
    "owner_read": Operator("owner_read", True, (A801,), "drop the owner filter of an owner-scoped read"),
    "owner_write": Operator("owner_write", True, (A801,), "drop the owner filter of an owner-scoped write or state change"),
    "expose_column": Operator("expose_column", True, (A802,), "expose a never-returned column on a route that answers its row"),
    "drop_login": Operator("drop_login", True, (R1,), "remove the login requirement from a Member route"),
    "status_403": Operator("status_403", True, (Detector("R", "R3", "not_found"),), "answer 403 where truth says 404 not_found"),
    "drop_parent": Operator("drop_parent", False, (A801, R2), "drop the parent-ownership check of an owner_via table"),
    "drop_guard": Operator("drop_guard", False, (Detector("R", "R3", "conflict"),), "drop a state guard (conflict) of a transition"),
    "loosen_bound": Operator("loosen_bound", False, (Detector("R", "R3", "invalid"),), "loosen a validation bound (remove the upper bound)"),
    "owner_body": Operator("owner_body", False, (A801, R2), "take the owner from the request body on create"),
    "widen_list": Operator("widen_list", False, (A801, R2), "widen a list route's owner filter to every row"),
}  # fmt: skip
INDEX_OPERATOR = {
    1: "owner_read", 2: "owner_read", 3: "owner_write", 4: "owner_write", 5: "expose_column",
    6: "expose_column", 7: "drop_login", 8: "drop_login", 9: "status_403", 10: "status_403",
    11: "drop_parent", 12: "drop_guard", 13: "drop_guard", 14: "loosen_bound", 15: "owner_body",
    16: "widen_list",
}  # fmt: skip
# the status an item carries when the truth has no refusal of that cause for the route
DEFAULT_STATUS = {"not_found": (404,), "conflict": (409,), "invalid": (422, 400)}
OPERATOR_PATTERNS: tuple[tuple[str, str], ...] = (  # first match wins
    ("widen_list", r"widen|every row|all rows"),
    ("expose_column", r"expos|never returned"),
    ("drop_login", r"login|authenticat|\bauth\b"),
    ("status_403", r"\b403\b|another status|status swap|wrong status"),
    ("drop_parent", r"parent|owner via"),
    ("drop_guard", r"guard|conflict|transition"),
    ("loosen_bound", r"validation|loosen|\bbound"),
    ("owner_body", r"request body|from the body|body on create|owner from"),
)  # fmt: skip


def classify_operator(text: str) -> str | None:
    """The operator key named by ``text`` (the operator column of MUTANTS.md), or None."""
    t = re.sub(r"[`'\"_\-*]+", " ", text.lower())
    t = re.sub(r"\s+", " ", t).strip()
    for key, pat in OPERATOR_PATTERNS:
        if re.search(pat, t):
            return key
    if "owner" in t or "filter" in t:
        return (
            "owner_write"
            if re.search(r"write|state change|update|mutat|change", t)
            else "owner_read"
        )
    return None


def operator_of(num: int, text: str) -> dict[str, Any]:
    """The operator used for mutant ``num``: the text's when it names one, else the id's (prereg instance rule:
    when only one route fits an operator the next gets three instances, so the text may differ from the id's)."""
    by_text, by_id = classify_operator(text), INDEX_OPERATOR[num]
    key = by_text or by_id
    # read and write are one detector (GO801 on the route): a read/write difference is not a shift
    shifted = by_text is not None and OPERATORS[by_text].detectors != OPERATORS[by_id].detectors
    return {
        "key": key,
        "from_text": by_text,
        "from_id": by_id,
        "shifted": shifted,
        "recognised": by_text is not None,
    }


# ---- MUTANTS.md and the patch set -----------------------------------------------------------------------------


@dataclass(frozen=True)
class MutRow:
    num: int
    line: str
    operator_text: str
    method: str
    path: str
    file: str | None
    file_line: int | None
    substitutions: str


ID_RE = re.compile(r"^[\s|*\->`#.]*(?:HM|M)?0*(\d{1,2})\b", re.I)
ROUTE_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[^\s'\"`|,;)]*)")
FILE_RE = re.compile(r"([\w./-]+\.go):(\d+)")


def parse_mutants_md(text: str) -> dict[int, MutRow]:
    """One MutRow per line that starts with an id and carries a route. A line without a route is ignored (a header);
    the caller checks that all 16 ids are there."""
    rows: dict[int, MutRow] = {}
    for ln in text.splitlines():
        m = ID_RE.match(ln)
        r = ROUTE_RE.search(ln)
        if not m or not r:
            continue
        num = int(m.group(1))
        if not 1 <= num <= 16:
            continue
        if num in rows:
            raise ValueError(f"MUTANTS.md: id {num} appears twice")
        f = FILE_RE.search(ln, r.end())
        rest = ln[(f.end() if f else r.end()) :]
        rows[num] = MutRow(
            num, ln.strip(), ln[m.end() : r.start()].strip(" |,;:-*`'\""), r.group(1), r.group(2).rstrip(".,"),
            f.group(1) if f else None, int(f.group(2)) if f else None, rest.strip(" |,;:-*`")
        )  # fmt: skip
    return rows


def patch_name(num: int) -> str:
    return f"m{num:02d}.diff"


def tree_files(root: Path) -> dict[str, str]:
    """relative path -> sha256 of every regular file under ``root`` (junk names skipped)."""
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not any(part in IGNORED for part in p.relative_to(root).parts):
            out[p.relative_to(root).as_posix()] = P1.sha(p)
    return out


def tree_sha256(root: Path) -> str:
    h = hashlib.sha256()
    for rel, digest in tree_files(root).items():
        h.update(rel.encode() + b"\0" + digest.encode() + b"\0")
    return h.hexdigest()


def diff_trees(a: Mapping[str, str], b: Mapping[str, str]) -> list[str]:
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def norm_path(p: str) -> str:
    """A path in the importer's ``{x}`` form without a trailing slash."""
    return P3.norm_path(p)


def gin_path(p: str) -> str:
    return P3.gin_path(p)


def route_id(method: str, path: str) -> tuple[str, str]:
    return method.upper(), gin_path(path)


def route_label(route: tuple[str, str]) -> str:
    return f"{route[0]} {route[1]}"


# ---- the truth: what each detector looks for ----------------------------------------------------------------------


def truth_routes(truth: Mapping[str, Any]) -> dict[tuple[str, str], str]:
    return {route_id(r["method"], r["path"]): r["auth"] for r in truth["routes"]}


def refusal_statuses(
    truth: Mapping[str, Any], route: tuple[str, str], cause: str
) -> tuple[list[int], bool]:
    """The statuses the truth gives the route's refusals of ``cause``; (statuses, from_truth). Without one the
    default for the cause is used (and the evidence says so)."""
    got: list[int] = []
    for r in truth.get("refusals", ()):
        method, _, path = str(r["route"]).partition(" ")
        if (
            r.get("cause") == cause
            and route_id(method, path) == route
            and int(r["status"]) not in got
        ):
            got.append(int(r["status"]))
    return (got, True) if got else (list(DEFAULT_STATUS[cause]), False)


def private_columns(truth: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """(struct, table, column) of every private column of the truth."""
    return [(t["struct"], t.get("table", ""), c["name"]) for t in truth.get("tables", ()) for c in t.get("columns", ()) if c.get("private")]  # fmt: skip


def column_spellings(name: str) -> tuple[str, str]:
    """``password_hash`` -> (password_hash, PasswordHash): how MUTANTS.md or a diff may spell the column."""
    return name, "".join(w[:1].upper() + w[1:] for w in name.split("_"))


def expected_columns(truth: Mapping[str, Any], text: str) -> tuple[set[str], bool]:
    """The ``Struct.column`` / ``table.column`` spellings a GO802 finding of the mutant may carry: the private columns
    of the truth that MUTANTS.md names for it, else every private column of the truth (resolved=False)."""
    cols = private_columns(truth)
    named = [
        c
        for c in cols
        if any(
            re.search(rf"(?<![A-Za-z0-9_]){re.escape(v)}(?![A-Za-z0-9_])", text)
            for v in column_spellings(c[2])
        )
    ]
    chosen = named or cols
    out = {f"{s}.{c}" for s, _, c in chosen} | {f"{t}.{c}" for _, t, c in chosen if t}
    return out, bool(named)


# ---- judging (pure: the unit tests feed these synthetic inputs) ------------------------------------------------


def key_route(key: str) -> tuple[str, str] | None:
    """``GO801|POST /tickets/:tid/close|Ticket`` -> (POST, /tickets/:tid/close)."""
    parts = key.split("|")
    if len(parts) < 2:
        return None
    method, _, path = parts[1].partition(" ")
    return route_id(method, path) if method in METHODS and path else None


def item_parts(item: str) -> tuple[str, tuple[str, str], int | None] | None:
    """``R3 POST /x/{id} 404 Not found`` -> (R3, (POST, /x/:id), 404); None when it is no per-route item."""
    toks = item.split(" ")
    if len(toks) < 3 or toks[1] not in METHODS:
        return None
    status = int(toks[3]) if len(toks) > 3 and toks[3].isdigit() else None
    return toks[0], route_id(toks[1], toks[2]), status


def a_hits(
    findings: Iterable[Mapping[str, str]],
    code: str,
    route: tuple[str, str],
    columns: set[str] | None = None,
) -> list[str]:
    """Keys of the findings with ``code`` on ``route`` (GO802: and a column of ``columns``)."""
    out = []
    for f in findings:
        key = f["key"]
        if f.get("code", key.split("|")[0]) != code or key_route(key) != route:
            continue
        if code == "GO802" and columns is not None and key.split("|")[-1] not in columns:
            continue
        out.append(key)
    return out


def r_items(
    items: Iterable[str], code: str, route: tuple[str, str], statuses: Sequence[int] = ()
) -> list[str]:
    out = []
    for i in sorted(set(items)):
        p = item_parts(i)
        if p and p[0] == code and p[1] == route and (code != "R3" or p[2] in statuses):
            out.append(i)
    return out


def eval_detector(
    det: Detector,
    route: tuple[str, str],
    *,
    a_control: Sequence[str],
    a_new: Sequence[Mapping[str, str]],
    r_control: Mapping[str, str],
    r_mutant: Mapping[str, str] | None,
    unstable: Collection[str] = (),
    statuses: Sequence[int] = (),
    columns: set[str] | None = None,
) -> dict[str, Any]:
    """Did ``det`` flip on the mutant? ``armed`` is False when it cannot (a finding already on the control; no such
    item, or no verified one, on the control; the layer-R data of the mutant is missing)."""
    if det.layer == "A":
        dirty = a_hits([{"key": k} for k in a_control], det.code, route, columns)
        hits = a_hits(a_new, det.code, route, columns)
        return {"detector": f"{det.code} on {route_label(route)}", "layer": "A", "armed": not dirty, "flipped": not dirty and bool(hits),
                "hits": hits, "control_findings": dirty, "why_not": "the control already has the finding" if dirty else ("" if hits else "no new finding of that code on the route")}  # fmt: skip
    items = r_items({*r_control, *(r_mutant or {})}, det.code, route, statuses)
    rows = {i: [r_control.get(i), (r_mutant or {}).get(i)] for i in items}
    usable = [i for i in items if i not in unstable and r_control.get(i) in VERIFIED]
    hits = [i for i in usable if (r_mutant or {}).get(i) == "refuted"]
    why = ""
    if r_mutant is None:
        why = "no layer-R data for the mutant"
    elif not items:
        why = "the spec has no such item"
    elif not usable:
        why = "no such item is verified on the control (or its control verdicts differ between two runs)"
    elif not hits:
        why = "no such item is refuted on the mutant"
    return {"detector": f"{det.code} {route_label(route)}" + (f" status {list(statuses)}" if det.code == "R3" else ""), "layer": "R",
            "armed": r_mutant is not None and bool(usable), "not_verified_on_control": [i for i in items if r_control.get(i) not in VERIFIED], "flipped": bool(hits), "hits": hits, "items [control, mutant]": rows, "why_not": why}  # fmt: skip


def judge_mutant(
    num: int,
    operator: str,
    route: tuple[str, str],
    *,
    a_control: Sequence[str],
    a_new: Sequence[Mapping[str, str]],
    r_control: Mapping[str, str],
    r_mutant: Mapping[str, str] | None,
    unstable: Collection[str],
    truth: Mapping[str, Any],
    mutants_md_text: str,
) -> dict[str, Any]:
    """The verdict of one mutant: killed iff a registered detector of its operator flips. ``r_mutant`` None means layer R
    cannot judge it (the harness cannot be handed a binary): then only layer-A detectors can kill it, and a mutant whose
    detectors are all layer R is NOT RUN (killed None)."""
    op = OPERATORS[operator]
    cols, resolved = (
        expected_columns(truth, mutants_md_text) if operator == "expose_column" else (None, True)
    )
    details, notes = [], {}
    for det in op.detectors:
        statuses: Sequence[int] = ()
        if det.code == "R3" and det.cause:
            statuses, from_truth = refusal_statuses(truth, route, det.cause)
            notes[f"status_of_{det.cause}"] = {"statuses": list(statuses), "from_truth": from_truth}
        details.append(eval_detector(det, route, a_control=a_control, a_new=a_new, r_control=r_control, r_mutant=r_mutant,
                                     unstable=unstable, statuses=statuses, columns=cols))  # fmt: skip
    killed: bool | None = any(d["flipped"] for d in details)
    layer_r_missing = r_mutant is None and any(d.layer == "R" for d in op.detectors)
    if not killed and layer_r_missing and all(d.layer == "R" for d in op.detectors):
        killed = None
    if killed:
        by = next(d["detector"] for d in details if d["flipped"])
    elif killed is None:
        by = "NOT RUN: layer R cannot be fed the mutant's binary"
    else:
        by = "survived: " + "; ".join(
            f"{d['detector']}: {d['why_not'] or 'not armed'}" for d in details
        )
    return {"operator_key": operator, "gated": op.gated, "route": route_label(route), "detectors": details, "killed": killed, "killed_by": by,
            "expose_column": {"columns": sorted(cols or ()), "named_by_mutants_md": resolved} if operator == "expose_column" else None, **notes}  # fmt: skip


def judge_control(
    routes: Iterable[tuple[str, str]],
    registered_r: Iterable[str],
    *,
    a_control: Sequence[str],
    r_control: Mapping[str, str],
    pristine_new: Sequence[Mapping[str, str]],
    copy_equals_s4: bool,
    r_available: bool = True,
) -> dict[str, Any]:
    """Prereg 6.2 control: no GO801/GO802 on any HM route in the original, no registered R item refuted, the pristine copy
    adds no finding, and the copy is S4 byte for byte."""
    rs = set(routes)
    dirty = [k for k in a_control if k.split("|")[0] in ("GO801", "GO802") and key_route(k) in rs]
    refuted = sorted(i for i in set(registered_r) if r_control.get(i) == "refuted")
    ok = not dirty and not refuted and not pristine_new and copy_equals_s4
    return {"passes": ok, "go801_go802_on_hm_routes": dirty, "registered_r_items_refuted": refuted,
            "pristine_copy_new_findings": [f"{x['code']} {x['key']}" for x in pristine_new], "pristine_copy_equals_s4": copy_equals_s4,
            "r_available": r_available}  # fmt: skip


def gate_report(killed: Mapping[int, bool | None]) -> dict[str, Any]:
    """Prereg 6.2 gates: the four class gates and >= 13 of 16. A row not run makes the gate ``incomplete`` (never a pass)."""
    classes = {}
    for name, ids, need in CLASS_GATES:
        n = sum(1 for i in ids if killed.get(i) is True)
        classes[name] = {"killed": n, "of": len(ids), "need": need, "pass": n >= need}
    total = sum(1 for i in IDS if killed.get(i) is True)
    not_run = [f"HM{i}" for i in IDS if killed.get(i) is None]
    ok = total >= GATE_OVERALL and all(c["pass"] for c in classes.values())
    return {"killed": total, "of": 16, "need": GATE_OVERALL, "predicted": list(PREDICTED_KILLED), "classes": classes, "not_run": not_run,
            "verdict": "incomplete" if not_run else ("pass" if ok else "fail"), "survivors": [f"HM{i}" for i in IDS if killed.get(i) is False]}  # fmt: skip


# ---- the self-hash check (never disabled) ------------------------------------------------------------------------------


def harness_file(s4: Path) -> Path | None:
    """The harness module file S4's studio.toml names (``[app] app = "pkg.mod:attr"``), next to the module directory."""
    toml = s4 / "studio.toml"
    if not toml.is_file():
        return None
    mod = str(tomllib.loads(toml.read_text()).get("app", {}).get("app", "")).partition(":")[0]
    return (s4.parent / (mod.rpartition(".")[2] + ".py")) if mod else None


def s4_sources(s4: Path) -> list[Path]:
    """The files of S4 that decide its behaviour: Go sources, go.mod, go.sum, studio.toml, truth.json."""
    return [
        p
        for rel in tree_files(s4)
        if ((p := s4 / rel).suffix in SOURCE_SUFFIXES or p.name in SOURCE_NAMES)
    ]


def required_paths(s4: Path, patches: Path, truth: Path) -> list[Path]:
    req = [truth, patches / "MUTANTS.md", *[patches / patch_name(i) for i in IDS], *s4_sources(s4)]
    if (h := harness_file(s4)) is not None:
        req.append(h)
    return req


def verify_freeze(
    path: Path, this: Path, required: Sequence[Path] = (), *, root: Path = REPO
) -> dict[str, Any]:
    """Every entry of the freeze file is re-hashed (absolute paths allowed); this runner, its test and every file of
    :data:`MUST_LIST` must be listed, and so must every file of ``required`` (the patches, MUTANTS.md, truth.json, S4's
    sources and its harness). Aborts on any problem; nothing can be excluded."""
    info = S3R.verify_listed(path, root=root, must_list=MUST_LIST, this=this)
    listed = {(root / rel).resolve() for _, rel in S3R.read_entries(path)}
    absent = sorted(str(p) for p in required if p.resolve() not in listed)
    if absent:
        raise S3R.FreezeError(
            f"freeze check failed ({path.name}): not listed {absent[:6]}{' ...' if len(absent) > 6 else ''} ({len(absent)} files)"
        )
    info["required_files_listed"] = len(required)
    return info


# ---- the ledger ------------------------------------------------------------------------------------------------------------


def ledger_gate(
    kind: str, runner_sha: str, freeze_sha: str, path: Path | None = None
) -> str | None:
    """None when a run of ``kind`` may start, else why not (one scored run; one rerun only after crash_before_metric)."""
    return S3R.ledger_refusal(S3R.read_ledger(path or LEDGER), ARM, kind, runner_sha, freeze_sha)


# ---- the machinery on the held-out ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Fx:
    """What ``layer_a_new`` reads of its fixture: the module directory of the unmutated service."""

    module_dir: Path


@contextmanager
def binary_env(name: str | None, path: Path | None):
    """The harness' ``BINARY_ENV`` set to ``path`` for the block (the process environment is restored)."""
    if not name or path is None:
        yield
        return
    keep = os.environ.get(name)
    os.environ[name] = str(path)
    try:
        yield
    finally:
        if keep is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = keep


def harness_module(tgt: dict[str, Any]) -> Any:
    """The harness module of S4 (import root on ``sys.path`` first, as the CLI does)."""
    import importlib

    toml = tomllib.loads((tgt["module_dir"] / "studio.toml").read_text())
    name = str(toml.get("app", {}).get("app", "")).partition(":")[0]
    S3R.add_import_root(tgt["root"])
    S3R.add_import_root(REPO)
    return importlib.import_module(name)


def binary_env_name(mod: Any) -> str | None:
    v = getattr(mod, "BINARY_ENV", None)
    return v if isinstance(v, str) and v else None


def binary_injected(
    mod: Any, env_name: str | None, binary: Path, scratch: Path
) -> tuple[bool, str]:
    """Does ``mod.make_app`` start ``binary`` when ``env_name`` names it? (layer R is only as good as this)"""
    if not env_name:
        return False, "the harness defines no BINARY_ENV"
    try:
        with binary_env(env_name, binary):
            app = mod.make_app(scratch / "inject-check.db")
            used = str(list(app.argv)[0])
    except Exception as err:  # noqa: BLE001 -- reported, layer R then cannot judge the mutants
        return False, f"make_app raised {type(err).__name__}: {err}"
    return (used == str(binary)), f"make_app starts {used}"


def make_ctx(args_like: Mapping[str, Any], scratch: Path, out: Path) -> P3.Ctx:
    ctx = P3.Ctx(scratch, out, BASE_REF)
    ctx.base_dir = Path(args_like["base"]).resolve()
    ctx.runs = 1
    return ctx


def setup(ctx: P3.Ctx, tgt: dict[str, Any], patches: Path) -> dict[str, Any]:
    """Shims, scratch, the base link, the snapshots of the repository's fixture roots, of S4 and of the patches."""
    os.environ.setdefault("G3_REAL_PATH", os.environ["PATH"])
    P1.make_shims(ctx)
    ctx.scratch.mkdir(parents=True, exist_ok=True)
    ctx.out.mkdir(parents=True, exist_ok=True)
    rel = tgt.get("snapshot_rel")
    if rel and rel not in P1.SNAPSHOT_ROOTS:
        P1.SNAPSHOT_ROOTS.append(rel)
    link = ctx.scratch / "base"
    if link.is_symlink():
        link.unlink()
    shutil.rmtree(link, ignore_errors=True)
    link.symlink_to(ctx.base_dir, target_is_directory=True)  # type: ignore[arg-type]
    ctx.snap0 = P1.snapshots()
    extra = {str(patches): P1.snapshot(patches)}
    if not rel:
        extra[str(tgt["module_dir"])] = P1.snapshot(tgt["module_dir"])
    h = harness_file(tgt["module_dir"])
    if h is not None and h.is_file():
        extra[str(h)] = {h.name: [P1.sha(h)]}
    return extra


def finish_snapshots(
    ctx: P3.Ctx, extra: Mapping[str, Any], tgt: dict[str, Any], patches: Path
) -> list[str]:
    changed = P1.snapshot_diff(ctx.snap0, P1.snapshots())
    for k, before in extra.items():
        p = Path(k)
        now = P1.snapshot(p) if p.is_dir() else {p.name: [P1.sha(p)]}
        changed += [f"{k}/{rel}" for rel in P3.diff_snaps(before, now)]
    return changed


def changed_lines(patch: Path) -> str:
    """The added and removed lines of a unified diff (what a mutant's MUTANTS.md line may not spell out)."""
    return "\n".join(
        ln[1:]
        for ln in patch.read_text().splitlines()
        if ln[:1] in "+-" and not ln.startswith(("+++", "---"))
    )


def apply_patch(copy: Path, patch: Path) -> dict[str, Any]:
    """``patch -p1`` from inside ``copy``. A reversed or already-applied hunk, a reject or a nonzero exit is a failure
    (Apple's patch otherwise reverses silently); the files it changed are reported."""
    before = tree_files(copy)
    proc = subprocess.run(["patch", "-p1", "-N", "-V", "none", "-i", str(patch)], cwd=copy, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)  # fmt: skip
    out = proc.stdout + proc.stderr
    rej = sorted(
        p.relative_to(copy).as_posix() for p in copy.rglob("*") if p.suffix in (".rej", ".orig")
    )
    bad = (
        proc.returncode != 0
        or rej
        or re.search(r"reversed|previously applied|hunks? (?:FAILED|failed|ignored)", out)
    )
    return {"ok": not bad, "returncode": proc.returncode, "output": out[-1200:], "leftovers": rej,
            "files_changed": diff_trees(before, tree_files(copy)), "fuzz_or_offset": bool(re.search(r"offset|fuzz", out))}  # fmt: skip


def go_build(src: Path, out: Path) -> tuple[bool, str]:
    ok, err, _ = P1.go_build(src, out)
    return ok, err


def copy_module(src: Path, dest: Path) -> None:
    shutil.rmtree(dest.parent, ignore_errors=True)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(*IGNORED))


def r_map(
    ctx: P3.Ctx, tgt: dict[str, Any], cand: Any, env_name: str | None, binary: Path | None
) -> dict[str, str]:
    """Layer R of the candidate spec on ``binary`` (variant ``seed``: Arm H's machinery)."""
    with binary_env(env_name, binary):
        return P1.verdict_map(P3.r_on_spec3(ctx, tgt, cand.SPEC, "seed"))


def original_keys(cand: Any, fx: Fx) -> list[str]:
    """Every finding of the original under ``go_spec_policy`` of the candidate spec."""
    from lattice.gocheck.derive import derive_go_policy, go_spec_policy

    derived = derive_go_policy(fx.module_dir)
    policy = go_spec_policy(cand.SPEC, fx.module_dir, derived)
    return sorted(f.key for f in P1.findings_of(fx.module_dir, policy))


def layer_a_row(cand: Any, fx: Fx, copy: Path) -> dict[str, Any]:
    return S3M.layer_a_new(cand, fx, copy)  # type: ignore[arg-type]


# ---- one run ------------------------------------------------------------------------------------------------------------------


def validate_inputs(
    s4: Path, patches: Path, truth_path: Path
) -> tuple[dict[str, Any], dict[int, MutRow], dict[int, Path]]:
    """Before any metric: the patch set is m01..m16 + MUTANTS.md, every line parses with a route that is in the truth."""
    problems = []
    if not (s4 / "go.mod").is_file():
        problems.append(f"{s4} is no Go module (no go.mod)")
    truth = json.loads(truth_path.read_text())
    files = {i: patches / patch_name(i) for i in IDS}
    problems += [f"{p} does not exist" for p in files.values() if not p.is_file()]
    md = patches / "MUTANTS.md"
    rows: dict[int, MutRow] = {}
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
    routes = truth_routes(truth) if "routes" in truth else {}
    for i, r in rows.items():
        if route_id(r.method, r.path) not in routes:
            problems.append(f"HM{i}: {r.method} {r.path} is not a route of truth.json")
    if problems:
        raise SystemExit("input check failed: " + "; ".join(problems))
    return truth, rows, files


def run_hmut(
    ctx: P3.Ctx,
    tgt: dict[str, Any],
    patches: Path,
    truth: dict[str, Any],
    rows: Mapping[int, MutRow],
    files: Mapping[int, Path],
    out: Path,
    *,
    only: Sequence[int] = IDS,
    on_control: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """The control and the mutants. No freeze check and no ledger here (they belong to :func:`main`); the evidence JSON of
    every mutant is written to ``out/evidence`` as soon as it is judged."""
    t_start = time.perf_counter()
    s4 = tgt["module_dir"]
    fx = Fx(s4)
    ev_dir = out / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    doc: dict[str, Any] = {"s4": str(s4), "s4_tree_sha256": tree_sha256(s4), "mutants_md_sha256": P1.sha(patches / "MUTANTS.md"),
                           "truth_sha256": P1.sha(Path(tgt.get("truth_path") or s4 / "truth.json")), "patch_sha256": {patch_name(i): P1.sha(files[i]) for i in IDS}}  # fmt: skip
    if shutil.which("go") is None:
        doc.update(
            control={"passes": None, "not_run": "go is not on PATH"},
            rows=[],
            gate=gate_report({}),
            not_run="go is not on PATH",
        )
        return doc
    mod = harness_module(tgt)
    env_name = binary_env_name(mod)
    # ---- the control: the unmutated copy, judged by every detector
    cdir = ctx.scratch / "mut" / "control" / s4.name
    copy_module(s4, cdir)
    cbin = ctx.scratch / "mut" / "control" / "bin"
    ok, err = go_build(cdir, cbin)
    if not ok:
        doc.update(
            control={"passes": False, "build_error": err[-600:]}, rows=[], gate=gate_report({})
        )
        return doc
    injected, why = binary_injected(mod, env_name, cbin, ctx.scratch)
    ctx.fx_env, ctx.fx_bin = env_name or ctx.fx_env, cbin
    if env_name:
        os.environ[env_name] = str(cbin)
    with binary_env(env_name, cbin):
        imp = P3.h_dir(ctx, tgt)
    cand = P1.load_candidate(imp)
    a_control = original_keys(cand, fx)
    pristine = layer_a_row(cand, fx, cdir)
    r1 = r_map(ctx, tgt, cand, env_name, cbin)
    r2 = r_map(ctx, tgt, cand, env_name, cbin)
    unstable = sorted(i for i in {*r1, *r2} if r1.get(i) != r2.get(i))
    plans = {}
    for i in IDS:
        row = rows[i]
        op = operator_of(i, row.operator_text)
        plans[i] = {"num": i, "route": route_id(row.method, row.path), "operator": op}
    registered: list[str] = []
    for p in plans.values():
        for det in OPERATORS[p["operator"]["key"]].detectors:
            if det.layer == "R":
                sts = (
                    refusal_statuses(truth, p["route"], det.cause)[0]
                    if det.code == "R3" and det.cause
                    else ()
                )
                registered += r_items(r1, det.code, p["route"], sts)
    control = judge_control([p["route"] for p in plans.values()], registered, a_control=a_control, r_control=r1,
                            pristine_new=pristine["new"], copy_equals_s4=tree_sha256(cdir) == doc["s4_tree_sha256"], r_available=injected)  # fmt: skip
    control.update({"layer_a_original_total": pristine["original_total"], "layer_a_original_outside_baseline": pristine["outside"],
                    "original_finding_keys": a_control, "r_items": len(r1), "r_verified": sum(1 for v in r1.values() if v in VERIFIED),
                    "r_items_unstable_between_two_runs": unstable, "binary_injection": {"harness_binary_env": env_name, "works": injected, "detail": why}})  # fmt: skip
    doc["control"] = control
    (ev_dir / "control.json").write_text(json.dumps(control, indent=2, default=str) + "\n")
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
    cbin_sha = P1.sha(cbin)
    for i in IDS:
        if i not in only:
            continue
        p, row = plans[i], rows[i]
        rec: dict[str, Any] = {"id": f"HM{i}", "patch": patch_name(i), "patch_sha256": doc["patch_sha256"][patch_name(i)], "mutants_md_line": row.line,
                               "operator_text": row.operator_text, "operator": p["operator"], "route": route_label(p["route"]),
                               "file_line": [row.file, row.file_line], "substitutions": row.substitutions}  # fmt: skip
        t0 = time.perf_counter()
        try:
            rec.update(judge_one(ctx, tgt, truth, md_line=row.line + "\n" + changed_lines(files[i]), num=i, plan=p, patch=files[i], cand=cand, fx=fx, a_control=a_control,
                                 r_control=r1, unstable=unstable, env_name=env_name, injected=injected, control_bin_sha=cbin_sha))  # fmt: skip
        except Exception as err:  # noqa: BLE001 -- a mutant whose code raised is a FAIL (round-2 Amendment 6 rule a), never NOT RUN
            rec.update(
                killed=False,
                status="crashed",
                killed_by=f"FAIL: {type(err).__name__}: {err}"[:400],
                traceback=traceback.format_exc()[-1500:],
            )
        rec["wall_s"] = round(time.perf_counter() - t0, 1)
        (ev_dir / f"hm{i:02d}.json").write_text(json.dumps(rec, indent=2, default=str) + "\n")
        print(f"HM{i}: killed={rec.get('killed')} {rec.get('killed_by', '')[:140]}", flush=True)
        mrows.append(rec)
    killed = {int(r["id"][2:]): r["killed"] for r in mrows}
    doc.update(rows=mrows, judged=True, gate=gate_report(killed) if len(mrows) == len(IDS) else {"partial": True, "killed": sum(1 for v in killed.values() if v is True), "of": len(mrows)})  # fmt: skip
    doc["wall_s"] = round(time.perf_counter() - t_start, 1)
    return doc


def judge_one(
    ctx: P3.Ctx,
    tgt: dict[str, Any],
    truth: Mapping[str, Any],
    *,
    md_line: str,
    num: int,
    plan: Mapping[str, Any],
    patch: Path,
    cand: Any,
    fx: Fx,
    a_control: Sequence[str],
    r_control: Mapping[str, str],
    unstable: Sequence[str],
    env_name: str | None,
    injected: bool,
    control_bin_sha: str,
) -> dict[str, Any]:
    """Apply, build and judge one mutant on a scratch copy of S4."""
    s4 = fx.module_dir
    copy = ctx.scratch / "mut" / f"hm{num:02d}" / s4.name
    copy_module(s4, copy)
    ap = apply_patch(copy, patch)
    if not ap["ok"]:
        return {
            "killed": False,
            "status": "patch_failed",
            "killed_by": "FAIL: the patch does not apply: " + ap["output"][-200:],
            "apply": ap,
        }
    binary = ctx.scratch / "mut" / f"hm{num:02d}" / "bin"
    ok, err = go_build(copy, binary)
    if not ok:
        return {
            "killed": False,
            "status": "build_failed",
            "killed_by": "FAIL: the mutant does not build: " + err[-300:],
            "apply": ap,
        }
    la = layer_a_row(cand, fx, copy)
    r_mut: dict[str, str] | None = None
    if injected:
        ok_inj, why = binary_injected(harness_module(tgt), env_name, binary, ctx.scratch)
        if ok_inj:
            r_mut = r_map(ctx, tgt, cand, env_name, binary)
    v = judge_mutant(num, plan["operator"]["key"], plan["route"], a_control=a_control, a_new=la["new"], r_control=r_control, r_mutant=r_mut,
                     unstable=unstable, truth=truth, mutants_md_text=md_line)  # fmt: skip
    flips = {i: [r_control.get(i), x] for i, x in (r_mut or {}).items() if r_control.get(i) != x}
    return {**v, "status": "judged", "apply": ap, "binary_differs_from_control": P1.sha(binary) != control_bin_sha,
            "layer_a": {"original_total": la["original_total"], "original_outside_baseline": la["outside"], "new_findings": [f"{x['code']} {x['key']}" for x in la["new"]]},
            "layer_r": {"ran": r_mut is not None, "flipped_items [control, mutant]": flips}}  # fmt: skip


# ---- the command line -------------------------------------------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Go parity round 3: the held-out mutants HM1-HM16 on a scratch copy of S4"
    )
    ap.add_argument("--s4", required=True, help="the held-out Go module directory (tests/spec/<x>)")
    ap.add_argument(
        "--patches", required=True, help="a directory with m01.diff .. m16.diff and MUTANTS.md"
    )
    ap.add_argument("--truth", required=True, help="truth.json of S4 (prereg section 4)")
    ap.add_argument(
        "--freeze",
        required=True,
        help="the freeze file; every entry is verified before the first metric",
    )
    ap.add_argument("--kind", required=True, choices=list(KINDS))
    ap.add_argument("--out", required=True)
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--base", required=True, help="an extracted `git archive 978b468`")
    ap.add_argument(
        "--only",
        default="",
        help="mutant numbers (1,5,9) of a shakedown; scored, rerun and verify cover all 16",
    )
    return ap


def now() -> str:
    return S3R._now()


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    this = Path(__file__).resolve()
    only = tuple(int(x) for x in args.only.split(",") if x.strip()) if args.only else IDS
    if args.only and args.kind != "shakedown":
        raise SystemExit(
            "--only restricts the mutants of a shakedown; a scored, rerun or verify run covers all 16"
        )
    if any(i not in IDS for i in only):
        raise SystemExit("--only takes numbers 1..16")
    s4, patches, truth_path = (
        Path(args.s4).resolve(),
        Path(args.patches).resolve(),
        Path(args.truth).resolve(),
    )
    out, scratch, freeze_path = (
        Path(args.out).resolve(),
        Path(args.scratch).resolve(),
        Path(args.freeze).resolve(),
    )
    lattice_file = S3R.lattice_file_inside(REPO)
    runner_sha = P1.sha(this)
    freeze_sha = P1.sha(freeze_path) if freeze_path.is_file() else "missing"
    why = ledger_gate(args.kind, runner_sha, freeze_sha)
    if why:
        raise SystemExit(f"the ledger refuses this run: {why}")
    run_id = uuid.uuid4().hex[:12]

    def entry(status: str, **extra: Any) -> dict[str, Any]:
        return {"ts": now(), "run_id": run_id, "runner": RUNNER_NAME, "runner_sha256": runner_sha, "freeze_sha256": freeze_sha, "arm": ARM,
                "kind": args.kind, "out": str(out), "lattice_file": lattice_file, "status": status, **extra}  # fmt: skip

    S3R.append_ledger(entry("started"), LEDGER)
    metric_started = False
    try:
        # the self-hash check first: nothing is measured before every entry has been re-hashed
        freeze = verify_freeze(freeze_path, this, required_paths(s4, patches, truth_path))
        freeze["lattice_file"] = lattice_file
        truth, rows, files = validate_inputs(s4, patches, truth_path)
        tgt = P3.make_target(s4.parent, s4.name, truth_path, "S4")
        tgt["truth_path"] = truth_path
        ctx = make_ctx({"base": args.base}, scratch, out)
        ctx.h_target = tgt
        extra = setup(ctx, tgt, patches)
        freeze["import_subprocess_lattice_file"] = S3R.subprocess_lattice_file(ctx)

        def control_done(c: Mapping[str, Any]) -> None:  # the control verdict is the first metric
            nonlocal metric_started
            metric_started = True

        doc = run_hmut(
            ctx, tgt, patches, truth, rows, files, out, only=only, on_control=control_done
        )
    except BaseException as err:  # noqa: BLE001 -- recorded in the ledger, then re-raised
        S3R.append_ledger(
            entry(
                "crash_after_metric" if metric_started else "crash_before_metric",
                error=repr(err)[:400],
            ),
            LEDGER,
        )
        if isinstance(err, S3R.FreezeError):
            print(err, file=sys.stderr)
            out.mkdir(parents=True, exist_ok=True)
            (out / "hmut3.json").write_text(
                json.dumps(
                    {"runner": RUNNER_NAME, "aborted": str(err), "kind": args.kind, "rows": []},
                    indent=2,
                )
                + "\n"
            )
            return 2
        raise
    changed = finish_snapshots(ctx, extra, tgt, patches)
    shim_calls = [ln for ln in ctx.shim_log.read_text().splitlines() if ln.strip()]
    doc.update(runner=RUNNER_NAME, runner_sha256=runner_sha, kind=args.kind, run_id=run_id, base_ref=BASE_REF, lattice_file=lattice_file, freeze=freeze,
               only=list(only), timings=ctx.timings, python=sys.version.split()[0], fixtures_changed=changed, shim_calls=shim_calls)  # fmt: skip
    out.mkdir(parents=True, exist_ok=True)
    (out / "hmut3.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")
    imp = ctx.scratch / "imp" / f"h-{tgt['label']}"
    for name in (
        "report.json",
        "candidate_spec.py",
        "profile.py",
        "proposals.json",
        "go_policy.json",
    ):
        if (imp / name).exists():
            shutil.copyfile(imp / name, out / f"hmut_import_{name}")
    control_ok = bool(doc["control"].get("passes"))
    S3R.append_ledger(
        entry(
            "completed",
            control_passed=control_ok,
            s4_tree_sha256=doc["s4_tree_sha256"],
            killed=doc.get("gate", {}).get("killed"),
        ),
        LEDGER,
    )
    summarise(doc)
    print(f"fixtures changed: {changed}; docker/claude shim calls: {shim_calls}")
    print(f"ledger sha256: {P1.sha(LEDGER)}")
    return 0 if control_ok else 1


def summarise(doc: Mapping[str, Any]) -> None:
    c = doc["control"]
    print(f"control passes: {c.get('passes')}")
    g = doc.get("gate", {})
    if "classes" in g:
        print(
            f"killed {g['killed']}/16 (gate >= {g['need']}, predicted {g['predicted']}): {g['verdict']}; survivors {g['survivors']}; not run {g['not_run']}"
        )
        for name, cl in g["classes"].items():
            print(
                f"  {name}: {cl['killed']}/{cl['of']} (need {cl['need']}): {'pass' if cl['pass'] else 'FAIL'}"
            )
    else:
        print(f"gate: {g}")


if __name__ == "__main__":
    raise SystemExit(main())
