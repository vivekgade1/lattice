"""Arm S+'s drafting aids (docs/archive/superpowers/plans/2026-10-05-pivot-spec-authoring.md,
item C): a generic edge-case checklist for spec drafts and the self-review request.

FROZEN before any S+ run: any change to :data:`CHECKLIST`, :data:`CHECKLIST_SECTION` or
:data:`REVIEW_REQUEST` is a change of the arm.

The checklist is derived from general API-defect categories, not from any app's tickets,
misses or traps:

* boundary-value analysis -- inclusive and exclusive limits, tested at and past the edge;
* response contracts -- every field of the response model stated once;
* OWASP API Top 10 -- broken object-level authorisation (another user's row), broken
  function-level authorisation (the wrong role), broken authentication (no
  credentials), mass assignment (a field the caller may not set);
* state-transition testing -- legal and illegal edges, repeats, final states;
* transactional atomicity -- a refused request writes nothing, all-or-nothing batches;
* equivalence partitioning of collections -- duplicates, empty lists, no matching rows;
* aggregate semantics -- count / sum / avg / min / max over zero rows, one row, and rows
  that must be excluded;
* the HTTP error taxonomy -- exact statuses (401 / 403 / 404 / 409 / 422), error text only
  where the requirement quotes it.

Its examples use placeholder tables (``projects`` / ``Project``, ``tasks`` / ``Task``) and
users (``ann`` owns, ``ben`` is another member, ``sam`` is staff) that belong to no app
in this repository (``tests/bench/test_splus_dry.py`` enforces it for the multi-vendor
app).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["CHECKLIST", "CHECKLIST_SECTION", "REVIEW_REQUEST", "ChecklistItem"]


@dataclass(frozen=True)
class ChecklistItem:
    """One check: a one-line ``rule`` and an ``example`` in the spec DSL (one Python
    expression: a record or a tuple of records)."""

    key: str
    title: str
    rule: str
    example: str


CHECKLIST: tuple[ChecklistItem, ...] = (
    ChecklistItem(
        "bounds",
        "Bounds",
        "For every limit the ticket states (a number, a length, a count, a date), one "
        "scenario at the edge value and one just past it; 'at most', 'at least', 'up to' "
        "and 'between' are inclusive, 'more than', 'less than', 'over', 'under', 'above' "
        "and 'below' are exclusive.",
        """(
    # "budget: more than 0, at most 1000"
    Scenario("tNN_budget_at_max", steps=(
        Step("POST", "/projects", "ann", json={"title": "A", "budget": 1000},
             expect_status=201),)),
    Scenario("tNN_budget_past_max", steps=(
        Step("POST", "/projects", "ann", json={"title": "A", "budget": 1001},
             expect_status=422, expect_db={"projects": {"where": {"title": "A"}, "count": 0}}),)),
    Scenario("tNN_budget_zero_is_refused", steps=(
        Step("POST", "/projects", "ann", json={"title": "A", "budget": 0},
             expect_status=422),)),
)""",
    ),
    ChecklistItem(
        "response_shape",
        "Response shape",
        "State every field of the response model, with its value, in at least one "
        "scenario's expect_body (nested objects, computed fields and nulls included); for "
        "a list, every element, in the expected sequence.",
        """Scenario("tNN_project_body", steps=(
    Step("GET", "/projects/3", "ann", expect_body={
        "id": 3, "title": "Roof", "budget": 500.0, "owner_id": 2, "status": "open",
        "closed_at": None, "tasks": [{"id": 7, "hours": 2}, {"id": 8, "hours": 5}],
        "task_count": 2}),))""",
    ),
    ChecklistItem(
        "access_matrix",
        "Role x ownership x missing",
        "For each route, one step per cell, each with its exact status: the owner, "
        "another user of the same role (someone else's row), each other role, a "
        "privileged role if the app has one, no credentials (401), and an id that does "
        "not exist (404); a field the caller may not set is ignored or refused, as the "
        "ticket or the app's convention says.",
        """Scenario("tNN_close_access", steps=(
    Step("POST", "/projects/3/close", None, expect_status=401),
    Step("POST", "/projects/3/close", "ben", expect_status=403),  # another member's row
    Step("POST", "/projects/999/close", "ann", expect_status=404),
    Step("POST", "/projects/3/close", "ann", expect_status=200,
         expect_body={"id": 3, "status": "closed"}),
))""",
    ),
    ChecklistItem(
        "state_edges",
        "State edges",
        "For a status the ticket moves, one scenario per legal move and at least one "
        "illegal move per state it names (from a final state, a skipped step, the same "
        "move twice), each with its status and the stored state afterwards; a new set of "
        "moves is a Machine.",
        """(
    Machine("Task", "status", edges={"open": ("active", "closed"), "active": ("closed",)},
            initial=("open",)),
    Scenario("tNN_reopen_closed_is_refused", steps=(
        Step("POST", "/tasks/5/close", "ann", expect_status=200),
        Step("POST", "/tasks/5/open", "ann", expect_status=409,
             expect_db={"tasks": {"where": {"id": 5}, "equals": {"status": "closed"}}}),
    )),
)""",
    ),
    ChecklistItem(
        "atomicity",
        "Atomicity",
        "After every refused request, expect_db shows that nothing was written: the row "
        "counts and the values the request would have changed; a request that writes "
        "several rows and fails on one writes none of them.",
        """Scenario("tNN_batch_with_one_bad_entry_writes_nothing", steps=(
    Step("POST", "/tasks/batch-hours", "ann",
         json={"entries": [{"task_id": 5, "hours": 3}, {"task_id": 999, "hours": 1}]},
         expect_status=404,
         expect_db={"tasks": {"where": {"id": 5}, "equals": {"hours": 2}},
                    "task_logs": {"count": 0}}),
))""",
    ),
    ChecklistItem(
        "duplicates",
        "Duplicates",
        "Where a request carries a list or a unique value, one scenario with the same "
        "element (or key) twice in one request, and one creating a value that already "
        "exists, with the answer the ticket gives (counted once, merged, refused); if the "
        "ticket does not say, an Unchecked entry, not a guess.",
        """(
    Scenario("tNN_same_task_twice_counts_once", steps=(
        Step("POST", "/projects/3/tasks/assign", "ann", json={"task_ids": [5, 5]},
             expect_body={"assigned": 1},
             expect_db={"task_assignments": {"where": {"task_id": 5}, "count": 1}}),)),
    Unchecked("tNN: the ticket does not say whether a repeated tag is refused",
              concerns=("POST /projects",)),
)""",
    ),
    ChecklistItem(
        "empty_collections",
        "Empty collections",
        "An empty list in a request (accepted or refused, as the ticket says) and a "
        "listing or filter that matches no row (an empty list, not an error, unless the "
        "ticket says otherwise).",
        """(
    Scenario("tNN_empty_batch", steps=(
        Step("POST", "/tasks/batch-hours", "ann", json={"entries": []}, expect_status=422,
             expect_db={"task_logs": {"count": 0}}),)),
    Scenario("tNN_filter_matches_nothing", steps=(
        Step("GET", "/projects", "ann", params={"status": "closed"}, expect_body=[]),)),
)""",
    ),
    ChecklistItem(
        "empty_aggregates",
        "Aggregates over zero rows",
        "For every count, sum, average, minimum or maximum the ticket defines, one "
        "scenario where it ranges over zero rows (count 0, sum 0; average, min and max "
        "as the ticket says), one over a single row, and one where rows it must leave "
        "out (another owner's, another state, inactive) exist.",
        """(
    Scenario("tNN_summary_of_a_project_without_tasks",
        given={"projects": [{"id": 9, "owner_id": 2, "title": "Empty", "budget": 10.0,
                             "status": "open", "closed_at": None}]},
        steps=(Step("GET", "/projects/9/summary", "ann",
                    expect_body={"task_count": 0, "hours": 0}),)),
    Invariant("project_hours", "A project's hours are the sum of its tasks' hours.",
              'Project.hours == sum(Task.hours, by="project_id")'),
)""",
    ),
    ChecklistItem(
        "statuses",
        "Exact statuses",
        "Every step states its exact expect_status (201 created, 204 no content, 401 no "
        "credentials, 403 not allowed, 404 missing, 409 conflict, 422 invalid input); an "
        "error's text goes in expect_body only where the ticket quotes it.",
        """Scenario("tNN_close_twice", steps=(
    Step("POST", "/projects/3/close", "ann", expect_status=200),
    # the ticket quotes the message: "Project is already closed"
    Step("POST", "/projects/3/close", "ann", expect_status=409,
         expect_body={"detail": "Project is already closed"}),
))""",
    ),
)


def _render() -> str:
    lines = [
        "## Edge-case checklist",
        "",
        "Before you answer, go through this list: for each item the ticket touches, add "
        "the cases it calls for. Add only what the ticket states or what follows from "
        "the app's existing spec; where the ticket leaves an answer open, write an "
        "`Unchecked` entry instead of guessing. The examples use placeholder tables "
        "(projects, tasks) and users (ann owns, ben is another member, sam is staff): "
        "use the app's own tables, users and seed rows, and the ticket's id as the "
        "name prefix instead of `tNN_`.",
    ]
    for k, item in enumerate(CHECKLIST, 1):
        lines += ["", f"{k}. **{item.title}.** {item.rule}", "", "```python", item.example,
                  "```"]  # fmt: skip
    return "\n".join(lines) + "\n"


CHECKLIST_SECTION = _render()

REVIEW_REQUEST = """\
Review your draft against the ticket and the edge-case checklist, item by item. For \
each item the ticket touches, check that the draft has its cases, and add the ones it \
misses: bounds at and past each edge, every response field, each role x ownership x \
missing cell, legal and illegal state moves, nothing written after a refused request, \
duplicates, empty collections, aggregates over zero rows, exact statuses. Fix any \
expected answer you worked out wrong. Do not invent requirements: add nothing the \
ticket does not state or the app's spec does not already imply, check an error's \
text only where the ticket quotes it, and put an open question in `unchecked`. Keep \
every correct rule of your draft.

Answer with the complete revised module in one ```python block that defines \
DELTA = SpecDelta(...)."""
