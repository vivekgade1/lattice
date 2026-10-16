"""The Go (Gin + GORM) front end (``lattice.importer.frontends.FrontEnd``): a Python shim
over ``gocheck extract`` (``lattice/gocheck/src/goextract*.go``), which reads the module's
Go source -- tables, routes (full path, handler, middleware chain, auth), request fields,
raise sites, owner evidence -- and answers ``lattice.goextract/1`` JSON. This module maps
that to a :class:`~lattice.importer.appmodel.RepoModel`; what the model has no field for
(status writes, transactions, the full unread list...) stays in the JSON, kept on
:attr:`GoGinFrontEnd.last_extract`.

Never, here: a network beyond 127.0.0.1, ``docker pull``, ``go build`` in (or any write
under) the target root, git on the target, an LLM or an API key. The extractor and the
shim only read the target; :meth:`GoGinFrontEnd.reconcile` runs the app's own binary (the
harness's) on a throwaway Docker server named ``lattice-gox-*`` and reports which of the
extracted routes the binary answers.

(This package's ``__init__`` loads this module lazily -- ``select("gin")`` or after every
Python front end declined -- so a Python import never reaches it.)
"""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import re
import secrets
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lattice.importer.appmodel import (
    AuthModel,
    DataModel,
    ExcludedRoute,
    FieldSpec,
    Guard,
    OwnerUse,
    RaiseSite,
    RepoModel,
    Route,
    RouteParam,
    TableConstraint,
    TableModel,
)
from lattice.importer.frontends import front_ends, register
from lattice.importer.graph import GraphHints
from lattice.importer.transport import AppRuntime, AuthDriver

EXTRACT_TIMEOUT_S = 120
SCHEMA = "lattice.goextract/1"
CONTAINER_PREFIX = "lattice-gox-"

_REQ = re.compile(r"^\s*(?:require\s+)?([A-Za-z0-9._~/-]+\.[A-Za-z0-9._~/-]+)\s+v[0-9]", re.M)


class GoExtractError(RuntimeError):
    """``gocheck extract`` could not run or answered something unreadable."""


def _go_mod(module_dir: Path) -> str | None:
    p = module_dir / "go.mod"
    try:
        return p.read_text()
    except OSError:
        return None


def _requires(text: str) -> set[str]:
    return set(_REQ.findall(text))


def _none_if_empty(value: Any) -> Any:
    return None if value in ("", [], None) else value


def _field(col: Mapping[str, Any]) -> FieldSpec:
    return FieldSpec(
        name=col["name"],
        annotation=col["go_type"],
        type=col["type"],
        nullable=bool(col["nullable"]),
        required=bool(col["required"]),
        default=col["default"],
        max_length=col["max_length"],
        unique=bool(col["unique"]),
        primary_key=bool(col["primary_key"]),
        foreign_key=col["foreign_key"],
        index=bool(col["index"]),
    )


def _param(p: Mapping[str, Any]) -> RouteParam:
    return RouteParam(
        name=p["name"],
        annotation=p["annotation"] or "",
        type=p["type"],
        source=p["source"],
        default=p["default"],
        required=bool(p["required"]),
        choices=tuple(p["choices"] or ()),
        ge=p["ge"],
        le=p["le"],
        min_length=p["min_length"],
        max_length=p["max_length"],
    )


def model_from_extract(data: Mapping[str, Any], package: str = ".") -> RepoModel:
    """Map one ``lattice.goextract/1`` document to the importer's app model."""
    if data.get("schema") != SCHEMA:
        raise GoExtractError(f"unexpected extractor schema {data.get('schema')!r}")
    tables = []
    constraints = []
    for t in data["tables"]:
        cols = tuple(_field(c) for c in t["columns"])
        together = tuple(tuple(u) for u in t["unique_together"])
        tables.append(
            TableModel(
                name=t["struct"],
                table_name=t["table"],
                module=t["package"],
                columns=cols,
                bases=(),
                unique_together=together,
                checks=tuple(t["checks"]),
            )
        )
        constraints += [TableConstraint(t["struct"], "unique", tuple(u)) for u in together]
        constraints += [TableConstraint(t["struct"], "check", (), expr=c) for c in t["checks"]]
    models = tuple(
        DataModel(
            name=m["name"],
            module=m["package"],
            fields=tuple(
                FieldSpec(
                    name=f["name"],
                    annotation=f["annotation"] or "",
                    type=f["type"],
                    required=bool(f["required"]),
                    min_length=f["min_length"],
                    max_length=f["max_length"],
                    ge=f["ge"],
                    le=f["le"],
                    choices=tuple(f["choices"] or ()),
                )
                for f in m["fields"]
            ),
            bases=(),
        )
        for m in data["models"]
    )
    routes = []
    for r in data["routes"]:
        h = r["handler"]
        if h is None:
            name, module, view = f"{r['method']} {r['path_template']}", "", None
        else:
            name, module = h["func"], h["module"]
            view = f"{module}:{h['func']}"
        unread = "; ".join(
            f"{u['file']}:{u['line']} {u['what']}: {u['reason']}" for u in r["unread"]
        )
        routes.append(
            Route(
                name=name,
                module=module,
                method=r["method"],
                path=r["path_template"],
                params=tuple(_param(p) for p in r["params"]),
                response_model=None,
                response_class=None,
                status_code=r["status_code"],
                dependencies=(),
                auth=r["auth"],
                auth_reason=r["auth_reason"],
                raises=tuple(
                    RaiseSite(x["status"], x["code"], x["line"], x["kind"]) for x in r["raises"]
                ),
                calls=tuple(r["calls"]),
                tables=tuple(r["tables"]),
                owner_uses=tuple(
                    OwnerUse(o["model"], o["column"], o["how"], o["line"]) for o in r["owner_uses"]
                ),
                body_models=tuple(r["body_models"]),
                line=r["line"],
                guards=tuple(
                    Guard(g["kind"], g["source"], g["status"], g["detail"], (), g["line"])
                    for g in r["guards"]
                ),
                view=view,
                unread=unread or None,
            )
        )
    excluded = tuple(
        ExcludedRoute(
            x.get("method", ""),
            x.get("path", ""),
            "framework" if x["kind"] == "framework" else "unreachable",
            x["reason"],
        )
        for x in data["excluded"]
        if x["what"] == "route"
    )
    a = data.get("auth")
    auth = None
    if a is not None:
        auth = AuthModel(
            login_path=a["login_path"],
            login_encoding=a["login_encoding"],
            username_field=a["username_field"] or "username",
            password_field=a["password_field"] or "password",
            token_path=(a["token_field"],) if a["token_field"] else ("access_token",),
            scheme=a["scheme"] or "bearer",
            users_table=a["users_table"],
            password_column=a["password_column"],
        )
    return RepoModel(
        app_package=data["module"] or package,
        tables=tuple(tables),
        models=models,
        dependencies=(),
        routes=tuple(routes),
        natives=(),
        database_url=None,
        dialect=data["dialect"] or None,
        exception_handlers=(),
        custom_serialization=(),
        framework="gin",
        orm="gorm",
        auth=auth,
        constraints=tuple(constraints),
        excluded_routes=excluded,
    )


def _harness_attr(root: Path, spec: str) -> Any:
    """``module:attr`` of the project's ``studio.toml`` ``[app]`` table, imported with the
    project root on ``sys.path`` (and no bytecode written there)."""
    module, _, attr = spec.partition(":")
    dont = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    sys.modules.pop(module, None)
    try:
        return getattr(importlib.import_module(module), attr)
    finally:
        sys.path.remove(str(root))
        sys.dont_write_bytecode = dont


def _studio_app(root: Path) -> dict[str, str]:
    p = root / "studio.toml"
    if not p.is_file():
        return {}
    app = tomllib.loads(p.read_text()).get("app", {})
    return {k: v for k, v in app.items() if isinstance(v, str)}


class GoGinFrontEnd:
    """The Go + Gin + GORM front end."""

    name = "gin"

    def __init__(self) -> None:
        self.last_extract: dict[str, Any] | None = None

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
        """``<root>/<package>/go.mod`` requires Gin and GORM (no go.mod: False)."""
        text = _go_mod(self._module_dir(root, package))
        if text is None:
            return False
        req = _requires(text)
        return "github.com/gin-gonic/gin" in req and "gorm.io/gorm" in req

    def extract_json(
        self, root: Path | str, package: str = ".", workflow: bool = False
    ) -> dict[str, Any]:
        """``gocheck extract`` on the module: its JSON (read-only on the target).
        ``workflow=True`` asks for the workflow layer's extra keys (opt-in: the default
        input and output are unchanged)."""
        from lattice.gocheck import build_gocheck

        module_dir = self._module_dir(root, package).resolve()
        if not module_dir.is_dir():
            raise GoExtractError(f"no Go module directory at {module_dir}")
        binary = build_gocheck()
        try:
            proc = subprocess.run(  # noqa: S603 -- the project's own checker binary
                [str(binary), "extract"],
                input=json.dumps({"root": str(module_dir), "workflow": True})
                if workflow
                else json.dumps({"root": str(module_dir)}),
                capture_output=True,
                text=True,
                check=False,
                timeout=EXTRACT_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as err:
            raise GoExtractError(f"gocheck extract timed out after {EXTRACT_TIMEOUT_S}s") from err
        if proc.returncode != 0:
            raise GoExtractError(
                f"gocheck extract failed (exit {proc.returncode}): {proc.stderr.strip()}"
            )
        try:
            data = json.loads(proc.stdout)
        except ValueError as err:
            raise GoExtractError(f"gocheck extract answered invalid JSON: {err}") from err
        return data  # type: ignore[no-any-return]

    def extract(self, root: Path, package: str = ".") -> RepoModel:
        data = self.extract_json(root, package)
        self.last_extract = data
        return model_from_extract(data, package)

    def sidecar(self) -> str:
        """The last extraction as deterministic JSON (what the model has no field for)."""
        if self.last_extract is None:
            raise GoExtractError("nothing has been extracted yet")
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
        """The harness's ``RUNTIME`` (``studio.toml`` ``[app] runtime``), else a process
        runtime for ``framework="go"`` logging in through the extracted login route."""
        from lattice.spec.process import process_runtime

        spec = _studio_app(Path(root)).get("runtime")
        if spec:
            rt = _harness_attr(Path(root), spec)
            if isinstance(rt, AppRuntime):
                return rt
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
        return process_runtime(framework="go", auth=auth)

    # -- reconcile -------------------------------------------------------------------------

    def reconcile(
        self, root: Path | str, model: RepoModel, *, timeout: float = 30.0
    ) -> dict[str, Any]:
        """Which extracted routes the app's binary answers: each route called
        unauthenticated (path parameters ``1``, ``{}`` on writes) plus a control request
        to ``<path>/zz-absent``. The result holds, per ``METHOD path``, ``status``,
        ``control`` and ``observed`` (the status is neither 404 nor 405) -- no port, no
        timing. Needs the harness (``studio.toml`` ``[app] app``, ``db``), Docker and a
        MySQL-capable driver; a missing one is an error naming it, never a SQLite
        fallback. The throwaway server is removed in ``finally``."""
        import httpx

        from lattice.spec import dbserver

        root = Path(root)
        cfg = _studio_app(root)
        if "app" not in cfg or "db" not in cfg:
            raise GoExtractError(f"{root}/studio.toml names no [app] app / db harness")
        saved = {
            k: v for k, v in os.environ.items() if k.startswith("LATTICE_") and k.endswith("_URL")
        }
        for k in saved:
            del os.environ[k]
        server = None
        owned = False
        app = None
        dbname = f"gox_{secrets.token_hex(4)}"
        try:
            make_app = _harness_attr(root, cfg["app"])
            build_db = _harness_attr(root, cfg["db"])
            if not dbserver.docker_available():
                raise GoExtractError("reconcile needs Docker (not available)")
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
                    control = client.request(r.method, path.rstrip("/") + "/zz-absent", **ckw)
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
FRONT_END = register(GoGinFrontEnd())
