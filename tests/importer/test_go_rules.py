"""The rules-first import for Go (Spike J, node J1): ``lattice.importer.go_rules`` (D1, D2)
and its wiring in ``rules.import_rules``. The owner inference is unit-tested on hand-made
extracts; the integration tests read the committed Gin fixtures and need ``go`` (the full
import of ``tests/spec/goginapp`` also builds that module: skipped when it cannot be built)."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from lattice.importer import go_rules, rules
from lattice.importer.frontends.go_gin import GoGinFrontEnd, model_from_extract

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "gocheck" / "fixtures"
SPEC_DIR = REPO / "tests" / "spec"

needs_go = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")


# ---- hand-made extracts -------------------------------------------------------------------------


def col(name: str, typ: str | None = "Int", *, pk: bool = False, fk: str | None = None) -> dict:
    return {
        "name": name, "field": name.title(), "go_type": "uint" if typ == "Int" else "string",
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


def extract(tables: list[dict], uses: list[dict], users: str | None = "User") -> dict:
    route = {
        "method": "GET", "path_template": "/x", "handler": None, "params": [], "unread": [],
        "status_code": None, "auth": "Customer", "auth_reason": "", "raises": [], "calls": [],
        "tables": [], "owner_uses": uses, "body_models": [], "line": 1, "guards": [],
    }  # fmt: skip
    auth = None
    if users:
        auth = {"login_path": None, "login_encoding": "json", "username_field": None,
                "password_field": None, "token_field": None, "scheme": "bearer",
                "users_table": users, "password_column": None}  # fmt: skip
    return {"schema": "lattice.goextract/1", "module": "x", "dialect": "", "tables": tables,
            "models": [], "routes": [route], "excluded": [], "auth": auth}  # fmt: skip


USERS = table("User", "users", col("id", pk=True), col("name", "Str"))


def infer(tables: list[dict], uses: list[dict]) -> tuple[Any, list[dict]]:
    data = extract([USERS, *tables], uses)
    return go_rules.infer_owners(data, model_from_extract(data))


def fk_of(model: Any, table_name: str, column: str) -> str | None:
    t = next(t for t in model.tables if t.name == table_name)
    return next(c.foreign_key for c in t.columns if c.name == column)


def test_a_compared_and_assigned_column_is_the_owner_with_its_evidence():
    run = table("Run", "runs", col("id", pk=True), col("user_id"), col("status", "Str"))
    uses = [use("Run", "user_id", "compare", 30), use("Run", "user_id", "assign", 10),
            use("Run", "user_id", "compare", 20, "other.go")]  # fmt: skip
    model, inferences = infer([run], uses)
    assert fk_of(model, "Run", "user_id") == "users.id"
    assert inferences == [{"table": "Run", "column": "user_id", "rule": "owner_uses",
                           "evidence": ["code:api.go:10", "code:api.go:30", "code:other.go:20"]}]  # fmt: skip  # noqa: E501


def test_assign_only_owner():
    """Rule 1 is compare *or* assign: Audit.actor is only ever set from the caller."""
    audit = table("Audit", "audits", col("id", pk=True), col("actor"), col("action", "Str"))
    model, inferences = infer([audit], [use("Audit", "actor", "assign", 7)])
    assert fk_of(model, "Audit", "actor") == "users.id"
    assert [i["column"] for i in inferences] == ["actor"]


def test_compare_only_owner():
    item = table("Item", "items", col("id", pk=True), col("owner_id"))
    model, inferences = infer([item], [use("Item", "owner_id", "compare", 5)])
    assert fk_of(model, "Item", "owner_id") == "users.id"
    assert inferences[0]["evidence"] == ["code:api.go:5"]


def test_two_candidates_unclear():
    """Rule 4: two columns meeting 1 and 2 make the table unclear, both named."""
    msg = table("Msg", "msgs", col("id", pk=True), col("sender_id"), col("receiver_id"))
    uses = [use("Msg", "sender_id", "assign", 3), use("Msg", "receiver_id", "compare", 4)]
    model, inferences = infer([msg], uses)
    (one,) = inferences
    assert one["unclear"] is True and one["column"] is None
    assert one["columns"] == ["sender_id", "receiver_id"]
    assert one["evidence"] == ["code:api.go:3", "code:api.go:4"]
    # both carry the key, so the profile generator reports two users foreign keys: unclear
    assert fk_of(model, "Msg", "sender_id") == fk_of(model, "Msg", "receiver_id") == "users.id"


def test_a_table_with_no_evidence_keeps_todays_answer():
    cfg = table("Config", "configs", col("id", pk=True), col("owner_id"))
    model, inferences = infer([cfg], [use("Run", "user_id", "compare", 1)])
    assert inferences == [] and fk_of(model, "Config", "owner_id") is None


def test_the_users_table_is_never_owned():
    data = extract([USERS], [use("User", "id", "compare", 3)])
    model, inferences = go_rules.infer_owners(data, model_from_extract(data))
    assert inferences == [] and fk_of(model, "User", "id") is None


def test_a_non_integer_column_is_not_an_owner():
    note = table("Note", "notes", col("id", pk=True), col("author", "Str"))
    model, inferences = infer([note], [use("Note", "author", "compare", 2)])
    assert inferences == [] and fk_of(model, "Note", "author") is None


def test_a_declared_users_foreign_key_wins():
    note = table("Note", "notes", col("id", pk=True), col("owner_id", fk="users.id"),
                 col("editor_id"))  # fmt: skip
    uses = [use("Note", "editor_id", "compare", 2)]
    model, inferences = infer([note], uses)
    assert inferences == [] and fk_of(model, "Note", "editor_id") is None


def test_without_a_users_table_nothing_is_inferred():
    run = table("Run", "runs", col("id", pk=True), col("user_id"))
    data = extract([run], [use("Run", "user_id", "compare", 2)], users=None)
    model, inferences = go_rules.infer_owners(data, model_from_extract(data))
    assert inferences == [] and fk_of(model, "Run", "user_id") is None


def test_the_input_model_is_not_changed():
    run = table("Run", "runs", col("id", pk=True), col("user_id"))
    data = extract([USERS, run], [use("Run", "user_id", "compare", 2)])
    before = model_from_extract(data)
    after, _ = go_rules.infer_owners(data, before)
    assert fk_of(before, "Run", "user_id") is None and after is not before
    assert json.dumps(data, sort_keys=True) == json.dumps(
        extract([USERS, run], [use("Run", "user_id", "compare", 2)]), sort_keys=True
    )


# ---- the fixtures -------------------------------------------------------------------------------


@needs_go
def test_the_goextract_fixture_gives_item_and_audit_their_owners():
    fe = GoGinFrontEnd()
    model = fe.extract(FIXTURES, "goextract")
    assert fe.last_extract is not None
    model, inferences = go_rules.infer_owners(fe.last_extract, model)
    owners = {i["table"]: i["column"] for i in inferences}
    assert owners == {"Item": "owner_id", "Audit": "actor"}
    item = next(i for i in inferences if i["table"] == "Item")
    assert len(item["evidence"]) == 4  # three compares, one assign


@needs_go
def test_the_inferring_front_end_infers_on_extract_and_delegates_the_rest():
    fe = go_rules.InferringFrontEnd(GoGinFrontEnd())
    model = fe.extract(FIXTURES, "goextract")
    assert fe.name == "gin" and fe.inferences
    assert fk_of(model, "Item", "owner_id") == "users.id"
    assert fe.sidecar().startswith("{")  # the extract is the wrapped front end's, untouched
    assert getattr(fe, "excluded", None) is None  # a missing optional method stays missing


# ---- D1: the factory ----------------------------------------------------------------------------


def test_harness_factory_reads_the_studio_toml_of_the_module(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO))
    found = go_rules.harness_factory(SPEC_DIR, "goginapp")
    assert found is not None
    factory, name = found
    assert name == "tests.spec.gogin_harness:make_app" and callable(factory)


def test_harness_factory_is_none_without_a_studio_toml(tmp_path):
    (tmp_path / "app").mkdir()
    assert go_rules.harness_factory(tmp_path, "app") is None
    (tmp_path / "app" / "studio.toml").write_text('[llm]\nmodel = "x"\n')
    assert go_rules.harness_factory(tmp_path, "app") is None


def test_the_python_path_never_asks_for_the_harness_factory(monkeypatch):
    """JM11: only the Go front end (framework gin) reads a harness. A FastAPI package goes
    through ``find_factory`` as before, whatever ``studio.toml`` says."""
    called: list[Any] = []
    monkeypatch.setattr(go_rules, "harness_factory", lambda *a: called.append(a))
    with pytest.raises(LookupError):
        rules.import_rules(FIXTURES, "json", framework="fastapi")  # no make_app in `json`
    assert called == []


def test_inferences_reach_the_report_only_when_there_are_some():
    empty = rules.RulesImport.__dataclass_fields__["inferences"].default_factory()
    assert empty == []
    ri = rules.RulesImport.__new__(rules.RulesImport)
    fields = {k: None for k in rules.RulesImport.__dataclass_fields__}
    ri.__dict__.update(fields)
    ri.rules = _Rules()
    ri.corrections, ri.excluded_routes, ri.baseline, ri.variant2 = [], [], [], {}
    ri.timing, ri.notes, ri.outside, ri.mining, ri.framework = {}, [], {}, None, "x"
    ri.inferences = []
    assert "inferences" not in ri.rules_dict()
    ri.inferences = [{"table": "Run"}]
    assert ri.rules_dict()["inferences"] == [{"table": "Run"}]


class _Rules:
    tables: dict = {}

    def to_dict(self) -> dict:
        return {"items": {}}


# ---- the whole import --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def goginapp_import(tmp_path_factory):
    """The rules-first import of tests/spec/goginapp (builds the module once)."""
    if shutil.which("go") is None:
        pytest.skip("go is not installed")
    from tests.spec import gogin_harness

    try:
        binary = gogin_harness.binary()
    except RuntimeError as err:
        pytest.skip(f"cannot build tests/spec/goginapp (module download?): {err}"[:300])
    out = tmp_path_factory.mktemp("goginapp-import")
    mp = pytest.MonkeyPatch()
    mp.setenv(gogin_harness.BINARY_ENV, str(binary))
    mp.syspath_prepend(str(REPO))
    sys.modules.pop("tests.spec.gogin_harness", None)
    try:
        result = rules.import_rules(SPEC_DIR, "goginapp", framework="gin", mine_proposals=False)
        rules.write_rules(result, out, package="goginapp")
    finally:
        mp.undo()
    return result, out


def test_the_import_of_goginapp_reads_routes_tables_and_the_owner(goginapp_import):
    result, out = goginapp_import
    assert result.framework == "gin"
    assert result.app_factory == "tests.spec.gogin_harness:make_app"
    assert len(result.spec.contracts) == 7
    assert [i["column"] for i in result.inferences] == ["user_id"]
    report = json.loads((out / "report.json").read_text())
    assert report["rules"]["inferences"] == result.inferences
    policy = json.loads((out / "go_policy.json").read_text())
    assert policy["policy"]["owners"] == {"Run": "user_id"}
    assert policy["policy"]["private"] == {"User": ["password", "token"]}
    assert policy["policy"]["machines"] == {}
    assert policy["dropped_machines"] == [
        {"table": "Run", "column": "status", "values": ["queued", "cancelled"]}
    ]


def test_the_import_writes_nothing_into_the_module(goginapp_import):
    _, out = goginapp_import
    assert not list((SPEC_DIR / "goginapp").glob("candidate_spec.py"))
    assert (out / "candidate_spec.py").is_file() and (out / "proposals.json").is_file()
