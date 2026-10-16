"""``gocheck extract`` with ``{"workflow": true}`` (the workflow layer's facts) on the fixture
app ``fixtures/goextract_workflow`` and on small modules written per test: request values
that reach columns (``param_uses``), creates and where their keys come from, the constants a
field is compared to, and create/update with the states a write is made from. The default
output is the opt-in's invariant: the flag adds keys and changes nothing else. Skipped when
``go`` is not on PATH."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest

from lattice.gocheck import build_gocheck

pytestmark = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")

FIXTURE = Path(__file__).parent / "fixtures" / "goextract_workflow"
PLAIN = Path(__file__).parent / "fixtures" / "goextract"
WORKFLOW_KEYS = (
    "param_uses",
    "creates",
    "param_literals",
    "status_facts",
    "workflow_unread",
    "workflow_notes",
)


GO_MOD = (
    "module example.com/t\n\ngo 1.22\n\nrequire (\n"
    "\tgithub.com/gin-gonic/gin v1.9.1\n\tgorm.io/gorm v1.25.5\n)\n"
)


def binary() -> str:
    """The checker binary (GOEXTRACT_BIN: a mutant build, for dogfood/workflow_mutants.py)."""
    return os.environ.get("GOEXTRACT_BIN") or str(build_gocheck())


def extract_raw(root: Path, workflow: bool) -> str:
    payload: dict[str, Any] = {"root": str(root)}
    if workflow:
        payload["workflow"] = True
    proc = subprocess.run(
        [binary(), "extract"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def extract(root: Path, workflow: bool = True) -> dict[str, Any]:
    return json.loads(extract_raw(root, workflow))  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def fx() -> dict[str, Any]:
    return extract(FIXTURE)


def write_module(tmp_path: Path, files: dict[str, str]) -> Path:
    (tmp_path / "go.mod").write_text("module example.com/t\n\ngo 1.22\n")
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text))
    return tmp_path


def uses(
    x: dict[str, Any], route: str, how: str | None = None
) -> set[tuple[str, str, str, str, str]]:
    return {
        (u["source"], u["param"], u["table"], u["column"], u["how"])
        for u in x["param_uses"]
        if u["route"] == route and (how is None or u["how"] == how)
    }


# ---- the opt-in changes nothing else ------------------------------------------------------


def test_the_flag_adds_keys_and_changes_nothing_else():
    for root in (FIXTURE, PLAIN):
        plain = json.loads(extract_raw(root, False))
        flagged = json.loads(extract_raw(root, True))
        assert all(k in flagged for k in WORKFLOW_KEYS)
        assert not any(k in plain for k in WORKFLOW_KEYS)
        stripped = {k: v for k, v in flagged.items() if k not in WORKFLOW_KEYS}
        assert json.dumps(stripped) == json.dumps(plain)


def test_the_default_output_has_no_workflow_key():
    plain = extract_raw(FIXTURE, False)
    assert not any(f'"{k}"' in plain for k in WORKFLOW_KEYS)


def test_the_output_is_deterministic():
    runs = {extract_raw(FIXTURE, True) for _ in range(3)}
    assert len(runs) == 1
    blob = next(iter(runs))
    assert "/Users" not in blob and "/private" not in blob and str(FIXTURE) not in blob


# ---- param uses ---------------------------------------------------------------------------


def test_a_path_id_is_followed_into_a_helper_and_compared_to_the_key(fx):
    got = uses(fx, "GET /api/projects/{id}", "compare")
    assert ("path", "id", "projects", "id", "compare") in got


def test_a_path_id_parsed_and_read_as_gorms_primary_key(fx):
    assert uses(fx, "DELETE /api/tickets/{id}") == {("path", "id", "tickets", "id", "compare")}


def test_a_condition_with_several_placeholders_binds_the_right_one(fx):
    # `id = ? AND owner_id = ?`: only the first placeholder holds the path value
    got = uses(fx, "POST /api/jobs/{id}/activate")
    assert got == {("path", "id", "jobs", "id", "compare")}


def test_a_body_value_reaches_a_column_as_an_assignment_and_as_a_comparison(fx):
    got = uses(fx, "POST /api/tickets")
    assert ("body", "project_id", "tickets", "project_id", "assign") in got
    assert ("body", "project_id", "projects", "id", "compare") in got


def test_a_bound_struct_field_carries_its_body_key(fx):
    got = uses(fx, "POST /api/projects", "assign")
    assert ("body", "id", "projects", "id", "assign") in got
    assert ("body", "name", "projects", "name", "assign") in got


def test_every_use_is_located(fx):
    for u in fx["param_uses"]:
        assert u["file"].endswith(".go") and u["line"] > 0 and not u["file"].startswith("/")


# ---- creates ------------------------------------------------------------------------------


def test_a_client_chosen_key_comes_from_a_body_field_and_a_server_key_does_not(fx):
    got = {(c["route"], c["table"], c["key"], c["key_from"]) for c in fx["creates"]}
    assert ("POST /api/projects", "projects", "id", "id") in got
    assert ("POST /api/tickets", "tickets", "id", "server") in got
    assert ("POST /api/jobs", "jobs", "id", "server") in got
    assert ("POST /api/jobs/{id}/runs", "runs", "id", "server") in got


# ---- literals -----------------------------------------------------------------------------


def test_the_constants_a_field_is_compared_to_are_its_literals(fx):
    lits = {(x["route"], x["source"], x["param"]): x["values"] for x in fx["param_literals"]}
    assert lits[("POST /api/projects", "body", "kind")] == ["public", "private"]


def test_a_switch_and_a_map_lookup_give_literals(tmp_path):
    root = write_module(
        tmp_path,
        {
            "go.mod": GO_MOD,
            "main.go": """
                package main

                import (
                    "github.com/gin-gonic/gin"
                    "gorm.io/gorm"
                )

                var allowed = map[string]bool{"red": true, "blue": true}

                type req struct {
                    Mode  string `json:"mode"`
                    Color string `json:"color"`
                }

                func main() {
                    var db *gorm.DB
                    r := gin.New()
                    r.POST("/x", func(c *gin.Context) {
                        var in req
                        if err := c.ShouldBindJSON(&in); err != nil {
                            c.Status(400)
                            return
                        }
                        switch in.Mode {
                        case "fast", "slow":
                        default:
                            c.Status(422)
                            return
                        }
                        if !allowed[in.Color] {
                            c.Status(422)
                            return
                        }
                        _ = db
                        c.Status(200)
                    })
                    _ = r.Run()
                }
            """,
        },
    )
    x = extract(root)
    lits = {(x_["source"], x_["param"]): x_["values"] for x_ in x["param_literals"]}
    assert lits[("body", "mode")] == ["fast", "slow"]
    assert sorted(lits[("body", "color")]) == ["blue", "red"]


# ---- status facts -------------------------------------------------------------------------


def facts(x: dict[str, Any], model: str, column: str) -> list[dict[str, Any]]:
    return [f for f in x["status_facts"] if f["model"] == model and f["column"] == column]


def test_a_create_site_and_the_update_sites_with_their_from_states(fx):
    got = {(f["kind"], tuple(f["from"])) for f in facts(fx, "Project", "status")}
    assert ("create", ()) in got  # the literal `Status: "draft"`
    assert ("update", ("draft",)) in got  # inside `if p.Status == "draft"`
    assert ("update", ("draft", "published")) in got  # `case "draft", "published":`


def test_an_update_chain_pinning_the_column_gives_its_from_state(fx):
    (f,) = [f for f in facts(fx, "Job", "status") if f["kind"] == "update"]
    assert f["from"] == ["queued"]
    assert f["from_at"] and f["from_at"][0]["line"] > 0


def test_an_unread_from_state_is_listed_not_guessed(tmp_path):
    root = write_module(
        tmp_path,
        {
            "go.mod": GO_MOD,
            "main.go": """
                package main

                import (
                    "github.com/gin-gonic/gin"
                    "gorm.io/gorm"
                )

                type Job struct {
                    ID     uint64 `gorm:"primaryKey"`
                    Status string
                }

                func main() {
                    var db *gorm.DB
                    r := gin.New()
                    r.POST("/a", func(c *gin.Context) {
                        db.Create(&Job{Status: "queued"})
                    })
                    r.POST("/b/:id", func(c *gin.Context) {
                        db.Model(&Job{}).Where("id = ?", c.Param("id")).Update("status", "done")
                    })
                    _ = r.Run()
                }
            """,
        },
    )
    x = extract(root)
    (f,) = [f for f in x["status_facts"] if f["kind"] == "update"]
    assert f["from"] == ["*"]
    assert any(u["what"] == "status_write" for u in x["workflow_unread"])


# ---- the rest -----------------------------------------------------------------------------


def test_a_conditional_route_is_kept_with_its_condition(fx):
    (r,) = [r for r in fx["routes"] if r["path_template"] == "/test/clock"]
    assert r["condition"] and "TEST_CLOCK" in r["condition"]


def test_a_nested_body_field_is_a_parameter_the_extractor_does_not_require(fx):
    (r,) = [r for r in fx["routes"] if r["path_template"] == "/api/batch"]
    items = [p for p in r["params"] if p["name"] == "items"]
    assert items and not items[0]["required"]


def test_a_failed_precondition_is_a_raise_of_the_route(fx):
    (r,) = [r for r in fx["routes"] if r["path_template"] == "/api/jobs/{id}/runs"]
    assert 409 in {x["status"] for x in r["raises"]}
