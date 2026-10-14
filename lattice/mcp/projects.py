"""Many projects behind one MCP server (layer 1 of the multi-project phase,
docs/design/lattice-multi-project-phase.md).

**Who is known** (:func:`known_projects`, discovery shared with Studio through
:func:`lattice.studio.hub.discover_sourced`):

- with ``--project`` given: those directories (source ``explicit``) and the workspace's projects
  (``workspace``), nothing else, so a single-``--project`` server stays a single-project server;
- with no ``--project``: the launch directory (``launch``, listed even before it has a spec),
  Studio's discovery over it (``discovered``: the directory, its children, ``dogfood/*``), the
  workspace's projects (``workspace``) and the registry file's (``registry``).

Duplicates (by resolved path) are dropped, the first source wins (a project the workspace declares
is labelled ``workspace`` rather than ``discovered``/``registry``), and a project the workspace
declares always carries its manifest name and role.

**The router** (:class:`ProjectRouter`) has the :class:`~lattice.mcp.tools.Toolbox` interface
(``list_tools``, ``names``, ``call``), so :class:`~lattice.mcp.server.McpServer` serves it as is.
Each project gets its own Toolbox (and backend), built on first use. Every existing tool takes an
optional ``project`` (a name or a path as ``project_list`` gives it; default: the active project,
which only ``project_set_active`` changes, for the life of the process). ``project_list`` and
``project_set_active`` are always listed (also ``--readonly``); ``workspace_impact``,
``contract_check`` (layer 3) and ``contract_impact`` (layer 4a) only when a workspace is known.

**Single-project compatibility.** With exactly one known project the existing tools' definitions,
the ``initialize`` instructions and every result are exactly the single-project server's: the
``project`` argument is accepted but advertised in ``inputSchema`` only when several projects are
known, the two project tools are appended after the existing ones, and no result gains a field.
With several projects every result names the project it ran on in ``project`` (a result that
already had a ``project`` key keeps that value under ``project_info``), and a tool error ends with
``(project: NAME)``.
"""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from lattice.mcp.tools import SPEC_FILES, Toolbox, ToolError

PROJECT_PROP = {
    "type": "string",
    "description": "the project to run on: a name or path from project_list (default: the "
    "active project)",
}
WORKSPACE_TOOLS = (  # listed only with a workspace
    "workspace_impact",
    "contract_check",
    "contract_impact",
)
OWN_TOOLS = ("project_list", "project_set_active", *WORKSPACE_TOOLS)
MULTI_NOTE = (
    " Several projects are known: project_list lists them; every tool takes an optional "
    "`project` (a name or path from project_list; default: the active project, which "
    "project_set_active changes)."
)


@dataclasses.dataclass(frozen=True)
class KnownProject:
    name: str
    root: Path
    source: str  # launch | explicit | discovered | registry | workspace
    role: str | None = None

    @property
    def spec(self) -> str:
        return "present" if all((self.root / n).is_file() for n in SPEC_FILES) else "missing"


def known_projects(
    explicit: Sequence[Path] = (),
    *,
    launch: Path,
    workspace: Any = None,
    registry: Path | None = None,
) -> list[KnownProject]:
    """The projects one server knows (see the module docstring)."""
    from lattice.studio.hub import discover_sourced

    if explicit:
        found = discover_sourced(
            (),
            first=[(Path(p), "explicit") for p in explicit],
            workspace=workspace,
            scan=False,
            projects_only=False,
        )
    else:
        found = discover_sourced(
            (),
            cwd=launch,
            registry=registry,
            workspace=workspace,
            first=[(launch, "launch")],
            projects_only=False,
        )
    roles = {wp.path: wp.role for wp in workspace.projects} if workspace is not None else {}
    return [KnownProject(n, r, s, roles.get(r)) for n, r, s in found]


class ProjectRouter:
    """The tools over many projects (see the module docstring). `make_backend(project)` builds a
    project's backend; the first project's Toolbox is built now (its tool definitions are every
    project's), the others on first use."""

    def __init__(
        self,
        projects: Sequence[KnownProject],
        make_backend: Callable[[KnownProject], Any],
        *,
        readonly: bool = False,
        workspace: Any = None,
        workspace_path: Path | None = None,
        workspace_problems: Sequence[Any] = (),
        launch: Path | None = None,
    ) -> None:
        if not projects:
            raise ValueError("no projects")
        self.projects: dict[str, KnownProject] = {p.name: p for p in projects}
        self.active = projects[0].name
        self.make_backend = make_backend
        self.readonly = readonly
        self.workspace = workspace
        self.workspace_path = workspace_path
        self.workspace_problems = list(workspace_problems)
        self.launch = (launch or Path.cwd()).resolve()
        self._boxes: dict[str, Toolbox] = {}
        self._template = self.box(self.active)
        self._own: dict[str, dict[str, Any]] = {}
        self._build_own()

    # -- projects
    @property
    def multi(self) -> bool:
        return len(self.projects) > 1

    def box(self, name: str) -> Toolbox:
        b = self._boxes.get(name)
        if b is None:
            b = self._boxes[name] = Toolbox(
                self.make_backend(self.projects[name]), readonly=self.readonly
            )
        return b

    def _unknown(self, value: str) -> ToolError:
        return ToolError(
            f"unknown project {value!r}; known projects: {', '.join(self.projects)} "
            "(project_list shows their paths)"
        )

    def resolve(self, value: Any) -> KnownProject:
        """The project a ``project`` argument names (None or "": the active one)."""
        if value is None or value == "":
            return self.projects[self.active]
        if not isinstance(value, str):
            raise ToolError("argument 'project' must be a string")
        v = value.strip()
        if v in self.projects:
            return self.projects[v]
        p = Path(v).expanduser()
        if not p.is_absolute():
            p = self.launch / p
        try:
            real = p.resolve()
        except OSError:
            raise self._unknown(value) from None
        for e in self.projects.values():
            if e.root == real:
                return e
        raise self._unknown(value)

    # -- the Toolbox interface
    def instructions(self, base: str) -> str:
        return base + MULTI_NOTE if self.multi else base

    def list_tools(self) -> list[dict[str, Any]]:
        defs = self._template.list_tools()
        if self.multi:
            defs = [_with_project(d) for d in defs]
        own = [t["def"] for n, t in self._own.items() if n not in WORKSPACE_TOOLS or self.workspace]
        return [*defs, *own]

    def names(self) -> list[str]:
        return [d["name"] for d in self.list_tools()]

    def call(self, name: str, args: Mapping[str, Any] | None) -> dict[str, Any]:
        own = self._own.get(name)
        if own is not None:
            if name in WORKSPACE_TOOLS and self.workspace is None:
                raise KeyError(name)
            return self._call_own(own, args)
        if name not in self._template.names():
            raise KeyError(name)
        if args is not None and not isinstance(args, Mapping):
            raise ToolError("arguments must be an object")
        rest = dict(args or {})
        entry = self.resolve(rest.pop("project", None))
        try:
            out = self.box(entry.name).call(name, rest)
        except ToolError as e:
            if self.multi:
                raise ToolError(f"{e} (project: {entry.name})") from None
            raise
        if self.multi and isinstance(out, Mapping):
            out = _tagged(out, entry.name)
        return out

    # -- the project tools
    def _call_own(self, tool: dict[str, Any], args: Mapping[str, Any] | None) -> dict[str, Any]:
        if args is not None and not isinstance(args, Mapping):
            raise ToolError("arguments must be an object")
        args = dict(args or {})
        props = tool["def"]["inputSchema"]["properties"]
        unknown = sorted(set(args) - set(props))
        if unknown:
            raise ToolError(f"unknown argument(s): {', '.join(unknown)}")
        return tool["fn"](args)

    def _reg(
        self,
        name: str,
        description: str,
        props: dict[str, Any],
        fn: Callable,
        required: Sequence[str] = (),
    ) -> None:
        self._own[name] = {
            "def": {
                "name": name,
                "description": description,
                "inputSchema": {
                    "type": "object",
                    "properties": props,
                    "required": list(required),
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": True, "openWorldHint": False},
            },
            "fn": fn,
        }

    def _row(self, p: KnownProject) -> dict[str, Any]:
        row: dict[str, Any] = {
            "name": p.name,
            "path": str(p.root),
            "spec": p.spec,
            "active": p.name == self.active,
            "source": p.source,
        }
        if self.workspace is not None:
            wp = self.workspace.by_path(p.root)
            row["role"] = p.role
            row["links"] = [lk.to_dict() for lk in self.workspace.links_of(wp.name)] if wp else []
        return row

    def _build_own(self) -> None:
        def project_list(_a: Mapping[str, Any]) -> dict[str, Any]:
            out: dict[str, Any] = {
                "active": self.active,
                "projects": [self._row(p) for p in self.projects.values()],
            }
            if self.workspace is not None or self.workspace_problems:
                ws = self.workspace
                out["workspace"] = {
                    "name": ws.name if ws is not None else None,
                    "path": str(ws.path if ws is not None else self.workspace_path),
                    "problems": [p.to_dict() for p in self.workspace_problems],
                }
            return out

        self._reg(
            "project_list",
            "The Lattice projects this server knows: name, path, spec (present|missing), "
            "active, source (launch|explicit|discovered|registry|workspace); with a workspace "
            "manifest also each project's role and links. Pass a name or path as `project` to "
            "any tool, or make one the default with project_set_active.",
            {},
            project_list,
        )

        def project_set_active(a: Mapping[str, Any]) -> dict[str, Any]:
            given = [k for k in ("name", "path") if a.get(k) not in (None, "")]
            if len(given) != 1:
                raise ToolError("pass exactly one of 'name' or 'path'")
            entry = self.resolve(a[given[0]])
            previous, self.active = self.active, entry.name
            return {
                "active": entry.name,
                "path": str(entry.root),
                "spec": entry.spec,
                "previous": previous,
                "note": "tools without a `project` argument now run on this project, until "
                "project_set_active is called again or the server restarts",
            }

        self._reg(
            "project_set_active",
            "Make a project the default for every tool called without `project` (for the life "
            "of this server). Pass its `name` or its `path` as project_list gives them.",
            {"name": {"type": "string"}, "path": {"type": "string"}},
            project_set_active,
        )

        def workspace_impact(a: Mapping[str, Any]) -> dict[str, Any]:
            ws = self.workspace
            entry = self.resolve(a.get("project"))
            wp = ws.by_path(entry.root) or ws.project(entry.name)
            if wp is None:
                raise ToolError(
                    f"project {entry.name} is not in the workspace {ws.name} "
                    f"(its projects: {', '.join(ws.names()) or 'none'})"
                )
            impacted = ws.impact_of(wp.name)
            reach = {wp.name, *impacted}
            via = [
                lk.to_dict()
                for lk in ws.links
                if lk.target in reach and lk.source in impacted and lk.source != lk.target
            ]
            return {
                "workspace": ws.name,
                "project": wp.name,
                "impacted": impacted,
                "via": via,
                "note": "who relies on this project through the manifest's declared links, "
                "directly or transitively; graph traversal only, no contract is checked",
            }

        self._reg(
            "workspace_impact",
            "Who is affected if a project changes: the workspace projects that rely on it "
            "through declared links (uses, depends_on), directly or transitively. `project`: "
            "a name or path (default: the active project). Graph traversal over the manifest "
            "only; no contract is checked.",
            {"project": dict(PROJECT_PROP)},
            workspace_impact,
        )

        def contract_check(a: Mapping[str, Any]) -> dict[str, Any]:
            from lattice import workspace as wsmod
            from lattice.contracts import check

            consumer = a.get("consumer")
            if consumer is not None and not isinstance(consumer, str):
                raise ToolError("argument 'consumer' must be a string")
            # the manifest is re-read on every call, so the answer is the CLI's at this moment
            ws, problems = wsmod.try_load(self.workspace.path)
            if ws is None:
                return {"workspace": None, "consumer": consumer or None, "checked": 0,
                        "links": [], "problems": [p.to_dict() for p in problems],
                        "elapsed_s": 0.0}  # fmt: skip
            return check.run_check(ws, consumer or None, static_only=True).to_dict()

        self._reg(
            "contract_check",
            "Check the contracts between the workspace's projects, statically: each consumer "
            "link that opted in (`contract` = static or live in lattice.workspace.toml) is "
            "compared, import by import, with the provider's pinned and current spec export. "
            "`consumer`: a project name (default: every consumer with an opted-in link). "
            "Returns what `lattice contracts check --static-only --json` prints: per link the "
            "status (ok|changed|broken|error), BROKEN and CHANGED findings, gaps, and for a "
            "live link `live: not_run`. Read-only: nothing is written and no app is started; "
            "the providers' spec modules are loaded in a subprocess.",
            {"consumer": {"type": "string", "description": "a project name of the workspace"}},
            contract_check,
        )

        def contract_impact(a: Mapping[str, Any]) -> dict[str, Any]:
            from lattice import workspace as wsmod
            from lattice.contracts import impact

            for key in ("provider", "candidate"):
                if not isinstance(a.get(key), str) or not a[key]:
                    raise ToolError(f"argument '{key}' is required (a non-empty string)")
            provider, candidate = a["provider"], a["candidate"]
            # the manifest is re-read on every call, so the answer is the CLI's at this moment
            ws, problems = wsmod.try_load(self.workspace.path)
            if ws is None:
                return impact.unavailable(provider, candidate, problems).to_dict()
            # a relative candidate directory is relative to the manifest's directory
            return impact.run_impact(ws, provider, candidate, base=ws.root).to_dict()

        self._reg(
            "contract_impact",
            "Preview what a candidate version of a provider would do to the consumers that rely "
            "on it, against each consumer's pin (advisory, read-only, static only). `provider`: a "
            "workspace project; `candidate`: a git ref of the provider's repository, or a "
            "directory (relative to the manifest's directory) holding its project. One row per "
            "consumer link that opted in (`contract` key): `contradiction` (a BROKEN reliance), "
            "`regression` (a relied CHANGED one) or `unaffected`, with the findings and gaps. "
            "Returns what `lattice contracts impact PROVIDER --candidate X --json` prints. "
            "Nothing is written or checked out and no app is started; the candidate's spec "
            "module is loaded in a subprocess.",
            {
                "provider": {"type": "string", "description": "a project name of the workspace"},
                "candidate": {
                    "type": "string",
                    "description": "a git ref of the provider's repository, or a directory",
                },
            },
            contract_impact,
            required=("provider", "candidate"),
        )


def _with_project(d: dict[str, Any]) -> dict[str, Any]:
    d = copy.deepcopy(d)
    d["inputSchema"]["properties"]["project"] = dict(PROJECT_PROP)
    return d


def _tagged(out: Mapping[str, Any], name: str) -> dict[str, Any]:
    new: dict[str, Any] = {"project": name}
    for k, v in out.items():
        if k == "project":
            if v != name:
                new["project_info"] = v
        else:
            new[k] = v
    return new
