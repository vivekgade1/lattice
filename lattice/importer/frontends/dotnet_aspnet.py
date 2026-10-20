"""The .NET (ASP.NET Core + EF Core) front end (``lattice.importer.frontends.FrontEnd``): a Python
shim over ``lattice.dncheck`` (``dncheck extract``, a Roslyn helper that reads the project's C#
source and answers ``lattice.dnextract/1`` JSON: the ``lattice.goextract/1`` key set plus one
``dotnet`` key). This module maps that to a :class:`~lattice.importer.appmodel.RepoModel` the way
the Go front end does (:func:`lattice.importer.frontends.go_gin.model_from_extract` is the same
mapping, reused read-only: the key set is the contract, ``docs/results/data/2026-10-18-dotnet/
b1-report.md``); what the model has no field for stays in the JSON, kept on
:attr:`AspNetFrontEnd.last_extract`.

Never, here: a network beyond 127.0.0.1, ``docker pull``, ``dotnet build``/``restore`` in (or any
write under) the target, an LLM or an API key. The helper and the shim only read the target;
:meth:`AspNetFrontEnd.reconcile` runs the app's own build (the harness's) on a throwaway Docker
server named ``lattice-dnx-*`` and reports which of the extracted routes it answers.

(This package's ``__init__`` loads this module lazily -- ``select("aspnetcore")`` or after every
Python and Go front end declined -- so a Python or Go import never reaches it.)
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import os
import re
import secrets
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lattice.importer.appmodel import RepoModel
from lattice.importer.frontends import DECLINED, front_ends, register
from lattice.importer.frontends.go_gin import (
    SCHEMA as GO_SCHEMA,
)
from lattice.importer.frontends.go_gin import (
    _harness_attr,
    _studio_app,
)
from lattice.importer.frontends.go_gin import model_from_extract as _model_from_goextract
from lattice.importer.graph import GraphHints
from lattice.importer.transport import AppRuntime, AuthDriver

SCHEMA = "lattice.dnextract/1"
CONTAINER_PREFIX = "lattice-dnx-"
EF_PACKAGES = (
    r"(?:Microsoft\.EntityFrameworkCore|Pomelo\.EntityFrameworkCore\.MySql"
    r"|MySql\.EntityFrameworkCore)"
)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_SDK_WEB = re.compile(
    r"""<(?:Project|Sdk)\b[^>]*\b(?:Sdk|Name)\s*=\s*["']Microsoft\.NET\.Sdk\.Web["']""", re.I
)
_WEB_REF = re.compile(
    r"""<FrameworkReference\b[^>]*\bInclude\s*=\s*["']Microsoft\.AspNetCore\.App["']"""
    r"""|<PackageReference\b[^>]*\bInclude\s*=\s*["']Microsoft\.AspNetCore\.""",
    re.I,
)
_EF_REF = re.compile(
    rf"""<(?:PackageReference|PackageVersion)\b[^>]*\bInclude\s*=\s*["']{EF_PACKAGES}""", re.I
)
_PROJECT_REF = re.compile(r"""<ProjectReference\b[^>]*\bInclude\s*=\s*["']([^"']+)["']""", re.I)
REFERENCE_DEPTH = 4


class DotnetFrontEndError(RuntimeError):
    """The extractor answered something the shim cannot map."""


def _text(path: Path) -> str:
    try:
        return _COMMENT.sub("", path.read_text(encoding="utf-8-sig", errors="replace"))
    except OSError:
        return ""


def _csprojs(module_dir: Path) -> list[Path]:
    """The project files directly in ``module_dir`` (hidden ones, ``._x.csproj``, are not)."""
    if not module_dir.is_dir():
        return []
    return sorted(p for p in module_dir.glob("*.csproj") if not p.name.startswith("."))


def _is_web(text: str) -> bool:
    return bool(_SDK_WEB.search(text) or _WEB_REF.search(text))


def _central_packages(project: Path, root: Path) -> bool:
    """A ``Directory.Packages.props`` from the project's directory up to ``root`` lists EF Core."""
    here = project.parent.resolve()
    top = root.resolve()
    while True:
        if _EF_REF.search(_text(here / "Directory.Packages.props")):
            return True
        if here == top or here.parent == here or top not in here.parents:
            return False
        here = here.parent


def _references_ef(
    project: Path, root: Path, seen: set[Path], depth: int = REFERENCE_DEPTH
) -> bool:
    """EF Core in the project, a ``<ProjectReference>``d project, or central package management."""
    path = project.resolve()
    if path in seen or depth < 0:
        return False
    seen.add(path)
    text = _text(path)
    if _EF_REF.search(text) or _central_packages(path, root):
        return True
    for ref in _PROJECT_REF.findall(text):
        target = (path.parent / ref.replace("\\", "/")).resolve()
        if target.is_file() and _references_ef(target, root, seen, depth - 1):
            return True
    return False


def model_from_extract(
    data: Mapping[str, Any], package: str = ".", database: str | None = None
) -> RepoModel:
    """Map one ``lattice.dnextract/1`` document to the importer's app model. ``database`` is the
    ``studio.toml`` ``[app] database`` (the dialect when the code names two providers)."""
    if data.get("schema") != SCHEMA:
        raise DotnetFrontEndError(f"unexpected extractor schema {data.get('schema')!r}")
    model = _model_from_goextract({**data, "schema": GO_SCHEMA}, package)
    columns = {(t["struct"], c["name"]): c for t in data["tables"] for c in t["columns"]}
    entity = {t["table"]: t["struct"] for t in data["tables"]}  # a route lists SQL table names
    tables = tuple(
        dataclasses.replace(
            t,
            columns=tuple(
                dataclasses.replace(c, choices=tuple(columns[(t.name, c.name)]["choices"] or ()))
                for c in t.columns
            ),
        )
        for t in model.tables
    )
    routes = tuple(
        dataclasses.replace(
            r,
            auth=ROLES.get(r.auth, r.auth),  # the extract says Public | Member | Admin
            tables=tuple(dict.fromkeys(entity.get(x, x) for x in r.tables)),
        )
        for r in model.routes
    )
    providers = (data.get("dotnet") or {}).get("providers") or []
    dialect = model.dialect or database or ("sqlite" if "sqlite" in providers else None)
    return dataclasses.replace(
        model, tables=tables, routes=routes, dialect=dialect, framework="aspnetcore", orm="efcore"
    )


# the importer's role vocabulary for the extract's route auth
ROLES = {"Member": "Customer"}

# the constant a probe sends for a login field the credentials do not cover, by parameter type
_LOGIN_VALUES = {"Uuid": "00000000-0000-4000-8000-000000000001", "Int": 1, "Bool": True}


def login_fields(data: Mapping[str, Any]) -> dict[str, Any]:
    """The extra fields the login route requires beyond the user name and the password (a device
    or installation id): ``{wire name: constant}``. The driver posts the two credentials only."""
    auth = data.get("auth") or {}
    names = {auth.get("username_field"), auth.get("password_field")}
    return {
        p["name"]: _LOGIN_VALUES.get(p["type"], "lattice-import")
        for r in data["routes"]
        if r["method"] == "POST" and r["path_template"] == auth.get("login_path")
        for p in r["params"]
        if p["source"] == "body" and p["required"] and p["name"] not in names
    }


class _LoginClient:
    """An httpx-compatible client adding ``fields`` to the JSON posted to the login ``path``."""

    def __init__(self, inner: Any, path: str, fields: Mapping[str, Any]) -> None:
        self._inner, self._path, self._fields = inner, path, fields

    def post(self, url: str, **kwargs: Any) -> Any:
        if url == self._path and isinstance(kwargs.get("json"), dict):
            kwargs["json"] = {**self._fields, **kwargs["json"]}
        return self._inner.post(url, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _LoginTransport:
    """A transport whose ``client`` is a :class:`_LoginClient` (all else the wrapped one's)."""

    def __init__(self, inner: Any, path: str, fields: Mapping[str, Any]) -> None:
        self._inner = inner
        self.client = _LoginClient(inner.client, path, fields)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __enter__(self) -> _LoginTransport:
        self._inner.__enter__()
        return self

    def __exit__(self, *exc: object) -> Any:
        return self._inner.__exit__(*exc)


class AspNetFrontEnd:
    """The ASP.NET Core + EF Core front end."""

    name = "aspnetcore"

    def __init__(self) -> None:
        self.last_extract: dict[str, Any] | None = None
        self.body_inferences: list[dict[str, Any]] = []

    @property
    def graph_hints(self) -> GraphHints:
        return GraphHints(
            is_route=lambda node: False,
            is_table=lambda node: False,
            is_depends=lambda node: False,
            overrides_attr=None,
            sql_modules=(),
        )

    # -- detect / extract ------------------------------------------------------------------

    def _module_dir(self, root: Path | str, package: str) -> Path:
        return Path(root) / package

    def detect(self, root: Path, package: str) -> bool:
        """Exactly one ``*.csproj`` in ``<root>/<package>``, a web project (``Sdk.Web``, the
        ``Microsoft.AspNetCore.App`` framework reference or a ``Microsoft.AspNetCore.*`` package)
        that references EF Core. Two or more project files, or a web project without EF Core, are
        False with a note (:data:`~lattice.importer.frontends.DECLINED`) for ``select``'s error."""
        module_dir = self._module_dir(root, package)
        projects = _csprojs(module_dir)
        if len(projects) > 1:
            names = ", ".join(p.name for p in projects)
            DECLINED.append(
                f"{module_dir} holds {len(projects)} project files ({names}): "
                "name the web project's directory as the package"
            )
            return False
        if not projects or not _is_web(_text(projects[0])):
            return False
        if _references_ef(projects[0], Path(root), set()):
            return True
        DECLINED.append(
            f"{projects[0].name} is an ASP.NET Core project without EF Core: data access "
            "through Dapper or plain ADO.NET is not supported"
        )
        return False

    def extract_json(
        self,
        root: Path | str,
        package: str = ".",
        workflow: bool = False,
        policy: bool = False,
    ) -> dict[str, Any]:
        """``dncheck extract`` on the project: its JSON (read-only on the target).
        ``workflow=True`` / ``policy=True`` ask for the opt-in keys (the default output is
        unchanged)."""
        from lattice.dncheck import extract_json

        return extract_json(
            self._module_dir(root, package),
            workflow=workflow,
            policy=policy,
            workspace=Path(root),
        )

    def extract(self, root: Path, package: str = ".") -> RepoModel:
        data = self.extract_json(root, package)
        self.last_extract = data
        database = _studio_app(self._module_dir(root, package)).get("database")
        model = model_from_extract(data, package, database.lower() if database else None)
        # a body read field by field becomes one parameter typed by a model (the DTO itself when
        # the route binds one; else a synthesised one): the contract builder reads nothing else
        from lattice.importer.go_rules import infer_body_models

        model, self.body_inferences = infer_body_models(data, model)
        return model

    def sidecar(self) -> str:
        """The last extraction as deterministic JSON (what the model has no field for)."""
        if self.last_extract is None:
            raise DotnetFrontEndError("nothing has been extracted yet")
        return json.dumps(self.last_extract, indent=2, sort_keys=False) + "\n"

    # -- runtime ---------------------------------------------------------------------------

    def runtime(
        self,
        root: Path,
        package: str = ".",
        *,
        model: RepoModel | None = None,
        credentials: Mapping[str, tuple[str, str]] | None = None,
    ) -> AppRuntime:
        """The harness's ``RUNTIME`` (``studio.toml`` ``[app] runtime``), else a process runtime
        for ``framework="dotnet"`` logging in through the extracted login route (wire names)."""
        from lattice.spec.process import process_runtime

        spec = _studio_app(Path(root)).get("runtime")
        if spec:
            rt = _harness_attr(Path(root), spec)
            if isinstance(rt, AppRuntime):
                return self._with_login_fields(rt)
        auth = AuthDriver()
        a = model.auth if model is not None else None
        if a is not None and a.login_path and credentials:
            auth = AuthDriver.json(
                a.login_path,
                credentials,
                username_field=a.username_field,
                password_field=a.password_field,
                token_path=a.token_path,
                scheme=a.scheme,
            )
        elif a is not None:
            auth = AuthDriver(scheme=a.scheme)
        return self._with_login_fields(process_runtime(framework="dotnet", auth=auth))

    def _with_login_fields(self, rt: AppRuntime) -> AppRuntime:
        """``rt``, its transports adding the login route's other required fields (see
        :func:`login_fields`) to the credentials a probe posts."""
        data = self.last_extract
        fields = login_fields(data) if data is not None else {}
        path = ((data or {}).get("auth") or {}).get("login_path")
        if not fields or not path:
            return rt
        opener = rt.transport
        return dataclasses.replace(
            rt, transport=lambda app, **kw: _LoginTransport(opener(app, **kw), path, fields)
        )

    # -- reconcile -------------------------------------------------------------------------

    def reconcile(
        self, root: Path | str, model: RepoModel, *, timeout: float = 30.0
    ) -> dict[str, Any]:
        """Which extracted routes the app's build answers: each route called unauthenticated
        (path parameters ``1``, ``{}`` on writes) plus a control request (same method) to
        ``/zz-absent-<hash>``, a path no sibling absorbs.
        The result holds, per ``METHOD path``, ``status``, ``control`` and ``observed`` (the status
        is neither 404 nor 405) -- no port, no timing. Needs the harness (``studio.toml`` ``[app]
        app``, ``db``), Docker and a MySQL-capable driver; a missing one is an error naming it,
        never a SQLite fallback. The throwaway server is removed in ``finally``."""
        import httpx

        from lattice.spec import dbserver

        root = Path(root)
        cfg = _studio_app(root)
        if "app" not in cfg or "db" not in cfg:
            raise DotnetFrontEndError(f"{root}/studio.toml names no [app] app / db harness")
        saved = {
            k: v for k, v in os.environ.items() if k.startswith("LATTICE_") and k.endswith("_URL")
        }
        for k in saved:
            del os.environ[k]
        server = None
        owned = False
        app = None
        dbname = f"dnx_{secrets.token_hex(4)}"
        try:
            make_app = _harness_attr(root, cfg["app"])
            build_db = _harness_attr(root, cfg["db"])
            if not dbserver.docker_available():
                raise DotnetFrontEndError("reconcile needs Docker (not available)")
            server, owned = dbserver.server_for("mysql", None, name_prefix=CONTAINER_PREFIX)
            url = server.create(dbname)
            build_db(url)
            app = make_app(url)
            app.start()
            out: dict[str, Any] = {}
            with httpx.Client(base_url=app.base_url, timeout=timeout) as client:
                for r in model.routes:
                    path = re.sub(r"\{[^}]*\}", "1", r.path)
                    body = {} if r.method in ("POST", "PUT", "PATCH") else None
                    kw = {"json": body} if body is not None else {}
                    status = client.request(r.method, path, **kw).status_code
                    ckw = {"json": {}} if body is not None else {}
                    # a control that no parametrised sibling (`/habits/{id}`) absorbs
                    absent = (
                        "/zz-absent-"
                        + hashlib.sha256(f"{r.method} {r.path}".encode()).hexdigest()[:8]
                    )
                    control = client.request(r.method, absent, **ckw)
                    out[f"{r.method} {r.path}"] = {
                        "status": status,
                        "control": control.status_code,
                        "observed": status not in (404, 405),
                    }
            return out
        finally:
            if app is not None:
                with contextlib.suppress(Exception):
                    app.stop()
            if server is not None:
                with contextlib.suppress(Exception):
                    server.drop(dbname)
                if owned:
                    with contextlib.suppress(Exception):
                        server.stop()
            os.environ.update(saved)


front_ends()  # the built-in (FastAPI) front end stays first
FRONT_END = register(AspNetFrontEnd())
