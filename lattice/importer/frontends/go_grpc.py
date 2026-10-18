"""The grpc-go front end (``lattice.importer.frontends.FrontEnd``): a Python shim over
``gocheck extract`` for Go servers that register gRPC services (Spike I). It reads the
module's ``.proto`` files as the declared contract and the grpc-go server code (the
registrations, the handler of each rpc, the codes it answers, the interceptors as auth
evidence, the grpc-gateway bindings), and maps them to the same framework-neutral
:class:`~lattice.importer.appmodel.RepoModel` as the Gin front end: an rpc is a route with
``method`` ``RPC`` and ``path`` ``/<pkg.Service>/<Method>`` (the gRPC wire path),
``transport`` ``grpc``, its gRPC codes as raise sites (``kind`` ``grpc_status``, ``code``
the name) and its grpc-gateway bindings listed on the rpc, never as a second route. The
module's Gin routes (if it has any) are carried over unchanged.

Out of scope, listed (never guessed): client, server and bidirectional streaming (an
excluded route, ``streaming_not_supported``), non-Go servers, gRPC-Web, deadlines and
retries as checked behaviour, load balancing, TLS, ``google.rpc.Status`` details.

Detection is on a server registration only (``pb.RegisterFooServer(s, impl)`` or
``s.RegisterService(&desc, impl)`` outside generated code), so a Gin app that imports a
gRPC client keeps the Gin front end. Never, here: a network beyond 127.0.0.1, a Docker
container, a write under the target root, git on the target, an LLM or an API key.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lattice.importer.appmodel import (
    ExcludedRoute,
    OwnerUse,
    RaiseSite,
    RepoModel,
    Route,
    RouteParam,
    RpcInfo,
)
from lattice.importer.frontends import register_first
from lattice.importer.frontends.go_gin import (
    GoExtractError,
    GoGinFrontEnd,
    _go_mod,
    _harness_attr,
    _requires,
    _studio_app,
    model_from_extract,
)
from lattice.importer.graph import GraphHints
from lattice.importer.transport import AppRuntime

GRPC_MODULE = "google.golang.org/grpc"
RPC_METHOD = "RPC"
_SKIP_DIRS = {"vendor", "testdata", "node_modules", ".git"}
_SERVER_CALL = re.compile(r"\bRegister(\w+)Server\s*\(|\.RegisterService\s*\(")
_GENERATED = ("_grpc.pb.go", ".pb.gw.go", ".pb.go")

# proto scalar -> the importer's column types
_TYPES = {
    "string": "Str",
    "bytes": "Str",
    "bool": "Bool",
    "double": "Float",
    "float": "Float",
    "int32": "Int",
    "int64": "Int",
    "uint32": "Int",
    "uint64": "Int",
    "sint32": "Int",
    "sint64": "Int",
    "fixed32": "Int",
    "fixed64": "Int",
    "sfixed32": "Int",
    "sfixed64": "Int",
}


_SQL_TYPES = {"INTEGER": "Int", "INT": "Int", "TEXT": "Str", "VARCHAR": "Str", "REAL": "Float",
              "FLOAT": "Float", "BOOLEAN": "Bool"}  # fmt: skip


def _harness_tables(root: Path, have_go: bool) -> tuple[list[dict[str, Any]], str]:
    """D5.2: the tables the ``studio.toml`` ``db`` harness builds (in a fresh temporary directory
    outside the target, removed after): ``(tables, "")``, or ``([], why)`` when it fails."""
    import tempfile

    from lattice.importer.outside import db_facts

    if have_go or not (spec := _studio_app(root).get("db")):
        return [], ""
    try:
        with tempfile.TemporaryDirectory(prefix="lattice-grpc-db-") as tmp:
            _harness_attr(root, spec)(Path(tmp) / "schema.db")
            facts = db_facts(Path(tmp) / "schema.db")
    except Exception as err:  # noqa: BLE001 -- the reason, not an exception out of the model
        return [], f"{type(err).__name__}: {err}"
    out = []
    for t in facts.tables:
        fk = {f.columns[0]: f"{f.table}.{f.ref_columns[0]}" for f in t.foreign_keys}
        cols = [{"name": c.name, "field": c.name, "go_type": c.type,
                 "type": _SQL_TYPES.get(c.type.upper()), "nullable": c.nullable,
                 "required": not (c.nullable or c.default or c.pk),
                 "default": c.default, "primary_key": bool(c.pk), "unique": (c.name,) in t.unique,
                 "index": any(c.name in i.columns for i in t.indexes), "max_length": None,
                 "foreign_key": fk.get(c.name)} for c in t.columns]  # fmt: skip
        out.append({"struct": t.name, "table": t.name, "name_unread": False, "package": ".",
                    "file": "studio.toml", "line": 0, "source": "harness_db", "migrated_at": [],
                    "columns": cols, "unique_together": [list(u) for u in t.unique if len(u) > 1],
                    "checks": []})  # fmt: skip
    return out, ""


def wire_path(service: str, method: str) -> str:
    """``/notes.v1.Notes/GetNote``: the path a gRPC call travels on."""
    return f"/{service}/{method}"


def _go_files(module_dir: Path) -> list[Path]:
    out: list[Path] = []
    for path in sorted(module_dir.rglob("*.go")):
        rel = path.relative_to(module_dir).parts
        if any(part in _SKIP_DIRS for part in rel[:-1]):
            continue
        if path.name.endswith("_test.go") or path.name.endswith(_GENERATED):
            continue
        out.append(path)
    return out


def has_server_registration(module_dir: Path) -> bool:
    """Whether some non-test, non-generated ``.go`` file registers a gRPC server."""
    for path in _go_files(module_dir):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if _SERVER_CALL.search(text):
            return True
    return False


def _param(field: Mapping[str, Any]) -> RouteParam:
    bounds = field.get("bounds") or {}
    kind = field["type"]
    scalar = _TYPES.get(kind)
    repeated = field["label"] in ("repeated", "map")
    required = bool(bounds.get("required")) or int(bounds.get("min_length") or 0) >= 1
    annotation = kind if field["label"] == "single" else f"{field['label']} {kind}"
    return RouteParam(
        name=field["name"],
        annotation=annotation,
        type=None if repeated else scalar,
        source="message",
        default=None,
        required=required,
        choices=tuple(bounds.get("choices") or ()),
        ge=bounds.get("ge"),
        le=bounds.get("le"),
        min_length=bounds.get("min_length"),
        max_length=bounds.get("max_length"),
        gt=bounds.get("gt"),
        lt=bounds.get("lt"),
    )


def rpc_routes(data: Mapping[str, Any]) -> tuple[tuple[Route, ...], tuple[ExcludedRoute, ...]]:
    """The routes of ``data["grpc"]``: one per unary rpc that is registered (implemented or
    served by the Unimplemented stub); the streaming ones become excluded routes."""
    grpc = data.get("grpc")
    if not grpc:
        return (), ()
    messages = {m["name"]: m for m in grpc["messages"]}
    structs = {t["table"]: t["struct"] for t in data["tables"]}
    routes: list[Route] = []
    excluded: list[ExcludedRoute] = []
    for svc in grpc["services"]:
        for r in svc["rpcs"]:
            path = wire_path(svc["name"], r["name"])
            state = r.get("state", "")
            if state == "streaming":
                excluded.append(
                    ExcludedRoute(
                        RPC_METHOD, path, "unreachable", "not supported: streaming_not_supported"
                    )
                )
                continue
            if state not in ("implemented", "unimplemented"):
                continue  # in the contract but not registered: a spec-only rpc, not a route
            h = r.get("handler")
            short = svc["name"].rsplit(".", 1)[-1]
            name = h["func"] if h else f"{short}.{r['name']}"
            module = h["module"] if h else ""
            fields = messages.get(r["input"], {}).get("fields", [])
            raises = tuple(
                RaiseSite(
                    None,
                    None,
                    c["line"],
                    "grpc_status"
                    if c["kind"] == "status_error"
                    else ("plain_error" if c["kind"] == "plain_error" else "grpc_unimplemented"),
                    code=c["name"],
                )
                for c in r.get("codes", [])
            )
            member = r.get("auth") == "Member"
            auth = "Customer" if member else r.get("auth", "unclear")
            unread = "; ".join(
                f"{u['file']}:{u['line']} {u['what']}: {u['reason']}" for u in r.get("unread", [])
            )
            routes.append(
                Route(
                    name=name,
                    module=module,
                    method=RPC_METHOD,
                    path=path,
                    params=tuple(_param(f) for f in fields),
                    response_model=r["output"],
                    response_class=None,
                    status_code=None,
                    dependencies=(),
                    auth=auth,
                    auth_reason=r.get("auth_reason", ""),
                    raises=raises,
                    calls=tuple(r.get("calls", [])),
                    tables=tuple(structs.get(t, t) for t in r.get("tables", [])),
                    owner_uses=tuple(
                        OwnerUse(o["model"], o["column"], o["how"], o["line"])
                        for o in r.get("owner_uses") or []
                    ),
                    body_models=(r["input"],),
                    line=h["line"] if h else r["line"],
                    view=f"{module}:{name}" if h else None,
                    unread=unread or None,
                    transport="grpc",
                    rpc=RpcInfo(
                        svc["name"], r["name"], r["input"], r["output"], state, svc["file"]
                    ),
                    http_bindings=tuple(
                        (b["method"], b["path"]) for b in r.get("http_bindings") or []
                    ),
                )
            )
    return tuple(routes), tuple(excluded)


def model_from_grpc_extract(data: Mapping[str, Any], package: str = ".") -> RepoModel:
    """One ``lattice.goextract/1`` document as the app model: the module's Gin machinery
    (tables, models, any Gin routes), plus the rpcs as routes."""
    base = model_from_extract(data, package)
    routes, excluded = rpc_routes(data)
    return dataclasses.replace(
        base,
        routes=(*base.routes, *routes),
        excluded_routes=(*base.excluded_routes, *excluded),
        framework="grpc-go",
    )


class GoGrpcFrontEnd(GoGinFrontEnd):
    """The Go + grpc-go + GORM front end (extraction shared with the Gin front end)."""

    name = "grpc-go"

    @property
    def graph_hints(self) -> GraphHints:
        return GraphHints(
            is_route=lambda node: False,
            is_table=lambda node: False,
            is_depends=lambda node: False,
            overrides_attr=None,
            sql_modules=(),
        )

    def detect(self, root: Path, package: str) -> bool:
        """``go.mod`` requires grpc-go **and** some non-test, non-generated ``.go`` file
        registers a gRPC server (``RegisterFooServer(`` / ``.RegisterService(``)."""
        module_dir = self._module_dir(root, package)
        text = _go_mod(module_dir)
        if text is None or GRPC_MODULE not in _requires(text):
            return False
        return has_server_registration(module_dir)

    def extract_json(
        self, root: Path | str, package: str = ".", workflow: bool = False
    ) -> dict[str, Any]:
        """The Gin extraction; with no table in the Go tree, the ``db`` harness's tables (D5.2)."""
        data = super().extract_json(root, package, workflow)
        tables, why = _harness_tables(self._module_dir(root, package), bool(data["tables"]))
        if tables:
            data["tables"], data["tables_source"] = tables, "harness_db"
        if why:
            data["unread"].append(
                {"file": "studio.toml", "line": 0, "what": "harness_db_failed", "reason": why}
            )
        return data

    def extract(self, root: Path, package: str = ".") -> RepoModel:
        data = self.extract_json(root, package)
        self.last_extract = data
        return model_from_grpc_extract(data, package)

    def sidecar(self) -> str:
        if self.last_extract is None:
            raise GoExtractError("nothing has been extracted yet")
        return json.dumps(self.last_extract, indent=2, sort_keys=False) + "\n"

    def runtime(
        self,
        root: Path,
        package: str = ".",
        *,
        model: RepoModel | None = None,
        credentials: Mapping[str, tuple[str, str]] | None = None,
    ) -> AppRuntime:
        """The harness's ``RUNTIME`` (``studio.toml`` ``[app] runtime``), else a process
        runtime for ``framework="go"`` (callers log in through the harness's driver)."""
        from lattice.spec.process import process_runtime

        spec = _studio_app(Path(root)).get("runtime")
        if spec:
            rt = _harness_attr(Path(root), spec)
            if isinstance(rt, AppRuntime):
                return rt
        return process_runtime(framework="go")

    def reconcile(  # type: ignore[override]
        self, root: Path | str, model: RepoModel, *, timeout: float = 30.0
    ) -> dict[str, Any]:
        """Which extracted rpcs the server answers: see :func:`lattice.spec.grpc.reconcile`
        (the app's own binary on a throwaway SQLite file, no Docker)."""
        from lattice.spec.grpc import reconcile

        return reconcile(Path(root), model, timeout=timeout)


FRONT_END = register_first(GoGrpcFrontEnd())
