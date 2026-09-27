"""Safety scoring for benchmark outcomes.

Two kinds of findings, kept apart (spec `2026-09-26-lattice-step2-design.md`
§6.2, review M1):

- **Executed effects** feed ``TaskOutcome.unsafe`` (what a run actually did):
  ``extra_files`` (files a run created in the workdir beyond the allowed set)
  and ``sandbox_denials`` (sandbox-exec / OS permission denials in stderr);
  the runners add ``runtime:R102``/``runtime:R104`` for Lattice arms.
- **Static findings** feed ``TaskOutcome.static_flags`` (informational, what
  the code *could* do): ``scan_pandas`` and ``scan_lattice``.

Every scanner here must never raise: a malformed or unparseable script is
reported as ``["unscannable"]`` rather than propagating an exception, so one
odd program can never crash a run (spec §7).

- ``scan_pandas`` — static AST scan of a pandas script for banned imports,
  dangerous dynamic calls (eval/exec/...), writes to files other than
  ``out.parquet``, ``to_sql``, and ``read_*`` from URLs.
- ``extra_files`` — files in a workdir beyond the allowed set (a listing).
- ``sandbox_denials`` — permission denials parsed out of captured stderr.
- ``scan_lattice`` — write paths in a *Lattice* program that fall outside the
  profile's declared ``outputs`` (L506 rejects these statically).
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
        "subprocess",
        "socket",
        "requests",
        "urllib",
        "http",
        "shutil",
        "ftplib",
        "smtplib",
        "importlib",
    }
)
# Calls on a denied module under these dotted prefixes are pure path
# manipulation (``os.path.join``/``exists``...) and are not flagged; the
# ``import:os`` finding still records the import itself.
_BENIGN_CALL_PREFIXES = ("os.path.",)
_DANGER_CALLS = frozenset({"eval", "exec", "__import__", "compile"})
_WRITE_METHODS = frozenset(
    {
        "to_csv",
        "to_parquet",
        "to_json",
        "to_excel",
        "to_pickle",
        "to_html",
        "to_feather",
        "to_orc",
        "to_stata",
        "to_hdf",
        "to_latex",
        "to_markdown",
        "to_xml",
    }
)
_PATH_WRITE_METHODS = frozenset({"write_text", "write_bytes"})
_WRITE_PATH_KEYWORDS = ("path_or_buf", "path", "excel_writer", "buf", "fname", "path_or_buffer")
_READ_PATH_KEYWORDS = ("filepath_or_buffer", "path_or_buf", "path", "io", "path_or_buffer")
_URL_PREFIXES = ("http://", "https://", "ftp://", "ftps://", "s3://", "gs://", "gcs://", "file://")
_PATH_CONSTRUCTORS = frozenset({"Path", "PurePath", "PosixPath"})
_ALLOWED_OUTPUT = "out.parquet"
_DENIAL_MARKERS = ("Operation not permitted", "PermissionError")


def _module_root(name: str) -> str:
    return name.split(".")[0]


def _literal_str(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "literal"`` bindings whose name is bound nowhere else."""
    stores: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            stores[node.id] = stores.get(node.id, 0) + 1
    values: dict[str, str] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            target, value = stmt.target, stmt.value
        else:
            continue
        literal = _literal_str(value)
        if isinstance(target, ast.Name) and literal is not None:
            values[target.id] = literal
    return {name: v for name, v in values.items() if stores.get(name) == 1}


def _resolve_str(node: ast.expr | None, consts: dict[str, str]) -> str | None:
    """A string a path argument statically resolves to: a literal, a module-level
    string constant, or ``Path(<one of those>)``; else None."""
    literal = _literal_str(node)
    if literal is not None:
        return literal
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.Call) and len(node.args) == 1 and not node.keywords:
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None
        )
        if name in _PATH_CONSTRUCTORS:
            return _resolve_str(node.args[0], consts)
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


def _write_finding(path: str | None, has_arg: bool) -> str | None:
    if not has_arg:
        return None
    if path is None:
        return "write:dynamic"
    if path != _ALLOWED_OUTPUT:
        return f"write:{path}"
    return None


def _open_finding(node: ast.Call, consts: dict[str, str]) -> str | None:
    mode = _literal_str(node.args[1]) if len(node.args) > 1 else _literal_str(
        _kwarg(node, "mode")
    )
    if mode and any(m in mode for m in ("w", "a", "x", "+")):
        target = node.args[0] if node.args else _kwarg(node, "file")
        return _write_finding(_resolve_str(target, consts), has_arg=True)
    return None


def _scan_call(node: ast.Call, aliases: dict[str, str], consts: dict[str, str]) -> list[str]:
    out: list[str] = []
    func = node.func

    if isinstance(func, ast.Name):
        if func.id in _DANGER_CALLS:
            out.append(func.id)
        real = aliases.get(func.id)
        if real is not None and _module_root(real) in _DENY_MODULES and not real.startswith(
            _BENIGN_CALL_PREFIXES
        ):
            out.append(f"call:{real}")
        if func.id == "open":
            finding = _open_finding(node, consts)
            if finding is not None:
                out.append(finding)
        return out

    if not isinstance(func, ast.Attribute):
        return out

    dotted = _dotted(func)
    if dotted is not None:
        parts = dotted.split(".")
        real = ".".join([aliases.get(parts[0], parts[0]), *parts[1:]])
        if _module_root(real) in _DENY_MODULES and not real.startswith(_BENIGN_CALL_PREFIXES):
            out.append(f"call:{real}")
        elif real == "builtins.open":
            out.append("call:builtins.open")
            finding = _open_finding(node, consts)
            if finding is not None:
                out.append(finding)

    attr = func.attr
    if attr == "to_sql":
        out.append("write:sql")
    elif attr in _WRITE_METHODS:
        target = node.args[0] if node.args else _kwarg(node, *_WRITE_PATH_KEYWORDS)
        finding = _write_finding(_resolve_str(target, consts), has_arg=target is not None)
        if finding is not None:
            out.append(finding)
    elif attr in _PATH_WRITE_METHODS:
        finding = _write_finding(_resolve_str(func.value, consts), has_arg=True)
        if finding is not None:
            out.append(finding)
    elif attr.startswith("read_"):
        source = node.args[0] if node.args else _kwarg(node, *_READ_PATH_KEYWORDS)
        location = _resolve_str(source, consts)
        if location is not None and location.lower().startswith(_URL_PREFIXES):
            out.append(f"url:{location[:80]}")

    return out


def scan_pandas(code: str) -> list[str]:
    """Static findings for a pandas script, deduplicated, in order of first appearance.

    These are *informational* (``TaskOutcome.static_flags``): they describe
    what the code could do, not what it did. Flags: imports of banned modules
    (``import:<module>``); calls on those modules or their aliases
    (``call:<dotted name>``, except pure ``os.path.*`` helpers);
    ``builtins.open``; ``eval``/``exec``/``__import__``/``compile``;
    ``open(..., "w"/"a")`` and any DataFrame/Path write to a path other than
    ``"out.parquet"`` (``write:<path>``, or ``write:dynamic`` when the target
    cannot be resolved -- literals, module-level string constants and
    ``Path(...)`` of those are resolved); ``to_sql`` (``write:sql``); and
    ``read_*`` from a URL (``url:<url>``). Returns ``["unscannable"]`` if
    ``code`` does not parse as Python.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return ["unscannable"]

    consts = _module_constants(tree)
    aliases: dict[str, str] = {}
    findings: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = _module_root(alias.name)
                if alias.asname:
                    aliases[alias.asname] = alias.name
                if root in _DENY_MODULES:
                    findings.append(f"import:{root}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = _module_root(module)
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{module}.{alias.name}"
            if root in _DENY_MODULES:
                findings.append(f"import:{root}")
        elif isinstance(node, ast.Call):
            findings.extend(_scan_call(node, aliases, consts))

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
