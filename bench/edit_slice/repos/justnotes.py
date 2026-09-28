"""The oracles of the JustNotes tickets (t22-t24): tables, auth, routes, cases.

JustNotes (``bench/import_repos/justnotes``) as the generated suite sees it: ``user`` (the
login is the ``username``, the hash ``password``; no active flag, no superusers), and
``note`` and ``tag`` owned through ``owner_id``, linked many-to-many by ``notetaglink``.
Another user's note is a 403 (after the 404 "Note not found"), and a public note is
readable by everyone. After the seed rewrite (:mod:`bench.edit_slice.repos`) the users are
``admin`` 1, ``alice`` 2, ``bob`` 3 and ``carol`` 4 (their usernames are their seed
e-mails), and :data:`NOTES` and :data:`TAGS` (ids 1-8, owned round-robin) replace the
plan's placeholder text. Alice owns notes 2 (private) and 6 (public) and tags 2 and 6;
Bob notes 3 (public) and 7 (private) and tags 3 and 7. Note 7 carries Alice's tag 2 --
the link routes never check whose tag they attach, so that happens.

Rows are stamped with ``int(time.time())``, so the whole suite runs on a frozen clock
(:data:`FROZEN_NOW`, :func:`bench.edit_slice.repos.frozen_time`). The seed's password
hashes are bcrypt at the lowest cost (the app checks any cost), so a login takes
milliseconds rather than the default cost's quarter second.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import Engine, text

from bench.edit_slice.repos import IdMap, RepoOracle, Writer, add_rows, frozen_time
from bench.edit_slice.variant import Variant
from lattice.importer.suite import (
    ACTOR,
    ADMIN,
    INACTIVE,
    OTHER,
    SEED_USERS,
    AuthSpec,
    ColumnSpec,
    ErrorSpec,
    ParamSpec,
    RouteSpec,
    SeedPlan,
    TableSpec,
)
from lattice.importer.verify import Case

EPOCH = 1_767_261_600  # 2026-01-01T10:00:00Z, the seed's first timestamp
FROZEN_NOW = 1_790_000_000.0  # 2026-09-21: "now" while the suite runs
MISSING = 999  # no such note or tag
NOBODY = "nobody@example.com"  # a token naming no user

C, P = ColumnSpec, ParamSpec
USER = TableSpec(
    "user",
    (
        C("id", "int", primary_key=True),
        C("username", "str"),
        C("password", "str"),
        C("createdAt", "int"),
        C("updatedAt", "int"),
    ),
)
NOTE = TableSpec(
    "note",
    (
        C("title", "str"),
        C("text", "str"),
        C("isPublic", "bool"),
        C("id", "int", primary_key=True),
        C("createdAt", "int"),
        C("updatedAt", "int"),
        C("owner_id", "int", foreign_key="user.id"),
    ),
    owner="owner_id",
)
TAG = TableSpec(
    "tag",
    (
        C("id", "int", primary_key=True),
        C("name", "str"),
        C("category", "str", nullable=True),
        C("owner_id", "int", foreign_key="user.id"),
        C("createdAt", "int"),
    ),
    owner="owner_id",
)
TABLES = (USER, NOTE, TAG)
ORDER = ("user", "note", "tag", "notetaglink")

# note i (id i + 1; owners admin, alice, bob, carol in turn): title, text, public, and
# hours between its creation and its last update (so the newest note is not the latest
# updated one)
NOTES: tuple[tuple[str, str, bool, int], ...] = (
    ("Release checklist", "Tag the build, publish the notes.", True, 30),
    ("Grocery list", "Eggs, milk, bread.", False, 60),
    ("Milk run", "Pick up oat milk for the office.", True, 50),
    ("Reading list", "Dune; The Left Hand of Darkness.", False, 7),
    ("Server notes", "Rotate the keys every 90 days.", False, 1),
    ("Trip ideas", "Lisbon in May: pack light, 100% sunscreen.", True, 3),
    ("Standup", "Blocked on the milk API review.", False, 90),
    ("Recipes", "Pancakes: flour, milk, eggs.", True, 4),
)
TAGS = ("urgent", "home", "work", "books", "ops", "travel", "team", "food")
# (note id, tag id), in note order: alice's note 2 has her tags 2 and 6, her note 6 tag
# 6; bob's note 3 his tag 3, and his note 7 *alice's* tag 2; carol's note 8 her tag 4
LINKS = ((2, 2), (2, 6), (3, 3), (6, 6), (7, 2), (8, 4))


def _hash(password: str) -> str:
    import bcrypt

    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=4)).decode("utf-8")


def auth(ids: IdMap) -> AuthSpec:
    """Login by username (the plan's e-mail); tokens minted by the app's own JWT helper."""
    # seed user k's username, by plan UUID and (after the rewrite) by id k + 1
    usernames: dict[Any, str] = {}
    for k, (handle, _, _) in enumerate(SEED_USERS):
        usernames[k + 1] = f"{handle}@example.com"
    for plan_id, k in ids.items():
        usernames[plan_id] = usernames[k]

    def mint_token(user_id: uuid.UUID | int) -> str:
        from bench.import_repos.justnotes import _ensure_process_tz_utc
        from bench.import_repos.justnotes.app.utils.jwt import create_access_token

        _ensure_process_tz_utc()  # the helper stamps a naive local "exp" (PROVENANCE.md)
        name = usernames.get(user_id, NOBODY)
        return create_access_token({"sub": name}, expires_delta=timedelta(minutes=30))

    return AuthSpec(
        users_table="user",
        email_column="username",
        password_column="password",
        active_column=None,
        admin_column=None,
        login_path="/auth/login",
        hash_password=_hash,
        mint_token=mint_token,
    )


def fixup(plan: SeedPlan) -> None:
    """Unix-int timestamps, readable notes and tags (:data:`NOTES`, :data:`TAGS`), and
    the tag links (:data:`LINKS`)."""
    for k, row in enumerate(plan.rows["user"]):
        row["createdAt"] = row["updatedAt"] = EPOCH + 86_400 * k
    for i, row in enumerate(plan.rows["note"]):
        title, body, public, updated = NOTES[i]
        created = EPOCH + 3600 * (10 + 5 * i)
        row.update(title=title, text=body, isPublic=public, createdAt=created,
                   updatedAt=created + 3600 * updated)  # fmt: skip
    for i, row in enumerate(plan.rows["tag"]):
        row.update(name=TAGS[i], createdAt=EPOCH + 3600 * i)
    plan.rows["notetaglink"] = [{"note_id": n, "tag_id": t} for n, t in LINKS]


# ---- routes -----------------------------------------------------------------------------------

NOTE_ID = P("id", "int", "path")
COPY = RouteSpec(
    "copy_note", "POST", "/notes/{id}/copy", "other", "note", params=(NOTE_ID,),
    errors=(ErrorSpec(404, "Note not found", "not_found"),
            ErrorSpec(403, "Not authorized to access this note", "not_owner")),
)  # fmt: skip
LIST = RouteSpec(
    "get_notes", "GET", "/notes", "list", "note",
    params=(P("tag_id", "int", "query", required=False), P("q", "str", "query", required=False)),
)  # fmt: skip
STATS = RouteSpec("get_note_stats", "GET", "/notes/stats", "other", "note")

# ---- N4's fresh held-out set's routes (t29, t30; see bench.edit_slice.tickets docstring) --
TAG_SUMMARY = RouteSpec("get_tag_summary", "GET", "/tags/summary", "other", "tag")
BULK_DELETE_NOTES = RouteSpec("bulk_delete_notes", "POST", "/notes/bulk-delete", "other", "note")

ROUTES: dict[str, tuple[RouteSpec, ...]] = {
    "t22_copy_note": (COPY,),
    "t23_note_filter": (LIST,),
    "t24_note_stats": (STATS,),
    "t29_tag_summary": (TAG_SUMMARY,),
    "t30_bulk_delete_notes": (BULK_DELETE_NOTES,),
}


def branches(ticket_id: str) -> Callable[[Variant], dict[str, list[Callable[..., Any]]]]:
    """Each route's function, and the helpers that raise for it."""

    def of(ref: Variant) -> dict[str, list[Callable[..., Any]]]:
        notes = ref.module("api.notes")
        crud = ref.module("database.note_crud").NoteCRUD
        if ticket_id == "t22_copy_note":
            return {"copy_note": [notes.copy_note, notes._check_note_access,
                                  crud.get_note_by_id]}  # fmt: skip
        if ticket_id == "t23_note_filter":
            return {"get_notes": [notes.get_notes, crud.check_tag_access]}
        if ticket_id == "t29_tag_summary":
            tags = ref.module("api.tags")
            return {"get_tag_summary": [tags.get_tag_summary]}
        if ticket_id == "t30_bulk_delete_notes":
            return {"bulk_delete_notes": [notes.bulk_delete_notes, crud.get_note_by_id]}
        return {"get_note_stats": [notes.get_note_stats]}

    return of


# ---- database variants ------------------------------------------------------------------------


def writers(ticket_id: str, plan: SeedPlan) -> dict[str, Writer]:
    uid = {u.handle: u.id for u in plan.users}

    def note(nid: int, owner: str, title: str) -> Writer:
        def write(engine: Engine) -> None:
            add_rows(engine, ticket_id, "note", [{
                "id": nid, "title": title, "text": "Kept from last time.", "isPublic": False,
                "owner_id": uid[owner], "createdAt": EPOCH, "updatedAt": EPOCH,
            }])  # fmt: skip

        return write

    def sql(statement: str, **params: Any) -> Writer:
        def write(engine: Engine) -> None:
            with engine.begin() as conn:
                conn.execute(text(statement), params)

        return write

    return {
        "alice_has_the_copy": note(101, ACTOR, "Grocery list (copy)"),
        "bob_has_that_title": note(101, OTHER, "Trip ideas (copy)"),
        "no_bob_notes": sql("DELETE FROM note WHERE owner_id = :o", o=uid[OTHER]),
        "no_links": sql("DELETE FROM notetaglink"),
    }


# ---- ticket-specific cases --------------------------------------------------------------------


def _note(plan: SeedPlan, handle: str, public: bool) -> int:
    return next(r["id"] for r in plan.owned("note", handle) if r["isPublic"] is public)


def _tag(plan: SeedPlan, handle: str, name: str) -> int:
    return next(r["id"] for r in plan.owned("tag", handle) if r["name"] == name)


def t22_cases(plan: SeedPlan) -> list[Case]:
    a_private, a_public = _note(plan, ACTOR, False), _note(plan, ACTOR, True)
    b_private, b_public = _note(plan, OTHER, False), _note(plan, OTHER, True)
    c_public = _note(plan, INACTIVE, True)

    def c(what: str, note: int, user: str = ACTOR, db: str = "seed",
          tables: tuple[str, ...] = ("note",)) -> Case:  # fmt: skip
        return Case(f"t22.{what}", "POST", f"/notes/{note}/copy", user=user, db=db,
                    tables=tables, route="copy_note")  # fmt: skip

    return [
        c("alice_own_private", a_private),
        c("alice_own_public", a_public),
        c("alice_bobs_public", b_public),
        c("alice_bobs_private", b_private),
        c("alice_missing", MISSING),
        c("alice_copies_again", a_private, db="alice_has_the_copy"),
        c("alice_title_taken_by_bob_only", a_public, db="bob_has_that_title"),
        c("tags_are_not_copied", a_private, tables=("note", "notetaglink")),
        c("carol_alices_private", a_private, user=INACTIVE),
        c("admin_carols_public", c_public, user=ADMIN),
    ]


def t23_cases(plan: SeedPlan) -> list[Case]:
    home, travel = _tag(plan, ACTOR, "home"), _tag(plan, ACTOR, "travel")
    work = _tag(plan, OTHER, "work")

    def c(what: str, user: str = ACTOR, **params: Any) -> Case:
        return Case(f"t23.{what}", "GET", "/notes", user=user, params=params,
                    route="get_notes")  # fmt: skip

    return [
        c("alice_tag_on_bobs_note_too", tag_id=home),
        c("alice_tag_on_two_notes", tag_id=travel),
        c("bob_own_tag", user=OTHER, tag_id=work),
        c("alice_bobs_tag", tag_id=work),
        c("alice_missing_tag", tag_id=MISSING),
        c("tag_malformed", tag_id="home"),
        c("q_ignores_case", q="MILK"),
        c("q_searches_the_text", q="lisbon"),
        c("q_percent_is_literal", q="%"),
        c("q_underscore_is_literal", q="_"),
        c("q_other_users_notes", user=OTHER, q="grocery"),
        c("bob_q", user=OTHER, q="milk"),
        c("q_and_tag", tag_id=travel, q="MILK"),
        c("q_and_tag_no_match", tag_id=travel, q="lisbon trip"),
        c("q_no_match", q="zzz"),
        c("empty_q", q=""),
    ]


def t24_cases(plan: SeedPlan) -> list[Case]:
    def c(what: str, user: str, db: str = "seed") -> Case:
        return Case(f"t24.{what}", "GET", "/notes/stats", user=user, db=db,
                    route="get_note_stats")  # fmt: skip

    return [
        c("alice", ACTOR),
        c("bob", OTHER),
        c("admin", ADMIN),
        c("carol", INACTIVE),
        c("bob_without_notes", OTHER, "no_bob_notes"),
        c("alice_without_links", ACTOR, "no_links"),
        Case("t24.note_route_still_works", "GET", f"/notes/{_note(plan, ACTOR, False)}",
             user=ACTOR, route="get_note_stats"),
        Case("t24.note_route_other_users_public", "GET", f"/notes/{_note(plan, OTHER, True)}",
             user=ACTOR, route="get_note_stats"),
    ]  # fmt: skip


def t29_cases(plan: SeedPlan) -> list[Case]:
    def c(what: str, user: str) -> Case:
        return Case(f"t29.{what}", "GET", "/tags/summary", user=user, route="get_tag_summary")

    return [
        # alice's "home" tag is also linked to bob's note 7 (the tag-link routes never
        # check whose tag they attach); her "travel" tag is linked to two of her own
        c("alice_tags_summary", ACTOR),
        c("bob_tags_summary", OTHER),
        # carol's "books" tag has one of her own notes linked, "food" none at all
        c("carol_tags_summary", INACTIVE),
        # admin's two tags ("urgent", "ops") are never linked to any note
        c("admin_tags_summary", ADMIN),
    ]


def t30_cases(plan: SeedPlan) -> list[Case]:
    a_private, a_public = _note(plan, ACTOR, False), _note(plan, ACTOR, True)
    b_public = _note(plan, OTHER, True)

    def c(what: str, ids: list[int], user: str = ACTOR) -> Case:
        return Case(f"t30.{what}", "POST", "/notes/bulk-delete", user=user,
                    json={"note_ids": ids}, tables=("note",),
                    route="bulk_delete_notes")  # fmt: skip

    return [
        c("alice_deletes_both_own", [a_private, a_public]),
        c("bobs_note_in_alices_list", [a_private, b_public]),
        c("unknown_id_in_list", [a_private, MISSING]),
        c("repeated_id_counted_once", [a_private, a_private]),
        c("empty_list", []),
        # JustNotes has no admin bypass (unlike the template): an admin deleting
        # someone else's note gets the same 403 anyone else would
        c("admin_cannot_delete_others_note", [b_public], user=ADMIN),
    ]


SPECIFIC = {"t22_copy_note": t22_cases, "t23_note_filter": t23_cases,
            "t24_note_stats": t24_cases, "t29_tag_summary": t29_cases,
            "t30_bulk_delete_notes": t30_cases}  # fmt: skip


def oracle_spec(ticket_id: str) -> RepoOracle:
    return RepoOracle(
        routes=ROUTES[ticket_id],
        tables=TABLES,
        auth=auth,
        order=ORDER,
        fixup=fixup,
        specific=SPECIFIC[ticket_id],
        writers=writers,
        branches=branches(ticket_id),
        clock=lambda: frozen_time(FROZEN_NOW),
    )


__all__ = ["FROZEN_NOW", "LINKS", "NOTES", "ROUTES", "SPECIFIC", "TABLES", "auth", "oracle_spec"]
