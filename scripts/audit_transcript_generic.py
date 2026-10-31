#!/usr/bin/env python3
"""Audit one `claude` session transcript for reads/writes outside a fixed directory (generic successor of the frozen H2 auditor).

    python3 audit_transcript_generic.py TRANSCRIPT.jsonl --root DIR [--scratch DIR] [--out FILE]
    python3 audit_transcript_generic.py --self-check

Standard library only. Every tool call of the transcript (``assistant`` messages, ``tool_use`` blocks, side chains included) is listed;
paths come from the file tools' arguments and from Bash command text. The Bash text is lexed, not grepped: quotes, comments, ``( )``
subshells, ``$( )``, here-documents, ``cd``/``pushd``/``popd``, ``VAR=value``/``export`` and ``for VAR in ...`` loops are modelled. The
working directory persists across the calls of one transcript; shell variables do not (each Bash call starts with none, as in the tool).

What is a path and what is data (the two false positives of the H2 round this fixes):

* a comment marker (``//`` in C#, ``# ...`` in shell) is never a path; a token of slashes only is not a path;
* the body of a here-document is DATA (``cat > X.cs <<'EOF' ...``), not scanned for paths. Two exceptions: a body fed to a shell
  (``bash <<EOF``) is lexed as commands, and a body fed to an interpreter (``python3 - <<EOF``, node, ruby, perl) or a ``-c``/``-e``
  argument is searched for quoted/absolute path literals (absolute, ``~``, ``$HOME``, ``../``). Forbidden-prefix text inside plain
  data is listed under ``review``, never voiding;
* ``../`` hops are resolved against the tracked cwd. A ``for n in 01 02 ...`` loop variable is tried with each listed value, so
  ``cd scratch/build/m$n && ... < ../../../mutants/m$n.diff`` lands inside the root. A variable that cannot be resolved is
  ``review`` (listed, not voiding), unless the literal part of the path already puts it under a forbidden prefix.

Classes: **forbidden** (a Lattice checkout or worktree ``~/Documents/lattice*``, ``~/.claude``, Streak, the job scheduler, another
``~/Documents/dn-*`` directory, any ``--forbid`` prefix); **outside** (any other absolute path outside ``--root``/``--scratch`` and
the toolchain allowlist, ``~`` and ``cd ~``, a ``..`` that really leaves the root, a tool outside Bash/Read/Write/Edit/Glob/Grep, a
transcript ``cwd`` outside the root); **review** (listed, not voiding: shared temporary directories, ``/etc``, unresolved variables).

Verdict ``void`` iff a forbidden or outside item exists; exit 0 (pass), 1 (void), 2 (the transcript cannot be read). Shell text is
parsed heuristically (a computed path can evade it): every Bash command is stored in the output for a human to read.
"""

# ruff: noqa: E501
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import re
import sys
import tempfile
from pathlib import Path

AUDITOR = "audit_transcript_generic/1"
HOME = os.path.expanduser("~")
FORBIDDEN_PREFIXES = (
    f"{HOME}/Documents/lattice",  # the repository, its worktrees, and lattice-* siblings (prefix match, on purpose)
    f"{HOME}/.claude",
    f"{HOME}/Documents/streak",
    f"{HOME}/Documents/job-scheduler-go",
)
OTHER_AUTHORS = f"{HOME}/Documents/dn-"
ALLOW_PREFIXES = (
    "/opt/homebrew",
    "/usr",
    "/bin",
    "/sbin",
    "/System",
    "/Library/Developer/CommandLineTools",
    "/Library/Frameworks/Python.framework",
)
ALLOW_EXACT = (
    "/dev/null",
    "/dev/stdin",
    "/dev/stdout",
    "/dev/stderr",
    "/dev/tty",
    "/dev/urandom",
    "/dev/random",
    "/",
)
ALLOW_DEV_FD = "/dev/fd/"
REVIEW_PREFIXES = (
    "/tmp",
    "/private/tmp",
    "/var/folders",
    "/private/var/folders",
    "/etc",
    "/private/etc",
    "/var/tmp",
    "/private/var/tmp",
)
ALLOWED_TOOLS = ("Bash", "Read", "Write", "Edit", "MultiEdit", "Glob", "Grep")
HARMLESS_TOOLS = ("TodoWrite",)

PH_OPEN, PH_CLOSE = "\x01", "\x02"  # an unresolved variable: PH_OPEN name PH_CLOSE
LIT_DOLLAR = "\x03"  # a `$` that must not expand (single-quoted or escaped)
SUB_OPEN, SUB_CLOSE = "\x04", "\x05"  # a command substitution: SUB_OPEN index SUB_CLOSE
PH_RE = re.compile(f"{PH_OPEN}([^{PH_CLOSE}]*){PH_CLOSE}")
SUB_RE = re.compile(f"{SUB_OPEN}(\\d+){SUB_CLOSE}")
VAR_RE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)([^}]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(\+?)=(.*)$", re.S)
BENIGN_ENV = {
    "PATH",
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "PYTHONPATH",
    "CLASSPATH",
    "MANPATH",
    "NODE_PATH",
    "CDPATH",
    "PS1",
    "IFS",
}
KEYWORDS = {
    "if",
    "then",
    "else",
    "elif",
    "fi",
    "do",
    "done",
    "while",
    "until",
    "!",
    "{",
    "}",
    "time",
    "esac",
    "case",
    "in",
    "function",
}
WRAPPERS = {
    "sudo",
    "env",
    "exec",
    "nohup",
    "command",
    "builtin",
    "nice",
    "time",
    "timeout",
    "xargs",
}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
INTERPRETERS = ("python", "node", "ruby", "perl", "php", "deno", "bun", "lua", "Rscript")
CODE_FLAGS = {"-c", "-e", "-p", "--eval", "--command"}

# path literals inside interpreter source or a -c/-e argument; the lookbehinds keep `//` comments, URLs and `a/b` expressions out
_SEG = r"""[^\s"'`<>|;&()\\{}\[\],=*?:/]"""
ABS_RE = re.compile(rf"""(?<![\w/.$~:@\-%{{]])/(?:{_SEG}+/?)+""")
HOME_RE = re.compile(
    rf"""(?<![\w/.$~:@\-])(?:~|\$HOME|\$\{{HOME\}})(?=/|[\s"'`;,)\]]|$)(?:/{_SEG}*)*"""
)
REL_RE = re.compile(rf"""(?<![\w/.$~:@\-])\.\./(?:{_SEG}*/?)*|(?<=["'])\.\.(?=["'])""")
FORBIDDEN_TEXT = re.compile(
    r"(?:~|\$HOME|\$\{HOME\}|"
    + re.escape(HOME)
    + r")/(?:Documents/(?:lattice|streak|job-scheduler-go|dn-)|\.claude)[\w./-]*"
)


def under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


def classify(
    path: str,
    root: str,
    scratch: str = "",
    forbid: tuple[str, ...] = (),
    allow: tuple[str, ...] = (),
) -> str:
    """``inside`` | ``forbidden`` | ``allowed`` | ``review`` | ``outside`` for a resolved absolute path (placeholders already neutralised)."""
    p = os.path.normpath(path)
    roots = {root, os.path.realpath(root)}
    if scratch:
        roots |= {scratch, os.path.realpath(scratch)}
    if any(under(p, r) for r in roots):
        return "inside"
    if any(p.startswith(f) for f in (*FORBIDDEN_PREFIXES, *forbid)) or p.startswith(OTHER_AUTHORS):
        return "forbidden"
    if (
        p in ALLOW_EXACT
        or p.startswith(ALLOW_DEV_FD)
        or any(under(p, a) for a in (*ALLOW_PREFIXES, *allow))
    ):
        return "allowed"
    if any(under(p, r) for r in REVIEW_PREFIXES):
        return "review"
    return "outside"


def looks_like_path(tok: str) -> bool:
    """A token the audit resolves: absolute (first component exists at the filesystem root), ``~``, or a ``./``/``../`` form."""
    if not tok or "://" in tok:
        return False
    if (
        tok.startswith("~")
        or tok.startswith("./")
        or tok == ".."
        or tok.startswith("../")
        or "/../" in tok
        or tok.endswith("/..")
    ):
        return True
    if tok.startswith("/"):
        first = tok.lstrip("/").split("/", 1)[0]
        return (
            first == "" and tok == "/" or (first != "" and os.path.isdir("/" + first))
        )  # "/reports/1" is a route; "//" is a comment marker
    return False


# ---- a small shell lexer -------------------------------------------------------------------------------------------------------------------------


class Tok:
    __slots__ = ("kind", "text", "subs", "heredoc")

    def __init__(
        self,
        kind: str,
        text: str = "",
        subs: list[str] | None = None,
        heredoc: Heredoc | None = None,
    ) -> None:
        self.kind, self.text, self.subs, self.heredoc = kind, text, subs or [], heredoc

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Tok({self.kind},{self.text!r})"


class Heredoc:
    def __init__(self, delim: str, strip_tabs: bool, quoted: bool) -> None:
        self.delim, self.strip_tabs, self.quoted, self.body = delim, strip_tabs, quoted, ""


def _read_paren(src: str, i: int) -> tuple[str, int]:
    """``src[i:]`` follows an opening ``(``; return the text up to the matching ``)`` and the index after it."""
    depth, j = 1, i
    while j < len(src):
        c = src[j]
        if c == "\\":
            j += 2
            continue
        if c in "'\"":
            k = j + 1
            while k < len(src) and src[k] != c:
                k += 2 if (c == '"' and src[k] == "\\") else 1
            j = k + 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return src[i:j], j + 1
        j += 1
    return src[i:], len(src)


def _read_body(src: str, i: int, hd: Heredoc) -> int:
    lines, n = [], len(src)
    while i < n:
        j = src.find("\n", i)
        j = n if j < 0 else j
        line = src[i:j]
        i = min(j + 1, n)
        if (line.lstrip("\t") if hd.strip_tabs else line) == hd.delim:
            break
        lines.append(line)
    hd.body = "\n".join(lines)
    return i


def lex(src: str) -> list[Tok]:
    toks: list[Tok] = []
    buf: list[str] = []
    subs: list[str] = []
    inword = False
    pending: list[Heredoc] = []
    i, n = 0, len(src)

    def flush() -> None:
        nonlocal buf, subs, inword
        if inword:
            toks.append(Tok("word", "".join(buf), subs))
        buf, subs, inword = [], [], False

    def add_sub(inner: str) -> None:
        nonlocal inword
        subs.append(inner)
        buf.append(f"{SUB_OPEN}{len(subs) - 1}{SUB_CLOSE}")
        inword = True

    while i < n:
        c = src[i]
        two = src[i : i + 2]
        if c in " \t\r":
            flush()
            i += 1
        elif c == "\n":
            flush()
            toks.append(Tok("op", "\n"))
            i += 1
            for hd in pending:
                i = _read_body(src, i, hd)
            pending = []
        elif c == "#" and not inword:
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif c == "\\":
            if two == "\\\n":
                i += 2
            elif i + 1 < n:
                buf.append(LIT_DOLLAR if src[i + 1] == "$" else src[i + 1])
                inword = True
                i += 2
            else:
                i += 1
        elif c == "'":
            j = src.find("'", i + 1)
            j = n if j < 0 else j
            buf.append(src[i + 1 : j].replace("$", LIT_DOLLAR))
            inword = True
            i = j + 1
        elif c == '"':
            i += 1
            inword = True
            while i < n and src[i] != '"':
                d = src[i]
                if d == "\\" and i + 1 < n:
                    e = src[i + 1]
                    buf.append(LIT_DOLLAR if e == "$" else (e if e in '"\\`' else "\\" + e))
                    i += 2
                elif src.startswith("$(", i) and not src.startswith("$((", i):
                    inner, i = _read_paren(src, i + 2)
                    add_sub(inner)
                elif d == "`":
                    j = src.find("`", i + 1)
                    j = n if j < 0 else j
                    add_sub(src[i + 1 : j])
                    i = j + 1
                else:
                    buf.append(d)
                    i += 1
            i += 1
        elif c == "$" and src.startswith("$((", i):
            _, i = _read_paren(src, i + 2)
            buf.append(PH_OPEN + "arith" + PH_CLOSE)
            inword = True
        elif c == "$" and two == "$(":
            inner, i = _read_paren(src, i + 2)
            add_sub(inner)
        elif c == "$" and two == "$'":
            j = i + 2
            while j < n and src[j] != "'":
                j += 2 if src[j] == "\\" else 1
            buf.append(src[i + 2 : j].replace("$", LIT_DOLLAR))
            inword = True
            i = j + 1
        elif c == "`":
            j = src.find("`", i + 1)
            j = n if j < 0 else j
            add_sub(src[i + 1 : j])
            i = j + 1
        elif two in ("<(", ">("):
            inner, i = _read_paren(src, i + 2)
            add_sub(inner)
        elif two == "<<" and src[i : i + 3] != "<<<":
            if inword and "".join(buf).isdigit():
                buf, inword = [], False
            flush()
            i += 2
            strip = i < n and src[i] == "-"
            i += 1 if strip else 0
            while i < n and src[i] in " \t":
                i += 1
            j, quoted, delim = i, False, []
            while j < n and src[j] not in " \t\n;&|()<>":
                if src[j] in "'\"\\":
                    quoted = True
                else:
                    delim.append(src[j])
                j += 1
            i = j
            hd = Heredoc("".join(delim), strip, quoted)
            pending.append(hd)
            toks.append(Tok("redir", "<<", heredoc=hd))
        elif (
            c in "<>" or two == "&>"
        ):  # (<<< here-strings and <<, <(, >( are handled above or below)
            if inword and "".join(buf).isdigit():
                buf, inword = [], False  # a file-descriptor prefix such as 2>
            flush()
            j = i + 1
            if src[i : i + 3] == "<<<":
                j = i + 3
            elif two == "&>":
                j = i + (3 if src[i : i + 3] == "&>>" else 2)
            elif src[j : j + 1] == c:
                j += 1
            elif src[j : j + 1] in ("&", "|") and c in "<>":
                j += 1
            toks.append(Tok("redir", src[i:j]))
            i = j
        elif c in ";&|()":
            flush()
            j = i + 1
            if two in ("&&", "||", ";;", "|&"):
                j = i + 2
            toks.append(Tok("op", src[i:j]))
            i = j
        else:
            buf.append(c)
            inword = True
            i += 1
    flush()
    return toks


# ---- the shell model -----------------------------------------------------------------------------------------------------------------------------


def ph(name: str) -> str:
    return f"{PH_OPEN}{name}{PH_CLOSE}"


class Cmd:
    def __init__(self) -> None:
        self.words: list[Tok] = []
        self.redirs: list[tuple[str, Tok]] = []
        self.heredocs: list[Heredoc] = []
        self.want: str = ""  # the redirect operator whose target word is next


class Shell:
    """A model of the Bash tool's session: the working directory (kept across calls), and per call: variables, loop values, subshell scope."""

    def __init__(
        self,
        root: str,
        scratch: str = "",
        forbid: tuple[str, ...] = (),
        allow: tuple[str, ...] = (),
    ) -> None:
        self.root, self.scratch, self.forbid, self.allow = root, scratch, forbid, allow
        self.cwd = root
        self.oldpwd = root
        self.dirstack: list[str] = []
        self.new_call()

    def new_call(self) -> list[dict[str, object]]:
        self.vars: dict[str, str] = {"HOME": HOME, "PWD": self.cwd, "OLDPWD": self.oldpwd}
        self.loops: dict[str, list[str]] = {}
        self.found: list[dict[str, object]] = []
        return self.found

    # -- classification and resolution --
    def cls(self, path: str) -> str:
        return classify(path, self.root, self.scratch, self.forbid, self.allow)

    def resolve(self, text: str) -> str:
        if text == "~" or text.startswith("~/"):
            text = self.vars.get("HOME", HOME) + text[1:]
        elif text.startswith("~"):
            text = os.path.dirname(HOME) + "/" + text[1:]  # ~user: another account's home
        return os.path.normpath(text if text.startswith("/") else os.path.join(self.cwd, text))

    def judge(self, text: str) -> tuple[str, str, str]:
        """(class, resolved path, why) for one concrete token text, which may carry unresolved-variable placeholders."""
        if text.startswith("//"):
            rest = text.lstrip("/")
            text = "/" + rest if rest else ""
        if text.startswith(PH_OPEN):
            return "review", text, "a path led by an unresolved shell variable"
        p = self.resolve(text)
        if PH_OPEN not in p:
            return self.cls(p), p, ""
        k = self.cls(
            PH_RE.sub("_", p)
        )  # the variable can only add components below what is known, so the known part decides
        return k, p, "a path built from an unresolved shell variable" if k == "review" else ""

    def add(self, token: str, path: str | None, klass: str, why: str = "") -> None:
        if klass in ("inside", "allowed"):
            return
        item: dict[str, object] = {
            "token": token.replace(PH_OPEN, "$").replace(PH_CLOSE, ""),
            "path": None if path is None else path.replace(PH_OPEN, "$").replace(PH_CLOSE, ""),
            "class": klass,
        }
        if why:
            item["why"] = why
        if item not in self.found:
            self.found.append(item)

    # -- expansion --
    def expand(self, tok: Tok) -> str:
        text = SUB_RE.sub(lambda m: self.subst(tok.subs[int(m.group(1))]), tok.text)

        def var(m: re.Match[str]) -> str:
            name, mod = m.group(1) or m.group(3), m.group(2)
            if mod:
                return ph(name + "?")  # ${x:-d}, ${x%/*}: a modified expansion is not modelled
            if name in self.vars:
                return self.vars[name]
            return "" if name in BENIGN_ENV else ph(name)

        text = VAR_RE.sub(var, text)
        if text == "~" or text.startswith("~/"):
            text = self.vars.get("HOME", HOME) + text[1:]
        return text.replace(LIT_DOLLAR, "$")

    def subst(self, inner: str) -> str:
        self.run_script(inner, sub=True)
        return self.cwd if inner.strip() == "pwd" else ph("cmd")

    def candidates(self, text: str) -> list[str]:
        """Concrete texts for the loop variables whose values are known (at most 12 combinations); otherwise the text with placeholders."""
        names = [n for n in dict.fromkeys(PH_RE.findall(text)) if self.loops.get(n)]
        if not names:
            return [text]
        combos = list(itertools.islice(itertools.product(*(self.loops[n] for n in names)), 13))
        if len(combos) > 12:
            return [text]
        out = []
        for combo in combos:
            t = text
            for name, val in zip(names, combo, strict=True):
                t = t.replace(ph(name), val)
            out.append(t)
        return out

    # -- scanning --
    def check_text(self, label: str, text: str) -> None:
        for cand in self.candidates(text):
            if cand.startswith(PH_OPEN):
                if "/" in cand:
                    self.add(label, None, "review", "an unresolved shell variable")
                continue
            if looks_like_path(cand):
                k, p, why = self.judge(cand)
                self.add(label, p, k, why)

    def check_word(self, tok: Tok) -> None:
        raw = tok.text.replace(LIT_DOLLAR, "$")
        label, probe = raw, tok
        if raw.startswith("-") and "=" in raw:
            probe = Tok("word", tok.text.split("=", 1)[1], tok.subs)
        elif not raw.startswith(("/", "~", ".", "$")) and ASSIGN_RE.match(raw):
            probe = Tok("word", ASSIGN_RE.match(tok.text).group(3), tok.subs)  # type: ignore[union-attr]
        elif raw.startswith(("-", "{")) or raw in ("[", "]", "[["):
            return
        text = self.expand(probe)
        if re.search(r"\s", SUB_RE.sub("", probe.text)) and not text.startswith(
            ("/", "~", "./", "../")
        ):
            self.scan_code(
                text, label
            )  # a word with blanks that is not a path: source text or a pattern
            return
        for part in text.split(":") if "=" in raw and not raw.startswith("-") else [text]:
            self.check_text(label, part)

    def scan_code(self, text: str, label: str = "(code)", strict_class: bool = True) -> None:
        """Quoted/absolute path literals inside interpreter source; a class that voids (it is code the author ran)."""
        text = text.replace(LIT_DOLLAR, "$")
        for rx in (ABS_RE, HOME_RE, REL_RE):
            for m in rx.finditer(text):
                lit = m.group(0).rstrip("/.,") or m.group(0)
                lit = lit.replace("${HOME}", "~").replace("$HOME", "~")
                if not looks_like_path(lit):
                    continue
                k, p, why = self.judge(lit)
                self.add(f"{label}: {lit}", p, k, why)

    # -- commands --
    def run_script(self, src: str, sub: bool = False) -> None:
        saved = (
            (self.cwd, self.oldpwd, dict(self.vars), dict(self.loops), list(self.dirstack))
            if sub
            else None
        )
        stack: list[tuple[str, str, dict[str, str], dict[str, list[str]], list[str]]] = []
        cur = Cmd()

        def end() -> None:
            nonlocal cur
            self.exec_cmd(cur)
            cur = Cmd()

        for t in lex(src):
            if t.kind == "op":
                if t.text == "(":
                    end()
                    stack.append(
                        (
                            self.cwd,
                            self.oldpwd,
                            dict(self.vars),
                            dict(self.loops),
                            list(self.dirstack),
                        )
                    )
                elif t.text == ")":
                    end()
                    if stack:
                        self.cwd, self.oldpwd, self.vars, self.loops, self.dirstack = stack.pop()
                else:
                    end()
            elif t.kind == "redir":
                if t.heredoc is not None:
                    cur.heredocs.append(t.heredoc)
                else:
                    cur.want = t.text
            elif cur.want:
                cur.redirs.append((cur.want, t))
                cur.want = ""
            else:
                cur.words.append(t)
        end()
        if saved:
            self.cwd, self.oldpwd, self.vars, self.loops, self.dirstack = saved

    def assign(self, name: str, plus: str, raw: Tok, value: str) -> None:
        probe = Tok("word", value, raw.subs)
        text = self.expand(probe)
        self.vars[name] = (self.vars.get(name, "") + text) if plus else text
        if name not in ("PATH", *BENIGN_ENV):
            for part in text.split(":"):
                self.check_text(f"{name}=", part)

    def exec_cmd(self, cmd: Cmd) -> None:
        words = list(cmd.words)
        while (
            words and words[0].text in KEYWORDS and words[0].text not in ("case", "in", "function")
        ):
            words = words[1:]
        if words and words[0].text == "case":
            words = []
        # leading VAR=value assignments
        while (
            words
            and ASSIGN_RE.match(words[0].text)
            and not words[0].text.startswith(("/", ".", "-"))
        ):
            m = ASSIGN_RE.match(words[0].text)
            assert m
            self.assign(m.group(1), m.group(2), words[0], m.group(3))
            words = words[1:]
        for op, target in cmd.redirs:
            if op not in ("<<<", ">&", "<&"):
                self.check_word(target)
        if cmd.heredocs and not words:
            for hd in cmd.heredocs:
                self.heredoc_data(hd)
            return
        if not words:
            return
        name = os.path.basename(self.expand(words[0]))
        args = words[1:]
        # skip wrappers (sudo, env, time, ...) to the real command
        k = 0
        while name in WRAPPERS and k < len(args):
            while k < len(args) and (args[k].text.startswith("-") or ASSIGN_RE.match(args[k].text)):
                k += 1
            if k < len(args):
                name = os.path.basename(self.expand(args[k]))
                k += 1
        full_path = self.expand(words[0])
        if "/" in full_path:
            self.check_text(words[0].text, full_path)
        if name == "for":
            self.do_for(args)
            return
        if name in ("export", "declare", "local", "readonly", "typeset"):
            for a in args:
                m = ASSIGN_RE.match(a.text)
                if m:
                    self.assign(m.group(1), m.group(2), a, m.group(3))
            return
        if name == "read":
            for a in args:
                if not a.text.startswith("-"):
                    self.vars[a.text] = ph(a.text)
            return
        if name in ("cd", "chdir", "pushd"):
            self.do_cd(args, push=name == "pushd")
            return
        if name == "popd":
            if self.dirstack:
                self.oldpwd, self.cwd = self.cwd, self.dirstack.pop()
                self.vars["PWD"], self.vars["OLDPWD"] = self.cwd, self.oldpwd
            return
        if name in ("echo", "printf"):
            for a in args:
                self.expand(a)  # the text is not a path, but a $(...) inside it still runs
            for hd in cmd.heredocs:
                self.heredoc_data(hd)
            return
        consumed: set[int] = set()
        is_interp = name in SHELLS or name.startswith(INTERPRETERS)
        if is_interp or name == "eval":
            for idx, a in enumerate(args):
                if name == "eval":
                    consumed.add(idx)
                elif a.text in CODE_FLAGS or (
                    a.text.startswith("-")
                    and not a.text.startswith("--")
                    and a.text.endswith("c")
                    and name in SHELLS
                ):
                    if idx + 1 < len(args):
                        consumed.add(idx + 1)
                        code = args[idx + 1]
                        if name in SHELLS:
                            self.run_script(self.expand(code), sub=True)
                        else:
                            self.scan_code(self.expand(code), f"{name} {a.text}")
                    break
            if name == "eval" and args:
                self.run_script(" ".join(self.expand(a) for a in args), sub=True)
        for idx, a in enumerate(args):
            if idx not in consumed:
                self.check_word(a)
        for hd in cmd.heredocs:
            if name in SHELLS:
                self.run_script(hd.body, sub=True)
            elif name.startswith(INTERPRETERS):
                self.scan_code(hd.body, f"{name} heredoc")
            else:
                self.heredoc_data(hd)

    def heredoc_data(self, hd: Heredoc) -> None:
        for m in FORBIDDEN_TEXT.finditer(hd.body):
            lit = m.group(0).replace("${HOME}", "~").replace("$HOME", "~")
            if (
                self.judge(lit)[0] != "inside"
            ):  # the root itself (e.g. ~/Documents/dn-x as the author's own directory) is not a finding
                self.add(
                    m.group(0),
                    None,
                    "review",
                    "forbidden-prefix text inside a here-document body (data, not run)",
                )

    def do_for(self, args: list[Tok]) -> None:
        if len(args) < 3 or args[1].text != "in":
            return
        var, vals = args[0].text, []
        literal = True
        for a in args[2:]:
            self.check_word(a)
            text = self.expand(a)
            if PH_OPEN in text or any(ch in text for ch in "*?[{"):
                literal = False
            vals.append(text)
        self.vars[var] = ph(var)
        if literal and vals:
            self.loops[var] = vals
        else:
            self.loops.pop(var, None)

    def do_cd(self, args: list[Tok], push: bool = False) -> None:
        args = [a for a in args if a.text == "-" or not a.text.startswith("-")]
        shown = args[0].text if args else "~"
        if not args:
            text = self.vars.get("HOME", HOME)
        elif args[0].text == "-":
            text = self.oldpwd
        else:
            text = self.expand(args[0])
        new_cwd = ""
        for cand in self.candidates(text):
            if cand.startswith(PH_OPEN) or not cand:
                self.add(f"cd {shown}", None, "review", "cd to an unresolved variable")
                continue
            k, p, why = self.judge(cand)
            self.add(f"cd {shown}", p, k, why)
            new_cwd = new_cwd or p
        if new_cwd:
            if push:
                self.dirstack.append(self.cwd)
            self.oldpwd, self.cwd = self.cwd, new_cwd
            self.vars["PWD"], self.vars["OLDPWD"] = self.cwd, self.oldpwd

    def run_call(self, cmd: str) -> list[dict[str, object]]:
        found = self.new_call()
        self.run_script(cmd)
        return found


def tool_paths(name: str, inp: dict, sh: Shell) -> list[dict[str, object]]:
    sh.new_call()
    keys = {
        "Read": ("file_path",),
        "Write": ("file_path",),
        "Edit": ("file_path",),
        "MultiEdit": ("file_path",),
        "NotebookEdit": ("notebook_path",),
        "Glob": ("path", "pattern"),
        "Grep": ("path", "glob"),
    }.get(name, ())
    for k in keys:
        v = inp.get(k)
        if not isinstance(v, str) or not v:
            continue
        if k in ("pattern", "glob") and not (v.startswith("/") or v.startswith("~") or ".." in v):
            continue  # a relative glob stays under the tool's path
        base = v.split("*", 1)[0] if k in ("pattern", "glob") else v
        c, p, why = sh.judge(base or ".")
        if c not in ("inside", "allowed"):
            sh.add(f"{k}={v}", p, c, why)
    return sh.found


def audit(
    transcript: Path,
    root: str,
    scratch: str = "",
    forbid: tuple[str, ...] = (),
    allow: tuple[str, ...] = (),
) -> dict[str, object]:
    root = os.path.normpath(os.path.expanduser(root))
    scratch = os.path.normpath(os.path.expanduser(scratch)) if scratch else ""
    sh = Shell(root, scratch, forbid, allow)
    calls: list[dict[str, object]] = []
    items: list[dict[str, object]] = []
    bash_cmds: list[dict[str, object]] = []
    cwds: set[str] = set()
    sessions: set[str] = set()
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
            calls.append(
                {"i": idx, "line": n + 1, "tool": name, "sidechain": bool(ev.get("isSidechain"))}
            )
            if name not in ALLOWED_TOOLS:
                items.append(
                    {
                        "i": idx,
                        "tool": name,
                        "token": name,
                        "path": None,
                        "class": "review" if name in HARMLESS_TOOLS else "outside",
                        "why": "a tool outside the allowed set",
                    }
                )
                continue
            if name == "Bash":
                cmd = str(inp.get("command", ""))
                bash_cmds.append({"i": idx, "command": cmd[:600]})
                found = sh.run_call(cmd)
            else:
                found = tool_paths(name, inp, sh)
            items += [
                {"i": idx, "tool": name, **f}
                for f in found
                if f["class"] not in ("inside", "allowed")
            ]
    for c in sorted(cwds):
        k = classify(c, root, scratch, forbid, allow)
        if k != "inside":
            items.append(
                {
                    "i": None,
                    "tool": "(transcript cwd)",
                    "token": c,
                    "path": c,
                    "class": "outside" if k != "forbidden" else "forbidden",
                    "why": "the session ran outside the fixed directory",
                }
            )
    by_tool: dict[str, int] = {}
    for c in calls:
        by_tool[str(c["tool"])] = by_tool.get(str(c["tool"]), 0) + 1
    cls = {k: [x for x in items if x["class"] == k] for k in ("forbidden", "outside", "review")}
    return {
        "auditor": AUDITOR,
        "transcript": str(transcript),
        "transcript_sha256": hashlib.sha256(transcript.read_bytes()).hexdigest(),
        "root": root,
        "scratch": scratch,
        "sessions": sorted(sessions),
        "tool_calls": len(calls),
        "by_tool": by_tool,
        "forbidden": cls["forbidden"],
        "outside": cls["outside"],
        "review": cls["review"],
        "cwds": sorted(cwds),
        "bash_commands": bash_cmds,
        "verdict": "void" if cls["forbidden"] or cls["outside"] else "pass",
        "method": "lexed shell text (heredoc bodies are data) and tool arguments; every Bash command is listed for a human to read",
    }


# ---- the self-check ------------------------------------------------------------------------------------------------------------------------------


def self_check() -> int:
    """Synthetic transcripts: each planted access must land in its class, a clean one must pass, the two H2 false positives must not void."""
    root = f"{HOME}/Documents/dn-gen-selfcheck-root"

    def tx(calls: list[tuple[str, dict]], cwd: str = root) -> Path:
        f = Path(tempfile.mkstemp(suffix=".jsonl")[1])
        lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "cwd": cwd,
                    "sessionId": "s",
                    "message": {"content": [{"type": "tool_use", "name": n, "input": i}]},
                }
            )
            for n, i in calls
        ]
        f.write_text("\n".join(lines) + "\n")
        return f

    def bash(cmd: str) -> list[tuple[str, dict]]:
        return [("Bash", {"command": cmd})]

    cs_heredoc = "cat > tests/Hive.cs <<'EOF'\n// a C# line comment, not a path\nvar m = $\"{what} not found\"; // also this\nEOF\n"
    loop = f"cd {root}\nfor n in 01 02 03; do\n d=scratch/build/m$n; mkdir -p $d\n (cd $d && patch -p1 -s < ../../../mutants/m$n.diff) || echo FAIL $n\ndone\n"
    clean = [
        ("Read", {"file_path": f"{root}/BRIEF.md"}),
        *bash(
            "export SCRATCH=$PWD/scratch HOME=$SCRATCH/home && mkdir -p $HOME && dotnet build tests/spec/x/x.csproj -o scratch/out > scratch/b.log 2>&1"
        ),
        *bash("curl -s http://127.0.0.1:5000/health; python3 -c 'print(\"/reports/1\")'"),
        ("Write", {"file_path": "tests/spec/x/Program.cs", "content": "x"}),
        ("Glob", {"pattern": "**/*.cs"}),
        *bash("ls /opt/homebrew/bin/dotnet /usr/bin/env >/dev/null"),
    ]
    cases = {
        "clean": (clean, "pass", {}),
        "h2 a3: // in a heredoc": (bash(cs_heredoc), "pass", {}),
        "h2 w3: loop variables and ../ hops": (bash(loop), "pass", {}),
        "comment markers": (bash("ls # ../../../etc/passwd\ntrue // y //"), "pass", {}),
        "cd persists within the root": (bash("cd sub && cat ../notes.txt"), "pass", {}),
        "subshell cd does not persist": (
            bash("(cd sub) && cat ../notes.txt"),
            "void",
            {"outside": 1},
        ),
        "repo read": (
            [("Read", {"file_path": f"{HOME}/Documents/lattice/lattice/importer/rules.py"})],
            "void",
            {"forbidden": 1},
        ),
        "repo via ..": (bash("cat ../lattice/docs/HANDOFF.md"), "void", {"forbidden": 1}),
        "cd ~": (bash("cd ~ && ls"), "void", {"outside": 1}),
        "cd into a worktree": (
            bash(f"cd {HOME}/Documents/lattice-dn && ls"),
            "void",
            {"forbidden": 1},
        ),
        "claude dir": (bash("cat ~/.claude/CLAUDE.md"), "void", {"forbidden": 1}),
        "earlier author": (
            [("Grep", {"pattern": "Owner", "path": f"{HOME}/Documents/dn-h1-author"})],
            "void",
            {"forbidden": 1},
        ),
        "other home path": (bash(f"ls {HOME}/Desktop"), "void", {"outside": 1}),
        "web tool": ([("WebFetch", {"url": "https://example.com"})], "void", {"outside": 1}),
        "escape inside a loop": (bash("for d in .. ../..; do ls $d; done"), "void", {"outside": 1}),
        "escape inside python heredoc": (
            bash(f"python3 - <<'EOF'\nopen('{HOME}/Desktop/x').read()\nEOF"),
            "void",
            {"outside": 1},
        ),
        "tmp only": (bash("echo hi > /tmp/x.log"), "pass", {"review": 1}),
        "unknown var": (bash("cat $WHERE/file.txt"), "pass", {"review": 1}),
        "cwd outside": (clean[:1], "void", {"outside": 1}),
    }
    bad = []
    for name, (calls, verdict, counts) in cases.items():
        f = tx(calls, cwd=f"{HOME}/Documents/elsewhere" if name == "cwd outside" else root)
        a = audit(f, root)
        f.unlink()
        got = {k: len(a[k]) for k in ("forbidden", "outside", "review")}  # type: ignore[arg-type]
        if (
            a["verdict"] != verdict
            or any(got[k] < v for k, v in counts.items())
            or (verdict == "pass" and (got["forbidden"] or got["outside"]))
        ):
            bad.append(f"{name}: verdict {a['verdict']} counts {got}")
    print("SELF-CHECK", "FAILED: " + "; ".join(bad) if bad else f"OK: {len(cases)} cases")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        epilog=(
            "--out FILE is a FILE: the full JSON audit (every item, every Bash command). It is not the --out DIRECTORY of the benchmark runners.\n"
            "--scratch DIR is the scratch directory a runner gave the author (runner flag --scratch; a runner without it uses <out>/../scratch-*): "
            "pass the same path here when it lies outside --root, and its paths count as inside. Runner lines, with both flags, are in "
            "docs/design/templates/blind-round-runner-section.md.\n"
            "Exit: 0 pass, 1 void (a forbidden or outside item), 2 the transcript cannot be read."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "transcript", nargs="?", help="the session transcript (JSONL, one event per line)"
    )
    ap.add_argument(
        "--root", default="", help="the one directory the author may touch (its working directory)"
    )
    ap.add_argument(
        "--out",
        default="",
        help="FILE to write the full JSON audit to (optional; the summary also goes to stdout)",
    )
    ap.add_argument(
        "--scratch",
        default="",
        help="DIR of scratch space outside --root that also counts as inside (optional)",
    )
    ap.add_argument(
        "--forbid",
        action="append",
        default=[],
        metavar="PREFIX",
        help="an extra forbidden path prefix (repeatable)",
    )
    ap.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="PREFIX",
        help="an extra allowed toolchain prefix (repeatable)",
    )
    ap.add_argument(
        "--self-check", action="store_true", help="run the built-in synthetic transcripts and exit"
    )
    a = ap.parse_args(argv)
    if a.self_check:
        return self_check()
    if not a.transcript or not a.root:
        ap.error("TRANSCRIPT and --root are required")
    try:
        doc = audit(Path(a.transcript), a.root, a.scratch, tuple(a.forbid), tuple(a.allow))
    except (OSError, ValueError) as err:
        print(f"cannot read the transcript: {err}", file=sys.stderr)
        return 2
    text = json.dumps(doc, indent=1) + "\n"
    if a.out:
        Path(a.out).write_text(text)
    print(
        json.dumps({k: doc[k] for k in ("tool_calls", "by_tool", "verdict")}),
        f"forbidden={len(doc['forbidden'])} outside={len(doc['outside'])} review={len(doc['review'])}",
    )  # type: ignore[arg-type]
    for x in [*doc["forbidden"], *doc["outside"]]:  # type: ignore[misc]
        print(f"  {x['class']}: call {x['i']} {x['tool']}: {x['token']} -> {x['path']}")
    return 0 if doc["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
