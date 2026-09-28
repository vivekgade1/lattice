"""The prompts of the edit slice's two arms (plan 2026-09-27 thin slice, node E3).

Both arms get the same ticket text (``Ticket.text``, verbatim) and the files a developer
would open for it, and answer in the same strict format: every changed file, complete,
under a ``### <file>`` heading line followed by one fenced ``python`` block.

* **Arm P** (Python edit): a short system prompt (the app and the answer format); the
  user turn holds the ticket and the current source of the ticket's files plus their
  direct context (``models.py``, ``api/deps.py``, ``crud.py`` when a shown file imports
  it). A held-out repo's ticket (``Ticket.repo``) gets the same prompt about its own
  app (:func:`p_system`) and that app's context files (``Repo.context``).
* **Arm L** (Lattice edit): the existing Lattice API guide
  (:func:`lattice.llm.api_prompt.api_lattice_system_prompt`) with its generic output
  section replaced by a short "editing an imported app" section (what the profile
  settles for this app, the constructs the imported programs use, the answer format);
  the user turn holds the ticket, the touched router's Lattice program and the profile
  source.

Nothing here reads the ticket module's docstring or the oracle: the traps and the hidden
cases never reach a prompt (``tests/bench/test_edit_slice_run.py`` checks it).

**Endpoint-scoped edits (spec 2026-09-27-lattice-surface-design.md §3).** Both arms may
answer with only the top-level definitions they add or change, merged into the start
files by name (:func:`merge_top_level`): a name sent again replaces it, a new name is
appended, and a line ``# delete: <name>`` on its own removes one. Arm L's names are its
programs' endpoints, ``@procedure``/``@record``/``@step`` definitions and constants; arm
P's are a module's top-level functions and classes (not constants or imports -- return
the whole file for those). Arm L may also send a ``profile.delta`` file instead of the
complete profile (:func:`parse_profile_delta`, :data:`PROFILE_DELTA_FILE`): new columns
and response models only. Whole files are always accepted too, and the two arms describe
the option in matching words (:func:`_partial_note`,
``tests/bench/test_edit_slice_run.py``'s fairness tests).
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from bench.edit_slice.apps import repo
from bench.edit_slice.tickets import Ticket
from lattice.llm.api_prompt import api_lattice_system_prompt
from lattice.llm.prompt import _OUTPUT_FORMAT_SECTION

APP_PREFIX = "app/"
PROFILE_FILE = "profile.py"
PROFILE_DELTA_FILE = "profile.delta"

# ---- the answer format (both arms) ---------------------------------------------------------

_FILE_BLOCK_RE = re.compile(
    r"^###[ \t]+`?([^\s`]+)`?[ \t]*\n+```[ \t]*(?:python|py)?[ \t]*\n(.*?)^```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)


def answer_format(what: str, example: str) -> str:
    return (
        "## Answer format\n\n"
        f"Return {what}. Write each file as a heading line `### <file>` followed by one "
        "```python block holding the complete new content of that file, e.g.\n\n"
        f"### {example}\n```python\n...\n```\n\n"
        "Files you do not return stay as they are. No other code blocks; any prose "
        "outside the blocks is ignored."
    )


def parse_files(text: str) -> dict[str, str]:
    """``{file: content}`` from a response in the answer format ({} when none). A file
    named twice keeps its last block."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return {name: body for name, body in _FILE_BLOCK_RE.findall(normalized)}


def render_files(files: Mapping[str, str]) -> str:
    """Files in the answer format (the dry run's scripted answers use it too)."""
    return "\n\n".join(
        f"### {name}\n```python\n{src.rstrip(chr(10))}\n```" for name, src in files.items()
    )


# ---- partial answers: merge by top-level name (both arms) -----------------------------------


class PartialAnswerError(ValueError):
    """A partial answer could not be applied: ``# delete:`` of a name that is not
    present (:func:`merge_top_level`), or a malformed ``profile.delta`` line
    (:func:`parse_profile_delta`). Its message is used as repair feedback, so it names
    the offending name or line."""


_DELETE_RE = re.compile(r"^[ \t]*#\s*delete:\s*([A-Za-z_]\w*)[ \t]*$", re.MULTILINE)
_NAMED_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _named(stmt: ast.stmt, *, allow_constants: bool) -> str | None:
    """The top-level name ``stmt`` binds: a def/class name, or (when ``allow_constants``)
    a single-name assignment target, plain or annotated (``RESPONSES: dict[...] = \
    {...}``); None for anything else, including a multi-target or tuple-target
    assignment."""
    if isinstance(stmt, _NAMED_DEFS):
        return stmt.name
    if not allow_constants:
        return None
    if (
        isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
    ):
        return stmt.targets[0].id
    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        return stmt.target.id
    return None


def _stmt_source(lines: Sequence[str], stmt: ast.stmt) -> str:
    """``stmt``'s exact source text, its decorators included."""
    start = min([d.lineno for d in getattr(stmt, "decorator_list", [])] + [stmt.lineno])
    return "\n".join(lines[start - 1 : stmt.end_lineno])


def _leading_comment(source: str) -> str:
    """The lines before ``source``'s first top-level statement (e.g. ``# lattice: v1``);
    "" when ``source`` has no statement (or does not parse)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    if not tree.body:
        return ""
    first = tree.body[0]
    start = min([d.lineno for d in getattr(first, "decorator_list", [])] + [first.lineno])
    return "\n".join(source.splitlines()[: start - 1])


@dataclass(frozen=True)
class _Op:
    kind: str  # "set" | "delete"
    name: str
    text: str = ""
    line: int = 0


def _parse_ops(fragment: str, *, allow_constants: bool) -> list[_Op] | None:
    """``fragment``'s ``# delete:`` directives and named top-level statements, in text
    order. None when ``fragment`` is not valid Python (a caller should then treat it as
    an ordinary, unmerged whole-file answer -- the existing checker/smoke feedback on bad
    syntax is more specific than a merge error).

    A top-level statement with no mergeable name (an import; for arm P, a constant too)
    is silently skipped, exactly as if it had not been sent: it is not part of either
    arm's partial vocabulary, and always comes from the base file/its own whole-file
    resend instead (never an error by itself -- only ``# delete:`` of an unknown name is,
    see :func:`merge_top_level`)."""
    ops = [
        _Op("delete", m.group(1), line=fragment.count("\n", 0, m.start()) + 1)
        for m in _DELETE_RE.finditer(fragment)
    ]
    try:
        tree = ast.parse(fragment)
    except SyntaxError:
        return None
    lines = fragment.splitlines()
    for stmt in tree.body:
        name = _named(stmt, allow_constants=allow_constants)
        if name is None:
            continue
        ops.append(_Op("set", name, _stmt_source(lines, stmt), stmt.lineno))
    ops.sort(key=lambda o: o.line)
    return ops


def _blocks(source: str, *, allow_constants: bool) -> list[tuple[str | None, str, bool]]:
    """``source``'s top-level statements as ``(name, text, is_def)`` (``name`` None for a
    fixed, unmerged statement: an import, or -- for arm P -- any other constant;
    ``is_def``: a function/class, as opposed to a constant -- endpoints/steps come after
    every constant, see :func:`merge_top_level`)."""
    tree = ast.parse(source)
    lines = source.splitlines()
    return [
        (
            _named(stmt, allow_constants=allow_constants),
            _stmt_source(lines, stmt),
            isinstance(stmt, _NAMED_DEFS),
        )
        for stmt in tree.body
    ]


@dataclass(frozen=True)
class MergedFile:
    text: str
    partial: bool  # whether merging actually happened (a name was left out, or deleted)


def merge_top_level(
    base: str | None, fragment: str, *, allow_constants: bool = True
) -> MergedFile:
    """Merge ``fragment``'s named top-level statements into ``base`` by name: a name sent
    again replaces it (in place), a new name is added (see :func:`_place`: after what it
    uses, before what uses it), and a line ``# delete: <name>`` removes one. ``base``
    None: ``fragment`` is a new file, used as is. ``allow_constants``: whether a
    single-name assignment (a constant) is mergeable (arm L) or must stay in the whole
    file (arm P: only functions/classes are named).

    A whole-file answer (see :func:`_is_whole`; none deleted) is returned exactly as
    sent, its removals included (:attr:`MergedFile.partial` False), so this is also what a
    plain "return the whole file" answer goes through. Raises
    :class:`PartialAnswerError` only for ``# delete:`` of a name that is not present; a
    fragment with a Python syntax error is instead treated as an (unmerged) whole-file
    answer, so the existing checker/smoke feedback on bad syntax still applies."""
    if base is None:
        return MergedFile(fragment, False)
    frag_ops = _parse_ops(fragment, allow_constants=allow_constants)
    if frag_ops is None:
        return MergedFile(fragment, False)
    try:
        blocks = _blocks(base, allow_constants=allow_constants)
    except SyntaxError:
        # an earlier round's whole-file answer left ``base`` broken (a syntax error);
        # there is nothing to merge against, so this fragment stands alone too, and the
        # existing checker/smoke feedback on bad syntax applies as it did before merging
        return MergedFile(fragment, False)
    items = {name: text for name, text, _ in blocks if name is not None}
    base_names = frozenset(items)
    new_names: list[str] = []
    seen: set[str] = set()
    used_delete = False
    for op in frag_ops:
        if op.kind == "delete":
            if op.name not in items:
                raise PartialAnswerError(f"# delete: {op.name} -- no such top-level name")
            del items[op.name]
            used_delete = True
        else:
            if op.name not in items and op.name not in base_names:
                new_names.append(op.name)
            items[op.name] = op.text
            seen.add(op.name)
    header = _leading_comment(base)
    if not used_delete and _is_whole(fragment, blocks, header, base_names, seen):
        return MergedFile(fragment, False)
    kept = [
        (name, text if name is None else items[name], is_def)
        for name, text, is_def in blocks
        if name is None or name in items
    ]
    for name in new_names:
        if name in items:
            _place(kept, name, items[name], _is_def_text(items[name]))
    rebuilt = [text for _, text, _ in kept]
    body = "\n\n\n".join(rebuilt)
    text = f"{header}\n\n{body}\n" if header else f"{body}\n"
    return MergedFile(text, True)


def _is_whole(
    fragment: str,
    blocks: list[tuple[str | None, str, bool]],
    header: str,
    base_names: frozenset[str],
    seen: set[str],
) -> bool:
    """Whether ``fragment`` (with no ``# delete:``) is a whole-file answer, used verbatim
    -- its removals, renames and fixed statements included -- rather than a partial one
    merged by name. Whole when it starts with an import block; or carries every one of
    ``base``'s fixed statements (imports, and for arm P constants too) verbatim; or, for
    a base with no fixed statements (a Lattice program), starts with base's pragma line
    (``# lattice: v1``); or sends every one of base's names again with a header."""
    try:
        tree = ast.parse(fragment)
    except SyntaxError:
        return True
    if tree.body and isinstance(tree.body[0], ast.Import | ast.ImportFrom):
        return True
    fixed = [text for name, text, _ in blocks if name is None]
    if fixed:
        lines = fragment.splitlines()
        sent = {_stmt_source(lines, stmt) for stmt in tree.body}
        if all(text in sent for text in fixed):
            return True
    else:
        pragma = header.splitlines()[0].strip() if header.strip() else ""
        own = _leading_comment(fragment)
        if pragma and own and own.splitlines()[0].strip() == pragma:
            return True
    looks_whole = not header or _leading_comment(fragment)
    return bool(looks_whole) and base_names <= seen


def _is_def_text(text: str) -> bool:
    tree = ast.parse(text)
    return bool(tree.body) and isinstance(tree.body[0], _NAMED_DEFS)


def _names_used(text: str) -> frozenset[str]:
    """Every bare name ``text`` (one top-level statement) loads, stores or calls."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return frozenset()
    return frozenset(n.id for n in ast.walk(tree) if isinstance(n, ast.Name))


def _names_defined(name: str | None, text: str) -> frozenset[str]:
    """The top-level names statement ``text`` binds: its merge name, or -- for a fixed
    statement -- its import aliases / assignment targets (``router = APIRouter()``)."""
    if name is not None:
        return frozenset({name})
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return frozenset()
    found: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.Import | ast.ImportFrom):
            found |= {(a.asname or a.name).split(".")[0] for a in stmt.names}
        elif isinstance(stmt, ast.Assign | ast.AnnAssign | ast.AugAssign):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            for target in targets:
                found |= {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
    return frozenset(found)


def _place(
    kept: list[tuple[str | None, str, bool]], name: str, text: str, is_def: bool
) -> None:
    """Insert the new top-level ``name`` into ``kept`` (in place). By default right after
    the leading imports/constants, before the first def/class kept from base: a new
    constant or step this way precedes any statement that might use it (L202/L604), and
    a new arm-P route precedes base's parameterised ones (FastAPI matches routes in
    declaration order: ``/stats`` must come before ``/{id}``). Two constraints move it:
    it goes before the first statement that uses it, and -- winning over that -- after
    the last statement defining a name it uses (a new ``class X: data:
    list[ItemPublic]`` after ``ItemPublic``; a new endpoint after an earlier round's step
    it calls, L604)."""
    uses = _names_used(text) - {name}
    lo = 0
    for i, (other, other_text, _) in enumerate(kept):
        if _names_defined(other, other_text) & uses:
            lo = i + 1
    hi = next(
        (i for i, (other, t, _) in enumerate(kept) if other != name and name in _names_used(t)),
        None,
    )
    first_def = next((i for i, (_, _, d) in enumerate(kept) if d), len(kept))
    pos = first_def if hi is None else min(hi, first_def)
    kept.insert(max(pos, lo), (name, text, is_def))


def diff_top_level(before: str, after: str, *, allow_constants: bool = True) -> str:
    """The partial-answer fragment (see :func:`merge_top_level`) that turns ``before``
    into ``after``: every top-level name ``after`` adds or changes, each complete, plus a
    ``# delete: <name>`` line for one ``before`` has and ``after`` does not. Used by the
    partial dry run (:func:`bench.edit_slice.run.dry_run`) to feed a hand-written
    reference through an arm as a partial answer instead of the whole file; not every
    change is expressible this way (e.g. arm P's constants), so a caller should verify by
    merging the fragment back (:func:`bench.edit_slice.run._partial_or_whole` does)."""
    before_items = named_items(before, allow_constants=allow_constants)
    after_blocks = _blocks(after, allow_constants=allow_constants)
    after_items = {n: t for n, t, _ in after_blocks if n}
    parts = [text for name, text, _ in after_blocks if name and before_items.get(name) != text]
    parts += [f"# delete: {name}" for name in before_items if name not in after_items]
    return "\n\n\n".join(parts)


def named_items(source: str, *, allow_constants: bool = True) -> dict[str, str]:
    """``source``'s top-level named definitions/constants (mergeable per
    ``allow_constants``), by name -- ignoring headers, imports and (for arm P) other
    constants. Two sources with the same named items are equivalent up to what
    :func:`merge_top_level` can express (used to check a partial-answer round trip,
    e.g. by the partial dry run's :func:`bench.edit_slice.run._partial_or_whole`)."""
    blocks = _blocks(source, allow_constants=allow_constants)
    return {name: text for name, text, _ in blocks if name}


def fixed_content(source: str, *, allow_constants: bool = True) -> tuple[str, ...]:
    """``source``'s top-level statements that :func:`merge_top_level` never touches
    (imports, plus -- for arm P -- other constants), in order. Merging cannot express a
    change that alters this (only the whole file can), so a caller comparing two sources
    for a partial-answer round trip must check this alongside :func:`named_items`."""
    blocks = _blocks(source, allow_constants=allow_constants)
    return tuple(text for name, text, _ in blocks if not name)


# ---- profile deltas (arm L) -------------------------------------------------------------------

_COLUMN_RE = re.compile(
    r"^column\s+([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*:\s*([A-Za-z]+)\s*(?:,\s*(.*))?$"
)
_RESPONSE_RE = re.compile(r"^response\s+([A-Za-z_]\w*)\s*:\s*(.+)$")


@dataclass(frozen=True)
class ColumnDelta:
    table: str
    name: str
    scalar_name: str  # a Lattice type spelling, e.g. "DateTime" (lattice.typesys.Scalar)
    private: bool = False
    admin_write: bool = False


@dataclass(frozen=True)
class ResponseDelta:
    name: str
    fields: tuple[str, ...]


@dataclass(frozen=True)
class ProfileDelta:
    columns: tuple[ColumnDelta, ...] = ()
    responses: tuple[ResponseDelta, ...] = field(default_factory=tuple)


def parse_profile_delta(text: str) -> ProfileDelta:
    """Parse a :data:`PROFILE_DELTA_FILE` (see :data:`_PROFILE_DELTA_DOC` for the format
    documented to the model). Raises :class:`PartialAnswerError` on a line matching
    neither directive."""
    columns: list[ColumnDelta] = []
    responses: list[ResponseDelta] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if m := _COLUMN_RE.match(line):
            table, name, scalar_name, flags = m.groups()
            flag_set = {f.strip() for f in (flags or "").split(",") if f.strip()}
            unknown = flag_set - {"nullable", "private", "admin_write"}
            if unknown:
                raise PartialAnswerError(f"line {lineno}: unknown column flag {sorted(unknown)}")
            columns.append(
                ColumnDelta(
                    table, name, scalar_name, "private" in flag_set, "admin_write" in flag_set
                )
            )
        elif m := _RESPONSE_RE.match(line):
            name, fields = m.groups()
            responses.append(
                ResponseDelta(name, tuple(f.strip() for f in fields.split(",") if f.strip()))
            )
        else:
            raise PartialAnswerError(
                f"line {lineno}: not a `column <Table>.<name>: <Type>` or `response "
                f"<Name>: <field>, ...` directive: {raw!r}"
            )
    return ProfileDelta(tuple(columns), tuple(responses))


_PROFILE_DELTA_DOC = f"""## Profile delta

Instead of the complete `{PROFILE_FILE}`, you may return a `{PROFILE_DELTA_FILE}` file \
naming only what it adds, one directive per line (blank lines and `#` comments ignored):

- `column <Table>.<name>: <Type>` adds a new, nullable column, e.g. `column \
Item.deleted_at: DateTime`. Add `, private` or `, admin_write` to mark it.
- `response <Name>: <field>, <field>, ...` adds a new response model, e.g. `response \
ItemStats: count, latest_created_at`.

Any other profile change (a new table, native, role, ...) still needs the complete \
`{PROFILE_FILE}`."""


def _partial_note(units: str, delete_example: str) -> str:
    """The partial-answer option, in the same words for both arms (arm-specific is only
    ``units`` and the example name); a fairness test checks they stay in step."""
    return (
        f"Instead of the complete files, you may return only the {units} you add or "
        "change, each complete; anything you do not mention stays as it is. Merge is by "
        "name: a name you send again replaces it, a new name is added, and a line `# "
        f"delete: {delete_example}` on its own removes one. Whole files are always "
        "accepted too."
    )


# ---- arm P ---------------------------------------------------------------------------------

def _p_system(about: str, package: str, imports: str, example: str) -> str:
    return f"""You are an expert backend engineer working on an existing FastAPI app: \
{about}. The app's package is imported as `{package}` (files are shown relative to it as \
`app/...`, and {imports}). Implement the ticket you are given by editing the app's files.

The edited app is imported fresh and started unattended; its tables are created from \
the models when a database is new. Keep the app's existing behaviour except where the \
ticket changes it.

{answer_format("every file you change or add", example)}

{_partial_note("top-level functions or classes of a module", "old_helper")}
"""


P_SYSTEM = _p_system(
    repo("template").about,
    repo("template").package,
    "imports inside the app use that package name",
    "app/api/routes/items.py",
)
# a held-out repo's modules import each other by package name (TaskFlow) or relatively
# (JustNotes): the prompt says which, as the template's says it
_P_IMPORTS = {
    "taskflow": "imports inside the app use that package name",
    "justnotes": "modules inside the app import each other relatively",
}
_P_EXAMPLE = {"taskflow": "app/routes.py", "justnotes": "app/api/notes.py"}


def p_system(repo_name: str = "template") -> str:
    """Arm P's system prompt for the app ``repo_name`` (the template's is :data:`P_SYSTEM`)."""
    if repo_name == "template":
        return P_SYSTEM
    app = repo(repo_name)
    return _p_system(app.about, app.package, _P_IMPORTS[app.name], _P_EXAMPLE[app.name])


def p_context_files(ticket: Ticket, files: Mapping[str, str]) -> tuple[str, ...]:
    """The files shown to arm P: the ticket's own, then models.py and api/deps.py, then
    crud.py when a shown file imports it (paths relative to the app package); for a
    held-out repo's ticket, the ticket's own then the app's context files."""
    if ticket.repo != "template":
        return tuple(dict.fromkeys([*ticket.files, *repo(ticket.repo).context]))
    shown = list(dict.fromkeys([*ticket.files, "models.py", "api/deps.py"]))
    if any(re.search(r"\bcrud\b", files[f]) for f in shown if f in files):
        shown.append("crud.py")
    return tuple(shown)


def p_user_message(ticket: Ticket, files: Mapping[str, str]) -> str:
    """The ticket and the current source of the files to open (``files``: path relative
    to the app package -> the start state's source)."""
    parts = [f"## Ticket: {ticket.title}\n\n{ticket.text.strip()}", "## Current files"]
    for rel in p_context_files(ticket, files):
        parts.append(f"### {APP_PREFIX}{rel}\n```python\n{files[rel].rstrip(chr(10))}\n```")
    return "\n\n".join(parts)


# ---- arm Q (round 5: Python plus the guarantee checks) --------------------------------------


def q_policy_statement(repo_name: str = "template") -> str:
    """The policies arm Q's edit is checked for (:mod:`lattice.pycheck`), stated for the app
    ``repo_name``: derived once from the original app, identical for every ticket."""
    from bench.edit_slice.qcheck import repo_policy
    from lattice.pycheck import describe_policy

    return describe_policy(repo_policy(repo_name))


def q_system_prompt(ticket: Ticket) -> str:
    """Arm Q's system prompt: arm P's, plus the statement of the checked policies. The user
    message is arm P's (:func:`p_user_message`); the repair feedback adds pycheck's
    findings to the smoke run."""
    return p_system(ticket.repo).rstrip("\n") + "\n\n" + q_policy_statement(ticket.repo)


# ---- arm L ---------------------------------------------------------------------------------

L_EDITING = f"""## Editing an imported app

The programs you edit were imported from an existing FastAPI app (one Lattice program \
per router file, e.g. `items.lattice.py` for `app/api/routes/items.py`); they are \
checked and emitted back into that app as its routes. The profile module \
(`{PROFILE_FILE}`) describes the app: its tables (`Schema.from_pairs`: columns, key, \
nullable columns, owner column), its response models (`RESPONSES`), its natives and its \
conventions. This app's conventions replace the generic ones above:

- `@endpoint(..., response="ItemPublic")` names the response model: the returned \
columns must be exactly its fields (`select` them). A `Page` answers \
`{{"data": [...], "count": n}}`; `page(..., total=True)` gives `count`, the number of \
rows before paging.
- Errors answer `{{"detail": "<message>"}}` with the message given to `NotFound(...)`, \
`Forbidden(...)`, `Conflict(...)` or `BadRequest(...)`; 401 and 422 are automatic.
- Parameter types also include `Uuid` and `DateTime` (ISO 8601; a `DateTime` default is \
a string with a timezone, e.g. `"2024-01-01T00:00:00Z"`).
- `p: Str(...) = UNSET` is an optional body field: not sent, the column keeps its \
value. It may be used only directly as an `update()` value or an `insert()` value, or \
in `sent(p)`.
- `require(t, owner_is(user), else_=Forbidden("..."))`: every row of t belongs to the \
caller (Admin callers pass).
- `native.<name>(...)` calls a function the profile declares in `natives`.
- A new column or response model is declared in the profile: return the complete \
`{PROFILE_FILE}` with it. A new nullable column is added to the existing database \
automatically.

Keep the app's existing behaviour except where the ticket changes it.

{
    answer_format(
        "every program you change (the complete file), and the complete profile only if you "
        "change it",
        "items.lattice.py",
    )
}

{
    _partial_note(
        "endpoints, `@procedure`, `@record`, `@step` and constants of a program", "old_step"
    )
}

{_PROFILE_DELTA_DOC}"""


# a held-out repo's import: the same section, with no template convention stated (the
# profile settles them: page_shape, error_body, roles_from, ...)
L_EDITING_IMPORTED = f"""## Editing an imported app

The programs you edit were imported from an existing FastAPI app (one Lattice program \
per router file, e.g. `notes.lattice.py` for `app/api/notes.py`); they are checked and \
emitted back into that app as its routes. The routes the import could not express stay \
the app's original code. The profile module (`{PROFILE_FILE}`) describes the app: its \
tables (`Schema.from_pairs`: columns, keys, nullable columns, owner column, `one_of` \
value lists), its response models (`RESPONSES`), its natives, its auth and its \
conventions (the page shape, the error body, how callers get their role). This app's \
conventions replace the generic ones above:

- `@endpoint(..., response="TaskPublic")` names a response model: the returned columns \
must be exactly its fields (`select` them).
- Errors answer in the profile's `error_body` style with the message given to \
`NotFound(...)`, `Forbidden(...)`, `Conflict(...)` or `BadRequest(...)`; 401 and 422 are \
automatic.
- Parameter types also include `Uuid`, `DateTime` and `OneOf(...)` (a column with a \
`one_of` list takes only a listed literal or a `OneOf` parameter).
- `p: Str(...) = UNSET` is an optional body field: not sent, the column keeps its \
value. It may be used only directly as an `update()` value or an `insert()` value, or \
in `sent(p)`.
- `native.<name>(...)` calls a function the profile declares in `natives`.
- A new column or response model is declared in the profile: return the complete \
`{PROFILE_FILE}` with it. A new nullable column is added to the existing database \
automatically.

Keep the app's existing behaviour except where the ticket changes it.

{
    answer_format(
        "every program you change or add (the complete file), and the complete profile only "
        "if you change it",
        "notes.lattice.py",
    )
}

{
    _partial_note(
        "endpoints, `@procedure`, `@record`, `@step` and constants of a program", "old_step"
    )
}

{_PROFILE_DELTA_DOC}"""


def l_system_prompt(*, hints: bool = False, repo: str = "template") -> str:
    """The Lattice API guide with its one-program output section replaced by
    :data:`L_EDITING` (a held-out repo: :data:`L_EDITING_IMPORTED`). ``hints``: keep the
    guide's pitfall Advice (off by default: it states another app's conventions, and arm
    P gets no advice either)."""
    guide = api_lattice_system_prompt(hints=hints)
    if _OUTPUT_FORMAT_SECTION not in guide:
        raise AssertionError("the Lattice guide's output section moved")
    section = L_EDITING if repo == "template" else L_EDITING_IMPORTED
    return guide.replace(_OUTPUT_FORMAT_SECTION, section).rstrip("\n") + "\n"


def router_file(rel: str) -> str:
    """``api/routes/items.py`` -> ``items.lattice.py``."""
    return rel.rsplit("/", 1)[-1].removesuffix(".py") + ".lattice.py"


def l_program_files(
    ticket: Ticket, programs: Mapping[str, str] | None = None
) -> tuple[str, ...]:
    """The Lattice programs of the routers the ticket touches: the template's
    ``api/routes/*`` files, and for a held-out repo each ticket file whose program the
    start state has (``programs``; a router the import lifted nothing from has none)."""
    if ticket.repo == "template":
        return tuple(router_file(f) for f in ticket.files if f.startswith("api/routes/"))
    return tuple(
        name for f in ticket.files if programs is not None and (name := router_file(f)) in programs
    )


def l_user_message(
    ticket: Ticket,
    programs: Mapping[str, str],
    profile_source: str,
    *,
    others: Sequence[str] = (),
) -> str:
    """The ticket, the touched routers' programs and the profile source; ``others``
    names the unchanged programs that are not shown."""
    parts = [f"## Ticket: {ticket.title}\n\n{ticket.text.strip()}", "## Current files"]
    for name in l_program_files(ticket, programs):
        parts.append(f"### {name}\n```python\n{programs[name].rstrip(chr(10))}\n```")
    parts.append(f"### {PROFILE_FILE}\n```python\n{profile_source.rstrip(chr(10))}\n```")
    if others:
        parts.append(
            "The app's other routers (" + ", ".join(f"`{o}`" for o in others) + ") are "
            "Lattice programs too; they are unchanged and not shown."
        )
    if ticket.repo != "template":
        parts.append(
            "Routes of the app that are in no program were not imported: they stay the "
            "app's original Python code, unchanged, and cannot be edited here. A new route "
            "goes in a program (a new file `<router>.lattice.py` if its router has none)."
        )
    return "\n\n".join(parts)


__all__ = [
    "P_SYSTEM",
    "PROFILE_DELTA_FILE",
    "PROFILE_FILE",
    "ColumnDelta",
    "MergedFile",
    "PartialAnswerError",
    "ProfileDelta",
    "ResponseDelta",
    "answer_format",
    "diff_top_level",
    "fixed_content",
    "merge_top_level",
    "named_items",
    "p_system",
    "parse_profile_delta",
    "l_program_files",
    "l_system_prompt",
    "l_user_message",
    "p_context_files",
    "p_user_message",
    "q_policy_statement",
    "q_system_prompt",
    "parse_files",
    "render_files",
    "router_file",
]
