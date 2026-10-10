"""Studio chat sessions: the chat panel runs on a real Claude Code session.

One user message spawns one ``claude -p ... --output-format stream-json`` subprocess
(``cwd`` = the project directory, environment = :func:`lattice.llm.cli_client.cli_env`, so
API credentials never reach it). The first turn's ``session_id`` is captured and passed
back with ``--resume`` on every later turn, so the conversation is one real Claude Code
session that the owner can also continue in a terminal (``claude --resume <id>``).

**Tools.** The model reaches the project only through the ``lattice`` MCP server
(``python -m lattice.mcp serve --project DIR``) and a few read-only built-ins; a
``PreToolUse`` hook (``python -m lattice.mcp.guard --project DIR``) vetoes edits and shell
commands as a second line behind ``--tools`` / ``--disallowedTools``. Both commands are a
fixed contract owned by ``lattice/mcp``. :data:`MODE_PROFILES` lists what each mode may
use:

========  ===============================================  ==========================
mode      tools                                            purpose
========  ===============================================  ==========================
ask       Read/Grep/Glob + model_get, rule_get, spec_ask,  questions about the spec
          code_links, graph_get, spec_lint (read-only)
explain   the same, a code-reading prompt                  how the app implements X
change    ask's tools + change_propose/get/list            propose a spec change
rebuild   (defined, 501 "not enabled yet")                 see POST /api/rebuild
========  ===============================================  ==========================

**Events** (the contract the UI codes against). Every event has ``seq`` (int from 1, never
reused) and ``ts`` (ISO, UTC):

- ``{type:"user", text, selected[]}``
- ``{type:"text", text, partial, message_id}``: streamed deltas are merged per
  (message_id, block) into one event that is replaced (with a fresh ``seq``) as it grows,
  so a poll sees the latest text, not fragments
- ``{type:"tool_use", id, name, label, input_summary}``
- ``{type:"tool_result", tool_use_id, ok, summary}``
- ``{type:"status", state:"starting"|"running"|"idle"|"interrupted"}``
- ``{type:"result", ok, text, cost_usd, duration_ms, input_tokens, output_tokens,
  num_turns, claude_session_id}``
- ``{type:"error", message, kind:"usage_limit"|"cli"|"timeout"|"other"}``

One turn runs at a time per session (409 otherwise). ``studio.toml`` ``[llm]
session_timeout`` (seconds, default 900) bounds a turn; ``claude_bin`` overrides the
binary (tests use a fake).
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lattice.llm.cli_client import _USAGE_LIMIT_RE, cli_env

__all__ = [
    "DEFAULT_SESSION_TIMEOUT",
    "MODE_PROFILES",
    "ModeProfile",
    "SessionError",
    "SessionManager",
    "Transcript",
    "build_argv",
    "claude_available",
    "parse_create",
    "parse_message",
    "tool_label",
]

DEFAULT_SESSION_TIMEOUT = 900.0
DEFAULT_MAX_TOKENS = 32000
KILL_GRACE = 3.0  # seconds between SIGTERM and SIGKILL of an interrupted turn
GUARD_MATCHER = "Edit|Write|MultiEdit|NotebookEdit|Bash"
LATTICE_ROOT = Path(__file__).resolve().parents[2]  # the directory holding the package

_READ_TOOLS = ("Read", "Grep", "Glob")
_MODEL_TOOLS = tuple(
    f"mcp__lattice__{n}"
    for n in ("model_get", "rule_get", "spec_ask", "code_links", "graph_get", "spec_lint")
)
_CHANGE_TOOLS = tuple(f"mcp__lattice__{n}" for n in ("change_propose", "change_get", "change_list"))
_DENIED = ("Edit", "Write", "MultiEdit", "NotebookEdit", "Bash")

_BASE_RULES = (
    "You are the chat of Lattice Studio, working on one Lattice project (the current "
    "directory): REQUIREMENTS.md (the owner's text), spec.py (the SPEC), spec.trace.json "
    "(rules, links, questions). Answer from the spec first (mcp__lattice__spec_ask, "
    "model_get, rule_get) and cite rule ids (R12) and targets (scenario:..., "
    "cell:row|GET /path). Say plainly when the spec does not say. Never edit files "
    "yourself and never run shell commands: you have no tools for it. Be brief."
)


@dataclass(frozen=True)
class ModeProfile:
    """What one chat mode may do: the tool allowlist (built-ins and MCP), whether the MCP
    server is read-only, whether the mode is enabled, and its prompt."""

    name: str
    builtin: tuple[str, ...]
    allowed: tuple[str, ...]
    disallowed: tuple[str, ...]
    readonly: bool
    enabled: bool
    prompt: str


MODE_PROFILES: dict[str, ModeProfile] = {
    "ask": ModeProfile(
        "ask",
        _READ_TOOLS,
        (*_READ_TOOLS, *_MODEL_TOOLS),
        _DENIED,
        True,
        True,
        "Mode: ask. Answer questions about what the spec says and decides. Read-only.",
    ),
    "explain": ModeProfile(
        "explain",
        _READ_TOOLS,
        (*_READ_TOOLS, *_MODEL_TOOLS),
        _DENIED,
        True,
        True,
        "Mode: explain. Explain how the app's code implements the selected rules, "
        "endpoints or tables: read the code (code_links first, then Read), say which "
        "file and function does what, and name any rule the code does not seem to honour.",
    ),
    "change": ModeProfile(
        "change",
        _READ_TOOLS,
        (*_READ_TOOLS, *_MODEL_TOOLS, *_CHANGE_TOOLS),
        _DENIED,
        False,
        True,
        "Mode: change. Turn the owner's request into a spec change with "
        "mcp__lattice__change_propose (one request in the owner's words). Check the "
        "current rules first, then report the change id and what it touches. Do not "
        "accept it: the owner reviews it in the Studio.",
    ),
    "rebuild": ModeProfile(
        "rebuild",
        _READ_TOOLS,
        (*_READ_TOOLS, *_MODEL_TOOLS),
        _DENIED,
        True,
        False,
        "Mode: rebuild.",
    ),
}

HTTP_UNAVAILABLE = 503  # the claude binary is missing
STATES = ("starting", "running", "idle", "interrupted")


class SessionError(Exception):
    """A session request refused: ``status`` is the HTTP status (404, 409, 422, 501)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def claude_available(claude_bin: str = "claude") -> bool:
    """Whether the ``claude`` binary can be run (found on PATH, or an executable path)."""
    if os.sep in claude_bin:
        return os.access(claude_bin, os.X_OK) and Path(claude_bin).is_file()
    return shutil.which(claude_bin) is not None


# ---- request validation (shared by the real and the mock server) ------------------------------


def parse_create(body: Mapping[str, Any]) -> tuple[str, str | None]:
    mode = body.get("mode")
    if not isinstance(mode, str) or mode not in MODE_PROFILES:
        raise SessionError(422, f"mode must be one of {', '.join(MODE_PROFILES)}")
    change = body.get("change")
    if change is not None and (not isinstance(change, str) or not change):
        raise SessionError(422, "change must be a change id")
    if not MODE_PROFILES[mode].enabled:
        raise SessionError(501, f"mode {mode} is not enabled yet")
    return mode, change


def parse_message(body: Mapping[str, Any]) -> tuple[str, list[str]]:
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise SessionError(422, "text is empty")
    selected = body.get("selected", [])
    if selected is None:
        selected = []
    if not isinstance(selected, list) or not all(isinstance(s, str) and s for s in selected):
        raise SessionError(422, "selected must be a list of targets")
    return text, selected


# ---- labels -----------------------------------------------------------------------------------

_MCP_LABELS = {
    "spec_ask": "Asked the spec",
    "model_get": "Read the model",
    "rule_get": "Read a rule",
    "code_links": "Looked up code links",
    "graph_get": "Read the graph",
    "spec_lint": "Linted the spec",
    "change_propose": "Proposed a change",
    "change_get": "Read a change",
    "change_list": "Listed the changes",
}


def _short(text: str, n: int = 120) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def tool_label(name: str, tool_input: Mapping[str, Any] | None) -> str:
    """A human label of one tool call ("Read runs.go", "Asked the spec")."""
    inp = tool_input or {}
    if name == "Read":
        return "Read " + (Path(str(inp.get("file_path") or "a file")).name or "a file")
    if name == "Grep":
        # quoted by hand: !r would double every backslash of a regex ("\\(a \\*App")
        return "Searched for '" + _short(str(inp.get("pattern") or ""), 40) + "'"
    if name == "Glob":
        return f"Listed {_short(str(inp.get('pattern') or 'files'), 40)}"
    if name.startswith("mcp__lattice__"):
        short = name.removeprefix("mcp__lattice__")
        label = _MCP_LABELS.get(short, short.replace("_", " ").capitalize())
        ident = inp.get("id") or inp.get("rule") or inp.get("rule_id") or inp.get("target")
        if short in ("rule_get", "change_get") and ident:
            label = label.replace("a rule", str(ident)).replace("a change", str(ident))
        return label
    return name.removeprefix("mcp__")


def _input_summary(name: str, tool_input: Mapping[str, Any] | None) -> str:
    inp = tool_input or {}
    if name == "Read":
        return _short(str(inp.get("file_path") or ""))
    if name in ("Grep", "Glob"):
        return _short(str(inp.get("pattern") or ""))
    for key in ("question", "request", "id", "rule", "target", "text"):
        if inp.get(key):
            return _short(str(inp[key]))
    try:
        return _short(json.dumps(inp, ensure_ascii=False, sort_keys=True))
    except (TypeError, ValueError):
        return _short(str(inp))


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return "" if content is None else str(content)


def error_kind(text: str) -> str:
    return "usage_limit" if _USAGE_LIMIT_RE.search(text) else "cli"


# ---- the transcript: a stream-json normalizer over an event log -------------------------------


class Transcript:
    """The normalized event log of one session, fed with Claude Code stream-json records.
    Thread safe. Used by the real :class:`Session` and by the mock server's replay."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.events: list[dict[str, Any]] = []
        self.seq = 0
        self.state = "idle"
        self.claude_session_id: str | None = None
        self.cost_total = 0.0
        self.turns = 0
        self._text_events: dict[tuple[str, int], dict[str, Any]] = {}
        self._tool_ids: set[str] = set()
        self._msg_id = ""
        self._turn_texts = 0
        self._anon = 0

    # -- the log
    def _add(self, event: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self.seq += 1
            event = {"seq": self.seq, "ts": _now(), **event}
            self.events.append(event)
            return event

    def _replace(self, old: dict[str, Any], **fields: Any) -> dict[str, Any]:
        """`old` with `fields` changed, under a fresh seq at the end of the log."""
        with self.lock:
            try:
                self.events.remove(old)
            except ValueError:
                pass
            self.seq += 1
            new = {**old, **fields, "seq": self.seq, "ts": _now()}
            self.events.append(new)
            for key, ev in list(self._text_events.items()):
                if ev is old:
                    self._text_events[key] = new
            return new

    def events_after(self, after: int) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(e) for e in self.events if e["seq"] > after]

    # -- turn lifecycle
    def add_user(self, text: str, selected: Sequence[str]) -> None:
        with self.lock:
            self.turns += 1
            self._turn_texts = 0
            self._add({"type": "user", "text": text, "selected": list(selected)})

    def set_state(self, state: str) -> None:
        with self.lock:
            if state == self.state and state != "starting":
                return
            self.state = state
            self._add({"type": "status", "state": state})

    def error(self, message: str, kind: str = "other") -> None:
        self._add({"type": "error", "message": message, "kind": kind})

    # -- stream-json records
    def feed(self, rec: Mapping[str, Any]) -> None:
        """Normalize one stream-json record into zero or more events."""
        kind = rec.get("type")
        with self.lock:
            if kind == "system":
                if rec.get("subtype") == "init":
                    sid = rec.get("session_id")
                    if isinstance(sid, str) and sid:
                        self.claude_session_id = sid
                    self.set_state("running")
            elif rec.get("parent_tool_use_id"):
                return  # a subagent's own chatter
            elif kind == "stream_event":
                self._stream_event(rec.get("event") or {})
            elif kind == "assistant":
                self._assistant(rec.get("message") or {})
            elif kind == "user":
                self._user(rec.get("message") or {})
            elif kind == "result":
                self._result(rec)

    def _stream_event(self, ev: Mapping[str, Any]) -> None:
        et = ev.get("type")
        if et == "message_start":
            self._msg_id = str((ev.get("message") or {}).get("id") or "")
            return
        idx = int(ev.get("index") or 0)
        key = (self._msg_id, idx)
        if et == "content_block_start":
            block = ev.get("content_block") or {}
            if block.get("type") == "text":
                self._text_events[key] = self._add(
                    {
                        "type": "text",
                        "text": str(block.get("text") or ""),
                        "partial": True,
                        "message_id": self._msg_id,
                    }
                )
        elif et == "content_block_delta":
            delta = ev.get("delta") or {}
            cur = self._text_events.get(key)
            if delta.get("type") == "text_delta":
                if cur is None:
                    cur = self._text_events[key] = self._add(
                        {"type": "text", "text": "", "partial": True, "message_id": self._msg_id}
                    )
                self._replace(cur, text=cur["text"] + str(delta.get("text") or ""))
        elif et == "content_block_stop":
            cur = self._text_events.get(key)
            if cur is not None and cur.get("partial"):
                self._replace(cur, partial=False)

    def _assistant(self, msg: Mapping[str, Any]) -> None:
        mid = str(msg.get("id") or "")
        for block in msg.get("content") or ():
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                self._final_text(mid, str(block.get("text") or ""))
            elif block.get("type") == "tool_use":
                tid = str(block.get("id") or "")
                if tid in self._tool_ids:
                    continue
                self._tool_ids.add(tid)
                name = str(block.get("name") or "")
                inp = block.get("input") if isinstance(block.get("input"), dict) else {}
                self._add(
                    {
                        "type": "tool_use",
                        "id": tid,
                        "name": name,
                        "label": tool_label(name, inp),
                        "input_summary": _input_summary(name, inp),
                    }
                )

    def _final_text(self, mid: str, text: str) -> None:
        if not text:
            return
        mine = [e for e in self._text_events.values() if e["message_id"] == mid]
        if any(e["text"] == text and not e["partial"] for e in mine):
            return
        for e in mine:
            if e["partial"] and text.startswith(e["text"]):
                self._replace(e, text=text, partial=False)
                return
        self._anon += 1
        self._text_events[(mid, 1000 + self._anon)] = self._add(
            {"type": "text", "text": text, "partial": False, "message_id": mid}
        )

    def _user(self, msg: Mapping[str, Any]) -> None:
        content = msg.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                self._add(
                    {
                        "type": "tool_result",
                        "tool_use_id": str(block.get("tool_use_id") or ""),
                        "ok": not block.get("is_error"),
                        "summary": _short(_content_text(block.get("content")), 200),
                    }
                )

    def _result(self, rec: Mapping[str, Any]) -> None:
        sid = rec.get("session_id")
        if isinstance(sid, str) and sid:
            self.claude_session_id = sid
        text = str(rec.get("result") or "")
        is_error = bool(rec.get("is_error")) or str(rec.get("subtype") or "").startswith("error")
        if is_error:
            self._add(
                {"type": "error", "message": _short(text or "claude failed", 500),
                 "kind": error_kind(text)}
            )  # fmt: skip
        elif not any(e["type"] == "text" for e in self.events[self._last_user_index() :]) and text:
            self._add({"type": "text", "text": text, "partial": False, "message_id": ""})
        usage = rec.get("usage") if isinstance(rec.get("usage"), dict) else {}
        cost = rec.get("total_cost_usd")
        cost = float(cost) if isinstance(cost, int | float) else 0.0
        self.cost_total += cost
        self._add(
            {
                "type": "result",
                "ok": not is_error,
                "text": text,
                "cost_usd": cost,
                "duration_ms": int(rec.get("duration_ms") or 0),
                "input_tokens": int(usage.get("input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
                "num_turns": int(rec.get("num_turns") or 0),
                "claude_session_id": self.claude_session_id,
            }
        )

    def _last_user_index(self) -> int:
        for i in range(len(self.events) - 1, -1, -1):
            if self.events[i]["type"] == "user":
                return i
        return 0

    @property
    def saw_result(self) -> bool:
        with self.lock:
            return any(e["type"] == "result" for e in self.events[self._last_user_index() :])

    def view(self, after: int = 0) -> dict[str, Any]:
        """The ``GET .../events`` answer."""
        with self.lock:
            return {
                "events": self.events_after(after),
                "next": self.seq,
                "state": self.state,
                "done": self.state not in ("starting", "running"),
                "claude_session_id": self.claude_session_id,
                "resume_command": (
                    f"claude --resume {self.claude_session_id}" if self.claude_session_id else None
                ),
                "cost_usd_total": round(self.cost_total, 6),
            }


# ---- argv -------------------------------------------------------------------------------------


def project_rules(project: Path) -> str:
    """The project's own chat rules: ``STUDIO_RULES.md`` in the project directory, if any."""
    path = project / "STUDIO_RULES.md"
    try:
        return path.read_text(encoding="utf-8")[:4000] if path.is_file() else ""
    except OSError:
        return ""


def system_prompt(
    mode: str, project: Path, selected: Sequence[str] = (), change: str | None = None
) -> str:
    parts = [_BASE_RULES, MODE_PROFILES[mode].prompt]
    if change:
        parts.append(f"The open change is {change}.")
    rules = project_rules(project)
    if rules.strip():
        parts.append("Project rules:\n" + rules.strip())
    if selected:
        parts.append(
            "The owner has selected these targets in the Studio; the message is about them: "
            + ", ".join(selected)
        )
    return "\n\n".join(parts)


def write_configs(
    directory: Path, mode: str, project: Path, python: str, studio_url: str | None = None
) -> tuple[Path, Path]:
    """The ``--mcp-config`` and ``--settings`` JSON files of a session, in `directory`."""
    prof = MODE_PROFILES[mode]
    args = ["-m", "lattice.mcp", "serve", "--project", str(project)]
    if prof.readonly:
        args.append("--readonly")
    if studio_url:
        # change sets live in the Studio server's memory: the MCP process talks to it
        args += ["--studio-url", studio_url]
    mcp = {"mcpServers": {"lattice": {"command": python, "args": args}}}
    guard = f"{shlex.quote(python)} -m lattice.mcp.guard --project {shlex.quote(str(project))}"
    settings = {
        "hooks": {
            "PreToolUse": [
                {"matcher": GUARD_MATCHER, "hooks": [{"type": "command", "command": guard}]}
            ]
        }
    }
    directory.mkdir(parents=True, exist_ok=True)
    mcp_path, settings_path = directory / "mcp.json", directory / "settings.json"
    mcp_path.write_text(json.dumps(mcp, indent=2), encoding="utf-8")
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return mcp_path, settings_path


def build_argv(
    *,
    claude_bin: str,
    mode: str,
    text: str,
    model: str,
    mcp_config: Path,
    settings: Path,
    prompt: str,
    resume: str | None = None,
) -> list[str]:
    """The ``claude`` argv of one turn. The prompt text goes last behind ``--`` because
    ``--allowedTools`` and ``--mcp-config`` are variadic."""
    prof = MODE_PROFILES[mode]
    argv = [
        claude_bin,
        "-p",
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
    ]  # fmt: skip
    if resume:
        argv += ["--resume", resume]
    argv += [
        # only --settings applies: the owner's user/project settings (their plugins, hooks
        # and skills, e.g. a SessionStart hook injecting pages of context) stay out
        "--setting-sources", "", "--disable-slash-commands",
        "--strict-mcp-config", "--mcp-config", str(mcp_config),
        "--settings", str(settings),
        "--tools", ",".join(prof.builtin),
        "--permission-mode", "dontAsk",
        "--allowedTools", ",".join(prof.allowed),
        "--disallowedTools", ",".join(prof.disallowed),
        "--append-system-prompt", prompt,
        "--model", model,
        "--", text,
    ]  # fmt: skip
    return argv


# ---- sessions ---------------------------------------------------------------------------------


class Session:
    """One chat: a transcript, its mode, and the (at most one) running turn."""

    def __init__(self, sid: str, mode: str, project: Path, change: str | None, tmp: Path) -> None:
        self.id = sid
        self.mode = mode
        self.project = project
        self.change = change
        self.tmp = tmp
        self.transcript = Transcript()
        self.lock = threading.Lock()  # guards proc / interrupted / the running check
        self.proc: subprocess.Popen[str] | None = None
        self.interrupted = False
        self.timed_out = False
        self.created = _now()

    @property
    def state(self) -> str:
        return self.transcript.state

    @property
    def running(self) -> bool:
        return self.state in ("starting", "running")


class SessionManager:
    """Every chat session of one server. Lazy: nothing is spawned until a message."""

    def __init__(
        self,
        project: Path,
        *,
        model: str = "sonnet",
        claude_bin: str = "claude",
        timeout: float = DEFAULT_SESSION_TIMEOUT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        env: Mapping[str, str] | None = None,
        python: str | None = None,
        studio_url: str | None = None,
    ) -> None:
        self.project = Path(project).resolve()
        self.model = model
        self.claude_bin = claude_bin
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._env = env
        self.python = python or sys.executable
        self.studio_url = studio_url  # the server's own base URL, for --studio-url
        self.sessions: dict[str, Session] = {}
        self.lock = threading.Lock()
        self._tmp_root: Path | None = None
        self.last_argv: dict[str, list[str]] = {}

    # -- public API (each raises SessionError)
    def available(self) -> bool:
        return claude_available(self.claude_bin)

    def create(self, body: Mapping[str, Any]) -> dict[str, Any]:
        mode, change = parse_create(body)
        with self.lock:
            if self._tmp_root is None:
                self._tmp_root = Path(tempfile.mkdtemp(prefix="lattice-studio-session-"))
            sid = "s" + uuid.uuid4().hex[:10]
            tmp = self._tmp_root / sid
            write_configs(tmp, mode, self.project, self.python, self.studio_url)
            self.sessions[sid] = Session(sid, mode, self.project, change, tmp)
        return self._summary(self.sessions[sid])

    def _get(self, sid: str) -> Session:
        with self.lock:
            s = self.sessions.get(sid)
        if s is None:
            raise SessionError(404, f"no session {sid}")
        return s

    def _summary(self, s: Session) -> dict[str, Any]:
        t = s.transcript
        return {
            "id": s.id,
            "mode": s.mode,
            "change": s.change,
            "state": t.state,
            "claude_session_id": t.claude_session_id,
            "resume_command": (
                f"claude --resume {t.claude_session_id}" if t.claude_session_id else None
            ),
            "cwd": str(s.project),
            "turns": t.turns,
            "cost_usd_total": round(t.cost_total, 6),
            "created": s.created,
        }

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            return [self._summary(s) for s in self.sessions.values()]

    def message(self, sid: str, body: Mapping[str, Any]) -> dict[str, Any]:
        text, selected = parse_message(body)
        s = self._get(sid)
        with s.lock:
            if s.running:
                raise SessionError(409, "a turn is already running in this session")
            if not self.available():
                raise SessionError(
                    HTTP_UNAVAILABLE, f"the claude binary ({self.claude_bin}) was not found"
                )
            s.interrupted = s.timed_out = False
            s.transcript.add_user(text, selected)
            s.transcript.set_state("starting")
            resume = s.transcript.claude_session_id
            turn = s.transcript.turns
            prompt = system_prompt(s.mode, s.project, selected, s.change)
            argv = build_argv(
                claude_bin=self.claude_bin,
                mode=s.mode,
                text=text,
                model=self.model,
                mcp_config=s.tmp / "mcp.json",
                settings=s.tmp / "settings.json",
                prompt=prompt,
                resume=resume,
            )
            self.last_argv[sid] = argv
            threading.Thread(
                target=self._run_turn, args=(s, argv), daemon=True, name=f"studio-session-{sid}"
            ).start()
        return {"turn": turn}

    def events(self, sid: str, after: int = 0) -> dict[str, Any]:
        return self._get(sid).transcript.view(after)

    def interrupt(self, sid: str) -> dict[str, Any]:
        s = self._get(sid)
        with s.lock:
            if not s.running:
                raise SessionError(409, "no turn is running in this session")
            s.interrupted = True
            proc = s.proc
        if proc is not None:
            _kill_group(proc, signal.SIGTERM)
        return {"id": sid, "state": "interrupted"}

    def close(self) -> None:
        """Stop every running turn and remove the temp files."""
        with self.lock:
            sessions = list(self.sessions.values())
            root, self._tmp_root = self._tmp_root, None
        for s in sessions:
            s.interrupted = True
            if s.proc is not None:
                _kill_group(s.proc, signal.SIGKILL)
        if root is not None:
            shutil.rmtree(root, ignore_errors=True)

    # -- one turn
    def _child_env(self) -> dict[str, str]:
        base = self._env if self._env is not None else os.environ
        env = cli_env(base, self.max_tokens)
        # the MCP server and the hook guard are started by `claude` as `python -m lattice...`
        # from the project directory: make the package importable wherever it lives.
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in (str(LATTICE_ROOT), env.get("PYTHONPATH", "")) if p
        )
        return env

    def _run_turn(self, s: Session, argv: list[str]) -> None:
        t = s.transcript
        try:
            self._run_turn_inner(s, argv)
        except Exception as exc:  # noqa: BLE001 -- the turn must always end
            t.error(f"{type(exc).__name__}: {exc}", "other")
        finally:
            with s.lock:
                s.proc = None
                interrupted = s.interrupted
            t.set_state("interrupted" if interrupted else "idle")

    def _run_turn_inner(self, s: Session, argv: list[str]) -> None:
        t = s.transcript
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(s.project),
                env=self._child_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            t.error(f"could not start {self.claude_bin}: {exc}", "cli")
            return
        with s.lock:
            s.proc = proc
            if s.interrupted:  # interrupted between start and spawn
                _kill_group(proc, signal.SIGTERM)
        stderr_lines: list[str] = []
        junk: list[str] = []

        def drain() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                stderr_lines.append(line)

        threading.Thread(target=drain, daemon=True).start()

        def on_timeout() -> None:
            s.timed_out = True
            _kill_group(proc, signal.SIGTERM)

        timer = threading.Timer(self.timeout, on_timeout)
        timer.daemon = True
        timer.start()
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    junk.append(line)
                    continue
                if isinstance(rec, dict):
                    t.feed(rec)
            rc = proc.wait()
        finally:
            timer.cancel()
            if proc.poll() is None:
                _kill_group(proc, signal.SIGKILL)
        time.sleep(0)  # let the stderr drainer finish its last line
        if s.interrupted:
            return
        if s.timed_out:
            t.error(f"the turn did not finish within {self.timeout:g}s", "timeout")
            return
        if not t.saw_result:
            tail = "".join(stderr_lines).strip() or "\n".join(junk).strip()
            message = _short(tail, 500) or f"claude exited with status {rc}"
            t.error(message, error_kind(tail))


def _kill_group(proc: subprocess.Popen[str], sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        return
    if sig == signal.SIGTERM:

        def escalate() -> None:
            time.sleep(KILL_GRACE)
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass

        threading.Thread(target=escalate, daemon=True).start()
