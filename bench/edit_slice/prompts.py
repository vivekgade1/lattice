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
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from bench.edit_slice.apps import repo
from bench.edit_slice.tickets import Ticket
from lattice.llm.api_prompt import api_lattice_system_prompt
from lattice.llm.prompt import _OUTPUT_FORMAT_SECTION

APP_PREFIX = "app/"
PROFILE_FILE = "profile.py"

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


# ---- arm P ---------------------------------------------------------------------------------

def _p_system(about: str, package: str, imports: str, example: str) -> str:
    return f"""You are an expert backend engineer working on an existing FastAPI app: \
{about}. The app's package is imported as `{package}` (files are shown relative to it as \
`app/...`, and {imports}). Implement the ticket you are given by editing the app's files.

The edited app is imported fresh and started unattended; its tables are created from \
the models when a database is new. Keep the app's existing behaviour except where the \
ticket changes it.

{answer_format("every file you change or add", example)}
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
}"""


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
}"""


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
    "PROFILE_FILE",
    "answer_format",
    "p_system",
    "l_program_files",
    "l_system_prompt",
    "l_user_message",
    "p_context_files",
    "p_user_message",
    "parse_files",
    "render_files",
    "router_file",
]
