"""The rules-first import for a Go (Gin + GORM) app (Spike J, node J1): what
:func:`lattice.importer.rules.import_rules` needs beyond the Python path.

* :func:`harness_factory` (D1): the app factory of a Go module comes from the module's
  ``studio.toml`` ``[app] app`` (read-only reuse of ``go_gin._harness_attr``), not from a
  Python ``make_app``.
* :func:`infer_owners` (D2): GORM has no association tag the profile generator could read an
  owner from, but the extract records ``owner_uses`` (a column a route compares with, or sets
  from, the caller). A column ``c`` of table ``T`` is ``T``'s owner when all four hold:

  1. some route's ``owner_uses`` has ``(T, c, compare)`` or ``(T, c, assign)``;
  2. ``c`` is an ``Int`` column and so is the users table's primary key;
  3. ``T`` is not the users table (and has no users foreign key already);
  4. no other column of ``T`` meets 1 and 2.

  The model copy gives ``c`` an **inferred** users foreign key (``<users>.<pk>``), recorded in
  ``inferences[]`` as ``{"table", "column", "evidence": ["code:<file>:<line>", ...], "rule":
  "owner_uses"}``. Two or more candidates make ``T`` ``unclear`` with both columns named (each
  gets the foreign key: the profile generator then reports two users foreign keys). A table
  with no evidence keeps today's answer. The extract itself (the sidecar, the inventory,
  ``workflows.json``) is never touched: the inference is in memory only.
* :class:`InferringFrontEnd`: the Go front end with D2 applied to the model it extracts, so
  ``lift_repo`` (which extracts again) sees the inferred keys without an edit to it.

Round 2 (Spike J, node J2) adds, from the opt-in *policy* extract and the workflow extract,
each recorded in ``inferences[]`` with its ``rule``:

* E1 ``path_parse``: a path parameter the handler parses with ``strconv`` and refuses (a
  constant 4xx) is typed ``Int`` in the model.
* E3 ``bind_status``: when every ``c.ShouldBind*`` refusal of the module has one constant
  status, the generated profile's ``validation_status`` is that status (:func:`patch_profile`);
  when they disagree nothing changes and the entry says ``unclear``.
* E4 ``status_facts``: a string column with no ``choices`` and a constant write or ``from``
  state gets its ``default`` first, then the other written / named states, sorted, so the
  seed (which cycles ``choices``) creates rows in every state.
* E5 ``json_dash``: the columns of a table whose ``json`` is ``"-"`` (never a name), minus the
  primary key, are the table's ``private`` columns in the generated profile.
"""

from __future__ import annotations

import dataclasses
import re
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from lattice.importer.appmodel import FieldSpec, RepoModel, TableModel

__all__ = [
    "InferringFrontEnd",
    "harness_factory",
    "infer_bind_status",
    "infer_choices",
    "infer_owners",
    "infer_path_parse",
    "infer_private",
    "patch_profile",
]

RULE = "owner_uses"
OWNER_HOWS = ("compare", "assign")


def harness_factory(root: Path | str, package: str) -> tuple[Callable[[Path], Any], str] | None:
    """The app factory ``fn(db_path) -> app`` and its ``module:function`` name from the
    ``studio.toml`` ``[app] app`` of the Go module ``<root>/<package>``; None when the module
    has no such entry (the caller then looks for a Python ``make_app``, as before)."""
    from lattice.importer.frontends.go_gin import _harness_attr, _studio_app

    module_dir = Path(root) / package
    spec = _studio_app(module_dir).get("app")
    if not spec or ":" not in spec:
        return None
    factory = _harness_attr(module_dir, spec)
    if not callable(factory):
        return None
    return factory, spec


def _users_pk(model: RepoModel, users: str) -> FieldSpec | None:
    table = next((t for t in model.tables if t.name == users), None)
    if table is None:
        return None
    return next((c for c in table.columns if c.primary_key), None)


def infer_owners(
    extract: Mapping[str, Any], model: RepoModel
) -> tuple[RepoModel, list[dict[str, Any]]]:
    """``model`` with D2's inferred users foreign keys, and the ``inferences`` (see the module
    docstring). ``extract`` is the ``lattice.goextract/1`` document ``model`` was built from."""
    auth = model.auth
    users = auth.users_table if auth is not None else None
    pk = _users_pk(model, users) if users else None
    if users is None or pk is None or pk.type != "Int":
        return model, []
    users_sql = next(t.table_name for t in model.tables if t.name == users)
    uses: dict[tuple[str, str], set[tuple[str, int]]] = {}
    for route in extract.get("routes", ()):
        for o in route.get("owner_uses", ()):
            if o.get("how") in OWNER_HOWS:
                uses.setdefault((o["model"], o["column"]), set()).add((o["file"], o["line"]))
    inferences: list[dict[str, Any]] = []
    tables: list[TableModel] = []
    for t in model.tables:
        declared = any((c.foreign_key or "").split(".")[0] == users_sql for c in t.columns)
        found = [
            c for c in t.columns
            if t.name != users and not declared and c.type == "Int"
            and (t.name, c.name) in uses
        ]  # fmt: skip
        if not found:
            tables.append(t)
            continue
        evidence = {
            c.name: [f"code:{f}:{n}" for f, n in sorted(uses[(t.name, c.name)])] for c in found
        }
        if len(found) == 1:
            c = found[0]
            inferences.append({"table": t.name, "column": c.name, "evidence": evidence[c.name],
                               "rule": RULE})  # fmt: skip
        else:
            inferences.append({
                "table": t.name,
                "column": None,
                "columns": [c.name for c in found],
                "evidence": [e for c in found for e in evidence[c.name]],
                "rule": RULE,
                "unclear": True,
            })  # fmt: skip
        names = {c.name for c in found}
        fk = f"{users_sql}.{pk.name}"
        cols = tuple(
            dataclasses.replace(c, foreign_key=fk) if c.name in names else c for c in t.columns
        )
        tables.append(dataclasses.replace(t, columns=cols))
    if not inferences:
        return model, []
    return dataclasses.replace(model, tables=tuple(tables)), inferences


def infer_path_parse(
    pol: Mapping[str, Any], model: RepoModel
) -> tuple[RepoModel, list[dict[str, Any]]]:
    """E1: the path parameters the policy extract marks ``parse: Int`` become ``Int``."""
    parsed = {
        (r["method"], r["path_template"]): {p["name"]: p for p in r["params"] if p.get("parse")}
        for r in pol["routes"]
    }
    inferences: list[dict[str, Any]] = []
    routes = []
    for r in model.routes:
        hit = parsed.get((r.method, r.path), {})
        params = []
        for p in r.params:
            q = hit.get(p.name) if p.source == "path" else None
            if q is not None and q["parse"] == "Int":
                params.append(dataclasses.replace(p, type="Int"))
                inferences.append({"rule": "path_parse", "route": f"{r.method} {r.path}",
                                   "param": p.name,
                                   "evidence": [f"code:{q['parse_at']}"]})  # fmt: skip
            else:
                params.append(p)
        routes.append(dataclasses.replace(r, params=tuple(params)))
    return dataclasses.replace(model, routes=tuple(routes)), inferences


def infer_bind_status(pol: Mapping[str, Any]) -> tuple[int | None, list[dict[str, Any]]]:
    """E3: ``(status, inferences)``. The status is the one constant status of every bind
    refusal of the module (400 or 422, the two a profile can state); None otherwise."""
    sites = [(x["status"], f"code:{x['file']}:{x['line']}") for r in pol["routes"]
             for x in r["raises"] if x.get("bind")]  # fmt: skip
    statuses = sorted({s for s, _ in sites if s is not None})
    if not sites:
        return None, []
    if (
        len(statuses) == 1
        and len(statuses) == len({s for s, _ in sites})
        and statuses[0] in (400, 422)
    ):
        return statuses[0], [{"rule": "bind_status", "status": statuses[0],
                              "evidence": [e for _, e in sites]}]  # fmt: skip
    return None, [{"rule": "bind_status", "unclear": True, "statuses": statuses}]


def infer_choices(
    data: Mapping[str, Any], workflow: Mapping[str, Any], model: RepoModel
) -> tuple[RepoModel, list[dict[str, Any]]]:
    """E4: ``choices`` for the string columns that have ``status_facts`` (see the module
    docstring). ``data`` is the default extract (its ``status_writes``)."""
    facts: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for f in workflow.get("status_facts") or ():
        facts.setdefault((f["model"], f["column"]), []).append(f)
    inferences: list[dict[str, Any]] = []
    tables = []
    for t in model.tables:
        cols = []
        for c in t.columns:
            fs = facts.get((t.name, c.name))
            if not fs or c.type != "Str" or c.choices:
                cols.append(c)
                continue
            ws = [w for w in data.get("status_writes", ())
                  if (w["model"], w["column"]) == (t.name, c.name)]  # fmt: skip
            named = {w["value"] for w in ws if isinstance(w["value"], str)}
            named |= {s for f in fs for s in f["from"] if s != "*"}
            choices = ([c.default] if c.default is not None else []) + sorted(named - {c.default})
            cols.append(dataclasses.replace(c, choices=tuple(choices)))
            evidence = sorted({f"code:{x['file']}:{x['line']}" for x in (*fs, *ws)})
            inferences.append({"rule": "status_facts", "table": t.name, "column": c.name,
                               "choices": choices, "evidence": evidence})  # fmt: skip
        tables.append(dataclasses.replace(t, columns=tuple(cols)))
    return dataclasses.replace(model, tables=tuple(tables)), inferences


def infer_private(pol: Mapping[str, Any]) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    """E5: ``({struct: private columns}, inferences)``: the columns whose ``json`` is ``"-"``."""
    private: dict[str, list[str]] = {}
    for t in pol["tables"]:
        hidden = sorted(c["name"] for c in t["columns"]
                        if c.get("json") == "-" and not c["primary_key"])  # fmt: skip
        if hidden:
            private[t["struct"]] = hidden
    return private, [{"rule": "json_dash", "table": t, "columns": c} for t, c in private.items()]


def patch_profile(result: Any, private: Mapping[str, list[str]], status: int | None) -> None:
    """E3 and E5 in the generated profile source (and the loaded profile of ``result``, an
    :class:`~lattice.importer.lift.ImportResult`): ``validation_status=<status>`` and the
    ``private=[...]`` of each table's schema. A no-op when there is nothing to say."""
    from lattice.importer.lift import load_profile
    from lattice.importer.profile_gen import _const_name

    src = result.profile.source
    if status is not None and src.count("validation_status=400") == 1:
        src = src.replace("validation_status=400", f"validation_status={status}")
    for table, cols in private.items():
        block = re.search(
            rf"^{_const_name(table)} = Schema\.from_pairs\(\n.*?\n\)\n", src, re.S | re.M
        )
        if block and "private=" not in block.group(0):
            text = block.group(0)[:-2] + f"    private={list(cols)!r},\n)\n"
            src = src[: block.start()] + text + src[block.end() :]
    if src == result.profile.source:
        return
    result.profile = dataclasses.replace(result.profile, source=src)
    module = load_profile(result.profile, f"lattice_import_profile_{uuid.uuid4().hex}")
    result.lattice_profile = module.profile()
    if result.context is not None:
        result.context.profile = result.lattice_profile


class InferringFrontEnd:
    """The Go front end with D2 and E1-E5 applied to every model it extracts. Everything
    else is the wrapped front end's (``name``, ``runtime``, ``last_extract``, ``sidecar``...).
    ``private`` and ``validation_status`` are what :func:`patch_profile` writes (E5, E3)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.inferences: list[dict[str, Any]] = []
        self.private: dict[str, list[str]] = {}
        self.validation_status: int | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def extract(self, root: Path, package: str = ".") -> RepoModel:
        from lattice.gocheck.derive import policy_extract

        model = self._inner.extract(root, package)
        data = self._inner.last_extract
        if data is None:
            return model
        model, owners = infer_owners(data, model)
        pol = policy_extract(Path(root) / package)
        workflow = self._inner.extract_json(root, package, workflow=True)
        model, parse = infer_path_parse(pol, model)
        self.validation_status, bind = infer_bind_status(pol)
        model, choices = infer_choices(data, workflow, model)
        self.private, dash = infer_private(pol)
        self.inferences = [*owners, *parse, *bind, *choices, *dash]
        return model
