"""Reviewing a rules import (Phase 2 plan 2026-10-05, §5; W2-D)::

    python -m lattice.importer review DIR [--user U] list [--kind K] [--status S]
    python -m lattice.importer review DIR show ID
    python -m lattice.importer review DIR accept ID... [--reason TEXT]
    python -m lattice.importer review DIR reject ID... --reason TEXT
    python -m lattice.importer review DIR edit ID --expr EXPR [--no-reprobe]
    python -m lattice.importer review DIR emit [--out spec.py]
    python -m lattice.importer review DIR status [--out spec.py]

``DIR`` is an import directory (``--rules-only``): ``report.json`` (its ``rules``: layer
R's verdict per rule item), ``candidate_spec.py`` (``lattice.importer.emit_spec``),
``proposals.json`` (W2-C's mined proposals; absent: none) and ``profile.py``.

**What is reviewed.** Proposals (kinds ``invariant`` / ``machine`` / ``unchecked`` /
``contract``) go into the spec only when accepted. Rule items (T1-T5, R1-R3, ids as
``lattice.spec.probes.rule_items``) go in by their verdict: ``verified`` without a
review step; ``unexercised`` / ``unclear`` (and every item when the rule layer did not
run) as ``inferred``, kept unless rejected; ``refuted`` items are ``dropped`` unless
accepted (then ``inferred``). ``list`` shows the proposals and the rule items that are
not verified. Dropping a route item edits its contract: R1 -> ``auth=None``, R2 ->
``owner_scope="unknown"`` (both then ``unclear`` in layer R), an R3 error is removed; a
table item (the profile's) or the ``R3 ... 5xx`` item stays, its status recorded with
``applied: false``.

**Decisions** are appended to ``review.jsonl`` -- one JSON object per id: ``time``
(UTC), ``user``, ``id``, ``decision`` (accept / reject / edit), ``reason``, and for an
edit the ``expr`` and its ``reprobe`` -- and never rewritten; the last decision on an id
wins. A command naming any unknown id records nothing.

**Edits** revise an invariant proposal's expression. The expression must compile
against the profile, and the revision is **re-probed** before it can be accepted: layer
R runs again on the original app (from the report's ``repo`` / ``package`` /
``app_factory``) with layer C watching the revised invariant on every probe's database;
a violation the seed does not already have refutes it. ``accept`` refuses a proposal
whose latest edit was not re-probed clean.

**Emit** writes the spec module (``emit_spec.write_spec``: profile, verified programs,
contracts, accepted proposals; every item commented with its status) and
``spec_meta.json``: per rule item its ``kind``, ``verdict``, ``decision``, ``status``
and ``applied``; per proposal its ``kind``, ``status`` and spec-object key; ``counts``;
``rules_summary`` (the report's); and ``sources``, the sha256 of each input
(``report.json``, ``candidate_spec.py``, ``proposals.json``, ``review.jsonl``), so
``status`` can tell whether the emitted spec is current.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lattice.importer.emit_spec import (
    LoadedSpec,
    file_digest,
    load_spec,
    object_key,
    spec_object_from,
    write_spec,
)
from lattice.spec import Contract, Invariant, Machine, Spec, Unchecked, validate

__all__ = ["Review", "ReviewError", "main", "reprobe_invariant"]

LOG = "review.jsonl"
SOURCES = ("report.json", "candidate_spec.py", "proposals.json", LOG)
PROPOSAL_KINDS = ("invariant", "machine", "unchecked", "contract")
ROUTE_ITEMS = ("R1", "R2", "R3")
# a re-probe: (review, revised invariant) -> {"ok": bool, ...}
Reprobe = Callable[["Review", Invariant], Mapping[str, Any]]


class ReviewError(Exception):
    """A review command that cannot be carried out (nothing is recorded)."""


@dataclass(frozen=True)
class Entry:
    """One reviewable thing: a proposal or a rule item, with its state."""

    id: str
    kind: str  # a proposal kind, or "rule"
    status: str  # pending / accepted / rejected / edited
    sentence: str
    verdict: str | None = None  # rule items: layer R's verdict


_DUPLICATE = re.compile(r" #(\d+)$")


def candidate_item_ids(items: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    """Each report item id -> its id in the corrected candidate spec. A corrected item takes
    its ``correction.corrected`` id. The others keep theirs, except that an id carrying a
    ``#n`` suffix (the n-th error of a route with the same status and detail) is renumbered:
    when a correction renames one of the duplicates, the rest move up, as
    ``lattice.spec.probes._error_ids`` numbers them in the candidate."""
    moved = {
        i: str(v["correction"]["corrected"])
        for i, v in items.items()
        if isinstance(v.get("correction"), Mapping) and v["correction"].get("corrected")
    }
    groups: dict[str, list[tuple[int, str]]] = {}
    for i in items:
        if i not in moved:
            m = _DUPLICATE.search(i)
            groups.setdefault(i[: m.start()] if m else i, []).append((int(m[1]) if m else 1, i))
    out = dict(moved)
    for base, members in groups.items():
        for n, (_, i) in enumerate(sorted(members), 1):
            out[i] = base if n == 1 else f"{base} #{n}"
    return out


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Review:
    """An import directory under review (see the module docstring)."""

    def __init__(self, directory: Path | str, *, user: str | None = None) -> None:
        self.dir = Path(directory)
        if not (self.dir / "report.json").exists():
            raise ReviewError(f"{self.dir} is not an import directory (no report.json)")
        if not (self.dir / "candidate_spec.py").exists():
            raise ReviewError(f"{self.dir} has no candidate_spec.py: import with --rules-only")
        self.user = user or os.environ.get("LATTICE_REVIEW_USER") or getpass.getuser()
        self.report: dict[str, Any] = json.loads((self.dir / "report.json").read_text())
        self.candidate: LoadedSpec = load_spec(self.dir / "candidate_spec.py")
        path = self.dir / "proposals.json"
        records = json.loads(path.read_text()) if path.exists() else []
        self.proposals: dict[str, dict[str, Any]] = {r["id"]: r for r in records}
        self._items = self._rule_items()

    # ---- what is under review --------------------------------------------------------

    def _rule_items(self) -> dict[str, dict[str, Any]]:
        from lattice.spec.probes import rule_items

        rules = self.report.get("rules") or {}
        verdicts = rules.get("items") or {}
        renamed = candidate_item_ids(verdicts)
        # a candidate id keeps the verdict of the report item it now names (duplicates renumbered)
        verdicts = {
            renamed[i]: v
            for i, v in verdicts.items()
            if not (isinstance(v.get("correction"), Mapping) and v["correction"].get("corrected"))
        }
        out: dict[str, dict[str, Any]] = {}
        for item in rule_items(self.candidate.spec):
            v = verdicts.get(item.id, {})
            out[item.id] = {
                "id": item.id,
                "kind": item.kind,
                "subject": item.subject,
                "sentence": item.sentence,
                "predicted": dict(item.predicted),
                "verdict": v.get("verdict"),
                "reason": v.get("reason", ""),
                "observed": v.get("observed"),
                "probes": v.get("probes", []),
                "notes": v.get("notes", []),
            }
        return out

    @property
    def log(self) -> Path:
        return self.dir / LOG

    def decisions(self) -> list[dict[str, Any]]:
        """Every recorded decision, in order."""
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]

    def _last(self) -> dict[str, dict[str, Any]]:
        """Per id: its last accept/reject (``decision``) and its last edit (``edit``)."""
        out: dict[str, dict[str, Any]] = {}
        for d in self.decisions():
            state = out.setdefault(d["id"], {})
            if d["decision"] == "edit":
                state["edit"] = d
                state.pop("decision", None)  # a revision is decided afresh
            else:
                state["decision"] = d
        return out

    def _known(self, ids: Sequence[str]) -> None:
        unknown = [i for i in ids if i not in self.proposals and i not in self._items]
        if unknown:
            raise ReviewError(f"unknown id(s): {', '.join(map(repr, unknown))} (see `list`)")
        if not ids:
            raise ReviewError("name at least one id")

    def entries(self, kind: str | None = None, status: str | None = None) -> list[Entry]:
        """The proposals, then the rule items that are not verified (``kind`` "rule" or a
        proposal kind; ``status`` pending / accepted / rejected / edited)."""
        last = self._last()
        out: list[Entry] = []
        for pid, p in self.proposals.items():
            state = last.get(pid, {})
            s = state.get("decision", {}).get("decision")
            st = {"accept": "accepted", "reject": "rejected"}.get(s or "", "")
            st = st or ("edited" if "edit" in state else "pending")
            out.append(Entry(pid, p["kind"], st, p.get("sentence") or ""))
        for item_id, item in self._items.items():
            if item["verdict"] == "verified":
                continue
            s = last.get(item_id, {}).get("decision", {}).get("decision")
            st = {"accept": "accepted", "reject": "rejected"}.get(s or "", "pending")
            out.append(Entry(item_id, "rule", st, item["sentence"], item["verdict"] or "unprobed"))
        return [
            e for e in out
            if (kind is None or e.kind == kind) and (status is None or e.status == status)
        ]  # fmt: skip

    def show(self, entry_id: str) -> dict[str, Any]:
        """Everything known about one id, with its decisions."""
        self._known([entry_id])
        record = dict(self.proposals.get(entry_id) or self._items[entry_id])
        record["decisions"] = [d for d in self.decisions() if d["id"] == entry_id]
        return record

    # ---- decisions ---------------------------------------------------------------------

    def _append(self, records: Sequence[dict[str, Any]]) -> None:
        with self.log.open("a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, sort_keys=False, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def _record(self, entry_id: str, decision: str, **extra: Any) -> dict[str, Any]:
        return {"time": _now(), "user": self.user, "id": entry_id, "decision": decision, **extra}

    def accept(self, ids: Sequence[str], reason: str | None = None) -> None:
        self._known(ids)
        last = self._last()
        for i in ids:
            edit = last.get(i, {}).get("edit")
            if edit is not None:
                probe = edit.get("reprobe")
                if probe is None:
                    raise ReviewError(f"{i}: its revision was not re-probed; edit it again")
                if not probe.get("ok"):
                    raise ReviewError(f"{i}: the re-probe refuted its revision: {probe}")
        self._append([self._record(i, "accept", reason=reason) for i in ids])

    def reject(self, ids: Sequence[str], reason: str) -> None:
        if not reason or not reason.strip():
            raise ReviewError("a rejection needs --reason")
        self._known(ids)
        self._append([self._record(i, "reject", reason=reason) for i in ids])

    def revised(self, pid: str, expr: str) -> Invariant:
        """The proposal's invariant with ``expr``, checked against the profile."""
        self._known([pid])
        p = self.proposals.get(pid)
        if p is None or p["kind"] != "invariant":
            raise ReviewError(f"{pid}: only invariant proposals take an edited expression")
        inv = spec_object_from(p["spec_object"])
        inv = replace(inv, expr=expr)
        problems = validate(Spec(self.candidate.spec.profile, invariants=(inv,)))
        if problems:
            raise ReviewError(f"{pid}: " + "; ".join(x.render() for x in problems))
        return inv

    def edit(self, pid: str, expr: str, *, reprobe: Reprobe | None | str = "default") -> dict:
        """Record a revision of an invariant proposal, re-probed with ``reprobe``
        (default :func:`reprobe_invariant`; None: not re-probed -- it cannot be accepted
        until edited again with one)."""
        inv = self.revised(pid, expr)
        if reprobe == "default":
            reprobe = reprobe_invariant
        result: Mapping[str, Any] | None = None
        if reprobe is not None:
            result = dict(reprobe(self, inv))  # type: ignore[operator]
        record = self._record(pid, "edit", expr=expr, reprobe=result)
        self._append([record])
        return record

    # ---- the spec ----------------------------------------------------------------------

    def _proposal_object(self, pid: str, state: Mapping[str, Any]) -> Any:
        p = self.proposals[pid]
        obj = spec_object_from(p["spec_object"])
        edit = state.get("edit")
        if edit is not None and isinstance(obj, Invariant):
            obj = replace(obj, expr=edit["expr"])
        return obj

    def build(self) -> tuple[Spec, dict[str, str], dict[str, Any]]:
        """The reviewed spec, its STATUS and the meta's ``items`` / ``proposals`` /
        ``counts``."""
        from lattice.spec.probes import _error_ids

        last = self._last()
        spec = self.candidate.spec
        contracts = {c.endpoint: c for c in spec.contracts}
        status: dict[str, str] = {}
        items: dict[str, dict[str, Any]] = {}
        drops: list[str] = []
        for item_id, item in self._items.items():
            decision = last.get(item_id, {}).get("decision", {}).get("decision")
            verdict = item["verdict"]
            if decision == "reject":
                s = "rejected"
            elif verdict == "verified":
                s = "verified"
            elif verdict == "refuted":
                s = "inferred" if decision == "accept" else "dropped"
            else:
                s = "inferred"
            applied = True
            if s in ("rejected", "dropped"):
                applied = item["kind"] in ROUTE_ITEMS and not item_id.endswith(" 5xx")
                if applied:
                    drops.append(item_id)
            items[item_id] = {"kind": item["kind"], "verdict": verdict, "decision": decision,
                              "status": s, "applied": applied}  # fmt: skip
            if applied and s in ("rejected", "dropped"):
                continue
            status[item_id] = s
        for item_id in drops:
            kind, _, rest = item_id.partition(" ")
            for ep, c in list(contracts.items()):
                if rest != ep and not rest.startswith(ep + " "):
                    continue
                if kind == "R1" and rest == ep:
                    contracts[ep] = replace(c, auth=None)
                elif kind == "R2" and rest == ep:
                    contracts[ep] = replace(c, owner_scope="unknown")
                elif kind == "R3":
                    ids = [i for i, _ in _error_ids(c)]
                    if item_id in ids:
                        k = ids.index(item_id)
                        contracts[ep] = replace(c, errors=c.errors[:k] + c.errors[k + 1 :])
        invariants, machines, unchecked = (
            list(spec.invariants),
            list(spec.machines),
            list(spec.unchecked),
        )
        proposals: dict[str, dict[str, Any]] = {}
        accepted: list[tuple[str, Any]] = []
        for pid, p in self.proposals.items():
            state = last.get(pid, {})
            decision = state.get("decision", {}).get("decision")
            s = {"accept": "proposed-accepted", "reject": "rejected"}.get(
                decision or "", "edited" if "edit" in state else "pending"
            )
            proposals[pid] = {"kind": p["kind"], "status": s, "sentence": p.get("sentence")}
            if s == "proposed-accepted":
                accepted.append((pid, self._proposal_object(pid, state)))
        for _, obj in accepted:
            if isinstance(obj, Invariant):
                invariants.append(obj)
            elif isinstance(obj, Contract):
                contracts[obj.endpoint] = obj
            elif isinstance(obj, Machine):
                machines.append(obj)
            elif isinstance(obj, Unchecked):
                unchecked.append(obj)
        reviewed = replace(spec, contracts=tuple(contracts.values()), invariants=tuple(invariants),
                           machines=tuple(machines), unchecked=tuple(unchecked))  # fmt: skip
        for pid, obj in accepted:
            index = reviewed.unchecked.index(obj) if obj in reviewed.unchecked else 0
            key = obj.endpoint if isinstance(obj, Contract) else object_key(obj, index)
            proposals[pid]["key"] = key
            status[key] = "proposed-accepted"
        counts: dict[str, dict[str, int]] = {"items": {}, "proposals": {}}
        for group, records in (("items", items), ("proposals", proposals)):
            for r in records.values():
                counts[group][r["status"]] = counts[group].get(r["status"], 0) + 1
        return reviewed, status, {"items": items, "proposals": proposals, "counts": counts}

    def default_out(self) -> Path:
        return self.dir / "spec.py"

    def emit(self, out: Path | str | None = None) -> Path:
        out = Path(out) if out is not None else self.default_out()
        spec, status, meta = self.build()
        rules = self.report.get("rules") or {}
        meta["rules_summary"] = rules.get("summary")
        meta["sources"] = {name: file_digest(self.dir / name) for name in SOURCES}
        meta["import_dir"] = str(self.dir.resolve())
        return write_spec(spec, out, status=status, package=self.candidate.package,
                          profile=self.dir / "profile.py", meta=meta)  # fmt: skip

    def status(self, out: Path | str | None = None) -> dict[str, Any]:
        """The counts, and whether the spec at ``out`` is current with the inputs."""
        out = Path(out) if out is not None else self.default_out()
        counts: dict[str, dict[str, int]] = {}
        for e in self.entries():
            group = counts.setdefault("rule items" if e.kind == "rule" else "proposals", {})
            group[e.status] = group.get(e.status, 0) + 1
        verdicts: dict[str, int] = {}
        for item in self._items.values():
            v = item["verdict"] or "unprobed"
            verdicts[v] = verdicts.get(v, 0) + 1
        meta_path = out.parent / "spec_meta.json"
        if not out.exists() or not meta_path.exists():
            emitted = "not emitted"
            stale: list[str] = []
        else:
            meta = json.loads(meta_path.read_text())
            stale = [
                n for n in SOURCES if meta.get("sources", {}).get(n) != file_digest(self.dir / n)
            ]
            if meta.get("spec") != file_digest(out):
                stale.append(out.name)
            emitted = "current" if not stale else "stale"
        return {"counts": counts, "verdicts": verdicts, "decisions": len(self.decisions()),
                "spec": str(out), "emitted": emitted, "stale": stale}  # fmt: skip


# ---- re-probing ------------------------------------------------------------------------------


def reprobe_invariant(review: Review, inv: Invariant) -> dict[str, Any]:
    """Layer R again on the original (the report's app), layer C watching ``inv`` on
    every probe's database: ``ok`` when no probe leaves a violation the seed lacks."""
    from lattice.importer.cli import resolve_factory
    from lattice.importer.emit_spec import probe_import
    from lattice.importer.frontends import select
    from lattice.importer.lift import lift_repo

    report = review.report
    if not (report.get("repo") and report.get("app_factory") and report.get("package")):
        return {"ok": False, "error": "the report names no repo, package or app factory"}
    root = Path(report["repo"])
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    framework = (report.get("rules") or {}).get("framework")
    frontend = select(framework) if framework else None
    result = lift_repo(root, report["package"], resolve_factory(report["app_factory"]), only=(),
                       frontend=frontend)  # fmt: skip
    spec = replace(review.candidate.spec, invariants=(inv,), machines=(), scenarios=())
    checked = probe_import(result, spec, layers="CR")
    found = [f for f in checked.findings if f.layer == "C" and f.rule == inv.sentence]
    return {
        "ok": not found,
        "violations": len(found),
        "examples": [{"case": f.case, "endpoint": f.endpoint} for f in found[:5]],
    }


# ---- the CLI -----------------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m lattice.importer review", description="review a rules import"
    )
    p.add_argument("dir", type=Path, help="the import directory (--rules-only)")
    p.add_argument(
        "--user", default=None, help="who decides (default: $LATTICE_REVIEW_USER, the login)"
    )
    sub = p.add_subparsers(dest="command", required=True)
    ls = sub.add_parser("list", help="proposals and the rule items that are not verified")
    ls.add_argument("--kind", choices=(*PROPOSAL_KINDS, "rule"))
    ls.add_argument("--status", choices=("pending", "accepted", "rejected", "edited"))
    show = sub.add_parser("show", help="one proposal or rule item")
    show.add_argument("id")
    acc = sub.add_parser("accept", help="accept proposals or rule items")
    acc.add_argument("ids", nargs="+")
    acc.add_argument("--reason", default=None)
    rej = sub.add_parser("reject", help="reject proposals or rule items")
    rej.add_argument("ids", nargs="+")
    rej.add_argument("--reason", default=None)
    ed = sub.add_parser("edit", help="revise an invariant proposal (re-probed)")
    ed.add_argument("id")
    ed.add_argument("--expr", required=True)
    ed.add_argument(
        "--no-reprobe",
        action="store_true",
        help="record the revision without re-probing (it cannot be accepted)",
    )
    em = sub.add_parser("emit", help="write the reviewed spec module and spec_meta.json")
    em.add_argument("--out", type=Path, default=None, help="default DIR/spec.py")
    st = sub.add_parser("status", help="counts, and whether the emitted spec is current")
    st.add_argument("--out", type=Path, default=None, help="default DIR/spec.py")
    return p


def _print_show(record: Mapping[str, Any]) -> None:
    for key in ("id", "kind", "subject", "sentence", "expr", "verdict", "reason", "observed",
                "predicted", "source", "support", "status"):  # fmt: skip
        if record.get(key) not in (None, "", [], {}):
            print(f"{key}: {record[key]}")
    if record.get("spec_object"):
        print(f"spec_object: {json.dumps(record['spec_object'])}")
    for key in ("evidence", "probes", "notes"):
        if record.get(key):
            print(f"{key}:")
            for x in record[key]:
                print(f"  - {json.dumps(x, default=str) if not isinstance(x, str) else x}")
    for d in record.get("decisions", []):
        extra = d.get("reason") or d.get("expr") or ""
        print(f"decision: {d['time']} {d['user']} {d['decision']} {extra}".rstrip())


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        r = Review(args.dir, user=args.user)
        match args.command:
            case "list":
                for e in r.entries(args.kind, args.status):
                    verdict = f" [{e.verdict}]" if e.verdict else ""
                    print(f"{e.id}\t{e.kind}\t{e.status}{verdict}\t{e.sentence}")
            case "show":
                _print_show(r.show(args.id))
            case "accept":
                r.accept(args.ids, args.reason)
                print(f"accepted: {', '.join(args.ids)}")
            case "reject":
                r.reject(args.ids, args.reason)
                print(f"rejected: {', '.join(args.ids)}")
            case "edit":
                record = r.edit(
                    args.id, args.expr, reprobe=None if args.no_reprobe else reprobe_invariant
                )
                probe = record["reprobe"]
                if probe is None:
                    state = "not re-probed"
                else:
                    state = "re-probed clean" if probe["ok"] else f"re-probe refuted it: {probe}"
                print(f"edited {args.id}: {args.expr} ({state})")
            case "emit":
                out = r.emit(args.out)
                print(f"wrote {out} and {out.parent / 'spec_meta.json'}")
            case "status":
                s = r.status(args.out)
                for group, counts in s["counts"].items():
                    print(f"{group}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
                print(
                    "rule verdicts: "
                    + ", ".join(f"{k} {v}" for k, v in sorted(s["verdicts"].items()))
                )
                print(f"decisions: {s['decisions']}")
                extra = f" ({', '.join(s['stale'])} changed since)" if s["stale"] else ""
                print(f"spec: {s['spec']}: {s['emitted']}{extra}")
    except ReviewError as err:
        print(f"review: {err}", file=sys.stderr)
        return 2
    return 0
