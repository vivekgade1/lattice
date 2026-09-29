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

**Round 5 (spec 2026-09-28-lattice-round5-decision-experiment.md).** Three more arms:

* **Q (Python plus the guarantee checks).** Arm P's start state, answer format and user
  message; the system prompt is :func:`bench.edit_slice.prompts.q_system_prompt` (P's plus
  the statement of the checked policies). After the smoke run, :mod:`lattice.pycheck`
  checks the candidate (:func:`check_round`): its findings (code, file, line, message,
  fix hint; :func:`render_findings`, the format family of arm L's diagnostics) are added
  to the repair feedback, and a round that passed the smoke run but has findings is
  repaired (problem ``check``). Findings on the final round do not change scoring: the
  oracle decides. ``round_findings`` records each round's codes (None: never built).
* **L+ / Q+ (the context aids, §7).** Arm L / Q with (a) two retrieved worked examples
  before the user message and (b) the "common mistakes -> exact fix" table appended to
  the system prompt, and nothing else (:func:`arm_prompt`, :mod:`bench.edit_slice.aids`);
  ``examples`` records the retrieved tickets.

Arm L's (and L+'s) checker feedback also shows each diagnostic's machine fix
(``Diagnostic.fix``) when the checker offers one, as arm Q's hints carry theirs.

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
results.jsonl [--max-tokens 16000] [--dry-run-reference | --dry-run-partial]`` (``--tickets
all``: the template's t1-t18; ``repos``: the held-out repos' t19-t24; ``round3``: every
ticket, t1-t24; ``round4``: t1-t30 (round3 plus N4's fresh held-out set, t25-t30, no
Lattice reference); ``option1``: the option-1 pilot's t31-t38 on the multi-vendor app;
``option1b``: its atomicity pilot, t39-t44;
or comma-separated ids); ``--estimate`` prints the prompt sizes and the
live run's cost estimate instead (no generation). The dry run feeds the hand-written
references through each arm (no API call); arm L on t13-t18 has no reference to feed
(they are held out from the language work, see :mod:`bench.edit_slice.tickets`) and is
reported as ``no_reference``. ``--dry-run-partial`` feeds each reference as a partial
answer (endpoint-scoped edits, diffed against the start down to the top-level names
that changed -- :func:`partial_reference_answer`) instead of the whole file.

``--backend cli`` (the only choice, and the default) is the live transport: the local
``claude -p`` (:class:`lattice.llm.cli_client.ClaudeCliClient`); the API-key transport is
disabled in this project. The record's ``backend`` says which one ran. ``--resume`` skips
(model, arm, ticket, rep) tuples already in ``--out``, so a round a CLI usage limit cut
short can continue without redoing finished work (a usage limit stops the whole run --
exit code 3 -- rather than being recorded as a failure). ``--workers N`` (default 1)
runs that many (ticket, arm, rep) tasks concurrently; the JSONL is still written in the
same order a ``--workers 1`` run would produce, by a single writer on the main thread.
"""

from __future__ import annotations

import argparse
import ast
import contextvars
import dataclasses
import difflib
import json
import logging
import shutil
import statistics
import sys
import tempfile
import threading
import time
import types
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
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
from bench.edit_slice.tickets import (
    ALL_TICKETS,
    OPTION1,
    OPTION1B,
    REPO_TICKETS,
    ROUND4_TICKETS,
    TICKETS,
    Ticket,
    ticket,
)
from bench.edit_slice.variant import app_source, load_variant
from bench.metrics import cost_usd
from lattice.llm.cli_client import CliError, CliUsageLimitError
from lattice.llm.client import Generation, GenerationRefused, Generator, ScriptedGenerator

Arm = Literal["P", "L", "Q", "L+", "Q+"]
ARMS: tuple[Arm, ...] = ("P", "L")  # rounds 1-4
# round 5 (spec 2026-09-28): Q = P + pycheck; L+ / Q+ = L / Q + the context aids (§7)
ROUND5_ARMS: tuple[Arm, ...] = ("P", "L", "Q", "L+", "Q+")
MAX_REPAIRS = 2
MAX_TOKENS = 16000  # the default output limit per model call (--max-tokens)
MAX_CONSECUTIVE_CLI_ERRORS = 3  # this many CliError records in a row stop the run (exit 4)
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
HELD_OUT_REPOS = ("taskflow", "justnotes", "multivendor")


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


def multivendor_app(db_path: str | Path) -> Any:
    """The original multi-vendor marketplace (arm P's start; option 1)."""
    return _original("multivendor").make_app(db_path)


APP_FACTORIES = {
    "taskflow": f"{__name__}:taskflow_app",
    "justnotes": f"{__name__}:justnotes_app",
    "multivendor": f"{__name__}:multivendor_app",
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

    return emit_into_app(programs, profile_src, out, app_factory=APP_FACTORIES[repo], lifted=lifted)


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


def base_arm(arm: str) -> str:
    """The arm without its aids: L+ -> L, Q+ -> Q."""
    return arm.removesuffix("+")


def language(arm: str) -> str:
    """What the arm's model edits: ``"P"`` (Python: P, Q, Q+) or ``"L"`` (L, L+)."""
    return "L" if base_arm(arm) == "L" else "P"


def is_aided(arm: str) -> bool:
    return arm.endswith("+")


def with_section(system: str, section: str) -> str:
    """``system`` with ``section`` appended (one blank line between)."""
    return system.rstrip("\n") + "\n\n" + section.rstrip("\n") + "\n"


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


def q_prompt(ticket_id: str) -> tuple[str, str]:
    """(system, first user message) of arm Q: arm P's user message, the Q system prompt
    (arm P's plus the statement of the checked policies)."""
    _, user = p_prompt(ticket_id)
    return prompts.q_system_prompt(ticket(ticket_id)), user


def arm_prompt(arm: str, ticket_id: str, *, hints: bool = False) -> tuple[str, str]:
    """(system, first user message) of any arm. L+ and Q+ are their base arm's prompt
    with the two aids (:mod:`bench.edit_slice.aids`) and nothing else: the mistakes
    table appended to the system prompt, the worked examples before the user message."""
    from bench.edit_slice import aids

    base = base_arm(arm)
    if base == "P" and arm == "P":
        system, user = p_prompt(ticket_id)
    elif base == "L":
        system, user = l_prompt(ticket_id, hints=hints)
    elif base == "Q":
        system, user = q_prompt(ticket_id)
    else:
        raise ValueError(f"unknown arm {arm!r}")
    if is_aided(arm):
        lang: aids.Lang = "lattice" if base == "L" else "python"
        system = with_section(system, aids.mistakes_section(lang))
        user = aids.worked_examples(ticket_id, lang) + "\n\n" + user
    return system, user


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
    # arms Q / Q+: each round's pycheck finding codes, aligned with round_problems (None:
    # a round never built -- no files, truncated, refused -- so never checked)
    round_findings: list[list[str] | None] = field(default_factory=list)
    # arms L+ / Q+: the retrieved worked examples' tickets
    examples: list[str] = field(default_factory=list)
    # each round's stop reason (end_turn / max_tokens / refusal / ...)
    stop_reasons: list[str | None] = field(default_factory=list)
    # the oracle's per-case tally (equal / equal_under_rule / deviation / silent / loud)
    cases: dict[str, int] = field(default_factory=dict)
    cases_generated: int = 0
    cases_specific: int = 0
    failed_cases: list[str] = field(default_factory=list)
    # the failed cases whose verdict was silent (the option-1 pilot's mechanism gate)
    silent_cases: list[str] = field(default_factory=list)
    # tokens and cost over every call of the run (refused calls included)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    # how cost_usd was priced, comma-joined over the run's calls (as served_model joins
    # served models): "cli_list_estimate" for a call the CLI transport itself priced
    # (its total_cost_usd); empty for a call priced here instead from usage tokens and
    # bench.metrics.PRICES (every API-arm call, and a refused CLI call, which carries no
    # reported cost)
    cost_basis: str = ""
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
    backend: str = "api"  # transport for live model calls: api (Anthropic SDK) or cli (`claude -p`)
    # the run's artefact directory (every round's output and candidate files), if kept
    artefacts: str | None = None
    # whole (every answer resent everything) or partial (a round used the merge-by-name
    # option -- an omitted name, a `# delete:`, or a profile delta -- on any file)
    answer_mode: str = "whole"
    truncated: bool = False  # any round was cut off at the output limit (see round_problems)

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
    # no_code | truncated | bad_file | merge | start | static | smoke | check | refused
    problem: str | None = None
    feedback: str = ""
    code: tuple[int, int] = (0, 0)  # (lines, AST nodes) of the code that runs
    cleanup: Callable[[], None] = lambda: None
    partial: bool = False  # the answer used the merge-by-name option on some file
    findings: list[str] | None = None  # arm Q: pycheck's finding codes (None: not checked)


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
        merged: dict[str, str] = {}
        partial = False
        for name, src in files.items():
            rel = name.removeprefix("./").removeprefix(prompts.APP_PREFIX)
            if not rel.endswith(".py") or rel.startswith("/") or ".." in rel.split("/"):
                return Candidate(
                    problem="bad_file",
                    feedback=f"`{name}` is not a Python file of the app (answer with paths "
                    "such as `app/api/routes/items.py`).",
                )
            base = self.edits.get(rel, p_view(self.overlay, rel, self.repo))
            try:
                result = prompts.merge_top_level(base, src, allow_constants=False)
            except prompts.PartialAnswerError as exc:
                return Candidate(problem="merge", feedback=f"`{name}`: {exc}")
            merged[rel] = result.text
            partial = partial or result.partial
        self.edits.update(merged)
        variant = load_variant({**self.overlay, **self.edits}, repo=self.repo)
        after = self.edited()[1]
        return Candidate(
            factory=variant.make_app, code=_python_size(list(after.values())), partial=partial
        )

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


class ProfileDeltaError(ValueError):
    """A ``profile.delta`` (:func:`bench.edit_slice.prompts.parse_profile_delta`) could
    not be applied: an unknown table/column/type, or a ``profile.py`` shape too far from
    the generated convention (``ApiProfile(tables=..., responses=...)`` naming module-
    level ``Schema.from_pairs(...)``/dict assignments) for this add-only edit to make.
    The complete ``profile.py`` is always accepted instead."""


def _find_call(tree: ast.AST, func_name: str) -> ast.Call | None:
    return next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == func_name
        ),
        None,
    )


def _kwarg(call: ast.Call, name: str) -> ast.expr | None:
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


def _module_assign(tree: ast.Module, name: str) -> ast.Assign | ast.AnnAssign | None:
    """The top-level ``name = ...`` or ``name: T = ...`` assignment (``RESPONSES``'s is
    usually annotated)."""
    for stmt in tree.body:
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == name
        ):
            return stmt
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            if stmt.target.id == name:
                return stmt
    return None


def _splice(source: str, stmt: ast.stmt, new_text: str) -> str:
    """``source`` with ``stmt``'s lines replaced by ``new_text``."""
    lines = source.splitlines()
    start = min([d.lineno for d in getattr(stmt, "decorator_list", [])] + [stmt.lineno])
    return "\n".join([*lines[: start - 1], new_text, *lines[stmt.end_lineno :]])


def _list_kwarg_add(call: ast.Call, name: str, value: str) -> None:
    """Add ``value`` to ``call``'s list keyword argument ``name`` (creating it, e.g.
    ``private=[...]``, if ``call`` has none)."""
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.List):
            kw.value.elts.append(ast.Constant(value))
            return
    call.keywords.append(
        ast.keyword(arg=name, value=ast.List(elts=[ast.Constant(value)], ctx=ast.Load()))
    )


def apply_profile_delta(profile_src: str, delta: prompts.ProfileDelta) -> str:
    """``profile_src`` with ``delta``'s columns and response models added: an add-only
    edit of the ``Schema.from_pairs(...)`` and responses-dict module-level assignments
    the profile's ``ApiProfile(tables=..., responses=...)`` call names. Raises
    :class:`ProfileDeltaError` on an unknown table/type, an existing column, or a shape
    it does not recognise (the complete ``profile.py`` is always accepted instead)."""
    from lattice.typesys import SCALAR_NAMES

    source = profile_src
    for col in delta.columns:
        tree = ast.parse(source)
        api_call = _find_call(tree, "ApiProfile")
        tables = api_call and _kwarg(api_call, "tables")
        if api_call is None or not isinstance(tables, ast.Dict):
            raise ProfileDeltaError("profile.delta needs the complete profile.py here")
        var_name = next(
            (
                v.id
                for k, v in zip(tables.keys, tables.values, strict=True)
                if isinstance(k, ast.Constant) and k.value == col.table and isinstance(v, ast.Name)
            ),
            None,
        )
        if var_name is None:
            raise ProfileDeltaError(f"profile.delta: no table named {col.table!r}")
        assign = _module_assign(tree, var_name)
        if assign is None or not isinstance(assign.value, ast.Call):
            raise ProfileDeltaError(f"profile.delta: {col.table}'s schema is not editable here")
        call = assign.value
        scalar = SCALAR_NAMES.get(col.scalar_name)
        if scalar is None:
            raise ProfileDeltaError(f"profile.delta: unknown type {col.scalar_name!r}")
        if not call.args or not isinstance(call.args[0], ast.List):
            raise ProfileDeltaError(f"profile.delta: {col.table}'s schema is not editable here")
        pairs = call.args[0]
        if any(
            isinstance(e, ast.Tuple)
            and e.elts
            and isinstance(e.elts[0], ast.Constant)
            and e.elts[0].value == col.name
            for e in pairs.elts
        ):
            raise ProfileDeltaError(f"profile.delta: column {col.table}.{col.name} already exists")
        pairs.elts.append(
            ast.Tuple(
                elts=[
                    ast.Constant(col.name),
                    ast.Attribute(
                        value=ast.Name(id="Scalar", ctx=ast.Load()),
                        attr=scalar.name,
                        ctx=ast.Load(),
                    ),
                ],
                ctx=ast.Load(),
            )
        )
        _list_kwarg_add(call, "nullable", col.name)
        if col.private:
            _list_kwarg_add(call, "private", col.name)
        if col.admin_write:
            _list_kwarg_add(call, "admin_write", col.name)
        source = _splice(source, assign, ast.unparse(assign))
    for resp in delta.responses:
        tree = ast.parse(source)
        api_call = _find_call(tree, "ApiProfile")
        responses = api_call and _kwarg(api_call, "responses")
        if api_call is None or not isinstance(responses, ast.Name):
            raise ProfileDeltaError("profile.delta needs the complete profile.py here")
        assign = _module_assign(tree, responses.id)
        if assign is None or not isinstance(assign.value, ast.Dict):
            raise ProfileDeltaError("profile.delta: the responses mapping is not editable here")
        d = assign.value
        kept = [
            (k, v)
            for k, v in zip(d.keys, d.values, strict=True)
            if not (isinstance(k, ast.Constant) and k.value == resp.name)
        ]
        kept.append(
            (
                ast.Constant(resp.name),
                ast.Tuple(elts=[ast.Constant(f) for f in resp.fields], ctx=ast.Load()),
            )
        )
        d.keys, d.values = [k for k, _ in kept], [v for _, v in kept]
        source = _splice(source, assign, ast.unparse(assign))
    return source


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
        merged: dict[str, str] = {}
        partial = False
        for name, src in files.items():
            if name == prompts.PROFILE_DELTA_FILE:
                try:
                    delta = prompts.parse_profile_delta(src)
                    base_profile = merged.get(
                        prompts.PROFILE_FILE, self.state[prompts.PROFILE_FILE]
                    )
                    merged[prompts.PROFILE_FILE] = apply_profile_delta(base_profile, delta)
                except (prompts.PartialAnswerError, ProfileDeltaError) as exc:
                    return Candidate(problem="merge", feedback=f"`{name}`: {exc}")
                partial = True
                continue
            if name == prompts.PROFILE_FILE:
                merged[name] = src
                continue
            if not name.endswith(".lattice.py"):
                return Candidate(
                    problem="bad_file",
                    feedback=f"`{name}` is neither a Lattice program (`<router>.lattice.py`), "
                    f"`{prompts.PROFILE_FILE}` nor `{prompts.PROFILE_DELTA_FILE}`.",
                )
            try:
                result = prompts.merge_top_level(self.state.get(name), src)
            except prompts.PartialAnswerError as exc:
                return Candidate(problem="merge", feedback=f"`{name}`: {exc}")
            merged[name] = result.text
            partial = partial or result.partial
        self.state.update(merged)
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
                reports.append(f"{name}:\n{render_for_llm(errors, fixes=True)}")
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
                # no Exact fix: a joined-program diagnostic's line/col are the joined
                # program's, not a line of the model's own file
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
            partial=partial,
        )


CHECK_INTRO = "The guarantee checks found problems in the edited code:"


def render_findings(findings: Sequence[Any]) -> str:
    """pycheck findings as repair feedback, in the format family of arm L's checker
    diagnostics (:func:`lattice.diagnostics.render_for_llm`): code, place, message, fix."""
    lines = [
        f"- {f.code} at {prompts.APP_PREFIX}{f.file}, line {f.line}: {f.message}. Fix: {f.hint}"
        for f in findings
    ]
    return CHECK_INTRO + "\n" + "\n".join(lines)


def check_round(repo: str, files: Mapping[str, str], candidate: Candidate) -> None:
    """Arm Q's step after the smoke run: pycheck on the candidate's files. Findings are
    added to the round's feedback (after the smoke run's, when it failed); a round whose
    app started and passed the smoke run but has findings is repaired as ``check``."""
    from bench.edit_slice.qcheck import check_files

    findings = check_files(repo, files)
    candidate.findings = [f.code for f in findings]
    if not findings:
        return
    text = render_findings(findings)
    candidate.feedback = f"{candidate.feedback}\n\n{text}" if candidate.feedback else text
    if candidate.problem is None:
        candidate.problem = "check"


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
    if arm not in ROUND5_ARMS:
        raise ValueError(f"unknown arm {arm!r}")
    system, user = arm_prompt(arm, ticket_id, hints=hints)
    state: _PythonArm | _LatticeArm = _LatticeArm(t) if language(arm) == "L" else _PythonArm(t)
    checked = base_arm(arm) == "Q"
    if is_aided(arm):
        from bench.edit_slice.aids import retrieve

        record.examples = retrieve(ticket_id)
    record.system_chars, record.user_chars = len(system), len(user)
    record.prompt_tokens_est = round((len(system) + len(user)) / CHARS_PER_TOKEN)

    messages: list[dict[str, str]] = [{"role": "user", "content": user}]
    generations: list[Generation] = []
    candidate = Candidate()
    any_partial = False
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
                if checked:
                    record.round_findings.append(None)
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
            any_partial = any_partial or candidate.partial
            if candidate.factory is not None:
                result = smoke.run_smoke(ticket_id, candidate.factory)
                if not result.started:
                    candidate.problem = "start"
                elif result.failed:
                    candidate.problem = "smoke"
                candidate.feedback = smoke.smoke_feedback(result) if candidate.problem else ""
                if checked:
                    check_round(t.repo, state.candidate_files(), candidate)
            if checked:
                found = candidate.findings
                record.round_findings.append(None if found is None else list(found))
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
    record.answer_mode = "partial" if any_partial else "whole"
    record.truncated = "truncated" in record.round_problems
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
    meta: dict[str, Any] = {
        "stop_reason": stop_reason,
        "problem": candidate.problem,
        "feedback": candidate.feedback,
    }
    if candidate.findings is not None:
        meta["findings"] = candidate.findings
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
    # (served model, usage, latency, transport-reported cost or None, its cost_basis)
    usages = [(g.model, g.usage, g.latency_s, g.cost_usd, g.cost_basis) for g in generations]
    if refused is not None:
        # a transport-reported cost of the refused call(s) (the CLI's total_cost_usd),
        # else derived from usage below
        usages.append(
            (refused.model, refused.usage, refused.latency_s, refused.cost_usd, refused.cost_basis)
        )
    bases = []
    for served, usage, latency, call_cost, basis in usages:
        record.input_tokens += usage.total_input_tokens
        record.output_tokens += usage.output_tokens
        record.cache_read_tokens += usage.cache_read_input_tokens
        record.cache_creation_tokens += usage.cache_creation_input_tokens
        record.llm_s += latency
        record.cost_usd += (
            call_cost
            if call_cost is not None
            else cost_usd(
                served,
                input_tokens=usage.total_input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_input_tokens,
                cache_creation_tokens=usage.cache_creation_input_tokens,
            )
        )
        if basis:
            bases.append(basis)
    served_models = list(dict.fromkeys(m for m, *_ in usages if m))
    record.served_model = ",".join(served_models)
    record.cost_basis = ",".join(dict.fromkeys(bases))
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
    record.silent_cases = sorted(n for n, v in outcome.cases.items() if v.verdict == "silent")
    if outcome.error:
        record.detail = (record.detail + "\n" if record.detail else "") + outcome.error


# ---- the dry run ----------------------------------------------------------------------------


def reference_answer(arm: Arm, ticket_id: str) -> str | None:
    """The hand-written solution as an answer in the arm's format (None: a gap): the
    Python reference for P, Q and Q+, the Lattice reference for L and L+."""
    t = ticket(ticket_id)
    if language(arm) == "P":
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


def _partial_or_whole(before: str | None, after: str, *, allow_constants: bool) -> str:
    """``after`` as a partial-answer fragment against ``before`` (endpoint-scoped edits,
    spec 2026-09-27-lattice-surface-design.md §3) when merging that fragment back into
    ``before`` reproduces ``after`` exactly; the whole file otherwise (``before`` cannot
    express the change by name alone -- e.g. a new arm-P constant), or when ``before`` is
    None (a new file, nothing to diff against)."""
    if before is None:
        return after
    # merging only ever touches named items; a change to anything else (an import, or
    # for arm P another constant) cannot be expressed this way -- the whole file only
    if prompts.fixed_content(before, allow_constants=allow_constants) != prompts.fixed_content(
        after, allow_constants=allow_constants
    ):
        return after
    fragment = prompts.diff_top_level(before, after, allow_constants=allow_constants)
    merged = prompts.merge_top_level(before, fragment, allow_constants=allow_constants)
    same = prompts.named_items(merged.text, allow_constants=allow_constants) == prompts.named_items(
        after, allow_constants=allow_constants
    )
    return fragment if same else after


def partial_reference_answer(arm: Arm, ticket_id: str) -> str | None:
    """The hand-written reference as a *partial* answer (see :func:`_partial_or_whole`):
    each changed file, diffed against the start down to its top-level names wherever that
    is expressible, whole otherwise. Feeding this through :func:`run_arm` exercises the
    same merge-by-name code a live partial answer would (None: a gap, as
    :func:`reference_answer`)."""
    t = ticket(ticket_id)
    if language(arm) == "P":
        overlay = p_start(t)
        files = {
            prompts.APP_PREFIX + rel: _partial_or_whole(
                p_view(overlay, rel, t.repo), src, allow_constants=False
            )
            for rel, src in reference_files(ticket_id).items()
            if src != p_view(overlay, rel, t.repo)
        }
        return prompts.render_files(files)
    if not has_lattice_reference(ticket_id):
        return None
    programs, profile = l_start(t)
    start = {**programs, prompts.PROFILE_FILE: profile}
    files = {}
    for n, s in lattice_reference_files(ticket_id).items():
        if s == start.get(n):
            continue
        # profile.py itself is whole-file only (a partial profile change goes through
        # profile.delta instead, see prompts.PROFILE_DELTA_FILE); every program is merged
        files[n] = (
            s
            if n == prompts.PROFILE_FILE
            else _partial_or_whole(start.get(n), s, allow_constants=True)
        )
    return prompts.render_files(files)


def dry_run(arm: Arm, ticket_id: str, *, partial: bool = False) -> RunRecord:
    """The reference fed through ``arm`` (no API call); ``partial``: as a partial answer
    (:func:`partial_reference_answer`) instead of the whole file
    (:func:`reference_answer`) -- both should score every ticket the same way. Three ways
    arm L can have nothing to run: no import at all for the app (``not_imported``, a
    held-out repo before the importer's output is registered); current Lattice cannot
    express the ticket, a recorded gap (``not_expressible``); or the ticket is held out
    from the language and lifter work on purpose, with no Lattice reference written for
    it at all (``no_reference`` -- round 3's t13-t18, see :mod:`bench.edit_slice.tickets`)."""
    repo = ticket(ticket_id).repo
    lattice = language(arm) == "L"
    if lattice and not has_lattice_start(repo):
        return RunRecord(
            ticket_id,
            arm,
            "reference",
            outcome="not_imported",
            detail=f"arm {arm} on {repo!r} needs the importer's output (start_lattice)",
            dry_run=True,
        )
    if lattice and not has_lattice_reference(ticket_id) and ticket_id not in GAPS:
        return RunRecord(
            ticket_id,
            arm,
            "reference",
            outcome="no_reference",
            detail=f"{ticket_id!r} is held out: no Lattice reference is written for it",
            dry_run=True,
        )
    answer = (partial_reference_answer if partial else reference_answer)(arm, ticket_id)
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
    """Prompt tokens of (system, user), estimated as chars / 3.5. (The count_tokens API
    would need an API key, which no tool in this project uses.)"""
    return lambda system, user: round((len(system) + len(user)) / CHARS_PER_TOKEN)


def estimate(
    models: Sequence[str], tickets: Sequence[str], reps: int, arms: Sequence[str] = ARMS
) -> dict[str, Any]:
    """Prompt sizes per arm and ticket and the expected cost of the live run. An arm's
    expected answer is its language's reference (Q and Q+ answer like P, L+ like L)."""
    from bench.metrics import price_for

    rows = []
    total = 0.0
    for model in models:
        count = token_counter(model)
        price = price_for(model) or (0.0, 0.0)
        for tid in tickets:
            # arms L and L+ only where they have a start state (an import of the app)
            lattice = has_lattice_start(ticket(tid).repo)
            here = [a for a in arms if language(a) == "P" or lattice]
            answers = {a: reference_answer(a, tid) for a in here}
            p_answer = reference_answer("P", tid) or ""
            for arm in here:
                system, user = arm_prompt(arm, tid)
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
        "counted": False,
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
    p.add_argument(
        "--arm",
        default="P,L",
        help="comma-separated arms: P, L (rounds 1-4), Q, L+, Q+ (round 5)",
    )
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
        "--dry-run-partial",
        action="store_true",
        help="like --dry-run-reference, but each reference as a partial answer "
        "(endpoint-scoped edits), diffed against the start",
    )
    p.add_argument(
        "--estimate",
        action="store_true",
        help="print prompt sizes and the live run's cost estimate",
    )
    p.add_argument("--estimate-models", default="claude-sonnet-5,claude-haiku-4-5")
    p.add_argument(
        "--backend",
        choices=("cli",),
        default="cli",
        help="transport for a live model call: the local `claude -p` (the API-key "
        "transport is disabled in this project)",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="skip (model, arm, ticket, rep) tuples already in --out, so a round "
        "interrupted (e.g. by a CLI usage limit) can continue without redoing work",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=1,
        help="run this many (ticket, arm, rep) tasks concurrently (default 1)",
    )
    return p


def _tickets(spec: str) -> list[str]:
    if spec == "all":
        return [t.id for t in TICKETS]
    if spec == "repos":
        return [t.id for t in REPO_TICKETS]
    if spec == "round3":
        return [t.id for t in ALL_TICKETS]
    if spec == "round4":
        return [t.id for t in ROUND4_TICKETS]
    if spec == "option1":
        return list(OPTION1)
    if spec == "option1b":
        return list(OPTION1B)
    ids = [s.strip() for s in spec.split(",") if s.strip()]
    for tid in ids:
        ticket(tid)
    return ids


def _make_generator(args: argparse.Namespace) -> Generator:
    """The generator for a live (non-dry-run) call: the local ``claude -p`` CLI. The
    API-key transport is disabled in this project (:class:`lattice.llm.client.
    ApiKeyTransportDisabled`)."""
    from lattice.llm.cli_client import ClaudeCliClient

    return ClaudeCliClient(model=args.model, max_tokens=args.max_tokens, effort=args.effort)


def _completed(out: Path) -> set[tuple[str | None, str | None, str | None, int | None]]:
    """(model, arm, ticket, rep) tuples already written to ``out`` -- what ``--resume``
    skips, so a round interrupted (e.g. by a CLI usage limit) can continue without
    redoing work already on disk. A line that fails to parse is ignored (not counted as
    done): it is likely a partially-written line from an interrupted run. An ``error``
    record (the harness or the transport failed) is not done either: it is redone."""
    done: set[tuple[str | None, str | None, str | None, int | None]] = set()
    if not out.exists():
        return done
    with out.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("outcome") == "error":
                continue
            done.add((d.get("model"), d.get("arm"), d.get("ticket"), d.get("rep")))
    return done


def _run_one(
    arm: Arm, tid: str, rep: int, args: argparse.Namespace, artefact_root: Path
) -> RunRecord:
    """One (arm, ticket, rep)'s record. A harness-side failure (a bug, a bad ticket, a
    transient :class:`~lattice.llm.cli_client.CliError`) becomes an ``error`` record --
    except :class:`~lattice.llm.cli_client.CliUsageLimitError`, which is not caught here:
    it must stop the whole run (see ``main``), never be recorded as one run's failure."""
    dry = args.dry_run_reference or args.dry_run_partial
    try:
        if dry:
            record = dry_run(arm, tid, partial=args.dry_run_partial)
            record.rep = rep
        else:
            generator = _make_generator(args)
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
    except CliUsageLimitError:
        raise
    except Exception as exc:  # noqa: BLE001 -- one run's harness crash is recorded
        record = RunRecord(
            tid,
            arm,
            "reference" if dry else args.model,
            rep,
            outcome="error",
            failure="cli_error" if isinstance(exc, CliError) else "harness",
            detail=f"{type(exc).__name__}: {exc}"[:_DETAIL_LIMIT],
            dry_run=dry,
        )
    record.backend = args.backend
    return record


def main(argv: Sequence[str] | None = None) -> int:
    from bench.envfile import load_env_file

    load_env_file()
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    args = _parser().parse_args(argv)
    tickets = _tickets(args.tickets)
    arms: list[Arm] = [a.strip() for a in args.arm.split(",") if a.strip()]  # type: ignore[misc]
    for arm in arms:
        if arm not in ROUND5_ARMS:
            raise SystemExit(f"unknown arm {arm!r}")
    if args.estimate:
        models = [m.strip() for m in args.estimate_models.split(",") if m.strip()]
        print(render_estimate(estimate(models, tickets, args.reps, arms)))
        return 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.touch(exist_ok=True)  # so the report below can always load it, even 0 runs in
    artefact_root = out.with_name(out.name + ".artefacts")

    dry = args.dry_run_reference or args.dry_run_partial
    model_key = "reference" if dry else args.model
    tasks = [(rep, tid, arm) for rep in range(args.reps) for tid in tickets for arm in arms]
    if args.resume:
        done = _completed(out)
        tasks = [t for t in tasks if (model_key, t[2], t[1], t[0]) not in done]

    write_lock = threading.Lock()

    def _write(record: RunRecord) -> None:
        # a single writer (this closure only ever runs on the main thread, called on
        # each future's result in submission order) -- JSONL append safety needs no
        # lock for that alone, but the print alongside it shares stderr, so keep both
        # under one lock for tidy, non-interleaved output.
        with write_lock:
            with out.open("a") as f:
                f.write(record.to_json() + "\n")
            print(
                f"{record.model} {record.arm} {record.ticket} rep{record.rep}: "
                f"{record.outcome} rounds={record.rounds} ${record.cost_usd:.4f}",
                file=sys.stderr,
            )

    stopped_early = False
    status = 0
    cli_errors = 0  # consecutive CliError records, in submission order
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        # submit every task up front, then collect results in submission order: workers
        # run concurrently, but the JSONL/report stay in the same deterministic order a
        # --workers 1 run would produce.
        futures = [
            ex.submit(_run_one, arm, tid, rep, args, artefact_root) for rep, tid, arm in tasks
        ]
        for fut in futures:
            try:
                record = fut.result()
            except CliUsageLimitError as exc:
                print(f"usage limit reached, stopping cleanly: {exc}", file=sys.stderr)
                ex.shutdown(wait=False, cancel_futures=True)
                stopped_early, status = True, 3
                break
            _write(record)
            cli_errors = cli_errors + 1 if record.failure == "cli_error" else 0
            if cli_errors >= MAX_CONSECUTIVE_CLI_ERRORS:
                print(
                    f"{cli_errors} consecutive CLI errors (last: {record.detail[:300]}); "
                    "stopping the run: check the `claude` CLI (login, plan, network), then "
                    "rerun with --resume (error records are redone)",
                    file=sys.stderr,
                )
                ex.shutdown(wait=False, cancel_futures=True)
                stopped_early, status = True, 4
                break

    from bench.edit_slice.report import load, render

    print(render(load([out])))
    return status if stopped_early else 0


if __name__ == "__main__":
    raise SystemExit(main())
