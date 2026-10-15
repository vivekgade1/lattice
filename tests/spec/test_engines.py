"""Engine descriptors (lattice/spec/engines.py, spike 2): parsing and exact validation
errors, the built-in descriptors equal to the tables they replaced (the old literals are
copied here verbatim from db.py, dbserver.py, invariants.py and race.py at cc77f73), the
adapter hook (``module:Class``) and a project's ``[engines] paths``. No Docker."""

from __future__ import annotations

import sys
import textwrap
import tomllib
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit

import pytest

from lattice.spec import db, dbserver, engines
from lattice.spec.db import UrlParts, canonical_url, check_dialect, dialect_of, open_db, url_for
from lattice.spec.engines import REGISTRY, EngineError, parse_descriptor, read_descriptor

ROOT = Path(__file__).resolve().parents[2]
MARIADB = ROOT / "docs/examples/engines/mariadb.toml"

# ---- the old literals (cc77f73), verbatim ------------------------------------------------------

OLD_DIALECTS = ("sqlite", "postgres", "mysql", "clickhouse")
OLD_SERVER_DIALECTS = ("postgres", "mysql", "clickhouse")
OLD_IMAGES = {
    "postgres": "postgres:16-alpine",
    "mysql": "mysql:8.4",
    "clickhouse": "clickhouse/clickhouse-server:24.8-alpine",
}
OLD_PORTS = {"postgres": 5432, "mysql": 3306, "clickhouse": 8123}
OLD_DATA_DIRS = {
    "postgres": "/var/lib/postgresql/data",
    "mysql": "/var/lib/mysql",
    "clickhouse": "/var/lib/clickhouse",
}
OLD_ADMIN_DB = {"postgres": "postgres", "mysql": "mysql", "clickhouse": "default"}
OLD_ADAPTER_DEFAULT_PORT = {"mysql": 3306, "clickhouse": 8123}  # MysqlDatabase / Clickhouse
OLD_BOOLS = {
    "sqlite": ("1", "0"),
    "postgres": ("TRUE", "FALSE"),
    "mysql": ("TRUE", "FALSE"),
    "clickhouse": ("true", "false"),
}
OLD_TABLES_SQL = {
    "postgres": (
        "SELECT table_name FROM information_schema.tables WHERE table_schema = "
        "current_schema() AND table_type = 'BASE TABLE' ORDER BY table_name"
    ),
    "mysql": (
        "SELECT table_name FROM information_schema.tables WHERE table_schema = "
        "DATABASE() AND table_type = 'BASE TABLE' ORDER BY table_name"
    ),
    "clickhouse": (
        "SELECT name FROM system.tables WHERE database = currentDatabase() AND "
        "engine NOT LIKE '%View' ORDER BY name"
    ),
}
OLD_COLUMNS_SQL = {
    "postgres": (
        "SELECT column_name FROM information_schema.columns WHERE table_schema = "
        "current_schema() AND table_name = %s ORDER BY ordinal_position"
    ),
    "mysql": (
        "SELECT column_name FROM information_schema.columns WHERE table_schema = "
        "DATABASE() AND table_name = %s ORDER BY ordinal_position"
    ),
    "clickhouse": (
        "SELECT name FROM system.columns WHERE database = currentDatabase() AND "
        "table = %s ORDER BY position"
    ),
}
OLD_TERMINATE = (
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
    "WHERE datname = %s AND pid <> pg_backend_pid()"
)
USER = "lattice"
PASSWORD = "lattice"


def old_run_args(dialect: str) -> tuple[list[str], list[str]]:
    data = OLD_DATA_DIRS[dialect]
    opts = ["--tmpfs", f"{data}:rw"]
    if dialect == "postgres":
        opts += [
            "-e",
            f"POSTGRES_USER={USER}",
            "-e",
            f"POSTGRES_PASSWORD={PASSWORD}",
            "-e",
            "POSTGRES_DB=postgres",
        ]
        args = [
            "-c",
            "fsync=off",
            "-c",
            "synchronous_commit=off",
            "-c",
            "full_page_writes=off",
            "-c",
            "max_connections=300",
        ]
        return opts, args
    if dialect == "mysql":
        opts += ["-e", f"MYSQL_ROOT_PASSWORD={PASSWORD}"]
        args = [
            "--innodb-flush-log-at-trx-commit=0",
            "--skip-log-bin",
            "--sync-binlog=0",
            "--max-connections=300",
            "--innodb-doublewrite=OFF",
        ]
        return opts, args
    opts += [
        "-e",
        f"CLICKHOUSE_USER={USER}",
        "-e",
        f"CLICKHOUSE_PASSWORD={PASSWORD}",
        "-e",
        "CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1",
        "--ulimit",
        "nofile=262144:262144",
    ]
    return opts, []


def old_admin_user(dialect: str) -> str:
    return "root" if dialect == "mysql" else USER


def old_dialect_of(ref: str) -> str:
    if "://" not in ref:
        return "sqlite"
    scheme = urlsplit(str(ref)).scheme.lower().split("+", 1)[0]
    if scheme in ("postgres", "postgresql"):
        return "postgres"
    if scheme in ("mysql", "mariadb"):
        return "mysql"
    if scheme in ("clickhouse", "ch", "http", "https"):
        return "clickhouse"
    if scheme == "sqlite":
        return "sqlite"
    raise ValueError(f"unknown database URL scheme {scheme!r}")


def old_canonical_url(dialect, host, port, user, password, database) -> str:
    auth = f"{quote(user, safe='')}:{quote(password, safe='')}@"
    if dialect == "postgres":
        return f"postgresql://{auth}{host}:{port}/{database}?sslmode=disable"
    if dialect == "mysql":
        return f"mysql://{auth}{host}:{port}/{database}"
    if dialect == "clickhouse":
        return f"clickhouse://{auth}{host}:{port}/{database}"
    raise ValueError(f"{dialect!r} is not a server dialect")


def old_url_for(url: str, driver: str) -> str:
    p = urlsplit(url)
    dialect = old_dialect_of(url)
    host = p.hostname or "127.0.0.1"
    port = p.port
    user = unquote(p.username) if p.username else None
    password = unquote(p.password) if p.password is not None else None
    database = unquote(p.path.lstrip("/")) or None
    query = dict(parse_qsl(p.query))

    def auth() -> str:
        if user is None:
            return ""
        pw = "" if password is None else ":" + quote(password, safe="")
        return quote(user, safe="") + pw + "@"

    hostport = f"{host}:{port}" if port else host
    dbn = database or ""
    if driver == "canonical":
        return url
    if dialect == "postgres":
        if driver in ("go-pgx", "go-pq", "psycopg", "libpq"):
            return url
        if driver == "sqlalchemy":
            return "postgresql+psycopg://" + url.split("://", 1)[1]
        if driver == "jdbc":
            q = {"user": user or "", "password": password or "", **query}
            return f"jdbc:postgresql://{hostport}/{dbn}?{urlencode(q)}"
    if dialect == "mysql":
        if driver == "go-mysql":
            a = ""
            if user is not None:
                a = user + ("" if password is None else ":" + password) + "@"
            q = {"parseTime": "true", **query}
            return f"{a}tcp({hostport})/{dbn}?{urlencode(q)}"
        if driver == "sqlalchemy":
            return "mysql+pymysql://" + url.split("://", 1)[1]
        if driver == "jdbc":
            q = {"user": user or "", "password": password or "", **query}
            return f"jdbc:mysql://{hostport}/{dbn}?{urlencode(q)}"
    if dialect == "clickhouse":
        if driver == "go-clickhouse":
            return f"http://{auth()}{hostport}/{dbn}"
        if driver == "sqlalchemy":
            return f"clickhouse+http://{auth()}{hostport}/{dbn}"
        if driver == "jdbc":
            q = {"user": user or "", "password": password or ""}
            return f"jdbc:clickhouse://{hostport}/{dbn}?{urlencode(q)}"
    raise ValueError(f"no {driver!r} form of a {dialect} URL")


URLS = [
    old_canonical_url("postgres", "127.0.0.1", 5433, "u", "p@ss", "db1"),
    old_canonical_url("mysql", "127.0.0.1", 3307, "root", "pw", "db1"),
    old_canonical_url("clickhouse", "127.0.0.1", 8124, "u", "pw", "db1"),
    "postgres://a:b%2Fc@db.example:5432/x?sslmode=require&application_name=z",
    "mysql://root@localhost/app?parseTime=false&loc=UTC",
    "mysql://root:p%40ss:w@127.0.0.1:3306/app",
    "mariadb://u:p@10.0.0.1/app",
    "mysql+pymysql://u:p@h:1/d",
    "clickhouse://default@ch:8123/",
    "ch://u:p@h/d",
    "https://u:p@ch.example:8443/analytics",
    "http://ch.example/analytics",
    "postgresql://u@h/d",
]
DRIVERS = [
    "canonical",
    "go-pgx",
    "go-pq",
    "psycopg",
    "libpq",
    "go-mysql",
    "go-clickhouse",
    "go-sqlite",
    "sqlalchemy",
    "jdbc",
    "path",
    "odbc",
]


# ---- built-ins equal the old tables, value for value -------------------------------------------


def test_builtin_names_and_dialect_tuples_are_unchanged():
    assert engines.BUILTIN_NAMES == OLD_SERVER_DIALECTS
    assert db.DIALECTS == OLD_DIALECTS
    assert db.SERVER_DIALECTS == OLD_SERVER_DIALECTS
    assert engines.database_names() == OLD_DIALECTS


def test_builtin_container_tables_equal_the_old_literals():
    assert dbserver.IMAGES == OLD_IMAGES
    assert dbserver.PORTS == OLD_PORTS
    assert dbserver.DATA_DIRS == OLD_DATA_DIRS
    assert dbserver.ADMIN_DB == OLD_ADMIN_DB
    for name in OLD_SERVER_DIALECTS:
        e = engines.engine(name)
        assert (e.server.image, e.server.port, e.server.data_dir, e.server.admin_db) == (
            OLD_IMAGES[name],
            OLD_PORTS[name],
            OLD_DATA_DIRS[name],
            OLD_ADMIN_DB[name],
        )
        assert e.default_port == OLD_PORTS[name]
        assert dbserver._run_args(name) == old_run_args(name)
        assert dbserver._admin_user(name) == old_admin_user(name)
        assert e.server.ready_sql == "SELECT 1"


def test_builtin_adapter_default_ports_are_unchanged():
    for name, port in OLD_ADAPTER_DEFAULT_PORT.items():
        assert engines.engine(name).default_port == port


def test_builtin_sql_flags_equal_the_old_literals():
    from lattice.spec.invariants import _sql_text

    for name in OLD_SERVER_DIALECTS:
        e = engines.engine(name)
        assert (e.sql.true_literal, e.sql.false_literal) == OLD_BOOLS[name]
        assert e.sql.list_tables == OLD_TABLES_SQL[name]
        assert e.sql.list_columns == OLD_COLUMNS_SQL[name]
        assert e.sql.placeholder == "%s"
        assert e.quote_ident('a"b`c') == ('`a"b``c`' if name == "mysql" else '"a""b`c"')
        assert _sql_text("a\\b'c", name) == (
            "'a\\\\b''c'" if name in ("mysql", "clickhouse") else "'a\\b''c'"
        )
    assert _sql_text("a\\b", "sqlite") == "'a\\b'"
    assert engines.engine("clickhouse").sql.float_cast == "toFloat64({x})"
    assert engines.engine("postgres").sql.terminate_sessions == OLD_TERMINATE
    assert engines.engine("mysql").sql.terminate_sessions == ""
    assert engines.engine("clickhouse").sql.terminate_sessions == ""


def test_builtin_capabilities_and_clone_strategies():
    caps = {n: dict(engines.engine(n).capabilities) for n in OLD_SERVER_DIALECTS}
    assert caps == {
        "postgres": {"transactions": True, "unique_constraints": True},
        "mysql": {"transactions": True, "unique_constraints": True},
        "clickhouse": {"transactions": False, "unique_constraints": False},
    }
    assert {n: engines.engine(n).clone for n in OLD_SERVER_DIALECTS} == {
        "postgres": "template",
        "mysql": "code",
        "clickhouse": "code",
    }
    assert {n: engines.engine(n).adapter for n in OLD_SERVER_DIALECTS} == {
        "postgres": "postgres",
        "mysql": "mysql",
        "clickhouse": "clickhouse",
    }
    assert engines.engine("postgres").aliases == ("postgresql",)
    from lattice.spec.race import _unenforceable

    assert _unenforceable("clickhouse") == ("ClickHouse", True)
    assert _unenforceable("mysql") == ("MySQL", False)
    assert _unenforceable("sqlite") == ("sqlite", False)


class _FakeAdmin:
    """Records what a DbServer sends to its admin connection."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.sent.append((sql, tuple(params)))

    def close(self):
        pass


@pytest.mark.parametrize("dialect", OLD_SERVER_DIALECTS)
def test_drop_quote_and_terminate_statements_are_unchanged(dialect):
    server = dbserver.DbServer(dialect, "x://unused")
    fake = _FakeAdmin()
    server._admin = fake  # type: ignore[assignment]
    server.drop("lattice_ab_c1")
    server.terminate("lattice_ab_t1")
    q = "`lattice_ab_c1`" if dialect == "mysql" else '"lattice_ab_c1"'
    want_drop = {
        "postgres": f"DROP DATABASE IF EXISTS {q} WITH (FORCE)",
        "clickhouse": f"DROP DATABASE IF EXISTS {q} SYNC",
        "mysql": f"DROP DATABASE IF EXISTS {q}",
    }[dialect]
    want = [(want_drop, ())]
    if dialect == "postgres":
        want.append((OLD_TERMINATE, ("lattice_ab_t1",)))
    assert fake.sent == want
    with pytest.raises(dbserver.DbServerError, match="refusing a database name"):
        server.q("x; DROP")


def test_the_postgres_clone_is_the_old_two_statements():
    server = dbserver.DbServer("postgres", "x://unused")
    fake = _FakeAdmin()
    server._admin = fake  # type: ignore[assignment]
    server.url = "postgresql://u:p@127.0.0.1:5432/postgres?sslmode=disable"
    url = server.clone("tpl", "c1")
    assert fake.sent == [(OLD_TERMINATE, ("tpl",)), ('CREATE DATABASE "c1" TEMPLATE "tpl"', ())]
    assert url == "postgresql://u:p@127.0.0.1:5432/c1?sslmode=disable"


def test_code_clones_dispatch_to_the_builtin_procedures(monkeypatch):
    calls = []
    monkeypatch.setitem(
        dbserver._BUILTIN_CLONES, "mysql", lambda s, t, n: calls.append(("my", t, n))
    )
    monkeypatch.setitem(
        dbserver._BUILTIN_CLONES, "clickhouse", lambda s, t, n: calls.append(("ch", t, n))
    )
    dbserver.DbServer("mysql", "mysql://r:p@h:1/mysql").clone("t", "c")
    dbserver.DbServer("clickhouse", "clickhouse://r:p@h:1/default").clone("t", "c")
    assert calls == [("my", "t", "c"), ("ch", "t", "c")]


def test_urls_dialects_and_driver_forms_equal_the_old_functions():
    for name in OLD_SERVER_DIALECTS:
        for args in (("127.0.0.1", 5433, "u", "p@ss/:x", "db1"), ("h", 1, "root", "", "d")):
            assert canonical_url(name, *args) == old_canonical_url(name, *args)
    for url in URLS:
        assert dialect_of(url) == old_dialect_of(url), url
        for driver in DRIVERS:
            try:
                want = old_url_for(url, driver)
            except ValueError as err:
                with pytest.raises(ValueError) as got:
                    url_for(url, driver)
                assert str(got.value) == str(err), (url, driver)
                continue
            assert url_for(url, driver) == want, (url, driver)


def test_unknown_names_fail_as_before():
    with pytest.raises(ValueError, match=r"^'oracle' is not a server dialect$"):
        canonical_url("oracle", "h", 1, "u", "p", "d")
    with pytest.raises(ValueError, match=r"^'postgresql' is not a server dialect$"):
        canonical_url("postgresql", "h", 1, "u", "p", "d")  # an alias is no canonical name
    with pytest.raises(ValueError, match=r"unknown database URL scheme 'oracle'"):
        dialect_of("oracle://u@h/d")
    assert check_dialect("PostgreSQL") == "postgres"
    assert check_dialect("") == "sqlite"
    with pytest.raises(
        ValueError,
        match=r"^unknown database 'db2' \(one of sqlite, postgres, "
        r"mysql, clickhouse\)$",
    ):
        check_dialect("db2")
    from lattice.spec.invariants import _Compiler

    with pytest.raises(ValueError, match="unknown SQL dialect 'postgresql'"):
        _Compiler.__init__(object.__new__(_Compiler), None, _empty_profile(), "postgresql")  # type: ignore[arg-type]


def _empty_profile():
    from lattice.profile import ApiProfile, NoRoles, Profile

    return Profile(
        schemas={},
        api=ApiProfile(
            tables={}, table_names={}, roles=frozenset({"Public"}), roles_from=NoRoles("Public")
        ),
    )


def test_the_builtin_toml_files_round_trip():
    for name in OLD_SERVER_DIALECTS:
        path = engines.HERE / f"{name}.toml"
        e = read_descriptor(path)
        assert e == engines.engine(name)
        assert parse_descriptor(e.to_dict()) == e


def test_the_cli_parsers_offer_the_same_database_choices():
    from lattice.spec import ask, draft
    from lattice.spec.__main__ import _parser

    def choices(parser, dest="database"):
        for action in parser._actions:
            if action.dest == dest:
                return tuple(action.choices)
            for sub in getattr(action, "choices", None) or ():
                if isinstance(action.choices, dict):
                    got = choices(action.choices[sub], dest)
                    if got:
                        return got
        return ()

    assert choices(_parser()) == OLD_DIALECTS
    assert choices(ask._parser()) == OLD_DIALECTS
    assert choices(draft._parser()) == OLD_DIALECTS


# ---- parsing and validation errors (exact messages) --------------------------------------------

BASE = tomllib.loads((engines.HERE / "mysql.toml").read_text())


def _with(**changes):
    data = {k: (dict(v) if isinstance(v, dict) else v) for k, v in BASE.items()}
    data["name"] = "testdb"
    data["url_schemes"] = ["testdb"]
    for key, value in changes.items():
        table, _, field = key.partition("__")
        if field:
            if value is None:
                data[table].pop(field, None)
            else:
                data[table][field] = value
        elif value is None:
            data.pop(table, None)
        else:
            data[table] = value
    return data


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"name": None}, "E: missing required field 'name'"),
        (
            {"colour": "red"},
            "E: unknown field 'colour' (known: name, title, aliases, url_schemes, "
            "url_template, default_port, adapter, requires, clone, drivers, sql, capabilities, "
            "server, perf)",
        ),
        (
            {"name": "Bad Name"},
            "E: name: 'Bad Name' is not a valid engine name (lowercase letters, "
            "digits, '-' and '_', starting with a letter)",
        ),
        ({"name": "sqlite"}, "E: name: 'sqlite' is built in and cannot be described"),
        ({"name": 3}, "E: name: must be a string, not an integer"),
        ({"url_schemes": []}, "E: url_schemes: must name at least one scheme"),
        ({"url_schemes": "testdb"}, "E: url_schemes: must be a list of strings"),
        ({"url_template": "testdb:{user}"}, "E: url_template: must be a URL (scheme://...)"),
        (
            {"url_template": "t://{user}@{hostname}/{db}"},
            "E: url_template: unknown placeholder "
            "{hostname} (known: {user}, {password}, {host}, {port}, {db})",
        ),
        ({"default_port": "3306"}, "E: default_port: must be an integer, not a string"),
        ({"default_port": 70000}, "E: default_port: must be a port number (1-65535), not 70000"),
        (
            {"adapter": "oracle"},
            "E: adapter: 'oracle' is neither a built-in adapter (postgres, "
            "mysql, clickhouse) nor module:Class",
        ),
        ({"clone": "copy"}, 'E: clone: must be "template" or "code", not \'copy\''),
        (
            {"adapter": "postgres"},
            'E: clone: clone = "code" needs an adapter with a clone '
            "procedure; the built-in adapter 'postgres' has none (use clone = \"template\")",
        ),
        (
            {"clone": "template"},
            'E: sql.clone_template: clone = "template" needs a clone template '
            "(CREATE DATABASE {name} ...)",
        ),
        (
            {"clone": "template", "sql__clone_template": "CREATE DATABASE {name}"},
            "E: sql.clone_template: must use {name} and {template}",
        ),
        ({"sql__identifier_quote": "["}, "E: sql.identifier_quote: must be '\"' or '`', not '['"),
        (
            {"sql__backslash_escapes": "yes"},
            "E: sql.backslash_escapes: must be true or false, not a string",
        ),
        ({"sql__true_literal": None}, "E: [sql] missing required field 'true_literal'"),
        (
            {"sql__dialect": "x"},
            "E: [sql] unknown field 'dialect' (known: identifier_quote, "
            "placeholder, true_literal, false_literal, backslash_escapes, float_cast, divide, "
            "list_tables, list_columns, drop_database, terminate_sessions, clone_template)",
        ),
        ({"sql__divide": "({a} / 2)"}, "E: sql.divide: must use {a} and {b}"),
        (
            {"sql__float_cast": "CAST({y} AS DOUBLE)"},
            "E: sql.float_cast: unknown placeholder {y} (known: {x})",
        ),
        ({"sql__drop_database": "DROP DATABASE x"}, "E: sql.drop_database: must use {name}"),
        (
            {"capabilities__json": True},
            "E: [capabilities] unknown flag 'json' (known: transactions, unique_constraints)",
        ),
        ({"capabilities__transactions": None}, "E: [capabilities] missing flag 'transactions'"),
        (
            {"capabilities__transactions": 1},
            "E: capabilities.transactions: must be true or false, not an integer",
        ),
        ({"server__image": None}, "E: [server] missing required field 'image'"),
        (
            {"server__data_dir": "var/lib"},
            "E: server.data_dir: must be an absolute path in the container, not 'var/lib'",
        ),
        (
            {"server__env": {"A": "{secret}"}},
            "E: server.env: unknown placeholder {secret} (known: {user}, {password})",
        ),
        ({"server__env": {"A-B": "x"}}, "E: server.env: 'A-B' is not an environment variable name"),
        (
            {"drivers": {"canonical": "{url}"}},
            "E: drivers.canonical: 'canonical' is always the URL itself",
        ),
        (
            {"drivers": {"go": {"template": "{url}", "query": {}}}},
            "E: [drivers.go] unknown field "
            "'query' (known: template, query_auth, query_fixed, query_url)",
        ),
        (
            {"drivers": {"go": "{dsn}"}},
            "E: drivers.go: unknown placeholder {dsn} (known: {url}, "
            "{rest}, {auth}, {raw_auth}, {host}, {port}, {hostport}, {db}, {user}, {password})",
        ),
        ({"sql": "x"}, "E: sql: must be a table, not a string"),
    ],
)
def test_validation_errors_are_exact(changes, message):
    with pytest.raises(EngineError) as err:
        parse_descriptor(_with(**changes), "E")
    assert str(err.value) == message


def test_a_file_error_names_the_file(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("name = \n")
    with pytest.raises(EngineError, match=r"^engine file .*bad\.toml: not valid TOML"):
        read_descriptor(bad)
    with pytest.raises(EngineError, match=r"^engine file .*missing\.toml: cannot read it"):
        read_descriptor(tmp_path / "missing.toml")


def test_defaults_fill_in_optional_fields():
    data = _with(
        sql__float_cast=None,
        sql__divide=None,
        sql__placeholder=None,
        server__port=None,
        server__ready_sql=None,
        server__env=None,
        server__args=None,
        title=None,
        requires=None,
        drivers=None,
    )
    e = parse_descriptor(data, "E")
    assert (e.sql.float_cast, e.sql.divide, e.sql.placeholder) == (
        "{x}",
        "(({a}) * 1.0 / ({b}))",
        "%s",
    )
    assert (e.server.port, e.server.ready_sql, e.server.env, e.server.args) == (
        3306,
        "SELECT 1",
        (),
        (),
    )
    assert (e.title, e.requires, e.drivers) == ("testdb", (), ())


# ---- the registry -----------------------------------------------------------------------------


def test_registry_clashes_and_idempotence():
    with REGISTRY.scoped() as reg:
        e = parse_descriptor(_with(), "E")
        reg.add(e, "project")
        reg.add(e, "project")  # the same descriptor again: a no-op
        other = parse_descriptor(_with(default_port=1234), "E")
        with pytest.raises(
            EngineError,
            match="^engine 'testdb' is already registered from "
            "project with a different descriptor$",
        ):
            reg.add(other, "project")
        with pytest.raises(
            EngineError, match="^engine 'mysql' is built in; give the descriptor another name$"
        ):
            reg.add(parse_descriptor(_with(name="mysql"), "E"), "project")
        with pytest.raises(
            EngineError, match="^engine 'pg2': the name 'postgresql' is taken by engine 'postgres'$"
        ):
            reg.add(
                parse_descriptor(
                    _with(name="pg2", url_schemes=["pg2"], aliases=["postgresql"]), "E"
                ),
                "project",
            )
        with pytest.raises(
            EngineError, match="^engine 'my2': URL scheme 'mysql' belongs to engine 'mysql'$"
        ):
            reg.add(parse_descriptor(_with(name="my2", url_schemes=["mysql"]), "E"), "project")
        with pytest.raises(
            EngineError, match="^engine 'te2': URL scheme 'testdb' belongs to engine 'testdb'$"
        ):
            reg.add(parse_descriptor(_with(name="te2"), "E"), "project")
        assert reg.names() == ("postgres", "mysql", "clickhouse", "testdb")
    assert REGISTRY.names() == OLD_SERVER_DIALECTS  # scoped: forgotten after the block


def test_a_project_engine_takes_over_a_builtin_alias_scheme_only_while_registered():
    assert dialect_of("mariadb://u:p@h/d") == "mysql"
    with REGISTRY.scoped():
        engines.load_engine_file(MARIADB)
        assert dialect_of("mariadb://u:p@h/d") == "mariadb"
        assert dialect_of("mysql://u:p@h/d") == "mysql"
        assert check_dialect("MariaDB") == "mariadb"
        assert engines.database_names() == (*OLD_DIALECTS, "mariadb")
        assert canonical_url("mariadb", "127.0.0.1", 4000, "root", "pw", "mysql") == (
            "mysql://root:pw@127.0.0.1:4000/mysql"
        )
        assert engines.accepts_url("mariadb", "mysql://root:pw@127.0.0.1:4000/mysql")
        assert not engines.accepts_url("mariadb", "postgres://u@h/d")
        assert url_for("mariadb://root:pw@h:3306/app", "go-mysql") == (
            "root:pw@tcp(h:3306)/app?parseTime=true"
        )
        assert url_for("mariadb://root:pw@h:3306/app", "jdbc") == (
            "jdbc:mariadb://h:3306/app?user=root&password=pw"
        )
        e = engines.engine("mariadb")
        assert e.run_args(USER, PASSWORD)[0] == [
            "--tmpfs",
            "/var/lib/mysql:rw",
            "-e",
            "MARIADB_ROOT_PASSWORD=lattice",
        ]
        assert e.adapter_class() is db.MysqlDatabase
    assert dialect_of("mariadb://u:p@h/d") == "mysql"


def test_the_spec_cli_registers_engine_files_before_reading_database(tmp_path, capsys):
    from lattice.spec.__main__ import main

    with REGISTRY.scoped():
        # an unknown --database without the file: argparse refuses it
        with pytest.raises(SystemExit):
            main(["check", "--spec", "m:S", "--app", "m:a", "--db", "m:d", "--database", "mariadb"])
        assert "invalid choice: 'mariadb'" in capsys.readouterr().err
        # with the file it is a choice; the check then fails on the missing module (exit 2)
        rc = main(
            [
                "check",
                "--spec",
                "no_such_module_xyz:S",
                "--app",
                "m:a",
                "--db",
                "m:d",
                "--database",
                "mariadb",
                f"--engine-file={MARIADB}",
            ]
        )
        assert rc == 2
        assert "No module named 'no_such_module_xyz'" in capsys.readouterr().err
        bad = tmp_path / "bad.toml"
        bad.write_text('name = "x"\n')
        rc = main(
            ["check", "--spec", "m:S", "--app", "m:a", "--db", "m:d", "--engine-file", str(bad)]
        )
        assert rc == 2
        assert capsys.readouterr().err == (
            f"error: EngineError: engine file {bad}: missing required field 'url_schemes'\n"
        )


def test_the_invariant_compiler_reads_a_project_engines_flags():
    from lattice.spec.invariants import _sql_text

    with REGISTRY.scoped():
        engines.load_engine_file(MARIADB)
        assert _sql_text("a\\b", "mariadb") == "'a\\\\b'"
        from lattice.spec.race import _unenforceable

        assert _unenforceable("mariadb") == ("MariaDB", False)


# ---- the adapter hook -------------------------------------------------------------------------

ADAPTER_SRC = '''
from lattice.spec.db import MysqlDatabase


class FakeAdapter(MysqlDatabase):
    """Never connects: records the URL (tests)."""

    dialect = "fake"
    opened = []

    def __init__(self, url):
        self.url = url
        FakeAdapter.opened.append(url)

    def close(self):
        pass

    @staticmethod
    def clone(server, template, name):
        FakeAdapter.opened.append(("clone", template, name))


class NotADatabase:
    pass
'''


def _descriptor_file(tmp_path: Path, adapter: str, clone: str = "code") -> Path:
    (tmp_path / "fake_adapter_mod.py").write_text(ADAPTER_SRC)
    text = (engines.HERE / "mysql.toml").read_text()
    text = (
        text.replace('name = "mysql"', 'name = "fakedb"')
        .replace('url_schemes = ["mysql", "mariadb"]', 'url_schemes = ["fakedb"]')
        .replace('url_template = "mysql://', 'url_template = "fakedb://')
        .replace('adapter = "mysql"', f'adapter = "{adapter}"')
        .replace('clone = "code"', f'clone = "{clone}"')
    )
    path = tmp_path / "fakedb.toml"
    path.write_text(text)
    return path


def test_the_adapter_hook_loads_module_class_from_the_descriptor_dir(tmp_path):
    path = _descriptor_file(tmp_path, "fake_adapter_mod:FakeAdapter")
    with REGISTRY.scoped():
        e = engines.load_engine_file(path)
        cls = e.adapter_class()
        assert cls.__name__ == "FakeAdapter" and issubclass(cls, db.Database)
        conn = open_db("fakedb://u:p@h:1/d")
        assert type(conn) is cls and conn.dialect == "fakedb" and conn.engine is e
        assert cls.opened == ["fakedb://u:p@h:1/d"]
        dbserver.DbServer("fakedb", "fakedb://u:p@h:1/d").clone("t", "c")
        assert cls.opened[-1] == ("clone", "t", "c")
        assert not list(tmp_path.rglob("__pycache__")), "an adapter import wrote bytecode"
    sys.modules.pop("fake_adapter_mod", None)


def test_the_adapter_hook_refuses_what_is_not_a_database(tmp_path):
    with REGISTRY.scoped():
        e = engines.load_engine_file(_descriptor_file(tmp_path, "fake_adapter_mod:NotADatabase"))
        with pytest.raises(
            EngineError,
            match="^engine 'fakedb': adapter 'fake_adapter_mod:"
            "NotADatabase' is not a lattice.spec.db.Database subclass$",
        ):
            e.adapter_class()
    with REGISTRY.scoped():
        (tmp_path / "x").mkdir()
        path = _descriptor_file(tmp_path / "x", "missing_mod_q:Thing")
        e = engines.load_engine_file(path)
        with pytest.raises(
            EngineError,
            match="^engine 'fakedb': cannot import adapter "
            "'missing_mod_q:Thing': ModuleNotFoundError",
        ):
            e.adapter_class()
    sys.modules.pop("fake_adapter_mod", None)


# ---- a project's [engines] paths -------------------------------------------------------------


def test_project_engine_paths_are_loaded_relative_to_the_project(tmp_path):
    from lattice.modules import load_project_engines

    (tmp_path / "engines").mkdir()
    (tmp_path / "engines" / "mariadb.toml").write_text(MARIADB.read_text())
    with REGISTRY.scoped():
        loaded = load_project_engines(tmp_path, {"paths": ["engines/mariadb.toml"]})
        assert [e.name for _, e in loaded.files] == ["mariadb"]
        assert loaded.errors == []
        assert REGISTRY.entry("mariadb").source == "project"
    with REGISTRY.scoped():
        loaded = load_project_engines(tmp_path, {"paths": ["engines/nope.toml"], "x": 1})
        assert loaded.files == []
        assert loaded.errors[0] == "studio.toml: [engines] unknown field 'x' (known: paths)"
        assert loaded.errors[1].startswith(
            f"engine file {tmp_path.resolve()}/engines/nope.toml: cannot read it"
        )
        assert load_project_engines(tmp_path, {"paths": "x"}).errors == [
            "studio.toml: [engines] paths must be a list of file paths"
        ]


def test_lattice_check_forwards_engine_paths_and_modules(tmp_path):
    from lattice import cli

    (tmp_path / "spec.py").write_text("")
    (tmp_path / "studio.toml").write_text(
        textwrap.dedent("""
        [app]
        app = "h:make_app"
        db = "h:build_db"
        database = "mariadb"
        [engines]
        paths = ["engines/mariadb.toml"]
    """)
    )
    (tmp_path / "lattice.modules.lock").write_text('{"version": 1, "modules": []}')
    out = cli._fill_check_args(tmp_path, [])
    assert isinstance(out, list)
    i = out.index("--engine-file")
    assert out[i + 1] == str((tmp_path / "engines/mariadb.toml").resolve())
    assert out[out.index("--modules") + 1] == str(tmp_path)
    assert out[out.index("--database") + 1] == "mariadb"


def test_a_check_on_an_unregistered_engine_fails_cleanly():
    from lattice.spec.check import CheckError, check
    from lattice.spec.model import Spec

    spec = Spec(profile=_empty_profile(), scenarios=())
    with pytest.raises(
        CheckError,
        match=r"unknown database 'mariadb' \(one of sqlite, postgres, "
        r"mysql, clickhouse\)",
    ):
        check(spec, lambda p: None, lambda p: None, database="mariadb")


def test_a_url_of_an_unknown_scheme_has_no_parts():
    with pytest.raises(ValueError, match="unknown database URL scheme"):
        UrlParts("oracle://u@h/d")
