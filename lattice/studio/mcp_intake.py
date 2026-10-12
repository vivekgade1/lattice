"""Connected MCP servers in Studio chat sessions (API.md v1.13, spike 4).

A chat session (``ask``, ``explain``, ``change``) runs ``claude -p`` with ``--setting-sources ""``
and the generated ``--mcp-config`` but **without** ``--strict-mcp-config``: it sees Studio's own
``lattice`` server plus the claude.ai connectors of the owner's account (Docs, Gmail, Drive, ...).
The owner's plugins, skills, hooks and plugin MCP servers (GitHub, Atlassian, ...) stay out: they
need the ``user`` setting source, which also brings back the SessionStart hook (a documented
limitation, docs/results/2026-10-12-k-r-mcp-visibility.md). ``rebuild`` and the planner keep
``--strict-mcp-config``: build runs see no external server.

**Which external tools run.** Sessions run under ``--permission-mode dontAsk``: only
``--allowedTools`` entries run. :func:`classify_tool` sorts a tool by the first word of its name
after ``mcp__<server>__``: a read verb is ``read``, a write verb is ``write``, anything else is
``unknown`` (treated as write). A tool whose first word is a read verb but whose name holds a
write verb anywhere (``get_or_create_issue``, ``get_draft``) is ``write``: the rule errs towards
denying. Every mode may use the **read** tools of connected servers; write and unknown tools are
put on ``--disallowedTools`` (never offered) unless the server is listed in ``studio.toml``::

    [mcp]
    allow_writes = ["claude.ai Linear"]   # default: none; in change mode only

``lattice`` server tools keep their per-mode lists (session.MODE_PROFILES).

**Discovery.** :func:`discover` runs ``claude -p --output-format stream-json --verbose
--max-turns 1 --setting-sources "" --mcp-config <cfg> -- "Reply ok. Do not call any tool."`` and
reads its ``system``/``init`` record (server names, status, source, tool NAMES; never
descriptions or secrets); the process is stopped as soon as that record has arrived. Results are
cached in memory for :data:`CACHE_TTL` seconds per (binary, cwd, config); ``refresh`` forces a
new discovery. A failure (no binary, timeout, no init record) is cached too and gives no external
tool: sessions fail closed.

**Connectors connect late.** Measured 2026-10-12 (Claude Code 2.1.296): without help, the init
record of a ``-p`` run often lists the claude.ai connectors as ``pending`` with no tools, or not
at all (their list had not been fetched yet). The discovery and the chat sessions therefore run
with ``MCP_CONNECTION_NONBLOCKING=false`` (unless set), and the discovery tries up to
:data:`DISCOVERY_ATTEMPTS` times while a server is ``pending`` or no server is listed, keeping
the most complete answer (docs/results/2026-10-12-spike4-results.md).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lattice.llm.cli_client import cli_env

__all__ = [
    "CACHE_TTL",
    "CHAT_MODES",
    "EMPTY_CONFIG",
    "READ_VERBS",
    "WRITE_VERBS",
    "McpIntake",
    "SourceError",
    "classify_tool",
    "discover",
    "discovery_argv",
    "external_tools",
    "parse_source",
    "stub_view",
]

CACHE_TTL = 300.0  # seconds a discovery is reused
DISCOVERY_TIMEOUT = 30.0
DISCOVERY_PROMPT = "Reply ok. Do not call any tool."
EMPTY_CONFIG = '{"mcpServers":{}}'
CHAT_MODES = ("ask", "explain", "change")
DISCOVERY_ATTEMPTS = 3
# make `claude -p` wait for MCP servers (the claude.ai connectors) before its first turn
CONNECT_ENV = {"MCP_CONNECTION_NONBLOCKING": "false"}
LATTICE_SERVER = "lattice"  # Studio's own server: its tools follow MODE_PROFILES

READ_VERBS = frozenset(
    "get list search read fetch find query describe view retrieve lookup show export".split()
)
WRITE_VERBS = frozenset(
    "create update delete send post write move trash untrash label unlabel forward reply draft "
    "apply submit add remove set edit merge close assign comment upload publish".split()
)

STUB_NOTE = (
    "The stub LLM backend (test only) runs no Claude Code session, so no connected MCP server "
    "is discovered or used."
)


# ---- classification -------------------------------------------------------------------------


def _words(tool: str) -> list[str]:
    """``search_threads`` -> [search, threads]; ``getJiraIssue`` -> [get, jira, issue]."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", tool)
    return [w.lower() for w in re.split(r"[^A-Za-z0-9]+", spaced) if w]


def tool_part(name: str) -> str:
    """The tool's own name: the part after ``mcp__<server>__`` (the name itself otherwise)."""
    if name.startswith("mcp__"):
        _server, sep, rest = name.removeprefix("mcp__").partition("__")
        if sep and rest:
            return rest
    return name


def classify_tool(name: str) -> str:
    """``read``, ``write`` or ``unknown`` (unknown is treated as write: denied)."""
    words = _words(tool_part(name))
    if not words:
        return "unknown"
    if words[0] in WRITE_VERBS or any(w in WRITE_VERBS for w in words):
        return "write"
    if words[0] in READ_VERBS:
        return "read"
    return "unknown"


def server_prefix(server: str) -> str:
    """The tool-name prefix Claude Code gives a server: ``claude.ai Gmail`` ->
    ``mcp__claude_ai_Gmail__``."""
    return "mcp__" + re.sub(r"[^A-Za-z0-9_-]", "_", server) + "__"


# ---- discovery ------------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def discovery_argv(claude_bin: str, mcp_config: str | Path) -> list[str]:
    return [
        claude_bin, "-p", "--output-format", "stream-json", "--verbose", "--max-turns", "1",
        "--setting-sources", "", "--disable-slash-commands", "--no-session-persistence",
        "--permission-mode", "dontAsk", "--model", "haiku",
        "--mcp-config", str(mcp_config),
        "--", DISCOVERY_PROMPT,
    ]  # fmt: skip


def parse_init(rec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The servers of one ``system``/``init`` record: name, status, source, tools sorted by
    class. Names only. ``lattice`` (Studio's own server) is left out."""
    servers = [s for s in rec.get("mcp_servers") or [] if isinstance(s, Mapping)]
    tools = [t for t in rec.get("tools") or [] if isinstance(t, str) and t.startswith("mcp__")]
    prefixes = sorted(
        ((server_prefix(str(s.get("name") or "")), s) for s in servers),
        key=lambda p: -len(p[0]),
    )
    owned: dict[int, list[str]] = {id(s): [] for s in servers}
    for t in tools:
        for pre, s in prefixes:  # the longest prefix wins ("a" vs "a_b")
            if t.startswith(pre):
                owned[id(s)].append(t)
                break
    out = []
    for s in servers:
        name = str(s.get("name") or "")
        if not name or name == LATTICE_SERVER:
            continue
        by: dict[str, list[str]] = {"read": [], "write": [], "unknown": []}
        for t in sorted(owned[id(s)]):
            by[classify_tool(t)].append(t)
        out.append({"name": name, "status": str(s.get("status") or "unknown"),
                    "source": str(s.get("source") or ""), "tools": by})  # fmt: skip
    return out


_CACHE: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()
_KEY_LOCKS: dict[tuple[str, str, str], threading.Lock] = {}


def _binary_ok(claude_bin: str) -> bool:
    if os.sep in claude_bin:
        return os.access(claude_bin, os.X_OK) and Path(claude_bin).is_file()
    return shutil.which(claude_bin) is not None


def _stop(proc: subprocess.Popen[str]) -> None:
    """End the discovery's process group (claude and the MCP servers it started)."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            break
        try:
            proc.wait(timeout=3)
            break
        except subprocess.TimeoutExpired:
            continue
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass


def discovery_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The discovery's environment: no API credentials, connectors waited for."""
    out = cli_env(env if env is not None else os.environ, None)
    for k, v in CONNECT_ENV.items():
        out.setdefault(k, v)
    return out


def _completeness(d: Mapping[str, Any]) -> tuple[int, int, int, int]:
    servers = d.get("servers") or []
    done = sum(1 for s in servers if s.get("status") != "pending")
    tools = sum(len(v) for s in servers for v in (s.get("tools") or {}).values())
    return (0 if d.get("error") else 1, done, tools, len(servers))


def _settled(d: Mapping[str, Any]) -> bool:
    servers = d.get("servers") or []
    return bool(d.get("error")) or (
        bool(servers) and not any(s.get("status") == "pending" for s in servers)
    )


def _run_discovery(
    claude_bin: str, cwd: Path, mcp_config: str | Path, timeout: float,
    env: Mapping[str, str] | None,
) -> dict[str, Any]:  # fmt: skip
    if not _binary_ok(claude_bin):
        return {"servers": [], "refreshed_at": _now(),
                "error": f"the claude binary ({claude_bin}) was not found"}  # fmt: skip
    try:
        proc = subprocess.Popen(
            discovery_argv(claude_bin, mcp_config),
            cwd=str(cwd),
            env=discovery_env(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        return {"servers": [], "refreshed_at": _now(), "error": f"could not start: {exc}"}
    found: list[dict[str, Any]] = []
    timer = threading.Timer(timeout, lambda: _stop(proc))
    timer.daemon = True
    timer.start()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(rec, dict)
                and rec.get("type") == "system"
                and (rec.get("subtype") == "init")
            ):
                found.append(rec)
                break  # all we need: stop before the model answers
    finally:
        timer.cancel()
        _stop(proc)
    if not found:
        why = "no init record (the claude process ended or timed out first)"
        return {"servers": [], "refreshed_at": _now(), "error": why}
    return {"servers": parse_init(found[0]), "refreshed_at": _now()}


def discover(
    claude_bin: str,
    cwd: str | Path,
    mcp_config: str | Path = EMPTY_CONFIG,
    *,
    refresh: bool = False,
    timeout: float = DISCOVERY_TIMEOUT,
    env: Mapping[str, str] | None = None,
    ttl: float = CACHE_TTL,
) -> dict[str, Any]:
    """``{servers: [{name, status, source, tools: {read, write, unknown}}], refreshed_at}``
    (plus ``error`` when the discovery failed), cached for `ttl` seconds."""
    key = (claude_bin, str(Path(cwd).resolve()), str(mcp_config))
    with _CACHE_LOCK:
        lock = _KEY_LOCKS.setdefault(key, threading.Lock())
    with lock:  # one discovery per key at a time; the others wait for its answer
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
        if hit is not None and not refresh and time.monotonic() - hit[0] < ttl:
            return json.loads(json.dumps(hit[1]))
        out = _run_discovery(claude_bin, Path(cwd), mcp_config, timeout, env)
        for _ in range(DISCOVERY_ATTEMPTS - 1):  # connectors may still be connecting
            if _settled(out):
                break
            again = _run_discovery(claude_bin, Path(cwd), mcp_config, timeout, env)
            if _completeness(again) > _completeness(out):
                out = again
        with _CACHE_LOCK:
            _CACHE[key] = (time.monotonic(), out)
        return json.loads(json.dumps(out))


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


# ---- the allowlist --------------------------------------------------------------------------


def _writes_ok(server: Mapping[str, Any], allow_writes: Iterable[str]) -> bool:
    names = set(allow_writes)
    name = str(server.get("name") or "")
    return name in names or server_prefix(name)[5:-2] in names


def external_tools(
    servers: Sequence[Mapping[str, Any]], mode: str, allow_writes: Iterable[str] = ()
) -> tuple[list[str], list[str]]:
    """(allowed, denied) external tool names for `mode`: the read tools of connected servers
    are allowed in every chat mode; a server in `allow_writes` also gets its write and
    unknown tools in ``change``; every other external tool is denied."""
    allow_writes = list(allow_writes)
    allowed: list[str] = []
    denied: list[str] = []
    for s in servers:
        tools = s.get("tools") or {}
        reads = list(tools.get("read") or [])
        others = [*(tools.get("write") or []), *(tools.get("unknown") or [])]
        connected = s.get("status") == "connected"
        if mode in CHAT_MODES and connected:
            allowed += reads
        else:
            denied += reads
        if mode == "change" and connected and _writes_ok(s, allow_writes):
            allowed += others
        else:
            denied += others
    return allowed, denied


def stub_view() -> dict[str, Any]:
    return {"servers": [], "refreshed_at": None, "allowed_in": dict.fromkeys(CHAT_MODES, 0),
            "note": STUB_NOTE}  # fmt: skip


class McpIntake:
    """The connected servers of one project: discovery (cached), the per-mode allowlist and
    the ``GET /api/mcp`` answer."""

    def __init__(
        self,
        claude_bin: str,
        cwd: str | Path,
        *,
        allow_writes: Iterable[str] = (),
        mcp_config: str | Path = EMPTY_CONFIG,
        timeout: float = DISCOVERY_TIMEOUT,
        env: Mapping[str, str] | None = None,
        discover_fn: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.claude_bin = claude_bin
        self.cwd = Path(cwd).resolve()
        self.allow_writes = [str(s) for s in allow_writes]
        self.mcp_config = mcp_config
        self.timeout = timeout
        self.env = env
        self._discover = discover_fn or discover

    def discovery(self, refresh: bool = False) -> dict[str, Any]:
        return self._discover(self.claude_bin, self.cwd, self.mcp_config, refresh=refresh,
                              timeout=self.timeout, env=self.env)  # fmt: skip

    def tools_for(self, mode: str) -> tuple[list[str], list[str]]:
        return external_tools(self.discovery()["servers"], mode, self.allow_writes)

    def view(self, refresh: bool = False) -> dict[str, Any]:
        d = self.discovery(refresh)
        servers = []
        total = dict.fromkeys(CHAT_MODES, 0)
        for s in d["servers"]:
            per = {m: len(external_tools([s], m, self.allow_writes)[0]) for m in CHAT_MODES}
            for m in CHAT_MODES:
                total[m] += per[m]
            servers.append({**s, "allowed_in": per,
                            "writes_allowed": _writes_ok(s, self.allow_writes)})  # fmt: skip
        out: dict[str, Any] = {"servers": servers, "refreshed_at": d.get("refreshed_at"),
                               "allowed_in": total}  # fmt: skip
        if d.get("error"):
            out["error"] = d["error"]
        return out


# ---- provenance -----------------------------------------------------------------------------

SOURCE_FIELDS = {"server": 120, "item": 200, "url": 500, "title": 300}  # field -> max length
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class SourceError(ValueError):
    """An invalid ``source`` (the message is safe to show)."""


def parse_source(value: Any) -> dict[str, str] | None:
    """A change's ``source`` (``{server, item, url?, title?}``, strings, length-capped), or
    None when absent. Raises :class:`SourceError`."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise SourceError("source must be an object {server, item, url?, title?}")
    unknown = sorted(set(value) - set(SOURCE_FIELDS))
    if unknown:
        raise SourceError(f"source: unknown field(s): {', '.join(map(str, unknown))}")
    out: dict[str, str] = {}
    for key, cap in SOURCE_FIELDS.items():
        v = value.get(key)
        if v is None or (v == "" and key in ("url", "title")):
            if key in ("server", "item"):
                raise SourceError(f"source.{key} is required")
            continue
        if not isinstance(v, str):
            raise SourceError(f"source.{key} must be a string")
        v = v.strip()
        if not v and key in ("server", "item"):
            raise SourceError(f"source.{key} is required")
        if len(v) > cap:
            raise SourceError(f"source.{key} is longer than {cap} characters")
        if _CONTROL.search(v):
            raise SourceError(f"source.{key} holds control characters")
        if key == "url" and not re.match(r"https?://", v, re.I):
            raise SourceError("source.url must start with http:// or https://")
        if v:
            out[key] = v
    return out
