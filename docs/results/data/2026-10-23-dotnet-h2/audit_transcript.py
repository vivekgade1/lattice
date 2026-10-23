#!/usr/bin/env python3
"""Audit one author's `claude` session transcript for forbidden reads (prereg docs/results/2026-10-23-dotnet-h2-prereg.md, section 9.5).

    python3 audit_transcript.py TRANSCRIPT.jsonl --root ~/Documents/dn-h2-author [--out AUDIT.json]
    python3 audit_transcript.py --self-check

Standard library only. Every tool call of the transcript (``assistant`` messages, ``tool_use`` blocks, side chains included) is listed;
paths are taken from the file tools' arguments and, heuristically, from Bash command text (a ``cd`` and simple variable assignments are
tracked across calls). Each path is resolved and classed:

* **forbidden**: under a Lattice checkout or worktree (``~/Documents/lattice*``), ``~/.claude``, Streak, the job scheduler, or another
  ``~/Documents/dn-*`` directory (an earlier author's);
* **outside**: any other existing-root absolute path outside ``--root`` and not on the toolchain allowlist; a tool outside Bash, Read, Write,
  Edit, Glob, Grep (and TodoWrite); a transcript ``cwd`` outside ``--root``;
* **review** (listed, not voiding): the shared temporary directories, ``/etc``, and path tokens built from a shell variable the audit cannot
  resolve.

Verdict ``void`` iff a forbidden or outside item exists; exit 0 (pass), 1 (void), 2 (the transcript cannot be read). Shell text is parsed
heuristically (a computed path can evade it): every Bash command is printed in the output for a human to read.
"""

# ruff: noqa: E501
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path

HOME = os.path.expanduser("~")
FORBIDDEN_PREFIXES = (
    f"{HOME}/Documents/lattice",  # the repository, its worktrees, and lattice-* siblings (prefix match, on purpose)
    f"{HOME}/.claude",
    f"{HOME}/Documents/streak",
    f"{HOME}/Documents/job-scheduler-go",
)
OTHER_AUTHORS = f"{HOME}/Documents/dn-"
ALLOW_PREFIXES = ("/opt/homebrew", "/usr", "/bin", "/sbin", "/System", "/Library/Developer/CommandLineTools", "/Library/Frameworks/Python.framework")
ALLOW_EXACT = ("/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/urandom", "/dev/random", "/")
ALLOW_DEV_FD = "/dev/fd/"
REVIEW_PREFIXES = ("/tmp", "/private/tmp", "/var/folders", "/private/var/folders", "/etc", "/private/etc", "/var/tmp", "/private/var/tmp")
ALLOWED_TOOLS = ("Bash", "Read", "Write", "Edit", "MultiEdit", "Glob", "Grep")
HARMLESS_TOOLS = ("TodoWrite",)
SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\||\n")
REDIRECT = re.compile(r"^\d*[<>]+&?")
VAR = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")


def under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


def classify(path: str, root: str) -> str:
    """``inside`` | ``forbidden`` | ``allowed`` | ``review`` | ``outside`` for a resolved absolute path."""
    p = os.path.normpath(path)
    if under(p, root):
        return "inside"
    if any(p.startswith(f) for f in FORBIDDEN_PREFIXES) or (p.startswith(OTHER_AUTHORS) and not under(p, root)):
        return "forbidden"
    if p in ALLOW_EXACT or p.startswith(ALLOW_DEV_FD) or any(under(p, a) for a in ALLOW_PREFIXES):
        return "allowed"
    if any(under(p, r) for r in REVIEW_PREFIXES):
        return "review"
    return "outside"


def looks_like_path(tok: str) -> bool:
    if not tok or "://" in tok:
        return False
    if tok.startswith("~") or tok.startswith("./") or tok == ".." or tok.startswith("../") or "/../" in tok or tok.endswith("/.."):
        return True
    if tok.startswith("/"):
        first = tok.lstrip("/").split("/", 1)[0]
        return first == "" or os.path.isdir("/" + first)  # "/reports/1" (a route) is not a path: no /reports at the filesystem root
    return False


class Shell:
    """A crude model of the Bash tool's session: the working directory and simple variables, carried across calls."""

    def __init__(self, root: str) -> None:
        self.cwd = root
        self.vars: dict[str, str] = {"PWD": root, "OUT": root}

    def expand(self, tok: str) -> tuple[str, bool]:
        unknown = False

        def sub(m: re.Match[str]) -> str:
            nonlocal unknown
            name = m.group(1)
            if name in self.vars:
                return self.vars[name]
            if name == "HOME":
                return HOME
            unknown = True
            return m.group(0)

        return VAR.sub(sub, tok), unknown

    def resolve(self, tok: str) -> str:
        if tok.startswith("~"):
            tok = HOME + tok[1:]
        return os.path.normpath(tok if tok.startswith("/") else os.path.join(self.cwd, tok))


def tokens_of(segment: str) -> list[str]:
    try:
        toks = shlex.split(segment, posix=True)
    except ValueError:
        toks = segment.split()
    out = []
    for t in toks:
        t = REDIRECT.sub("", t)
        if "=" in t and not t.startswith("/"):
            t = t.split("=", 1)[1]
        out.append(t)
    return out


def bash_paths(cmd: str, sh: Shell) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for seg in SEGMENT_SPLIT.split(cmd):
        seg = seg.strip()
        if not seg:
            continue
        for a in seg.split():  # simple assignments (VAR=value, export VAR=value), in order
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(\S+)$", a)
            if m:
                val, _ = sh.expand(m.group(2).strip("'\""))
                sh.vars[m.group(1)] = sh.resolve(val) if looks_like_path(val) or val.startswith("$") else val
        toks = tokens_of(seg)
        if toks and toks[0] in ("cd", "pushd"):
            target, unknown = sh.expand(toks[1] if len(toks) > 1 else HOME)
            if unknown:
                found.append({"token": toks[1], "path": None, "class": "review", "why": "cd to an unresolved variable"})
                continue
            sh.cwd = sh.resolve(target)
            found.append({"token": f"cd {toks[1] if len(toks) > 1 else '~'}", "path": sh.cwd, "class": classify(sh.cwd, sh.vars["PWD_ROOT"])})
            continue
        for t in toks[1:] if toks and toks[0] in ("echo", "printf") else toks:
            t2, unknown = sh.expand(t)
            if unknown and ("/" in t2 or t2.startswith("$")):
                found.append({"token": t, "path": None, "class": "review", "why": "an unresolved shell variable"})
                continue
            if looks_like_path(t2):
                p = sh.resolve(t2)
                found.append({"token": t, "path": p, "class": classify(p, sh.vars["PWD_ROOT"])})
    return found


def tool_paths(name: str, inp: dict, sh: Shell, root: str) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    keys = {"Read": ("file_path",), "Write": ("file_path",), "Edit": ("file_path",), "MultiEdit": ("file_path",), "NotebookEdit": ("notebook_path",), "Glob": ("path", "pattern"), "Grep": ("path", "glob")}.get(name, ())
    for k in keys:
        v = inp.get(k)
        if not isinstance(v, str) or not v:
            continue
        if k in ("pattern", "glob") and not (v.startswith("/") or v.startswith("~") or ".." in v):
            continue  # a relative glob stays under the tool's path
        base = v.split("*", 1)[0] if k in ("pattern", "glob") else v
        p = sh.resolve(base or ".")
        out.append({"token": f"{k}={v}", "path": p, "class": classify(p, root)})
    return out


def audit(transcript: Path, root: str) -> dict[str, object]:
    root = os.path.normpath(os.path.expanduser(root))
    sh = Shell(root)
    sh.vars["PWD_ROOT"] = root
    calls, items, bash_cmds, cwds, sessions = [], [], [], set(), set()
    for n, line in enumerate(transcript.read_text().splitlines()):
        if not line.strip():
            continue
        ev = json.loads(line)
        if ev.get("cwd"):
            cwds.add(ev["cwd"])
        if ev.get("sessionId"):
            sessions.add(ev["sessionId"])
        msg = ev.get("message") or {}
        if ev.get("type") != "assistant" or not isinstance(msg.get("content"), list):
            continue
        for block in msg["content"]:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name, inp = block.get("name", ""), block.get("input") or {}
            idx = len(calls)
            calls.append({"i": idx, "line": n + 1, "tool": name, "sidechain": bool(ev.get("isSidechain"))})
            if name not in ALLOWED_TOOLS:
                items.append({"i": idx, "tool": name, "token": name, "path": None, "class": "review" if name in HARMLESS_TOOLS else "outside", "why": "a tool outside the allowed set"})
                continue
            if name == "Bash":
                cmd = str(inp.get("command", ""))
                bash_cmds.append({"i": idx, "command": cmd[:600]})
                found = bash_paths(cmd, sh)
            else:
                found = tool_paths(name, inp, sh, root)
            items += [{"i": idx, "tool": name, **f} for f in found if f["class"] not in ("inside", "allowed")]
    for c in sorted(cwds):
        k = classify(c, root)
        if k not in ("inside",):
            items.append({"i": None, "tool": "(transcript cwd)", "token": c, "path": c, "class": "outside" if k != "forbidden" else "forbidden", "why": "the session ran outside the fixed directory"})
    by_tool: dict[str, int] = {}
    for c in calls:
        by_tool[c["tool"]] = by_tool.get(c["tool"], 0) + 1
    cls = {k: [x for x in items if x["class"] == k] for k in ("forbidden", "outside", "review")}
    return {
        "transcript": str(transcript),
        "transcript_sha256": hashlib.sha256(transcript.read_bytes()).hexdigest(),
        "root": root,
        "sessions": sorted(sessions),
        "tool_calls": len(calls),
        "by_tool": by_tool,
        "forbidden": cls["forbidden"],
        "outside": cls["outside"],
        "review": cls["review"],
        "cwds": sorted(cwds),
        "bash_commands": bash_cmds,
        "verdict": "void" if cls["forbidden"] or cls["outside"] else "pass",
        "method": "heuristic path extraction from tool arguments and shell text; every Bash command is listed for a human to read",
    }


def self_check() -> int:
    """Synthetic transcripts: each planted access must land in its class, a clean one must pass."""
    root = f"{HOME}/Documents/dn-h2-selfcheck-root"

    def tx(calls: list[tuple[str, dict]], cwd: str = root) -> Path:
        f = Path(tempfile.mkstemp(suffix=".jsonl")[1])
        lines = [json.dumps({"type": "assistant", "cwd": cwd, "sessionId": "s", "message": {"content": [{"type": "tool_use", "name": n, "input": i}]}}) for n, i in calls]
        f.write_text("\n".join(lines) + "\n")
        return f

    clean = [("Read", {"file_path": f"{root}/BRIEF.md"}), ("Bash", {"command": "export SCRATCH=$PWD/scratch HOME=$SCRATCH/home && mkdir -p $HOME && dotnet build tests/spec/x/x.csproj -o scratch/out > scratch/b.log 2>&1"}), ("Bash", {"command": "curl -s http://127.0.0.1:5000/health; python3 -c 'print(\"/reports/1\")'"}), ("Write", {"file_path": "tests/spec/x/Program.cs", "content": "x"}), ("Glob", {"pattern": "**/*.cs"}), ("Bash", {"command": "ls /opt/homebrew/bin/dotnet /usr/bin/env >/dev/null"})]
    cases = {
        "clean": (clean, "pass", {}),
        "repo read": ([("Read", {"file_path": f"{HOME}/Documents/lattice/lattice/importer/rules.py"})], "void", {"forbidden": 1}),
        "repo via ..": ([("Bash", {"command": "cat ../lattice/docs/HANDOFF.md"})], "void", {"forbidden": 1}),
        "cd into a worktree": ([("Bash", {"command": f"cd {HOME}/Documents/lattice-dn && ls"})], "void", {"forbidden": 1}),
        "earlier author": ([("Grep", {"pattern": "Owner", "path": f"{HOME}/Documents/dn-h1-author"})], "void", {"forbidden": 1}),
        "other home path": ([("Bash", {"command": f"ls {HOME}/Desktop"})], "void", {"outside": 1}),
        "web tool": ([("WebFetch", {"url": "https://example.com"})], "void", {"outside": 1}),
        "tmp only": ([("Bash", {"command": "echo hi > /tmp/x.log"})], "pass", {"review": 1}),
        "unknown var": ([("Bash", {"command": "cat $WHERE/file.txt"})], "pass", {"review": 1}),
        "cwd outside": (clean[:1], "void", {"outside": 1}),
    }
    bad = []
    for name, (calls, verdict, counts) in cases.items():
        f = tx(calls, cwd=f"{HOME}/Documents/elsewhere" if name == "cwd outside" else root)
        a = audit(f, root)
        f.unlink()
        got = {k: len(a[k]) for k in ("forbidden", "outside", "review")}  # type: ignore[arg-type]
        if a["verdict"] != verdict or any(got[k] < v for k, v in counts.items()) or (verdict == "pass" and (got["forbidden"] or got["outside"])):
            bad.append(f"{name}: verdict {a['verdict']} counts {got}")
    print("SELF-CHECK", "FAILED: " + "; ".join(bad) if bad else f"OK: {len(cases)} cases")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("transcript", nargs="?")
    ap.add_argument("--root", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args(argv)
    if a.self_check:
        return self_check()
    if not a.transcript or not a.root:
        ap.error("TRANSCRIPT and --root are required")
    try:
        doc = audit(Path(a.transcript), a.root)
    except (OSError, ValueError) as err:
        print(f"cannot read the transcript: {err}", file=sys.stderr)
        return 2
    text = json.dumps(doc, indent=1) + "\n"
    if a.out:
        Path(a.out).write_text(text)
    print(json.dumps({k: doc[k] for k in ("tool_calls", "by_tool", "verdict")}), f"forbidden={len(doc['forbidden'])} outside={len(doc['outside'])} review={len(doc['review'])}")  # type: ignore[arg-type]
    for x in [*doc["forbidden"], *doc["outside"]]:  # type: ignore[misc]
        print(f"  {x['class']}: call {x['i']} {x['tool']}: {x['token']} -> {x['path']}")
    return 0 if doc["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
