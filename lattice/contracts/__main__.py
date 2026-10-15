"""``lattice contracts export|pin|check|impact|notify|questions``: contracts between projects
(multi-project layers 3, 4a and 4b).

    lattice contracts export NAME [--spec REF] [--workspace PATH]    # the provider's export
    lattice contracts export --project DIR [--spec REF] [--exclude "METHOD /path" ...]
                                                                     # ... without a workspace
    lattice contracts pin CONSUMER [--force] [--json] [--workspace PATH]
    lattice contracts check [CONSUMER] [--json] [--static-only] [--workspace PATH]
    lattice contracts impact PROVIDER --candidate REF|PATH [--transitive] [--json]
                             [--workspace PATH]
    lattice contracts notify PROVIDER --candidate REF|PATH [--dry-run] [--json] [--workspace PATH]
    lattice contracts questions list CONSUMER [--status open|resolved|dismissed|all] [--json]
    lattice contracts questions resolve|dismiss CONSUMER ID [--note TEXT] [--json]
    lattice contracts questions init CONSUMER

``export`` prints the canonical JSON ``{"export", "spec_ref", "version"}`` (exit 0, or 2 when the
spec cannot be loaded), plus ``exclude_unmatched`` when the provider excludes routes. The
exclusion of a workspace project is its manifest's ``exclude`` list; ``--exclude`` (repeatable,
validated like the key, exit 2 with the reason) is for ``--project`` only, and is refused with a
NAME so that ``export NAME`` is always what ``check`` uses. ``pin`` writes the consumer's
``lattice.contracts.lock.json`` (exit 0), refuses while a link has a BROKEN finding unless
``--force`` (exit 1), exit 2 when it cannot run.
``check`` exits 0 when clean, 1 on any BROKEN or CHANGED, 2 when it cannot run (several links: 2
over 1 over 0). Only links with ``contract = "static"`` or ``"live"`` in the workspace manifest
are looked at; ``--static-only`` skips the live part. See :mod:`lattice.contracts.check`.
``impact`` previews what a candidate version of PROVIDER (a directory, or a git ref of its
repository) would do to each consumer's declared reliance, against that consumer's pin: advisory,
read-only, static only; one row per opted-in link, labelled ``contradiction`` (a BROKEN finding),
``regression`` (a relied CHANGED one) or ``unaffected``; exit 0, 1 on a contradiction, 2 when it
cannot run (a ``regression`` alone exits 0). See :mod:`lattice.contracts.impact`.
``impact --transitive`` adds the projects that rely on a ``contradiction`` consumer through
opted-in links, at any depth, as ``downstream`` rows: declared reachability, unverified, never a
label of the first-order tier and never in its counts or exit code (one extra ``transitive`` key
in ``--json``, one extra block in the text). See :mod:`lattice.contracts.transitive`.
``notify`` is ``impact`` plus advisory notes in the consumers' own state (multi-project slice
4b): every consumer row that is a ``contradiction`` gets a linked question (``created``, or
``existing`` when the same break is already open), delivered with the first cross-project write
(only ``<consumer state>/linked_questions/``); exit 0, 1 a consumer undelivered or in error, 2
the impact could not run; ``--dry-run`` writes nothing. ``questions`` is the consumer owner's
side: ``list`` (exit 0, 2 unknown consumer or manifest error), ``resolve`` / ``dismiss`` (0, 1
refused: not open, bad note, 2 cannot), ``init`` creates the consumer's state directory. See
:mod:`lattice.contracts.linked`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from lattice import workspace as wsmod
from lattice.contracts import export as ex


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m lattice.contracts",
        description="Contracts between projects: export a provider, pin and check a consumer.",
    )
    ap.add_argument("--workspace", default=None, help="the manifest (or its directory); default: "
                    f"${wsmod.ENV}, else {wsmod.MANIFEST} walking up from the cwd")  # fmt: skip
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--workspace", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    e = sub.add_parser("export", help="the canonical export and version of a provider project")
    e.add_argument("name", nargs="?", help="a project of the workspace")
    e.add_argument("--project", help="a project directory (no workspace needed)")
    e.add_argument("--exclude", action="append", metavar="'METHOD /path'",
                   help="a route that is not part of the contract (repeatable; with "
                   "--project only)")  # fmt: skip
    e.add_argument(
        "--spec", help="module:attr of the Spec (default: the manifest's, else spec:SPEC)"
    )
    common(e)
    p = sub.add_parser("pin", help="write the consumer's lock from the providers' current exports")
    p.add_argument("consumer")
    p.add_argument("--force", action="store_true", help="pin although a link has a BROKEN finding")
    p.add_argument("--json", action="store_true")
    common(p)
    c = sub.add_parser("check", help="check the consumer's imports against the pinned exports")
    c.add_argument("consumer", nargs="?")
    c.add_argument("--json", action="store_true")
    c.add_argument("--static-only", action="store_true", help="skip the live part of live links")
    common(c)
    i = sub.add_parser("impact", help="preview a candidate provider version against the "
                       "consumers' pins (advisory, read-only)")  # fmt: skip
    i.add_argument("provider", help="a project of the workspace")
    i.add_argument(
        "--candidate",
        required=True,
        metavar="REF|PATH",
        help="a directory (the provider project), or a git ref of its repository",
    )
    i.add_argument("--transitive", action="store_true", help="also list the projects that rely "
                   "on a contradiction consumer through opted-in links (declared "
                   "reachability, unverified, advisory)")  # fmt: skip
    i.add_argument("--json", action="store_true")
    common(i)
    n = sub.add_parser("notify", help="leave advisory linked questions in the consumers' state "
                       "for the contradictions of a candidate provider version")  # fmt: skip
    n.add_argument("provider", help="a project of the workspace")
    n.add_argument(
        "--candidate",
        required=True,
        metavar="REF|PATH",
        help="a directory (the provider project), or a git ref of its repository",
    )
    n.add_argument("--dry-run", action="store_true", help="decide, write nothing")
    n.add_argument("--json", action="store_true")
    common(n)
    q = sub.add_parser("questions", help="a consumer's linked questions (its owner's side)")
    qsub = q.add_subparsers(dest="qcmd", required=True)
    ql = qsub.add_parser("list", help="list the consumer's linked questions")
    ql.add_argument("consumer")
    ql.add_argument("--status", choices=["open", "resolved", "dismissed", "all"], default="all")
    ql.add_argument("--json", action="store_true")
    common(ql)
    for verb, what in (("resolve", "handled: adapted, re-pinned or agreed"),
                       ("dismiss", "not relevant")):  # fmt: skip
        qc = qsub.add_parser(verb, help=f"clear an open question as {what}")
        qc.add_argument("consumer")
        qc.add_argument("id", metavar="ID", help="a question id (lq1, lq2, ...)")
        qc.add_argument("--note", default="", help="a note of at most 2000 characters")
        qc.add_argument("--json", action="store_true")
        common(qc)
    qi = qsub.add_parser("init", help="create the consumer's state directory and its "
                         "linked_questions/ (idempotent)")  # fmt: skip
    qi.add_argument("consumer")
    qi.add_argument("--json", action="store_true")
    common(qi)
    return ap


def _short(version: dict[str, Any]) -> str:
    ref = (version.get("git_ref") or "no-git")[:8]
    return f"{ref}{'+dirty' if version.get('dirty') else ''} ({version['spec_hash'][:12]})"


def _print_finding(f: dict[str, Any]) -> None:
    tag = f["class"] if f["class"] != "CHANGED" else ("CHANGED*" if f.get("relied") else "CHANGED")
    where = f"  {f['where']}" if f.get("where") else ""
    item = f"  {f['item']}" if f["item"] else ""
    print(f"  {tag:<8} {f['kind']}{where}{item}  @ {f['endpoint']}")


def _print_check(report: Any) -> None:
    for lk in report.links:
        head = f"{lk['consumer']} -> {lk['provider']}  [{lk['contract']}]"
        if lk["status"] == "error":
            print(f"{head}  ERROR [{lk['error']['code']}] {lk['error']['message']}")
            continue
        print(f"{head}  {lk['status'].upper()}  {lk['imports']} import(s), "
              f"pinned {_short(lk['pinned'])}, now {_short(lk['current'])}")  # fmt: skip
        if lk["version_changed"] or lk["spec_ref_changed"]:
            note = "spec_ref changed" if lk["spec_ref_changed"] else "version changed"
            print(f"  note: {note}; spec_hash {'changed' if lk['spec_hash_changed'] else 'equal'}")
        for f in lk["findings"]:
            _print_finding(f)
        for g in lk["gaps"]:
            print(f"  gap      {g['kind']}  {g['item'] or ''}  @ {g['endpoint']}")
        live = lk["live"]
        if live is not None:
            if live["status"] in ("not_built", "not_run"):
                print(f"  live: {live['status']} ({live['reason']})")
            else:
                print(f"  live: {live['status']}, replayed {live.get('replayed', 0)}, "
                      f"{len(live['findings'])} finding(s), "
                      f"{len(live['verified_live'])} gap(s) verified live")  # fmt: skip
                for f in live["findings"]:
                    _print_finding(f)
    for p in report.problems:
        print(f"  {p.severity}: [{p.code}] {p.message}")
    n = len(report.links)
    broken = sum(1 for lk in report.links for f in lk["findings"] if f["class"] == "BROKEN")
    changed = sum(1 for lk in report.links for f in lk["findings"] if f["class"] == "CHANGED")
    gaps = sum(len(lk["gaps"]) for lk in report.links)
    print(f"{n} link(s) checked: {broken} broken, {changed} changed, {gaps} gap(s)")


def _print_impact(rep: Any) -> None:
    cand, cur = rep.candidate, rep.current
    ver = cand["version"]
    if ver:
        ref = (ver["git_ref"] or "no-git")[:8] + ("+dirty" if ver["dirty"] else "")
        shown = f"{cand['kind']}, {ref} {ver['spec_hash'][:12]}"
    else:
        shown = f"{cand['kind']}, not loaded"
    print(f"impact of {cand['input']} ({shown}) on {rep.provider}: advisory, static only, "
          f"baseline = each consumer's pin; provider now "
          f"{_short(cur) if cur else 'unavailable'}")  # fmt: skip
    for row in rep.consumers:
        if row["error"] is not None:
            print(f"{row['consumer']}  ERROR [{row['error']['code']}] {row['error']['message']}")
            continue
        now = row["current_label"] or "unavailable"
        print(f"{row['consumer']}  {row['label'].upper()}  {row['imports']} import(s), "
              f"pinned {_short(row['pinned'])}, now: {now}")  # fmt: skip
        for f in row["findings"]:
            _print_finding(f)
        for g in row["gaps"]:
            print(f"  gap      {g['kind']}  {g['item'] or ''}  @ {g['endpoint']}")
    for p in rep.problems:
        print(f"  {p.severity}: [{p.code}] {p.message}")
    n = rep.counts
    print(f"{len(rep.consumers)} consumer(s): {n['contradiction']} contradiction, "
          f"{n['regression']} regression, {n['unaffected']} unaffected, "
          f"{n['error']} error")  # fmt: skip


def _print_transitive(doc: dict[str, Any]) -> None:
    n = doc["counts"]
    print(f"downstream (declared links, unverified; from {', '.join(doc['propagates_from'])} "
          f"only): {n['roots']} root(s), {n['downstream']} project(s)")  # fmt: skip
    for row in doc["downstream"]:
        print(f"{row['consumer']}  {row['label'].upper()}  depth {row['depth']} via "
              f"{', '.join(row['via'])} (roots: {', '.join(row['roots'])})")  # fmt: skip
        for lk in row["links"]:
            relies = lk["relies_on"]
            what = "unreadable" if relies is None else (", ".join(relies) or "nothing")
            pin = _short(lk["pinned"]) if lk["pinned"] else "no pin"
            notes = f" [{', '.join(lk['notes'])}]" if lk["notes"] else ""
            print(f"  {lk['provider']} {lk['contract']}: relies on {what}; pinned {pin}{notes}")
    for note in doc["notes"]:
        print(f"  note: [{note['code']}] {note['message']}")


def _print_pin(report: Any) -> None:
    from lattice.contracts import check

    for lk in report.links:
        if lk["status"] == "error":
            print(f"{lk['consumer']} -> {lk['provider']}  ERROR [{lk['error']['code']}] "
                  f"{lk['error']['message']}")  # fmt: skip
            continue
        word = {"pinned": "pinned", "refused": "REFUSED"}[lk["status"]]
        print(f"{lk['consumer']} -> {lk['provider']}  {word}  {lk['endpoints']} endpoint(s), "
              f"{_short(lk['current'])}")  # fmt: skip
        for f in lk["broken"]:
            _print_finding(f)
        if lk["status"] == "refused":
            print("  not written: re-pinning would turn each BROKEN into a gap "
                  "(use --force to accept)")  # fmt: skip
            if any(f["detail"] == check.EXCLUDED_DETAIL for f in lk["broken"]):
                print("  note: an import of an excluded endpoint stays BROKEN even when forced")
        elif lk["forced"]:
            print("  forced: the BROKEN items above are now gaps")
            if any(f["detail"] == check.EXCLUDED_DETAIL for f in lk["broken"]):
                print("  note: an import of an excluded endpoint stays BROKEN")
    for p in report.problems:
        print(f"  {p.severity}: [{p.code}] {p.message}")
    if report.extra.get("written"):
        print(f"wrote {report.extra['lock']}")


def _print_questions(doc: dict[str, Any]) -> None:
    n = doc["counts"]
    where = f" ({doc['state_dir']})" if doc["state_dir"] else ""
    print(f"{doc['consumer']}: state {doc['state']}{where}: {n['open']} open, "
          f"{n['resolved']} resolved, {n['dismissed']} dismissed, "
          f"{n['superseded']} superseded")  # fmt: skip
    for q in doc["questions"]:
        sup = q.get("superseded")
        flag = f"  superseded by {sup['by'] or 'nothing'} ({sup['reason']})" if sup else ""
        ends = ", ".join(q["imports_affected"])
        print(f"{q['id']}  {q['status'].upper()}  {q['provider']} [{q['contract']}]  "
              f"{len(q['imports_affected'])} endpoint(s): {ends}{flag}")  # fmt: skip
        if q.get("resolution"):
            res = q["resolution"]
            print(f"  {res['status']} ({res['via']}) {res['at']}: {res['note']}".rstrip(": "))
    for p in doc["problems"]:
        print(f"  warning: [{p['code']}] {p['message']}")


def _questions(ns: argparse.Namespace, ws: wsmod.Workspace) -> int:
    from lattice.contracts import linked

    as_json = getattr(ns, "json", False)

    def fail(code: str, message: str, rc: int) -> int:
        if as_json:
            err = {"format": linked.FORMAT, "error": {"code": code, "message": message}}
            print(json.dumps(err, indent=1))
        else:
            print(f"lattice contracts questions: [{code}] {message}", file=sys.stderr)
        return rc

    if ws.errors:
        return fail("manifest_error", "; ".join(p.message for p in ws.errors), 2)
    wp = ws.project(ns.consumer)
    if wp is None:
        have = ", ".join(ws.names()) or "none"
        return fail("unknown_project", f"no project {ns.consumer!r} in the workspace "
                    f"(have {have})", 2)  # fmt: skip
    loc = linked.locate(wp.path, wp.name)
    if ns.qcmd == "init":
        try:
            d, created = linked.init_state(wp.path, wp.name)
        except linked.LinkedError as e:
            return fail(e.code, e.message, 2)
        if as_json:
            doc = {"format": linked.FORMAT, "consumer": wp.name, "state_dir": str(d),
                   "created": created}  # fmt: skip
            print(json.dumps(doc, indent=1))
        else:
            print(f"{wp.name}: state directory {d} ({'created' if created else 'exists'}); "
                  f"{linked.DIR}/ ready")  # fmt: skip
        return 0
    if ns.qcmd == "list":
        doc = linked.list_body(wp.name, loc.state, loc.state_dir, ns.status, loc.warnings)
        if as_json:
            print(json.dumps(doc, indent=1))
        else:
            _print_questions(doc)
        return 0
    status = linked.RESOLVED if ns.qcmd == "resolve" else linked.DISMISSED
    if loc.state != "present" or loc.state_dir is None:
        return fail("no_state", f"{wp.name} has no state directory ({loc.state})", 2)
    try:
        q = linked.clear(loc.state_dir, ns.id, status, ns.note, "cli")
    except linked.LinkedError as e:
        return fail(e.code, e.message, 1 if e.code in ("not_open", "bad_note") else 2)
    if as_json:
        print(json.dumps(q, indent=1))
    else:
        print(f"{q['id']}: {q['status']} (advisory: nothing was re-pinned; "
              f"`lattice contracts check {wp.name}` shows the link now)")  # fmt: skip
    return 0


def _workspace(ns: argparse.Namespace) -> wsmod.Workspace | int:
    path = wsmod.find(explicit=ns.workspace)
    if path is None:
        print(f"lattice contracts: no {wsmod.MANIFEST} found (walking up from {Path.cwd()}); "
              f"pass --workspace PATH or set {wsmod.ENV}", file=sys.stderr)  # fmt: skip
        return 2
    try:
        return wsmod.parse(path)
    except wsmod.ManifestUnreadable as e:
        print(f"lattice contracts: {e}", file=sys.stderr)
        return 2


def _export(ns: argparse.Namespace) -> int:
    project, ref = ns.project, ns.spec
    exclude: list[str] = ns.exclude or []
    keys, reasons = ex.check_exclude(exclude)
    if reasons:
        print(f"lattice contracts export: {'; '.join(reasons)}", file=sys.stderr)
        return 2
    if project is None:
        if exclude and ns.name:
            print("lattice contracts export: the exclusion of a workspace project comes from "
                  "its manifest (exclude), not from --exclude", file=sys.stderr)  # fmt: skip
            return 2
        if not ns.name:
            print("lattice contracts export: give a project NAME (or --project DIR)",
                  file=sys.stderr)  # fmt: skip
            return 2
        ws = _workspace(ns)
        if isinstance(ws, int):
            return ws
        wp = ws.project(ns.name)
        if wp is None:
            print(f"lattice contracts: no project {ns.name!r} in the workspace "
                  f"(have {', '.join(ws.names()) or 'none'})", file=sys.stderr)  # fmt: skip
            return 2
        project, ref = str(wp.path), ref or wp.spec
        try:
            out = ex.compute_export(project, ref, wp.exclude or ())
        except ex.ExportFailed as e:
            print(f"lattice contracts: {e}", file=sys.stderr)
            return 2
    else:  # the subprocess entry of compute_export, or a direct call
        try:
            out = ex.build_export(Path(project).resolve(), ref or ex.DEFAULT_SPEC, keys)
        except Exception as e:  # noqa: BLE001 - any failure of the provider's own code
            print(f"lattice contracts: {type(e).__name__}: {e}", file=sys.stderr)
            return 2
    print(ex.canonical(out))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ns = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    if ns.cmd == "export":
        return _export(ns)
    ws = _workspace(ns)
    if isinstance(ws, int):
        return ws
    if ns.cmd == "impact":
        from lattice.contracts import impact

        rep = impact.run_impact(ws, ns.provider, ns.candidate, base=Path.cwd(),
                                transitive=ns.transitive)  # fmt: skip
        if ns.json:
            print(json.dumps(rep.to_dict(), indent=1))
        else:
            _print_impact(rep)
            if rep.transitive is not None:
                _print_transitive(rep.transitive)
        return rep.exit_code
    if ns.cmd == "notify":
        from lattice.contracts import linked

        doc = linked.notify(ws, ns.provider, ns.candidate, via="cli", base=Path.cwd(),
                            dry_run=ns.dry_run)  # fmt: skip
        if ns.json:
            print(json.dumps(doc, indent=1))
        else:
            linked.print_notify(doc)
        return int(doc["exit_code"])
    if ns.cmd == "questions":
        return _questions(ns, ws)
    from lattice.contracts import check

    if ns.cmd == "pin":
        report = check.run_pin(ws, ns.consumer, force=ns.force)
        render = _print_pin
    else:
        report = check.run_check(ws, ns.consumer, static_only=ns.static_only)
        render = _print_check
    if getattr(ns, "json", False):
        print(json.dumps(report.to_dict(), indent=1))
    else:
        render(report)
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
