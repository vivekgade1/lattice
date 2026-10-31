"""The rules-first import for Go, G9: ``owner_via`` (a table whose rows belong to a user only
through a parent row) in ``lattice.importer.go_rules.infer_owners``. GORM declares no foreign
key the profile generator's ``owner_via`` could follow, so the importer infers the parent key
from the Go naming (``ShelfID`` -> ``Shelf``) and the routes' owner checks on the parent. Unit
tests on hand-made extracts; the ``shelfie`` fixture tests need ``go``."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest

from lattice.gocheck import GoPolicy
from lattice.importer import go_rules
from lattice.importer.emit_spec import build_spec
from lattice.importer.frontends.go_gin import GoGinFrontEnd, model_from_extract
from lattice.importer.lift import load_profile
from lattice.importer.profile_gen import generate_profile

REPO = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO / "tests" / "spec"

needs_go = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")


# ---- hand-made extracts -------------------------------------------------------------------------


def col(name: str, field: str, typ: str = "Int", *, pk: bool = False,
        fk: str | None = None) -> dict:  # fmt: skip
    return {
        "name": name, "field": field, "go_type": "uint" if typ == "Int" else "string",
        "type": typ, "nullable": False, "required": True, "default": None,
        "primary_key": pk, "unique": False, "index": False, "max_length": None,
        "foreign_key": fk, "line": 1,
    }  # fmt: skip


def table(struct: str, sql: str, *cols: dict) -> dict:
    return {
        "struct": struct, "table": sql, "name_unread": False, "package": "x/models",
        "file": "models.go", "line": 1, "source": "migrate", "migrated_at": [],
        "columns": list(cols), "unique_together": [], "checks": [],
    }  # fmt: skip


def use(model: str, column: str, how: str, line: int, file: str = "api.go") -> dict:
    return {"model": model, "column": column, "how": how, "file": file, "line": line}


def route(method: str, path: str, tables: list[str], uses: list[dict]) -> dict:
    return {
        "method": method, "path_template": path, "handler": None, "params": [], "unread": [],
        "status_code": None, "auth": "Customer", "auth_reason": "", "raises": [], "calls": [],
        "tables": tables, "owner_uses": uses, "body_models": [], "line": 1, "guards": [],
    }  # fmt: skip


def extract(tables: list[dict], routes: list[dict]) -> dict:
    auth = {"login_path": None, "login_encoding": "json", "username_field": None,
            "password_field": None, "token_field": None, "scheme": "bearer",
            "users_table": "User", "password_column": None}  # fmt: skip
    return {"schema": "lattice.goextract/1", "module": "x", "dialect": "", "tables": tables,
            "models": [], "routes": routes, "excluded": [], "auth": auth}  # fmt: skip


USER = table("User", "users", col("id", "ID", pk=True), col("name", "Name", "Str"))
SHELF = table("Shelf", "shelves", col("id", "ID", pk=True), col("user_id", "UserID"),
              col("name", "Name", "Str"))  # fmt: skip
BOOK = table("Book", "books", col("id", "ID", pk=True), col("shelf_id", "ShelfID"),
             col("title", "Title", "Str"))  # fmt: skip
SHELF_ROUTES = [
    route("GET", "/shelves", ["Shelf"], [use("Shelf", "user_id", "compare", 10)]),
    route("POST", "/shelves", ["Shelf"], [use("Shelf", "user_id", "assign", 20)]),
]
BOOK_ROUTE = route("GET", "/shelves/{id}/books", ["Book", "Shelf"],
                   [use("Shelf", "user_id", "compare", 30, "books.go")])  # fmt: skip


def infer(tables: list[dict], routes: list[dict]) -> tuple[Any, list[dict]]:
    data = extract([USER, *tables], routes)
    return go_rules.infer_owners(data, model_from_extract(data))


def fk_of(model: Any, table_name: str, column: str) -> str | None:
    t = next(t for t in model.tables if t.name == table_name)
    return next(c.foreign_key for c in t.columns if c.name == column)


def via_rows(inferences: list[dict]) -> list[dict]:
    return [i for i in inferences if i["rule"] == "owner_via"]


def profile_of(model: Any) -> Any:
    return generate_profile(model)


# ---- the child reached through its parent -------------------------------------------------------


def test_a_child_owned_through_its_parent_gets_the_parent_key_and_owner_via():
    model, inferences = infer([SHELF, BOOK], [*SHELF_ROUTES, BOOK_ROUTE])
    assert fk_of(model, "Book", "shelf_id") == "shelves.id"
    assert via_rows(inferences) == [{
        "table": "Book", "via": "shelf_id", "parent": "Shelf", "rule": "owner_via",
        "evidence": ["code:books.go:30"],
    }]  # fmt: skip
    generated = profile_of(model)
    assert "    owner_via=('shelf_id', 'Shelf')," in generated.source
    assert generated.ownership["Book"] == "owner_via"


def test_the_owner_via_reaches_the_go_checker_policy_through_the_spec():
    model, _ = infer([SHELF, BOOK], [*SHELF_ROUTES, BOOK_ROUTE])
    profile = load_profile(
        generate_profile(model), f"lattice_import_profile_{uuid.uuid4().hex}"
    ).profile()
    spec = build_spec(model, profile, users={}, programs={}, proposals=(), public={})
    policy = GoPolicy.from_spec(spec)
    assert policy.owner_via == {"Book": ("shelf_id", "Shelf")}
    assert policy.owners == {"Shelf": "user_id"}


def test_owner_via_rows_carry_no_owner_column():
    """``derive._owners`` and ``rules._go_policy`` read every inference's ``column`` as a
    users owner: an ``owner_via`` row must not have one."""
    _, inferences = infer([SHELF, BOOK], [*SHELF_ROUTES, BOOK_ROUTE])
    assert all("column" not in i for i in via_rows(inferences))
    owners = {i["table"]: i["column"] for i in inferences if i.get("column")}
    assert owners == {"Shelf": "user_id"}


def test_the_parent_column_name_alone_names_the_parent():
    """GORM's column for ``ShelfID`` is ``shelf_id``: either names the parent."""
    book = table("Book", "books", col("id", "ID", pk=True), col("shelf_id", "Shelf_Id"))
    model, inferences = infer([SHELF, book], [*SHELF_ROUTES, BOOK_ROUTE])
    assert fk_of(model, "Book", "shelf_id") == "shelves.id"
    assert [i["parent"] for i in via_rows(inferences)] == ["Shelf"]


# ---- direct owners are unchanged ----------------------------------------------------------------


def test_direct_users_foreign_keys_are_unchanged():
    """Shelf's own owner is today's answer; a child with its own users owner (Note: user_id and
    book_id) stays directly owned and gets no ``owner_via``."""
    note = table("Note", "notes", col("id", "ID", pk=True), col("user_id", "UserID"),
                 col("book_id", "BookID"))  # fmt: skip
    routes = [*SHELF_ROUTES, BOOK_ROUTE,
              route("GET", "/books/{id}/notes", ["Book", "Note", "Shelf"],
                    [use("Note", "user_id", "compare", 40), use("Shelf", "user_id", "compare", 41)])]  # fmt: skip  # noqa: E501
    model, inferences = infer([SHELF, BOOK, note], routes)
    owners = [i for i in inferences if i["rule"] == "owner_uses"]
    assert owners == [
        {"table": "Shelf", "column": "user_id", "rule": "owner_uses",
         "evidence": ["code:api.go:10", "code:api.go:20", "code:api.go:41", "code:books.go:30"]},
        {"table": "Note", "column": "user_id", "rule": "owner_uses",
         "evidence": ["code:api.go:40"]},
    ]  # fmt: skip
    assert [i["table"] for i in via_rows(inferences)] == ["Book"]
    assert fk_of(model, "Note", "book_id") is None
    assert fk_of(model, "Note", "user_id") == fk_of(model, "Shelf", "user_id") == "users.id"
    source = profile_of(model).source
    assert source.count("owner_via=") == 1 and "owner='user_id'" in source


def test_without_a_child_nothing_changes():
    model, inferences = infer([SHELF], SHELF_ROUTES)
    assert via_rows(inferences) == [] and fk_of(model, "Shelf", "user_id") == "users.id"


# ---- ambiguous or unsupported: nothing ----------------------------------------------------------


def test_two_owned_parents_both_checked_give_nothing():
    rack = table("Rack", "racks", col("id", "ID", pk=True), col("user_id", "UserID"))
    book = table("Book", "books", col("id", "ID", pk=True), col("shelf_id", "ShelfID"),
                 col("rack_id", "RackID"))  # fmt: skip
    routes = [*SHELF_ROUTES, BOOK_ROUTE,
              route("GET", "/racks", ["Rack"], [use("Rack", "user_id", "compare", 50)]),
              route("GET", "/racks/{id}/books", ["Book", "Rack"],
                    [use("Rack", "user_id", "compare", 51)])]  # fmt: skip
    model, inferences = infer([SHELF, rack, book], routes)
    assert via_rows(inferences) == []
    assert fk_of(model, "Book", "shelf_id") is None and fk_of(model, "Book", "rack_id") is None
    assert "owner_via=" not in profile_of(model).source


def test_a_parent_without_a_user_owner_gives_nothing():
    shelf = table("Shelf", "shelves", col("id", "ID", pk=True), col("name", "Name", "Str"))
    routes = [route("GET", "/shelves/{id}/books", ["Book", "Shelf"], [])]
    model, inferences = infer([shelf, BOOK], routes)
    assert inferences == [] and fk_of(model, "Book", "shelf_id") is None


def test_a_parent_with_two_user_owners_gives_nothing():
    shelf = table("Shelf", "shelves", col("id", "ID", pk=True), col("user_id", "UserID"),
                  col("curator_id", "CuratorID"))  # fmt: skip
    routes = [route("GET", "/shelves", ["Shelf"], [use("Shelf", "user_id", "compare", 10),
                                                   use("Shelf", "curator_id", "compare", 11)]),
              BOOK_ROUTE]  # fmt: skip
    model, inferences = infer([shelf, BOOK], routes)
    assert via_rows(inferences) == [] and fk_of(model, "Book", "shelf_id") is None


def test_no_route_checking_the_child_through_the_parent_gives_nothing():
    routes = [*SHELF_ROUTES, route("GET", "/books/{id}", ["Book", "Shelf"], [])]
    model, inferences = infer([SHELF, BOOK], routes)
    assert via_rows(inferences) == [] and fk_of(model, "Book", "shelf_id") is None


def test_a_declared_parent_key_is_left_to_the_profile_generator():
    """A declared foreign key needs no inference: the profile already follows it."""
    book = table("Book", "books", col("id", "ID", pk=True),
                 col("shelf_id", "ShelfID", fk="shelves.id"))  # fmt: skip
    model, inferences = infer([SHELF, book], [*SHELF_ROUTES, BOOK_ROUTE])
    assert via_rows(inferences) == []
    assert "    owner_via=('shelf_id', 'Shelf')," in profile_of(model).source


# ---- the shelfie fixture (the Go round 3 held-out app) ------------------------------------------


@needs_go
def test_shelfie_books_are_owned_through_their_shelf():
    fe = go_rules.InferringFrontEnd(GoGinFrontEnd())
    model = fe.extract(SPEC_DIR, "shelfie")
    rows = via_rows(fe.inferences)
    assert [(i["table"], i["via"], i["parent"]) for i in rows] == [("Book", "shelf_id", "Shelf")]
    assert {i["table"]: i["column"] for i in fe.inferences if i["rule"] == "owner_uses"} == {
        "Note": "user_id", "Shelf": "user_id"}  # fmt: skip
    assert "    owner_via=('shelf_id', 'Shelf')," in generate_profile(model).source
