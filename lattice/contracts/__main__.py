"""``lattice contracts export|pin|check``: contracts between projects (multi-project layer 3).

    lattice contracts export NAME [--spec REF] [--workspace PATH]    # the provider's export
    lattice contracts export --project DIR [--spec REF]              # ... without a workspace
    lattice contracts pin CONSUMER [--force] [--json] [--workspace PATH]
    lattice contracts check [CONSUMER] [--json] [--static-only] [--workspace PATH]

``export`` prints the canonical JSON ``{"export", "spec_ref", "version"}`` (exit 0, or 2 when the
spec cannot be loaded). ``pin`` writes the consumer's ``lattice.contracts.lock.json`` (exit 0),
refuses while a link has a BROKEN finding unless ``--force`` (exit 1), exit 2 when it cannot run.
``check`` exits 0 when clean, 1 on any BROKEN or CHANGED, 2 when it cannot run (several links: 2
over 1 over 0). Only links with ``contract = "static"`` or ``"live"`` in the workspace manifest
are looked at; ``--static-only`` skips the live part. See :mod:`lattice.contracts.check`.
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


def _print_pin(report: Any) -> None:
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
        elif lk["forced"]:
            print("  forced: the BROKEN items above are now gaps")
    for p in report.problems:
        print(f"  {p.severity}: [{p.code}] {p.message}")
    if report.extra.get("written"):
        print(f"wrote {report.extra['lock']}")


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
    if project is None:
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
            out = ex.compute_export(project, ref)
        except ex.ExportFailed as e:
            print(f"lattice contracts: {e}", file=sys.stderr)
            return 2
    else:  # the subprocess entry of compute_export, or a direct call
        try:
            out = ex.build_export(Path(project).resolve(), ref or ex.DEFAULT_SPEC)
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
