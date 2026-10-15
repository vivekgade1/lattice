"""A zero-dependency MCP server over stdio: JSON-RPC 2.0, one JSON message per line.

Implements ``initialize``, ``notifications/initialized``, ``ping``, ``tools/list`` and
``tools/call``. Protocol errors are JSON-RPC errors; a tool that fails answers a normal
result with ``isError: true``. stdout carries only protocol messages (anything else
written to it, by the app under check for instance, is sent to stderr)."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO, Any

from lattice.mcp.tools import (
    OUTPUT_CAP,
    HttpBackend,
    PendingBackend,
    Toolbox,
    ToolError,
    cap_result,
)

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_VERSION = "2024-11-05"
SERVER_INFO = {"name": "lattice", "version": "0.1.0"}

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

INSTRUCTIONS = (
    "Lattice Studio tools for this project's requirements spec. Read with model_get, "
    "rule_get, spec_ask, spec_lint, code_links, graph_get. Never edit REQUIREMENTS.md, "
    "spec.py or spec.trace.json directly: propose a change with change_propose, poll "
    "change_get, and tell the owner to accept it in Lattice Studio."
)


class McpServer:
    def __init__(self, tools: Toolbox, *, cap: int = OUTPUT_CAP) -> None:
        self.tools = tools
        self.cap = cap
        self.initialized = False
        self._write_lock = threading.Lock()

    # -- one message in, zero or one out
    def handle(self, msg: Any) -> dict[str, Any] | None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            if (
                isinstance(msg, dict)
                and "method" not in msg
                and ("result" in msg or "error" in msg)
            ):
                return None  # a response to a request we never send
            return self._err(
                msg.get("id") if isinstance(msg, dict) else None,
                INVALID_REQUEST,
                "not a JSON-RPC 2.0 request",
            )
        method = msg["method"]
        rid = msg.get("id")
        is_note = "id" not in msg
        params = msg.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, dict):
            return None if is_note else self._err(rid, INVALID_REQUEST, "bad method or params")
        try:
            result = self._dispatch(method, params, is_note)
        except _RpcError as e:
            return None if is_note else self._err(rid, e.code, e.message)
        except Exception as e:  # noqa: BLE001 -- a bug must not kill the session
            traceback.print_exc(file=sys.stderr)
            return None if is_note else self._err(rid, INTERNAL_ERROR, f"{type(e).__name__}: {e}")
        if is_note:
            return None
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def _dispatch(self, method: str, params: dict[str, Any], is_note: bool) -> Any:
        if method == "initialize":
            want = params.get("protocolVersion")
            version = want if want in SUPPORTED_VERSIONS else DEFAULT_VERSION
            return {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": (
                    self.tools.instructions(INSTRUCTIONS)
                    if hasattr(self.tools, "instructions")
                    else INSTRUCTIONS
                ),
            }
        if method == "notifications/initialized":
            self.initialized = True
            return None
        if method.startswith("notifications/"):
            return None
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self.tools.list_tools()}
        if method == "tools/call":
            return self._call(params)
        raise _RpcError(METHOD_NOT_FOUND, f"method not found: {method}")

    def _call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str):
            raise _RpcError(INVALID_PARAMS, "tools/call needs a string `name`")
        args = params.get("arguments")
        if args is not None and not isinstance(args, dict):
            raise _RpcError(INVALID_PARAMS, "`arguments` must be an object")
        try:
            data = self.tools.call(name, args)
        except KeyError:
            raise _RpcError(INVALID_PARAMS, f"unknown tool: {name}") from None
        except ToolError as e:
            return {"content": [{"type": "text", "text": str(e)}], "isError": True}
        except Exception as e:  # noqa: BLE001 -- tool bugs are tool failures
            traceback.print_exc(file=sys.stderr)
            msg = f"{type(e).__name__}: {e}"
            return {"content": [{"type": "text", "text": msg}], "isError": True}
        text, truncated = cap_result(data, self.cap)
        out: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": False}
        out["structuredContent"] = (
            {"truncated": True, **json.loads(text)} if truncated else _structured(data)
        )
        return out

    @staticmethod
    def _err(rid: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}

    # -- the loop
    def run(self, stdin: IO[str], stdout: IO[str]) -> None:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as e:
                self._send(stdout, self._err(None, PARSE_ERROR, f"parse error: {e}"))
                continue
            if isinstance(msg, list):  # a JSON-RPC batch
                outs = [r for m in msg if (r := self.handle(m)) is not None]
                if outs:
                    self._send(stdout, outs)
                continue
            resp = self.handle(msg)
            if resp is not None:
                self._send(stdout, resp)

    def _send(self, stdout: IO[str], obj: Any) -> None:
        with self._write_lock:
            stdout.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
            stdout.flush()


def _structured(data: Any) -> Any:
    return data if isinstance(data, Mapping) else {"result": data}


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


LISTING_TIMEOUT = 10.0  # seconds, for GET <base>/api/projects


def studio_base(url: str) -> str:
    """The Studio's base URL: `url` without a trailing ``/`` and without a trailing
    ``/p/<name>`` (chat sessions pass ``http://host/p/<name>``)."""
    return re.sub(r"/p/[^/]+$", "", url.rstrip("/"))


def studio_listing(base: str) -> list[Any] | None:
    """``GET <base>/api/projects``: the hub's projects, or None when the Studio has no such
    listing (404: a Studio without the hub API). Any other failure is a :class:`ToolError`."""
    try:
        with urllib.request.urlopen(  # noqa: S310 -- the owner's Studio URL
            base + "/api/projects", timeout=LISTING_TIMEOUT
        ) as r:
            data = json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        try:
            detail = json.loads(e.read().decode()).get("detail") or e.reason
        except Exception:  # noqa: BLE001
            detail = e.reason
        raise ToolError(f"Studio answered {e.code}: {detail}") from None
    except (urllib.error.URLError, OSError) as e:
        raise ToolError(f"cannot reach Studio at {base}: {e}") from None
    except ValueError:
        raise ToolError(f"Studio answered 200: {base}/api/projects is not JSON") from None
    if not isinstance(data, list):
        raise ToolError(f"Studio answered 200: {base}/api/projects is not a list")
    return data


def studio_name_of(listing: Sequence[Any], root: Path) -> str | None:
    """The name the Studio serves the project at `root` under: the first row whose real path
    equals it (by path, never by name: the hub's names and this server's may differ)."""
    want = os.path.realpath(root)
    for row in listing:
        if not isinstance(row, dict):
            continue
        name, where = row.get("name"), row.get("root")
        if isinstance(name, str) and isinstance(where, str) and os.path.realpath(where) == want:
            return name
    return None


class StudioReads:
    """A non-first project the Studio serves: its READ methods (model, lint, change records)
    come from ``<base>/p/<name>``; ask, new_change and check stay local, exactly as for a
    project the Studio does not serve (a proposal is never routed into another project's
    Studio queue by this server)."""

    kind = "http"

    def __init__(self, http: Any, local: Any) -> None:
        self.http = http
        self.local = local
        self.root = http.root
        self._own_changes: set[str] = set()  # ids new_change made here: the local ones

    @property
    def configured(self) -> bool:
        return self.local.configured

    def model(self) -> dict[str, Any]:
        return self.http.model()

    def lint(self) -> list[dict[str, Any]]:
        return self.http.lint()

    def get_change(self, cid: str) -> dict[str, Any]:
        return (self.local if cid in self._own_changes else self.http).get_change(cid)

    def ask(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self.local.ask(body)

    def new_change(self, body: Mapping[str, Any]) -> dict[str, Any]:
        out = self.local.new_change(body)
        if isinstance(out, Mapping) and isinstance(out.get("id"), str):
            self._own_changes.add(out["id"])
        return out

    def check(self, layers: str | None, timeout: float) -> dict[str, Any]:
        return self.local.check(layers, timeout)


def _walk_up_manifest(start: Path) -> Path | None:
    for d in (start, *start.parents):
        cand = d / "lattice.workspace.toml"
        if cand.is_file():
            return cand
    return None


def serve(
    project: Path | Sequence[Path] | None = None,
    *,
    readonly: bool = False,
    model: str | None = None,
    studio_url: str | None = None,
    workspace: Path | str | None = None,
    launch: Path | None = None,
) -> int:
    """Serve the tools over stdio. `project`: one directory, several, or none (the launch
    directory, `launch` or the cwd, plus discovery: lattice/mcp/projects.py). `workspace`: a
    ``lattice.workspace.toml`` (default: ``$LATTICE_WORKSPACE``, else found walking up from the
    launch directory, else, with a ``--project``, found walking up from the first project when
    that manifest loads and declares it). ``--studio-url`` proxies the first project's tools to
    that Studio; with several projects the others' READ tools go to the same Studio's
    ``/p/<name>`` when it serves them (matched by path; see :class:`StudioReads`)."""
    from lattice import workspace as wsmod
    from lattice.mcp.projects import ProjectRouter, known_projects

    explicit = [project] if isinstance(project, Path | str) else list(project or [])
    explicit = [Path(p).resolve() for p in explicit]
    for root in explicit:
        if not root.is_dir():
            print(f"lattice mcp: {root} is not a directory", file=sys.stderr)
            return 2
    here = (launch or Path.cwd()).resolve()
    ws_path = wsmod.find(start=here, explicit=workspace)
    ws, problems = wsmod.try_load(ws_path)
    if ws_path is None and explicit:
        # Nothing from the flag, the env or the launch directory: anchor at the project, so a
        # host that launches elsewhere still finds the manifest that declares it.
        near = _walk_up_manifest(explicit[0])
        if near is not None:
            found, found_problems = wsmod.try_load(near)
            if found is None or any(p.code == "unreadable" for p in found_problems):
                why = "it cannot be read: " + "; ".join(p.message for p in found_problems)
            elif found.by_path(explicit[0]) is None:
                why = f"it does not declare {explicit[0]}"
            else:
                ws_path, ws, problems = near, found, found_problems
                why = ""
            if why:
                print(
                    f"lattice mcp: workspace {near} (found from {explicit[0]}) not used: {why}",
                    file=sys.stderr,
                )
    for prob in problems:
        print(f"lattice mcp: workspace {ws_path}: {prob.severity}: {prob.message}", file=sys.stderr)
    known = known_projects(explicit, launch=here, workspace=ws)
    first = known[0].name
    base = studio_base(studio_url) if studio_url else None
    # Protocol on the real stdout; every other write to fd 1 (the app under check, child
    # processes) goes to stderr.
    proto_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    stdout = os.fdopen(proto_fd, "w", encoding="utf-8", buffering=1)

    def make_backend(p: Any) -> Any:
        if studio_url and p.name == first:
            return HttpBackend(studio_url, p.root)
        # A directory without a spec still starts (tools say what is missing and upgrade
        # themselves once the spec exists), so a user-level plugin never shows a failed server.
        local = PendingBackend(p.root, model=model)
        if base is None:
            return local
        # Another project behind --studio-url: when that Studio serves it (found by path), its
        # reads come from the Studio. A failure to ask is a tool error and is not remembered.
        listing = studio_listing(base)
        name = studio_name_of(listing, p.root) if listing is not None else None
        if name is None:
            print(f"lattice mcp: Studio at {base} does not serve {p.root}: local", file=sys.stderr)
            return local
        http = HttpBackend(f"{base}/p/{urllib.parse.quote(name, safe='')}", p.root)
        return StudioReads(http, local)

    router = ProjectRouter(
        known,
        make_backend,
        readonly=readonly,
        workspace=ws,
        workspace_path=ws_path,
        workspace_problems=problems,
        launch=here,
    )
    server = McpServer(router)
    try:
        server.run(sys.stdin, stdout)
    except (BrokenPipeError, KeyboardInterrupt):
        pass
    return 0
