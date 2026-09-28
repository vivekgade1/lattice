"""The edit-slice harness (plan 2026-09-27 thin slice, node E3): arms P and L.

For one ticket (:mod:`bench.edit_slice.tickets`) and one model, an arm asks the model
for the change, repairs it on what a developer would see locally, then scores the final
candidate with the hidden oracle (:func:`bench.edit_slice.oracle.evaluate`):

* **P (Python edit).** Start state: the template (or, for a ticket with a ``base``, the
  base ticket's Python reference). The model gets the ticket and the current source of
  the ticket's files plus ``models.py`` / ``api/deps.py`` (``crud.py`` when referenced)
  and returns complete files; the candidate is
  :func:`bench.edit_slice.variant.load_variant` of the start overlay with them.
* **L (Lattice edit).** Start state: the verified import of the ticket's app
  (:func:`start_lattice`; for the template :func:`lattice.importer.lift.lift_repo`), or
  the base ticket's Lattice reference (:mod:`bench.edit_slice.lattice_reference`). The
  model gets the ticket, the touched router's Lattice program and the profile source,
  with the Lattice API guide, and returns edited program(s) (and the profile when it
  changes); the candidate is checked,
  emitted (:func:`lattice.emit.thin.emit_into_template`) and mounted
  (:func:`lattice.emit.thin.make_emitted_app`): its routes replace the template routes
  the start programs lifted (a lifted route the edit drops is gone), and every other
  template route (``/users/signup``, ``/users/{user_id}``, ...) is kept as it is.

**The ticket's app.** A ticket targets the template or a held-out repo (``Ticket.repo``:
TaskFlow or JustNotes, round 3's t19-t24; :mod:`bench.edit_slice.apps`). Arm P works the
same on every app: the variant, smoke run and oracle take the ticket's app, and the
prompt describes it (:func:`bench.edit_slice.prompts.p_system`). Arm L's start state for
a held-out repo must be the *importer's* output -- those repos are held out from the
language and lifter work, so no Lattice is written for them here. The two hooks the
importer's output plugs into are :func:`start_lattice` (programs + profile) and
:func:`emit_lattice` (emit and mount them into that app); both raise
``NotImplementedError`` for ``taskflow``/``justnotes`` until then, and the dry run
reports arm L on those tickets as ``not_imported``.

Both arms have the same repair budget (``max_repairs``, default 2). A round is repaired
when its answer has no file blocks (or names a file the arm cannot take), when the app
does not start (the traceback), when -- arm L only -- the checker refuses a program
(its error diagnostics), or when the visible smoke run fails
(:mod:`bench.edit_slice.smoke`: the ticket's happy-path requests, with the traceback of
a server error). The oracle's cases, their names and the traps never reach the model.
A round whose smoke run passes is final.

Outcome of a run: the oracle's ``correct`` / ``loud`` / ``silent``; a final round
without a runnable candidate (no file blocks, a refused program, a refusal) is loud.

Each round records the model's stop reason (``stop_reasons``). An answer cut off at the
output limit (``max_tokens``) is never built: its round's problem is ``truncated`` (not
``no_code``), repaired by asking for the complete files again, so these runs are
distinguishable from format errors.

Artefacts: a live run (or any run given ``artefact_root``) writes, per round,
``<root>/<model>/<arm>/<ticket>/rep<N>/round<k>/`` with ``response.md`` (the model's
text), ``round.json`` (stop reason, problem, feedback) and ``files/`` (the complete
candidate: arm P the start overlay plus every returned app file, relative to the app
package; arm L every program and ``profile.py``), once the answer had file blocks. The
CLI's root is ``<out>.artefacts`` next to the JSONL, and the record's ``artefacts`` is the
run's ``rep<N>`` directory.

``python -m bench.edit_slice.run --model M --arm P|L --tickets ... --reps N --out
results.jsonl [--max-tokens 16000] [--dry-run-reference]`` (``--tickets all``: the template's
t1-t18; ``repos``: the held-out repos' t19-t24; ``round3``: every ticket, t1-t24; or
comma-separated ids); ``--estimate`` prints the
prompt sizes and the live run's cost estimate instead (no generation). The dry run
feeds the hand-written references through each arm (no API call); arm L on t13-t18 has no
reference to feed (they are held out from the language work, see
:mod:`bench.edit_slice.tickets`) and is reported as ``no_reference``.
"""

from __future__ import annotations

import argparse
import contextvars
import dataclasses
import difflib
import json
import logging
import os
import shutil
import statistics
import sys
import tempfile
import time
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Literal

from bench.edit_slice import prompts, smoke
from bench.edit_slice.apps import REPOS
from bench.edit_slice.lattice_reference import (
    GAPS,
    has_lattice_reference,
    lattice_reference_files,
)
from bench.edit_slice.oracle import Outcome, evaluate
from bench.edit_slice.reference import reference_files
from bench.edit_slice.tickets import ALL_TICKETS, REPO_TICKETS, TICKETS, Ticket, ticket
from bench.edit_slice.variant import app_source, load_variant
from bench.metrics import cost_usd
from lattice.llm.client import Generation, GenerationRefused, Generator, ScriptedGenerator

Arm = Literal["P", "L"]
ARMS: tuple[Arm, ...] = ("P", "L")
MAX_REPAIRS = 2
MAX_TOKENS = 16000  # the default output limit per model call (--max-tokens)
REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PACKAGE = "bench.template_app.app"
CHARS_PER_TOKEN = 3.5  # the prompt-size estimate when no token count is available
LOGIN_PATH = "/api/v1/login/access-token"
LOGIN_PATHS = tuple(dict.fromkeys(r.login_path for r in REPOS.values()))
_DETAIL_LIMIT = 2000

# ---- start states ---------------------------------------------------------------------------


@cache
def imported() -> tuple[dict[str, str], str]:
    """The verified Lattice import of the template: ``{router file: program}`` (e.g.
    ``items.lattice.py``) and the generated profile source. Built once per process."""
    from bench.template_app import make_app
    from lattice.importer.lift import lift_repo

    logging.getLogger("httpx2").setLevel(logging.WARNING)
    result = lift_repo(REPO_ROOT, TEMPLATE_PACKAGE, make_app)
    programs = {f"{router}.lattice.py": src for router, src in result.programs.items()}
    return programs, result.profile.source


IMPORTS = REPO_ROOT / "bench" / "imports"  # the held-out repos' imports (lattice.importer)


def import_dir(repo: str) -> Path:
    """The committed import of the held-out app ``repo`` (``python -m lattice.importer
    ... --out bench/imports/<repo>``): ``profile.py``, ``natives.py``, ``routes/`` (one
    ``<router>.lattice.py`` per router with a verified route) and ``report.json``."""
    return IMPORTS / repo


def imported_repo(repo: str) -> tuple[dict[str, str], str]:
    """The held-out app's import, as :func:`imported` gives the template's: ``{router
    file: program}`` and the profile source, read from :func:`import_dir` -- the
    importer's output, unedited."""
    out = import_dir(repo)
    programs = {p.name: p.read_text() for p in sorted((out / "routes").glob("*.lattice.py"))}
    return programs, (out / "profile.py").read_text()


# repo -> a function returning ({program file: source}, profile source): the template's
# lifters' output; a held-out repo's is its committed import (import_dir), when written
LATTICE_STARTS: dict[str, Callable[[], tuple[dict[str, str], str]]] = {"template": imported}
HELD_OUT_REPOS = ("taskflow", "justnotes")


def _start(repo: str) -> Callable[[], tuple[dict[str, str], str]] | None:
    if repo in LATTICE_STARTS:
        return LATTICE_STARTS[repo]
    if repo in HELD_OUT_REPOS and (import_dir(repo) / "profile.py").exists():
        return lambda: imported_repo(repo)
    return None


def has_lattice_start(repo: str) -> bool:
    """Whether arm L has a start state (an import) for the app ``repo``."""
    return _start(repo) is not None


def start_lattice(repo: str) -> tuple[dict[str, str], str]:
    """Arm L's start state for the app ``repo``: the verified import's programs
    (``{"<router>.lattice.py": source}``) and profile source.

    For a held-out repo it is the importer's committed output (:func:`import_dir`),
    never Lattice written by hand: TaskFlow and JustNotes are held out from the language
    and lifter work. Its routes that the import left as islands are not in the programs
    (the emitted app keeps them as the original code)."""
    start = _start(repo)
    if start is None:
        raise NotImplementedError(
            f"arm L has no start state for {repo!r}: it must be the importer's output "
            f"(python -m lattice.importer ... --out {import_dir(repo)}); no Lattice is "
            "written by hand for it"
        )
    return start()


@cache
def _original(repo: str) -> Any:
    return load_variant({}, repo=repo)


def taskflow_app(db_path: str | Path) -> Any:
    """The original TaskFlow app (arm P's start: its tables created at once, the test
    secret) -- the app arm L's programs are emitted into."""
    return _original("taskflow").make_app(db_path)


def justnotes_app(db_path: str | Path) -> Any:
    """The original JustNotes app (arm P's start) -- the app arm L's programs are
    emitted into."""
    return _original("justnotes").make_app(db_path)


APP_FACTORIES = {
    "taskflow": f"{__name__}:taskflow_app",
    "justnotes": f"{__name__}:justnotes_app",
}


def emit_lattice(repo: str, programs: Mapping[str, str], profile_src: str, out: Path,
                 lifted: Sequence[str]) -> Any:  # fmt: skip
    """Emit arm L's programs into the app ``repo``: the template's emitter for the
    template; for a held-out repo, the same route-preserving merge into its original app
    (:func:`lattice.emit.thin.emit_into_app`): the programs' routes replace the routes
    the import lifted (``lifted``), every other route -- the import's islands -- is
    kept as the original code."""
    if repo == "template":
        from lattice.emit.thin import emit_into_template

        return emit_into_template(programs, profile_src, out, lifted=lifted)
    if repo not in APP_FACTORIES:
        raise NotImplementedError(f"no emitter mounts Lattice into {repo!r}")
    from lattice.emit.thin import emit_into_app

    return emit_into_app(
        programs, profile_src, out, app_factory=APP_FACTORIES[repo], lifted=lifted
    )


def lift_status(ticket_id: str) -> dict[str, str]:
    """Per route the ticket touches (``Ticket.routes``): ``lifted`` (a Lattice route of
    arm L's start state, editable), ``island`` (a route of the app the import did not
    lift: kept as the original code, not editable in arm L) or ``new`` (no such route
    yet: arm L may add it)."""
    from lattice.emit.thin import _normalize, app_routes, program_routes

    t = ticket(ticket_id)
    programs, profile_src = start_lattice(t.repo)
    profile = load_profile_source(profile_src)
    lifted = {_normalize(r) for r in program_routes(programs, profile)} if programs else set()
    if t.repo == "template":
        from lattice.emit.thin import _template_routes

        existing = {_normalize(f"{m} {p}") for m, p in _template_routes()}
    else:
        existing = {_normalize(r) for r in app_routes(APP_FACTORIES[t.repo])}
    out = {}
    for route in t.routes:
        key = _normalize(route)
        out[route] = "lifted" if key in lifted else "island" if key in existing else "new"
    return out


def p_start(t: Ticket) -> dict[str, str]:
    """Arm P's start overlay (the base ticket's reference files, or nothing)."""
    return dict(reference_files(t.base)) if t.base else {}


def p_view(overlay: Mapping[str, str], rel: str, repo: str = "template") -> str | None:
    """The source of ``rel`` in the app ``overlay`` describes (None: no such file)."""
    if rel in overlay:
        return overlay[rel]
    try:
        return app_source(rel, repo)
    except FileNotFoundError:
        return None


def l_start(t: Ticket) -> tuple[dict[str, str], str]:
    """Arm L's start state: programs and profile source (the import, with the base
    ticket's Lattice reference applied)."""
    programs, profile = start_lattice(t.repo)
    programs = dict(programs)
    if t.base:
        for name, src in lattice_reference_files(t.base).items():
            if name == prompts.PROFILE_FILE:
                profile = src
            else:
                programs[name] = src
    return programs, profile


def p_prompt(ticket_id: str) -> tuple[str, str]:
    """(system, first user message) of arm P."""
    t = ticket(ticket_id)
    overlay = p_start(t)
    names = [*t.files, "models.py", "api/deps.py", "crud.py", *REPOS[t.repo].context]
    files = {rel: src for rel in names if (src := p_view(overlay, rel, t.repo)) is not None}
    return prompts.p_system(t.repo), prompts.p_user_message(t, files)


def l_prompt(ticket_id: str, *, hints: bool = False) -> tuple[str, str]:
    """(system, first user message) of arm L."""
    t = ticket(ticket_id)
    programs, profile = l_start(t)
    shown = prompts.l_program_files(t, programs)
    others = [n for n in programs if n not in shown]
    user = prompts.l_user_message(t, programs, profile, others=others)
    return prompts.l_system_prompt(hints=hints, repo=t.repo), user


# ---- run records ----------------------------------------------------------------------------


@dataclass
class RunRecord:
    """One run, one JSONL line. ``outcome``: correct / loud / silent (the oracle's; a
    final round without a runnable candidate is loud), or ``error`` (the harness
    failed) / ``not_expressible`` (dry run of a ticket in ``GAPS``)."""

    ticket: str
    arm: str
    model: str
    rep: int = 0
    outcome: str = "loud"
    failure: str | None = None  # the final round's problem, or None
    detail: str = ""
    rounds: int = 0
    round_problems: list[str | None] = field(default_factory=list)
    # each round's stop reason (end_turn / max_tokens / refusal / ...)
    stop_reasons: list[str | None] = field(default_factory=list)
    # the oracle's per-case tally (equal / equal_under_rule / deviation / silent / loud)
    cases: dict[str, int] = field(default_factory=dict)
    cases_generated: int = 0
    cases_specific: int = 0
    failed_cases: list[str] = field(default_factory=list)
    # tokens and cost over every call of the run (refused calls included)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    served_model: str = ""
    refused_attempts: int = 0
    # wall time: the whole run, the model calls, the oracle
    wall_s: float = 0.0
    llm_s: float = 0.0
    eval_s: float = 0.0
    # the first prompt (identical ticket text in both arms)
    system_chars: int = 0
    user_chars: int = 0
    prompt_tokens_est: int = 0
    # the edit, in the arm's own artefact (Python files / Lattice programs + profile)
    edit_files: list[str] = field(default_factory=list)
    lines_added: int = 0
    lines_removed: int = 0
    lines_changed: int = 0
    artefact_lines: int = 0
    # the code that runs: P the edited Python files, L the emitted package
    code_lines: int = 0
    code_ast_nodes: int = 0
    # correct runs: per request on the oracle's cases (login requests excluded)
    mean_latency_ms: float | None = None
    mean_queries: float | None = None
    max_queries: int | None = None
    max_repairs: int = MAX_REPAIRS
    max_tokens: int | None = None  # the output limit per call (None: not a live run)
    hints: bool = False
    dry_run: bool = False
    # the run's artefact directory (every round's output and candidate files), if kept
    artefacts: str | None = None

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self))


def _lines_diff(old: str, new: str) -> tuple[int, int]:
    added = removed = 0
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def edit_size(before: Mapping[str, str | None], after: Mapping[str, str]) -> tuple[int, int, int]:
    """(lines added, lines removed, lines of the edited files) over ``after``'s files,
    each against its ``before`` source (a new file against nothing)."""
    added = removed = lines = 0
    for name, new in after.items():
        a, r = _lines_diff(before.get(name) or "", new)
        added, removed, lines = added + a, removed + r, lines + len(new.splitlines())
    return added, removed, lines


def _python_size(sources: Sequence[str]) -> tuple[int, int]:
    import ast

    lines = nodes = 0
    for src in sources:
        lines += len(src.splitlines())
        try:
            nodes += sum(1 for _ in ast.walk(ast.parse(src)))
        except SyntaxError:
            pass
    return lines, nodes


# ---- request stats on the oracle's cases ----------------------------------------------------

_COUNTER: contextvars.ContextVar[list[int] | None] = contextvars.ContextVar(
    "edit_slice_queries", default=None
)
_COUNTED = ("SELECT", "INSERT", "UPDATE", "DELETE", "WITH", "REPLACE")


def _count(sql: str) -> None:
    counter = _COUNTER.get()
    head = sql.lstrip().split(None, 1)
    if counter is not None and head and head[0].upper() in _COUNTED:
        counter[0] += 1


class _StatsMiddleware:
    """Records (method, path, ms, SQL data statements) per HTTP request."""

    def __init__(self, app: Any, sink: list[tuple[str, str, float, int]]) -> None:
        self.app, self.sink = app, sink

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        counter = [0]
        token = _COUNTER.set(counter)
        start = time.perf_counter()
        try:
            await self.app(scope, receive, send)
        finally:
            ms = (time.perf_counter() - start) * 1000
            self.sink.append((scope["method"], scope["path"], ms, counter[0]))
            _COUNTER.reset(token)


def instrumented(factory: Callable[[Path], Any], sink: list[tuple[str, str, float, int]]):
    """``factory`` whose apps record per-request latency and SQL statements in ``sink``."""
    from sqlalchemy import event

    def build(path: Path) -> Any:
        app = factory(path)
        app.add_middleware(_StatsMiddleware, sink=sink)
        engine = getattr(app.state, "engine", None)
        if engine is not None:
            event.listen(engine, "before_cursor_execute", lambda conn, cur, sql, *a: _count(sql))
        package = getattr(app.state, "emitted", None)
        kit = getattr(package, "kit", None)
        if kit is not None:
            kit._on_connect = lambda conn: conn.set_trace_callback(_count)
        return app

    return build


def request_stats(sink: Sequence[tuple[str, str, float, int]]) -> tuple[float, float, int] | None:
    """(mean ms, mean statements, max statements) over the non-login requests."""
    rows = [(ms, q) for _, path, ms, q in sink if not path.endswith(LOGIN_PATHS)]
    if not rows:
        return None
    return (
        statistics.fmean(ms for ms, _ in rows),
        statistics.fmean(q for _, q in rows),
        max(q for _, q in rows),
    )


# ---- a round's candidate --------------------------------------------------------------------


@dataclass
class Candidate:
    """What one round produced: the app factory when it was built, or the problem and
    the feedback a developer would see."""

    factory: Callable[[Path], Any] | None = None
    # no_code | truncated | bad_file | start | static | smoke | refused
    problem: str | None = None
    feedback: str = ""
    code: tuple[int, int] = (0, 0)  # (lines, AST nodes) of the code that runs
    cleanup: Callable[[], None] = lambda: None


NO_FILES = (
    "Your answer contained no file in the required format: a heading line `### <file>` "
    "followed by one ```python block with the file's complete content."
)
TRUNCATED = (
    "Your answer was cut off at the output limit before it was complete, so none of it "
    "was applied. Return the files again, each complete, with less prose."
)


class _PythonArm:
    """Arm P's state: the start overlay and the files the model has returned so far
    (a file it does not return again keeps its last version)."""

    def __init__(self, t: Ticket) -> None:
        self.repo = t.repo
        self.overlay = p_start(t)
        self.edits: dict[str, str] = {}

    def build(self, text: str) -> Candidate:
        files = prompts.parse_files(text)
        if not files:
            return Candidate(problem="no_code", feedback=NO_FILES)
        edits: dict[str, str] = {}
        for name, src in files.items():
            rel = name.removeprefix("./").removeprefix(prompts.APP_PREFIX)
            if not rel.endswith(".py") or rel.startswith("/") or ".." in rel.split("/"):
                return Candidate(
                    problem="bad_file",
                    feedback=f"`{name}` is not a Python file of the app (answer with paths "
                    "such as `app/api/routes/items.py`).",
                )
            edits[rel] = src
        self.edits.update(edits)
        variant = load_variant({**self.overlay, **self.edits}, repo=self.repo)
        after = self.edited()[1]
        return Candidate(factory=variant.make_app, code=_python_size(list(after.values())))

    def edited(self) -> tuple[dict[str, str | None], dict[str, str]]:
        """(before, after) of the files the edit changed."""
        after = {r: s for r, s in self.edits.items() if s != p_view(self.overlay, r, self.repo)}
        return {r: p_view(self.overlay, r, self.repo) for r in after}, after

    def candidate_files(self) -> dict[str, str]:
        """The candidate's files beyond the template (paths relative to the app)."""
        return {**self.overlay, **self.edits}


def load_profile_source(source: str) -> Any:
    """The Profile a profile module's source defines (its ``profile()``)."""
    module = types.ModuleType(f"_edit_slice_profile_{abs(hash(source))}")
    exec(compile(source, f"<{prompts.PROFILE_FILE}>", "exec"), module.__dict__)  # noqa: S102
    fn = getattr(module, "profile", None)
    if not callable(fn):
        raise ValueError(f"{prompts.PROFILE_FILE} must define profile() returning a Profile")
    return fn()


class _LatticeArm:
    """Arm L's state: the start programs and profile and the files returned so far."""

    def __init__(self, t: Ticket) -> None:
        self.repo = t.repo
        programs, profile = l_start(t)
        self.start = {**programs, prompts.PROFILE_FILE: profile}
        self.state = dict(self.start)
        self._lifted: tuple[str, ...] | None = None

    def lifted(self) -> tuple[str, ...]:
        """The routes of the start programs: the template routes the edit may replace or
        drop (lattice.emit.thin keeps every other template route)."""
        if self._lifted is None:
            from lattice.emit.thin import program_routes

            start = {n: s for n, s in self.start.items() if n != prompts.PROFILE_FILE}
            profile = load_profile_source(self.start[prompts.PROFILE_FILE])
            self._lifted = program_routes(start, profile) if start else ()
        return self._lifted

    def edited(self) -> tuple[dict[str, str | None], dict[str, str]]:
        after = {n: s for n, s in self.state.items() if s != self.start.get(n)}
        return {n: self.start.get(n) for n in after}, after

    def candidate_files(self) -> dict[str, str]:
        """Every program and the profile."""
        return dict(self.state)

    def build(self, text: str) -> Candidate:
        from lattice.check import check_source
        from lattice.diagnostics import Severity, render_for_llm
        from lattice.emit.thin import EmitError, make_emitted_app

        files = prompts.parse_files(text)
        if not files:
            return Candidate(problem="no_code", feedback=NO_FILES)
        for name in files:
            if name != prompts.PROFILE_FILE and not name.endswith(".lattice.py"):
                return Candidate(
                    problem="bad_file",
                    feedback=f"`{name}` is neither a Lattice program (`<router>.lattice.py`) "
                    f"nor `{prompts.PROFILE_FILE}`.",
                )
        self.state.update(files)
        programs = {n: s for n, s in self.state.items() if n != prompts.PROFILE_FILE}
        profile_src = self.state[prompts.PROFILE_FILE]
        try:
            profile = load_profile_source(profile_src)
        except Exception as exc:  # noqa: BLE001 -- shown like an import traceback
            return Candidate(
                problem="start",
                feedback=(f"Loading {prompts.PROFILE_FILE} failed:\n{smoke.format_exception(exc)}"),
            )
        reports = []
        for name, src in programs.items():
            errors = [
                d for d in check_source(src, profile).diagnostics if d.severity is Severity.ERROR
            ]
            if errors:
                reports.append(f"{name}:\n{render_for_llm(errors)}")
        if reports:
            return Candidate(
                problem="static",
                feedback=("The Lattice checker refused the program:\n" + "\n".join(reports)),
            )
        root = Path(tempfile.mkdtemp(prefix="edit-slice-emit-"))
        out = root / "emitted"

        def cleanup() -> None:
            shutil.rmtree(root, ignore_errors=True)

        try:
            emitted = emit_lattice(self.repo, programs, profile_src, out, self.lifted())
        except EmitError as exc:
            cleanup()
            errors = [d for d in exc.diagnostics if d.severity is Severity.ERROR]
            return Candidate(
                problem="static",
                feedback=(
                    "The Lattice checker refused the joined program:\n"
                    + render_for_llm(errors or exc.diagnostics)
                ),
            )
        except Exception as exc:  # noqa: BLE001
            cleanup()
            return Candidate(
                problem="start",
                feedback=(f"Emitting the program failed:\n{smoke.format_exception(exc)}"),
            )
        return Candidate(
            factory=lambda path: make_emitted_app(out, path),
            code=(emitted.lines, emitted.ast_nodes),
            cleanup=cleanup,
        )


def repair_message(feedback: str) -> str:
    return f"{feedback}\n\nReturn the corrected files in the same format."


# ---- the arms -------------------------------------------------------------------------------


def run_arm(
    arm: Arm,
    ticket_id: str,
    generator: Generator,
    *,
    model: str = "",
    rep: int = 0,
    max_repairs: int = MAX_REPAIRS,
    hints: bool = False,
    dry_run: bool = False,
    max_tokens: int | None = None,
    artefact_root: Path | None = None,
) -> RunRecord:
    """One run of ``arm`` on ``ticket_id`` (see the module docstring). ``max_tokens`` is
    recorded (the generator applies it); ``artefact_root``: keep every round's output
    and candidate files under it."""
    started = time.perf_counter()
    t = ticket(ticket_id)
    record = RunRecord(
        ticket_id,
        arm,
        model,
        rep,
        max_repairs=max_repairs,
        max_tokens=max_tokens,
        hints=hints,
        dry_run=dry_run,
    )
    rep_dir = (
        None
        if artefact_root is None
        else artefact_root / _path_part(model or "model") / arm / ticket_id / f"rep{rep}"
    )
    if rep_dir is not None:
        shutil.rmtree(rep_dir, ignore_errors=True)
        rep_dir.mkdir(parents=True)
        record.artefacts = str(rep_dir)
    state: _PythonArm | _LatticeArm
    if arm == "P":
        state, (system, user) = _PythonArm(t), p_prompt(ticket_id)
    elif arm == "L":
        state, (system, user) = _LatticeArm(t), l_prompt(ticket_id, hints=hints)
    else:
        raise ValueError(f"unknown arm {arm!r}")
    record.system_chars, record.user_chars = len(system), len(user)
    record.prompt_tokens_est = round((len(system) + len(user)) / CHARS_PER_TOKEN)

    messages: list[dict[str, str]] = [{"role": "user", "content": user}]
    generations: list[Generation] = []
    candidate = Candidate()
    try:
        for attempt in range(max_repairs + 1):
            try:
                generation = generator.generate(system, messages)
            except GenerationRefused as exc:
                _account(record, generations, exc)
                record.failure, record.detail = "refused", str(exc)[:_DETAIL_LIMIT]
                record.rounds = len(generations) + 1
                record.round_problems.append("refused")
                record.stop_reasons.append("refusal")
                candidate.cleanup()
                candidate = Candidate(problem="refused", feedback=str(exc))
                _save_round(rep_dir, attempt + 1, None, "refusal", candidate, None)
                break
            generations.append(generation)
            record.stop_reasons.append(generation.stop_reason)
            candidate.cleanup()
            truncated = generation.stop_reason == "max_tokens"
            if truncated:
                candidate = Candidate(problem="truncated", feedback=TRUNCATED)
            else:
                candidate = state.build(generation.text)
            if candidate.factory is not None:
                result = smoke.run_smoke(ticket_id, candidate.factory)
                if not result.started:
                    candidate.problem = "start"
                elif result.failed:
                    candidate.problem = "smoke"
                candidate.feedback = smoke.smoke_feedback(result) if candidate.problem else ""
            record.round_problems.append(candidate.problem)
            applied = (
                not truncated
                and candidate.problem != "bad_file"
                and bool(prompts.parse_files(generation.text))
            )
            _save_round(
                rep_dir,
                attempt + 1,
                generation.text,
                generation.stop_reason,
                candidate,
                state.candidate_files() if applied else None,
            )
            if candidate.problem is None or attempt == max_repairs:
                break
            messages.append({"role": "assistant", "content": generation.text})
            messages.append({"role": "user", "content": repair_message(candidate.feedback)})
        else:  # pragma: no cover
            raise AssertionError("unreachable")
        if candidate.problem != "refused":
            _account(record, generations, None)
            record.rounds = len(generations)
            record.failure = candidate.problem
            record.detail = candidate.feedback[:_DETAIL_LIMIT]
        _score(record, ticket_id, candidate, *state.edited())
    finally:
        candidate.cleanup()
    record.wall_s = time.perf_counter() - started
    return record


def _path_part(name: str) -> str:
    """``name`` as one path segment (a model id may hold '/' or ':')."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name) or "_"


def _save_round(
    rep_dir: Path | None,
    k: int,
    text: str | None,
    stop_reason: str | None,
    candidate: Candidate,
    files: Mapping[str, str] | None,
) -> None:
    """Round ``k``'s artefacts: the model's text, its stop reason, problem and feedback,
    and the candidate's files (None: the answer had no file blocks to apply)."""
    if rep_dir is None:
        return
    round_dir = rep_dir / f"round{k}"
    round_dir.mkdir(parents=True, exist_ok=True)
    if text is not None:
        (round_dir / "response.md").write_text(text)
    meta = {
        "stop_reason": stop_reason,
        "problem": candidate.problem,
        "feedback": candidate.feedback,
    }
    (round_dir / "round.json").write_text(json.dumps(meta, indent=2) + "\n")
    for rel, src in (files or {}).items():
        target = (round_dir / "files" / rel).resolve()
        if not target.is_relative_to(round_dir.resolve()):
            continue  # a bad_file path outside the round's directory
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(src)


def _account(
    record: RunRecord, generations: Sequence[Generation], refused: GenerationRefused | None
) -> None:
    usages = [(g.model, g.usage, g.latency_s) for g in generations]
    if refused is not None:
        usages.append((refused.model, refused.usage, refused.latency_s))
    for served, usage, latency in usages:
        record.input_tokens += usage.total_input_tokens
        record.output_tokens += usage.output_tokens
        record.cache_read_tokens += usage.cache_read_input_tokens
        record.cache_creation_tokens += usage.cache_creation_input_tokens
        record.llm_s += latency
        record.cost_usd += cost_usd(
            served,
            input_tokens=usage.total_input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_input_tokens,
            cache_creation_tokens=usage.cache_creation_input_tokens,
        )
    served_models = list(dict.fromkeys(m for m, _, _ in usages if m))
    record.served_model = ",".join(served_models)
    record.refused_attempts = sum(g.refusals for g in generations)


def _score(
    record: RunRecord,
    ticket_id: str,
    candidate: Candidate,
    before: Mapping[str, str | None],
    after: Mapping[str, str],
) -> None:
    record.edit_files = sorted(after)
    record.lines_added, record.lines_removed, record.artefact_lines = edit_size(before, after)
    record.lines_changed = record.lines_added + record.lines_removed
    if candidate.factory is None:
        record.outcome = "loud"
        return
    record.code_lines, record.code_ast_nodes = candidate.code
    sink: list[tuple[str, str, float, int]] = []
    started = time.perf_counter()
    outcome = evaluate(ticket_id, instrumented(candidate.factory, sink))
    record.eval_s = time.perf_counter() - started
    _outcome(record, outcome)
    stats = request_stats(sink) if outcome.kind == "correct" else None
    if stats is not None:
        record.mean_latency_ms, record.mean_queries, record.max_queries = stats


def _outcome(record: RunRecord, outcome: Outcome) -> None:
    record.outcome = outcome.kind
    record.cases = dict(sorted(outcome.tally().items()))
    record.cases_generated, record.cases_specific = outcome.counts()
    record.failed_cases = sorted(
        n for n, v in outcome.cases.items() if v.verdict in ("silent", "loud")
    )
    if outcome.error:
        record.detail = (record.detail + "\n" if record.detail else "") + outcome.error


# ---- the dry run ----------------------------------------------------------------------------


def reference_answer(arm: Arm, ticket_id: str) -> str | None:
    """The hand-written solution as an answer in the arm's format (None: a gap)."""
    t = ticket(ticket_id)
    if arm == "P":
        overlay = p_start(t)
        files = {
            prompts.APP_PREFIX + rel: src
            for rel, src in reference_files(ticket_id).items()
            if src != p_view(overlay, rel, t.repo)
        }
        return prompts.render_files(files)
    if not has_lattice_reference(ticket_id):
        return None
    programs, profile = l_start(t)
    start = {**programs, prompts.PROFILE_FILE: profile}
    files = {n: s for n, s in lattice_reference_files(ticket_id).items() if s != start.get(n)}
    return prompts.render_files(files)


def dry_run(arm: Arm, ticket_id: str) -> RunRecord:
    """The reference fed through ``arm`` (no API call). Three ways arm L can have nothing
    to run: no import at all for the app (``not_imported``, a held-out repo before the
    importer's output is registered); current Lattice cannot express the ticket, a
    recorded gap (``not_expressible``); or the ticket is held out from the language and
    lifter work on purpose, with no Lattice reference written for it at all
    (``no_reference`` -- round 3's t13-t18, see :mod:`bench.edit_slice.tickets`)."""
    repo = ticket(ticket_id).repo
    if arm == "L" and not has_lattice_start(repo):
        return RunRecord(
            ticket_id,
            arm,
            "reference",
            outcome="not_imported",
            detail=f"arm L on {repo!r} needs the importer's output (start_lattice)",
            dry_run=True,
        )
    if arm == "L" and not has_lattice_reference(ticket_id) and ticket_id not in GAPS:
        return RunRecord(
            ticket_id,
            arm,
            "reference",
            outcome="no_reference",
            detail=f"{ticket_id!r} is held out: no Lattice reference is written for it",
            dry_run=True,
        )
    answer = reference_answer(arm, ticket_id)
    if answer is None:
        return RunRecord(
            ticket_id,
            arm,
            "reference",
            outcome="not_expressible",
            detail=GAPS[ticket_id],
            dry_run=True,
        )
    return run_arm(arm, ticket_id, ScriptedGenerator([answer]), model="reference", dry_run=True)


# ---- the cost estimate ----------------------------------------------------------------------

REPAIR_RATE = (0.30, 0.15)  # assumed share of runs needing a 1st / 2nd repair round
FEEDBACK_TOKENS = 600  # a repair message (traceback, diagnostics or smoke lines)
OUTPUT_OVERHEAD = 1.25  # answer tokens over the solution's own (headings, prose)


def token_counter(model: str) -> Callable[[str, str], int]:
    """Prompt tokens of (system, user): the count_tokens API when a key is set, else
    chars / 3.5."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic

        client = anthropic.Anthropic()

        def count(system: str, user: str) -> int:
            r = client.messages.count_tokens(
                model=model, system=system or "-", messages=[{"role": "user", "content": user}]
            )
            return r.input_tokens

        return count
    return lambda system, user: round((len(system) + len(user)) / CHARS_PER_TOKEN)


def estimate(models: Sequence[str], tickets: Sequence[str], reps: int) -> dict[str, Any]:
    """Prompt sizes per arm and ticket and the expected cost of the live run."""
    from bench.metrics import price_for

    rows = []
    total = 0.0
    for model in models:
        count = token_counter(model)
        price = price_for(model) or (0.0, 0.0)
        for tid in tickets:
            # arm L only where it has a start state (an import of the ticket's app)
            arms = [a for a in ARMS if a == "P" or has_lattice_start(ticket(tid).repo)]
            answers = {a: reference_answer(a, tid) for a in arms}
            p_answer = answers["P"] or ""
            for arm in arms:
                system, user = p_prompt(tid) if arm == "P" else l_prompt(tid)
                prompt = count(system, user)
                answer = answers[arm]
                if answer is None:  # no reference (a gap): the touched program, edited
                    programs, _ = l_start(ticket(tid))
                    answer = prompts.render_files(
                        {n: programs[n] for n in prompts.l_program_files(ticket(tid), programs)}
                    )
                out = round(count("", answer) * OUTPUT_OVERHEAD) if answer.strip() else 0
                # repair round k's prompt: the first prompt + every earlier answer and
                # feedback; it happens in REPAIR_RATE[k - 1] of the runs
                expected_in = prompt + sum(
                    rate * (prompt + k * (out + FEEDBACK_TOKENS))
                    for k, rate in enumerate(REPAIR_RATE, start=1)
                )
                expected_out = out * (1 + sum(REPAIR_RATE))
                cost = (expected_in * price[0] + expected_out * price[1]) / 1_000_000
                total += cost * reps
                rows.append(
                    {
                        "model": model,
                        "ticket": tid,
                        "arm": arm,
                        "prompt_tokens": prompt,
                        "system_chars": len(system),
                        "user_chars": len(user),
                        "answer_tokens": out,
                        "expected_input": round(expected_in),
                        "expected_output": round(expected_out),
                        "cost_per_run": cost,
                        "p_answer_chars": len(p_answer),
                    }
                )
    return {
        "rows": rows,
        "total_usd": total,
        "runs": len(rows) * reps,
        "counted": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }


def render_estimate(est: Mapping[str, Any]) -> str:
    lines = [
        "| model | ticket | arm | prompt tok | system chars | user chars | answer tok "
        "| exp. in | exp. out | $/run |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in est["rows"]:
        lines.append(
            f"| {r['model']} | {r['ticket']} | {r['arm']} | {r['prompt_tokens']} "
            f"| {r['system_chars']} | {r['user_chars']} | {r['answer_tokens']} "
            f"| {r['expected_input']} | {r['expected_output']} | {r['cost_per_run']:.4f} |"
        )
    how = "count_tokens API" if est["counted"] else f"chars / {CHARS_PER_TOKEN}"
    lines.append(
        f"\n{est['runs']} runs, expected total ${est['total_usd']:.2f} (prompt tokens by "
        f"{how}; repair rates {REPAIR_RATE}; no prompt caching assumed)"
    )
    return "\n".join(lines)


# ---- CLI ------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m bench.edit_slice.run", description=__doc__)
    p.add_argument("--model", default="claude-sonnet-5")
    p.add_argument("--arm", default="P,L", help="P, L or P,L")
    p.add_argument(
        "--tickets",
        default="all",
        help=(
            "comma-separated ticket ids, all (the template's t1-t18), repos (t19-t24) "
            "or round3 (all of t1-t24)"
        ),
    )
    p.add_argument("--reps", type=int, default=1)
    p.add_argument("--out", default="results/edit_slice/results.jsonl")
    p.add_argument("--max-repairs", type=int, default=MAX_REPAIRS)
    p.add_argument(
        "--max-tokens",
        type=int,
        default=MAX_TOKENS,
        help=f"output limit per model call (default {MAX_TOKENS})",
    )
    p.add_argument("--effort", default="high")
    p.add_argument("--hints", action="store_true", help="keep the Lattice guide's Advice")
    p.add_argument(
        "--dry-run-reference",
        action="store_true",
        help="feed the hand-written references through the arms (no API call)",
    )
    p.add_argument(
        "--estimate",
        action="store_true",
        help="print prompt sizes and the live run's cost estimate",
    )
    p.add_argument("--estimate-models", default="claude-sonnet-5,claude-haiku-4-5")
    return p


def _tickets(spec: str) -> list[str]:
    if spec == "all":
        return [t.id for t in TICKETS]
    if spec == "repos":
        return [t.id for t in REPO_TICKETS]
    if spec == "round3":
        return [t.id for t in ALL_TICKETS]
    ids = [s.strip() for s in spec.split(",") if s.strip()]
    for tid in ids:
        ticket(tid)
    return ids


def main(argv: Sequence[str] | None = None) -> int:
    from bench.envfile import load_env_file

    load_env_file()
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    args = _parser().parse_args(argv)
    tickets = _tickets(args.tickets)
    arms: list[Arm] = [a.strip() for a in args.arm.split(",") if a.strip()]  # type: ignore[misc]
    for arm in arms:
        if arm not in ARMS:
            raise SystemExit(f"unknown arm {arm!r}")
    if args.estimate:
        models = [m.strip() for m in args.estimate_models.split(",") if m.strip()]
        print(render_estimate(estimate(models, tickets, args.reps)))
        return 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    artefact_root = out.with_name(out.name + ".artefacts")
    for rep in range(args.reps):
        for tid in tickets:
            for arm in arms:
                try:
                    if args.dry_run_reference:
                        record = dry_run(arm, tid)
                        record.rep = rep
                    else:
                        from lattice.llm.client import ClaudeGenerator

                        generator = ClaudeGenerator(
                            model=args.model, max_tokens=args.max_tokens, effort=args.effort
                        )
                        record = run_arm(
                            arm,
                            tid,
                            generator,
                            model=args.model,
                            rep=rep,
                            max_repairs=args.max_repairs,
                            hints=args.hints,
                            max_tokens=args.max_tokens,
                            artefact_root=artefact_root,
                        )
                except Exception as exc:  # noqa: BLE001 -- one run's harness crash is recorded
                    record = RunRecord(
                        tid,
                        arm,
                        "reference" if args.dry_run_reference else args.model,
                        rep,
                        outcome="error",
                        detail=f"{type(exc).__name__}: {exc}"[:_DETAIL_LIMIT],
                        dry_run=args.dry_run_reference,
                    )
                with out.open("a") as f:
                    f.write(record.to_json() + "\n")
                print(
                    f"{record.model} {arm} {tid} rep{rep}: {record.outcome} "
                    f"rounds={record.rounds} ${record.cost_usd:.4f}",
                    file=sys.stderr,
                )
    from bench.edit_slice.report import load, render

    print(render(load([out])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
