"""The edit slice's change tickets (plan 2026-09-27 thin slice, node E1).

Twelve realistic changes to the vendored FastAPI template (``bench/template_app``): the
baseline six (t1-t6) and a held-out six (t7-t12, see below), written
the way a PM or engineer files them. Each names what to build and the exact response
contract (paths, parameter names, status codes, error details) -- the hidden acceptance
suite compares answers exactly, so the contract has to be stated -- but *not* the
requirements a careful engineer infers from how the app already behaves. Those are the
traps (``bench/edit_slice/oracle.py`` tests each one):

* ``t1_search`` -- normal users still see only their own items when searching;
* ``t2_since`` -- a malformed ``since`` is a 422, not a crash; an offset is honoured
  (stored times are UTC); items without a ``created_at`` do not match a ``since``;
* ``t3_soft_delete`` -- the list ``count`` excludes deleted items; reading, updating or
  deleting a deleted item is a 404; admins do not see deleted items either;
  ``deleted_at`` cannot be set through the create/update bodies;
* ``t4_restore`` -- a normal user gets the app's usual superuser 403, even on their own
  item;
* ``t5_title_window`` -- SQLite gives back naive (UTC) datetimes, so comparing them with
  an aware "now" crashes; ownership is still checked first; the admin bypass;
* ``t6_stats`` -- ``/items/stats`` must not be swallowed by ``/items/{id}``; admins get
  their *own* stats (the list's admin-sees-all rule does not apply); no items is
  ``count: 0``.

**Held-out set (t7-t12).** Written after the language was extended to close the gaps
t1-t6 exposed, to guard against tuning Lattice to those six. The ticket texts were
written first, in this order -- t7, t8, t9, t10, t11, t12 -- picked as a real backlog
would hold them (users/admin features, a plan limit, an aggregate admin page, a
validation change, an ownership write), before any was checked for Lattice
expressibility. Their traps:

* ``t7_deactivate`` -- a normal user gets the app's usual superuser 403, even on their
  own account (not the self-deactivation message); the answer is ``UserPublic`` (no
  ``hashed_password``); an admin may deactivate *another* admin; activating yourself
  is allowed;
* ``t8_user_filter`` -- ``is_active=false`` really filters (a truthiness test skips
  it); ``count`` is the filtered count; ``q`` ignores case; the list stays admin-only;
  both filters combine;
* ``t9_item_quota`` -- the limit counts the caller's *own* items (an admin's too, not
  every item they can see); reaching 50 is allowed, the 51st is refused (off by one);
  a refused create writes nothing;
* ``t10_owner_counts`` -- users with no items are listed with 0 (an inner join drops
  them); ``/users/item-counts`` must not be swallowed by ``/users/{user_id}``; inactive
  users are users too; ``count`` is the number of users;
* ``t11_title_trim`` -- a ``PUT`` without a ``title`` (description only) still works;
  the stored title is the stripped one (the answer too); tabs and newlines are
  whitespace; inner spaces stay;
* ``t12_transfer`` -- only the item's owner or an admin may transfer (anyone else gets
  the app's 403 "Not enough permissions"), and that is checked *before* the email is
  looked up (a stranger cannot probe for accounts); an admin may transfer anyone's
  item; no error writes anything; an unknown item is the app's 404.

**Held-out repos (t19-t24, round 3).** Six tickets on the importer's held-out repos
(``bench/import_repos/``; ``Ticket.repo``): three on TaskFlow (tasks; another user's task
is a 404 "Task not found", never a 403; ``PATCH`` stamps ``updated_at``; datetimes are
naive UTC) and three on JustNotes (notes, tags; another user's note is a 403 -- "Not
authorized to access this note" to read, "... to modify this note" to write -- after a
404 "Note not found"; a public note is readable by everyone; an owner's note titles are
unique, 400 "Note with same title already exists"; timestamps are Unix ints). They test
whether the language and profile mechanisms are generic rather than shaped by the
template, so their traps follow each app's own conventions, not the template's. The
texts were written first, in this order -- t19, t20, t21, t22, t23, t24 -- as each app's
backlog would hold them (a dashboard aggregate, a batch write, a new column; a write
across owners, a filtered list, an aggregate), before any oracle case. Their traps:

* ``t19_task_summary`` -- ``/tasks/summary`` must be declared before ``/tasks/{task_id}``
  (an int path parameter: "summary" is a 422); only the caller's tasks (every TaskFlow
  task route is owner-scoped); ``by_status`` has all three statuses, 0 included (a
  ``GROUP BY`` drops them); a task without a ``due_date`` is never overdue (comparing
  ``None`` crashes); stored due dates are naive UTC (an aware "now" crashes);
* ``t20_bulk_status`` -- another user's task is "not found" too (TaskFlow's 404, not a
  403), and it is not touched; all or nothing: a 404 anywhere in the list writes nothing,
  even the caller's tasks listed before it; "as if PATCHed" means ``updated_at`` is
  stamped on every listed task, including one already in that status; a repeated id is
  counted once;
* ``t21_completed_at`` -- a task *created* with status ``done`` (``POST`` takes a status)
  is stamped too; a ``PATCH`` that leaves a done task done (``done`` again, or a title
  edit) keeps the original ``completed_at``; ``completed_at`` sent in a create or update
  body is ignored (adding it to ``TaskBase``/``TaskUpdate`` makes it writable); list
  items carry it as well; ``"status": null`` stays the app's 422 and writes nothing;
* ``t22_copy_note`` -- "allowed to open" is JustNotes' read rule: the owner, or anyone
  for a public note (another user's public note may be copied, the copy is the
  caller's); another user's private note is the app's 403 "Not authorized to access
  this note" and writes nothing; an unknown id is the app's 404 "Note not found"; the
  app refuses a second note with the same title for one owner (400 "Note with same
  title already exists"), so copying again is refused -- and only the caller's own
  titles count; the copy is private even when the original is public;
* ``t23_note_filter`` -- only the caller's own notes, even when another user's note
  carries the caller's tag (the link routes do not check the tag's owner, so that
  happens); ``q`` is a plain substring: ``%`` and ``_`` match themselves (an unescaped
  ``LIKE`` matches everything); ``q`` searches the text as well as the title; the
  entries keep today's shape (``owner_id`` and the ``owner`` object) and order;
* ``t24_note_stats`` -- ``/notes/stats`` must be declared before ``/notes/{id}`` ("stats"
  is a 422 otherwise); the caller's own notes only, not the other users' public notes
  they can open; ``tagged`` counts notes, not links (a note with two tags counts once,
  and a note tagged with someone else's tag counts); no notes is 0s and null.

``files`` are the app files the reference changes (paths relative to the app package:
``bench/template_app/app`` for the template, ``bench/import_repos/<repo>/app`` for the
held-out repos); ``routes`` the routes the ticket touches. ``base`` names the ticket
whose reference is the starting point (the restore ticket needs soft delete). ``repo``
names the app (:mod:`bench.edit_slice.apps`).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Ticket:
    id: str
    title: str
    text: str
    routes: tuple[str, ...]
    files: tuple[str, ...]
    base: str | None = None
    repo: str = "template"


ITEMS = "api/routes/items.py"
MODELS = "models.py"
USERS = "api/routes/users.py"

TICKETS: tuple[Ticket, ...] = (
    Ticket(
        id="t1_search",
        title="Search items by title",
        text="""\
Users with a lot of items can't find anything in the item list. Please add an optional
`q` query parameter to `GET /api/v1/items/` that narrows the list to items whose title
contains `q`, ignoring case (so `q=groc` finds "Weekly Groceries"). `count` should be the
number of matching items, and `skip`/`limit` keep working as they do now. Without `q`
(or with an empty `q`) the endpoint behaves exactly as it does today.""",
        routes=("GET /api/v1/items/",),
        files=(ITEMS,),
    ),
    Ticket(
        id="t2_since",
        title="Filter the item list by creation time and choose the sort order",
        text="""\
The mobile client wants to sync incrementally. Add two optional query parameters to
`GET /api/v1/items/`:

- `since`: an ISO 8601 timestamp, e.g. `2026-02-01T12:00:00Z`. Only items created at or
  after that moment are returned.
- `sort`: `newest` (the default, today's order: most recently created first) or `oldest`
  (the reverse).

`count` is the number of items matching the filter, and `skip`/`limit` page through the
filtered, sorted list as they do today. With neither parameter the response is unchanged.""",
        routes=("GET /api/v1/items/",),
        files=(ITEMS,),
    ),
    Ticket(
        id="t3_soft_delete",
        title="Soft-delete items",
        text="""\
Support keeps getting asked to recover items people deleted by mistake, and today a
delete removes the row for good. Switch items to soft delete:

- add a nullable `deleted_at` timestamp column to the `item` table (a UTC timestamp,
  NULL for live items);
- `DELETE /api/v1/items/{id}` sets `deleted_at` to the current time instead of removing
  the row, and keeps its current response;
- from the API's point of view a deleted item is gone: it must not show up anywhere in
  the item endpoints.

`deleted_at` is internal bookkeeping for now and isn't part of the API's request or
response models. No migration tooling is needed; the column is created with the table.""",
        routes=(
            "GET /api/v1/items/",
            "GET /api/v1/items/{id}",
            "POST /api/v1/items/",
            "PUT /api/v1/items/{id}",
            "DELETE /api/v1/items/{id}",
        ),
        files=(MODELS, ITEMS),
    ),
    Ticket(
        id="t4_restore",
        title="Admin endpoint to restore a soft-deleted item",
        text="""\
Follow-up to soft delete (items now have a `deleted_at` column that `DELETE` sets). Support
needs a way to undo a delete. Add `POST /api/v1/items/{id}/restore`, for admins
(superusers) only, which clears `deleted_at` and returns the restored item in the same
shape as `GET /api/v1/items/{id}`.

- Restoring an item that isn't deleted: 400 with detail "Item is not deleted".
- Unknown id: 404 with detail "Item not found".""",
        routes=("POST /api/v1/items/{id}/restore",),
        files=(ITEMS,),
        base="t3_soft_delete",
    ),
    Ticket(
        id="t5_title_window",
        title="Lock item titles 24 hours after creation",
        text="""\
Item titles are used as stable references in the exported reports, so they shouldn't
change once an item is more than a day old. In `PUT /api/v1/items/{id}`, reject a request
that sets `title` on an item created more than 24 hours ago with 403 and the detail
"Title can only be changed within 24 hours of creation". The description stays editable
at any time. Superusers can still change titles whenever they need to (support uses this
to fix typos).""",
        routes=("PUT /api/v1/items/{id}",),
        files=(ITEMS,),
    ),
    Ticket(
        id="t6_stats",
        title="Item stats for the signed-in user",
        text="""\
The new dashboard header shows "You have N items, last one created <time>". Add
`GET /api/v1/items/stats`, returning for the signed-in user's own items:

    {"count": <number of items>, "latest_created_at": <created_at of the newest item>}

`latest_created_at` is null when there's nothing to report. Same authentication as the
other item endpoints.""",
        routes=("GET /api/v1/items/stats",),
        files=(MODELS, ITEMS),
    ),
    # ---- the held-out set (see the module docstring), in the order written ----
    Ticket(
        id="t7_deactivate",
        title="Admin endpoints to deactivate and reactivate a user",
        text="""\
Support needs a quick way to lock an account without deleting it, and to undo that.
Add two admin (superuser) endpoints:

- `POST /api/v1/users/{user_id}/deactivate` sets the user's `is_active` to false;
- `POST /api/v1/users/{user_id}/activate` sets it back to true.

Both return the user in the same shape as `GET /api/v1/users/{user_id}`. Calling one on
a user who is already in that state is fine: nothing changes and the user is returned.
Unknown id: 404 with detail "User not found". An admin must not be able to lock
themselves out: deactivating your own account is refused with 403 and the detail
"Super users are not allowed to deactivate themselves".""",
        routes=(
            "POST /api/v1/users/{user_id}/deactivate",
            "POST /api/v1/users/{user_id}/activate",
        ),
        files=(USERS,),
    ),
    Ticket(
        id="t8_user_filter",
        title="Filter and search the admin user list",
        text="""\
The admin user list is getting long. Add two optional query parameters to
`GET /api/v1/users/`:

- `is_active`: `true` or `false`, only users with that status;
- `q`: only users whose email contains `q`, ignoring case (`q=ALICE` finds
  "alice@example.com").

They can be combined. `count` is the number of matching users, and `skip`/`limit` page
through the matching users in the current order. Without the parameters the endpoint
behaves as it does today.""",
        routes=("GET /api/v1/users/",),
        files=(USERS,),
    ),
    Ticket(
        id="t9_item_quota",
        title="Limit users to 50 items",
        text="""\
We're introducing plan limits. Every account, admins included, can own at most 50 items.
`POST /api/v1/items/` by a user who already owns 50 items is refused with 409 and the
detail "Item limit reached: you can own at most 50 items". Nothing else about creating
items changes.""",
        routes=("POST /api/v1/items/",),
        files=(ITEMS,),
    ),
    Ticket(
        id="t10_owner_counts",
        title="Admin page: item counts per user",
        text="""\
Admins want to see who is actually using the app. Add `GET /api/v1/users/item-counts`,
for admins (superusers) only, listing every user with the number of items they own:

    {"data": [{"id": <user id>, "email": <email>, "item_count": <n>}, ...],
     "count": <number of users>}

Sorted by `item_count`, highest first; users with the same count by email, A to Z.
`skip` and `limit` work as they do on `GET /api/v1/users/`.""",
        routes=("GET /api/v1/users/item-counts",),
        files=(MODELS, USERS),
    ),
    Ticket(
        id="t11_title_trim",
        title="Strip whitespace from item titles",
        text="""\
The exports keep turning up items titled " Groceries " or just "   " -- leading and
trailing whitespace pasted in from other tools. On `POST /api/v1/items/` and
`PUT /api/v1/items/{id}`, strip leading and trailing whitespace from `title` before
saving it. A title that is empty after stripping is refused with 400 and the detail
"Title must not be blank". Whitespace inside a title stays as it is.""",
        routes=("POST /api/v1/items/", "PUT /api/v1/items/{id}"),
        files=(ITEMS,),
    ),
    Ticket(
        id="t12_transfer",
        title="Transfer an item to another user",
        text="""\
People moving between teams need to hand their items over to a colleague. Add
`POST /api/v1/items/{id}/transfer` with the JSON body `{"new_owner_email": "<email>"}`.
It makes the user with that email the item's owner and returns the item in the same
shape as `GET /api/v1/items/{id}`.

- No user has that email: 404 with detail "User not found".
- The new owner's account is deactivated: 400 with detail "Cannot transfer an item to
  an inactive user".
- The item already belongs to that user: 400 with detail "Item already belongs to this
  user".""",
        routes=("POST /api/v1/items/{id}/transfer",),
        files=(MODELS, ITEMS),
    ),
)

TASKFLOW_ROUTES = "routes.py"
TASKFLOW_MODELS = "models.py"
TASKFLOW_CRUD = "crud.py"
NOTES = "api/notes.py"
NOTE_CRUD = "database/note_crud.py"

# ---- round 3: the held-out repos (see the module docstring), in the order written ----
REPO_TICKETS: tuple[Ticket, ...] = (
    Ticket(
        id="t19_task_summary",
        title="Task summary for the dashboard",
        text="""\
The dashboard header needs a quick overview of the signed-in user's tasks. Add
`GET /api/v1/tasks/summary`, returning

    {"total": <number of tasks>,
     "by_status": {"todo": <n>, "in_progress": <n>, "done": <n>},
     "overdue": <number of overdue tasks>}

A task is overdue when its `due_date` has passed and its status isn't `done`. Same
authentication as the other task endpoints.""",
        routes=("GET /api/v1/tasks/summary",),
        files=(TASKFLOW_ROUTES,),
        repo="taskflow",
    ),
    Ticket(
        id="t20_bulk_status",
        title="Change the status of several tasks at once",
        text="""\
Board users want to move a batch of cards in one go. Add
`POST /api/v1/tasks/bulk-status` with the JSON body

    {"task_ids": [<task id>, ...], "status": "todo" | "in_progress" | "done"}

Each listed task gets the new status, exactly as if it had been changed with
`PATCH /api/v1/tasks/{task_id}`. The answer is `{"updated": <number of different tasks
listed>}`. If a listed task can't be found, answer 404 with detail "Task not found".""",
        routes=("POST /api/v1/tasks/bulk-status",),
        files=(TASKFLOW_MODELS, TASKFLOW_ROUTES),
        repo="taskflow",
    ),
    Ticket(
        id="t21_completed_at",
        title="Record when a task was completed",
        text="""\
Reporting wants to know how long tasks take to finish. Add a `completed_at` timestamp to
tasks:

- a nullable `completed_at` column on the `tasks` table (UTC, like `created_at`);
- it is set to the current time when a task's status becomes `done`, and cleared (null)
  when a done task's status changes to anything else;
- tasks in API responses include `completed_at` (null while the task isn't done).

`completed_at` is maintained by the server; clients can't set it. No migration tooling
is needed; the column is created with the table.""",
        routes=(
            "POST /api/v1/tasks",
            "GET /api/v1/tasks",
            "GET /api/v1/tasks/{task_id}",
            "PATCH /api/v1/tasks/{task_id}",
        ),
        files=(TASKFLOW_MODELS, TASKFLOW_CRUD),
        repo="taskflow",
    ),
    Ticket(
        id="t22_copy_note",
        title="Copy a note",
        text="""\
People keep re-typing notes to use them as templates. Add `POST /notes/{id}/copy`: it
creates a new note belonging to the signed-in user, with the original's text, the title
"<original title> (copy)" and `isPublic` false, and answers 201 with the new note in the
same shape as `POST /notes/create`. Users can copy any note they are allowed to open.
Tags and keywords are not copied.""",
        routes=("POST /notes/{id}/copy",),
        files=(NOTES,),
        repo="justnotes",
    ),
    Ticket(
        id="t23_note_filter",
        title="Filter my notes by tag and text",
        text="""\
The notes list is getting long. Add two optional query parameters to `GET /notes`:

- `tag_id`: only notes that have this tag, e.g. `GET /notes?tag_id=1`;
- `q`: only notes whose title or text contains `q`, ignoring case (`q=MILK` finds
  "Eggs, milk, bread.").

They can be combined. The tag must be one of the user's own tags: an unknown `tag_id` is
404 with detail "Tag not found", someone else's tag is 403 with detail "Not authorized to
access this tag". Without the parameters the endpoint behaves as it does today.""",
        routes=("GET /notes",),
        files=(NOTES, NOTE_CRUD),
        repo="justnotes",
    ),
    Ticket(
        id="t24_note_stats",
        title="Note statistics for the signed-in user",
        text="""\
The profile page shows a few numbers about the signed-in user's notes. Add
`GET /notes/stats`, returning for the user's own notes

    {"total": <number of notes>, "public": <number of public notes>,
     "tagged": <number of notes with at least one tag>,
     "last_updated_at": <the latest updatedAt of a note>}

`last_updated_at` is null when there is nothing to report. Same authentication as
`GET /notes`.""",
        routes=("GET /notes/stats",),
        files=(NOTES,),
        repo="justnotes",
    ),
)

BASELINE: tuple[str, ...] = tuple(t.id for t in TICKETS[:6])
HELD_OUT: tuple[str, ...] = tuple(t.id for t in TICKETS[6:])
HELD_OUT_REPOS: tuple[str, ...] = tuple(t.id for t in REPO_TICKETS)
# every ticket: the template's (TICKETS, what `--tickets all` runs) and the held-out repos'
ALL_TICKETS: tuple[Ticket, ...] = (*TICKETS, *REPO_TICKETS)


def ticket(ticket_id: str) -> Ticket:
    for t in ALL_TICKETS:
        if t.id == ticket_id:
            return t
    raise KeyError(f"unknown ticket {ticket_id!r}")
