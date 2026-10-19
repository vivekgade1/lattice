"""The rules-first import for Go, round 3 (Spike J, node G3): K3 (request-body models), K4 (an app
on a server database is not probed) and K5 (``choices`` need two values) of
``lattice.importer.go_rules`` / ``rules`` on the parse-only fixture ``goparity3`` (truth in its
``truth.json``), the committed Gin fixtures and small modules written to ``tmp_path``. Needs ``go``
for the extractor; nothing here builds or runs an app."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

from lattice.importer import go_rules, rules
from lattice.importer.emit_spec import build_spec
from lattice.importer.frontends.go_gin import GoGinFrontEnd
from lattice.importer.lift import load_profile
from lattice.importer.profile_gen import generate_profile

pytestmark = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")

REPO = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO / "tests" / "spec"
GP3 = REPO / "tests" / "gocheck" / "fixtures" / "goparity3"


def front(root: Path, package: str = ".") -> go_rules.InferringFrontEnd:
    fe = go_rules.InferringFrontEnd(GoGinFrontEnd())
    fe.model = fe.extract(root, package)  # type: ignore[attr-defined]
    return fe


def contract_params(fe: go_rules.InferringFrontEnd, path: str) -> list:
    profile = load_profile(
        generate_profile(fe.model, None),  # type: ignore[attr-defined]
        f"lattice_import_profile_{uuid.uuid4().hex}",
    ).profile()
    spec = build_spec(fe.model, profile, users={}, programs={}, proposals=(), public={})  # type: ignore[attr-defined]
    return [c for c in spec.contracts if c.path == path][0].params


# ---- K3 ----------------------------------------------------------------------------------------


def test_k3_a_named_body_struct_becomes_the_routes_body_model():
    fe = front(GP3)
    (inf,) = [i for i in fe.inferences if i["rule"] == "body_model"]
    assert (inf["route"], inf["model"], inf["fields"]) == ("POST /widgets", "widgetBody", ["title"])
    assert inf["evidence"] and inf["evidence"][0].startswith("code:main.go:")
    (route,) = [r for r in fe.model.routes if r.path == "/widgets"]  # type: ignore[attr-defined]
    assert [(p.name, p.source, p.type) for p in route.params] == [
        ("widgetBody", "body", "widgetBody")
    ]


def test_k3_the_contract_carries_the_body_field_with_its_bounds():
    (p,) = contract_params(front(GP3), "/widgets")
    assert (p.name, p.source, p.required, p.max_len) == ("title", "body", True, 40)


def test_k3_routes_without_a_body_are_untouched():
    fe = front(GP3)
    others = [r for r in fe.model.routes if r.path != "/widgets"]  # type: ignore[attr-defined]
    assert all(p.type != "widgetBody" for r in others for p in r.params)
    assert [i["route"] for i in fe.inferences if i["rule"] == "body_model"] == ["POST /widgets"]


def test_k3_goginapp_and_gotickets_get_one_inference_per_bound_body():
    s2 = front(SPEC_DIR, "goginapp")
    assert [(i["route"], i["model"]) for i in s2.inferences if i["rule"] == "body_model"] == [
        ("POST /login", "loginReq")
    ]
    s3 = front(SPEC_DIR, "gotickets")
    got = {
        i["route"]: (i["model"], i["fields"]) for i in s3.inferences if i["rule"] == "body_model"
    }
    assert got == {
        "POST /login": ("loginReq", ["username", "password"]),
        "POST /queues/{qid}/tickets": ("ticketBody", ["title"]),
        "POST /tickets/{tid}/handoffs": ("handoffBody", ["to_user_id"]),
    }
    titles = [p for p in contract_params(s3, "/queues/{qid}/tickets") if p.source == "body"]
    assert [(p.name, p.required, p.max_len) for p in titles] == [("title", True, 80)]


def test_k3_an_anonymous_body_gets_a_synthesised_model(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/t\n\ngo 1.22\n")
    (tmp_path / "main.go").write_text(
        'package main\n\nimport (\n\t"net/http"\n\n\t"github.com/gin-gonic/gin"\n)\n\n'
        'func main() {\n\tr := gin.New()\n\tr.POST("/notes", func(c *gin.Context) {\n'
        '\t\tvar in struct {\n\t\t\tText string `json:"text" binding:"required,max=20"`\n\t\t}\n'
        "\t\tif err := c.ShouldBindJSON(&in); err != nil {\n"
        '\t\t\tc.JSON(http.StatusUnprocessableEntity, gin.H{"d": 1})\n\t\t\treturn\n\t\t}\n'
        '\t\tc.JSON(http.StatusCreated, gin.H{"text": in.Text})\n\t})\n\t_ = r\n}\n'
    )
    fe = front(tmp_path)
    (inf,) = [i for i in fe.inferences if i["rule"] == "body_model"]
    assert inf["route"] == "POST /notes" and inf["fields"] == ["text"]
    assert inf["model"] == "POST_notesBody"
    assert any(m.name == "POST_notesBody" for m in fe.model.models)  # type: ignore[attr-defined]


# ---- K5 ----------------------------------------------------------------------------------------


def choices(fe: go_rules.InferringFrontEnd) -> dict[str, list]:
    return {
        f"{t.name}.{c.name}": list(c.choices)
        for t in fe.model.tables  # type: ignore[attr-defined]
        for c in t.columns
        if c.choices
    }


def test_k5_one_constant_is_not_a_state_set_and_two_are():
    fe = front(GP3)
    assert choices(fe) == {"Gadget.kind": ["a", "b"]}
    facts = {(i["table"], i["column"]) for i in fe.inferences if i["rule"] == "status_facts"}
    assert facts == {("Gadget", "kind")}  # no status_facts inference for the one-value column


def test_k5_keeps_the_states_of_goginapp_and_gotickets():
    assert choices(front(SPEC_DIR, "goginapp")) == {"Run.status": ["queued", "cancelled"]}
    assert choices(front(SPEC_DIR, "gotickets")) == {"Ticket.status": ["open", "closed"]}


# ---- K4 ----------------------------------------------------------------------------------------


def toml_module(tmp_path: Path, database: str | None) -> Path:
    shutil.copytree(GP3, tmp_path / "app", ignore=shutil.ignore_patterns("truth.json"))
    (tmp_path / "app" / "harness.py").write_text(
        "def make_app(db_path):\n    raise RuntimeError('the importer must not start the app')\n"
    )
    db = f'database = "{database}"\n' if database else ""
    (tmp_path / "app" / "studio.toml").write_text('[app]\napp = "harness:make_app"\n' + db)
    return tmp_path


@pytest.mark.parametrize("database", ["mysql", "postgres", "postgresql", "clickhouse", "MySQL"])
def test_k4_a_server_database_is_named(tmp_path, database):
    root = toml_module(tmp_path, database)
    assert go_rules.server_dialect(root, "app") in {"mysql", "postgres", "clickhouse"}


@pytest.mark.parametrize("database", ["sqlite", "", None, "no-such-engine"])
def test_k4_sqlite_or_nothing_is_not_a_server_database(tmp_path, database):
    root = toml_module(tmp_path, database)
    assert go_rules.server_dialect(root, "app") is None


def test_k4_the_import_of_a_server_app_skips_layer_r_and_says_why(tmp_path):
    root = toml_module(tmp_path, "mysql")
    imported = rules.import_rules(root, "app", framework="gin")  # the harness would raise if run
    assert imported.not_run == "not_run:server_dialect" and imported.dialect == "mysql"
    data = imported.rules_dict()
    assert data["status"] == "not_run:server_dialect" and data["dialect"] == "mysql"
    assert data["items"] == {} and data["corrections"] == []
    out = rules.write_rules(imported, tmp_path / "out", package="app")
    assert (out / "report.md").read_text().splitlines()[0].startswith("layer R was not run")
    assert json.loads((out / "proposals.json").read_text()) == []
    report = json.loads((out / "report.json").read_text())
    assert report["rules"]["status"] == "not_run:server_dialect"
    assert (out / "candidate_spec.py").is_file() and (out / "go_policy.json").is_file()
    assert not list(tmp_path.rglob("*.db")) and not list(tmp_path.rglob("*.sqlite*"))
    assert len(imported.spec.contracts) == len(imported.result.model.routes)
    assert "not run" in rules.summary_line(out, imported)


def test_k4_a_sqlite_studio_toml_does_not_skip(tmp_path):
    """The skip is the server dialect's: a SQLite app reaches the Seeder (and the harness)."""
    root = toml_module(tmp_path, None)
    with pytest.raises(RuntimeError, match="must not start the app"):
        rules.import_rules(root, "app", framework="gin")
