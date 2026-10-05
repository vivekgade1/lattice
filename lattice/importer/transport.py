"""Running an original app under test, framework-neutrally (Phase 2 plan §3, W1-A).

* :class:`Transport` -- ``request(method, path, params=, json=, form=, headers=)`` ->
  :class:`Answer` (status, headers, body). Two are built in, both over an httpx client
  (``Transport.client``, what a legacy :data:`Login` callable is handed):

  - :class:`AsgiTransport`: Starlette's ``TestClient`` -- exactly what the importer used
    before (FastAPI; lifespan run on enter, an unhandled error a 500 unless
    ``raise_errors``);
  - :class:`WsgiTransport`: ``httpx.Client`` over ``httpx.WSGITransport`` (Flask; an
    unhandled error a 500 unless ``raise_errors``), base URL ``http://testserver``.

  The Django front end serves its WSGI application through :class:`WsgiTransport` too.
* :class:`AuthDriver` -- how a case acting as a user is authenticated: a login (the app's
  own login route: :func:`form_login`, :func:`json_login`, :func:`session_login`; or any
  :data:`Login`; :func:`basic_token_login` posts HTTP Basic credentials to a login
  route; :func:`basic_login` is stateless HTTP Basic), else a token ``mint`` (handle ->
  token, no request); the token is sent in the ``Authorization`` header with its
  ``scheme`` (``bearer`` / ``token`` / ``jwt`` / ``basic`` spelled ``Bearer`` / ``Token``
  / ``JWT`` / ``Basic``; ``basic_token``: ``Basic base64(<token>:)``), or -- ``cookie``
  set -- as that session cookie, held in the client's cookie jar.
* :class:`AppRuntime` -- everything the importer does to an original app that depends on
  its framework: the transport, the auth driver, the dispose hooks run after every app
  (before the database is read: ``app.state.engine.dispose()`` for FastAPI), the seed's
  insert hook (the ORM's), the error-site predicates (branch coverage and the token
  answers) and the OpenAPI reader. ``AppRuntime()`` is today's FastAPI + SQLModel
  behaviour, field for field; a front end supplies its own.

Nothing here imports a web framework at module level. (Its string literals are kept to
ones the importer already has: see ``lattice.importer.suite`` on Hypothesis's local
constants.)
"""

from __future__ import annotations

import ast
import json as jsonlib
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from lattice.importer.appmodel import AuthModel, TableModel
    from lattice.importer.outside import OpenApiFacts

Login = Callable[[Any, str], str]  # (client, user handle) -> token
# the token sent as the HTTP Basic username with an empty password (Flask-HTTPAuth apps
# whose verify_password takes ``email_or_token``, as flasky's)
BASIC_TOKEN = "basic_token"
AppFactory = Callable[[Path], Any]
InsertHook = Callable[[Path, Any], None]  # (database path, suite.SeedPlan) -> None
# an error site of a front end: an AST node -> (status, detail when literal), or None
ErrorSite = Callable[[ast.AST], "tuple[int | None, str | None] | None"]

# ---------------------------------------------------------------------------- answers --


@dataclass(frozen=True)
class Answer:
    """What an app answered: ``status``, ``headers`` (lower-case names), ``body`` (the
    JSON value; the text when the content is not JSON; None when there is none) and the
    raw ``content`` / ``text``."""

    status: int
    headers: Mapping[str, str]
    body: Any
    content: bytes = b""
    text: str = ""

    @classmethod
    def of(cls, response: Any) -> Answer:
        """An httpx-style response (``status_code``, ``headers``, ``content``, ``text``,
        ``json()``) as an Answer."""
        content = response.content
        body = None
        if content:
            try:
                body = response.json()
            except jsonlib.JSONDecodeError:
                body = response.text
        return cls(response.status_code, dict(response.headers), body, content, response.text)

    @property
    def status_code(self) -> int:
        return self.status

    def json(self) -> Any:
        return jsonlib.loads(self.content)


# ------------------------------------------------------------------------- transports --


class Transport(Protocol):
    """A client of one app. ``client``: the underlying client, handed to a :data:`Login`
    (an httpx-compatible one for the built-in transports)."""

    client: Any

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        form: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Answer: ...

    def set_cookie(self, name: str, value: str) -> None: ...

    def __enter__(self) -> Transport: ...

    def __exit__(self, *exc: object) -> None: ...


# app -> transport (``raise_errors``: an unhandled error in the app raises, else a 500)
TransportFactory = Callable[..., Transport]


class HttpxTransport:
    """A transport over an httpx-compatible client (entered and left with it)."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        form: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Answer:
        response = self.client.request(
            method, path, params=params, json=json, data=form, headers=headers
        )
        return Answer.of(response)

    def set_cookie(self, name: str, value: str) -> None:
        self.client.cookies.set(name, value)

    def __enter__(self) -> HttpxTransport:
        self.client.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        self.client.__exit__(*exc)


class AsgiTransport(HttpxTransport):
    """An ASGI app through Starlette's ``TestClient`` (FastAPI's)."""

    def __init__(self, app: Any, *, raise_errors: bool = False) -> None:
        from fastapi.testclient import TestClient

        super().__init__(TestClient(app, raise_server_exceptions=raise_errors))


class WsgiTransport(HttpxTransport):
    """A WSGI app through ``httpx.WSGITransport`` (Flask's); see
    :func:`lattice.importer.wsgi.wsgi_client`."""

    def __init__(self, app: Any, *, raise_errors: bool = False, base_url: str | None = None):
        from lattice.importer.wsgi import wsgi_client

        super().__init__(wsgi_client(app, raise_errors=raise_errors, base_url=base_url))


# ------------------------------------------------------------------------------- auth --


def form_login(
    path: str,
    credentials: Mapping[str, tuple[str, str]],
    *,
    username_field: str = "username",
    password_field: str = "password",
    token_field: str = "access_token",
) -> Login:
    """A ``login`` for an OAuth2 password form: handle -> (username, password)."""

    def login(client: Any, handle: str) -> str:
        username, password = credentials[handle]
        r = client.post(path, data={username_field: username, password_field: password})
        if r.status_code != 200:
            raise RuntimeError(f"login as {handle!r} failed: {r.status_code} {r.text}")
        return r.json()[token_field]

    return login


def json_login(
    path: str,
    credentials: Mapping[str, tuple[str, str]],
    *,
    username_field: str = "username",
    password_field: str = "password",
    token_path: Sequence[str] = ("access_token",),
) -> Login:
    """A ``login`` posting JSON credentials; the token is at ``token_path`` in the
    answer (``("data", "token")``: ``answer["data"]["token"]``)."""

    def login(client: Any, handle: str) -> str:
        username, password = credentials[handle]
        r = client.post(path, json={username_field: username, password_field: password})
        if r.status_code != 200:
            raise RuntimeError(f"login as {handle!r} failed: {r.status_code} {r.text}")
        value = r.json()
        for key in token_path:
            value = value[key]
        return value

    return login


def session_login(
    path: str,
    credentials: Mapping[str, tuple[str, str]],
    cookie: str,
    *,
    as_json: bool = False,
    username_field: str = "username",
    password_field: str = "password",
) -> Login:
    """A ``login`` through a session login route: the credentials posted (a form, or
    JSON), the session id the value of the ``cookie`` the answer sets."""

    def login(client: Any, handle: str) -> str:
        username, password = credentials[handle]
        data = {username_field: username, password_field: password}
        r = client.post(path, json=data) if as_json else client.post(path, data=data)
        if r.status_code >= 400:
            raise RuntimeError(f"login as {handle!r} failed: {r.status_code} {r.text}")
        value = client.cookies.get(cookie)
        if value is None:
            raise RuntimeError(f"login as {handle!r} set no {cookie!r} cookie")
        return value

    return login


def basic_credentials(username: str, password: str) -> str:
    """An HTTP Basic ``Authorization`` value: ``Basic base64(<username>:<password>)``."""
    import base64

    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


def basic_token_login(
    path: str,
    credentials: Mapping[str, tuple[str, str]],
    *,
    token_path: Sequence[str] = ("access_token",),
) -> Login:
    """A ``login`` posting no body, the credentials as HTTP Basic (Flask-HTTPAuth's
    ``HTTPBasicAuth``); the token is at ``token_path`` in the answer."""

    def login(client: Any, handle: str) -> str:
        username, password = credentials[handle]
        r = client.post(path, headers={"Authorization": basic_credentials(username, password)})
        if r.status_code != 200:
            raise RuntimeError(f"login as {handle!r} failed: {r.status_code} {r.text}")
        value = r.json()
        for key in token_path:
            value = value[key]
        return value

    return login


def basic_login(credentials: Mapping[str, tuple[str, str]]) -> Login:
    """A ``login`` for HTTP Basic auth: no request, the token is ``base64(user:password)``
    (sent as ``Authorization: Basic <token>``)."""
    import base64

    def login(client: Any, handle: str) -> str:
        username, password = credentials[handle]
        return base64.b64encode(f"{username}:{password}".encode()).decode()

    return login


def scheme_header(scheme: str) -> str:
    """The ``Authorization`` header's spelling of a scheme: ``bearer`` -> ``Bearer``,
    ``token`` -> ``Token``, ``jwt`` -> ``JWT``."""
    return scheme.upper() if scheme == "jwt" else scheme.capitalize()


@dataclass
class AuthDriver:
    """How a case acting as a user is authenticated (see the module docstring).
    ``scheme`` is canonical lowercase (``AuthDriver(scheme="Token")`` equals
    ``AuthDriver(scheme="token")``); ``header`` the header the token is sent in;
    ``login`` (the app's own route) wins over ``mint``; ``cookie`` set: the token is that
    session cookie's value instead of a header. ``AuthDriver()`` is FastAPI's today:
    ``Authorization: Bearer <token>``, logged in by the caller's :data:`Login`."""

    scheme: str = "bearer"
    header: str = "Authorization"
    login: Login | None = None
    mint: Callable[[str], str] | None = None
    cookie: str | None = None

    def __post_init__(self) -> None:
        self.scheme = self.scheme.lower()

    @classmethod
    def form(
        cls,
        path: str,
        credentials: Mapping[str, tuple[str, str]],
        *,
        username_field: str = "username",
        password_field: str = "password",
        token_field: str = "access_token",
        scheme: str = "bearer",
    ) -> AuthDriver:
        login = form_login(path, credentials, username_field=username_field,
                           password_field=password_field, token_field=token_field)  # fmt: skip
        return cls(scheme=scheme, login=login)

    @classmethod
    def json(
        cls,
        path: str,
        credentials: Mapping[str, tuple[str, str]],
        *,
        username_field: str = "username",
        password_field: str = "password",
        token_path: Sequence[str] = ("access_token",),
        scheme: str = "bearer",
    ) -> AuthDriver:
        login = json_login(path, credentials, username_field=username_field,
                           password_field=password_field, token_path=token_path)  # fmt: skip
        return cls(scheme=scheme, login=login)

    @classmethod
    def session(
        cls,
        path: str,
        credentials: Mapping[str, tuple[str, str]],
        cookie: str,
        *,
        as_json: bool = False,
        username_field: str = "username",
        password_field: str = "password",
    ) -> AuthDriver:
        login = session_login(path, credentials, cookie, as_json=as_json,
                              username_field=username_field,
                              password_field=password_field)  # fmt: skip
        return cls(scheme="session", login=login, cookie=cookie)

    @classmethod
    def basic_route(
        cls,
        path: str,
        credentials: Mapping[str, tuple[str, str]],
        *,
        token_path: Sequence[str] = ("access_token",),
        scheme: str = "bearer",
    ) -> AuthDriver:
        """Logged in with HTTP Basic credentials (:func:`basic_token_login`); the token then
        sent with ``scheme`` (``basic_token``: as the Basic username, empty password)."""
        return cls(scheme=scheme, login=basic_token_login(path, credentials, token_path=token_path))

    @classmethod
    def basic(cls, credentials: Mapping[str, tuple[str, str]]) -> AuthDriver:
        """HTTP Basic: every request carries ``Authorization: Basic base64(user:pw)``."""
        return cls(scheme="basic", login=basic_login(credentials))

    @classmethod
    def from_model(
        cls, auth: AuthModel | None, credentials: Mapping[str, tuple[str, str]]
    ) -> AuthDriver:
        """The driver a front end's ``AuthModel`` describes, logging in with
        ``credentials`` (handle -> (login name, password)): Basic; a session login route;
        a JSON or form login route answering the token at ``token_path``; else the
        scheme alone (a caller's :data:`Login` or a minter supplies tokens). None:
        FastAPI's default driver."""
        if auth is None:
            return cls()
        fields = {"username_field": auth.username_field, "password_field": auth.password_field}
        if auth.scheme == "basic":
            return cls.basic(credentials)
        if auth.login_path is None:
            return cls(scheme=auth.scheme, cookie=auth.cookie)
        if auth.login_encoding == "basic":
            return cls.basic_route(auth.login_path, credentials, token_path=auth.token_path,
                                   scheme=auth.scheme)  # fmt: skip
        if auth.scheme == "session" and auth.cookie is not None:
            return cls.session(auth.login_path, credentials, auth.cookie,
                               as_json=auth.login_encoding == "json", **fields)  # fmt: skip
        if auth.login_encoding == "json":
            return cls.json(auth.login_path, credentials, token_path=auth.token_path,
                            scheme=auth.scheme, **fields)  # fmt: skip
        return cls.form(auth.login_path, credentials, token_field=".".join(auth.token_path),
                        scheme=auth.scheme, **fields)  # fmt: skip

    @property
    def can_login(self) -> bool:
        return self.login is not None or self.mint is not None

    def token(self, transport: Transport, handle: str, login: Login | None = None) -> str:
        """``handle``'s token: ``login`` (the caller's; else the driver's own) through
        the transport's client, else minted."""
        use = login or self.login
        if use is not None:
            return use(transport.client, handle)
        if self.mint is not None:
            return self.mint(handle)
        raise ValueError(f"no way to log in as {handle!r}: no login and no minter")

    def headers(self, token: str | None) -> dict[str, str]:
        """The request headers carrying ``token`` ({} for none, or a session cookie)."""
        if token is None or self.cookie is not None:
            return {}
        if self.scheme == BASIC_TOKEN:
            return {self.header: basic_credentials(token, "")}
        return {self.header: f"{scheme_header(self.scheme)} {token}"}

    def present(self, transport: Transport, token: str | None) -> dict[str, str]:
        """Attach ``token`` to the transport's next requests: a session cookie goes in the
        client's jar; anything else is returned as headers."""
        if token is not None and self.cookie is not None:
            transport.set_cookie(self.cookie, token)
        return self.headers(token)


# ---------------------------------------------------------- the FastAPI default hooks --


def dispose_state_engine(app: Any) -> None:
    """FastAPI apps of the importer's convention keep their engine at
    ``app.state.engine``: disposed so the SQLite file is released (no-op without one)."""
    engine = getattr(getattr(app, "state", None), "engine", None)
    if engine is not None:
        engine.dispose()


def status_of(node: ast.expr | None) -> int | None:
    """A literal status: ``404`` or ``status.HTTP_403_FORBIDDEN``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.Attribute):  # status.HTTP_403_FORBIDDEN
        m = re.match(r"HTTP_(\d{3})_", node.attr)
        return int(m.group(1)) if m else None
    return None


def is_http_exception(node: ast.expr) -> bool:
    """``HTTPException`` (or any ``*HTTPException``) as a called name or attribute."""
    name = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", None)
    return isinstance(name, str) and name.endswith("HTTPException")


def http_exception_site(node: ast.AST) -> tuple[int | None, str | None] | None:
    """FastAPI's error site: ``raise HTTPException(status_code, detail)`` -> (status,
    detail when literal)."""
    if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)):
        return None
    call = node.exc
    if not is_http_exception(call.func):
        return None
    kw = {k.arg: k.value for k in call.keywords if k.arg}
    status = status_of(kw.get("status_code", call.args[0] if call.args else None))
    det = kw.get("detail", call.args[1] if len(call.args) > 1 else None)
    detail = det.value if isinstance(det, ast.Constant) and isinstance(det.value, str) else None
    return status, detail


def is_http_exception_class(qual: str) -> bool:
    """A resolved exception class (``"module:Name"``) that is FastAPI's error answer."""
    return qual.endswith(":HTTPException")


def sqlmodel_seed_hook(tables: Sequence[TableModel]) -> InsertHook | None:
    """The seed written through the original's own SQLModel classes (None when they
    cannot be imported): ``lattice.importer.suite.sqlmodel_insert_hook``."""
    from lattice.importer.adapt import _insert_hook

    return _insert_hook(tables)


def fastapi_openapi(app: Any) -> OpenApiFacts:
    """The facts of the app's ``app.openapi()``."""
    from lattice.importer.outside import openapi_facts

    return openapi_facts(app)


# ---------------------------------------------------------------------------- runtime --


@dataclass(frozen=True)
class AppRuntime:
    """How the importer runs an original app (see the module docstring). Every default
    is FastAPI + SQLModel's, i.e. the importer's behaviour before front ends."""

    framework: str = "fastapi"
    transport: TransportFactory = AsgiTransport
    auth: AuthDriver = field(default_factory=AuthDriver)
    # run on every app built, after its requests and before its database is read
    dispose: tuple[Callable[[Any], None], ...] = (dispose_state_engine,)
    # the seed's insert hook for the app's tables (None: plain SQL from the table specs)
    seed_hook: Callable[[Sequence[TableModel]], InsertHook | None] | None = sqlmodel_seed_hook
    # the error sites branch coverage tracks, and the error class the token answers read
    error_site: ErrorSite = http_exception_site
    error_class: Callable[[str], bool] = is_http_exception_class
    # the app's OpenAPI facts (None: the framework has no document)
    openapi: Callable[[Any], OpenApiFacts] | None = fastapi_openapi

    def open(self, app: Any, *, raise_errors: bool = False) -> Transport:
        return self.transport(app, raise_errors=raise_errors)

    def close(self, app: Any) -> None:
        """The dispose hooks, in order."""
        for hook in self.dispose:
            hook(app)

    @contextmanager
    def serve(
        self, app_factory: AppFactory, db_path: Path, *, raise_errors: bool = False
    ) -> Iterator[tuple[Any, Transport]]:
        """``app_factory(db_path)`` and an entered transport to it; the dispose hooks run
        on the way out, whatever happened."""
        app = app_factory(db_path)
        try:
            with self.open(app, raise_errors=raise_errors) as transport:
                yield app, transport
        finally:
            self.close(app)
