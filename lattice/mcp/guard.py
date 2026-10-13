"""A Claude Code ``PreToolUse`` hook that keeps the agent's hands off the spec files.

Wire it in ``.claude/settings.json``::

    {"hooks": {"PreToolUse": [{
        "matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash",
        "hooks": [{"type": "command",
                   "command": "python -m lattice.mcp.guard --project /path/to/project"}]}]}}

It reads the hook's JSON from stdin (``tool_name``, ``tool_input``, ``cwd``, ...). The
protected files are a project's ``REQUIREMENTS.md``, ``spec.py`` and ``spec.trace.json``
(plus names under ``[mcp] protected = [...]`` in ``studio.toml``, relative to the project).

**Many projects** (layer 1 of the multi-project phase): ``--project`` is optional and may be
repeated. Each ``--project`` that has a drafted spec is protected as before; on top of that,
every path the hook sees is checked against its *owning* project: the nearest directory holding
``spec.trace.json``, walking up from the path's real location (the walk stops at the
filesystem root, or at a directory holding ``.git`` that has no ``spec.trace.json``: a project
never owns files outside its repository). A path with no owning project is allowed. Commands
that name no path (an interpreter one-liner, ``git reset --hard``, a bare ``git stash``) are
checked against the explicit projects and the project owning the command's working directory.
The deny reason names the project (its ``[project] name`` and directory).

Output format (Claude Code hooks):

- allow: exit 0 and print nothing (the normal permission flow continues);
- deny (default ``--mode json``): exit 0 and print on stdout::

      {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                              "permissionDecision": "deny",
                              "permissionDecisionReason": "<use change_propose instead>"}}

  Claude Code blocks the call and feeds the reason back to the model;
- deny with ``--mode exit2``: exit code 2 with the reason on stderr (also blocks the call,
  stderr goes back to the model);
- unreadable input: exit 1 with a message on stderr (a non-blocking error: the tool call
  proceeds; the guard fails open on its own bugs, never on a recognised write).

Paths are resolved against the hook's ``cwd`` and with symlinks followed (``realpath``), so
``../`` and symlinked aliases of a protected file are denied too. Bash commands are split
on ``;``, ``&&``, ``||``, ``|`` and newlines and checked for: output redirection
(``>``, ``>>``, ``>|``, ``&>``), ``tee``, ``sed -i``, ``perl -i``, ``mv``, ``cp`` (destination),
``rm``/``unlink``/``truncate``/``ln``/``install``, ``dd of=``, ``git checkout|restore|rm|
reset --hard|stash|apply|clean|mv`` touching the files (or a directory holding them), and
interpreter one-liners (``python -c`` etc.) that name a protected file next to a write word.
A check on shell text is a safety net against slips, not a sandbox."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

DEFAULT_PROTECTED = ("REQUIREMENTS.md", "spec.py", "spec.trace.json")
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
PATH_KEYS = ("file_path", "notebook_path", "path")

_INTERP = {"python", "python3", "node", "perl", "ruby", "deno", "bun"}
_WRITE_WORDS = re.compile(
    r"\.write|write_text|write_bytes|writeFile|unlink|rename|\.replace\(|os\.remove|shutil"
    r"|copy|move|open\([^)]*[\"'][rbt]*[wax][+btr]*[\"']",
    re.I,
)


def _reason(name: str, project: Path | None = None) -> str:
    where = ""
    pick = ""
    if project is not None:
        from lattice.cli import project_name

        where = f" of project {project_name(project)} ({project})"
        pick = f', project: "{project}"'
    return (
        f"Blocked: {name}{where} is a Lattice spec file that Studio owns, and nothing changes in "
        "it without a checked change set. Do not edit it directly (not with Edit/Write and not "
        "from the shell). Call the Lattice MCP tool change_propose with {request: <the "
        f"change in plain words>, selected: [<targets>]{pick}}}, poll change_get until it is "
        "ready, and tell the owner to review and accept it in Lattice Studio."
    )


class Guard:
    def __init__(self, project: Path, extra: Sequence[str] = ()) -> None:
        self.project = project.resolve()
        names = [*DEFAULT_PROTECTED, *extra]
        try:
            cfg = tomllib.loads((self.project / "studio.toml").read_text(encoding="utf-8"))
            more = (cfg.get("mcp") or {}).get("protected") or []
            names += [str(x) for x in more if isinstance(x, str)]
        except (OSError, tomllib.TOMLDecodeError):
            pass
        self.names = list(dict.fromkeys(names))
        self.protected = [self._real(self.project / n) for n in self.names]
        self._fold = sys.platform == "darwin"
        self.last_project: Path | None = None  # the project of the last protected hit

    def _cwd_guards(self, cwd: str) -> list[Guard]:
        """The guards that apply to a command naming no path, run in `cwd`."""
        return [self]

    @staticmethod
    def _real(p: str | Path) -> str:
        return os.path.realpath(p)

    def _key(self, p: str) -> str:
        return p.casefold() if self._fold else p

    def _resolve(self, raw: str, cwd: str) -> str:
        raw = os.path.expanduser(raw)
        if not os.path.isabs(raw):
            raw = os.path.join(cwd, raw)
        return self._real(raw)

    def protected_name(self, resolved: str) -> str | None:
        """The protected file `resolved` is, or None."""
        k = self._key(resolved)
        for name, p in zip(self.names, self.protected, strict=True):
            if k == self._key(p):
                self.last_project = self.project
                return name
            try:
                if os.path.exists(resolved) and os.path.samefile(resolved, p):
                    self.last_project = self.project
                    return name
            except OSError:
                pass
        return None

    def holds_protected(self, resolved: str) -> str | None:
        """The protected file a directory `resolved` holds, or None."""
        if not os.path.isdir(resolved):
            return None
        base = self._key(resolved.rstrip(os.sep)) + os.sep
        for name, p in zip(self.names, self.protected, strict=True):
            if self._key(p).startswith(base):
                self.last_project = self.project
                return name
        return None

    # -- the edit tools
    def check_edit(self, tool_input: dict[str, Any], cwd: str) -> str | None:
        for key in PATH_KEYS:
            v = tool_input.get(key)
            if isinstance(v, str) and v:
                name = self.protected_name(self._resolve(v, cwd))
                if name:
                    return name
        return None

    # -- Bash
    @staticmethod
    def _tokens(command: str) -> list[str]:
        lex = shlex.shlex(command, posix=True, punctuation_chars="();<>|&\n")
        lex.whitespace = " \t\r"
        lex.whitespace_split = True
        lex.commenters = ""
        try:
            return list(lex)
        except ValueError:
            return re.split(r"\s+|(?=[;&|<>])|(?<=[;&|<>])", command)

    def check_bash(self, command: str, cwd: str) -> str | None:
        seg: list[str] = []
        segments: list[list[str]] = []
        for t in self._tokens(command):
            if t and re.fullmatch(r"[;&|()\n]+", t):
                segments.append(seg)
                seg = []
            elif t:
                seg.append(t)
        segments.append(seg)
        for toks in segments:
            if not toks:
                continue
            if toks[0] in ("cd", "pushd") and len(toks) > 1:
                cwd = self._resolve(toks[1], cwd)
                continue
            hit = self._segment(toks, cwd)
            if hit:
                return hit
        return None

    def _paths(self, toks: Sequence[str], cwd: str) -> list[tuple[str, str]]:
        """(token, resolved) for every token that could be a path."""
        out = []
        for t in toks:
            if not t or t.startswith("-") and "=" not in t:
                continue
            if "=" in t and not t.startswith(("/", ".", "~")):  # of=FILE, --out=FILE
                t = t.split("=", 1)[1]
            if t:
                out.append((t, self._resolve(t, cwd)))
        return out

    def _first_hit(self, toks: Sequence[str], cwd: str, dirs: bool = False) -> str | None:
        for _t, r in self._paths(toks, cwd):
            n = self.protected_name(r) or (self.holds_protected(r) if dirs else None)
            if n:
                return n
        return None

    def _segment(self, toks: list[str], cwd: str) -> str | None:
        # redirections anywhere in the segment: `>`, `>>`, `>|`, `&>`, `N>` then the file
        for i, t in enumerate(toks):
            if re.fullmatch(r"[<>|&]*>[|&>]*", t) and not t.endswith("&"):
                if i + 1 < len(toks):
                    n = self.protected_name(self._resolve(toks[i + 1], cwd))
                    if n:
                        return n
        cmd_i = 0
        while cmd_i < len(toks) and (
            re.fullmatch(r"\w+=.*", toks[cmd_i])
            or toks[cmd_i] in ("sudo", "env", "command", "time", "nohup")
        ):
            cmd_i += 1
        if cmd_i >= len(toks):
            return None
        cmd = os.path.basename(toks[cmd_i])
        args = toks[cmd_i + 1 :]
        if cmd == "tee":
            return self._first_hit(args, cwd)
        if cmd in ("sed", "gsed", "perl", "awk", "gawk", "ruby"):
            inplace = any(
                a in ("--in-place", "inplace")
                or a.startswith("--in-place=")
                or re.fullmatch(r"-[A-Za-z]*i[^\s]*", a)
                for a in args
            )
            if inplace:
                return self._first_hit(args, cwd)
        if cmd in ("mv", "rm", "unlink", "truncate", "ln", "install", "rsync", "shred", "chmod"):
            if cmd == "chmod":
                return None
            return self._first_hit(args, cwd, dirs=cmd in ("mv", "rm"))
        if cmd == "cp":
            ps = self._paths([a for a in args if not a.startswith("-")], cwd)
            if ps:
                n = self.protected_name(ps[-1][1])
                if n:
                    return n
            return None
        if cmd == "dd":
            for a in args:
                if a.startswith("of="):
                    n = self.protected_name(self._resolve(a[3:], cwd))
                    if n:
                        return n
            return None
        if cmd == "git":
            return self._git(args, cwd)
        if cmd in _INTERP or re.fullmatch(r"python[\d.]+", cmd):
            blob = " ".join(args)
            if _WRITE_WORDS.search(blob):
                for g in self._cwd_guards(cwd):
                    for name in g.names:
                        if os.path.basename(name) in blob:
                            self.last_project = g.project
                            return name
        return None

    def _git(self, args: list[str], cwd: str) -> str | None:
        i = 0
        while i < len(args) and args[i].startswith("-"):
            if args[i] in ("-C", "-c") and i + 1 < len(args):
                if args[i] == "-C":
                    cwd = self._resolve(args[i + 1], cwd)
                i += 1
            i += 1
        if i >= len(args):
            return None
        sub, rest = args[i], args[i + 1 :]
        if sub in ("checkout", "restore", "rm", "mv", "clean", "apply", "am", "revert"):
            hit = self._first_hit(rest, cwd, dirs=sub in ("checkout", "restore", "clean"))
            return hit
        if sub == "reset" and "--hard" in rest:
            return self._cwd_hit(cwd)
        if sub == "stash":
            verb = rest[0] if rest else "push"
            if verb in ("list", "show", "branch"):
                return None
            if verb in ("push", "save", "pop", "apply", "drop", "clear") or verb.startswith("-"):
                pathspec = [a for a in rest if a not in ("push", "save", "--", "-u", "-k")]
                hit = self._first_hit(pathspec, cwd, dirs=True)
                if hit:
                    return hit
                if not [a for a in rest if not a.startswith("-") and a not in ("push", "save")]:
                    return self._cwd_hit(cwd)  # a bare stash sweeps every tracked change
        return None

    def _cwd_hit(self, cwd: str) -> str | None:
        """The first protected name of the guards that apply in `cwd` (a whole-tree command)."""
        for g in self._cwd_guards(cwd):
            if g.names:
                self.last_project = g.project
                return g.names[0]
        return None

    # -- the hook
    def decide(self, event: dict[str, Any]) -> str | None:
        """The protected file the call would write, or None to allow."""
        tool = event.get("tool_name")
        ti = event.get("tool_input")
        if not isinstance(ti, dict):
            return None
        cwd = event.get("cwd")
        cwd = cwd if isinstance(cwd, str) and cwd else os.getcwd()
        if tool in EDIT_TOOLS:
            return self.check_edit(ti, cwd)
        if tool == "Bash":
            cmd = ti.get("command")
            if isinstance(cmd, str):
                return self.check_bash(cmd, cwd)
        return None


TRACE_FILE = "spec.trace.json"


def owner_of(path: str | Path) -> Path | None:
    """The project owning `path` (already a real path): the nearest directory holding
    ``spec.trace.json``, walking up from the path (a directory counts itself). The walk stops at
    the filesystem root, or at a directory holding ``.git`` without ``spec.trace.json``."""
    p = Path(path)
    try:
        start = p if p.is_dir() else p.parent
    except OSError:
        start = p.parent
    for d in (start, *start.parents):
        try:
            if (d / TRACE_FILE).is_file():
                return d
            if (d / ".git").exists():
                return None
        except OSError:
            return None
    return None


class MultiGuard(Guard):
    """The guard over many projects: the explicit ones (as :class:`Guard` each, when they have a
    drafted spec) plus, for every path, the project owning it (:func:`owner_of`)."""

    def __init__(self, projects: Sequence[Path] = ()) -> None:
        self.static: list[Guard] = []
        for p in projects:
            if is_lattice_project(p) and all(g.project != p.resolve() for g in self.static):
                self.static.append(Guard(p))
        self._owners: dict[str, Guard | None] = {}
        self._fold = sys.platform == "darwin"
        self.last_project: Path | None = None
        self.names = [n for g in self.static for n in g.names]  # informational only
        self.project = self.static[0].project if self.static else Path(os.sep)

    def _owner_guard(self, resolved: str) -> Guard | None:
        own = owner_of(resolved)
        if own is None:
            return None
        key = str(own)
        if key not in self._owners:
            same = next((g for g in self.static if g.project == own.resolve()), None)
            self._owners[key] = same or Guard(own)
        return self._owners[key]

    def _guards_for(self, resolved: str) -> list[Guard]:
        out = list(self.static)
        g = self._owner_guard(resolved)
        if g is not None and g not in out:
            out.append(g)
        return out

    def _cwd_guards(self, cwd: str) -> list[Guard]:
        return self._guards_for(self._real(cwd))

    def protected_name(self, resolved: str) -> str | None:
        for g in self._guards_for(resolved):
            name = g.protected_name(resolved)
            if name:
                self.last_project = g.project
                return name
        return None

    def holds_protected(self, resolved: str) -> str | None:
        for g in self._guards_for(resolved):
            name = g.holds_protected(resolved)
            if name:
                self.last_project = g.project
                return name
        return None


def is_lattice_project(project: Path) -> bool:
    """A project is guarded only once it has a drafted spec (spec.py and spec.trace.json).

    The hook is installed at user level, so it runs in every directory; a project that has
    its own unrelated ``spec.py`` or ``REQUIREMENTS.md`` must stay editable, and before a
    spec is drafted ``REQUIREMENTS.md`` is the owner's own input."""
    root = project.resolve()
    return (root / "spec.py").is_file() and (root / "spec.trace.json").is_file()


def main(argv: Sequence[str] | None = None, stdin: Any = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m lattice.mcp.guard", description=__doc__)
    ap.add_argument(
        "--project",
        type=Path,
        action="append",
        default=[],
        help="a project to protect (repeatable; optional: every path is also checked against "
        "the nearest project holding spec.trace.json)",
    )
    ap.add_argument("--mode", choices=("json", "exit2"), default="json")
    ns = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        event = json.loads((stdin or sys.stdin).read())
        if not isinstance(event, dict):
            raise ValueError("the hook input is not an object")
    except (ValueError, OSError) as e:
        print(f"lattice guard: unreadable hook input: {e}", file=sys.stderr)
        return 1
    g = MultiGuard(ns.project)
    hit = g.decide(event)
    if hit is None:
        return 0  # nothing protected is written (no project owns the paths, or no spec file)
    reason = _reason(hit, g.last_project)
    if ns.mode == "exit2":
        print(reason, file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
