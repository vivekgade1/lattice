"""The rules-first import for an ASP.NET Core + EF Core app (Spike J, .NET node B2): what
:func:`lattice.importer.rules.import_rules` needs beyond the Python path, the Go path
(:mod:`lattice.importer.go_rules`) with .NET inputs. ``harness_factory`` (the app factory of
``studio.toml`` ``[app] app``), ``server_dialect`` (the K4 ``not_run:server_dialect`` skip) and
``patch_profile`` (``validation_status`` and ``private`` in the generated profile) are the Go
ones, reused: none of them reads anything Go. Each inference below is recorded in ``inferences[]``
with its ``rule``; the extract itself (the sidecar, ``workflows.json``) is never touched.

* ``owner_uses`` (the D2 rule): a column ``c`` of table ``T`` is ``T``'s owner when some route's
  ``owner_uses`` has ``(T, c)`` as a ``compare``, ``assign`` or ``filter`` (a global query filter
  is owner evidence for every query on its entity, N8), ``c`` has the type of the users table's
  primary key, ``T`` is not the users table, and no other column of ``T`` qualifies. A column
  EF Core already declares a foreign key to the users table for is *confirmed* (``declared``,
  the model unchanged); otherwise the model copy gets an inferred foreign key (two candidates:
  ``unclear``, both columns named).
* ``route_constraint``: a path parameter the route template constrains (``{id:int}``, ``{id:
  guid}``) is typed by the extract; the entry records the evidence that the framework refuses a
  mismatch (the ``path_parse`` analogue).
* ``bind_status``: the one constant status of the module's bind refusals (``raises[].bind``,
  policy extract), else the module's own (``dotnet.bind_status``: 400 for ``[ApiController]`` and
  ``AddValidation()``); 400 and 422 are the two a profile can state.
* ``enum_choices``: a status-like column's ``choices`` (an enum's members from the extract, or the
  constants written / named as ``from`` states, default first, at least two: the K5 rule).
* ``json_ignore``: the columns a ``[JsonIgnore]`` hides (never the primary key) are the table's
  ``private`` columns in the generated profile. A private column is named by its SQL name (a
  profile checks its private columns against the schema).
* ``json_naming``: the ``System.Text.Json`` policy (``dotnet.json_naming``) and the columns whose
  wire name differs from their SQL name.
* ``body_model`` (K3, the Go function reused): a body read field by field is handed to the contract
  builder as one parameter typed by a model.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lattice.importer.appmodel import RepoModel, TableModel
from lattice.importer.go_rules import (
    harness_factory,
    infer_bind_status,
    infer_choices,
    patch_profile,
    server_dialect,
)

__all__ = [
    "InferringFrontEnd",
    "harness_factory",
    "infer_bind",
    "infer_choices_dn",
    "infer_constraints",
    "infer_naming",
    "infer_owners",
    "infer_private",
    "patch_profile",
    "server_dialect",
    "wire_name",
]

RULE = "owner_uses"
OWNER_HOWS = ("compare", "assign", "filter")


def _evidence(sites: Any) -> list[str]:
    return [f"code:{f}:{n}" for f, n in sorted(sites)]


def infer_owners(
    extract: Mapping[str, Any], model: RepoModel
) -> tuple[RepoModel, list[dict[str, Any]]]:
    """``model`` with the inferred users foreign keys, and the ``inferences`` (module docstring)."""
    auth = model.auth
    users = next((t for t in model.tables if auth is not None and t.name == auth.users_table), None)
    pk = next((c for c in users.columns if c.primary_key), None) if users else None
    if users is None or pk is None:
        return model, []
    uses: dict[tuple[str, str], set[tuple[str, int]]] = {}
    for route in extract.get("routes", ()):
        for o in route.get("owner_uses", ()):
            if o.get("how") in OWNER_HOWS:
                uses.setdefault((o["model"], o["column"]), set()).add((o["file"], o["line"]))
    fk = f"{users.table_name}.{pk.name}"
    inferences: list[dict[str, Any]] = []
    tables: list[TableModel] = []
    ignored: dict[str, set[str]] = {}  # entity -> columns of another type than the users key
    for t in model.tables:
        found = [c for c in t.columns
                 if t is not users and c.type == pk.type and (t.name, c.name) in uses]  # fmt: skip
        declared = [c for c in found if (c.foreign_key or "").split(".")[0] == users.table_name]
        if len(declared) == 1:  # EF Core states the association: confirm it
            c = declared[0]
            inferences.append({"table": t.name, "column": c.name, "rule": RULE, "declared": True,
                               "evidence": _evidence(uses[(t.name, c.name)])})  # fmt: skip
            found = []
        elif found:
            inferences.append(_entry(t.name, found, uses))
        if found or declared:
            # a column of another type (a device or session id compared with the caller's) is
            # no ownership evidence in a table that has its key-typed owner
            ignored[t.name] = {c.name for c in t.columns if c.type != pk.type}
        names = {c.name for c in found}
        cols = tuple(
            dataclasses.replace(c, foreign_key=fk) if c.name in names else c for c in t.columns
        )
        tables.append(dataclasses.replace(t, columns=cols) if names else t)
    routes = tuple(
        dataclasses.replace(
            r,
            owner_uses=tuple(o for o in r.owner_uses if o.column not in ignored.get(o.table, ())),
        )
        for r in model.routes
    )
    return dataclasses.replace(model, tables=tuple(tables), routes=routes), inferences


def _entry(
    table: str, found: list[Any], uses: Mapping[tuple[str, str], set[tuple[str, int]]]
) -> dict[str, Any]:
    """The inference for ``table``'s owner candidates (two or more: ``unclear``, all named)."""
    if len(found) == 1:
        c = found[0]
        return {"table": table, "column": c.name, "rule": RULE,
                "evidence": _evidence(uses[(table, c.name)])}  # fmt: skip
    return {
        "table": table,
        "column": None,
        "columns": [c.name for c in found],
        "evidence": [e for c in found for e in _evidence(uses[(table, c.name)])],
        "rule": RULE,
        "unclear": True,
    }


def infer_constraints(extract: Mapping[str, Any]) -> list[dict[str, Any]]:
    """``route_constraint``: one entry per path parameter the route template constrains."""
    return [
        {"rule": "route_constraint", "route": f"{r['method']} {r['path_template']}",
         "param": p["name"], "type": p["type"],
         "evidence": [f"code:{p['parse_at']}"] if p.get("parse_at") else []}
        for r in extract.get("routes", ())
        for p in r["params"]
        if p["source"] == "path" and p.get("parse")
    ]  # fmt: skip


def infer_bind(pol: Mapping[str, Any]) -> tuple[int | None, list[dict[str, Any]]]:
    """``(status, inferences)``: the Go rule on ``raises[].bind``, else ``dotnet.bind_status``."""
    status, found = infer_bind_status(pol)
    own = (pol.get("dotnet") or {}).get("bind_status")
    if found or own not in (400, 422):
        return status, found
    return own, [{"rule": "bind_status", "status": own, "evidence": ["dotnet.bind_status"]}]


def infer_choices_dn(
    data: Mapping[str, Any], workflow: Mapping[str, Any], model: RepoModel
) -> tuple[RepoModel, list[dict[str, Any]]]:
    """``enum_choices``: the Go K5 rule (``status_facts``) plus the extract's enum members."""
    inferences: list[dict[str, Any]] = []
    for t in data["tables"]:
        for c in t["columns"]:
            if c.get("choices"):
                inferences.append({"rule": "enum_choices", "table": t["struct"],
                                   "column": c["name"], "choices": list(c["choices"]),
                                   "evidence": [f"code:{t['file']}:{c['line']}"]})  # fmt: skip
    model, facts = infer_choices(data, workflow, model)
    return model, [*inferences, *({**i, "rule": "enum_choices"} for i in facts)]


def _camel(s: str) -> str:
    if not s or not s[0].isupper():
        return s
    c = list(s)
    for i in range(len(c)):
        if i == 1 and not c[i].isupper():
            break
        if i > 0 and i + 1 < len(c) and not c[i + 1].isupper():
            if c[i + 1] == " ":
                c[i] = c[i].lower()
            break
        c[i] = c[i].lower()
    return "".join(c)


def _separated(s: str, sep: str, upper: bool) -> str:
    out: list[str] = []
    state = 0  # 0 none, 1 upper, 2 lower or digit, 3 separator
    for i, ch in enumerate(s):
        if ch.isupper():
            if state == 2 or (state == 1 and s[i + 1 : i + 2].islower()):
                out.append(sep)
            out.append(ch if upper else ch.lower())
            state = 1
        elif ch.islower() or ch.isdigit():
            out.append(ch.upper() if upper else ch)
            state = 2
        elif ch in " _-":
            state = 3 if state else 0
            out.append(sep if ch == " " else ch)
        else:
            out.append(ch)
            state = 0
    return "".join(out)


def wire_name(name: str, policy: str | None) -> str:
    """``System.Text.Json``'s naming ``policy`` applied to a member name (a ``[JsonIgnore]``d
    member has no wire name in the extract: the one it would have had)."""
    if policy == "CamelCase":
        return _camel(name)
    kinds = {"SnakeCaseLower": ("_", False), "SnakeCaseUpper": ("_", True),
             "KebabCaseLower": ("-", False), "KebabCaseUpper": ("-", True)}  # fmt: skip
    return _separated(name, *kinds[policy]) if policy in kinds else name


def infer_private(pol: Mapping[str, Any]) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    """``({entity: private columns}, inferences)``: the columns a ``[JsonIgnore]`` hides."""
    private: dict[str, list[str]] = {}
    found: list[dict[str, Any]] = []
    for t in pol["tables"]:
        hidden = [c for c in t["columns"] if c.get("private") and not c["primary_key"]]
        if hidden:
            cols = private[t["struct"]] = sorted(c["name"] for c in hidden)
            evidence = [f"code:{t['file']}:{c['line']}" for c in hidden]
            found.append({"rule": "json_ignore", "table": t["struct"], "columns": cols,
                          "evidence": evidence})  # fmt: skip
    return private, found


def infer_naming(pol: Mapping[str, Any]) -> list[dict[str, Any]]:
    """``json_naming``: the policy, and the columns whose wire name is not their SQL name."""
    dn = pol.get("dotnet") or {}
    policy = dn.get("json_naming")
    if not policy:
        return []
    renamed: dict[str, dict[str, str]] = {}
    for t in pol["tables"]:
        for c in t["columns"]:
            wire = c["wire"] or wire_name(c["field"], policy)
            if wire != c["name"]:
                renamed.setdefault(t["struct"], {})[c["name"]] = wire
    entry: dict[str, Any] = {"rule": "json_naming", "naming": policy,
                             "by_style": dn.get("json_naming_by_style")}  # fmt: skip
    if renamed:
        entry["renamed"] = renamed
    return [entry]


class InferringFrontEnd:
    """The .NET front end with the inferences above applied to every model it extracts. Everything
    else is the wrapped front end's (``name``, ``runtime``, ``last_extract``, ``sidecar``...).
    ``private`` and ``validation_status`` are what :func:`patch_profile` writes."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.inferences: list[dict[str, Any]] = []
        self.private: dict[str, list[str]] = {}
        self.validation_status: int | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def extract(self, root: Path, package: str = ".") -> RepoModel:
        model = self._inner.extract(root, package)
        data = self._inner.last_extract
        if data is None:
            return model
        pol = self._inner.extract_json(root, package, workflow=True, policy=True)
        model, owners = infer_owners(data, model)
        self.validation_status, bind = infer_bind(pol)
        model, choices = infer_choices_dn(data, pol, model)
        self.private, ignored = infer_private(pol)
        self.inferences = [*owners, *infer_constraints(data), *bind, *choices, *ignored,
                           *infer_naming(pol), *self._inner.body_inferences]  # fmt: skip
        return model
