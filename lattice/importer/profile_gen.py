"""Profile generation: ``RepoModel`` -> Lattice profile source + unclear decisions (spec
2026-09-27 importer §3.1, §6).

The generated source is a self-contained Python module: typed native wrappers that call
the original functions (``NATIVES``), one ``Schema`` per table and ``profile()``
returning the ``Profile``. Each rule below only decides what the evidence settles; the
rest is an ``Unclear`` and is left unset, so the checker (or a human) has to supply it:

* owner (plan node C2): a table's owner is its single foreign key to the users table
  (a) when some route -- directly or through the helpers and repository methods it hands
  the caller or the caller's id (``Route.owner_uses``) -- filters or checks that column
  against the caller (b) or sets it from the caller (c), and no route contradicts it.
  A contradiction is an authenticated non-admin route returning rows of the table
  without any owner check: the table is then *shared*, a decision recorded as such
  (``ProfileResult.ownership``), not an unclear one. Two user foreign keys, a route
  comparing another column or no evidence at all -> unclear. A table with no user
  foreign key whose foreign-key parent is owned, and whose routes check through that
  parent, is owned through it (``owner_via``). A wrong owner is never silent: every
  lifted route is still verified against the original;
* private: the columns no response model mapped to the table returns;
* admin_write: the columns only admin routes' request bodies write;
* auth / roles / active: the token-decoding native, the superuser and active checks;
* page shape / responses: the route response models (envelopes: a list + count);
* storage / representation: the dialect, custom exception handlers and serialisers;
* a route whose auth rests on a dependency the extractor does not know is unclear
  (review focus #2), never Public.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lattice.importer.extract import (
    LANGUAGE_GAP,
    DataModel,
    Dependency,
    NativeCandidate,
    RepoModel,
    Route,
    TableModel,
)
from lattice.profile import TOKEN_ANSWER_STATUSES

if TYPE_CHECKING:
    from lattice.importer.policy import TokenAnswers

SCALAR_ATTR = {
    "Str": "STR",
    "Email": "STR",
    "Int": "INT",
    "Float": "FLOAT",
    "Bool": "BOOL",
    "Date": "DATE",
    "DateTime": "DATETIME",
    "Uuid": "UUID",
}
PY_TYPE = {
    "Str": "str",
    "Email": "str",
    "Int": "int",
    "Float": "float",
    "Bool": "bool",
    "Date": "date",
    "DateTime": "datetime",
    "Uuid": "uuid.UUID",
}
KEY_CONVERT = {"Uuid": "uuid.UUID", "Int": "int", "Str": "str"}
# The automatic 401 text of FastAPI's security classes.
FASTAPI_UNAUTHORIZED = "Not authenticated"
CUSTOMER = "Customer"
ADMIN = "Admin"
TOTAL_NAMES = ("count", "total")
LIMIT_NAMES = ("limit", "size", "page_size")
OFFSET_NAMES = ("offset", "skip")


@dataclass(frozen=True)
class Unclear:
    """A decision the evidence does not settle. ``topic``: owner, private, auth, roles,
    route_auth, page_shape, storage, error_body, datetime_wire, users_table,
    column_type, profile; ``subject``: the table, route or app package concerned."""

    topic: str
    subject: str
    reason: str
    candidates: tuple[str, ...] = ()


OWNED = "owner"
OWNED_VIA = "owner_via"
SHARED = "shared"
UNCLEAR = "unclear"


@dataclass(frozen=True)
class ProfileResult:
    source: str  # the profile module (natives + schemas + profile())
    unclear: list[Unclear] = field(default_factory=list)
    # table -> owner / owner_via / shared / unclear: the ownership decision for every
    # table with a user foreign key or an owned foreign-key parent, and why
    ownership: dict[str, str] = field(default_factory=dict)
    ownership_reasons: dict[str, str] = field(default_factory=dict)


@dataclass
class _TablePlan:
    table: TableModel
    columns: list[tuple[str, str]]  # (name, scalar attr)
    keys: list[tuple[str, ...]]
    nullable: list[str]
    owner: str | None = None
    owner_via: tuple[str, str] | None = None
    ownership: str | None = None  # OWNED, OWNED_VIA, SHARED, UNCLEAR or None
    ownership_reason: str = ""
    private: list[str] = field(default_factory=list)
    admin_write: list[str] = field(default_factory=list)
    one_of: dict[str, tuple[str, ...]] = field(default_factory=dict)
    one_of_stored: dict[str, tuple[str, ...]] = field(default_factory=dict)


class _Generator:
    def __init__(self, model: RepoModel) -> None:
        self.m = model
        self.unclear: list[Unclear] = []
        self.models = {d.name: d for d in model.models}
        self.tables = {t.name: t for t in model.tables}
        self.deps = {d.name: d for d in model.dependencies}

    def note(self, topic: str, subject: str, reason: str, candidates: tuple[str, ...] = ()) -> None:
        self.unclear.append(Unclear(topic, subject, reason, candidates))

    # -- decisions --

    def users_table(self) -> str | None:
        fe = self.m.auth
        if fe is not None and fe.users_table in self.tables:
            return fe.users_table  # a front end's AuthModel names it
        found = sorted(
            {d.returns for d in self.m.dependencies if d.kind == "current_user" and d.returns}
        )
        if len(found) == 1:
            return found[0]
        self.note(
            "users_table",
            self.m.app_package,
            "no single current-user dependency names the users table"
            if not found
            else "current-user dependencies return different tables",
            tuple(found),
        )
        return None

    def responses(self) -> tuple[dict[str, tuple[str, ...]], list[DataModel]]:
        """Plain response models (fields in order) and the envelope models."""
        responses: dict[str, tuple[str, ...]] = {}
        envelopes: list[DataModel] = []
        for route in self.m.routes:
            name = route.response_model
            if name is None:
                continue
            if name in self.tables:
                responses.setdefault(name, tuple(c.name for c in self.tables[name].columns))
                continue
            model = self.models.get(name)
            if model is None:
                continue
            item = _envelope_item(model)
            if item is not None:
                if model not in envelopes:
                    envelopes.append(model)
                inner = self.models.get(item)
                if inner is not None:
                    responses.setdefault(item, tuple(f.name for f in inner.fields))
                continue
            if model.fields:  # an empty schema (APIFairy's EmptySchema) states no fields
                responses.setdefault(name, tuple(f.name for f in model.fields))
        return responses, envelopes

    def page_shape(self, envelopes: list[DataModel]) -> dict[str, str] | None:
        shapes = []
        for env in envelopes:
            names = [f.name for f in env.fields]
            items = next(f.name for f in env.fields if f.type == "List")
            ints = [f.name for f in env.fields if f.type == "Int"]
            shape = {
                "items": items,
                "total": _pick(ints, TOTAL_NAMES),
                "limit": _pick(ints, LIMIT_NAMES),
                "offset": _pick(ints, OFFSET_NAMES),
            }
            if set(names) - {v for v in shape.values() if v}:
                self.note(
                    "page_shape",
                    env.name,
                    f"envelope {env.name} has fields the page shape cannot name",
                    tuple(names),
                )
            shapes.append(shape)
        if not shapes:
            return None
        if any(s != shapes[0] for s in shapes):
            self.note(
                "page_shape",
                self.m.app_package,
                "the envelope models disagree on their field names",
                tuple(e.name for e in envelopes),
            )
        return shapes[0]

    def table_plans(
        self, users: str | None, responses: dict[str, tuple[str, ...]]
    ) -> list[_TablePlan]:
        plans = []
        users_name = self.tables[users].table_name if users in self.tables else None
        for table in self.m.tables:
            columns = []
            one_of: dict[str, tuple[str, ...]] = {}
            stored: dict[str, tuple[str, ...]] = {}
            for c in table.columns:
                if c.type in SCALAR_ATTR:
                    columns.append((c.name, SCALAR_ATTR[c.type]))
                    if c.choices and c.type == "Str":
                        one_of[c.name] = tuple(map(str, c.choices))
                        # an Enum column: the ORM stores its members' names
                        if c.choice_names and c.choice_names != one_of[c.name]:
                            stored[c.name] = c.choice_names
                    elif c.choices:
                        self.note(
                            "column_type",
                            f"{table.name}.{c.name}",
                            f"{c.annotation} allows only {', '.join(map(str, c.choices))}; "
                            f"kept as {c.type}: {LANGUAGE_GAP} (enum constraint) -- Lattice's "
                            "OneOf constraint is on Str columns and parameters only",
                            tuple(map(str, c.choices)),
                        )
                else:
                    self.note(
                        "column_type",
                        f"{table.name}.{c.name}",
                        f"column type {c.annotation} has no Lattice scalar; column left out",
                    )
            kept = {n for n, _ in columns}
            keys = [tuple(c.name for c in table.columns if c.primary_key and c.name in kept)]
            plan = _TablePlan(
                table,
                columns,
                [k for k in keys if k],
                [c.name for c in table.columns if c.nullable and c.name in kept],
            )
            plan.one_of, plan.one_of_stored = one_of, stored
            plan.private = self.private(table, responses, kept)
            if table.name != users:
                self.owner(plan, users_name)
            plans.append(plan)
        by_table_name = {p.table.table_name: p for p in plans}
        for plan in plans:
            if plan.table.name != users and plan.ownership is None:
                self.owner_via(plan, by_table_name)
        for plan in plans:
            kept = {n for n, _ in plan.columns}
            plan.admin_write = self.admin_write(plan.table, kept, plan)
        return plans

    def private(
        self, table: TableModel, responses: dict[str, tuple[str, ...]], kept: set[str]
    ) -> list[str]:
        mapped = [name for name in responses if self._maps_to(name, table)]
        if not mapped:
            self.note(
                "private",
                table.name,
                f"no response model returns {table.name}; its private columns are not inferred",
            )
            return []
        returned = {f for name in mapped for f in responses[name]}
        return [c.name for c in table.columns if c.name in kept and c.name not in returned]

    def _maps_to(self, model_name: str, table: TableModel) -> bool:
        if model_name == table.name:
            return True
        model = self.models.get(model_name)
        if model is None:
            return False
        if set(model.bases) & set(table.bases):
            return True
        return model_name.startswith(table.name) and not model_name[len(table.name) :][:1].islower()

    def _returns_rows(self, route: Route, table: TableModel) -> bool:
        """Whether ``route`` answers with rows of ``table``: its return values, or its
        response model (an envelope's items) mapped to the table."""
        if route.returns == table.name:
            return True
        name = route.response_model
        if name is None:
            return False
        if self._maps_to(name, table):
            return True
        model = self.models.get(name)
        item = _envelope_item(model) if model is not None else None
        return item is not None and (item == table.name or self._maps_to(item, table))

    def _unchecked(self, table: TableModel, checked: tuple[str, ...]) -> list[str]:
        """The authenticated non-admin routes returning rows of ``table`` with no owner
        check on any of the ``checked`` tables: each makes the table shared."""
        return sorted(
            r.name
            for r in self.m.routes
            if table.name in r.tables
            and r.auth == CUSTOMER
            and not any(u.table in checked for u in r.owner_uses)
            and self._returns_rows(r, table)
        )

    def _decide(self, plan: _TablePlan, kind: str, reason: str) -> None:
        plan.ownership, plan.ownership_reason = kind, reason

    def owner(self, plan: _TablePlan, users_name: str | None) -> None:
        table = plan.table
        if users_name is None:
            return
        fks = [c.name for c in table.columns if (c.foreign_key or "").split(".")[0] == users_name]
        if not fks:
            return
        unchecked = self._unchecked(table, (table.name,))
        compared = any(
            u.table == table.name and u.how == "compare"
            for r in self.m.routes
            for u in r.owner_uses
        )
        if unchecked and self.m.auth is not None and compared:
            # a Django / Flask app: reads open to any user beside owner-checked writes
            # (DRF's IsOwnerOrReadOnly, an author check before an edit) -- the table is
            # owned, those reads reach anyone's rows (their contracts' owner_scope="any")
            unchecked = []
        if unchecked:
            self._decide(
                plan,
                SHARED,
                f"authenticated routes {', '.join(unchecked)} return {table.name} rows "
                "without an owner check",
            )
            return
        if len(fks) > 1:
            reason = (
                f"{table.name} has {len(fks)} foreign keys to the users table; "
                "the owner is not inferred"
            )
            self.note("owner", table.name, reason, tuple(fks))
            self._decide(plan, UNCLEAR, reason)
            return
        fk = fks[0]
        touching = [r for r in self.m.routes if table.name in r.tables]
        used = {u.column for r in touching for u in r.owner_uses if u.table == table.name}
        other = sorted(used - {fk})
        if other:
            routes = sorted(
                {
                    r.name
                    for r in touching
                    for u in r.owner_uses
                    if u.table == table.name and u.column in other
                }
            )
            reason = (
                f"routes {', '.join(routes)} compare {', '.join(other)} (not {fk}) to the caller"
            )
            self.note("owner", table.name, reason, (fk, *other))
            self._decide(plan, UNCLEAR, reason)
            return
        if not used:
            reason = f"no route filters, checks or sets {table.name}.{fk} from the caller"
            self.note("owner", table.name, reason, (fk,))
            self._decide(plan, UNCLEAR, reason)
            return
        evidence = sorted({r.name for r in touching for u in r.owner_uses if u.table == table.name})
        plan.owner = fk
        self._decide(
            plan,
            OWNED,
            f"its one users foreign key, which {', '.join(evidence)} filter, check or set "
            "from the caller",
        )

    def owner_via(self, plan: _TablePlan, by_table_name: dict[str, _TablePlan]) -> None:
        """``owner_via`` (foreign key, parent): the one foreign key to an owned parent (by
        its one-column key) the routes touching the table check the caller through."""
        table = plan.table
        candidates: list[tuple[str, _TablePlan]] = []
        for c in table.columns:
            if not c.foreign_key or "." not in c.foreign_key:
                continue
            ref_table, ref_col = c.foreign_key.split(".", 1)
            parent = by_table_name.get(ref_table)
            if (
                parent is not None
                and parent is not plan
                and parent.owner is not None
                and len(parent.keys) == 1
                and parent.keys[0] == (ref_col,)
            ):
                candidates.append((c.name, parent))
        if not candidates:
            return
        parents = tuple(p.table.name for _, p in candidates)
        unchecked = self._unchecked(table, (table.name, *parents))
        if unchecked:
            self._decide(
                plan,
                SHARED,
                f"authenticated routes {', '.join(unchecked)} return {table.name} rows "
                f"without an owner check (on it or on {', '.join(parents)})",
            )
            return
        checked = [
            (fk, parent)
            for fk, parent in candidates
            if any(
                table.name in r.tables and any(u.table == parent.table.name for u in r.owner_uses)
                for r in self.m.routes
            )
        ]
        if len(checked) != 1:
            reason = (
                f"no route touching {table.name} checks the caller through {', '.join(parents)}"
                if not checked
                else f"routes touching {table.name} check the caller through several "
                f"owned parents ({', '.join(p.table.name for _, p in checked)})"
            )
            self.note("owner", table.name, reason, tuple(fk for fk, _ in candidates))
            self._decide(plan, UNCLEAR, reason)
            return
        fk, parent = checked[0]
        plan.owner_via = (fk, parent.table.name)
        self._decide(
            plan,
            OWNED_VIA,
            f"its foreign key {fk} to {parent.table.name} (owned), which its routes check "
            "the caller through",
        )

    def admin_write(self, table: TableModel, kept: set[str], plan: _TablePlan) -> list[str]:
        admin: set[str] = set()
        others: set[str] = set()
        for route in self.m.routes:
            if table.name not in route.tables:
                continue
            fields = {f.name for b in route.body_models for f in self._body_fields(b)}
            (admin if route.auth == ADMIN else others).update(fields & kept)
        exclude = {c for k in plan.keys for c in k} | set(plan.private)
        if plan.owner:
            exclude.add(plan.owner)
        if plan.owner_via:
            exclude.add(plan.owner_via[0])
        return [c.name for c in table.columns if c.name in admin - others - exclude]

    def _body_fields(self, name: str) -> tuple:
        if name in self.models:
            return self.models[name].fields
        if name in self.tables:
            return self.tables[name].columns
        return ()

    def auth_deps(self) -> tuple[Dependency | None, Dependency | None]:
        """(the dependency holding the active check, the superuser dependency)."""
        active = next((d for d in self.m.dependencies if d.active_column), None)
        admin = next((d for d in self.m.dependencies if d.kind == "admin"), None)
        return active, admin

    def natives(self, private: set[str]) -> list[tuple[NativeCandidate, str]]:
        """(candidate, NativeSig source) for every expressible native."""
        out = []
        for n in self.m.natives:
            if not n.expressible:
                continue
            params = [p for p in n.params if p.bound is None]
            args = ", ".join(f"Scalar.{SCALAR_ATTR[p.type]}" for p in params if p.type)
            parts = [f"({args}{',' if len(params) == 1 else ''})"]
            if n.effect:
                parts.append("effect=True")
            else:
                parts.append(f"Scalar.{SCALAR_ATTR[n.result_type or 'Str']}")
                if n.nullable:
                    parts.append("nullable=True")
                if any(set(p.passed_columns) & private for p in n.params):
                    parts.append("reads_private=True")
                if n.settings:
                    parts.append(f"settings={tuple(name for name, _, _ in n.settings)!r}")
            out.append((n, f"NativeSig({', '.join(parts)})"))
        return out


# ---- rendering -----------------------------------------------------------------------------


def _pick(names: list[str], wanted: tuple[str, ...]) -> str:
    return next((n for n in names if n in wanted), "")


def _envelope_item(model: DataModel) -> str | None:
    lists = [f for f in model.fields if f.type == "List"]
    if len(lists) != 1 or lists[0].item is None:
        return None
    if not all(f.type in ("List", "Int") for f in model.fields):
        return None
    return lists[0].item


def _const_name(table: str) -> str:
    return "TABLE_" + "".join(c if c.isalnum() else "_" for c in table).upper()


def _wrapper(n: NativeCandidate, alias: str) -> list[str]:
    params = [p for p in n.params if p.bound is None]
    sig = ", ".join(f"{p.name}: {PY_TYPE[p.type or 'Str']}" for p in params)
    if n.effect:
        result = "None"
    else:
        result = PY_TYPE[n.result_type or "Str"] + (" | None" if n.nullable else "")
    call_args = []
    for p in n.params:
        value = p.bound if p.bound is not None else p.name
        call_args.append(value if p.positional_only else f"{p.name}={value}")
    origin_fn = n.origin.split(":", 1)[1]
    call = f"{alias}.{origin_fn}({', '.join(call_args)})"
    lines = [f"def {n.name}({sig}) -> {result}:", f'    """Wraps {n.origin}."""']
    if n.effect:
        lines.append(f"    {call}")
    elif n.adapter == "first":
        lines.append(f"    return {call}[0]")
    else:
        lines.append(f"    return {call}")
    return lines


def _decoder(n: NativeCandidate) -> list[str]:
    token = n.token_param or "token"
    catches = tuple(dict.fromkeys([*n.catches, "ValueError", "TypeError"]))
    convert = KEY_CONVERT.get(n.result_type or "Uuid", "str")
    ctx = "ctx: Mapping[str, object], " if n.settings else ""
    what = f" (the users column {n.lookup!r})" if n.lookup else ""
    return [
        f"def {n.name}({ctx}{token}: str) -> {PY_TYPE[n.result_type or 'Uuid']} | None:",
        f'    """The decode step of {n.origin}: the {n.claim!r} claim of a valid token{what}, '
        'or None."""',
        "    try:",
        f"        payload = {n.decode_expr}",
        f"    except ({', '.join(catches)}):",
        "        return None",
        f"    claim = payload.get({n.claim!r}) if isinstance(payload, dict) else None",
        "    if not isinstance(claim, str | int) or isinstance(claim, bool):",
        "        return None",
        "    try:",
        f"        return {convert}(claim)"
        if convert != "uuid.UUID"
        else "        return uuid.UUID(str(claim))",
        "    except (ValueError, TypeError):",
        "        return None",
    ]


def generate_profile(model: RepoModel, answers: TokenAnswers | None = None) -> ProfileResult:
    """The profile module's source for ``model``. ``answers``: the original's answers to
    a token naming no caller (:func:`lattice.importer.policy.token_answers`); the token
    auth answers so where they differ from its default 401 (``NativeToken(invalid=,
    unknown=)``), on every route."""
    g = _Generator(model)
    users = g.users_table()
    responses, envelopes = g.responses()
    page_shape = g.page_shape(envelopes)
    plans = g.table_plans(users, responses)
    private = {c for p in plans for c in p.private}
    natives = g.natives(private)
    active, admin = g.auth_deps()
    fe = model.auth  # a Django / Flask front end's AuthModel (None: FastAPI's dependencies)
    if fe is not None and users is not None:
        natives = [*natives, _frontend_decoder(g, model, users)]
        active, admin = _frontend_deps(model, fe)

    for route in model.routes:
        if route.auth == "unclear":
            g.note("route_auth", route.name, route.auth_reason, route.dependencies)

    decoder = next((n for n, _ in natives if n.kind == "token_decoder"), None)
    if decoder is None:
        refused = next((n for n in model.natives if n.kind == "token_decoder"), None)
        g.note(
            "auth",
            users or model.app_package,
            f"the token decoder is not expressible: {refused.reason}"
            if refused is not None
            else "no token-decoding step found in a current-user dependency",
        )
    if decoder is not None and decoder.lookup and decoder.lookup_unique == "runtime":
        g.note(
            "auth",
            users or model.app_package,
            f"the token's {decoder.claim!r} claim names {users}.{decoder.lookup}, which is "
            "not declared unique (the app may enforce it in code only): the lookup is "
            'checked per request (NativeToken unique="runtime"): no row answers 401, '
            "several a loud 500 (R311, an ambiguous caller)",
        )
    users_cols = {c.name: c for c in g.tables[users].columns} if users in g.tables else {}
    no_roles = False
    scheme = model.role_scheme
    if scheme is not None:
        admin = None  # the role column decides who is the admin (RoleColumn(values=...))
    elif admin is None:
        role = users_cols.get("role")
        if users is not None and (role is None or role.type != "Str"):
            # no admin concept: every caller is a Customer (single-role auth)
            no_roles = True
        else:
            g.note(
                "roles", model.app_package, "no superuser dependency: roles_from is not inferred"
            )
    # a users table only with a way to authenticate against it (the default token column
    # would name a column the table does not have)
    emit_users = users is not None and (decoder is not None or "token" in users_cols)
    if users is not None and not emit_users:
        g.note(
            "users_table",
            users,
            f"{users} is the users table, but the profile has no auth for it: users_table "
            "is left unset",
        )
    token_dep = next((d for d in model.dependencies if d.kind == "token"), None)

    sqlite = model.dialect == "sqlite"
    if not sqlite:
        g.note(
            "storage",
            model.app_package,
            f"dialect {model.dialect or 'unknown'}: storage formats are not inferred",
        )
    if model.exception_handlers:
        g.note(
            "error_body",
            model.app_package,
            "custom exception handlers change the error body",
            model.exception_handlers,
        )
    if not sqlite or model.custom_serialization:
        g.note(
            "datetime_wire",
            model.app_package,
            "custom serialisation or a non-SQLite dialect: the datetime wire is not inferred",
            model.custom_serialization,
        )

    # ---- render ----
    out: list[str] = [
        f'"""Lattice profile for {model.app_package}.',
        "",
        'Generated by lattice.importer.profile_gen; the natives wrap the original functions."""',
        "",
        "from __future__ import annotations",
        "",
        "import uuid",
        "",
    ]
    imports: list[str] = []
    aliases: dict[str, str] = {}
    for n, _ in natives:
        if n.kind == "token_decoder":
            imports.extend(n.imports)
            continue
        module = n.origin.split(":", 1)[0]
        if module not in aliases:
            aliases[module] = f"_native_{len(aliases)}"
            imports.append(f"import {module} as {aliases[module]}")
        for p in n.params:
            imports.extend(p.bound_imports)
    out.extend(sorted(dict.fromkeys(imports)))
    settings = decoder.settings if decoder is not None else ()
    if settings:
        out += ["from collections.abc import Mapping"]
    names = "AdminFlag, ApiProfile, NativeSig, NativeToken, " + ("NoRoles, " if no_roles else "")
    if scheme is not None:
        names += "RoleColumn, "
    out += [
        "",
        f"from lattice.profile import {names}Profile",
        "from lattice.typesys import Scalar, Schema",
        "",
        "# ---- natives: typed wrappers over the original functions ----",
        "",
    ]
    for n, _ in natives:
        if n.kind == "token_decoder":
            out += [*_decoder(n), "", ""]
        else:
            out += [*_wrapper(n, aliases[n.origin.split(":", 1)[0]]), "", ""]
    out.append("NATIVES = {")
    out += [f"    {n.name!r}: {n.name}," for n, _ in natives]
    out += ["}", ""]
    if settings:
        paths = "".join(f"    {name!r}: {path!r},\n" for name, _, path in settings)
        out += [
            "# ---- settings: the app configuration natives read (their NativeContext) ----",
            "",
            "# setting -> where the original app keeps it (an attribute path from the app object)",
            "SETTINGS_FROM_APP = {\n" + paths + "}",
            "",
            "",
            "def settings_from_app(app: object) -> dict[str, object]:",
            '    """The settings\' values, read from the running original app (built by its',
            '    factory): pass them to the Lattice app at start-up."""',
            "    out = {}",
            "    for name, path in SETTINGS_FROM_APP.items():",
            "        value = app",
            "        for attr in path.split('.'):",
            "            value = getattr(value, attr)",
            "        out[name] = value",
            "    return out",
            "",
            "",
        ]
    out += ["# ---- tables ----", ""]
    for plan in plans:
        if plan.ownership in (OWNED, OWNED_VIA, SHARED):
            out.append(f"# {plan.table.name}: {plan.ownership} -- {plan.ownership_reason}")
        out.append(f"{_const_name(plan.table.name)} = Schema.from_pairs(")
        out.append("    [")
        out += [f"        ({name!r}, Scalar.{attr})," for name, attr in plan.columns]
        out.append("    ],")
        keys = plan.keys
        if (
            decoder is not None
            and decoder.lookup
            and decoder.lookup_unique == "declared"
            and plan.table.name == users
        ):
            keys = [*keys, (decoder.lookup,)]  # the unique column the token names
        out.append(f"    keys={keys!r},")
        out.append(f"    nullable={plan.nullable!r},")
        if plan.owner:
            out.append(f"    owner={plan.owner!r},")
        if plan.owner_via:
            out.append(f"    owner_via={plan.owner_via!r},")
        if plan.private:
            out.append(f"    private={plan.private!r},")
        if plan.admin_write:
            out.append(f"    admin_write={plan.admin_write!r},")
        if plan.one_of:
            out.append(f"    one_of={plan.one_of!r},")
        if plan.one_of_stored:
            out.append(f"    one_of_stored={plan.one_of_stored!r},")
        out += [")", ""]
    out.append("RESPONSES: dict[str, tuple[str, ...]] = {")
    out += [f"    {name!r}: {fields!r}," for name, fields in responses.items()]
    out += ["}", "", ""]

    api: list[str] = [
        "tables={"
        + ", ".join(f"{p.table.name!r}: {_const_name(p.table.name)}" for p in plans)
        + "}",
        "table_names={"
        + ", ".join(f"{p.table.name!r}: {p.table.table_name!r}" for p in plans)
        + "}",
        "roles=frozenset({"
        + ", ".join(
            repr(r)
            for r in (
                ["Public", *(n for _, _, n in scheme.roles), *([] if scheme.admin else [ADMIN])]
                if scheme is not None
                else ["Public", CUSTOMER, ADMIN]
            )
        )
        + "})",
    ]
    if emit_users:
        api.append(f"users_table={users!r}")
    if page_shape is not None:
        api.append(f"page_shape={page_shape!r}")
    api.append("responses=RESPONSES")
    entries = "".join(f"            {n.name!r}: {sig},\n" for n, sig in natives)
    api.append("natives={\n" + entries + "        }")
    if decoder is not None and decoder.settings:
        declared = ", ".join(
            f"{name!r}: Scalar.{SCALAR_ATTR[t]}" for name, t, _ in decoder.settings
        )
        api.append(f"settings={{{declared}}}")
    unauthorized = (
        FASTAPI_UNAUTHORIZED if not model.exception_handlers and token_dep is not None else None
    )
    if decoder is not None:
        lookup = f", lookup={decoder.lookup!r}" if decoder.lookup else ""
        if decoder.lookup and decoder.lookup_unique != "declared":
            lookup += f", unique={decoder.lookup_unique!r}"
        for name, answer in (
            ("invalid", answers.invalid if answers else None),
            ("unknown", answers.unknown if answers else None),
        ):
            if answer is not None and not _token_answer_ok(answer):
                # NativeToken carries only a 400/401/403/404 with a detail: keep the
                # default answer (the verifier declares the difference as a per-route
                # token policy) rather than refuse the profile, and say why
                g.note(
                    "token_answer",
                    name,
                    f"the original answers {answer!r}, which NativeToken cannot carry "
                    f"(a status in {TOKEN_ANSWER_STATUSES} and a non-empty detail); "
                    "the default 401 is kept and the difference declared per route",
                )
                continue
            if answer is not None and answer != (401, unauthorized):
                lookup += f", {name}={answer!r}"
        api.append(f"auth=NativeToken({decoder.name!r}{lookup})")
    if scheme is not None:
        # keyed by the column's value as the profile reads it: an enum column stored by
        # member name is decoded to its value first (one_of_stored)
        values = {value: name for value, _, name in scheme.roles}
        if fe is not None and fe.role_table is not None:
            # roles kept as rows of a table: the users column holds the row's key
            values = {stored: name for _, stored, name in scheme.roles}
        api.append(f"roles_from=RoleColumn({scheme.column!r}, values={values!r})")
    elif admin is not None and admin.admin_column:
        api.append(f"roles_from=AdminFlag({admin.admin_column!r}, {CUSTOMER!r})")
    elif no_roles:
        api.append(f"roles_from=NoRoles({CUSTOMER!r})")
    if sqlite:
        api += ["backend='sqlite'", "uuid_storage='hex32'", "datetime_storage='sql_space'"]
        # SQLite's own ORDER BY puts NULLs first ascending and last descending: the
        # imported app keeps that order for every sort that does not say nulls=
        api.append("null_order='smallest'")
    if active is not None and active.active_column:
        api.append(f"active_column={active.active_column!r}")
        if active.active_status is not None:
            api.append(f"inactive_status={active.active_status!r}")
        if active.active_detail:
            api.append(f"inactive_detail={active.active_detail!r}")
    if fe is not None:
        api += _frontend_errors(model, fe, scheme)
    elif not model.exception_handlers:
        api.append("error_body='fastapi'")
        if unauthorized is not None:
            api.append(f"unauthorized_detail={unauthorized!r}")
        if admin is not None and admin.admin_detail:
            api.append(f"forbidden_detail={admin.admin_detail!r}")
        elif scheme is not None and scheme.detail:
            api.append(f"forbidden_detail={scheme.detail!r}")
    if sqlite and not model.custom_serialization:
        api.append("datetime_wire='naive'")
    out += ["def profile() -> Profile:", f'    """The Lattice profile of {model.app_package}."""']
    source = _render_profile(out, api)
    problem = _refused(source)
    if problem is not None:
        # never crash on an unfamiliar app: drop the auth settings the profile refuses
        # (the routes needing them then have no auth) and say so
        dropped = tuple(a.split("=", 1)[0] for a in api if a.startswith(AUTH_SETTINGS))
        g.note(
            "profile",
            model.app_package,
            f"the generated profile is refused ({problem}); its auth settings are left unset",
            dropped,
        )
        api = [a for a in api if not a.startswith(AUTH_SETTINGS)]
        source = _render_profile(out, api)
    decided = [p for p in plans if p.ownership is not None]
    return ProfileResult(
        source,
        g.unclear,
        {p.table.name: p.ownership for p in decided},  # type: ignore[misc]
        {p.table.name: p.ownership_reason for p in decided},
    )


# ---- Django / Flask front ends (an AuthModel instead of FastAPI dependencies) ----------------

ERROR_STYLES = {"django": "drf", "flask": "json_key(message)"}


def _frontend_decoder(g: _Generator, model: RepoModel, users: str) -> tuple[NativeCandidate, str]:
    """The token decoder of a front end's auth: the app's own (DRF authentication,
    Flask-HTTPAuth, a session...) is not lifted, so the profile's ``NativeToken`` names
    a native that refuses to run (``lattice.importer.app_auth.not_lifted``): serving a
    program on this profile fails loudly, never silently. Layer R probes the original
    app, so the rule layer does not need it."""
    from lattice.importer.appmodel import NativeParam

    fe = model.auth
    assert fe is not None
    key = next((c for c in g.tables[users].columns if c.primary_key), None)
    key_type = key.type if key is not None and key.type in KEY_CONVERT else "Int"
    g.note(
        "auth",
        users,
        f"the app's own auth ({model.framework}, {fe.scheme}) is not lifted: the profile's "
        "NativeToken names caller_key, which refuses to run (lattice.importer.app_auth)",
    )
    decoder = NativeCandidate(
        "caller_key",
        "lattice.importer.app_auth:not_lifted",
        "token_decoder",
        (NativeParam("token", "str", "Str"),),
        PY_TYPE[key_type],
        key_type,
        nullable=True,
        imports=("from lattice.importer.app_auth import not_lifted",),
        decode_expr=f"not_lifted({model.framework!r}, {fe.scheme!r}, token)",
        token_param="token",
        claim="sub",
    )
    return decoder, f"NativeSig((Scalar.STR,), Scalar.{SCALAR_ATTR[key_type]}, nullable=True)"


def _frontend_deps(model: RepoModel, fe: object) -> tuple[Dependency | None, Dependency | None]:
    """The active check and the admin flag a front end's AuthModel states, in the shape
    of FastAPI's dependencies (so the profile settings render alike)."""
    active_answer = fe.inactive_answer  # type: ignore[attr-defined]
    guard = next(
        (g for r in model.routes for g in r.guards if g.kind == "active" and g.status), None
    )
    status, detail = active_answer if active_answer else (None, None)
    if guard is not None and (status is None or not detail):
        status, detail = guard.status, guard.detail
    active = None
    if fe.active_column:  # type: ignore[attr-defined]
        active = Dependency(
            "auth", model.app_package, "active_user",
            active_column=fe.active_column,  # type: ignore[attr-defined]
            active_status=status if status in (400, 401, 403) else None,
            active_detail=detail or None,
        )  # fmt: skip
    admin = None
    if fe.admin_column and model.role_scheme is None:  # type: ignore[attr-defined]
        admin = Dependency("auth", model.app_package, "admin",
                           admin_column=fe.admin_column,  # type: ignore[attr-defined]
                           admin_detail=_forbidden_detail(model))  # fmt: skip
    return active, admin


def _forbidden_detail(model: RepoModel) -> str | None:
    """The detail most role / admin / owner guards refuse with (DRF's "You do not have
    permission to perform this action.")."""
    from collections import Counter

    found = Counter(
        g.detail
        for r in model.routes
        for g in r.guards
        if g.kind in ("role", "admin", "object_owner", "custom") and g.status == 403 and g.detail
    )
    return found.most_common(1)[0][0] if found else None


def _frontend_errors(model: RepoModel, fe: object, scheme: object) -> list[str]:
    """A front end's error convention: DRF's bodies (``drf``) or Flask's error handlers'
    ``message`` key, 400 validation errors, and the auth answers' details."""
    out = [f"error_body={ERROR_STYLES.get(model.framework, 'fastapi')!r}",
           "validation_status=400"]  # fmt: skip
    unauth = fe.unauthenticated_answer  # type: ignore[attr-defined]
    if unauth and unauth[0] == 401 and unauth[1]:
        out.append(f"unauthorized_detail={unauth[1]!r}")
    forbidden = _forbidden_detail(model) or getattr(scheme, "detail", None)
    if forbidden:
        out.append(f"forbidden_detail={forbidden!r}")
    return out


AUTH_SETTINGS = (
    "users_table=", "auth=", "roles_from=", "active_column=", "inactive_status=",
    "inactive_detail=", "unauthorized_detail=", "forbidden_detail=",
)  # fmt: skip


def _render_profile(head: list[str], api: list[str]) -> str:
    out = [*head, "    api = ApiProfile("]
    out += [f"        {line}," for line in api]
    out += ["    )", "    return Profile(schemas={}, api=api)", ""]
    return "\n".join(out)


def _token_answer_ok(answer: object) -> bool:
    """Whether ``answer`` is a (status, detail) ``NativeToken(invalid=/unknown=)``
    accepts (see ``NativeToken.__post_init__``)."""
    return (
        isinstance(answer, tuple)
        and len(answer) == 2
        and type(answer[0]) is int
        and answer[0] in TOKEN_ANSWER_STATUSES
        and isinstance(answer[1], str)
        and bool(answer[1])
    )


def _refused(source: str) -> str | None:
    """Why building the generated profile fails (a ValueError of ApiProfile), or None."""
    from types import ModuleType

    module = ModuleType("lattice_import_profile_check")
    try:
        exec(compile(source, "<generated profile>", "exec"), module.__dict__)  # noqa: S102 -- our own generated source
    except Exception:  # noqa: BLE001 -- the original is not importable here: not ours to judge
        return None
    try:
        module.profile()
    except ValueError as err:
        return str(err)
    return None


__all__ = ["ProfileResult", "Unclear", "generate_profile"]
