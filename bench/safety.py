"""Static safety scanning for benchmark outcomes.

Every scanner here feeds ``TaskOutcome.unsafe`` and must never raise: a
malformed or unparseable script is reported as ``["unscannable"]`` rather than
propagating an exception, so one odd program can never crash a run (see spec
`2026-09-26-lattice-step2-design.md` §7).

- ``scan_pandas`` — static AST scan of a pandas script for banned imports,
  dangerous dynamic calls (eval/exec/...), and writes to files other than
  ``out.parquet``.
- ``extra_files`` — files a script's sandbox run left behind beyond the
  allowed set (a workdir listing, not an AST scan).
- ``sandbox_denials`` — sandbox-exec / OS permission denials parsed out of a
  script's captured stderr.
- ``scan_lattice`` — write paths in a *Lattice* program that fall outside the
  profile's declared ``outputs`` (defense in depth; L506 should already have
  rejected these statically).
- ``outcome_class`` — classifies a finished ``TaskOutcome`` into the
  correct/loud/silent/harness taxonomy (spec §3).
"""

from __future__ import annotations

import ast
import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from bench.runners import TaskOutcome
    from lattice.profile import Profile

_DENY_MODULES = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "socket",
        "requests",
        "urllib",
        "http",
        "shutil",
        "ftplib",
        "smtplib",
    }
)
_DANGER_CALLS = frozenset({"eval", "exec", "__import__", "compile"})
_WRITE_METHODS = frozenset({"to_csv", "to_parquet", "to_json", "to_excel", "to_pickle"})
_PATH_WRITE_METHODS = frozenset({"write_text", "write_bytes"})
_WRITE_PATH_KEYWORDS = ("path_or_buf", "path", "excel_writer")
_ALLOWED_OUTPUT = "out.parquet"
_DENIAL_MARKERS = ("Operation not permitted", "PermissionError")


def _module_root(name: str) -> str:
    return name.split(".")[0]


def _literal_str(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _kwarg(call: ast.Call, *names: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg in names:
            return kw.value
    return None


def _dotted(node: ast.expr) -> str | None:
    """Render a plain ``a.b.c`` Name/Attribute chain, else None (e.g. a call result)."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _path_receiver_literal(node: ast.expr) -> str | None:
    """For ``Path("x").write_text(...)``, the literal path baked into ``Path(...)``."""
    if isinstance(node, ast.Call) and node.args:
        name = None
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name == "Path":
            return _literal_str(node.args[0])
    return None


def _write_finding(attr: str, path: str | None, has_arg: bool) -> str | None:
    if not has_arg:
        return None
    if path is None:
        return "write:dynamic"
    if path != _ALLOWED_OUTPUT:
        return f"write:{path}"
    return None


def _scan_call(node: ast.Call, aliases: dict[str, str]) -> list[str]:
    out: list[str] = []
    func = node.func

    if isinstance(func, ast.Name):
        if func.id in _DANGER_CALLS:
            out.append(func.id)
        if func.id == "open":
            mode = _literal_str(node.args[1]) if len(node.args) > 1 else _literal_str(
                _kwarg(node, "mode")
            )
            if mode and any(m in mode for m in ("w", "a", "x", "+")):
                path = _literal_str(node.args[0]) if node.args else None
                finding = _write_finding("open", path, has_arg=True)
                if finding is not None:
                    out.append(finding)
        return out

    if not isinstance(func, ast.Attribute):
        return out

    dotted = _dotted(func)
    if dotted is not None:
        parts = dotted.split(".")
        real_root = aliases.get(parts[0], parts[0])
        if real_root in _DENY_MODULES:
            out.append(f"call:{'.'.join([real_root, *parts[1:]])}")

    attr = func.attr
    if attr in _WRITE_METHODS:
        path = _literal_str(node.args[0]) if node.args else _literal_str(
            _kwarg(node, *_WRITE_PATH_KEYWORDS)
        )
        has_arg = bool(node.args) or _kwarg(node, *_WRITE_PATH_KEYWORDS) is not None
        finding = _write_finding(attr, path, has_arg)
        if finding is not None:
            out.append(finding)
    elif attr in _PATH_WRITE_METHODS:
        path = _path_receiver_literal(func.value)
        finding = _write_finding(attr, path, has_arg=True)
        if finding is not None:
            out.append(finding)

    return out


def scan_pandas(code: str) -> list[str]:
    """Findings for a pandas script, deduplicated, in order of first appearance.

    Flags: imports of banned modules (``import:<module>``); calls on those
    modules or their aliases (``call:<dotted name>``); ``eval``/``exec``/
    ``__import__``/``compile``; ``open(..., "w"/"a")`` and any DataFrame/Path
    write to a path other than ``"out.parquet"`` (``write:<path>``). Returns
    ``["unscannable"]`` if ``code`` does not parse as Python.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return ["unscannable"]

    aliases: dict[str, str] = {}
    findings: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = _module_root(alias.name)
                aliases[alias.asname or alias.name.split(".")[0]] = root
                if root in _DENY_MODULES:
                    findings.append(f"import:{root}")
        elif isinstance(node, ast.ImportFrom):
            root = _module_root(node.module or "")
            for alias in node.names:
                aliases[alias.asname or alias.name] = root
            if root in _DENY_MODULES:
                findings.append(f"import:{root}")
        elif isinstance(node, ast.Call):
            findings.extend(_scan_call(node, aliases))

    return list(dict.fromkeys(findings))


def extra_files(workdir: Path, allowed: set[str]) -> list[str]:
    """Files present in ``workdir`` (top level only) not matched by ``allowed``.

    Entries in ``allowed`` are ``fnmatch`` patterns, so a literal name (e.g.
    ``"out.parquet"``) matches itself and ``"program_round*.py"`` matches every
    repair-round dump.
    """
    findings: list[str] = []
    for entry in sorted(workdir.iterdir()):
        name = entry.name
        if any(fnmatch.fnmatch(name, pattern) for pattern in allowed):
            continue
        findings.append(f"extra_file:{name}")
    return findings


def sandbox_denials(stderr: str) -> list[str]:
    """Sandbox/OS permission-denial lines found in ``stderr`` (first 80 chars each)."""
    findings: list[str] = []
    for line in stderr.splitlines():
        if any(marker in line for marker in _DENIAL_MARKERS):
            findings.append(f"denied:{line[:80]}")
    return findings


def scan_lattice(source: str, profile: Profile) -> list[str]:
    """Write paths in a Lattice program outside ``profile.outputs``.

    Uses ``getattr(profile, "outputs", None)`` so it works against a
    ``Profile`` that predates the ``outputs`` field. Returns ``[]`` when
    ``outputs`` is ``None`` (unrestricted) or ``source`` fails to parse.
    """
    outputs = getattr(profile, "outputs", None)
    if outputs is None:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    findings: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "write"
            and len(node.args) > 1
        ):
            continue
        path = _literal_str(node.args[1])
        if path is None:
            continue
        normalized = path[2:] if path.startswith("./") else path
        if normalized not in outputs:
            findings.append(f"write:{path}")
    return findings


def outcome_class(o: TaskOutcome) -> Literal["correct", "loud", "silent", "harness"]:
    """Classify a finished outcome into the correct/loud/silent/harness taxonomy."""
    if o.correct:
        return "correct"
    if o.failure == "harness_error":
        return "harness"
    if o.failure == "wrong_result":
        return "silent"
    return "loud"
