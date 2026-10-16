"""Probes of the Go (Gin + GORM) importer front end (docs/results/2026-10-15-go-frontend-prereg.md).

The ground truth below (GT_* and ``parse_models``) is independent of the extractor: the
registrations are the ones the pre-registration lists (read off the Streak source with
grep before any code), the tables come from a regex parse of the GORM models by D3's
rules, the error / field cells from Streak's own spec. Nothing here reads the extractor's
output to decide what is expected.

Run (see the pre-registration, section 6)::

    "$DRV" dogfood/importer_go_probes.py --streak-ref a0679e9 --streak-ref2 1d00ede \\
        --runs 10 --scratch "$SCRATCH/p" --out "$SCRATCH/p-data"

The held-out stage (``--held-out``) takes the app directories and the ground-truth files as
arguments and is run by the orchestrator, never by the builder.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parents[1]
STREAK = Path("/Users/vivekgade/Documents/streak")

# ---- ground truth: registrations (pre-registration, section 3) ---------------------------

# (method, path with every parameter as {}); the 26 registrations of a0679e9
GT_PUBLIC = [
    ("POST", "/v1/auth/register"),
    ("POST", "/v1/auth/login"),
    ("POST", "/v1/auth/refresh"),
    ("GET", "/health"),
    ("PUT", "/test/clock"),
]
GT_MEMBER = [
    ("POST", "/v1/auth/logout"),
    ("GET", "/v1/me"),
    ("PUT", "/v1/me"),
    ("DELETE", "/v1/me"),
    ("POST", "/v1/devices"),
    ("DELETE", "/v1/devices/{}"),
    ("POST", "/v1/habits"),
    ("GET", "/v1/habits"),
    ("GET", "/v1/habits/{}"),
    ("PATCH", "/v1/habits/{}"),
    ("DELETE", "/v1/habits/{}"),
    ("POST", "/v1/habits/{}/completions"),
    ("GET", "/v1/habits/{}/completions"),
    ("DELETE", "/v1/habits/{}/completions/{}"),
    ("POST", "/v1/completions/batch"),
    ("GET", "/v1/habits/{}/progress"),
    ("GET", "/v1/habits/{}/history"),
    ("GET", "/v1/home"),
    ("GET", "/v1/sync"),
    ("GET", "/v1/nudges"),
    ("POST", "/v1/nudges/{}/dismiss"),
]
GT_S = GT_PUBLIC + GT_MEMBER
# 1d00ede registers 20: the six the plan's state lacks are home, sync, nudges, dismiss
# and the two devices routes
GT_S4_ABSENT = [
    ("GET", "/v1/home"),
    ("GET", "/v1/sync"),
    ("GET", "/v1/nudges"),
    ("POST", "/v1/nudges/{}/dismiss"),
    ("POST", "/v1/devices"),
    ("DELETE", "/v1/devices/{}"),
]
GT_S4 = [r for r in GT_S if r not in GT_S4_ABSENT]

# the handler of each registration: (package directory, function regex); /health and
# /test/clock are literals in router.go
GT_HANDLERS: dict[tuple[str, str], tuple[str, str]] = {
    ("POST", "/v1/auth/register"): ("internal/auth", r"^func \(h handler\) register\("),
    ("POST", "/v1/auth/login"): ("internal/auth", r"^func \(h handler\) login\("),
    ("POST", "/v1/auth/refresh"): ("internal/auth", r"^func \(h handler\) refresh\("),
    ("POST", "/v1/auth/logout"): ("internal/auth", r"^func \(h handler\) logout\("),
    ("GET", "/v1/me"): ("internal/account", r"^func \(h handler\) get\("),
    ("PUT", "/v1/me"): ("internal/account", r"^func \(h handler\) put\("),
    ("DELETE", "/v1/me"): ("internal/account", r"^func \(h handler\) remove\("),
    ("POST", "/v1/devices"): ("internal/account", r"^func \(h handler\) postDevice\("),
    ("DELETE", "/v1/devices/{}"): ("internal/account", r"^func \(h handler\) deleteDevice\("),
    ("POST", "/v1/habits"): ("internal/habits", r"^func \(h handler\) create\("),
    ("GET", "/v1/habits"): ("internal/habits", r"^func \(h handler\) list\("),
    ("GET", "/v1/habits/{}"): ("internal/habits", r"^func \(h handler\) get\("),
    ("PATCH", "/v1/habits/{}"): ("internal/habits", r"^func \(h handler\) patch\("),
    ("DELETE", "/v1/habits/{}"): ("internal/habits", r"^func \(h handler\) remove\("),
    ("POST", "/v1/habits/{}/completions"): (
        "internal/completions",
        r"^func \(h handler\) create\(",
    ),
    ("GET", "/v1/habits/{}/completions"): ("internal/completions", r"^func \(h handler\) list\("),
    ("DELETE", "/v1/habits/{}/completions/{}"): (
        "internal/completions",
        r"^func \(h handler\) remove\(",
    ),
    ("POST", "/v1/completions/batch"): ("internal/completions", r"^func \(h handler\) batch\("),
    ("GET", "/v1/habits/{}/progress"): ("internal/progress", r"^func \(h handler\) progress\("),
    ("GET", "/v1/habits/{}/history"): ("internal/progress", r"^func \(h handler\) history\("),
    ("GET", "/v1/home"): ("internal/home", r"^func \(h handler\) home\("),
    ("GET", "/v1/sync"): ("internal/sync", r"^func \(h handler\) sync\("),
    ("GET", "/v1/nudges"): ("internal/nudges", r"^func \(h handler\) list\("),
    ("POST", "/v1/nudges/{}/dismiss"): ("internal/nudges", r"^func \(h handler\) dismiss\("),
    ("GET", "/health"): ("internal/httpapi", r'^\tr\.GET\("/health", func\(c \*gin\.Context\)'),
    ("PUT", "/test/clock"): ("internal/httpapi", r"^\treturn func\(c \*gin\.Context\) error \{"),
}


def gt_handler_line(root: Path, key: tuple[str, str]) -> tuple[str, int] | None:
    """(file, line) of the handler's ``func`` line, by grep."""
    pkg, pattern = GT_HANDLERS[key]
    rx = re.compile(pattern)
    for f in sorted((root / pkg).glob("*.go")):
        if f.name.endswith("_test.go"):
            continue
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if rx.search(line):
                return f.relative_to(root).as_posix(), i
    return None


# ---- ground truth: tables (an independent regex parse by D3's rules) ---------------------

_STRUCT = re.compile(r"^type (\w+) struct \{\n(.*?)^\}", re.M | re.S)
_FIELD = re.compile(r"^\s*(\w+)\s+(\*?[\w.\[\]]+)\s*(?:`([^`]*)`)?\s*(?://.*)?$")
_TABLENAME = re.compile(
    r"^func \((?:\w+ )?(\w+)\) TableName\(\) string\s*\{\s*return\s+\"([^\"]+)\""
)
_CHAR = re.compile(r"(?i)(?:var)?char\s*\(\s*(\d+)\s*\)")


def _snake(name: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    return s.lower()


def _plural(s: str) -> str:
    if s.endswith(("s", "x", "z", "ch", "sh")):
        return s + "es"
    if s.endswith("y") and len(s) > 1 and s[-2] not in "aeiou":
        return s[:-1] + "ies"
    return s + "s"


def _tag_parts(tag: str) -> dict[str, str]:
    m = re.search(r'gorm:"([^"]*)"', tag or "")
    out: dict[str, str] = {}
    if not m:
        return out
    for part in m.group(1).split(";"):
        part = part.strip()
        if not part:
            continue
        k, _, v = part.partition(":")
        out[k.strip().lower()] = v.strip()
    return out


def parse_models(files: list[Path]) -> dict[str, dict[str, Any]]:
    """{table name: {"struct", "columns": {col: facts}, "unique_together": [[cols]]}}.

    facts = (nullable, primary_key, unique, index, default, max_length, type), by D3:
    nullable = no `not null` and not a primary key; primary_key = `primaryKey`, else a
    field ID when none is tagged; unique = `unique` or a `uniqueIndex` no other field
    shares; index = any index / uniqueIndex; max_length = size:N else (var)char(N).
    """
    names: dict[str, str] = {}
    structs: dict[str, str] = {}
    for f in files:
        text = f.read_text()
        for m in _STRUCT.finditer(text):
            structs[m.group(1)] = m.group(2)
        for line in text.splitlines():
            tn = _TABLENAME.match(line)
            if tn:
                names[tn.group(1)] = tn.group(2)
    # one-line TableName methods are matched above; the others are not in this app
    out: dict[str, dict[str, Any]] = {}
    for struct, body in structs.items():
        if struct not in names and struct not in ("SchemaMigration",):
            continue
        table = names.get(struct) or _plural(_snake(struct))
        fields = []
        for line in body.splitlines():
            line = line.rstrip()
            if not line.strip() or line.strip().startswith("//"):
                continue
            m = _FIELD.match(line)
            if not m:
                continue
            fields.append((m.group(1), m.group(2), _tag_parts(m.group(3) or "")))
        any_pk = any("primarykey" in t for _, _, t in fields)
        shared: dict[str, list[str]] = {}
        for _, _, t in fields:
            if "uniqueindex" in t and t["uniqueindex"]:
                shared.setdefault(t["uniqueindex"].split(",")[0], []).append(t.get("column", ""))
        cols: dict[str, tuple] = {}
        for name, gotype, t in fields:
            col = t.get("column") or _snake(name)
            pk = "primarykey" in t or (not any_pk and name == "ID")
            nullable = ("not null" not in t) and not pk
            uidx = t.get("uniqueindex")
            unique = ("unique" in t) or (
                "uniqueindex" in t and (not uidx or len(shared.get(uidx.split(",")[0], [])) < 2)
            )
            index = "index" in t or "uniqueindex" in t
            default = t.get("default")
            ml = None
            if "size" in t:
                ml = int(t["size"])
            else:
                m2 = _CHAR.search(t.get("type", ""))
                if m2:
                    ml = int(m2.group(1))
            base = gotype.lstrip("*")
            if base == "string":
                typ: str | None = "Str"
            elif base.startswith(("int", "uint")):
                typ = "Int"
            elif base.startswith("float"):
                typ = "Float"
            elif base == "bool":
                typ = "Bool"
            elif base == "time.Time":
                typ = (
                    "Date"
                    if t.get("type", "").lower().startswith("date")
                    and not t.get("type", "").lower().startswith("datetime")
                    else "DateTime"
                )
            else:
                typ = None
            cols[col] = (nullable, pk, unique, index, default, ml, typ)
        together = []
        for members in shared.values():
            if len(members) >= 2:
                together.append(sorted(members))
        out[table] = {"struct": struct, "columns": cols, "unique_together": sorted(together)}
    return out


def gt_tables(root: Path) -> dict[str, dict[str, Any]]:
    files = [
        root / "internal/db/models.go",
        root / "internal/db/db.go",
        root / "internal/clock/clock.go",
    ]
    return parse_models(files)


# The pre-registration's count table (m): cols / nullable / pk / unique / index / default /
# max_length / unique_together per struct.
GT_COUNTS = {
    "User": (6, 0, 1, 1, 1, 1, 3, 0),
    "NotificationPrefs": (6, 1, 1, 0, 0, 1, 3, 0),
    "Device": (9, 0, 1, 0, 2, 0, 5, 0),
    "RefreshToken": (8, 2, 1, 1, 2, 0, 2, 0),
    "Habit": (14, 3, 1, 0, 1, 2, 3, 0),
    "HabitTarget": (4, 0, 1, 0, 2, 0, 1, 1),
    "Completion": (8, 2, 1, 0, 1, 0, 2, 0),
    "Nudge": (12, 3, 1, 0, 2, 0, 5, 1),
}


# ---- ground truth: the spec's cells -------------------------------------------------------

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}$")


def norm_path(p: str) -> str:
    p = p.split("?")[0]
    segs = ["{}" if (_UUID.match(s) or s.isdigit()) else s for s in p.strip("/").split("/")]
    return "/" + "/".join(segs)


def spec_cells(root: Path) -> dict[str, Any]:
    """The spec's endpoints, error cells (endpoint, status >= 400), (endpoint, status, code)
    triples and 422 (endpoint, field) cells, read from the app's spec.py."""
    sys.path.insert(0, str(root))
    sys.modules.pop("spec", None)
    try:
        import spec as streak_spec  # noqa: PLC0415

        from lattice.spec import Step  # noqa: PLC0415

        steps = [st for sc in streak_spec.SPEC.scenarios for st in sc.steps if isinstance(st, Step)]
    finally:
        sys.path.remove(str(root))
        sys.modules.pop("spec", None)
    endpoints: set[tuple[str, str]] = set()
    cells: set[tuple[tuple[str, str], int]] = set()
    triples: set[tuple[str, str, int, str]] = set()
    fields422: set[tuple[tuple[str, str], str]] = set()
    anon_public: dict[tuple[str, str], bool] = {}
    for st in steps:
        key = (st.method, norm_path(st.path))
        if st.expect_status < 300:
            endpoints.add(key)
        if st.user is None and st.expect_status < 300:
            anon_public[key] = True
        if st.expect_status >= 400:
            cells.add((key, st.expect_status))
            body = st.expect_body
            err = body.get("error") if isinstance(body, dict) else None
            if isinstance(err, dict) and err.get("code"):
                triples.add((key[0], key[1], st.expect_status, err["code"]))
                if st.expect_status == 422 and err.get("field"):
                    fields422.add((key, err["field"]))
    return {
        "endpoints": sorted(endpoints),
        "cells": sorted(cells),
        "triples": sorted(triples),
        "fields422": sorted(fields422),
        "anon_public": anon_public,
    }


# ---- running the extractor ----------------------------------------------------------------


def tpl(path: str) -> str:
    """A path with every parameter ``{}`` (the scoring identity)."""
    return re.sub(r"\{[^}]*\}", "{}", path)


def archive(repo: Path, ref: str, dest: Path) -> None:
    """``git archive <ref>`` of ``repo`` into ``dest`` (never writes into ``repo``)."""
    if dest.exists():
        subprocess.run(["rm", "-rf", str(dest)], check=True)
    dest.mkdir(parents=True)
    tar = subprocess.run(
        ["git", "-C", str(repo), "archive", ref], capture_output=True, check=True
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=tar, check=True)


def extract_bytes(binary: Path, root: Path) -> tuple[bytes, float]:
    """``gocheck extract`` on ``root``: stdout bytes and the wall seconds."""
    t0 = time.perf_counter()
    proc = subprocess.run(
        [str(binary), "extract"],
        input=json.dumps({"root": str(root)}).encode(),
        capture_output=True,
        timeout=120,
        check=False,
    )
    wall = time.perf_counter() - t0
    if proc.returncode != 0:
        raise RuntimeError(f"extract failed ({proc.returncode}): {proc.stderr.decode()[:500]}")
    return proc.stdout, wall


def tree_digest(root: Path) -> dict[str, str]:
    """{relative path: sha256} of every file under ``root`` (a listing and its hashes)."""
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() or p.is_symlink():
            rel = p.relative_to(root).as_posix()
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "symlink"
    return out


def key_of(r: dict[str, Any]) -> tuple[str, str]:
    return (r["method"], tpl(r["path_template"]))


def by_key(x: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {key_of(r): r for r in x["routes"]}


def chain_raises(x: dict[str, Any], route: dict[str, Any]) -> list[dict[str, Any]]:
    """The route's raise sites and those of its middleware chain."""
    mws = {m["func"]: m for m in x["middleware"]}
    out = list(route["raises"])
    for ref in route["middleware"]:
        out += mws.get(ref["func"], {}).get("raises", [])
    return out


# ---- the probes ---------------------------------------------------------------------------


def _result(pid: str, desc: str, predicted: Any, observed: Any, passed: bool | None, **ev: Any):
    return {
        "id": pid,
        "probe": desc,
        "ran": True,
        "predicted": predicted,
        "observed": observed,
        "pass": passed,
        "evidence": ev,
    }


def probe_pr1(x: dict[str, Any], gt: list[tuple[str, str]], label: str = "PR1") -> dict[str, Any]:
    keys = [key_of(r) for r in x["routes"]]
    got, want = set(keys), set(gt)
    exact = sum(1 for r in x["routes"] if key_of(r) in want and r["path_exact"])
    obs = {
        "extracted": len(keys),
        "recall": f"{len(got & want)}/{len(want)}",
        "precision": f"{len(got & want)}/{len(got)}",
        "exact": exact,
        "duplicates": len(keys) - len(got),
    }
    ok = got == want and exact == len(want) and len(keys) == len(got)
    return _result(
        label,
        "routes: method + full path vs the registrations",
        {"recall": "26/26", "precision": "26/26", "exact": 26, "extra": 0},
        obs,
        ok,
        missing=sorted(want - got),
        extra=sorted(got - want),
    )


def probe_pr2(x: dict[str, Any], root: Path) -> dict[str, Any]:
    routes = by_key(x)
    distinct = {
        (r["handler"]["file"], r["handler"]["line"]) for r in routes.values() if r["handler"]
    }
    line_ok, wrap_ok, bad = 0, 0, []
    for key in GT_S:
        r = routes.get(key)
        want = gt_handler_line(root, key)
        if r is None or r["handler"] is None:
            bad.append((key, "no handler"))
            continue
        h = r["handler"]
        if want == (h["file"], h["line"]):
            line_ok += 1
        else:
            bad.append((key, {"got": [h["file"], h["line"]], "want": list(want or [])}))
        wrappers = [] if key == ("GET", "/health") else ["httpapi.Handle"]
        if h["wrappers"] == wrappers:
            wrap_ok += 1
        else:
            bad.append((key, {"wrappers": h["wrappers"], "want": wrappers}))
    obs = {
        "distinct_handlers": len(distinct),
        "file_line_exact": f"{line_ok}/26",
        "wrappers_exact": f"{wrap_ok}/26",
    }
    return _result(
        "PR2",
        "handlers: distinct, file:line, wrappers",
        {"distinct": 26, "file_line": "26/26", "wrappers": "25/25 (+ /health none)"},
        obs,
        len(distinct) == 26 and line_ok == 26 and wrap_ok == 26,
        mismatches=bad,
    )


def spec_only(x: dict[str, Any], endpoints: list[tuple[str, str]]) -> list[tuple[str, str]]:
    got = {key_of(r) for r in x["routes"]}
    return sorted(set(endpoints) - got)


def probe_pr3(x4: dict[str, Any], x: dict[str, Any], c4: dict[str, Any], c: dict[str, Any]):
    p1 = probe_pr1(x4, GT_S4, "PR3")
    so4, so = spec_only(x4, c4["endpoints"]), spec_only(x, c["endpoints"])
    ok = bool(p1["pass"]) and sorted(GT_S4_ABSENT) == so4 and so == []
    return _result(
        "PR3",
        "S4 vs its 20 registrations; spec-only on S4 and S",
        {"s4": "20/20", "spec_only_s4": sorted(GT_S4_ABSENT), "spec_only_s": []},
        {"s4_routes": p1["observed"], "spec_only_s4": so4, "spec_only_s": so},
        ok,
        s4_missing=p1["evidence"]["missing"],
        s4_extra=p1["evidence"]["extra"],
    )


def probe_pa(x: dict[str, Any], cells: dict[str, Any]) -> dict[str, Any]:
    routes = by_key(x)
    code_ok, spec_ok, bad = 0, 0, []
    for key in GT_S:
        r = routes.get(key)
        want = "Public" if key in GT_PUBLIC else "Customer"
        if r is not None and r["auth"] == want:
            code_ok += 1
        else:
            bad.append((key, r["auth"] if r else None, want))
        anon = cells["anon_public"].get(key)
        spec_want = "Public" if anon else "Customer"
        if r is not None and r["auth"] == spec_want:
            spec_ok += 1
    n = {"Customer": 0, "Public": 0}
    for r in x["routes"]:
        n[r["auth"]] = n.get(r["auth"], 0) + 1
    return _result(
        "PA",
        "auth vs the code (Member iff on r.Member) and the spec's anon column",
        {"counts": {"Customer": 21, "Public": 5}, "code": "26/26", "spec": "26/26"},
        {"counts": n, "code": f"{code_ok}/26", "spec": f"{spec_ok}/26"},
        code_ok == 26 and spec_ok == 26,
        mismatches=bad,
    )


def probe_pa2(x: dict[str, Any]) -> dict[str, Any]:
    a = x["auth"] or {}
    want = {
        "login_path": "/v1/auth/login",
        "users_table": "User",
        "scheme": "bearer",
        "password_column": "hashed_password",
        "username_field": "email",
        "password_field": "password",
    }
    got = {k: a.get(k) for k in want}
    ok = sum(1 for k in want if got[k] == want[k])
    return _result(
        "PA2",
        "AuthModel: login path, users table, scheme, password column, fields",
        want,
        got,
        ok == 6,
        matched=f"{ok}/6",
    )


def column_facts(col: dict[str, Any]) -> tuple:
    return (
        col["nullable"],
        col["primary_key"],
        col["unique"],
        col["index"],
        col["default"],
        col["max_length"],
        col["type"],
    )


def probe_pt(x: dict[str, Any], truth: dict[str, dict[str, Any]]) -> dict[str, Any]:
    got = {t["table"]: t for t in x["tables"]}
    tables_ok = set(got) == set(truth)
    cols_n = cols_ok = facts_n = facts_ok = 0
    ut_n = ut_ok = 0
    diffs: list[Any] = []
    extra_cols: list[Any] = []
    model_tables = [t for t in truth if truth[t]["struct"] in GT_COUNTS]
    for tname in sorted(truth):
        gt = truth[tname]
        t = got.get(tname)
        if t is None:
            continue
        gcols = {c["name"]: c for c in t["columns"]}
        in_model = tname in model_tables
        for col, want in gt["columns"].items():
            c = gcols.get(col)
            if in_model:
                cols_n += 1
            if c is None:
                diffs.append((tname, col, "missing column"))
                continue
            if in_model:
                cols_ok += 1
            have = column_facts(c)
            for i, (a, b) in enumerate(zip(have, want, strict=True)):
                if in_model:
                    facts_n += 1
                if a == b:
                    if in_model:
                        facts_ok += 1
                else:
                    diffs.append((tname, col, i, a, b))
        extra_cols += [(tname, c) for c in gcols if c not in gt["columns"]]
        if in_model:
            ut_n += len(gt["unique_together"])
            ut_ok += sum(
                1
                for u in gt["unique_together"]
                if sorted(u) in [sorted(v) for v in t["unique_together"]]
            )
    counts = {}
    for t in x["tables"]:
        cols = t["columns"]
        counts[t["struct"]] = (
            len(cols),
            sum(c["nullable"] for c in cols),
            sum(c["primary_key"] for c in cols),
            sum(c["unique"] for c in cols),
            sum(c["index"] for c in cols),
            sum(c["default"] is not None for c in cols),
            sum(c["max_length"] is not None for c in cols),
            len(t["unique_together"]),
        )
    counts_ok = all(counts.get(k) == v for k, v in GT_COUNTS.items())
    obs = {
        "tables": len(got),
        "tables_equal": tables_ok,
        "columns": f"{cols_ok}/{cols_n}",
        "facts": f"{facts_ok}/{facts_n}",
        "unique_together": f"{ut_ok}/{ut_n}",
        "count_table_exact": counts_ok,
    }
    ok = (
        tables_ok
        and cols_ok == cols_n == 67
        and facts_ok == facts_n == 469
        and ut_ok == ut_n == 2
        and counts_ok
    )
    return _result(
        "PT",
        "tables vs the independent regex parse (D3's rules)",
        {
            "tables": 10,
            "columns": "67/67",
            "facts": "469/469",
            "unique_together": "2/2",
            "count_table_exact": True,
        },
        obs,
        ok,
        diffs=diffs,
        extra_columns=extra_cols,
        counts={k: list(v) for k, v in counts.items()},
    )


def probe_pe(x: dict[str, Any], cells: dict[str, Any]) -> list[dict[str, Any]]:
    routes = by_key(x)
    found, bad = 0, []
    for key, status in cells["cells"]:
        r = routes.get(tuple(key))
        have = set()
        if r is not None:
            have = {s["status"] for s in r["raises"]} | {g["status"] for g in r["guards"]}
        if status in have:
            found += 1
        else:
            bad.append((list(key), status))
    pe = _result(
        "PE",
        "spec error cells (endpoint, status >= 400) in raises + guards",
        {"cells": 54, "endpoints": 24, "found": "54/54"},
        {"cells": len(cells["cells"]), "found": f"{found}/{len(cells['cells'])}"},
        found == len(cells["cells"]) == 54,
        missing=bad,
    )
    # PE2: codes and extras (report)
    found_t, miss_t = 0, []
    have_all: set[tuple[Any, ...]] = set()
    for r in x["routes"]:
        for s in chain_raises(x, r):
            if s["code"] and s["status"] and s["status"] >= 400:
                have_all.add((r["method"], tpl(r["path_template"]), s["status"], s["code"]))
        for g in r["guards"]:
            if g["detail"] and g["status"]:
                have_all.add((r["method"], tpl(r["path_template"]), g["status"], g["detail"]))
    spec_t = {tuple(t) for t in cells["triples"]}
    for t in sorted(spec_t):
        if t in have_all:
            found_t += 1
        else:
            miss_t.append(t)
    extras = sorted(have_all - spec_t)
    member429 = sum(1 for t in extras if t[2] == 429)
    # the same count under narrower readings of "code statuses with no spec cell"
    handler_only: set[tuple[Any, ...]] = set()
    handler_guards: set[tuple[Any, ...]] = set()
    for r in x["routes"]:
        for s in r["raises"]:
            if s["code"] and s["status"] and s["status"] >= 400:
                handler_only.add((r["method"], tpl(r["path_template"]), s["status"], s["code"]))
        handler_guards |= handler_only
        for g in r["guards"]:
            if g["detail"] and g["status"]:
                handler_guards.add((r["method"], tpl(r["path_template"]), g["status"], g["detail"]))
    pairs = {(t[2], t[3]) for t in have_all}
    spec_pairs = {(t[2], t[3]) for t in spec_t}
    pe2 = _result(
        "PE2",
        "codes and extras (report)",
        {"spec_triples_found": "58-65 of 65", "extras": "25-45 (19 are Member 429s)"},
        {
            "spec_triples_found": f"{found_t}/{len(spec_t)}",
            "extras": len(extras),
            "extra_429": member429,
            "extras_handler_raises_only": len(handler_only - spec_t),
            "extras_handler_raises_and_guards": len(handler_guards - spec_t),
            "distinct_status_code_pairs_not_in_spec": sorted(pairs - spec_pairs),
        },
        None,
        missing=miss_t,
        extras=[list(e) for e in extras],
    )
    return [pe, pe2]


def probe_pf(x: dict[str, Any], cells: dict[str, Any]) -> list[dict[str, Any]]:
    routes = by_key(x)
    found, bad = 0, []
    for key, field in cells["fields422"]:
        r = routes.get(tuple(key))
        names = {p["name"] for p in r["params"]} if r else set()
        if field in names:
            found += 1
        else:
            bad.append((list(key), field))
    pf = _result(
        "PF",
        "spec 422 (endpoint, field) cells found as RouteParam",
        {"cells": 32, "found": "31-32"},
        {"cells": len(cells["fields422"]), "found": f"{found}/{len(cells['fields422'])}"},
        found >= 30,
        missing=bad,
    )

    # PF2 bounds
    def param(key, name, source=None):
        r = routes.get(key)
        for p in r["params"] if r else []:
            if p["name"] == name and (source is None or p["source"] == source):
                return p
        return None

    facts = {}
    p = param(("POST", "/v1/habits"), "name")
    facts["name.min_length=1"] = bool(p and p["min_length"] == 1)
    facts["name.max_length=100"] = bool(p and p["max_length"] == 100)
    p = param(("POST", "/v1/habits"), "description")
    facts["description.max_length=500"] = bool(p and p["max_length"] == 500)
    p = param(("POST", "/v1/habits"), "target_count")
    facts["target_count.ge=1"] = bool(p and p["ge"] == 1)
    facts["target_count.le unread at habits/routes.go:55"] = any(
        u["file"] == "internal/habits/routes.go"
        and u["line"] == 55
        and u["what"] == "bound"
        and "target_count" in u["reason"]
        for u in x["unread"]
    )
    p = param(("GET", "/v1/sync"), "limit")
    facts["sync limit.ge=1"] = bool(p and p["ge"] == 1)
    facts["sync limit.le=200"] = bool(p and p["le"] == 200)
    n = sum(facts.values())
    pf2 = _result(
        "PF2",
        "bounds: name 1..100, description 500, target_count ge 1 / le unread, sync limit 1..200",
        "7/7",
        f"{n}/7",
        n == 7,
        facts=facts,
    )
    return [pf, pf2]


GT_HABIT_COMPARE = [
    ("GET", "/v1/habits/{}"),
    ("PATCH", "/v1/habits/{}"),
    ("DELETE", "/v1/habits/{}"),
    ("POST", "/v1/habits/{}/completions"),
    ("GET", "/v1/habits/{}/completions"),
    ("DELETE", "/v1/habits/{}/completions/{}"),
    ("POST", "/v1/completions/batch"),
    ("GET", "/v1/habits/{}/progress"),
    ("GET", "/v1/habits/{}/history"),
    ("GET", "/v1/habits"),
    ("GET", "/v1/home"),
    ("GET", "/v1/sync"),
    ("GET", "/v1/nudges"),
    ("POST", "/v1/nudges/{}/dismiss"),
]


def probe_po(x: dict[str, Any]) -> dict[str, Any]:
    routes = by_key(x)

    def uses(key, table, col, how):
        r = routes.get(key)
        return bool(
            r
            and any(
                o["table"] == table and o["column"] == col and o["how"] == how
                for o in r["owner_uses"]
            )
        )

    cmp_ok = [k for k in GT_HABIT_COMPARE if uses(k, "habits", "user_id", "compare")]
    extra_cmp = sorted(
        k
        for k, r in routes.items()
        if k not in GT_HABIT_COMPARE
        and any(
            o["table"] == "habits" and o["column"] == "user_id" and o["how"] == "compare"
            for o in r["owner_uses"]
        )
    )
    assign_ok = [
        uses(("POST", "/v1/habits"), "habits", "user_id", "assign"),
        uses(("POST", "/v1/devices"), "devices", "user_id", "assign"),
    ]
    any_use = [k for k in GT_MEMBER if routes.get(k) and routes[k]["owner_uses"]]
    public_use = [k for k in GT_PUBLIC if routes.get(k) and routes[k]["owner_uses"]]
    obs = {
        "habits.user_id compare": f"{len(cmp_ok)}/14",
        "assign": f"{sum(assign_ok)}/2",
        "caller-id use per Member route": f"{len(any_use)}/21",
        "public with uses": len(public_use),
    }
    ok = len(cmp_ok) == 14 and all(assign_ok) and len(any_use) >= 20 and not public_use
    return _result(
        "PO",
        "owner evidence",
        {"compare": "14/14", "assign": "2/2", "member_use": ">= 20/21", "public": 0},
        obs,
        ok,
        compare_missing=[k for k in GT_HABIT_COMPARE if k not in cmp_ok],
        compare_extra=[list(k) for k in extra_cmp],
        member_without_use=[list(k) for k in GT_MEMBER if k not in any_use],
        public_with_use=[list(k) for k in public_use],
    )


def probe_pm(x: dict[str, Any]) -> dict[str, Any]:
    by_root: dict[str, set[str]] = {}
    for w in x["status_writes"]:
        if w["table"] == "nudges" and w["column"] == "delivery":
            by_root.setdefault(w["root"], set()).add(w["value"])
    union = set().union(*by_root.values()) if by_root else set()
    route_roots = [
        "POST /v1/habits/{id}/completions",
        "DELETE /v1/habits/{id}/completions/{cid}",
        "POST /v1/completions/batch",
    ]
    obs = {r: sorted(v) for r, v in sorted(by_root.items())}
    ok = (
        union == {"pending", "sent", "failed", "suppressed"}
        and all(by_root.get(r) == {"pending", "suppressed"} for r in route_roots)
        and by_root.get("nudges.RunDailyPass") == {"failed", "sent", "suppressed"}
    )
    return _result(
        "PM",
        "Nudge.delivery values by root",
        {
            "routes (3)": ["pending", "suppressed"],
            "nudges.RunDailyPass": ["failed", "sent", "suppressed"],
            "union": ["failed", "pending", "sent", "suppressed"],
        },
        obs,
        ok,
        union=sorted(union),
    )


def read_lines(root: Path, rel: str) -> list[str] | None:
    p = root / rel
    return p.read_text().splitlines() if p.is_file() else None


def probe_pu(x: dict[str, Any], root: Path) -> dict[str, Any]:
    structural = [
        r
        for r in x["routes"]
        if not r["path_exact"] or r["handler"] is None or r["auth"] == "unclear"
    ]
    detail = x["unread"]
    bad = []
    for u in detail:
        lines = read_lines(root, u["file"])
        if lines is None or not (1 <= u["line"] <= len(lines)):
            bad.append((u, "file or line missing"))
            continue
        text = " ".join(lines[u["line"] - 1].split())
        symbols = re.findall(r"`([^`]+)`", u["reason"])
        if symbols and not any(" ".join(s.split()) in text for s in symbols):
            bad.append((u, f"line holds none of {symbols}: {text[:120]}"))
    return _result(
        "PU",
        "unread: structural per route, detail items located",
        {"structural": "0/26", "detail": "3-25", "located": "all"},
        {
            "structural": f"{len(structural)}/{len(x['routes'])}",
            "detail": len(detail),
            "located": f"{len(detail) - len(bad)}/{len(detail)}",
        },
        not structural and len(detail) - len(bad) == len(detail),
        items=detail,
        unlocated=bad,
    )


def probe_pd(binary: Path, root: Path, root2: Path, runs: int, out: Path) -> dict[str, Any]:
    from lattice.importer.frontends.go_gin import model_from_extract

    blobs, models = [], []
    for _ in range(runs):
        b, _ = extract_bytes(binary, root)
        blobs.append(b)
        models.append(model_from_extract(json.loads(b)).to_json())
    b2, _ = extract_bytes(binary, root2)
    blobs.append(b2)
    models.append(model_from_extract(json.loads(b2)).to_json())
    same_sidecar = sum(1 for b in blobs if b == blobs[0])
    same_model = sum(1 for m in models if m == models[0])
    leaks = [
        s
        for s in ("/Users", "/private", str(root), str(root2))
        if s.encode() in blobs[0] or s.encode() in b2
    ]
    (out / "streak-sidecar.json").write_bytes(blobs[0])
    (out / "streak-model.json").write_text(models[0])
    n = runs + 1
    return _result(
        "PD",
        f"{runs} runs + 1 from a second clone path: byte-identical",
        {"sidecar": f"{n}/{n}", "model": f"{n}/{n}", "leaks": []},
        {"sidecar": f"{same_sidecar}/{n}", "model": f"{same_model}/{n}", "leaks": leaks},
        same_sidecar == n and same_model == n and not leaks,
        sha256=hashlib.sha256(blobs[0]).hexdigest(),
    )


def probe_ps(model: Any) -> dict[str, Any]:
    from lattice.importer.emit_spec import build_spec
    from lattice.importer.lift import load_profile
    from lattice.importer.profile_gen import generate_profile

    gen = generate_profile(model)
    module = load_profile(gen, "lattice_gox_profile")
    profile = module.profile()
    spec = build_spec(model, profile, users={})
    unclear_owner = {t: v for t, v in gen.ownership.items() if v == "unclear"}
    return _result(
        "PS",
        "generate_profile + build_spec on S",
        {"runs": True, "contracts": 26, "user_id owners": "unclear"},
        {
            "runs": True,
            "contracts": len(spec.contracts),
            "ownership": dict(gen.ownership),
            "unclear": unclear_owner,
            "unclear_notes": [f"{u.topic}:{u.subject}" for u in gen.unclear][:30],
        },
        len(spec.contracts) == 26,
        ownership_reasons=dict(gen.ownership_reasons),
    )


def probe_pt2(binary: Path, root: Path, n: int = 10) -> dict[str, Any]:
    walls = [extract_bytes(binary, root)[1] for _ in range(n)]
    med = statistics.median(walls)
    return _result(
        "PT2-extract",
        f"goextract wall on S (median of {n})",
        "0.05-0.5 s; <= 5 s",
        {"median_s": round(med, 3), "min_s": round(min(walls), 3), "max_s": round(max(walls), 3)},
        med <= 5.0,
    )


def probe_pt2_cold() -> dict[str, Any]:
    """Cold ``build_gocheck``: a fresh checker cache (warm Go build cache) and a fresh Go
    build cache as well (stdlib compiled again)."""
    import tempfile

    code = (
        "import sys, time; sys.dont_write_bytecode=True; t=time.perf_counter(); "
        "from lattice.gocheck import build_gocheck; build_gocheck(); "
        "print(time.perf_counter()-t)"
    )
    seconds: dict[str, float] = {}
    for label, fresh_gocache in (
        ("fresh_checker_cache", False),
        ("fresh_checker_and_go_cache", True),
    ):
        cache = tempfile.mkdtemp(prefix="gox-cache-", dir=os.environ.get("TMPDIR"))
        gocache = tempfile.mkdtemp(prefix="gox-gocache-", dir=os.environ.get("TMPDIR"))
        env = dict(os.environ, LATTICE_GOCHECK_CACHE=cache, PYTHONDONTWRITEBYTECODE="1")
        if fresh_gocache:
            env["GOCACHE"] = gocache
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
        subprocess.run(["rm", "-rf", cache, gocache])
        if proc.returncode != 0:
            return _result(
                "PT2-build", "cold build_gocheck", "3-15 s; <= 60 s", proc.stderr[-400:], False
            )
        seconds[label] = round(float(proc.stdout.strip().splitlines()[-1]), 1)
    return _result(
        "PT2-build", "cold build_gocheck", "3-15 s; <= 60 s", seconds, max(seconds.values()) <= 60.0
    )


def old_binary(scratch: Path) -> Path:
    """The gocheck binary of 4845ab5 (the baseline commit), built in scratch."""
    src = scratch / "old-gocheck"
    out = scratch / "old-gocheck-bin"
    if out.is_file():
        return out
    archive(REPO, "4845ab5", src)
    env = dict(os.environ, GOFLAGS="-mod=mod", GOPROXY="off", GOTOOLCHAIN="local", GOWORK="off")
    subprocess.run(
        ["go", "build", "-trimpath", "-buildvcs=false", "-o", str(out), "."],
        cwd=src / "lattice/gocheck/src",
        env=env,
        check=True,
        capture_output=True,
    )
    return out


def gocheck_run(binary: Path, root: Path, policy: dict[str, Any]) -> bytes:
    proc = subprocess.run(
        [str(binary)],
        input=json.dumps({"root": str(root), "policy": policy}).encode(),
        capture_output=True,
        check=False,
        timeout=120,
    )
    return proc.stdout + b"\n--exit " + str(proc.returncode).encode()


def probe_pb_diff() -> dict[str, Any]:
    """The change set against HEAD (the pre-registration commit) is the owned files only; the
    importer modules the pre-registration freezes, and the goldens, have an empty diff
    against 4845ab5."""

    def git(*a: str) -> list[str]:
        out = subprocess.run(
            ["git", "-C", str(REPO), *a], capture_output=True, text=True, check=True
        )
        return [x for x in out.stdout.split("\n") if x.strip()]

    changed = sorted(
        {x.split("\t")[-1] for x in git("diff", "HEAD", "--name-status")}
        | set(git("ls-files", "--others", "--exclude-standard"))
    )
    allowed = (
        "lattice/gocheck/src/goextract",
        "tests/gocheck/fixtures/goextract/",
        "docs/results/data/2026-10-15-go-frontend/",
    )
    exact = {
        "lattice/gocheck/src/main.go",
        "lattice/importer/frontends/go_gin.py",
        "lattice/importer/frontends/__init__.py",
        "tests/gocheck/test_goextract.py",
        "tests/importer/test_go_gin.py",
        "dogfood/importer_go_probes.py",
        "dogfood/importer_go_mutants.py",
        "docs/results/2026-10-15-go-frontend-results.md",
    }
    foreign = [c for c in changed if c not in exact and not c.startswith(allowed)]
    frozen = [
        "tests/importer/goldens",
        "lattice/importer/appmodel.py",
        "lattice/importer/emit_spec.py",
        "lattice/importer/profile_gen.py",
        "lattice/importer/rules.py",
        "lattice/importer/transport.py",
        "lattice/spec/process.py",
        "lattice/spec/dbserver.py",
    ]
    stat = git("diff", "4845ab5", "--stat", "--", *frozen)
    return _result(
        "PB-diff",
        "changes against 4845ab5: owned files only; frozen modules and goldens untouched",
        {"foreign": [], "frozen_diff": []},
        {"foreign": foreign, "frozen_diff": stat, "changed": changed},
        not foreign and not stat,
    )


def probe_pb(scratch: Path, new_bin: Path, streak_root: Path) -> dict[str, Any]:
    """Byte-compat: the diff of the gocheck sources, and the findings of the old and the new
    binary on every fixture (empty policy and the tests' policies) and on S."""
    old_bin = old_binary(scratch)
    stat = subprocess.run(
        ["git", "-C", str(REPO), "diff", "4845ab5", "--name-status", "--", "lattice/gocheck/src"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\n")
    untracked = subprocess.run(
        [
            "git",
            "-C",
            str(REPO),
            "ls-files",
            "--others",
            "--exclude-standard",
            "--",
            "lattice/gocheck/src",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    changed = sorted(line.split("\t")[-1] for line in stat if line.strip()) + sorted(untracked)
    owned = all(
        c == "lattice/gocheck/src/main.go" or Path(c).name.startswith("goextract") for c in changed
    )
    from lattice.gocheck import GoPolicy

    jobs: list[tuple[str, Path, dict[str, Any]]] = []
    fx = REPO / "tests/gocheck/fixtures"
    for d in sorted(p for p in fx.iterdir() if p.is_dir()):
        jobs.append((f"{d.name}:empty", d, GoPolicy().to_json()))
    sys.path.insert(0, str(REPO))
    try:
        import importlib

        rules = importlib.import_module("tests.gocheck.test_gocheck_rules")
        clean = importlib.import_module("tests.gocheck.test_gocheck_clean")
        tasks = importlib.import_module("tests.gocheck.tasks_spec")
    finally:
        sys.path.remove(str(REPO))
    named = {
        "private": rules.PRIVATE,
        "owner": rules.OWNER,
        "app": rules.APP,
        "runs": rules.RUNS,
        "clean": clean.POLICY,
        "clean2": clean.POLICY2,
    }
    for name, pol in named.items():
        jobs.append((f"{name}:test-policy", fx / name, pol.to_json()))
    jobs.append(
        (
            "partial:GO807",
            fx / "partial",
            GoPolicy(
                tables={"Job": "jobs", "JobEvent": "job_events", "User": "users"}, rules=("GO807",)
            ).to_json(),
        )
    )
    jobs.append(("app:tasks-spec", fx / "app", GoPolicy.from_spec(tasks.SPEC).to_json()))
    sys.path.insert(0, str(streak_root))
    try:
        sys.modules.pop("spec", None)
        import spec as streak_spec  # noqa: PLC0415

        jobs.append(
            ("streak:from_spec", streak_root, GoPolicy.from_spec(streak_spec.SPEC).to_json())
        )
    finally:
        sys.path.remove(str(streak_root))
        sys.modules.pop("spec", None)
    jobs.append(("streak:empty", streak_root, GoPolicy().to_json()))
    same, diffs = 0, []
    sizes = {}
    for label, root, pol in jobs:
        a, b = gocheck_run(old_bin, root, pol), gocheck_run(new_bin, root, pol)
        sizes[label] = len(a)
        if a == b:
            same += 1
        else:
            diffs.append(label)
    ok = owned and not diffs
    return _result(
        "PB-gocheck",
        "gocheck sources changed only in main.go + goextract*.go; old and "
        "new findings byte-identical",
        {"changed": "main.go + new goextract*.go", "findings": "identical"},
        {
            "changed": changed,
            "owned_only": owned,
            "identical": f"{same}/{len(jobs)}",
            "different": diffs,
        },
        ok,
        sizes=sizes,
    )


# ---- reconcile (needs Docker, the Streak harness and a MySQL driver) -----------------------

GO_ENV = {"GOFLAGS": "-mod=mod", "GOPROXY": "off", "GOTOOLCHAIN": "local", "GOWORK": "off"}


def go_build(root: Path, out: Path | None = None, src: Path | None = None) -> None:
    """The runner's own ``go build`` of the app (never the extractor's or the shim's)."""
    target = out or (root / "bin" / "streak-api")
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["go", "build", "-o", str(target), "./cmd/server"],
        cwd=src or root,
        env=dict(os.environ, **GO_ENV),
        check=True,
        capture_output=True,
    )


def gox_containers() -> list[str]:
    proc = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=lattice-gox-", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return [n for n in proc.stdout.split() if n]


def probe_px(fe: Any, root: Path, model: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    from lattice.spec import dbserver

    before = gox_containers()
    n_before = len(dbserver.started_containers())
    res = fe.reconcile(root, model)
    started = dbserver.started_containers()[n_before:]
    after = gox_containers()
    statuses = {
        f"{k.split(' ', 1)[0]} {tpl(k.split(' ', 1)[1])}": v["status"] for k, v in res.items()
    }
    member = [f"{m} {p}" for m, p in GT_MEMBER]
    m401 = sum(1 for k in member if statuses.get(k) == 401)
    health = statuses.get("GET /health")
    public_4xx = sum(
        1
        for m, p in GT_PUBLIC
        if (m, p) != ("GET", "/health") and 400 <= statuses.get(f"{m} {p}", 0) < 500
    )
    controls = {k: v["control"] for k, v in res.items()}
    n404 = sum(1 for v in controls.values() if v == 404)
    obs = sum(1 for v in res.values() if v["observed"])
    px = _result(
        "PX",
        "reconcile on S: observed, control, container leaks",
        {
            "observed": "26/26",
            "member_401": 21,
            "health": 200,
            "public_4xx": 4,
            "control_404": "26/26",
            "containers_started": 1,
            "leaks": 0,
        },
        {
            "observed": f"{obs}/{len(res)}",
            "member_401": m401,
            "health": health,
            "public_4xx": public_4xx,
            "control_404": f"{n404}/{len(res)}",
            "containers_started": len(started),
            "leaks": len(after) - len(before),
        },
        obs == len(res) == 26 and n404 == 26 and len(after) == 0,
        non_404_controls={k: v for k, v in controls.items() if v != 404},
        statuses=statuses,
    )
    return px, res


def probe_px2(fe: Any, root: Path, scratch: Path, model: Any) -> dict[str, Any]:
    """A binary built from the source minus ``r.Member.GET("/home", ...)``, the source
    unchanged: only ``GET /v1/home`` is not observed."""
    stale = scratch / "s-stale"
    subprocess.run(["rm", "-rf", str(stale)], check=True)
    subprocess.run(["cp", "-R", str(root), str(stale)], check=True)
    p = stale / "internal/home/routes.go"
    text = p.read_text()
    needle = '\tr.Member.GET("/home", httpapi.Handle(h.home))\n'
    if text.count(needle) != 1:
        return _result(
            "PX2",
            "stale binary",
            "only GET /v1/home not observed",
            "script bug: the registration line was not found once",
            False,
        )
    p.write_text(text.replace(needle, "\t_ = h\n", 1))
    go_build(root, out=root / "bin" / "streak-api", src=stale)
    try:
        res = fe.reconcile(root, model)
    finally:
        go_build(root)  # restore the binary of the source
    missing = sorted(k for k, v in res.items() if not v["observed"])
    return _result(
        "PX2",
        "stale binary: reconcile reports the route the binary lacks",
        {"not_observed": ["GET /v1/home"]},
        {"not_observed": missing},
        missing == ["GET /v1/home"],
        statuses={k: v["status"] for k, v in res.items()},
    )


# ---- held out (run by the orchestrator, once) ----------------------------------------------


def score_app(x: dict[str, Any] | None, truth: dict[str, Any], spec: dict[str, Any] | None):
    """One held-out app against its ground-truth file and (optionally) the spec's cells.

    ``truth`` = {"routes": [[method, path], ...], "tables": {table: {"columns": {col:
    {"nullable", "primary_key", "unique", "index", "default", "max_length", "type"}}}}}.
    """
    if x is None:
        return {"crashed": True}
    want = {(m.upper(), tpl(p)) for m, p in truth["routes"]}
    got = {key_of(r) for r in x["routes"]}
    out: dict[str, Any] = {
        "crashed": False,
        "route_recall": round(len(want & got) / len(want), 3) if want else None,
        "route_precision": round(len(want & got) / len(got), 3) if got else None,
        "routes_missing": sorted(want - got),
        "routes_extra": sorted(got - want),
    }
    tables = {t["table"]: t for t in x["tables"]}
    tt = truth.get("tables", {})
    out["table_recall"] = round(len([t for t in tt if t in tables]) / len(tt), 3) if tt else None
    n = ok = 0
    for tname, spec_t in tt.items():
        have = {c["name"]: c for c in tables[tname]["columns"]} if tname in tables else {}
        for col, facts in spec_t["columns"].items():
            c = have.get(col)
            for k in (
                "nullable",
                "primary_key",
                "unique",
                "index",
                "default",
                "max_length",
                "type",
            ):
                n += 1
                if c is not None and c[k] == facts.get(k):
                    ok += 1
    out["column_facts"] = round(ok / n, 3) if n else None
    structural = [
        r
        for r in x["routes"]
        if not r["path_exact"] or r["handler"] is None or r["auth"] == "unclear"
    ]
    out["structural_unread"] = round(len(structural) / len(x["routes"]), 3) if x["routes"] else None
    out["unread_items"] = len(x["unread"])
    if spec is not None:
        # paths matched modulo the prefix: the spec's path is a suffix of the code's
        def suffix_match(code: str, sp: str) -> bool:
            a, b = tpl(code).strip("/").split("/"), tpl(sp).strip("/").split("/")
            return len(a) >= len(b) and a[len(a) - len(b) :] == b

        def find(method: str, sp: str):
            for r in x["routes"]:
                if r["method"] == method and suffix_match(r["path_template"], sp):
                    return r
            return None

        rows = ok_rows = 0
        for key in spec["endpoints"]:
            r = find(*key)
            if r is None:
                continue
            rows += 1
            want_public = bool(spec["anon_public"].get(tuple(key)))
            if (r["auth"] == "Public") == want_public and r["auth"] != "unclear":
                ok_rows += 1
        out["auth_vs_spec_rows"] = round(ok_rows / rows, 3) if rows else None
        cells = ok_cells = 0
        for key, status in spec["cells"]:
            r = find(*key)
            cells += 1
            if r is not None:
                have = {s["status"] for s in chain_raises(x, r)} | {
                    g["status"] for g in r["guards"]
                }
                if status in have:
                    ok_cells += 1
        out["raise_status_recall"] = round(ok_cells / cells, 3) if cells else None
    return out


def run_held_out(args: argparse.Namespace) -> int:
    """``--app NAME=DIR@REF`` ... ``--truth NAME=FILE`` ... ``--spec-dir DIR``: archive each
    app into scratch, extract once, score against its truth file. Report-only."""
    binary = _gocheck_binary()
    scratch, out = Path(args.scratch), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    freeze = {
        p.relative_to(REPO).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((REPO / "lattice/gocheck/src").glob("*.go"))
    }
    freeze["lattice/importer/frontends/go_gin.py"] = hashlib.sha256(
        (REPO / "lattice/importer/frontends/go_gin.py").read_bytes()
    ).hexdigest()
    truths = {}
    for item in args.truth:
        name, _, path = item.partition("=")
        raw = Path(path).read_bytes()
        truths[name] = (json.loads(raw), hashlib.sha256(raw).hexdigest())
    spec = None
    if args.spec_dir:
        spec = spec_cells(Path(args.spec_dir))
    results: dict[str, Any] = {}
    for item in args.app:
        name, _, rest = item.partition("=")
        src, _, ref = rest.partition("@")
        dest = scratch / f"h-{name}"
        archive(Path(src), ref or "HEAD", dest)
        try:
            raw, wall = extract_bytes(binary, dest)
            x: dict[str, Any] | None = json.loads(raw)
            (out / f"{name}-extract.json").write_bytes(raw)
        except Exception as err:  # noqa: BLE001 -- a crash is a result
            x, wall = None, 0.0
            results[name] = {"crashed": True, "error": str(err)[:400]}
        if x is not None:
            truth, digest = truths[name]
            results[name] = score_app(x, truth, spec) | {
                "truth_sha256": digest,
                "wall_s": round(wall, 3),
            }
    doc = {
        "freeze": freeze,
        "apps": results,
        "crashes": sum(1 for r in results.values() if r.get("crashed")),
    }
    (out / "held-out.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    print(json.dumps(doc, indent=2, sort_keys=True))
    return 0


# ---- the Streak stage ------------------------------------------------------------------------


def _gocheck_binary() -> Path:
    from lattice.gocheck import build_gocheck

    return build_gocheck()


def evaluate_streak(
    binary: Path,
    s_root: Path,
    s4_root: Path,
    s_root2: Path,
    out: Path,
    runs: int,
    only: set[str] | None = None,
    scratch: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], Any]:
    """Every probe that needs no Docker, against ``binary`` (the mutants runner calls this
    with a mutated build): the probe results, the extraction of S and its app model."""
    results: list[dict[str, Any]] = []

    def want(pid: str) -> bool:
        return only is None or pid in only

    def record(r: dict[str, Any] | list[dict[str, Any]]) -> None:
        for item in r if isinstance(r, list) else [r]:
            results.append(item)

    raw, _ = extract_bytes(binary, s_root)
    x = json.loads(raw)
    raw4, _ = extract_bytes(binary, s4_root)
    x4 = json.loads(raw4)
    (out / "s-extract.json").write_bytes(raw)
    (out / "s4-extract.json").write_bytes(raw4)
    cells_s, cells_s4 = spec_cells(s_root), spec_cells(s4_root)
    truth = gt_tables(s_root)
    if want("PR1"):
        record(probe_pr1(x, GT_S))
    if want("PR2"):
        record(probe_pr2(x, s_root))
    if want("PR3"):
        record(probe_pr3(x4, x, cells_s4, cells_s))
    if want("PA"):
        record(probe_pa(x, cells_s))
    if want("PA2"):
        record(probe_pa2(x))
    if want("PT"):
        record(probe_pt(x, truth))
    if want("PE"):
        record(probe_pe(x, cells_s))
    if want("PF"):
        record(probe_pf(x, cells_s))
    if want("PO"):
        record(probe_po(x))
    if want("PM"):
        record(probe_pm(x))
    if want("PU"):
        record(probe_pu(x, s_root))

    from lattice.importer.frontends.go_gin import model_from_extract

    model = model_from_extract(x)
    if want("PS"):
        try:
            record(probe_ps(model))
        except Exception as err:  # noqa: BLE001 -- reported, not hidden
            record(
                _result(
                    "PS",
                    "generate_profile + build_spec on S",
                    "runs; 26 contracts",
                    f"{type(err).__name__}: {err}",
                    False,
                )
            )
    if want("PD"):
        record(probe_pd(binary, s_root, s_root2, runs, out))
    return results, x, model


def probe_repro(
    binary: Path,
    s_root: Path,
    s4_root: Path,
    s_root2: Path,
    out: Path,
    n: int,
    fe: Any,
    model: Any,
    scratch: Path,
    docker: bool,
) -> dict[str, Any]:
    """Every deterministic probe ``n`` times: the results of the probes that need no Docker,
    and (with Docker) PX and PX2, must be identical run to run."""
    import tempfile

    def canon(results: list[dict[str, Any]]) -> str:
        return json.dumps(results, sort_keys=True, default=str)

    first: str | None = None
    same = 0
    for _ in range(n):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
            results, _, _ = evaluate_streak(binary, s_root, s4_root, s_root2, Path(tmp), 3)
        c = canon(results)
        first = first or c
        same += c == first
    docker_same, docker_runs = 0, 0
    docker_first = None
    if docker:
        for _ in range(n):
            px, _ = probe_px(fe, s_root, model)
            px2 = probe_px2(fe, s_root, scratch, model)
            c = json.dumps(
                [px["observed"], px["evidence"], px2["observed"], px2["evidence"]],
                sort_keys=True,
                default=str,
            )
            docker_first = docker_first or c
            docker_same += c == docker_first
            docker_runs += 1
    ok = same == n and docker_same == docker_runs
    return _result(
        "REPRO",
        f"every deterministic probe {n}x identical",
        {"no_docker_probes": f"{n}/{n}", "px_px2": f"{n}/{n}" if docker else "not run"},
        {"no_docker_probes": f"{same}/{n}", "px_px2": f"{docker_same}/{docker_runs}"},
        ok,
    )


def run_streak(args: argparse.Namespace) -> int:
    scratch, out = Path(args.scratch), Path(args.out)
    scratch.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    only = set(args.only.split(",")) if args.only else None
    not_run: list[str] = []

    def want(pid: str) -> bool:
        return only is None or pid in only

    s_root, s4_root, s_root2 = scratch / "s", scratch / "s4", scratch / "s-second-path"
    archive(STREAK, args.streak_ref, s_root)
    archive(STREAK, args.streak_ref2, s4_root)
    archive(STREAK, args.streak_ref, s_root2)
    binary = _gocheck_binary()
    # the runner's own build (bin/ in the clone), then the tree snapshot PW compares to
    go_build(s_root)
    snap0 = tree_digest(s_root)
    results, x, model = evaluate_streak(
        binary, s_root, s4_root, s_root2, out, args.runs, only, scratch
    )

    def record(r: dict[str, Any] | list[dict[str, Any]]) -> None:
        for item in r if isinstance(r, list) else [r]:
            results.append(item)
            print(f"{item['id']}: {item['pass']} {json.dumps(item['observed'], default=str)[:200]}")

    for item in list(results):
        print(f"{item['id']}: {item['pass']} {json.dumps(item['observed'], default=str)[:200]}")
    from lattice.importer.frontends.go_gin import GoGinFrontEnd

    if want("PT2"):
        record(probe_pt2(binary, s_root))
        record(probe_pt2_cold())
    if want("PB"):
        record(probe_pb(scratch, binary, s_root))
        record(probe_pb_diff())
    fe = GoGinFrontEnd()
    if want("PX"):
        try:
            px, _ = probe_px(fe, s_root, model)
            record(px)
        except Exception as err:  # noqa: BLE001
            record(_result("PX", "reconcile on S", "26/26", f"{type(err).__name__}: {err}", False))
    else:
        not_run.append("PX")
    if want("PW"):
        snap1 = tree_digest(s_root)
        changed = sorted(k for k in snap0 if snap1.get(k) != snap0[k])
        new = sorted(set(snap1) - set(snap0))
        record(
            _result(
                "PW",
                "tree listing + sha256 of S: after build vs after extract + reconcile",
                {"changed": 0, "new": 0},
                {"changed": len(changed), "new": len(new)},
                not changed and not new,
                changed=changed,
                new=new,
                files=len(snap0),
            )
        )
    else:
        not_run.append("PW")
    if want("PX2"):
        try:
            record(probe_px2(fe, s_root, scratch, model))
        except Exception as err:  # noqa: BLE001
            record(
                _result(
                    "PX2",
                    "stale binary",
                    "only GET /v1/home not observed",
                    f"{type(err).__name__}: {err}",
                    False,
                )
            )
    else:
        not_run.append("PX2")
    if want("REPRO"):
        record(
            probe_repro(
                binary,
                s_root,
                s4_root,
                s_root2,
                out,
                args.runs,
                fe,
                model,
                scratch,
                docker=want("PX") and want("PX2"),
            )
        )
    else:
        not_run.append("REPRO")
    leaked = gox_containers()
    record(
        _result(
            "LEAK",
            "lattice-gox- containers after the run",
            0,
            len(leaked),
            not leaked,
            names=leaked,
        )
    )
    ran = sorted({r["id"] for r in results})
    for pid in (
        "REPRO",
        "PR1",
        "PR2",
        "PR3",
        "PA",
        "PA2",
        "PT",
        "PE",
        "PE2",
        "PF",
        "PF2",
        "PO",
        "PM",
        "PS",
        "PU",
        "PD",
        "PT2-extract",
        "PT2-build",
        "PB-gocheck",
        "PB-diff",
        "PX",
        "PX2",
        "PW",
    ):
        if pid not in ran and pid not in not_run:
            not_run.append(pid)
    doc = {
        "streak_ref": args.streak_ref,
        "streak_ref2": args.streak_ref2,
        "runs": args.runs,
        "ran": ran,
        "not_run": sorted(set(not_run)),
        "results": results,
    }
    (out / "probes.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")
    lines = ["# Go front end probes (streak stage)", ""]
    for r in results:
        verdict = {True: "PASS", False: "FAIL", None: "REPORT"}[r["pass"]]
        lines.append(
            f"- RAN {r['id']}: {verdict} -- {r['probe']}: observed "
            f"{json.dumps(r['observed'], default=str)[:300]}"
        )
    for pid in sorted(set(not_run)):
        lines.append(f"- NOT RUN {pid}")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    return 0 if all(r["pass"] in (True, None) for r in results) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--streak-ref", default="a0679e9")
    ap.add_argument("--streak-ref2", default="1d00ede")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--held-out", action="store_true")
    ap.add_argument("--app", action="append", default=[], help="NAME=DIR@REF (held out)")
    ap.add_argument("--truth", action="append", default=[], help="NAME=FILE (held out)")
    ap.add_argument("--spec-dir", default="", help="a directory with the spec.py (held out)")
    args = ap.parse_args(argv)
    return run_held_out(args) if args.held_out else run_streak(args)


if __name__ == "__main__":
    raise SystemExit(main())
