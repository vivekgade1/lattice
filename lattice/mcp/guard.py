"""A Claude Code ``PreToolUse`` hook that keeps the agent's hands off the spec files.

Wire it in ``.claude/settings.json``::

    {"hooks": {"PreToolUse": [{
        "matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash",
        "hooks": [{"type": "command",
                   "command": "python -m lattice.mcp.guard --project /path/to/project"}]}]}}

It reads the hook's JSON from stdin (``tool_name``, ``tool_input``, ``cwd``, ...). The
protected files are a project's ``REQUIREMENTS.md``, ``spec.py`` and ``spec.trace.json``
(plus names under ``[mcp] protected = [...]`` in ``studio.toml``, relative to the project).
While they exist, the owner's approved ``<stem>.rationale.json`` and ``<stem>.glossary.json``
(Spike G, named from the trace's ``source``) are protected too; the proposals files are not.

**Many projects** (layer 1 of the multi-project phase): ``--project`` is optional and may be
repeated. Each ``--project`` that has a drafted spec is protected as before; on top of that,
every path the hook sees is checked against its *owning* project: the nearest directory holding
``spec.trace.json``, walking up from the path's real location (the walk stops at the
filesystem root, or at a directory holding ``.git`` that has no ``spec.trace.json``: a project
never owns files outside its repository). A path with no owning project is allowed. Commands
that name no path (an interpreter one-liner, ``git reset --hard``, a bare ``git stash``) are
checked against the explicit projects and the project owning the command's working directory.
The deny reason names the project (its ``[project] name`` and directory).

**A parent of a project.** The owner walk goes up, so ``rm -rf <dir>`` of a directory that holds
a project nobody named would pass. For a path the shell forms below check as a directory (``rm``,
``mv`` sources, ``git checkout|restore|clean`` paths, ``git stash`` pathspecs) the guard also
looks DOWN: breadth first, at most :data:`WALK_DEPTH` levels and :data:`WALK_BUDGET` directory
listings, not entering :data:`PRUNED_DIRS` nor following directory symlinks; the first project
found (a directory holding ``spec.trace.json``) is named in the deny. A ``mv`` destination is
not walked; when the budget is spent the command is allowed (a safety net, not a sandbox). Known
gaps: globs (``rm -rf *``), ``find -delete``, ``xargs rm``, ``rsync --delete``, ``$(...)``,
``git rm`` and ``git mv`` of a directory, a pathless ``git clean`` (``git clean -fdx .`` is denied
inside a project, ``git clean -fdx`` is not).

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
# Spike G: the owner's approved rationale and glossary files, protected while they exist
NOTES_SUFFIXES = (".rationale.json", ".glossary.json")
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
PATH_KEYS = ("file_path", "notebook_path", "path")

_INTERP = {"python", "python3", "node", "perl", "ruby", "deno", "bun"}
_WRITE_WORDS = re.compile(
    r"\.write|write_text|write_bytes|writeFile|unlink|rename|\.replace\(|os\.remove|shutil"
    r"|copy|move|open\([^)]*[\"'][rbt]*[wax][+btr]*[\"']",
    re.I,
)


def notes_files(project: Path) -> list[str]:
    """The names (relative to `project`) of the owner's authoritative rationale and glossary
    files that exist: ``<stem>.rationale.json`` and ``<stem>.glossary.json`` beside the trace,
    named from its ``source`` (the same rule as ``lattice.spec.rationale.notes_names``, kept
    here so the hook does not import the spec package). Empty without a trace or any such file."""
    try:
        data = json.loads((project / "spec.trace.json").read_text(encoding="utf-8"))
        source = data.get("source") if isinstance(data, dict) else None
    except (OSError, ValueError):
        return []
    if not isinstance(source, str) or not source:
        return []
    stem = Path(source).stem
    return [
        f"{stem}{suffix}" for suffix in NOTES_SUFFIXES if (project / f"{stem}{suffix}").is_file()
    ]


def _reason(name: str, project: Path | None = None) -> str:
    where = ""
    pick = ""
    if project is not None:
        from lattice.cli import project_name

        where = f" of project {project_name(project)} ({project})"
        pick = f', project: "{project}"'
    if name.endswith(NOTES_SUFFIXES):
        return (
            f"Blocked: {name}{where} holds the owner's approved rationale or glossary. Only the "
            "owner writes it, in a terminal (`python -m lattice.spec rationale approve|add` or "
            "`glossary approve|add`), or in their own editor. Do not edit it directly (not with "
            "Edit/Write and not from the shell). You may draft proposals into the "
            "`.proposed.json` file beside it with `rationale draft --out` or `glossary draft "
            "--out`; the owner approves them."
        )
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
        names += notes_files(self.project)  # Spike G: only while the owner's files exist
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
            if k == self._key(p) or (  # a path under a protected directory is protected (L1b)
                os.path.isdir(p) and k.startswith(self._key(p).rstrip(os.sep) + os.sep)
            ):
                self.last_project = self.project
                return name
            try:
                if os.path.exists(resolved) and os.path.samefile(resolved, p):
                    self.last_project = self.project
                    return name
            except OSError:
                pass
        return None

    def holds_protected(self, resolved: str, below: bool = True) -> str | None:
        """The protected file a directory `resolved` holds, or None. `below` is read by
        :class:`MultiGuard` only (the walk down for a project nobody named)."""
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

    def _first_hit(
        self, toks: Sequence[str], cwd: str, dirs: bool = False, dest_last: bool = False
    ) -> str | None:
        """The first protected name `toks` touch. `dirs`: a directory also counts when it holds
        a protected file (and, for a :class:`MultiGuard`, when a project sits below it).
        `dest_last`: the last path of several is a destination (``mv``): moving INTO a directory
        leaves its contents, so it gets the checks without the walk down."""
        ps = self._paths(toks, cwd)
        for i, (_t, r) in enumerate(ps):
            below = not (dest_last and len(ps) > 1 and i == len(ps) - 1)
            n = self.protected_name(r) or (self.holds_protected(r, below=below) if dirs else None)
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
            return self._first_hit(args, cwd, dirs=cmd in ("mv", "rm"), dest_last=cmd == "mv")
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

# The walk down (MultiGuard.project_below): directories never entered, how deep, how many
# directories listed before the guard gives up (and allows).
PRUNED_DIRS = frozenset(
    {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache",
     ".ruff_cache", ".tox"}
)  # fmt: skip
WALK_DEPTH = 5
WALK_BUDGET = 4000


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
        return self._guard_of(own)

    def _guard_of(self, own: Path) -> Guard:
        key = str(own)
        found = self._owners.get(key)
        if found is None:
            same = next((g for g in self.static if g.project == own.resolve()), None)
            found = self._owners[key] = same or Guard(own)
        return found

    def project_below(self, target: str) -> Path | None:
        """The first project found BELOW `target` (a real path), or None.

        Only an existing directory that is not pruned (:data:`PRUNED_DIRS`) is walked. Breadth
        first, the entries of each level sorted by name, so the shallowest and then first-sorted
        project is the one named. A project is a directory holding ``spec.trace.json``.
        Directories at depth 1..:data:`WALK_DEPTH` are listed; a directory symlink inside the
        target is not followed (``rm -rf`` removes the link, and a cycle cannot loop); a
        directory that cannot be listed is skipped. When :data:`WALK_BUDGET` listings are spent
        the walk stops and answers None: the guard fails open there, never crashes."""
        try:
            if not os.path.isdir(target) or os.path.basename(target.rstrip(os.sep)) in PRUNED_DIRS:
                return None
        except OSError:
            return None
        level = [target]
        spent = 0
        for depth in range(WALK_DEPTH + 1):
            nxt: list[str] = []
            for d in level:
                if spent >= WALK_BUDGET:
                    return None
                spent += 1
                try:
                    with os.scandir(d) as it:
                        entries = sorted(it, key=lambda e: e.name)
                except OSError:
                    continue
                for e in entries:
                    try:
                        if e.name == TRACE_FILE and e.is_file():
                            return Path(d)
                    except OSError:
                        continue
                if depth == WALK_DEPTH:
                    continue
                for e in entries:
                    try:
                        if e.name not in PRUNED_DIRS and e.is_dir(follow_symlinks=False):
                            nxt.append(e.path)
                    except OSError:
                        continue
            level = nxt
            if not level:
                break
        return None

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

    def holds_protected(self, resolved: str, below: bool = True) -> str | None:
        """The protected file the directory `resolved` holds (an explicit project, or the one
        owning it, walking up), else, with `below`, the first one of a project found below it
        (:meth:`project_below`: ``rm -rf`` of a parent of a project nobody named)."""
        for g in self._guards_for(resolved):
            name = g.holds_protected(resolved)
            if name:
                self.last_project = g.project
                return name
        if below:
            found = self.project_below(resolved)
            if found is not None:
                g = self._guard_of(found)
                if g.names:
                    self.last_project = g.project
                    return g.names[0]
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
