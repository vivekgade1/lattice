"""A workspace: several Lattice projects and the links between them (``lattice.workspace.toml``).

Layer 2 of the multi-project phase (docs/design/lattice-multi-project-phase.md). The manifest::

    [workspace]
    name = "shop"

    [[project]]
    name = "billing-api"          # unique, [a-z0-9._-]+
    path = "../billing"           # relative to the manifest (an absolute path also works)
    role = "provider"             # optional: provider | consumer | library | app

    [[link]]
    from = "web-app"
    to = "billing-api"
    kind = "uses"                 # uses | depends_on
    note = "POST /invoices"       # optional
    contract = "static"           # optional: static | live (opt-in contract check, layer 3)

A link ``from -> to`` says *from* relies on *to*: when *to* changes, *from* may be affected.
:meth:`Workspace.impact_of` is that relation, transitively; it is graph traversal over the
declared links only (no contract is checked: that is layer 3).

Layer 3 (contracts between projects, :mod:`lattice.contracts`) adds optional keys, all emitted by
``to_dict`` and ``show`` only when set (a manifest without them prints byte-identically):

* ``[[link]] contract``: ``"static"`` or ``"live"`` opts the link in to ``lattice contracts check``
  (absent: never checked, the provider's spec is never loaded). Any other value or type gives the
  error ``bad_contract`` and the link stays listed. ``live_url`` (a string) is read by the live
  check only, which refuses it (``not_loopback`` / ``live_url_unsupported``).
* ``[[project]] spec`` (``module:attr``, default ``spec:SPEC``), and for live checks ``app``,
  ``db``, ``login``, ``src`` (strings) and ``env`` (a table of strings; ``$PROJECT`` expands to the
  project directory). A wrong type gives ``bad_type``.

Problems (:class:`Problem`, ``severity`` error or warning): ``unknown_project`` (a link end that is
not a declared project), ``duplicate_name``, ``self_link``, ``depends_on_cycle`` (a cycle made of
``depends_on`` links; ``uses`` links may form cycles), ``bad_name``, ``bad_role``, ``bad_kind``,
``missing_field``, ``bad_type``, ``bad_contract``, and the warning ``missing_path`` (a declared
directory that does not exist). :func:`load` raises :class:`ManifestInvalid` on an error,
:class:`ManifestUnreadable` when the file cannot be read or is not TOML; :func:`parse` returns the
workspace and every problem without raising on content.

Finding the manifest (:func:`find`): an explicit path (the file, or a directory holding it), else
``$LATTICE_WORKSPACE``, else the first ``lattice.workspace.toml`` walking up from the start
directory (the workspace usually sits above several repositories, so the walk crosses them).

CLI: ``python -m lattice.workspace show|validate|impact NAME [--json] [--workspace PATH]`` (also
``lattice workspace ...``). Exit 0 ok (warnings allowed), 1 problems (or an unknown project), 2
the manifest cannot be found or read.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

MANIFEST = "lattice.workspace.toml"
ENV = "LATTICE_WORKSPACE"
NAME_RE = re.compile(r"[a-z0-9._-]+")
ROLES = ("provider", "consumer", "library", "app")
KINDS = ("uses", "depends_on")
CONTRACTS = ("static", "live")
# [[project]] keys that are plain strings (layer 3: where the provider's spec and app come from)
PROJECT_STRINGS = ("spec", "app", "db", "login", "src")

ERROR, WARNING = "error", "warning"


@dataclasses.dataclass(frozen=True)
class Problem:
    code: str
    message: str
    severity: str = ERROR

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "severity": self.severity, "message": self.message}


@dataclasses.dataclass(frozen=True)
class WsProject:
    name: str
    path: Path  # resolved, absolute
    role: str | None = None
    declared: str = ""  # the path as written in the manifest
    # layer 3, None when the manifest does not say (see the module docstring)
    spec: str | None = None
    app: str | None = None
    db: str | None = None
    login: str | None = None
    src: str | None = None
    env: dict[str, str] | None = None

    @property
    def exists(self) -> bool:
        return self.path.is_dir()

    def settings(self) -> dict[str, Any]:
        """The layer 3 keys that are set, as written (``env`` unexpanded)."""
        out: dict[str, Any] = {k: getattr(self, k) for k in PROJECT_STRINGS}
        out["env"] = self.env
        return {k: v for k, v in out.items() if v is not None}

    def expanded_env(self) -> dict[str, str]:
        """``env`` with ``$PROJECT`` replaced by the project directory."""
        return {k: v.replace("$PROJECT", str(self.path)) for k, v in (self.env or {}).items()}

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "path": str(self.path), "role": self.role}
        out.update(self.settings())
        return out


@dataclasses.dataclass(frozen=True)
class Link:
    source: str  # "from" in the manifest
    target: str  # "to"
    kind: str
    note: str = ""
    contract: str | None = None  # layer 3 opt-in: "static" | "live" | None (never checked)
    live_url: str | None = None  # read by the live check only (refused there)

    def to_dict(self) -> dict[str, str]:
        out = {"from": self.source, "to": self.target, "kind": self.kind, "note": self.note}
        if self.contract is not None:
            out["contract"] = self.contract
        if self.live_url is not None:
            out["live_url"] = self.live_url
        return out


class WorkspaceError(Exception):
    """Base of the workspace errors."""


class ManifestUnreadable(WorkspaceError):
    """The manifest cannot be found, read or parsed as TOML."""


class ManifestInvalid(WorkspaceError):
    """The manifest has at least one error; ``problems`` lists every problem."""

    def __init__(self, problems: Sequence[Problem]) -> None:
        self.problems = list(problems)
        errs = [p for p in self.problems if p.severity == ERROR]
        super().__init__("; ".join(p.message for p in errs) or "invalid workspace manifest")


@dataclasses.dataclass
class Workspace:
    name: str
    path: Path  # the manifest file
    projects: list[WsProject]
    links: list[Link]
    problems: list[Problem] = dataclasses.field(default_factory=list)

    @property
    def root(self) -> Path:
        return self.path.parent

    @property
    def errors(self) -> list[Problem]:
        return [p for p in self.problems if p.severity == ERROR]

    @property
    def warnings(self) -> list[Problem]:
        return [p for p in self.problems if p.severity == WARNING]

    def names(self) -> list[str]:
        return [p.name for p in self.projects]

    def project(self, name: str) -> WsProject | None:
        return next((p for p in self.projects if p.name == name), None)

    def by_path(self, path: Path | str) -> WsProject | None:
        try:
            real = Path(path).resolve()
        except OSError:
            return None
        return next((p for p in self.projects if p.path == real), None)

    def links_of(self, name: str) -> list[Link]:
        """The links with `name` at either end, in manifest order."""
        return [lk for lk in self.links if name in (lk.source, lk.target)]

    def impact_of(self, name: str) -> list[str]:
        """Who is affected if `name` changes: the projects that rely on it through links, directly
        or transitively, in breadth-first order (`name` itself excluded). Unknown name: KeyError."""
        if self.project(name) is None:
            raise KeyError(name)
        known = set(self.names())
        dependents: dict[str, list[str]] = {}
        for lk in self.links:
            if lk.source in known and lk.target in known and lk.source != lk.target:
                dependents.setdefault(lk.target, []).append(lk.source)
        order: list[str] = []
        seen = {name}
        frontier = [name]
        while frontier:
            nxt: list[str] = []
            for n in frontier:
                for d in dependents.get(n, []):
                    if d not in seen:
                        seen.add(d)
                        order.append(d)
                        nxt.append(d)
            frontier = nxt
        return order

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": {"name": self.name, "path": str(self.path)},
            "projects": [p.to_dict() for p in self.projects],
            "links": [lk.to_dict() for lk in self.links],
            "problems": [p.to_dict() for p in self.problems],
        }


# ---- finding and parsing ---------------------------------------------------------------------


def find(start: Path | str | None = None, explicit: Path | str | None = None) -> Path | None:
    """The manifest in force (see the module docstring), or None. An explicit path or
    ``$LATTICE_WORKSPACE`` that names a directory means the manifest inside it; either is
    returned even when it does not exist (reading it then fails loudly, not silently)."""
    for given in (explicit, os.environ.get(ENV) or None):
        if given:
            p = Path(given).expanduser()
            return p / MANIFEST if p.is_dir() else p
    here = Path.cwd() if start is None else Path(start)
    try:
        here = here.resolve()
    except OSError:
        return None
    for d in (here, *here.parents):
        cand = d / MANIFEST
        if cand.is_file():
            return cand
    return None


def _cycle(links: Sequence[Link], names: set[str]) -> list[str] | None:
    """One cycle made of depends_on links (a list of names, first == last), or None."""
    adj: dict[str, list[str]] = {}
    for lk in links:
        if lk.kind == "depends_on" and lk.source != lk.target and {lk.source, lk.target} <= names:
            adj.setdefault(lk.source, []).append(lk.target)
    color: dict[str, int] = {}  # 1 on the stack, 2 done
    stack: list[str] = []

    def visit(n: str) -> list[str] | None:
        color[n] = 1
        stack.append(n)
        for m in adj.get(n, []):
            if color.get(m) == 1:
                return [*stack[stack.index(m) :], m]
            if m not in color:
                found = visit(m)
                if found:
                    return found
        stack.pop()
        color[n] = 2
        return None

    for n in sorted(adj):
        if n not in color:
            found = visit(n)
            if found:
                return found
    return None


def parse(path: Path | str) -> Workspace:
    """The workspace of the manifest at `path`, with every problem in ``problems`` (never raises
    on content). Unreadable or not TOML: :class:`ManifestUnreadable`."""
    path = Path(path).expanduser()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        raise ManifestUnreadable(f"cannot read {path}: {e}") from None
    except tomllib.TOMLDecodeError as e:
        raise ManifestUnreadable(f"{path} is not valid TOML: {e}") from None
    path = path.resolve()
    probs: list[Problem] = []
    ws = data.get("workspace")
    name = ws.get("name") if isinstance(ws, dict) else None
    if not isinstance(name, str) or not name.strip():
        name = path.parent.name
    raw_projects = data.get("project", [])
    if not isinstance(raw_projects, list):
        probs.append(Problem("bad_type", "[[project]] must be an array of tables"))
        raw_projects = []
    projects: list[WsProject] = []
    taken: set[str] = set()
    for i, item in enumerate(raw_projects, 1):
        if not isinstance(item, dict):
            probs.append(Problem("bad_type", f"project #{i} is not a table"))
            continue
        pname, ppath, role = item.get("name"), item.get("path"), item.get("role")
        if not isinstance(pname, str) or not pname:
            probs.append(Problem("missing_field", f"project #{i} has no name"))
            continue
        if not NAME_RE.fullmatch(pname):
            probs.append(Problem("bad_name", f"project name {pname!r} must match [a-z0-9._-]+"))
            continue
        if not isinstance(ppath, str) or not ppath.strip():
            probs.append(Problem("missing_field", f"project {pname} has no path"))
            continue
        if role is not None and role not in ROLES:
            probs.append(Problem("bad_role", f"project {pname}: role {role!r} is not one of "
                                 f"{', '.join(ROLES)}"))  # fmt: skip
            role = None
        if pname in taken:
            probs.append(Problem("duplicate_name", f"project name {pname} is declared twice"))
            continue
        taken.add(pname)
        real = (path.parent / Path(ppath.strip()).expanduser()).resolve()
        extra: dict[str, Any] = {}
        for key in PROJECT_STRINGS:
            val = item.get(key)
            if val is None:
                continue
            if isinstance(val, str) and val.strip():
                extra[key] = val.strip()
            else:
                probs.append(Problem("bad_type", f"project {pname}: {key} must be a non-empty "
                                     "string"))  # fmt: skip
        env = item.get("env")
        if env is not None:
            if isinstance(env, dict) and all(isinstance(v, str) for v in env.values()):
                extra["env"] = {str(k): v for k, v in env.items()}
            else:
                probs.append(Problem("bad_type", f"project {pname}: env must be a table of "
                                     "strings"))  # fmt: skip
        wp = WsProject(pname, real, role, ppath.strip(), **extra)
        if not wp.exists:
            probs.append(Problem("missing_path", f"project {pname}: {real} does not exist",
                                 WARNING))  # fmt: skip
        projects.append(wp)
    raw_links = data.get("link", [])
    if not isinstance(raw_links, list):
        probs.append(Problem("bad_type", "[[link]] must be an array of tables"))
        raw_links = []
    links: list[Link] = []
    for i, item in enumerate(raw_links, 1):
        if not isinstance(item, dict):
            probs.append(Problem("bad_type", f"link #{i} is not a table"))
            continue
        src, dst, kind, note = item.get("from"), item.get("to"), item.get("kind"), item.get("note")
        if not isinstance(src, str) or not isinstance(dst, str) or not src or not dst:
            probs.append(Problem("missing_field", f"link #{i} needs from and to"))
            continue
        if kind not in KINDS:
            probs.append(Problem("bad_kind", f"link {src} -> {dst}: kind {kind!r} is not one of "
                                 f"{', '.join(KINDS)}"))  # fmt: skip
            continue
        for end in (src, dst):
            if end not in taken:
                probs.append(Problem("unknown_project", f"link {src} -> {dst}: {end} is not a "
                                     "declared project"))  # fmt: skip
        if src == dst:
            probs.append(Problem("self_link", f"link {src} -> {dst}: a project cannot link to "
                                 "itself"))  # fmt: skip
        contract = item.get("contract")
        if contract is not None and contract not in CONTRACTS:  # a non-string never matches
            probs.append(Problem("bad_contract", f"link {src} -> {dst}: contract {contract!r} is "
                                 f"not one of {', '.join(CONTRACTS)}"))  # fmt: skip
            contract = None
        live_url = item.get("live_url")
        if live_url is not None and not isinstance(live_url, str):
            probs.append(Problem("bad_type", f"link {src} -> {dst}: live_url must be a string"))
            live_url = None
        # kept even when bad (listed with its problem); impact_of and the cycle check skip it
        links.append(Link(src, dst, kind, note if isinstance(note, str) else "", contract,
                          live_url))  # fmt: skip
    cyc = _cycle(links, taken)
    if cyc:
        probs.append(Problem("depends_on_cycle", "depends_on links form a cycle: "
                             + " -> ".join(cyc)))  # fmt: skip
    return Workspace(name.strip(), path, projects, links, probs)


def load(path: Path | str) -> Workspace:
    """The workspace at `path`; raises :class:`ManifestInvalid` on any error (warnings pass) and
    :class:`ManifestUnreadable` when it cannot be read."""
    ws = parse(path)
    if ws.errors:
        raise ManifestInvalid(ws.problems)
    return ws


def try_load(path: Path | None) -> tuple[Workspace | None, list[Problem]]:
    """``(workspace or None, problems)`` for a manifest that may be absent or broken: never raises.
    An unreadable manifest gives ``(None, [unreadable])``; an invalid one its best-effort
    workspace with the problems."""
    if path is None:
        return None, []
    try:
        ws = parse(path)
    except ManifestUnreadable as e:
        return None, [Problem("unreadable", str(e))]
    return ws, list(ws.problems)


# ---- CLI -------------------------------------------------------------------------------------


def _print_problems(problems: Sequence[Problem]) -> None:
    for p in problems:
        print(f"  {p.severity}: [{p.code}] {p.message}")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m lattice.workspace",
        description="Show, validate or query a workspace manifest (lattice.workspace.toml).",
    )
    ap.add_argument("--workspace", default=None, help="the manifest (or its directory); default: "
                    f"${ENV}, else {MANIFEST} walking up from the cwd")  # fmt: skip
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, hlp in (("show", "the projects, links and problems"),
                      ("validate", "exit 0 when the manifest has no error")):  # fmt: skip
        sp = sub.add_parser(name, help=hlp)
        sp.add_argument("--json", action="store_true")
        sp.add_argument("--workspace", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    ip = sub.add_parser("impact", help="who is affected if NAME changes (transitive, over links)")
    ip.add_argument("name")
    ip.add_argument("--json", action="store_true")
    ip.add_argument("--workspace", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    ns = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    path = find(explicit=ns.workspace)
    if path is None:
        print(f"lattice workspace: no {MANIFEST} found (walking up from {Path.cwd()}); pass "
              f"--workspace PATH or set {ENV}", file=sys.stderr)  # fmt: skip
        return 2
    try:
        ws = parse(path)
    except ManifestUnreadable as e:
        print(f"lattice workspace: {e}", file=sys.stderr)
        return 2
    rc = 1 if ws.errors else 0
    if ns.cmd == "impact":
        if ws.project(ns.name) is None:
            msg = (
                f"no project {ns.name!r} in the workspace (have {', '.join(ws.names()) or 'none'})"
            )
            if ns.json:
                print(json.dumps({"error": msg, "problems": [p.to_dict() for p in ws.problems]}))
            else:
                print(f"lattice workspace: {msg}", file=sys.stderr)
            return 1
        impacted = ws.impact_of(ns.name)
        if ns.json:
            out = {"workspace": ws.name, "project": ns.name, "impacted": impacted,
                   "problems": [p.to_dict() for p in ws.problems]}  # fmt: skip
            print(json.dumps(out, indent=1))
        else:
            print(f"{ns.name} changes -> {', '.join(impacted) if impacted else 'no other project'}"
                  " (declared links only)")  # fmt: skip
            if ws.problems:
                print("problems:")
                _print_problems(ws.problems)
        return rc
    if ns.json:
        print(json.dumps({**ws.to_dict(), "ok": not ws.errors}, indent=1))
        return rc
    if ns.cmd == "validate":
        if ws.errors:
            print(f"{ws.path}: {len(ws.errors)} error(s)")
        else:
            print(f"{ws.path}: ok ({len(ws.projects)} project(s), {len(ws.links)} link(s)"
                  + (f", {len(ws.warnings)} warning(s)" if ws.warnings else "") + ")")  # fmt: skip
        _print_problems(ws.problems)
        return rc
    print(f"workspace {ws.name}  ({ws.path})")
    w = max((len(p.name) for p in ws.projects), default=4)
    for p in ws.projects:
        state = "" if p.exists else "  (missing)"
        print(f"  {p.name:<{w}}  {p.role or '-':<8}  {p.path}{state}")
        for key, val in p.settings().items():
            print(f"  {'':<{w}}    {key} = {json.dumps(val) if key == 'env' else val}")
    if ws.links:
        print("links:")
        for lk in ws.links:
            print(
                f"  {lk.source} --{lk.kind}--> {lk.target}"
                + (f"  ({lk.note})" if lk.note else "")
                + (f"  [contract: {lk.contract}]" if lk.contract else "")
                + (f"  [live_url: {lk.live_url}]" if lk.live_url else "")
            )
    if ws.problems:
        print("problems:")
        _print_problems(ws.problems)
    return rc


if __name__ == "__main__":
    sys.exit(main())
