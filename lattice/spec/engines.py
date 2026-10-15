"""Database engines of the spec checker as **data**: one descriptor per server engine, read
from TOML, validated, and kept in a registry that ``db.py``, ``dbserver.py``,
``invariants.py``, ``race.py`` and the ``--database`` parsers read instead of hard-coded
tables (spike 2, the descriptor-only slice; evidence:
``docs/results/2026-10-12-m-r-engine-seams.md``; the field reference:
``docs/design/lattice-module-descriptor.md``).

The built-in server engines (``postgres``, ``mysql``, ``clickhouse``) ship as
``lattice/spec/engines/*.toml``. SQLite is not a descriptor: it stays built in
(:data:`SQLITE`). A project adds an engine without touching the core:

* ``studio.toml`` ``[engines] paths = ["engines/mariadb.toml"]`` (Studio and ``lattice
  check --project`` load them), or ``lattice spec check --engine-file FILE`` (repeatable);
* an installed module (``lattice module install``, :mod:`lattice.modules`) of kind
  ``engine``.

An ``adapter`` that names a built-in adapter (``postgres``, ``mysql``, ``clickhouse``)
needs no code; ``module:Class`` names a :class:`lattice.spec.db.Database` subclass, imported
on first use (the descriptor's directory is put on ``sys.path``). That is the one code hook
of this slice (the SDK surface is documented, not built).

A descriptor (every key; ``?`` = optional, with its default)::

    name = "mysql"                    # [a-z][a-z0-9_-]*; also the --database value
    title? = name                     # for notes ("not applicable on ClickHouse")
    aliases? = []                     # more --database spellings (postgres: postgresql)
    url_schemes = ["mysql", "mariadb"]  # URL schemes resolving to this engine
    url_template = "mysql://{user}:{password}@{host}:{port}/{db}"   # the canonical URL
    default_port = 3306
    adapter = "mysql"                 # a built-in adapter name, or "module:Class"
    requires? = []                    # pip requirements (shown, never installed)
    clone = "code"                    # "template" (sql.clone_template) | "code" (adapter)

    [drivers]                         # url_for(ref, driver): a template, or a table
    sqlalchemy = "mysql+pymysql://{rest}"
    [drivers.go-mysql]                # (a table form; inline tables work too)
    template = "{raw_auth}tcp({hostport})/{db}"
    query_fixed = { parseTime = "true" }
    query_url = true

    [sql]
    identifier_quote = "`"            # one character: '"' or '`'
    placeholder? = "%s"
    true_literal = "TRUE"
    false_literal = "FALSE"
    backslash_escapes = true          # a backslash in a text literal is doubled
    float_cast? = "{x}"               # ClickHouse: "toFloat64({x})"
    divide? = "(({a}) * 1.0 / ({b}))"  # a and b already float_cast
    list_tables = "SELECT ..."        # one column: the table names
    list_columns = "SELECT ... = %s"  # one parameter: the table; one column: the names
    drop_database = "DROP DATABASE IF EXISTS {name}"
    terminate_sessions? = ""          # one parameter: the database name ("" = none)
    clone_template? = ""              # for clone = "template":
                                      #   "CREATE DATABASE {name} TEMPLATE {template}"

    [capabilities]                    # only these two flags
    unique_constraints = true
    transactions = true

    [server]                          # the throwaway container
    image = "mysql:8.4"
    port? = default_port
    data_dir = "/var/lib/mysql"       # a tmpfs
    admin_user = "root"               # "{user}" = the checker's own account
    admin_db = "mysql"
    env? = { MYSQL_ROOT_PASSWORD = "{password}" }
    args? = []                        # the server's own arguments
    docker_args? = []                 # more ``docker run`` options (ClickHouse: --ulimit)
    ready_sql? = "SELECT 1"

Registries. The process has one base registry (the built-ins, then what the CLI flags
``--engine-file`` and ``--modules`` add). ``REGISTRY`` is a proxy for the registry of the
*current context*: a project's own registry once :func:`activate` or :func:`using` made it
current (``lattice.modules.load_project_engines`` builds one per project: the built-ins, its
``[engines] paths``, its verified modules), else the base. Two projects in one process
(Studio's hub) can therefore declare engines of the same name, and a thread that never
activated a project's registry sees the built-ins only (it fails closed: an unknown
``--database``), never the registry another request loaded.

Errors are :class:`EngineError` with exact messages (``tests/spec/test_engines.py``).
"""

from __future__ import annotations

import contextlib
import contextvars
import importlib
import re
import sys
import threading
import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

HERE = Path(__file__).resolve().parent / "engines"
BUILTIN_FILES = ("postgres.toml", "mysql.toml", "clickhouse.toml")
SQLITE = "sqlite"  # built in, not a descriptor
# adapter names that need no code: lattice.spec.db's classes
BUILTIN_ADAPTERS = {
    "postgres": "PostgresDatabase",
    "mysql": "MysqlDatabase",
    "clickhouse": "ClickhouseDatabase",
}
# built-in adapters that have a clone procedure (lattice.spec.dbserver), for clone = "code"
CODE_CLONES = ("mysql", "clickhouse")
CAPABILITIES = ("transactions", "unique_constraints")
SOURCES = ("builtin", "project", "module")

_NAME = re.compile(r"[a-z][a-z0-9_-]*")
_SCHEME = re.compile(r"[a-z][a-z0-9.-]*")
_ADAPTER_REF = re.compile(r"[A-Za-z_][\w.]*:[A-Za-z_][\w.]*")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")

# the placeholders each template may use
URL_VARS = ("user", "password", "host", "port", "db")
DRIVER_VARS = (
    "url",
    "rest",
    "auth",
    "raw_auth",
    "host",
    "port",
    "hostport",
    "db",
    "user",
    "password",
)
SERVER_VARS = ("user", "password")


class EngineError(ValueError):
    """A descriptor that cannot be read, is invalid, or clashes with a registered engine."""


def fill(text: str, /, **values: str) -> str:
    """``text`` with each ``{name}`` replaced by ``values[name]``, in one pass (a value
    holding ``{...}`` is never substituted again)."""
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], text)


# ---- the descriptor -------------------------------------------------------------------------


@dataclass(frozen=True)
class Driver:
    """One ``url_for`` form: ``template`` plus an optional query string built from the
    user and password (``query_auth``), fixed parameters, then the URL's own query."""

    template: str
    query_auth: bool = False
    query_fixed: tuple[tuple[str, str], ...] = ()
    query_url: bool = False

    @property
    def has_query(self) -> bool:
        return self.query_auth or bool(self.query_fixed) or self.query_url


@dataclass(frozen=True)
class Sql:
    identifier_quote: str
    true_literal: str
    false_literal: str
    backslash_escapes: bool
    list_tables: str
    list_columns: str
    drop_database: str
    placeholder: str = "%s"
    float_cast: str = "{x}"
    divide: str = "(({a}) * 1.0 / ({b}))"
    terminate_sessions: str = ""
    clone_template: str = ""


@dataclass(frozen=True)
class Server:
    image: str
    port: int
    data_dir: str
    admin_user: str
    admin_db: str
    env: tuple[tuple[str, str], ...] = ()
    args: tuple[str, ...] = ()
    docker_args: tuple[str, ...] = ()
    ready_sql: str = "SELECT 1"


@dataclass(frozen=True)
class EngineDescriptor:
    """One server engine (see the module docstring for every field)."""

    name: str
    title: str
    aliases: tuple[str, ...]
    url_schemes: tuple[str, ...]
    url_template: str
    default_port: int
    adapter: str
    requires: tuple[str, ...]
    clone: str
    drivers: tuple[tuple[str, Driver], ...]
    sql: Sql
    capabilities: tuple[tuple[str, bool], ...]
    server: Server
    # where it was read from (for module:Class imports and messages); not compared
    base_dir: Path | None = field(default=None, compare=False)

    # -- derived

    def has(self, capability: str) -> bool:
        return dict(self.capabilities)[capability]

    def driver(self, name: str) -> Driver | None:
        return dict(self.drivers).get(name)

    def quote_ident(self, name: str) -> str:
        q = self.sql.identifier_quote
        return q + name.replace(q, q + q) + q

    def canonical_url(self, host: str, port: int, user: str, password: str, db: str) -> str:
        return fill(
            self.url_template,
            user=quote(user, safe=""),
            password=quote(password, safe=""),
            host=host,
            port=str(port),
            db=db,
        )

    def template_scheme(self) -> str:
        return self.url_template.split("://", 1)[0].lower()

    def admin_user(self, user: str) -> str:
        return fill(self.server.admin_user, user=user, password="")

    def run_args(self, user: str, password: str) -> tuple[list[str], list[str]]:
        """(``docker run`` options, the server's own arguments) for the container."""
        opts = ["--tmpfs", f"{self.server.data_dir}:rw"]
        for key, value in self.server.env:
            opts += ["-e", f"{key}={fill(value, user=user, password=password)}"]
        opts += list(self.server.docker_args)
        return opts, list(self.server.args)

    def adapter_class(self) -> type:
        """The :class:`lattice.spec.db.Database` subclass this engine opens connections
        with (imported on first use)."""
        return _adapter_class(self.name, self.adapter, self.base_dir)

    def to_dict(self) -> dict[str, Any]:
        """The descriptor as plain data (what the TOML says, defaults filled in)."""
        drivers: dict[str, Any] = {}
        for name, d in self.drivers:
            if d.has_query:
                entry: dict[str, Any] = {"template": d.template}
                if d.query_auth:
                    entry["query_auth"] = True
                if d.query_fixed:
                    entry["query_fixed"] = dict(d.query_fixed)
                if d.query_url:
                    entry["query_url"] = True
                drivers[name] = entry
            else:
                drivers[name] = d.template
        sql = {f: getattr(self.sql, f) for f in _SQL_FIELDS}
        server = {f: getattr(self.server, f) for f in _SERVER_FIELDS}
        server["env"] = dict(self.server.env)
        server["args"] = list(self.server.args)
        server["docker_args"] = list(self.server.docker_args)
        return {
            "name": self.name,
            "title": self.title,
            "aliases": list(self.aliases),
            "url_schemes": list(self.url_schemes),
            "url_template": self.url_template,
            "default_port": self.default_port,
            "adapter": self.adapter,
            "requires": list(self.requires),
            "clone": self.clone,
            "drivers": drivers,
            "sql": sql,
            "capabilities": dict(self.capabilities),
            "server": server,
        }


_TOP_FIELDS = (
    "name",
    "title",
    "aliases",
    "url_schemes",
    "url_template",
    "default_port",
    "adapter",
    "requires",
    "clone",
    "drivers",
    "sql",
    "capabilities",
    "server",
)
_TOP_REQUIRED = (
    "name",
    "url_schemes",
    "url_template",
    "default_port",
    "adapter",
    "clone",
    "sql",
    "capabilities",
    "server",
)
_SQL_FIELDS = (
    "identifier_quote",
    "placeholder",
    "true_literal",
    "false_literal",
    "backslash_escapes",
    "float_cast",
    "divide",
    "list_tables",
    "list_columns",
    "drop_database",
    "terminate_sessions",
    "clone_template",
)
_SQL_REQUIRED = (
    "identifier_quote",
    "true_literal",
    "false_literal",
    "backslash_escapes",
    "list_tables",
    "list_columns",
    "drop_database",
)
_SERVER_FIELDS = (
    "image",
    "port",
    "data_dir",
    "admin_user",
    "admin_db",
    "env",
    "args",
    "docker_args",
    "ready_sql",
)
_SERVER_REQUIRED = ("image", "data_dir", "admin_user", "admin_db")
_DRIVER_FIELDS = ("template", "query_auth", "query_fixed", "query_url")


# ---- parsing and validation -----------------------------------------------------------------


def _type_name(value: Any) -> str:
    return {
        bool: "a boolean",
        int: "an integer",
        str: "a string",
        list: "a list",
        dict: "a table",
        float: "a number",
    }.get(type(value), type(value).__name__)


class _Reader:
    """Reads one table of a descriptor, failing with ``<where>: <key path>: <problem>``."""

    def __init__(self, where: str, prefix: str, data: Any) -> None:
        self.where, self.prefix = where, prefix
        if not isinstance(data, dict):
            raise self.err(f"must be a table, not {_type_name(data)}", key="")
        self.data = data

    def err(self, problem: str, key: str | None = None) -> EngineError:
        path = ".".join(p for p in (self.prefix, key) if p)
        return EngineError(
            f"{self.where}: {path}: {problem}" if path else f"{self.where}: {problem}"
        )

    def check_keys(self, known: tuple[str, ...], required: tuple[str, ...]) -> None:
        for key in self.data:
            if key not in known:
                table = f"[{self.prefix}] " if self.prefix else ""
                raise EngineError(
                    f"{self.where}: {table}unknown field {key!r} (known: {', '.join(known)})"
                )
        for key in required:
            if key not in self.data:
                table = f"[{self.prefix}] " if self.prefix else ""
                raise EngineError(f"{self.where}: {table}missing required field {key!r}")

    def str(self, key: str, default: str | None = None, empty: bool = False) -> str:
        value = self.data.get(key, default)
        if not isinstance(value, str):
            raise self.err(f"must be a string, not {_type_name(value)}", key)
        if not empty and not value.strip():
            raise self.err("must not be empty", key)
        return value

    def bool(self, key: str, default: bool | None = None) -> bool:
        value = self.data.get(key, default)
        if not isinstance(value, bool):
            raise self.err(f"must be true or false, not {_type_name(value)}", key)
        return value

    def int(self, key: str, default: int | None = None) -> int:
        value = self.data.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise self.err(f"must be an integer, not {_type_name(value)}", key)
        if not 0 < value < 65536:
            raise self.err(f"must be a port number (1-65535), not {value}", key)
        return value

    def strs(self, key: str, default: list[str] | None = None) -> tuple[str, ...]:
        value = self.data.get(key, default)
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise self.err("must be a list of strings", key)
        return tuple(value)

    def str_table(self, key: str) -> tuple[tuple[str, str], ...]:
        value = self.data.get(key, {})
        if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
            raise self.err("must be a table of strings", key)
        return tuple((str(k), v) for k, v in value.items())

    def template(self, key: str, allowed: tuple[str, ...], value: str | None = None) -> str:
        text = self.str(key) if value is None else value
        for name in _PLACEHOLDER.findall(text):
            if name not in allowed:
                raise self.err(
                    f"unknown placeholder {{{name}}} (known: "
                    f"{', '.join('{' + a + '}' for a in allowed)})",
                    key,
                )
        return text


def parse_descriptor(
    data: Mapping[str, Any], where: str = "engine", base_dir: Path | None = None
) -> EngineDescriptor:
    """A validated descriptor from TOML data; ``where`` prefixes every error message."""
    top = _Reader(where, "", dict(data))
    top.check_keys(_TOP_FIELDS, _TOP_REQUIRED)
    name = top.str("name")
    if not _NAME.fullmatch(name):
        raise top.err(
            f"{name!r} is not a valid engine name (lowercase letters, digits, "
            "'-' and '_', starting with a letter)",
            "name",
        )
    if name == SQLITE:
        raise top.err("'sqlite' is built in and cannot be described", "name")
    title = top.str("title", name)
    aliases = top.strs("aliases", [])
    for a in aliases:
        if not _NAME.fullmatch(a):
            raise top.err(f"{a!r} is not a valid engine name", "aliases")
    schemes = tuple(s.lower() for s in top.strs("url_schemes"))
    if not schemes:
        raise top.err("must name at least one scheme", "url_schemes")
    for s in schemes:
        if not _SCHEME.fullmatch(s):
            raise top.err(f"{s!r} is not a URL scheme", "url_schemes")
        if s == SQLITE:
            raise top.err("'sqlite' is the built-in SQLite scheme", "url_schemes")
    url_template = top.template("url_template", URL_VARS)
    if "://" not in url_template:
        raise top.err("must be a URL (scheme://...)", "url_template")
    default_port = top.int("default_port")
    adapter = top.str("adapter")
    if adapter not in BUILTIN_ADAPTERS and not _ADAPTER_REF.fullmatch(adapter):
        raise top.err(
            f"{adapter!r} is neither a built-in adapter ({', '.join(BUILTIN_ADAPTERS)})"
            " nor module:Class",
            "adapter",
        )
    requires = top.strs("requires", [])
    clone = top.str("clone")
    if clone not in ("template", "code"):
        raise top.err(f'must be "template" or "code", not {clone!r}', "clone")

    drivers_raw = top.data.get("drivers", {})
    drivers_tbl = _Reader(where, "drivers", drivers_raw)
    drivers: list[tuple[str, Driver]] = []
    for dname, spec in drivers_tbl.data.items():
        if dname == "canonical":
            raise drivers_tbl.err("'canonical' is always the URL itself", dname)
        if isinstance(spec, str):
            drivers.append((dname, Driver(drivers_tbl.template(dname, DRIVER_VARS))))
            continue
        d = _Reader(where, f"drivers.{dname}", spec)
        d.check_keys(_DRIVER_FIELDS, ("template",))
        drivers.append(
            (
                dname,
                Driver(
                    d.template("template", DRIVER_VARS),
                    d.bool("query_auth", False),
                    d.str_table("query_fixed"),
                    d.bool("query_url", False),
                ),
            )
        )

    s = _Reader(where, "sql", top.data["sql"])
    s.check_keys(_SQL_FIELDS, _SQL_REQUIRED)
    quote_char = s.str("identifier_quote")
    if quote_char not in ('"', "`"):
        raise s.err(f"must be '\"' or '`', not {quote_char!r}", "identifier_quote")
    sql = Sql(
        identifier_quote=quote_char,
        true_literal=s.str("true_literal"),
        false_literal=s.str("false_literal"),
        backslash_escapes=s.bool("backslash_escapes"),
        list_tables=s.template("list_tables", ()),
        list_columns=s.template("list_columns", ()),
        drop_database=s.template("drop_database", ("name",)),
        placeholder=s.str("placeholder", "%s"),
        float_cast=s.template("float_cast", ("x",), s.str("float_cast", "{x}")),
        divide=s.template("divide", ("a", "b"), s.str("divide", "(({a}) * 1.0 / ({b}))")),
        terminate_sessions=s.template(
            "terminate_sessions", (), s.str("terminate_sessions", "", empty=True)
        ),
        clone_template=s.template(
            "clone_template", ("name", "template"), s.str("clone_template", "", empty=True)
        ),
    )
    if "{x}" not in sql.float_cast:
        raise s.err("must use {x}", "float_cast")
    if "{a}" not in sql.divide or "{b}" not in sql.divide:
        raise s.err("must use {a} and {b}", "divide")
    if "{name}" not in sql.drop_database:
        raise s.err("must use {name}", "drop_database")
    if clone == "template" and not sql.clone_template.strip():
        raise s.err(
            'clone = "template" needs a clone template (CREATE DATABASE {name} ...)',
            "clone_template",
        )
    if clone == "template" and (
        "{name}" not in sql.clone_template or "{template}" not in sql.clone_template
    ):
        raise s.err("must use {name} and {template}", "clone_template")
    if clone == "code" and adapter in BUILTIN_ADAPTERS and adapter not in CODE_CLONES:
        raise top.err(
            f'clone = "code" needs an adapter with a clone procedure; the built-in '
            f'adapter {adapter!r} has none (use clone = "template")',
            "clone",
        )

    c = _Reader(where, "capabilities", top.data["capabilities"])
    for key in c.data:
        if key not in CAPABILITIES:
            raise EngineError(
                f"{where}: [capabilities] unknown flag {key!r} (known: {', '.join(CAPABILITIES)})"
            )
    for key in CAPABILITIES:
        if key not in c.data:
            raise EngineError(f"{where}: [capabilities] missing flag {key!r}")
    capabilities = tuple((key, c.bool(key)) for key in CAPABILITIES)

    v = _Reader(where, "server", top.data["server"])
    v.check_keys(_SERVER_FIELDS, _SERVER_REQUIRED)
    env = v.str_table("env")
    for key, value in env:
        v.template("env", SERVER_VARS, value)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise v.err(f"{key!r} is not an environment variable name", "env")
    server = Server(
        image=v.str("image"),
        port=v.int("port", default_port),
        data_dir=v.str("data_dir"),
        admin_user=v.template("admin_user", SERVER_VARS),
        admin_db=v.str("admin_db"),
        env=env,
        args=v.strs("args", []),
        docker_args=v.strs("docker_args", []),
        ready_sql=v.str("ready_sql", "SELECT 1"),
    )
    if not server.data_dir.startswith("/"):
        raise v.err(
            f"must be an absolute path in the container, not {server.data_dir!r}", "data_dir"
        )
    return EngineDescriptor(
        name,
        title,
        aliases,
        schemes,
        url_template,
        default_port,
        adapter,
        requires,
        clone,
        tuple(drivers),
        sql,
        capabilities,
        server,
        base_dir,
    )


def read_descriptor(path: Path | str) -> EngineDescriptor:
    """The descriptor in the TOML file ``path`` (validated)."""
    p = Path(path)
    where = f"engine file {p}"
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as err:
        raise EngineError(f"{where}: cannot read it ({err.strerror or err})") from err
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as err:
        raise EngineError(f"{where}: not valid TOML ({err})") from err
    return parse_descriptor(data, where, p.resolve().parent)


# ---- adapters -------------------------------------------------------------------------------

_ADAPTER_CACHE: dict[tuple[str, str], type] = {}


def _check_adapter_module_is_free(engine: str, module: str, base_dir: Path | None) -> None:
    """Refuse an adapter module whose top-level name another project's file already
    holds in ``sys.modules``: Python would hand this engine that other class (two
    projects in one process, each with an ``adapter.py``). Give the adapter module a
    distinct name."""
    if base_dir is None:
        return
    top = module.split(".")[0]
    own = [base_dir / f"{top}.py", base_dir / top / "__init__.py"]
    present = [p for p in own if p.is_file()]
    loaded = sys.modules.get(top)
    if not present or loaded is None:
        return
    where = getattr(loaded, "__file__", None)
    if where is None or any(Path(where).resolve() == p.resolve() for p in present):
        return
    raise EngineError(
        f"engine {engine!r}: adapter module {top!r} is already imported from {where}; "
        "give this engine's adapter module a distinct name"
    )


def _adapter_class(engine: str, ref: str, base_dir: Path | None) -> type:
    from lattice.spec import db

    if ref in BUILTIN_ADAPTERS:
        return getattr(db, BUILTIN_ADAPTERS[ref])
    key = (ref, str(base_dir))
    if key in _ADAPTER_CACHE:
        return _ADAPTER_CACHE[key]
    module, _, attr = ref.partition(":")
    _check_adapter_module_is_free(engine, module, base_dir)
    if base_dir is not None and str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    # an installed module's tree is hashed on every load: importing it must not write
    # __pycache__ into it
    old = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        obj: Any = importlib.import_module(module)
        for part in attr.split("."):
            obj = getattr(obj, part)
    except Exception as err:  # noqa: BLE001 -- any import failure is the descriptor's
        raise EngineError(
            f"engine {engine!r}: cannot import adapter {ref!r}: {type(err).__name__}: {err}"
        ) from err
    finally:
        sys.dont_write_bytecode = old
    if not (isinstance(obj, type) and issubclass(obj, db.Database)):
        raise EngineError(
            f"engine {engine!r}: adapter {ref!r} is not a lattice.spec.db.Database subclass"
        )
    _ADAPTER_CACHE[key] = obj
    return obj


# ---- the registry ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Registered:
    engine: EngineDescriptor
    source: str  # builtin | project | module
    path: Path | None = None
    module: str | None = None  # the installed module's name (source "module")


class EngineRegistry:
    """The server engines a process knows: the built-ins, then the ones projects and
    modules added (in that order). Names, aliases and URL schemes are unique, except that
    an added engine may take over a built-in engine's *alias* scheme (``mariadb``, which
    the built-in ``mysql`` also reads)."""

    def __init__(self) -> None:
        self._entries: dict[str, Registered] = {}
        self._lock = threading.RLock()

    # -- reading

    def names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._entries)

    def entries(self) -> list[Registered]:
        with self._lock:
            return list(self._entries.values())

    def get(self, name: str) -> EngineDescriptor | None:
        """The engine called ``name`` (or one of its aliases), else None."""
        with self._lock:
            entry = self._entries.get(name)
            if entry is not None:
                return entry.engine
            for e in self._entries.values():
                if name in e.engine.aliases:
                    return e.engine
            return None

    def entry(self, name: str) -> Registered | None:
        with self._lock:
            return self._entries.get(name)

    def by_scheme(self, scheme: str) -> EngineDescriptor | None:
        """The engine a URL scheme resolves to: an added engine before a built-in one."""
        with self._lock:
            ordered = [e for e in self._entries.values() if e.source != "builtin"]
            ordered += [e for e in self._entries.values() if e.source == "builtin"]
            for e in ordered:
                if scheme in e.engine.url_schemes:
                    return e.engine
            return None

    # -- writing

    def add(
        self,
        engine: EngineDescriptor,
        source: str,
        path: Path | None = None,
        module: str | None = None,
    ) -> EngineDescriptor:
        """Register ``engine``; the same descriptor again is a no-op. Raises EngineError on
        a clash (a name, alias or scheme another engine has)."""
        if source not in SOURCES:
            raise EngineError(f"unknown engine source {source!r}")
        with self._lock:
            known = self._entries.get(engine.name)
            if known is not None:
                if known.engine == engine:
                    return known.engine
                if known.source == "builtin":
                    raise EngineError(
                        f"engine {engine.name!r} is built in; give the descriptor another name"
                    )
                where = known.path or known.module or known.source
                raise EngineError(
                    f"engine {engine.name!r} is already registered from {where} "
                    "with a different descriptor"
                )
            for other in self._entries.values():
                o = other.engine
                clash = {engine.name, *engine.aliases} & {o.name, *o.aliases}
                if clash:
                    raise EngineError(
                        f"engine {engine.name!r}: the name {sorted(clash)[0]!r} "
                        f"is taken by engine {o.name!r}"
                    )
                for s in engine.url_schemes:
                    if s not in o.url_schemes:
                        continue
                    if other.source == "builtin" and source != "builtin" and s != o.url_schemes[0]:
                        continue  # an added engine takes over a built-in alias scheme
                    raise EngineError(
                        f"engine {engine.name!r}: URL scheme {s!r} belongs to engine {o.name!r}"
                    )
            self._entries[engine.name] = Registered(engine, source, path, module)
            return engine

    def remove(self, name: str) -> None:
        with self._lock:
            entry = self._entries.get(name)
            if entry is not None and entry.source != "builtin":
                del self._entries[name]

    @contextlib.contextmanager
    def scoped(self) -> Iterator[EngineRegistry]:
        """Engines added inside the block are forgotten after it (tests)."""
        with self._lock:
            saved = dict(self._entries)
        try:
            yield self
        finally:
            with self._lock:
                self._entries = saved


# The process registry of the CLI: the built-ins, then what ``--engine-file`` and
# ``--modules`` add. A project registry (``project_registry``, ``using``) is per context and
# never merged into it, so two projects in one process (Studio's hub) can each declare an
# engine of the same name.
_BASE = EngineRegistry()
_ACTIVE: contextvars.ContextVar[EngineRegistry | None] = contextvars.ContextVar(
    "lattice_active_engines", default=None
)


class _RegistryProxy:
    """``REGISTRY``: the registry of the current context (the project one a scope or a
    ``load_project_engines`` activated), else the process one. A thread or task that never
    activated a project registry sees the process registry only (the built-ins, plus what
    the CLI flags added), never another project's engines."""

    @staticmethod
    def current() -> EngineRegistry:
        return _ACTIVE.get() or _BASE

    def names(self) -> tuple[str, ...]:
        return self.current().names()

    def entries(self) -> list[Registered]:
        return self.current().entries()

    def get(self, name: str) -> EngineDescriptor | None:
        return self.current().get(name)

    def entry(self, name: str) -> Registered | None:
        return self.current().entry(name)

    def by_scheme(self, scheme: str) -> EngineDescriptor | None:
        return self.current().by_scheme(scheme)

    def add(
        self,
        engine: EngineDescriptor,
        source: str,
        path: Path | None = None,
        module: str | None = None,
    ) -> EngineDescriptor:
        return self.current().add(engine, source, path, module)

    def remove(self, name: str) -> None:
        self.current().remove(name)

    @contextlib.contextmanager
    def scoped(self) -> Iterator[_RegistryProxy]:
        """Engines added inside the block, and the registry activated inside it, are
        forgotten after it (tests)."""
        active = _ACTIVE.get()
        with (active or _BASE).scoped():
            try:
                yield self
            finally:
                _ACTIVE.set(active)


REGISTRY = _RegistryProxy()


def load_builtins(registry: EngineRegistry = _BASE) -> None:
    for name in BUILTIN_FILES:
        path = HERE / name
        registry.add(read_descriptor(path), "builtin", path)


load_builtins()
BUILTIN_NAMES: tuple[str, ...] = _BASE.names()


def project_registry() -> EngineRegistry:
    """A new registry holding the built-in engines only."""
    reg = EngineRegistry()
    for e in _BASE.entries():
        if e.source == "builtin":
            reg.add(e.engine, "builtin", e.path)
    return reg


def activate(registry: EngineRegistry) -> None:
    """Make ``registry`` the one the calling context sees, until something else is
    activated (no reset; a thread's context ends with the thread). Prefer
    :func:`using`, which restores the previous one."""
    _ACTIVE.set(registry)


@contextlib.contextmanager
def using(registry: EngineRegistry) -> Iterator[EngineRegistry]:
    """``registry`` is the one the calling context sees inside the block; the previous
    one is restored after it, also when the block raises."""
    previous = _ACTIVE.get()
    _ACTIVE.set(registry)
    try:
        yield registry
    finally:
        _ACTIVE.set(previous)


def load_engine_file(
    path: Path | str, source: str = "project", registry: Any = REGISTRY
) -> EngineDescriptor:
    """Read and register the descriptor in ``path``."""
    p = Path(path).resolve()
    return registry.add(read_descriptor(p), source, p)


# ---- lookups the checker uses -----------------------------------------------------------


def server_engines() -> tuple[str, ...]:
    """The server engines' names (built-ins first)."""
    return REGISTRY.names()


def database_names() -> tuple[str, ...]:
    """Every ``--database`` value: ``sqlite`` and the server engines."""
    return (SQLITE, *REGISTRY.names())


def engine(name: str) -> EngineDescriptor:
    """The server engine ``name`` (raises EngineError for an unknown one)."""
    found = REGISTRY.get(name)
    if found is None:
        raise EngineError(f"{name!r} is not a server dialect")
    return found


def find(name: str) -> EngineDescriptor | None:
    return REGISTRY.get(name)


def engine_for_scheme(scheme: str) -> EngineDescriptor | None:
    return REGISTRY.by_scheme(scheme)


def accepts_url(name: str, url: str) -> bool:
    """Whether ``url`` is a URL of engine ``name``'s servers: its scheme resolves to the
    engine, or is the scheme of the engine's canonical URL (MariaDB's ``mysql://``)."""
    scheme = urlsplit(url).scheme.lower().split("+", 1)[0]
    found = REGISTRY.by_scheme(scheme)
    e = REGISTRY.get(name)
    return (found is not None and found.name == name) or (
        e is not None and scheme == e.template_scheme()
    )


def driver_url(engine_: EngineDescriptor, url: str, parts: Any, driver: str) -> str | None:
    """``url`` (canonical, with its :class:`lattice.spec.db.UrlParts`) as ``driver`` takes it,
    from the engine's ``[drivers]``; None when the engine has no such form."""
    d = engine_.driver(driver)
    if d is None:
        return None
    hostport = f"{parts.host}:{parts.port}" if parts.port else parts.host
    raw_auth = ""
    if parts.user is not None:
        raw_auth = parts.user + ("" if parts.password is None else ":" + parts.password) + "@"
    text = fill(
        d.template,
        url=url,
        rest=url.split("://", 1)[1],
        auth=parts.auth(),
        raw_auth=raw_auth,
        host=parts.host,
        port=str(parts.port or ""),
        hostport=hostport,
        db=parts.database or "",
        user=parts.user or "",
        password=parts.password or "",
    )
    if not d.has_query:
        return text
    q: dict[str, str] = {}
    if d.query_auth:
        q.update({"user": parts.user or "", "password": parts.password or ""})
    q.update(dict(d.query_fixed))
    if d.query_url:
        q.update(parts.query)
    return f"{text}?{urlencode(q)}"


__all__ = [
    "BUILTIN_ADAPTERS",
    "BUILTIN_NAMES",
    "CAPABILITIES",
    "REGISTRY",
    "SQLITE",
    "EngineDescriptor",
    "EngineError",
    "EngineRegistry",
    "Registered",
    "accepts_url",
    "activate",
    "database_names",
    "engine",
    "engine_for_scheme",
    "find",
    "load_engine_file",
    "parse_descriptor",
    "project_registry",
    "read_descriptor",
    "server_engines",
    "using",
]
