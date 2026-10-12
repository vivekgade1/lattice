"""``python -m lattice.improve`` (``lattice improve``): the improvement report of a project's
event log, the import of an existing plan record, and the improvement ledger.

    lattice improve report      [--project DIR] [--state DIR] [--top N] [--json]
    lattice improve import-plan RECORD.json [--project DIR] [--state DIR] [--json]
    lattice improve ledger      [--project DIR] [--state DIR] [--json]
                                [--signal KEY --status open|closed [--id ID] [--commit SHA]]

The log is ``<state dir>/events.jsonl`` (Studio's state directory of the project: ``[state]
dir`` of its ``studio.toml``, else ``<repo root>/.lattice/state/<project>/``); the ledger is
``improvements.jsonl`` beside it. ``report`` states the method and its blind spots: the
ranking is an input to the owner, who decides. Exit codes: 0 done, 2 could not read (no such
project directory, an unreadable log, a record that is not a plan).
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from lattice.improve.analyze import IMPROVEMENTS_FILE, analyze, read_ledger, report_text
from lattice.improve.events import EVENTS_FILE, EventLog, read_events
from lattice.improve.importer import plan_events


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lattice improve", description="improvement proposals from the event log"
    )
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("report", help="rank the logged signals into at most N proposals")
    r.add_argument("--top", type=int, default=5, help="at most N proposals (default 5)")
    i = sub.add_parser("import-plan", help="turn a plan record (JSON) into events")
    i.add_argument("record", help="a plan record, e.g. GET /api/plan/{id} saved to a file")
    g = sub.add_parser("ledger", help="list the improvement ledger, or append one entry")
    g.add_argument("--signal", help="the signal key (fnmatch pattern) the entry is about")
    g.add_argument("--status", choices=("open", "closed"))
    g.add_argument("--id", dest="item", help="the improvement's id (e.g. I-05)")
    g.add_argument("--commit", help="the commit that closed it")
    for s in sub.choices.values():
        s.add_argument("--project", default=".", help="the project directory (default: cwd)")
        s.add_argument("--state", help="the state directory (default: the project's)")
        s.add_argument("--json", action="store_true")
    return p


def state_dir(project: Path, override: str | None = None, *, create: bool = False) -> Path:
    """The project's Studio state directory (as :func:`lattice.studio.state.state_dir_for`
    finds it; `create` also makes it and git-ignores it, as Studio does)."""
    if override:
        return Path(override).resolve()
    from lattice.studio import state as st

    cfg: dict = {}
    toml = project / "studio.toml"
    if toml.is_file():
        try:
            cfg = tomllib.loads(toml.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            cfg = {}
    state_t = cfg.get("state") if isinstance(cfg.get("state"), dict) else {}
    rebuild_t = cfg.get("rebuild") if isinstance(cfg.get("rebuild"), dict) else {}
    if create:
        d = st.state_dir_for(project, project.name, state_t, rebuild_t)
        d.mkdir(parents=True, exist_ok=True)
        return d
    if isinstance(state_t.get("dir"), str) and state_t["dir"].strip():
        return (project / state_t["dir"].strip()).resolve()
    root = st._toplevel((project / str(rebuild_t.get("repo") or ".")).resolve()) or project
    return root / st.STATE_DIR / st._slug(project.name)


def _err(msg: str) -> int:
    print(f"lattice improve: {msg}", file=sys.stderr)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    project = Path(args.project).resolve()
    if not project.is_dir():
        return _err(f"no project directory {project}")
    if args.command == "report":
        d = state_dir(project, args.state)
        path = d / EVENTS_FILE
        if path.exists() and not path.is_file():
            return _err(f"cannot read {path}")
        try:
            if path.exists():
                path.open("rb").close()
        except OSError as exc:
            return _err(f"cannot read {path}: {exc}")
        res = read_events(path)
        ledger, lw = read_ledger(d / IMPROVEMENTS_FILE)
        rep = analyze(res.events, ledger, args.top)
        rep["warnings"] = res.warnings + lw
        rep["log"] = str(path)
        print(
            json.dumps(rep, indent=1) if args.json else report_text(rep),
            end="" if not args.json else "\n",
        )
        return 0
    if args.command == "import-plan":
        try:
            rec = json.loads(Path(args.record).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return _err(f"cannot read {args.record}: {exc}")
        if not isinstance(rec, dict) or "nodes" not in rec or "id" not in rec:
            return _err(f"{args.record} is not a plan record (no id and nodes)")
        d = state_dir(project, args.state, create=True)
        evs = plan_events(rec, source=Path(args.record).name)
        known = {str(e.get("id")) for e in read_events(d / EVENTS_FILE).events}
        log = EventLog.in_dir(d, project.name)
        n = log.extend(evs, known)
        if log.warnings:
            return _err("; ".join(log.warnings))
        out = {
            "plan": rec.get("id"),
            "events": len(evs),
            "written": n,
            "skipped": len(evs) - n,
            "log": str(d / EVENTS_FILE),
        }
        print(
            json.dumps(out)
            if args.json
            else f"plan {out['plan']}: {n} event(s) written, {out['skipped']} already there "
            f"({out['log']})"
        )
        return 0
    # ledger
    d = state_dir(project, args.state, create=bool(args.signal))
    path = d / IMPROVEMENTS_FILE
    if args.signal:
        if not args.status:
            return _err("--signal needs --status open|closed")
        entry = {
            "id": args.item,
            "signal": args.signal,
            "status": args.status,
            "commit": args.commit,
            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as exc:
            return _err(f"cannot write {path}: {exc}")
        print(json.dumps(entry) if args.json else f"ledger: {entry['signal']} {entry['status']}")
        return 0
    entries, warnings = read_ledger(path)
    if args.json:
        print(json.dumps({"entries": entries, "warnings": warnings}, indent=1))
    else:
        for e in entries:
            print(
                f"{e.get('ts') or '-'}  {e['status']:<6}  {e['signal']}  {e.get('id') or ''} "
                f"{e.get('commit') or ''}".rstrip()
            )
        for w in warnings:
            print(f"warning: {w}")
        if not entries:
            print(f"(no entries in {path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
