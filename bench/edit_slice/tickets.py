"""The edit slice's change tickets (plan 2026-09-27 thin slice, node E1).

Eighteen realistic changes to the vendored FastAPI template (``bench/template_app``): the
baseline six (t1-t6), a held-out six (t7-t12) and a second held-out six for round 3
(t13-t18, see below), written
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

**Held-out template set, round 3 (t13-t18).** Six more tickets on the template, written
after t7-t12 to guard against tuning Lattice to the first held-out set the same way that
set guarded against tuning it to the baseline six. Unlike t7-t12, **no Lattice reference
is written for these**: they are held out from the language and lifter work entirely
(see :mod:`bench.edit_slice.lattice_reference` and :func:`bench.edit_slice.run.dry_run`,
which records arm L's dry run on them as ``no_reference``, not a language gap). The
texts were written first, in this order -- t13, t14, t15, t16, t17, t18 -- the way a
backlog would hold them (a support fix, a security fix, a feature request, an admin
need, a UX preference, a data-quality fix), before any oracle case. Their traps:

* ``t13_password_reset`` -- an admin may reset *their own* password with this endpoint
  (no self-lockout restriction, unlike deactivate); an admin may reset another admin's
  password, or an inactive user's; no old password is required or checked;
* ``t14_email_change`` -- the current password is checked *before* the new email is
  looked up (a wrong password never reveals whether an email is taken); the caller's
  own current email is never a conflict (excluded by id, as the app's own ``/users/me``
  update already does);
* ``t15_duplicate_item`` -- the copy belongs to the *original item's owner*, not to the
  caller (an admin duplicating another user's item does not take it over); the copy
  gets a fresh id and creation time, never the original's;
* ``t16_admin_audit`` -- items owned by deactivated users are still listed; ``/audit``
  must not be swallowed by ``/{id}``; ``owner_email`` matches an account's email
  exactly (case-sensitively, as everywhere else an email identifies one account), not
  as a case-insensitive search; ``count`` is the filtered count; an unmatched
  ``owner_email`` is an empty list, not a 404;
* ``t17_page_size_preference`` -- before ever being set, the preference reads back as
  null, not a 404; exactly 1 and 100 are allowed, 0 and 101 are refused; the preference
  is the caller's own, set and read through no other route;
* ``t18_duplicate_title_guard`` -- the duplicate check is scoped to the caller's own
  items (another user's, or even an admin's, identical title is fine); on update, the
  ownership check runs *before* the duplicate-title check (a stranger editing someone
  else's item to a colliding title still gets the app's 403, never the 400, and never
  learns of the collision); the match is exact (case-sensitive, as the app's own email
  uniqueness is), so a different-case title is not a duplicate; a refused create or
  update writes nothing.

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

**Fresh held-out set (t25-t30, N4, benchmark hygiene: 2026-09-27
lattice-surface-design.md §5).** Six more tickets, written before any normalisation
work (the round-4 rerun this node feeds) was visible to their author, to hold that rerun
to the same held-out standard as t7-t24: three more on the template (t25-t27) and three
on the held-out repos (t28-t30, spread across TaskFlow and JustNotes). **No Lattice
reference is written for any of them** -- like t13-t18, they are held out from the
language and lifter work entirely; :func:`~bench.edit_slice.lattice_reference.has_lattice_reference`
answers False for each (no directory, no ``GAPS`` entry) and the dry run reports them
``no_reference``. The texts were written first, in this order -- t25, t26, t27, t28,
t29, t30 -- as a backlog would hold them (a cleanup write, a re-org write, a coverage
read; an ownership write, a usage read, a cleanup write), before any oracle case. Their
traps:

* ``t25_bulk_delete_items`` -- all or nothing: an unknown or not-owned id anywhere in
  the list deletes nothing, even the caller's own items listed before it; a repeated id
  is counted once; an admin may bulk-delete anyone's items (the template's usual
  ownership bypass);
* ``t26_move_items`` -- both ids are checked before anything moves (an unknown
  ``to_user_id`` moves nothing, even when ``from_id`` owns items); moving into an
  inactive user is refused, but an inactive ``from_id`` (or an inactive caller, since
  this is admin-only anyway) is fine; there is no self-lockout analogous to deactivate
  -- an admin may move items into their own account; a user who owns nothing moves 0,
  not an error;
* ``t27_item_description_coverage`` -- ``/items/description-coverage`` must not be
  swallowed by ``/items/{id}``; the signed-in user's *own* items only (an admin does not
  get everyone's, as the list endpoint's admin-sees-all rule does not apply here
  either); the two counts always add up to the total, including 0 items;
* ``t28_task_reassign`` -- another user's task is TaskFlow's usual 404 "Task not
  found", not a 403 (the lookup must stay owner-scoped, or a stranger could reassign
  anyone's task); the new owner is looked up only after that scoped lookup succeeds;
  reassigning to the task's current owner is refused, not a silent no-op;
* ``t29_tag_summary`` -- a tag's ``note_count`` is the caller's *own* notes carrying
  it, not every note linked to it (JustNotes' tag-link routes never check whose tag they
  attach, so another user's note can carry the caller's tag); ``count`` is the number of
  tags, independent of their note counts; an unused tag lists 0, not absent;
* ``t30_bulk_delete_notes`` -- all or nothing: an unknown or not-owned id anywhere in
  the list deletes nothing, even the caller's own notes listed before it (JustNotes'
  usual order: existence before ownership, "Note not found" before "Not authorized to
  delete this note"); a repeated id is counted once.

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
    # ---- round 3's held-out template set (see the module docstring), in the order written ----
    Ticket(
        id="t13_password_reset",
        title="Admin endpoint to reset a user's password",
        text="""\
Support keeps getting locked-out-account tickets where the user has no way to prove who
they are well enough to reset their own password. Add
`POST /api/v1/users/{user_id}/reset-password`, for admins (superusers) only, with the
JSON body `{"new_password": "<new password>"}` (the same length rules as everywhere else:
8 to 128 characters). It sets the user's password to the given value and returns
`{"message": "Password reset successfully"}`.

- Unknown id: 404 with detail "User not found".""",
        routes=("POST /api/v1/users/{user_id}/reset-password",),
        files=(USERS,),
    ),
    Ticket(
        id="t14_email_change",
        title="Self-service email change with password confirmation",
        text="""\
Users can change their email today without proving they still know their password, which
security wants fixed. Add `PATCH /api/v1/users/me/email` with the JSON body
`{"current_password": "<password>", "new_email": "<email>"}`. On success it updates the
caller's email and returns the user in the same shape as `GET /api/v1/users/me`.

- Wrong `current_password`: 400 with detail "Incorrect password".
- `new_email` already belongs to another account: 409 with detail "User with this email
  already exists".""",
        routes=("PATCH /api/v1/users/me/email",),
        files=(USERS,),
    ),
    Ticket(
        id="t15_duplicate_item",
        title="Duplicate an item",
        text="""\
People keep recreating similar items from scratch instead of tweaking a copy. Add
`POST /api/v1/items/{id}/duplicate`: it creates a new item titled "<original title>
(copy)", with the same description, and answers 200 with the new item in the same shape
as `GET /api/v1/items/{id}`. Only the item's owner or an admin may duplicate it (anyone
else gets the app's usual 403).

- Unknown id: 404 with detail "Item not found".""",
        routes=("POST /api/v1/items/{id}/duplicate",),
        files=(ITEMS,),
    ),
    Ticket(
        id="t16_admin_audit",
        title="Admin audit: items across all users",
        text="""\
Support needs to audit item activity across every account. Add `GET /api/v1/items/audit`,
for admins (superusers) only:

    {"data": [{"id": <item id>, "title": <title>, "owner_email": <email>,
               "created_at": <created_at>}, ...],
     "count": <number of matching items>}

Ordered newest first. An optional `owner_email` query parameter narrows the list to the
items owned by the account with that email. `skip` and `limit` page through the
(possibly narrowed) list as on `GET /api/v1/items/`.""",
        routes=("GET /api/v1/items/audit",),
        files=(MODELS, ITEMS),
    ),
    Ticket(
        id="t17_page_size_preference",
        title="Per-user default page size preference",
        text="""\
Some users page through far more items than others. Let each user set how many results
they would like per page by default. Add:

- `PUT /api/v1/users/me/preferences` with the JSON body `{"default_page_size": <1-100 or
  null>}`, setting the caller's own preference (null clears it back to unset). Returns
  `{"default_page_size": <value>}`.
- `GET /api/v1/users/me/preferences`, returning the caller's current value the same way.

`default_page_size` outside 1-100 is refused with 422.""",
        routes=("PUT /api/v1/users/me/preferences", "GET /api/v1/users/me/preferences"),
        files=(MODELS, USERS),
    ),
    Ticket(
        id="t18_duplicate_title_guard",
        title="Refuse duplicate item titles for the same owner",
        text="""\
Exports get confusing when the same person has two items with the identical title. On
`POST /api/v1/items/` and `PUT /api/v1/items/{id}`, refuse a title that matches the title
of another item the same caller already owns, with 400 and detail "Item with this title
already exists". Nothing else about creating or updating items changes.""",
        routes=("POST /api/v1/items/", "PUT /api/v1/items/{id}"),
        files=(ITEMS,),
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
HELD_OUT: tuple[str, ...] = tuple(t.id for t in TICKETS[6:12])
# round 3's held-out template set (t13-t18): no Lattice reference is written for these
# (see the module docstring); ``HELD_OUT`` stays t7-t12, the first held-out set.
ROUND3_TEMPLATE: tuple[str, ...] = tuple(t.id for t in TICKETS[12:])
HELD_OUT_REPOS: tuple[str, ...] = tuple(t.id for t in REPO_TICKETS)
# every ticket: the template's (TICKETS, what `--tickets all` runs) and the held-out repos'
ALL_TICKETS: tuple[Ticket, ...] = (*TICKETS, *REPO_TICKETS)

# ---- N4's fresh held-out set (t25-t30, see the module docstring); no Lattice reference ----

TAGS_API = "api/tags.py"

ROUND4_TEMPLATE: tuple[Ticket, ...] = (
    Ticket(
        id="t25_bulk_delete_items",
        title="Delete several items at once",
        text="""\
Cleaning out old items one at a time is tedious. Add `POST /api/v1/items/bulk-delete`
with the JSON body `{"item_ids": [<item id>, ...]}`. Each listed item is deleted, exactly
as if `DELETE /api/v1/items/{id}` had been called on it. The answer is `{"deleted":
<number of different items listed>}`.

- An item that doesn't belong to the caller (and the caller isn't an admin): 403 with
  detail "Not enough permissions".
- An unknown id: 404 with detail "Item not found".""",
        routes=("POST /api/v1/items/bulk-delete",),
        files=(ITEMS,),
    ),
    Ticket(
        id="t26_move_items",
        title="Admin endpoint to move all of one user's items to another",
        text="""\
Two accounts sometimes need to be consolidated after a re-org. Add
`POST /api/v1/users/{from_id}/move-items` with the JSON body `{"to_user_id": "<user
id>"}`, for admins (superusers) only. It makes every item currently owned by the user
with id `from_id` belong to `to_user_id` instead, and answers `{"moved": <number of
items moved>}`.

- Either id names no user: 404 with detail "User not found".
- `from_id` and `to_user_id` are the same user: 400 with detail "Cannot move items to
  the same user".
- The `to_user_id` account is deactivated: 400 with detail "Cannot move items to an
  inactive user".""",
        routes=("POST /api/v1/users/{from_id}/move-items",),
        files=(USERS,),
    ),
    Ticket(
        id="t27_item_description_coverage",
        title="Item description coverage for the signed-in user",
        text="""\
The dashboard wants a quick view of how thoroughly people are filling in their items. Add
`GET /api/v1/items/description-coverage`, returning for the signed-in user's own items:

    {"total": <number of items>, "with_description": <count>, "without_description":
     <count>}

An item counts as having a description when its `description` is neither null nor an
empty string. There's no paging: it covers every item the caller owns. Same
authentication as the other item endpoints.""",
        routes=("GET /api/v1/items/description-coverage",),
        files=(ITEMS,),
    ),
)

ROUND4_REPO_TICKETS: tuple[Ticket, ...] = (
    Ticket(
        id="t28_task_reassign",
        title="Reassign a task to another user",
        text="""\
People switching teams need to hand their open tasks to a teammate. Add
`POST /api/v1/tasks/{task_id}/reassign` with the JSON body
`{"new_owner_email": "<email>"}`. It makes the user with that email the task's owner and
answers with the task in the same shape as `GET /api/v1/tasks/{task_id}`.

- No user has that email: 404 with detail "User not found".
- The new owner's account is deactivated: 400 with detail "Cannot reassign a task to an
  inactive user".
- The task already belongs to that user: 400 with detail "Task already belongs to this
  user".""",
        routes=("POST /api/v1/tasks/{task_id}/reassign",),
        files=(TASKFLOW_ROUTES,),
        repo="taskflow",
    ),
    Ticket(
        id="t29_tag_summary",
        title="Tag usage summary for the signed-in user",
        text="""\
People want to see how their tags are actually used. Add `GET /tags/summary`, returning

    {"data": [{"id": <tag id>, "name": <name>, "note_count": <n>}, ...],
     "count": <number of tags>}

for the signed-in user's own tags, ordered by name, A to Z. A tag nobody has used yet
lists `note_count: 0`.""",
        routes=("GET /tags/summary",),
        files=(TAGS_API,),
        repo="justnotes",
    ),
    Ticket(
        id="t30_bulk_delete_notes",
        title="Delete several notes at once",
        text="""\
People clearing out old notes want to remove several at once instead of one at a time.
Add `POST /notes/bulk-delete` with the JSON body `{"note_ids": [<note id>, ...]}`. Each
listed note is deleted, exactly as if `DELETE /notes/{id}` had been called on it. The
answer is `{"deleted": <number of different notes listed>}`.

- If a listed note can't be found: 404 with detail "Note not found".
- If a listed note isn't the caller's own: 403 with detail "Not authorized to delete this
  note".""",
        routes=("POST /notes/bulk-delete",),
        files=(NOTES,),
        repo="justnotes",
    ),
)

# t25-t30, the order they were written in (see the module docstring)
ROUND4_NEW: tuple[str, ...] = tuple(t.id for t in (*ROUND4_TEMPLATE, *ROUND4_REPO_TICKETS))
# every ticket, t1-t30: what `--tickets round4` runs (round3's t1-t24 plus the fresh set)
ROUND4_TICKETS: tuple[Ticket, ...] = (*ALL_TICKETS, *ROUND4_TEMPLATE, *ROUND4_REPO_TICKETS)

# ---- option 1's pilot set (t31-t38, docs/archive/option1-handoff.md) on the multi-vendor app ----
#
# Written in this order, as texts, before any reference, oracle case or Lattice check, and
# approved as written on 2026-09-29. Two families: aggregations with traps (t31-t34) and
# ownership inherited through parent tables (t35-t38; t35 is a control: an order line has
# two owners, which Lattice's single-owner model does not express). Their traps (never
# sent to a model; ``bench/edit_slice/repos/multivendor.py`` pins each):
#
# * t31_vendor_sales -- revenue is the vendor's own lines x price_at_purchase (not the
#   order total, which includes the other vendor's lines, nor the current price); an order
#   with two of the vendor's lines is one order; `end` covers the whole day; a vendor with
#   no sales gets 0/0/0.0, not null; a malformed date is a 422; a non-vendor the usual 404.
# * t32_monthly_report -- /orders/report is not swallowed by /orders/{order_id}; month
#   boundaries (Feb 1 00:00 and Feb 28 23:59:59 in, Jan 31 23:30 and Mar 1 00:00 out);
#   December rolls into the next year; every status listed, zeros included; an empty month
#   is zeros; 2026-13 and malformed values are 422; a vendor gets the usual 403.
# * t33_top_sellers -- never-sold products listed with 0; inactive products excluded even
#   when they sold; not swallowed by /products/{product_id}; cancelled units excluded but
#   the product still listed.
# * t34_customer_spend -- buyers without orders listed with 0/0.0; inactive buyers are
#   customers; vendors and admins are not; no fan-out from joining order lines; not
#   swallowed by /orders/{order_id}.
# * t35_vendor_order_lines (control) -- only the vendor's own lines of a multi-vendor
#   order; inactive products' lines included; cancelled orders included; the non-vendor 404.
# * t36_cancel_order -- another buyer's order is the usual 403, checked before the status;
#   every line's stock restored; shipped/delivered is the state machine's 400 and writes
#   nothing; unknown order the usual 404; admins may cancel any order.
# * t37_product_buyers -- another vendor's product is 403 "You do not own this product",
#   after "Product not found"; an admin gets the permission 403 (no admin bypass); a buyer
#   with several orders appears once, units summed; inactive buyers listed; no sales is [].
# * t38_reorder -- current prices, not price_at_purchase; another buyer's order the usual
#   403; not enough stock is the 409 and writes nothing (no order, no stock change: upstream
#   `place_order` commits line by line); a cancelled order may be reordered; the new order
#   is pending with a fresh created_at.

MV_VENDOR_ROUTER = "routers/vendor_router.py"
MV_VENDOR_SERVICE = "services/vendor_service.py"
MV_ORDER_ROUTER = "routers/order_router.py"
MV_ORDER_SERVICE = "services/order_service.py"
MV_PRODUCT_ROUTER = "routers/product_router.py"
MV_PRODUCT_SERVICE = "services/product_service.py"

OPTION1_TICKETS: tuple[Ticket, ...] = (
    Ticket(
        id="t31_vendor_sales",
        title="Sales summary for vendors",
        text="""\
Vendors want to see how their shop is doing. Add `GET /vendors/me/sales`, answering

    {"orders": <number of orders containing the vendor's products>,
     "units": <units of the vendor's products sold>,
     "revenue": <money the vendor earned from them>}

It takes two optional query parameters, `start` and `end` (dates, `YYYY-MM-DD`, both
inclusive), to limit the summary to orders placed in that period. Cancelled orders don't
count. Same access as `GET /vendors/me`.""",
        routes=("GET /vendors/me/sales",),
        files=(MV_VENDOR_ROUTER, MV_VENDOR_SERVICE),
        repo="multivendor",
    ),
    Ticket(
        id="t32_monthly_report",
        title="Monthly order report for admins",
        text="""\
Finance wants a monthly snapshot of orders. Add `GET /orders/report?month=YYYY-MM`, for
admins only (the same permission as viewing every order), answering

    {"month": "2026-02",
     "orders": <number of orders placed that month>,
     "revenue": <sum of those orders' totals, not counting cancelled orders>,
     "by_status": {"pending": <n>, "confirmed": <n>, "shipped": <n>, "delivered": <n>,
                   "cancelled": <n>}}

`month` is required; an invalid month is a 422.""",
        routes=("GET /orders/report",),
        files=(MV_ORDER_ROUTER, MV_ORDER_SERVICE),
        repo="multivendor",
    ),
    Ticket(
        id="t33_top_sellers",
        title="Best-seller list",
        text="""\
The storefront wants a "popular" shelf. Add `GET /products/top-sellers`, listing every
active product as

    {"product_id": <id>, "name": <name>, "units_sold": <units sold>}

best-selling first, ties by `product_id`. An optional `category` query parameter limits
the list to one category. Units in cancelled orders don't count. It is public, like
`GET /products`.""",
        routes=("GET /products/top-sellers",),
        files=(MV_PRODUCT_ROUTER, MV_PRODUCT_SERVICE),
        repo="multivendor",
    ),
    Ticket(
        id="t34_customer_spend",
        title="Customer spend list for admins",
        text="""\
Support wants to know who the big customers are. Add `GET /orders/customers`, for admins
only (the same permission as viewing every order), listing every buyer as

    {"user_id": <id>, "email": <email>, "orders": <number of their orders>,
     "spent": <sum of those orders' totals>}

Cancelled orders don't count in either number. Highest `spent` first, ties by
`user_id`.""",
        routes=("GET /orders/customers",),
        files=(MV_ORDER_ROUTER, MV_ORDER_SERVICE),
        repo="multivendor",
    ),
    Ticket(
        id="t35_vendor_order_lines",
        title="Vendors see the order lines they must fulfil",
        text="""\
Vendors need a packing list. Add `GET /vendors/me/order-lines`, listing every order line
for the vendor's products as

    {"order_id": <id>, "status": <the order's status>, "product_id": <id>,
     "product_name": <name>, "quantity": <n>, "price_at_purchase": <price>,
     "ordered_at": <when the order was placed>}

newest order first, then by line id. Same access as `GET /vendors/me`.""",
        routes=("GET /vendors/me/order-lines",),
        files=(MV_VENDOR_ROUTER, MV_VENDOR_SERVICE),
        repo="multivendor",
    ),
    Ticket(
        id="t36_cancel_order",
        title="Buyers cancel their own orders",
        text="""\
Buyers keep emailing support to cancel orders. Add `POST /orders/{order_id}/cancel`. A
buyer can cancel their own order while it is pending or confirmed; admins can cancel any
order. Cancelling puts the ordered quantities back in stock. Answer with the order, as
`GET /orders/{order_id}` does.""",
        routes=("POST /orders/{order_id}/cancel",),
        files=(MV_ORDER_ROUTER, MV_ORDER_SERVICE),
        repo="multivendor",
    ),
    Ticket(
        id="t37_product_buyers",
        title="Who bought my product",
        text="""\
Vendors want to know who buys what. Add `GET /products/{product_id}/buyers`, listing the
customers of one of the vendor's products as

    {"user_id": <id>, "full_name": <name>, "units": <units of this product they bought>}

most units first, ties by `user_id`. Cancelled orders don't count. Same access rules as
`PATCH /products/{product_id}`.""",
        routes=("GET /products/{product_id}/buyers",),
        files=(MV_PRODUCT_ROUTER, MV_PRODUCT_SERVICE),
        repo="multivendor",
    ),
    Ticket(
        id="t38_reorder",
        title="Buy again",
        text="""\
Add a "buy again" button's endpoint: `POST /orders/{order_id}/reorder` places a new order
for the caller with the same products and quantities as one of their past orders, and
answers 201 with the new order, like `POST /orders`. Only buyers can reorder, and only
their own orders. If a product doesn't have enough stock, answer 409 as `POST /orders`
does.""",
        routes=("POST /orders/{order_id}/reorder",),
        files=(MV_ORDER_ROUTER, MV_ORDER_SERVICE),
        repo="multivendor",
    ),
)
OPTION1: tuple[str, ...] = tuple(t.id for t in OPTION1_TICKETS)

# ---- option 1's second pilot (t39-t44): atomicity, on the same app ---------------------
#
# After the first pilot's one real slip (t38: a refused reorder left a partial write), six
# tickets whose correct change needs several writes to succeed or fail together; the app's
# own helpers commit partway (update_details, register_vendor, decrement_stock_optimistic).
# Written as texts first and approved on 2026-09-29. Every error the oracle compares is
# stated in the text (the first pilot's lesson). Traps (never sent to a model):
#
# * t39_bulk_price -- all or nothing: a missing or foreign product anywhere in the list
#   changes nothing (a per-item PATCH commits each); a repeated id counts once; a price
#   <= 0 anywhere rejects the whole request.
# * t40_edit_order_items -- a 409 writes nothing (no stock returned, no lines replaced);
#   stock the order already holds counts toward the new quantity; another buyer's order is
#   the 403, checked before the status.
# * t41_close_shop -- the 409 is decided before any write; cancelling a multi-vendor order
#   restores every line's stock, the other vendor's too; orders are counted once.
# * t42_split_order -- every item is validated before the new order exists (a 400 writes
#   nothing); totals from price_at_purchase, not current prices; stock unchanged.
# * t43_vendor_onboarding -- a duplicate name writes nothing (no vendor row, the role
#   stays buyer: register_vendor commits first); an existing vendor creates nothing.
# * t44_merge_orders -- every order is validated before anything moves; stock is not
#   returned (the lines moved, nothing was cancelled back into stock).

OPTION1B_TICKETS: tuple[Ticket, ...] = (
    Ticket(
        id="t39_bulk_price",
        title="Update several prices at once",
        text="""\
Vendors reprice their catalogue in batches. Add `POST /products/bulk-price` with the JSON
body

    {"changes": [{"product_id": <id>, "price": <new price>}, ...]}

Each listed product gets its new price, exactly as if it had been changed with
`PATCH /products/{product_id}`, and the answer is `{"updated": <number of products
changed>}`. Same access rules and errors as `PATCH /products/{product_id}`: 404 "Product
not found" for a product that doesn't exist, 403 "You do not own this product" for
another vendor's.""",
        routes=("POST /products/bulk-price",),
        files=(MV_PRODUCT_ROUTER, MV_PRODUCT_SERVICE),
        repo="multivendor",
    ),
    Ticket(
        id="t40_edit_order_items",
        title="Change the contents of a pending order",
        text="""\
Buyers want to fix an order before it is confirmed. Add `PATCH /orders/{order_id}/items`
with the JSON body

    {"items": [{"product_id": <id>, "quantity": <n>}, ...]}

which becomes the order's new contents. Only the buyer who placed the order can change it
(anyone else gets 403 "You do not have access to this order"; an unknown order is 404
"Order not found"), and only while it is pending (otherwise 400 "Only pending orders can
be changed"). Stock and the order total follow the change, and every line is priced at
the product's current price. If a product doesn't have enough stock, answer 409 as
`POST /orders` does. Answer with the order, as `GET /orders/{order_id}` does.""",
        routes=("PATCH /orders/{order_id}/items",),
        files=(MV_ORDER_ROUTER, MV_ORDER_SERVICE),
        repo="multivendor",
    ),
    Ticket(
        id="t41_close_shop",
        title="Vendors can close their shop",
        text="""\
Add `POST /vendors/me/close` for vendors leaving the marketplace. Every product of the
vendor is deactivated, and every pending order containing their products is cancelled
(its stock goes back, as when an order is cancelled). If any confirmed or shipped order
contains their products, the shop can't close yet: answer 409 with detail "Shop has
orders being fulfilled". The answer is

    {"products_deactivated": <number of the vendor's products that were active>,
     "orders_cancelled": <number of orders cancelled>}

Same access as `GET /vendors/me`.""",
        routes=("POST /vendors/me/close",),
        files=(MV_VENDOR_ROUTER, MV_VENDOR_SERVICE),
        repo="multivendor",
    ),
    Ticket(
        id="t42_split_order",
        title="Split an order",
        text="""\
Buyers want part of a pending order shipped separately. Add
`POST /orders/{order_id}/split` with the JSON body `{"item_ids": [<order item id>, ...]}`:
the listed lines of the buyer's pending order move to a new pending order for the same
buyer, and both orders' totals follow. Answer 201 with the new order, as `POST /orders`
does.

- Someone else's order: 403 "You do not have access to this order"; an unknown order: 404
  "Order not found".
- The order isn't pending: 400 "Only pending orders can be split".
- A listed item isn't a line of this order: 400 "Item <id> is not in this order".
- Every line of the order is listed: 400 "Cannot move every item".""",
        routes=("POST /orders/{order_id}/split",),
        files=(MV_ORDER_ROUTER, MV_ORDER_SERVICE),
        repo="multivendor",
    ),
    Ticket(
        id="t43_vendor_onboarding",
        title="Onboard a vendor with their first products",
        text="""\
New sellers want to open a shop and list their first products in one step. Add
`POST /vendors/onboard` with the JSON body

    {"business_name": <name>, "products": [<a product, as for POST /products>, ...]}

It registers the caller as a vendor, exactly as `POST /vendors` does (including its 400
"This user is already registered as a vendor"), and creates the listed products for the
new shop, exactly as `POST /products` would. Product names must be unique within the
request: otherwise answer 400 with detail "Duplicate product name: <name>". Answer 201
with

    {"vendor": <the vendor, as GET /vendors/me shows it>,
     "products": [<each product, as GET /products/{product_id} shows it>, ...]}""",
        routes=("POST /vendors/onboard",),
        files=(MV_VENDOR_ROUTER, MV_VENDOR_SERVICE),
        repo="multivendor",
    ),
    Ticket(
        id="t44_merge_orders",
        title="Merge pending orders",
        text="""\
Buyers who placed several small orders want them shipped together. Add
`POST /orders/merge` with the JSON body `{"order_ids": [<id>, <id>, ...]}` (at least
two). Every line of the listed orders moves into the first order listed, whose total
follows; the other orders are cancelled and left empty, with a total of 0. Answer with
the merged order, as `GET /orders/{order_id}` does.

Each listed order must be the caller's own pending order: someone else's is 403 "You do
not have access to this order", an unknown one 404 "Order not found", and one that isn't
pending 400 "Only pending orders can be merged".""",
        routes=("POST /orders/merge",),
        files=(MV_ORDER_ROUTER, MV_ORDER_SERVICE),
        repo="multivendor",
    ),
)
OPTION1B: tuple[str, ...] = tuple(t.id for t in OPTION1B_TICKETS)

# ---- the pivot's held-out set (t45-t56, plan 2026-10-05-pivot-spec-authoring.md, B) ----
#
# Twelve new tickets on the same app, written as texts first by an agent that saw neither
# the drafting checklist (plan item C) nor Phase 1's per-ticket results. They cover state
# machines (including illegal moves), invariants (totals, stock, counts), nested answers,
# bounds (exclusive ones too), role x ownership x missing rows, atomicity, aggregates
# over no rows, duplicates in a request, empty collections and full answer shapes. Every
# error the oracle compares is stated in the text. Traps (never sent to a model;
# ``bench/edit_slice/repos/multivendor.py`` pins each, ``tests/bench/
# test_edit_slice_pivot.py`` has a silent variant for each):
#
# * t45_restock -- 0 is refused and 1000 accepted (the bounds as stated); stock is added
#   to, not set; another vendor's product is the PATCH 403; inactive products restock.
# * t46_ship_order -- a vendor with products, but none in this order, gets the 403; a
#   pending order cannot jump to shipped; a cancelled one cannot be shipped; an admin
#   needs no products in the order.
# * t47_quote -- a product listed twice is one line, its stock checked against the summed
#   quantity; lines keep the request's order; `items` is units, not lines; inactive
#   products are refused; nothing is written.
# * t48_vendor_dashboard -- an order with two of the vendor's lines counts once; every
#   status listed, zeros included; inactive products are not "out of stock"; units in
#   cancelled orders not sold; a shop whose products never sold has a null best seller.
# * t49_remove_order_item -- the stock comes back; the total follows at the prices paid,
#   not current prices; an item of another order is 404 and that order is untouched; the
#   last line stays; another buyer's order is the 403, checked before the status.
# * t50_cancel_batch -- a refused batch writes nothing (a later unknown or shipped order);
#   an order listed twice is cancelled and restocked once; every line restocked; only
#   pending and confirmed orders can be cancelled.
# * t51_low_stock -- "fewer than" is strict; 0 is refused and 1000 accepted; inactive
#   products are not on sale; no product is [].
# * t52_order_by_vendor -- prices paid, not current prices; vendors by id and lines by
#   item id, not the order the lines come in; a vendor with a product in the order still
#   gets GET /orders/{order_id}'s 403.
# * t53_move_stock -- a refused move writes nothing (the target is checked before the
#   source is debited); moving all of the stock is allowed; the same product is the 400;
#   0 is the 422.
# * t54_order_stats -- no orders is 0 spent, not null; the latest order is the latest
#   non-cancelled one; units and orders exclude cancelled orders; orders, not lines.
# * t55_vendor_directory -- a verified shop with no active products is listed with 0 and
#   []; ordered by name, not id; inactive products' counts and categories excluded;
#   categories once each; unverified shops excluded.
# * t56_order_received -- only shipped -> delivered (not from pending or confirmed);
#   another buyer's order is the 403 before the status; admins and the order's vendors
#   are not the buyer.

MV_USER_ROUTER = "routers/user_router.py"
MV_USER_SERVICE = "services/user_service.py"

PIVOT_TICKETS: tuple[Ticket, ...] = (
    Ticket(
        id="t45_restock",
        title="Restock a product",
        text="""\
Vendors restock by typing a new stock number by hand and keep getting it wrong. Add
`POST /products/{product_id}/restock` with the JSON body `{"quantity": <n>}`, which adds
`n` units to the product's stock. `n` must be greater than 0 and at most 1000; any other
value is a 422. Same access rules and errors as `PATCH /products/{product_id}`. Answer
with the product, as `GET /products/{product_id}` does.""",
        routes=("POST /products/{product_id}/restock",),
        files=(MV_PRODUCT_ROUTER, MV_PRODUCT_SERVICE),
        repo="multivendor",
    ),
    Ticket(
        id="t46_ship_order",
        title="Vendors ship orders",
        text="""\
Vendors want to mark orders as shipped themselves. Add `POST /orders/{order_id}/ship`, for
vendors and admins (the same permission as `PATCH /orders/{order_id}/status`). A vendor
can ship an order only if it contains at least one of their products (otherwise 403 with
detail "You have no products in this order"); an admin can ship any order. Only a
confirmed order can be shipped: otherwise answer 400 with detail "Only confirmed orders
can be shipped". An unknown order is 404 "Order not found". Answer with the order, as
`GET /orders/{order_id}` does.""",
        routes=("POST /orders/{order_id}/ship",),
        files=(MV_ORDER_ROUTER, MV_ORDER_SERVICE),
        repo="multivendor",
    ),
    Ticket(
        id="t47_quote",
        title="Price a basket before ordering",
        text="""\
Buyers want to see what a basket costs before they order it. Add `POST /orders/quote`,
for anyone signed in, taking the same body as `POST /orders`
(`{"items": [{"product_id": <id>, "quantity": <n>}, ...]}`). It places no order and
changes nothing; it answers

    {"lines": [{"product_id": <id>, "name": <name>, "quantity": <n>,
                "unit_price": <the product's price>,
                "line_total": <quantity x unit_price>}, ...],
     "items": <total number of units>,
     "total": <sum of the line totals>}

with one line per product, in the order the products first appear in the request.

- A product that doesn't exist: 404 with detail "Product <id> not found".
- A product that is no longer on sale (inactive): 400 "Product <id> is not available".
- Not enough stock for the quantity asked: 409 "Insufficient stock for product <id>".""",
        routes=("POST /orders/quote",),
        files=(MV_ORDER_ROUTER, MV_ORDER_SERVICE),
        repo="multivendor",
    ),
    Ticket(
        id="t48_vendor_dashboard",
        title="Shop dashboard for vendors",
        text="""\
Vendors want one page showing how their shop stands. Add `GET /vendors/me/dashboard`,
answering

    {"vendor": <the vendor, as GET /vendors/me shows it>,
     "products": {"total": <number of their products>,
                  "active": <number of their active products>,
                  "out_of_stock": <number of their active products with no stock left>},
     "orders": {"pending": <n>, "confirmed": <n>, "shipped": <n>, "delivered": <n>,
                "cancelled": <n>},
     "units_sold": <units of their products sold>,
     "best_seller": {"product_id": <id>, "name": <name>, "units": <units sold>}}

`orders` counts, by status, the orders containing the vendor's products. Units in
cancelled orders were not sold. `best_seller` is the product with the most units sold
(ties: the lower `product_id`), or null if none of their products has sold. Same access
as `GET /vendors/me`.""",
        routes=("GET /vendors/me/dashboard",),
        files=(MV_VENDOR_ROUTER, MV_VENDOR_SERVICE),
        repo="multivendor",
    ),
    Ticket(
        id="t49_remove_order_item",
        title="Remove one item from an order",
        text="""\
Buyers want to drop one item from an order without cancelling all of it. Add
`DELETE /orders/{order_id}/items/{item_id}`: the line is removed from the order, its
quantity goes back in stock, and the order's total follows. Answer with the order, as
`GET /orders/{order_id}` does.

- Only the buyer who placed the order can do this: anyone else gets 403 "You do not have
  access to this order". An unknown order is 404 "Order not found".
- Only pending orders can be changed: otherwise 400 "Only pending orders can be
  changed".
- An item that isn't a line of this order: 404 "Item not found".
- An order must keep at least one line: removing its only line is 400 "Cannot remove the
  last item".""",
        routes=("DELETE /orders/{order_id}/items/{item_id}",),
        files=(MV_ORDER_ROUTER, MV_ORDER_SERVICE),
        repo="multivendor",
    ),
    Ticket(
        id="t50_cancel_batch",
        title="Cancel orders in a batch",
        text="""\
Support cancels orders in batches when they find fraud. Add `POST /orders/cancel-batch`,
for admins only (the same permission as viewing every order), with the JSON body
`{"order_ids": [<id>, ...]}`. Every listed order is cancelled, as
`PATCH /orders/{order_id}/status` with `{"status": "cancelled"}` would cancel it, and the
ordered quantities go back in stock. The answer is `{"cancelled": <number of orders
cancelled>}`.

- A listed order doesn't exist: 404 with detail "Order <id> not found".
- A listed order can no longer be cancelled: 400 "Order <id> cannot be cancelled".""",
        routes=("POST /orders/cancel-batch",),
        files=(MV_ORDER_ROUTER, MV_ORDER_SERVICE),
        repo="multivendor",
    ),
    Ticket(
        id="t51_low_stock",
        title="Low-stock list for vendors",
        text="""\
Vendors want to know what to reorder. Add `GET /vendors/me/low-stock`, listing the
vendor's products that are on sale and have fewer than `threshold` units in stock, as

    {"product_id": <id>, "name": <name>, "stock_quantity": <n>}

lowest stock first, ties by `product_id`. `threshold` is an optional query parameter, a
whole number greater than 0 and at most 1000 (otherwise 422); it defaults to 10. Same
access as `GET /vendors/me`.""",
        routes=("GET /vendors/me/low-stock",),
        files=(MV_VENDOR_ROUTER, MV_VENDOR_SERVICE),
        repo="multivendor",
    ),
    Ticket(
        id="t52_order_by_vendor",
        title="An order, grouped by shop",
        text="""\
The order page should show an order grouped by shop. Add
`GET /orders/{order_id}/by-vendor`, with the same access as `GET /orders/{order_id}`,
answering

    {"order_id": <id>, "status": <the order's status>, "total": <the order's total>,
     "vendors": [{"vendor_id": <id>, "business_name": <name>,
                  "subtotal": <what the order pays this vendor>,
                  "lines": [{"item_id": <order item id>, "product_id": <id>,
                             "name": <product name>, "quantity": <n>,
                             "price": <the price paid per unit>}, ...]}, ...]}

with one entry per vendor who has products in the order, vendors in order of
`vendor_id`, and each vendor's lines in order of `item_id`.""",
        routes=("GET /orders/{order_id}/by-vendor",),
        files=(MV_ORDER_ROUTER, MV_ORDER_SERVICE),
        repo="multivendor",
    ),
    Ticket(
        id="t53_move_stock",
        title="Move stock between two listings",
        text="""\
Vendors who sell one item under two listings want to move stock between them. Add
`POST /products/{product_id}/move-stock` with the JSON body
`{"to_product_id": <id>, "quantity": <n>}`: `n` units leave the product's stock and are
added to the other product's.

- Both products must be the vendor's own, with the same access rules and errors as
  `PATCH /products/{product_id}` (404 "Product not found", 403 "You do not own this
  product"), whichever of the two products fails them.
- `quantity` must be greater than 0 (otherwise 422), and no more than the product has in
  stock (otherwise 409 "Not enough stock").
- Moving stock to the same product: 400 "Cannot move stock to the same product".

Answer `{"from": <the product>, "to": <the other product>}`, each as
`GET /products/{product_id}` shows it.""",
        routes=("POST /products/{product_id}/move-stock",),
        files=(MV_PRODUCT_ROUTER, MV_PRODUCT_SERVICE),
        repo="multivendor",
    ),
    Ticket(
        id="t54_order_stats",
        title="Order summary on the account page",
        text="""\
Buyers want a small summary on their account page. Add `GET /users/me/order-stats`, for
anyone signed in, about the orders they placed:

    {"orders": <number of orders>, "units": <units bought>,
     "spent": <sum of the orders' totals>,
     "last_order_at": <when the latest order was placed, or null if there is none>}

Cancelled orders don't count.""",
        routes=("GET /users/me/order-stats",),
        files=(MV_USER_ROUTER, MV_USER_SERVICE),
        repo="multivendor",
    ),
    Ticket(
        id="t55_vendor_directory",
        title="Public list of shops",
        text="""\
The storefront wants a page listing the shops. Add `GET /vendors`, public like
`GET /products`, listing every verified vendor as

    {"vendor_id": <id>, "business_name": <name>,
     "products": <number of their active products>,
     "categories": [<the categories of their active products, each once,
                     alphabetically>]}

ordered by `business_name`.""",
        routes=("GET /vendors",),
        files=(MV_VENDOR_ROUTER, MV_VENDOR_SERVICE),
        repo="multivendor",
    ),
    Ticket(
        id="t56_order_received",
        title="Buyers confirm delivery",
        text="""\
Buyers want to confirm that a parcel arrived. Add `POST /orders/{order_id}/received`,
which marks the order delivered. Only the buyer who placed the order can do this: anyone
else gets 403 "You do not have access to this order"; an unknown order is 404 "Order not
found". Only a shipped order can be marked received: otherwise 400 "Only shipped orders
can be marked received". Answer with the order, as `GET /orders/{order_id}` does.""",
        routes=("POST /orders/{order_id}/received",),
        files=(MV_ORDER_ROUTER, MV_ORDER_SERVICE),
        repo="multivendor",
    ),
)
PIVOT: tuple[str, ...] = tuple(t.id for t in PIVOT_TICKETS)


def ticket(ticket_id: str) -> Ticket:
    for t in (*ROUND4_TICKETS, *OPTION1_TICKETS, *OPTION1B_TICKETS, *PIVOT_TICKETS):
        if t.id == ticket_id:
            return t
    raise KeyError(f"unknown ticket {ticket_id!r}")
