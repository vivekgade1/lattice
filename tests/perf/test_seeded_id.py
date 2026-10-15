"""Seeded-id endpoints (``capture`` in a seed step; pre-registration
``docs/results/2026-10-15-perf-missing-index-prereg.md``, decision 10): the response's value of
a seed step becomes ``{name}`` for the size, usable in later steps and in the endpoint's path.
Only captured names are substituted; any other ``{x}`` stays literal."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lattice.perf.budget import BudgetError, parse, pluck, substitute
from lattice.perf.measure import Harness, MeasureError, SqliteCounter, measure, measure_size
from lattice.spec.check import as_db_factory
from tests.perf import indexapp


def _budget(steps: list[dict], endpoint: str = "GET /items/{item}/notes", **extra: Any) -> dict:
    return {"id": "b", "endpoint": endpoint, "user": "ann",
            "seed": {"items": "N", "steps": steps}, "sizes": [2, 6],
            "queries": {"constant": 3, "per_item": 0}, "latency_ms": None, **extra}  # fmt: skip


def _file(budget: dict):
    return parse({"version": 1, "budgets": [budget]})


NOTES = [
    {"method": "POST", "path": "/items", "user": "ann", "count": 1, "json": {"name": "i"},
     "capture": {"item": "id"}},
    {"method": "POST", "path": "/items/{item}/notes", "user": "ann", "count": "N",
     "json": {"body": "note-{i}"}},
]  # fmt: skip


# ---- the budget file ---------------------------------------------------------------------------


def test_capture_is_parsed():
    (b,) = _file(_budget(NOTES)).budgets
    assert b.steps[0].capture == (("item", "id"),) and b.steps[1].capture == ()
    assert b.endpoint == "GET /items/{item}/notes"  # the template is kept


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        ([{"method": "POST", "path": "/a", "capture": {"Item": "id"}}], "name 'Item' must be"),
        ([{"method": "POST", "path": "/a", "capture": {"i": "id"}}], "name 'i' must be"),
        ([{"method": "POST", "path": "/a", "capture": {"uuid": "id"}}], "name 'uuid' must be"),
        ([{"method": "POST", "path": "/a", "capture": {"x" * 33: "id"}}], "must be"),
        ([{"method": "POST", "path": "/a", "capture": {"a": ""}}], "a dotted key"),
        ([{"method": "POST", "path": "/a", "capture": {"a": "x..y"}}], "a dotted key"),
        ([{"method": "POST", "path": "/a", "capture": {"a": 3}}], "a dotted key"),
        ([{"method": "POST", "path": "/a", "capture": []}], "an object"),
        ([{"method": "POST", "path": "/a", "capture": {}}], "an object"),
        (
            [
                {"method": "POST", "path": "/a", "capture": {"a": "id"}},
                {"method": "POST", "path": "/b", "capture": {"a": "id"}},
            ],
            "'a' is already captured",
        ),
        (
            [{"method": "POST", "path": "/a/{a}", "capture": {"a": "id"}}],
            "{a} is used before the step that captures it",
        ),
        (
            [
                {"method": "POST", "path": "/b", "json": {"x": ["{a}"]}},
                {"method": "POST", "path": "/a", "capture": {"a": "id"}},
            ],
            "{a} is used before the step that captures it",
        ),
    ],
)
def test_capture_validation(steps, message):
    with pytest.raises(BudgetError, match=message.replace("{", r"\{").replace("}", r"\}")):
        _file(_budget(steps))


def test_substitute_and_pluck():
    got = {"item": "a/b c", "n": "3"}
    assert substitute("/items/{item}/notes/{n}", got, path=True) == "/items/a%2Fb%20c/notes/3"
    assert substitute({"k": ["{item}", 4, {"z": "{n}"}]}, got) == {"k": ["a/b c", 4, {"z": "3"}]}
    assert substitute("/x/{other}/{item}", got) == "/x/{other}/a/b c"  # uncaptured: literal
    assert substitute("{item}", {"item": "{n}", "n": "3"}) == "{n}"  # one pass
    assert substitute("/x/{item}", {}) == "/x/{item}"
    data = {"id": 7, "a": {"b": ["x", {"c": 1.5}]}, "t": True, "o": {}, "s": "str"}
    assert pluck(data, "id") == "7" and pluck(data, "s") == "str"
    assert pluck(data, "a.b.0") == "x" and pluck(data, "a.b.1.c") == "1.5"
    assert pluck(data, "t") is None and pluck(data, "o") is None and pluck(data, "nope") is None
    assert pluck([1, 2], "x") is None and pluck(None, "id") is None


# ---- the run: a fake transport that records what it is sent ----------------------------------


class _Response:
    def __init__(self, status: int, payload: Any) -> None:
        self.status_code, self._payload = status, payload
        self.text = json.dumps(payload)
        self.content = self.text.encode()

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no JSON")
        return self._payload


def _runtime(log: list[tuple], answer: Any):
    class Client:
        def request(self, method: str, path: str, json: Any = None, headers: Any = None):
            log.append((method, path, json))
            return _Response(201 if method == "POST" else 200, answer(method, path))

    @contextlib.contextmanager
    def open_(app: Any):
        yield Client()

    return SimpleNamespace(
        framework="fastapi", one_server=False, open=open_, close=lambda app: None,
        auth=SimpleNamespace(token=lambda c, user, login: "tok", present=lambda c, t: {}),
    )  # fmt: skip


def _run(tmp_path: Path, budget: dict, answer: Any, n: int = 2) -> list[tuple]:
    log: list[tuple] = []
    h = Harness(lambda db: object(), lambda path: path, _runtime(log, answer), None, "sqlite")
    (b,) = _file(budget).budgets
    measure_size(b, n, h, as_db_factory(h.db_factory), SqliteCounter(), tmp_path, runs=1)
    return log


def test_captured_value_reaches_the_app_percent_quoted(tmp_path):
    log = _run(tmp_path, _budget(NOTES), lambda m, p: {"id": "a/b"})
    assert log[0] == ("POST", "/items", {"name": "i"})
    assert [x[1] for x in log[1:3]] == ["/items/a%2Fb/notes"] * 2
    assert log[-1] == ("GET", "/items/a%2Fb/notes", None)


def test_captured_value_in_a_json_body_is_not_quoted(tmp_path):
    steps = [
        NOTES[0],
        {"method": "POST", "path": "/n", "user": "ann", "count": "N",
         "json": {"parent": "{item}", "n": 1, "tag": "x-{item}-{i}"}},
    ]  # fmt: skip
    log = _run(tmp_path, _budget(steps, "GET /n"), lambda m, p: {"id": "a/b"})
    assert log[1] == ("POST", "/n", {"parent": "a/b", "n": 1, "tag": "x-a/b-1"})


def test_only_the_first_repetition_is_captured(tmp_path):
    ids = iter(["first", "second", "third", "fourth", "fifth", "sixth"])
    steps = [{"method": "POST", "path": "/things", "user": "ann", "count": "N",
              "capture": {"item": "id"}},
             {"method": "POST", "path": "/things/{item}", "user": "ann", "count": 1}]  # fmt: skip
    log = _run(tmp_path, _budget(steps, "GET /things/{item}"),
               lambda m, p: {"id": next(ids)}, n=3)  # fmt: skip
    assert [x[1] for x in log] == ["/things"] * 3 + ["/things/first", "/things/first"]


def test_uncaptured_braces_stay_literal(tmp_path):
    steps = [NOTES[0], {"method": "POST", "path": "/items/{item}/{other}/notes", "user": "ann",
                        "count": "N", "json": {"body": "{other}-{item}"}}]  # fmt: skip
    log = _run(tmp_path, _budget(steps, "GET /items/{item}/{other}"), lambda m, p: {"id": 5})
    assert log[1] == ("POST", "/items/5/{other}/notes", {"body": "{other}-5"})
    assert log[-1][1] == "/items/5/{other}"
    # a budget file that never captured means what it meant: braces are literal everywhere
    plain = [{"method": "POST", "path": "/x/{item}", "user": "ann", "count": "N",
              "json": {"a": "{item}"}}]  # fmt: skip
    log = _run(tmp_path, _budget(plain, "GET /y/{item}"), lambda m, p: {"id": 5})
    assert log[0] == ("POST", "/x/{item}", {"a": "{item}"}) and log[-1][1] == "/y/{item}"


@pytest.mark.parametrize("answer", [{"nope": 1}, {"id": {"deep": 1}}, {"id": True}, None])
def test_missing_or_non_scalar_key_is_a_measure_error(tmp_path, answer):
    with pytest.raises(MeasureError) as err:
        _run(tmp_path, _budget(NOTES), lambda m, p: answer)
    assert str(err.value) == (
        "budget 'b' N=2: seed step POST /items #1: capture item: response has no id"
    )


def test_a_dotted_key(tmp_path):
    steps = [{**NOTES[0], "capture": {"item": "data.0.id"}}, NOTES[1]]
    log = _run(tmp_path, _budget(steps), lambda m, p: {"data": [{"id": 9}]})
    assert log[-1][1] == "/items/9/notes"


# ---- the real app -------------------------------------------------------------------------------


def test_seeded_id_counts_exactly_on_the_real_app():
    h = Harness(indexapp.make_app, indexapp.build_db, None, indexapp.login, "sqlite")
    (r,) = measure(_file(_budget(NOTES)), h, runs=3)
    assert [s.statements for s in r.sizes] == [[3] * 3, [3] * 3]
    assert r.fit.to_dict() == {"constant": 3, "per_item": 0, "exact": True}
    assert r.verdict == "pass"
    assert [n for n in r.notes if n.startswith("seeded id")] == [
        "seeded id: {item} from seed step 1 (key id)"
    ]
    assert r.to_dict()["endpoint"] == "GET /items/{item}/notes"
    # the same file with an id that does not exist answers 404 (no capture, literal braces)
    (b,) = _file(_budget(NOTES[1:])).budgets
    h2 = Harness(indexapp.make_app, indexapp.build_db, None, indexapp.login, "sqlite")
    with pytest.raises(MeasureError, match="answered (404|422)"):
        measure(parse({"version": 1, "budgets": [_budget(NOTES[1:])]}), h2, runs=1)
    assert b.steps[0].capture == ()
