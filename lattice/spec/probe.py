"""``lattice.spec.probe``: an adversarial gap-finder for a drafted spec.

Given a spec's rule :class:`~lattice.spec.rules.Trace`, a passing app, and the shell
command that checks the app against the spec, :func:`probe` asks a second model, one
rule at a time, to patch the app so it **breaks that rule** in a user-observable way
while the check still passes. A patch the check does not catch is a *gap*: the spec
never really checked that rule, and the patch is the evidence. This mirrors how a human
reviewer would stress-test a spec -- not "does the app pass", but "can I break rule N
without the check noticing".

**The patch format the model must answer in.** The prompt asks for exactly one of:

1. One block per changed file, in this exact shape, nothing else before or after::

       FILE: <path, exactly as shown in a "--- path ---" header in the prompt>
       ```
       <the file's full new contents>
       ```

   repeated for every changed file. Full replacement contents, never a diff -- the
   model is not asked to produce something this module has to merge.
2. Or, if no change can break the rule while keeping the app passing and building, the
   single line ``IMPOSSIBLE: <why>``.

Anything else -- no ``FILE:`` block and no ``IMPOSSIBLE``, or a ``FILE:`` block naming a
path outside the app (absolute, or with a ``..`` segment) -- is treated the same as an
explicit refusal: :attr:`ProbeResult.status` ``"refused"``.

**Classification.** The patch is applied to a fresh copy of ``app_dir`` (never the
original) and ``check_cmd`` is run there, following the exit-code convention
``python -m lattice.spec check`` already uses (see :mod:`lattice.spec.__main__`: 0
pass, 1 fail, 2 could not run) -- which any ``check_cmd`` a caller supplies is expected
to follow, since that is what "a shell command, exit 0 = pass" means project-wide:

* exit 0 -- ``"gap"``: the check still passes, so it missed the violation.
* exit 1 -- ``"caught"``: the check ran and reported the violation.
* exit 2, any other exit code, or a timeout -- ``"broken"``: the patch did not build,
  crashed at startup, or the check errored before it could run its scenarios.
* the model refused or produced unusable output -- ``"refused"``.

``app_dir`` is only ever read, never written: every patch is applied to a temporary
copy (``.git``, ``.venv``, ``bin``, ``node_modules`` and ``__pycache__`` are not copied;
``.venv`` is symlinked back instead, so ``uv run`` works without reinstalling), and the
copy is removed once the rule's check has run.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lattice.llm.client import GenerationRefused, Generator, Usage
from lattice.spec.rules import Rule, Trace

__all__ = ["ProbeResult", "probe", "main"]

# Directories never copied into a probe's scratch app dir, and never sent to the model
# as "source the model may change" even if a --files glob would otherwise match inside
# them.
_SKIP_DIRS = {".git", ".venv", "bin", "node_modules", "__pycache__"}

# One block per changed file: "FILE: <path>" then a fenced code block with the file's
# full new contents. The language tag on the fence (if any) is ignored.
_FILE_BLOCK_RE = re.compile(
    r"^FILE:[ \t]*(?P<path>\S+)[ \t]*\r?\n```[^\n]*\r?\n(?P<body>.*?)\r?\n```[ \t]*$",
    re.MULTILINE | re.DOTALL,
)

# --signal: rules worth probing first because their wording promises something a sloppy
# check is likely to miss (an absolute bound, an exclusivity, a background effect).
SIGNAL_WORDS = ("never", "at most", "only", "unique", "exactly", "background", "loop")

# The total size (characters) of source sent in one prompt; past this, files are
# trimmed largest-first (see _select_files) and the model is told which were dropped.
MAX_PROMPT_CHARS = 60_000

MODEL_ALIASES: dict[str, str] = {}  # the claude CLI resolves haiku / sonnet / opus itself

SYSTEM_PROMPT = (
    "You are an adversarial reviewer stress-testing whether an automated spec check "
    "really enforces one specific rule of a larger requirements document. You will be "
    "shown the rule's exact text and the current source of an app that is already "
    "known to pass the spec's check. Your job: find the smallest source change that "
    "makes the app violate that one rule in a way a real user could observe (not a "
    "comment, not a disabled test, not a change only visible by reading the code) -- "
    "while the app keeps building/starting and every *other* rule's behaviour stays "
    "the same. If you cannot find such a change, say so; do not pad a no-op change to "
    "look like an answer. Answer only in the exact format the user message gives you, "
    "nothing before or after it."
)


@dataclass(frozen=True)
class ProbeResult:
    """One rule's probe outcome. ``patch_summary`` is a unified diff of the applied
    change (``a/<path>`` / ``b/<path>`` per changed file) for ``gap``/``caught``/
    ``broken``, or the one-line reason for ``refused``. ``check_tail`` is the check's
    exit code and the tail of its combined stdout/stderr."""

    rule_id: str
    status: str  # "gap" | "caught" | "broken" | "refused"
    patch_summary: str
    changed_files: list[str] = field(default_factory=list)
    check_tail: str = ""
    usage: Usage = field(default_factory=lambda: Usage(0, 0))


@dataclass(frozen=True)
class _CheckOutcome:
    returncode: int | None  # None on timeout
    output: str
    timed_out: bool = False


# ---- app_dir -> a disposable scratch copy -----------------------------------------------------


def _copy_app(app_dir: Path, dest: Path) -> None:
    """``app_dir`` into the already-created empty directory ``dest``, skipping
    ``_SKIP_DIRS`` at every depth; ``.venv`` (if ``app_dir`` has one) is symlinked back
    instead of copied, so ``uv run`` finds it without reinstalling."""

    def _ignore(_dir: str, names: list[str]) -> list[str]:
        return [n for n in names if n in _SKIP_DIRS]

    shutil.copytree(app_dir, dest, ignore=_ignore, dirs_exist_ok=True, symlinks=True)
    venv = app_dir / ".venv"
    if venv.is_dir():
        link = dest / ".venv"
        if not link.exists():
            link.symlink_to(venv.resolve())


# ---- picking and rendering the source the model is shown ---------------------------------------


def _select_files(app_dir: Path, patterns: Sequence[str]) -> tuple[dict[str, str], list[str]]:
    """Every file under ``app_dir`` matching one of ``patterns`` (globs relative to
    ``app_dir``; ``**`` recurses), outside ``_SKIP_DIRS``, as ``{relative path: text}``,
    trimmed to fit ``MAX_PROMPT_CHARS`` by dropping the largest files first -- so a
    trim keeps more (smaller) files rather than one giant one. Returns the kept files
    and the relative paths of any dropped for size."""
    seen: dict[Path, None] = {}
    for pattern in patterns:
        for p in sorted(app_dir.glob(pattern)):
            if not p.is_file() or p in seen:
                continue
            if any(part in _SKIP_DIRS for part in p.relative_to(app_dir).parts):
                continue
            seen[p] = None

    sized: list[tuple[Path, str]] = []
    for p in seen:
        try:
            sized.append((p, p.read_text(errors="replace")))
        except OSError:
            continue
    sized.sort(key=lambda pt: len(pt[1]), reverse=True)  # largest first

    total = sum(len(t) for _, t in sized)
    dropped: list[str] = []
    while total > MAX_PROMPT_CHARS and sized:
        p, t = sized.pop(0)
        dropped.append(str(p.relative_to(app_dir)))
        total -= len(t)

    selected = {str(p.relative_to(app_dir)): t for p, t in sized}
    return selected, sorted(dropped)


def _render_prompt(rule: Rule, files: dict[str, str], dropped: list[str]) -> str:
    lines = [
        f"Rule {rule.id} ({rule.section or 'top level'}):",
        rule.text,
        "",
        "Below are the app's current source files you may change (anything not shown "
        "is off limits). Reply with the smallest patch that breaks ONLY this rule, "
        "while the app keeps building and every other rule's behaviour is unchanged.",
        "",
        "Answer with exactly one of the two:",
        "",
        "1. One block per changed file, in this exact shape, nothing else before or "
        "after any block:",
        "",
        "FILE: <path, exactly as shown in a '--- path ---' header below>",
        "```",
        "<the file's full new contents -- the whole file, not a diff>",
        "```",
        "",
        "2. Or, if no such change exists, the single line:",
        "IMPOSSIBLE: <why>",
        "",
        "=== source files ===",
    ]
    for path in sorted(files):
        lines.append(f"--- {path} ---")
        lines.append(files[path])
    if dropped:
        lines.append(
            "(omitted above for size, largest-dropped first: " + ", ".join(dropped) + ")"
        )
    return "\n".join(lines)


# ---- parsing and applying the model's answer ----------------------------------------------------


def _parse_patch(text: str) -> dict[str, str] | None:
    """The ``FILE:`` blocks of `text`, in order; ``None`` if there are none (the caller
    then checks for ``IMPOSSIBLE`` / treats it as malformed)."""
    blocks = {m.group("path").strip(): m.group("body") for m in _FILE_BLOCK_RE.finditer(text)}
    return blocks or None


def _safe_rel_path(path: str) -> Path | None:
    """``path`` as a ``Path`` relative to the app root, or ``None`` if it escapes it
    (absolute, or a ``..`` segment)."""
    p = Path(path)
    if p.is_absolute() or ".." in p.parts:
        return None
    return p


def _apply_patch(dest: Path, blocks: dict[str, str]) -> bool:
    """Writes every block's contents into `dest`. ``False`` (patch not applied) if any
    path is unsafe -- the whole answer is then treated as malformed, not partially
    applied."""
    targets: list[tuple[Path, str]] = []
    for path, body in blocks.items():
        rel = _safe_rel_path(path)
        if rel is None:
            return False
        targets.append((dest / rel, body))
    for target, body in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    return True


def _diff_for(app_dir: Path, path: str, new_text: str) -> str:
    original = app_dir / path
    old_lines = (
        original.read_text(errors="replace").splitlines(keepends=True)
        if original.exists()
        else []
    )
    new_lines = new_text.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{path}", tofile=f"b/{path}")
    )


# ---- running the check and classifying the outcome ----------------------------------------------


def _run_check(check_cmd: list[str] | str, cwd: Path, timeout_s: float) -> _CheckOutcome:
    try:
        proc = subprocess.run(
            check_cmd,
            shell=isinstance(check_cmd, str),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as err:
        out = (err.stdout or "") + (err.stderr or "")
        return _CheckOutcome(None, out, timed_out=True)
    return _CheckOutcome(proc.returncode, (proc.stdout or "") + (proc.stderr or ""))


def _classify(outcome: _CheckOutcome) -> str:
    if outcome.timed_out:
        return "broken"
    if outcome.returncode == 0:
        return "gap"
    if outcome.returncode == 1:
        return "caught"
    return "broken"  # 2, or any other exit: couldn't run / crashed before scenarios


def _tail(outcome: _CheckOutcome, n: int = 40) -> str:
    code = "timeout" if outcome.timed_out else outcome.returncode
    lines = outcome.output.splitlines()[-n:]
    return f"exit={code}\n" + "\n".join(lines)


# ---- one rule, end to end -------------------------------------------------------------------


def _probe_rule(
    rule: Rule,
    *,
    app_dir: Path,
    check_cmd: list[str] | str,
    selected_files: dict[str, str],
    dropped: list[str],
    generator: Generator,
    timeout_s: float,
) -> ProbeResult:
    prompt = _render_prompt(rule, selected_files, dropped)
    try:
        gen = generator.generate(SYSTEM_PROMPT, [{"role": "user", "content": prompt}])
    except GenerationRefused as refused:
        return ProbeResult(
            rule.id, "refused", f"model refused: {refused}", [], "", refused.usage
        )

    text = gen.text or ""
    blocks = _parse_patch(text)
    if blocks is None:
        stripped = text.strip()
        if stripped.upper().startswith("IMPOSSIBLE"):
            summary = stripped.splitlines()[0]
        else:
            summary = "malformed model output (no FILE block and no IMPOSSIBLE)"
        return ProbeResult(rule.id, "refused", summary, [], "", gen.usage)

    tmp = Path(tempfile.mkdtemp(prefix="lattice-probe-"))
    try:
        _copy_app(app_dir, tmp)
        if not _apply_patch(tmp, blocks):
            return ProbeResult(
                rule.id,
                "refused",
                "malformed model output (a FILE path escaped the app directory)",
                [],
                "",
                gen.usage,
            )
        diff = "".join(_diff_for(app_dir, path, blocks[path]) for path in sorted(blocks))
        outcome = _run_check(check_cmd, tmp, timeout_s)
        status = _classify(outcome)
        return ProbeResult(rule.id, status, diff, sorted(blocks), _tail(outcome), gen.usage)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---- the public API -------------------------------------------------------------------------


def probe(
    rules: list[Rule],
    *,
    app_dir: Path,
    check_cmd: list[str] | str,
    files: list[str],
    generator: Generator,
    model: str,
    timeout_s: float = 900,
    jobs: int = 1,
) -> list[ProbeResult]:
    """Probes every rule of ``rules`` against the app at ``app_dir`` (never modified):
    for each, asks ``generator`` (bound to ``model`` -- accepted here only so callers
    that build a report can record which model ran it; this function does not need it
    to pick a transport) to patch the files matching ``files`` (globs relative to
    ``app_dir``) so the app violates that rule while still passing ``check_cmd``, then
    runs ``check_cmd`` on a scratch copy of the app and classifies the result (see the
    module docstring). One rule at a time by default; ``jobs > 1`` runs up to that many
    rules at once, each against its own scratch copy, and returns results in ``rules``
    order regardless."""
    app_dir = Path(app_dir)
    selected, dropped = _select_files(app_dir, files)
    jobs = max(1, jobs)

    def run_one(rule: Rule) -> ProbeResult:
        return _probe_rule(
            rule,
            app_dir=app_dir,
            check_cmd=check_cmd,
            selected_files=selected,
            dropped=dropped,
            generator=generator,
            timeout_s=timeout_s,
        )

    if jobs == 1:
        return [run_one(r) for r in rules]

    results: list[ProbeResult | None] = [None] * len(rules)
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(run_one, r): i for i, r in enumerate(rules)}
        for fut in as_completed(futures):
            results[futures[fut]] = fut.result()
    return [r for r in results if r is not None]


# ---- CLI --------------------------------------------------------------------------------------


def _signal_rules(rules: Sequence[Rule]) -> list[Rule]:
    return [r for r in rules if any(w in r.text.lower() for w in SIGNAL_WORDS)]


def _resolve_model(name: str) -> str:
    return MODEL_ALIASES.get(name.lower(), name)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m lattice.spec probe",
        description="adversarially probe a spec's rule trace for gaps the check misses",
    )
    p.add_argument("--trace", required=True, type=Path, help="the spec's <module>.trace.json")
    p.add_argument("--app-dir", required=True, type=Path, help="the passing app; never modified")
    p.add_argument("--check", required=True, help="the spec check command, e.g. './check.sh 5'")
    p.add_argument(
        "--files",
        action="append",
        required=True,
        help="glob of source files the model may change, relative to --app-dir "
        "(repeatable; comma-separated also accepted), e.g. 'internal/**/*.go'",
    )
    sel = p.add_mutually_exclusive_group()
    sel.add_argument("--rules", help="comma-separated rule ids to probe, e.g. R12,R44")
    sel.add_argument(
        "--signal",
        action="store_true",
        help="probe only rules with a signal word: " + ", ".join(SIGNAL_WORDS),
    )
    p.add_argument(
        "--model", default="sonnet", help="model name, or an alias: haiku, sonnet, opus"
    )
    p.add_argument("--jobs", type=int, default=1, help="rules probed at once (default 1)")
    p.add_argument("--timeout", type=float, default=900.0, help="per-rule check timeout, seconds")
    p.add_argument("--json", type=Path, help="also write the full report as JSON to this path")
    return p


def _one_line(r: ProbeResult) -> str:
    if r.status == "refused":
        return r.patch_summary.splitlines()[0] if r.patch_summary else "(refused, no reason given)"
    files = ", ".join(r.changed_files) or "(no files changed)"
    tail = next((ln for ln in r.check_tail.splitlines() if ln.strip()), "")
    return f"{files} -- {tail}"


def _print_table(results: Sequence[ProbeResult]) -> None:
    width = max((len(r.rule_id) for r in results), default=4)
    print(f"{'rule':<{width}}  {'status':<8}  summary")
    for r in results:
        print(f"{r.rule_id:<{width}}  {r.status:<8}  {_one_line(r)}")
    counts = Counter(r.status for r in results)
    print()
    print(", ".join(f"{counts.get(s, 0)} {s}" for s in ("gap", "caught", "broken", "refused")))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        trace = Trace.load(args.trace)
        by_id = {r.id: r for r in trace.rules}
        if args.rules:
            wanted = [s.strip() for s in args.rules.split(",") if s.strip()]
            missing = [w for w in wanted if w not in by_id]
            if missing:
                raise SystemExit(f"unknown rule id(s): {', '.join(missing)}")
            rules = [by_id[w] for w in wanted]
        elif args.signal:
            rules = _signal_rules(trace.rules)
        else:
            rules = list(trace.rules)

        patterns = [p.strip() for entry in args.files for p in entry.split(",") if p.strip()]
        model = _resolve_model(args.model)

        from lattice.llm.cli_client import ClaudeCliClient

        generator = ClaudeCliClient(model=model, full_text=True)
        results = probe(
            rules,
            app_dir=args.app_dir,
            check_cmd=args.check,
            files=patterns,
            generator=generator,
            model=model,
            timeout_s=args.timeout,
            jobs=args.jobs,
        )
    except Exception as err:  # noqa: BLE001 -- any failure to set up/run is a hard error
        print(f"error: {type(err).__name__}: {err}", file=sys.stderr)
        return 2

    _print_table(results)
    if args.json:
        args.json.write_text(json.dumps([asdict(r) for r in results], indent=2) + "\n")
    return 0  # this command is a report, not a pass/fail gate


if __name__ == "__main__":
    sys.exit(main())
