"""S4 and S5 of the importer's evidence sources (spec 2026-09-27 importer §3.6): facts
read from *outside* the source code.

* :func:`openapi_facts` reads a FastAPI app's ``app.openapi()``: endpoints, parameters
  and their bounds, request and response schemas, declared status codes, security.
* :func:`db_facts` introspects a SQLite database (``PRAGMA``): tables, columns and
  declared types, nullability, primary and foreign keys, unique and plain indexes, and
  the **storage spellings** sampled from rows (a Uuid as 32 hex digits or hyphenated, a
  DateTime with ``T`` or a space, with ``Z``, an offset or no zone).
* :func:`reconcile` compares both with the extractor's :class:`RepoModel` and returns a
  :class:`Disagreement` per fact they disagree on.

All facts are plain frozen dataclasses.
"""

from __future__ import annotations

import ast
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lattice.importer.extract import DataModel, FieldSpec, RepoModel, Route, TableModel

__all__ = [
    "ApiParam",
    "DbColumn",
    "DbFacts",
    "DbForeignKey",
    "DbTable",
    "Disagreement",
    "Endpoint",
    "OpenApiFacts",
    "SchemaFacts",
    "SchemaProp",
    "db_facts",
    "openapi_facts",
    "reconcile",
    "spelling",
]

BOUND_KEYS = (
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "pattern",
    "minItems",
    "maxItems",
)
HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")

# ---- S4: OpenAPI -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ApiParam:
    name: str
    location: str  # path | query | header | cookie
    type: str | None  # the JSON-schema type ("string", "integer", ...), nulls removed
    format: str | None = None
    required: bool = False
    nullable: bool = False
    default: Any = None
    bounds: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SchemaProp:
    name: str
    type: str | None
    format: str | None = None
    ref: str | None = None  # the schema a property refers to ($ref or array items)
    required: bool = False
    nullable: bool = False
    default: Any = None
    bounds: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SchemaFacts:
    name: str
    properties: tuple[SchemaProp, ...]


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str
    operation: str | None  # the endpoint function's name (from app.routes)
    params: tuple[ApiParam, ...]
    body: str | None  # the request body schema name
    body_media: str | None
    responses: Mapping[int, str | None]  # status -> response schema name (None: inline/none)
    secured: bool

    @property
    def statuses(self) -> tuple[int, ...]:
        return tuple(sorted(self.responses))

    @property
    def success(self) -> int | None:
        ok = [s for s in self.statuses if 200 <= s < 300]
        return ok[0] if ok else None


@dataclass(frozen=True)
class OpenApiFacts:
    endpoints: tuple[Endpoint, ...]
    schemas: Mapping[str, SchemaFacts]

    def endpoint(self, method: str, path: str) -> Endpoint:
        for e in self.endpoints:
            if e.method == method.upper() and e.path == path:
                return e
        raise KeyError(f"{method} {path}")


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _schema_shape(schema: Mapping[str, Any]) -> tuple[str | None, str | None, str | None, bool]:
    """(type, format, ref, nullable) of a JSON schema, looking through anyOf-with-null."""
    nullable = False
    options = schema.get("anyOf") or schema.get("oneOf")
    if options:
        rest = [o for o in options if o.get("type") != "null"]
        nullable = len(rest) != len(options)
        if len(rest) == 1:
            t, f, r, n = _schema_shape(rest[0])
            return t, f, r, nullable or n
        return None, None, None, nullable
    if "$ref" in schema:
        return "object", None, _ref_name(schema["$ref"]), False
    t = schema.get("type")
    if isinstance(t, list):
        nullable = "null" in t
        t = next((x for x in t if x != "null"), None)
    ref = None
    if t == "array" and isinstance(schema.get("items"), Mapping):
        ref = _schema_shape(schema["items"])[2]
    return t, schema.get("format"), ref, nullable


def _bounds(schema: Mapping[str, Any]) -> dict[str, Any]:
    out = {k: schema[k] for k in BOUND_KEYS if k in schema}
    for o in schema.get("anyOf") or ():
        out |= {k: o[k] for k in BOUND_KEYS if k in o}
    return out


def _schema_facts(name: str, schema: Mapping[str, Any]) -> SchemaFacts:
    required = set(schema.get("required", ()))
    props = []
    for pname, p in (schema.get("properties") or {}).items():
        t, f, ref, nullable = _schema_shape(p)
        props.append(
            SchemaProp(pname, t, f, ref, pname in required, nullable, p.get("default"), _bounds(p))
        )
    return SchemaFacts(name, tuple(props))


def openapi_facts(app: Any) -> OpenApiFacts:
    """The facts of ``app.openapi()`` (a FastAPI app, or anything with ``openapi()``)."""
    doc = app.openapi()
    names: dict[tuple[str, str], str] = {}
    for r in getattr(app, "routes", ()):
        for m in getattr(r, "methods", None) or ():
            if hasattr(r, "path") and hasattr(r, "name"):
                names[(m.upper(), r.path)] = r.name
    endpoints = []
    for path, ops in (doc.get("paths") or {}).items():
        for method, op in ops.items():
            if method not in HTTP_METHODS:
                continue
            params = []
            for p in op.get("parameters", ()):
                schema = p.get("schema", {})
                t, f, _, nullable = _schema_shape(schema)
                params.append(
                    ApiParam(p["name"], p["in"], t, f, bool(p.get("required")), nullable,
                             schema.get("default"), _bounds(schema))
                )  # fmt: skip
            body = media = None
            content = (op.get("requestBody") or {}).get("content") or {}
            if content:
                media, spec = next(iter(content.items()))
                body = _schema_shape(spec.get("schema", {}))[2]
            responses: dict[int, str | None] = {}
            for status, resp in (op.get("responses") or {}).items():
                if not str(status).isdigit():
                    continue
                schema = ((resp.get("content") or {}).get("application/json") or {}).get("schema")
                responses[int(status)] = _schema_shape(schema)[2] if schema else None
            secured = bool(op.get("security", doc.get("security")))
            endpoints.append(
                Endpoint(method.upper(), path, names.get((method.upper(), path)), tuple(params),
                         body, media, responses, secured)
            )  # fmt: skip
    schemas = {
        n: _schema_facts(n, s)
        for n, s in ((doc.get("components") or {}).get("schemas") or {}).items()
    }
    return OpenApiFacts(tuple(endpoints), schemas)


# ---- S5: the database ------------------------------------------------------------------------

UUID_HEX = re.compile(r"^[0-9a-fA-F]{32}$")
UUID_DASHED = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?P<sep>[T ])\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?P<zone>Z|[+-]\d{2}:?\d{2})?$"
)
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SAMPLE = 50


def spelling(value: object) -> str | None:
    """The storage spelling of one stored value: ``hex32`` / ``canonical`` (a Uuid),
    ``iso_z`` / ``sql_space`` / ``iso_t`` / ``iso_offset`` / ``sql_z`` / ``sql_offset``
    (a DateTime: separator then zone), ``date``; None for anything else."""
    if not isinstance(value, str):
        return None
    if UUID_HEX.match(value):
        return "hex32"
    if UUID_DASHED.match(value):
        return "canonical"
    m = DATETIME.match(value)
    if m:
        sep = "iso" if m["sep"] == "T" else "sql"
        zone = m["zone"]
        if zone is None:
            return "iso_t" if sep == "iso" else "sql_space"
        return f"{sep}_{'z' if zone == 'Z' else 'offset'}"
    if DATE.match(value):
        return "date"
    return None


@dataclass(frozen=True)
class DbColumn:
    name: str
    type: str  # the declared type, as written
    nullable: bool
    default: str | None
    pk: int  # position in the primary key (0: not part of it)
    storage: str | None = None  # the sampled spelling (see spelling()), "mixed(...)" if several


@dataclass(frozen=True)
class DbForeignKey:
    columns: tuple[str, ...]
    table: str
    ref_columns: tuple[str, ...]
    on_delete: str
    on_update: str


@dataclass(frozen=True)
class DbIndex:
    name: str
    columns: tuple[str, ...]
    unique: bool
    origin: str  # c (CREATE INDEX) | u (UNIQUE constraint) | pk


@dataclass(frozen=True)
class DbTable:
    name: str
    columns: tuple[DbColumn, ...]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[DbForeignKey, ...]
    indexes: tuple[DbIndex, ...]
    rows: int

    @property
    def unique(self) -> tuple[tuple[str, ...], ...]:
        """Column sets a UNIQUE index or constraint covers (the primary key excluded)."""
        return tuple(i.columns for i in self.indexes if i.unique and i.origin != "pk")

    def column(self, name: str) -> DbColumn:
        return next(c for c in self.columns if c.name == name)


@dataclass(frozen=True)
class DbFacts:
    tables: tuple[DbTable, ...]

    def table(self, name: str) -> DbTable:
        for t in self.tables:
            if t.name == name:
                return t
        raise KeyError(name)


def _q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _column_storage(conn: sqlite3.Connection, table: str, column: str) -> str | None:
    values = [
        r[0]
        for r in conn.execute(
            f"SELECT {_q(column)} FROM {_q(table)} WHERE {_q(column)} IS NOT NULL "
            f"ORDER BY rowid LIMIT {SAMPLE}"
        )
    ]
    kinds = [spelling(v) for v in values]
    if not kinds or any(k is None for k in kinds):
        return None
    distinct = sorted(set(kinds))  # type: ignore[type-var]
    return distinct[0] if len(distinct) == 1 else f"mixed({','.join(distinct)})"  # type: ignore[arg-type]


def db_facts(db_path: Path | str) -> DbFacts:
    """The schema of the SQLite database at ``db_path``, with sampled spellings."""
    conn = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    try:
        names = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        tables = []
        for t in names:
            cols = [
                DbColumn(name, type_, not notnull and not pk, default, pk,
                         _column_storage(conn, t, name))
                for _, name, type_, notnull, default, pk in conn.execute(
                    f"PRAGMA table_info({_q(t)})")
            ]  # fmt: skip
            pk = tuple(c.name for c in sorted((c for c in cols if c.pk), key=lambda c: c.pk))
            fks: dict[int, list[tuple]] = {}
            for row in conn.execute(f"PRAGMA foreign_key_list({_q(t)})"):
                fks.setdefault(row[0], []).append(row)
            foreign = tuple(
                DbForeignKey(
                    tuple(r[3] for r in rows),
                    rows[0][2],
                    tuple(r[4] for r in rows),
                    rows[0][6],
                    rows[0][5],
                )  # fmt: skip
                for _, rows in sorted(fks.items())
            )
            indexes = []
            for _, iname, unique, origin, _partial in conn.execute(f"PRAGMA index_list({_q(t)})"):
                icols = tuple(r[2] for r in conn.execute(f"PRAGMA index_info({_q(iname)})"))
                indexes.append(DbIndex(iname, icols, bool(unique), origin))
            rows = conn.execute(f"SELECT COUNT(*) FROM {_q(t)}").fetchone()[0]
            tables.append(
                DbTable(t, tuple(cols), pk, foreign, tuple(sorted(indexes, key=lambda i: i.name)),
                        rows)
            )  # fmt: skip
        return DbFacts(tuple(tables))
    finally:
        conn.close()


# ---- reconciliation --------------------------------------------------------------------------


@dataclass(frozen=True)
class Disagreement:
    """``source`` (openapi | db) says ``other`` where the extractor says ``extracted``
    about ``field`` of ``subject``."""

    source: str
    subject: str
    field: str
    extracted: Any
    other: Any
    note: str = ""


JSON_TYPES = {
    "Str": ("string", None),
    "Email": ("string", "email"),
    "Int": ("integer", None),
    "Float": ("number", None),
    "Bool": ("boolean", None),
    "Uuid": ("string", "uuid"),
    "DateTime": ("string", "date-time"),
    "Date": ("string", "date"),
    "List": ("array", None),
}
FIELD_BOUNDS = (
    ("min_length", "minLength"),
    ("max_length", "maxLength"),
    ("ge", "minimum"),
    ("le", "maximum"),
    ("gt", "exclusiveMinimum"),
    ("lt", "exclusiveMaximum"),
)
# SQLite type affinity (https://sqlite.org/datatype3.html §3.1) per extracted type
AFFINITIES = {
    "Str": {"TEXT"},
    "Email": {"TEXT"},
    "Uuid": {"TEXT"},
    "Int": {"INTEGER"},
    "Float": {"REAL"},
    "Bool": {"NUMERIC", "INTEGER"},
    "DateTime": {"NUMERIC", "TEXT"},
    "Date": {"NUMERIC", "TEXT"},
}
# how the importer's profile says a SQLite app stores values (profile_gen.generate_profile)
SQLITE_STORAGE = {"Uuid": "hex32", "DateTime": "sql_space"}


def affinity(declared: str) -> str:
    t = declared.upper()
    if "INT" in t:
        return "INTEGER"
    if any(s in t for s in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in t or not t:
        return "BLOB"
    if any(s in t for s in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def _declared_length(declared: str) -> int | None:
    m = re.search(r"\((\d+)\)", declared)
    return int(m[1]) if m else None


def _literal(text: str | None) -> Any:
    if text is None:
        return None
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text


class _Reconciler:
    def __init__(self) -> None:
        self.found: list[Disagreement] = []

    def differ(
        self, source: str, subject: str, what: str, mine: Any, theirs: Any, note: str = ""
    ) -> None:
        if mine != theirs:
            self.found.append(Disagreement(source, subject, what, mine, theirs, note))

    # -- OpenAPI --

    def endpoints(self, routes: Sequence[Route], api: OpenApiFacts) -> None:
        served = {(e.method, e.path): e for e in api.endpoints}
        listed = {(r.method, r.path) for r in routes}
        for r in routes:
            subject = f"{r.method} {r.path}"
            e = served.get((r.method, r.path))
            if e is None:
                self.differ("openapi", subject, "endpoint", "present", "absent",
                            "the route is in the source but the running app does not serve "
                            "it (a conditional include_router?)")  # fmt: skip
                continue
            self.route(r, e, subject)
        for key, e in served.items():
            if key not in listed:
                note = "the running app serves a route the extractor did not find"
                self.differ(
                    "openapi", f"{e.method} {e.path}", "endpoint", "absent", "present", note
                )

    def route(self, r: Route, e: Endpoint, subject: str) -> None:
        self.differ("openapi", subject, "status", r.status_code or 200, e.success)
        self.differ(
            "openapi", subject, "response", r.response_model, e.responses.get(e.success or 200)
        )
        if r.auth != "unclear":
            self.differ("openapi", subject, "secured", r.auth != "Public", e.secured)
        params = {p.name: p for p in e.params}
        for p in r.params:
            if p.source not in ("path", "query", "header", "cookie"):
                continue
            ap = params.pop(p.name, None)
            if ap is None:
                self.differ("openapi", f"{subject} {p.name}", "param", p.source, None)
                continue
            self.differ("openapi", f"{subject} {p.name}", "location", p.source, ap.location)
            self.differ("openapi", f"{subject} {p.name}", "required", p.required, ap.required)
            if not p.required:
                self.differ(
                    "openapi", f"{subject} {p.name}", "default", _literal(p.default), ap.default
                )
            if p.type in JSON_TYPES:
                self.differ("openapi", f"{subject} {p.name}", "type", JSON_TYPES[p.type],
                            (ap.type, ap.format if JSON_TYPES[p.type][1] else None))  # fmt: skip
        for name, ap in params.items():
            self.differ("openapi", f"{subject} {name}", "param", None, ap.location)
        forms = [p for p in r.params if p.source == "form"]
        if forms:
            self.differ("openapi", subject, "body_media", "application/x-www-form-urlencoded",
                        e.body_media)  # fmt: skip
        elif len(r.body_models) == 1:
            self.differ("openapi", subject, "body", r.body_models[0], e.body)
        elif not r.body_models:
            self.differ("openapi", subject, "body", None, e.body)

    def schemas(self, models: Sequence[DataModel | TableModel], api: OpenApiFacts) -> None:
        by_name: dict[str, SchemaFacts] = {}
        for name, s in api.schemas.items():
            base = name.removesuffix("-Input").removesuffix("-Output")
            by_name.setdefault(base, s)
        for m in models:
            s = by_name.get(m.name)
            if s is None:
                continue
            fields = m.fields if isinstance(m, DataModel) else m.columns
            props = {p.name: p for p in s.properties}
            for f in fields:
                subject = f"{m.name}.{f.name}"
                p = props.pop(f.name, None)
                if p is None:
                    self.differ("openapi", subject, "property", "present", "absent")
                    continue
                self.field(f, p, subject)
            for name in props:
                self.differ("openapi", f"{m.name}.{name}", "property", "absent", "present")

    def field(self, f: FieldSpec, p: SchemaProp, subject: str) -> None:
        self.differ("openapi", subject, "required", f.required, p.required)
        self.differ("openapi", subject, "nullable", f.nullable, p.nullable)
        if f.type in JSON_TYPES:
            want = JSON_TYPES[f.type]
            self.differ("openapi", subject, "type", want, (p.type, p.format if want[1] else None))
        for attr, key in FIELD_BOUNDS:
            self.differ("openapi", subject, key, getattr(f, attr), p.bounds.get(key))

    # -- the database --

    def tables(self, tables: Sequence[TableModel], db: DbFacts, dialect: str | None) -> None:
        stored = {t.name: t for t in db.tables}
        for t in tables:
            dt = stored.pop(t.table_name, None)
            if dt is None:
                self.differ("db", t.table_name, "table", "present", "absent")
                continue
            self.table(t, dt, dialect)
        for name in stored:
            self.differ("db", name, "table", "absent", "present")

    def table(self, t: TableModel, dt: DbTable, dialect: str | None) -> None:
        cols = {c.name: c for c in dt.columns}
        pk = tuple(c.name for c in t.columns if c.primary_key)
        self.differ("db", t.table_name, "primary_key", pk, dt.primary_key)
        fks = {fk.columns[0]: f"{fk.table}.{fk.ref_columns[0]}" for fk in dt.foreign_keys
               if len(fk.columns) == 1}  # fmt: skip
        unique = {u[0] for u in dt.unique if len(u) == 1}
        indexed = {i.columns[0] for i in dt.indexes if i.origin != "pk" and i.columns}
        for f in t.columns:
            subject = f"{t.table_name}.{f.name}"
            c = cols.pop(f.name, None)
            if c is None:
                self.differ("db", subject, "column", "present", "absent")
                continue
            if not f.primary_key:
                self.differ("db", subject, "nullable", f.nullable, c.nullable)
                self.differ("db", subject, "unique", f.unique, f.name in unique)
                self.differ("db", subject, "index", f.index, f.name in indexed)
            self.differ("db", subject, "foreign_key", f.foreign_key, fks.pop(f.name, None))
            if f.type in AFFINITIES and affinity(c.type) not in AFFINITIES[f.type]:
                self.differ("db", subject, "type", f.type, c.type,
                            f"affinity {affinity(c.type)}")  # fmt: skip
            length = _declared_length(c.type)
            if f.max_length is not None and length is not None:
                self.differ("db", subject, "max_length", f.max_length, length)
            if dialect == "sqlite" and f.type in SQLITE_STORAGE and c.storage is not None:
                self.differ("db", subject, "storage", SQLITE_STORAGE[f.type], c.storage,
                            "the profile's storage spelling for SQLite")  # fmt: skip
        for name in cols:
            self.differ("db", f"{t.table_name}.{name}", "column", "absent", "present")
        for name, target in fks.items():
            self.differ("db", f"{t.table_name}.{name}", "foreign_key", None, target)


def reconcile(
    model: RepoModel, openapi: OpenApiFacts | None, db: DbFacts | None
) -> list[Disagreement]:
    """Every fact the OpenAPI document or the database states differently from the
    extractor: endpoints (presence, success status, response model, security, path and
    query parameters, request body), schemas (properties, required, nullable, type,
    bounds) and tables (presence, columns, primary and foreign keys, nullability, unique,
    index, type affinity, VARCHAR length, and -- for SQLite -- the storage spelling the
    profile assumes)."""
    r = _Reconciler()
    if openapi is not None:
        r.endpoints(model.routes, openapi)
        r.schemas([*model.models, *model.tables], openapi)
    if db is not None:
        r.tables(model.tables, db, model.dialect)
    return r.found
