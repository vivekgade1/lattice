# ruff: noqa: B008 -- idiomatic FastAPI dependency injection in the synthetic app
"""lattice.importer.trace (spec 2026-09-27 importer §3.6, S6): the SQL each request of a
suite issues against the original app, and N+1 detection -- on a synthetic app with a
lazy relationship and on the template."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from sqlalchemy.orm import selectinload
from sqlmodel import Session, create_engine, select

from bench.template_app import make_app
from bench.template_app.seed import TEMPLATE_USERS, build_template_db
from lattice.importer.trace import (
    NPlusOne,
    RequestTrace,
    SqlTracer,
    case_bindings,
    n_plus_one,
    shape,
    trace_suite,
)
from lattice.importer.verify import Case, form_login
from tests.importer.library_models import Author, Book, Library

# ---- a synthetic app: authors and their books, read lazily or eagerly ----------------------


def seed(variant: str, path: Path) -> Path:
    engine = create_engine(f"sqlite:///{path}")
    Library.metadata.create_all(engine)
    with Session(engine) as s:
        for i in range(5):
            a = Author(name=f"author {i}")
            s.add(a)
            s.flush()
            s.add_all(Book(title=f"book {i}.{j}", author_id=a.id) for j in range(2))
        s.commit()
    engine.dispose()
    return path


def library_app(path: Path) -> FastAPI:
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    app = FastAPI()
    app.state.engine = engine

    def get_session() -> Generator[Session]:
        with Session(engine) as s:
            yield s

    @app.get("/lazy")
    def lazy(s: Session = Depends(get_session)) -> list[dict[str, Any]]:
        return [{"name": a.name, "books": len(a.books)} for a in s.exec(select(Author)).all()]

    @app.get("/eager")
    def eager(s: Session = Depends(get_session)) -> list[dict[str, Any]]:
        authors = s.exec(select(Author).options(selectinload(Author.books))).all()  # type: ignore[arg-type]
        return [{"name": a.name, "books": len(a.books)} for a in authors]

    @app.get("/one/{id}")
    def one(id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
        a = s.get(Author, id)
        b = s.get(Author, id + 1)
        return {"a": a.name if a else None, "b": b.name if b else None}

    @app.post("/rename/{id}")
    def rename(id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
        a = s.get(Author, id)
        assert a is not None
        a.name = "renamed"
        s.add(a)
        s.commit()
        return {"ok": True}

    return app


CASES = [
    Case("lazy", "GET", "/lazy", route="lazy"),
    Case("eager", "GET", "/eager", route="eager"),
    Case("one", "GET", "/one/1", route="one"),
    Case("rename", "POST", "/rename/2", route="rename"),
]


@pytest.fixture(scope="module")
def traces(tmp_path_factory) -> dict[str, RequestTrace]:
    root = tmp_path_factory.mktemp("trace")
    return trace_suite(library_app, {"seed": seed("seed", root / "seed.db")}, CASES)


def test_each_request_is_traced_separately(traces):
    assert list(traces) == ["lazy", "eager", "one", "rename"]
    lazy = traces["lazy"]
    assert (lazy.route, lazy.method, lazy.path) == ("lazy", "GET", "/lazy")
    assert len(lazy.queries()) == 1 + 5  # the authors, then each author's books
    assert all(s.kind == "SELECT" for s in lazy.queries())
    assert len(traces["eager"].queries()) == 2  # authors, then all books IN (...)


def test_statements_keep_the_bound_form_and_the_executed_text(traces):
    get = traces["one"].queries()[0]
    assert get.template is not None and "?" in get.template
    assert get.params == (1,)
    assert get.sql.endswith("WHERE author.id = 1")
    kinds = [s.kind for s in traces["rename"].statements]
    # the engine's first connection opens inside the request: SQLAlchemy's dialect probe
    assert kinds == ["PRAGMA", "SELECT", "BEGIN", "UPDATE", "COMMIT"]
    update = traces["rename"].queries()[1]
    assert update.params == ("renamed", 2)


def test_n_plus_one_is_the_per_row_repeat(traces):
    found = n_plus_one(traces["lazy"])
    assert found == [
        NPlusOne(
            case="lazy",
            shape="SELECT book.id AS book_id, book.title AS book_title, book.author_id AS "
            "book_author_id FROM book WHERE ? = book.author_id",
            count=5,
            distinct=5,
            after="SELECT author.id, author.name FROM author",
        )
    ]
    assert n_plus_one(traces["eager"]) == []
    assert n_plus_one(traces["one"]) == []  # two lookups: below the threshold
    assert n_plus_one(traces["one"], threshold=2) != []


def test_a_repeat_with_the_same_values_is_not_n_plus_one():
    t = RequestTrace("c", None, "GET", "/")
    from lattice.importer.trace import TracedStatement

    for i in range(4):
        t.statements.append(TracedStatement(i, "SELECT x FROM t WHERE id = 1", None, None))
    assert n_plus_one(t) == []


def test_shape_replaces_literals_and_params():
    assert shape("SELECT a FROM t WHERE b = 'x''y' AND c = 12 AND d = ? LIMIT :n") == (
        "SELECT a FROM t WHERE b = ? AND c = ? AND d = ? LIMIT ?"
    )
    assert shape("SELECT a\n FROM t WHERE id IN (1, 2, 3)") == "SELECT a FROM t WHERE id IN (?)"
    assert shape('SELECT "t0"."c1" FROM t0') == 'SELECT "t0"."c1" FROM t0'


def test_sql_outside_a_case_is_not_attributed(tmp_path: Path):
    db = seed("seed", tmp_path / "s.db")
    with SqlTracer() as tracer:
        engine = create_engine(f"sqlite:///{db}")
        with Session(engine) as s:
            s.exec(select(Author)).all()  # outside any request
            with tracer.request("manual"):
                s.exec(select(Book)).all()
        engine.dispose()
    assert list(tracer.traces) == ["manual"]
    assert [q.sql for q in tracer.traces["manual"].queries()] == [
        "SELECT book.id, book.title, book.author_id \nFROM book"
    ]


# ---- the template ---------------------------------------------------------------------------


def test_the_template_login_is_not_part_of_the_case(tmp_path: Path):
    users = dict(TEMPLATE_USERS)
    credentials = {h: (e, p) for h, (e, p, _) in users.items()}
    login = form_login("/api/v1/login/access-token", credentials)
    db = build_template_db(tmp_path / "seed.db")
    cases = [Case("alice_items", "GET", "/api/v1/items/", user="alice", params={"skip": 1})]
    traces = trace_suite(make_app, {"seed": db}, cases, login=login)
    queries = traces["alice_items"].queries()
    alice = users["alice"][2].hex
    assert [q.params for q in queries] == [(alice,), (alice,), (alice, 100, 1)]
    assert queries[1].template.startswith("SELECT count(*) AS count_1")  # type: ignore[union-attr]
    assert n_plus_one(traces["alice_items"]) == []


def test_case_bindings_name_each_value_by_its_input():
    item, me = uuid.uuid4(), uuid.uuid4()
    case = Case("c", "PUT", f"/items/{item}", params={"skip": 3},
                json={"title": "t", "description": None, "n": True})  # fmt: skip
    got = case_bindings(case, "/items/{id}", caller=me.hex, defaults={"limit": 100, "skip": 0})
    assert got == {item: "id", me: "user", 3: "skip", 100: "limit", 0: "skip", "t": "title",
                   1: "n", None: "description"}  # fmt: skip
    two_nulls = Case("c", "PUT", "/", json={"a": None, "b": None})
    assert None not in case_bindings(two_nulls)
