"""The transitive tier of ``lattice contracts impact --transitive`` (multi-project slice 4c):
which projects sit DOWNSTREAM of a first-order contradiction, by declared links.

**What it is, and what it is not.** A provider's export is derived from its own spec and nothing
else, so when A's candidate changes and B's spec does not, B's export and every check of C against
B are unchanged by construction: at the contract level A's candidate cannot reach C. A real
chain exists only through something Lattice does not model (B's code or spec changing *because*
A did). So this tier computes no label: it is a **declared-reachability report gated at its
root by a computed contradiction**. Each first-order ``contradiction`` consumer is a *root* (it
must change, re-pin or get a revert); each project that relies on a root through opted-in links
(a ``contract`` key, ``static`` or ``live``), at any depth, is ``downstream``: unverified,
affected only if a root changes what it provides.

**Rules** (decided before coding, see the 2026-10-15 pre-registration):

* roots are the first-order rows labelled ``contradiction``; ``regression``, ``unaffected`` and
  error rows never propagate (an error row with opted-in dependents is a ``root_error`` note);
* edges are the manifest links with a ``contract`` key, both ends declared, ``from != to``;
* a multi-source BFS from the roots (depth 1) over the dependents, its visited set seeded with
  the roots and the PROVIDER: a root or the provider is never a row, a project appears once,
  the visited set ends every ``uses`` cycle, and there is no depth cap (the loop is bounded by
  the number of projects only so that it terminates whatever the graph);
* ``via`` is every exposed project (root or downstream) the row's project has an opted-in link to,
  ``roots`` the roots from which it is reachable, ``links`` one entry per ``via`` project;
* downstream rows are never labelled ``contradiction`` or ``regression``, never enter the
  first-order ``counts`` and never change an exit code.

**Read-only.** The module reads each downstream project's ``lattice.imports.toml`` and
``lattice.contracts.lock.json`` (layer 3's readers) and nothing else: no export, no subprocess,
no write. Everything is sorted: the same inputs give the same bytes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from lattice.contracts import check as ck
from lattice.workspace import Workspace

BASIS = "declared_links"
DOWNSTREAM = "downstream"
ROOT_LABELS = ("contradiction",)
CONTRACTS = ("static", "live")


def empty() -> dict[str, Any]:
    """The ``transitive`` document with no root and no row (every key present)."""
    return {"basis": BASIS, "verified": False, "propagates_from": list(ROOT_LABELS),
            "roots": [], "downstream": [], "notes": [],
            "counts": {"roots": 0, "downstream": 0}}  # fmt: skip


def _reach(start: str, dependents: Mapping[str, set[str]], banned: set[str]) -> set[str]:
    """Every project reachable from `start` over `dependents` without entering `banned`
    (`start` itself excluded)."""
    seen = {start, *banned}
    found: set[str] = set()
    stack = [start]
    while stack:
        for nxt in sorted(dependents.get(stack.pop(), ())):
            if nxt not in seen:
                seen.add(nxt)
                found.add(nxt)
                stack.append(nxt)
    return found


def _imports_of(ws: Workspace, name: str) -> tuple[list[ck.Import] | None, str | None]:
    """``(imports, None)`` or ``(None, note)`` with the note ``no_imports`` or ``bad_imports``
    (what ``check`` would refuse on, never an error here)."""
    project = ws.project(name)
    assert project is not None
    try:
        got = ck.parse_imports(project.path / ck.IMPORTS_FILE)
    except ck.ContractError as e:
        return None, e.code
    except (OSError, ValueError):
        return None, "bad_imports"
    known = set(ws.names())
    if any(imp.provider not in known for imp in got):
        return None, "bad_imports"
    return got, None


def _lock_of(ws: Workspace, name: str) -> tuple[dict[str, Any] | None, str | None]:
    """``(lock, None)`` or ``(None, note)``: ``no_pin`` (no lock file) or ``bad_pin``."""
    project = ws.project(name)
    assert project is not None
    try:
        lock = ck.read_lock(project.path / ck.LOCK_FILE, name)
    except ck.ContractError as e:
        return None, e.code
    except (OSError, ValueError):
        return None, "bad_pin"
    return (lock, None) if lock is not None else (None, "no_pin")


def downstream(ws: Workspace, rows: Sequence[Mapping[str, Any]], provider: str) -> dict[str, Any]:
    """The ``transitive`` document for the first-order `rows` of an impact of `provider`: pure
    over the manifest's links and the rows, plus the read-only imports and lock of each
    downstream project."""
    doc = empty()
    if ws.errors:  # as the first-order tier: nothing runs on an invalid manifest
        return doc
    known = set(ws.names())
    dependents: dict[str, set[str]] = {}  # B -> the projects that link to B with a contract
    relies_on_of: dict[str, dict[str, str]] = {}  # C -> {B: contract} (the first by sorted order)
    for lk in sorted(ws.links, key=lambda x: (x.source, x.target, x.contract or "")):
        if lk.contract not in CONTRACTS:
            continue
        if lk.source == lk.target or lk.source not in known or lk.target not in known:
            continue
        dependents.setdefault(lk.target, set()).add(lk.source)
        relies_on_of.setdefault(lk.source, {}).setdefault(lk.target, lk.contract or "")

    broken: dict[str, set[str]] = {}  # root consumer -> its BROKEN endpoints
    failed: dict[str, str] = {}  # consumer with a row in error -> the error code
    for row in rows:
        if row["error"] is not None:
            failed[row["consumer"]] = row["error"]["code"]
        elif row["label"] in ROOT_LABELS:
            broken.setdefault(row["consumer"], set()).update(
                f["endpoint"] for f in row["findings"] if f["class"] == "BROKEN"
            )
    roots = sorted(broken)

    visited = {provider}
    visited.update(roots)
    levels: list[list[str]] = [list(roots)]  # levels[i] is at depth i + 1
    for _ in range(len(known)):
        nxt = sorted({d for b in levels[-1] for d in dependents.get(b, ())} - visited)
        if not nxt:
            break
        visited.update(nxt)
        levels.append(nxt)
    exposed = {name for level in levels for name in level}

    files: dict[str, Any] = {}

    def read(name: str) -> tuple[Any, Any]:
        if name not in files:
            files[name] = (_imports_of(ws, name), _lock_of(ws, name))
        return files[name]  # type: ignore[no-any-return]

    out_rows: list[dict[str, Any]] = []
    reach = {r: _reach(r, dependents, {provider}) for r in roots}
    for index, level in enumerate(levels[1:], start=1):
        depth = index + 1
        for name in level:
            via = sorted(set(relies_on_of.get(name, ())) & exposed)
            (imports, imp_note), (lock, lock_note) = read(name)
            links = []
            for prov in via:
                link_notes = [imp_note] if imp_note else []
                relies: list[str] | None = None
                if imports is not None:
                    relies = sorted({i.endpoint for i in imports if i.provider == prov})
                pinned = None
                entry = ((lock or {}).get("providers") or {}).get(prov)
                if lock is None:
                    link_notes.append(lock_note or "no_pin")
                elif entry is None:
                    link_notes.append("no_pin")
                else:
                    pinned = ck.version_of(entry)
                links.append({"provider": prov, "contract": relies_on_of[name][prov],
                              "relies_on": relies, "pinned": pinned,
                              "notes": sorted(link_notes)})  # fmt: skip
            out_rows.append(
                {
                    "consumer": name,
                    "label": DOWNSTREAM,
                    "depth": depth,
                    "via": via,
                    "roots": sorted(r for r in roots if name in reach[r]),
                    "links": links,
                }
            )

    notes: list[dict[str, str]] = []
    for name in sorted(failed):
        deps = sorted(d for d in dependents.get(name, ()) if d != provider)
        if name not in broken and deps:
            notes.append(
                {
                    "code": "root_error",
                    "message": (
                        f"{name}: its impact row is in error ({failed[name]}); its "
                        f"{len(deps)} dependent(s) ({', '.join(deps)}) are not listed: "
                        "exposure unknown, not clean"
                    ),
                }
            )
    for target in sorted(relies_on_of.get(provider, ())):
        if target in exposed:
            notes.append(
                {
                    "code": "provider_downstream",
                    "message": (
                        f"{provider} itself has an opted-in link to {target}, which is exposed "
                        f"(a cycle through the provider; {provider} is the change, never a row)"
                    ),
                }
            )
    doc["roots"] = [
        {
            "consumer": r,
            "endpoints": sorted(broken[r]),
            "dependents": sorted(d for d in dependents.get(r, ()) if d != provider),
        }
        for r in roots
    ]
    doc["downstream"] = out_rows
    doc["notes"] = sorted(notes, key=lambda n: (n["code"], n["message"]))
    doc["counts"] = {"roots": len(roots), "downstream": len(out_rows)}
    return doc
