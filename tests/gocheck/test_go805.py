"""Spike J, node G3 (round 3), K8: GO805 for Go (``lattice/gocheck/notowned.py``, the opt-in ``raise`` link of
``goextract_owner.go`` / ``goextract_raise.go``, ``GoPolicy.not_owned``). Small modules written to ``tmp_path`` and copies of
the S2/S3 fixtures. Needs ``go``; nothing here builds or runs an app."""

# ruff: noqa: E501
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from lattice import gocheck
from lattice.gocheck import GO_CODES, GO_GATED, GoPolicy, all_go_findings, run_gocheck
from lattice.gocheck.derive import policy_extract
from lattice.gocheck.notowned import (
    derive_not_owned,
    linked_uses,
    not_owned_findings,
    with_not_owned,
)

pytestmark = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")

REPO = Path(__file__).resolve().parents[2]
SPEC_APPS = REPO / "tests" / "spec"
JOBS = GoPolicy(tables={"Job": "jobs"}, owners={"Job": "user_id"})

# four owner-scoped lookups in the four forms the link reads (init, bound then tested, rows affected, chain in the
# condition) and a decoy: a comparison whose result nothing tests, with a validation refusal two lines below it
MAIN = """package main

import (
	"net/http"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

type User struct {
	ID    uint   `gorm:"primaryKey"`
	Email string `json:"email"`
}

type Job struct {
	ID     uint   `gorm:"primaryKey" json:"id"`
	UserID uint   `gorm:"not null;index" json:"user_id"`
	State  string `json:"state"`
	Title  string `json:"title"`
}

type h struct{ db *gorm.DB }

func auth() gin.HandlerFunc {
	return func(c *gin.Context) {
		if c.GetHeader("Authorization") == "" {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"detail": "no"})
			return
		}
		c.Set("uid", uint(1))
		c.Next()
	}
}

func refuse(c *gin.Context, code int, msg string) {
	c.AbortWithStatusJSON(code, gin.H{"detail": msg})
}

// init form: if err := chain; err != nil
func (s *h) getJob(c *gin.Context) {
	var j Job
	if err := s.db.Where("id = ? AND user_id = ?", c.Param("id"), c.GetUint("uid")).First(&j).Error; err != nil {
		refuse(c, http.StatusNotFound, "Not found")
		return
	}
	c.JSON(http.StatusOK, j)
}

// bound form: res := chain, then the next statement tests res.Error
func (s *h) getJobBound(c *gin.Context) {
	var j Job
	res := s.db.Where("id = ? AND user_id = ?", c.Param("id"), c.GetUint("uid")).First(&j)
	if res.Error != nil {
		c.AbortWithStatusJSON(http.StatusNotFound, gin.H{"detail": "Not found"})
		return
	}
	c.JSON(http.StatusOK, j)
}

// rows affected form
func (s *h) cancel(c *gin.Context) {
	res := s.db.Model(&Job{}).Where("id = ? AND user_id = ?", c.Param("id"), c.GetUint("uid")).Update("state", "cancelled")
	if res.RowsAffected == 0 {
		refuse(c, http.StatusNotFound, "Not found")
		return
	}
	c.Status(http.StatusNoContent)
}

// chain inside the condition
func (s *h) getJobInline(c *gin.Context) {
	var j Job
	if s.db.Where("id = ? AND user_id = ?", c.Param("id"), c.GetUint("uid")).First(&j).Error != nil {
		refuse(c, http.StatusNotFound, "Not found")
		return
	}
	c.JSON(http.StatusOK, j)
}

// decoy: the comparison's result is not tested; a validation refusal follows within two lines
func (s *h) decoy(c *gin.Context) {
	var js []Job
	s.db.Where("user_id = ?", c.GetUint("uid")).Find(&js)
	var in struct{ Title string }
	if err := c.ShouldBindJSON(&in); err != nil {
		refuse(c, http.StatusUnprocessableEntity, "invalid")
		return
	}
	c.JSON(http.StatusOK, js)
}

func main() {
	var db *gorm.DB
	s := &h{db: db}
	r := gin.New()
	g := r.Group("/", auth())
	g.GET("/jobs/:id", s.getJob)
	g.GET("/jobs/:id/bound", s.getJobBound)
	g.POST("/jobs/:id/cancel", s.cancel)
	g.GET("/jobs/:id/inline", s.getJobInline)
	g.POST("/jobs/decoy", s.decoy)
	_ = r.Run()
}
"""


def module(tmp_path: Path, text: str = MAIN, name: str = "app") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "go.mod").write_text("module example.com/fx\n\ngo 1.22\n")
    (root / "main.go").write_text(text)
    return root


def answering(text: str, marker: str, status: str) -> str:
    """``text`` with the first ``http.StatusNotFound`` after the comment ``marker`` replaced by ``status``."""
    head, tail = text.split(marker, 1)
    assert "http.StatusNotFound" in tail
    return head + marker + tail.replace("http.StatusNotFound", status, 1)


def refusals(root: Path) -> dict[str, tuple[int, int] | None]:
    """{METHOD path: (status, line) of the linked refusal of its owner comparison, None when not linked}."""
    out: dict[str, tuple[int, int] | None] = {}
    for r in policy_extract(root)["routes"]:
        uses = [u for u in r["owner_uses"] if u["how"] == "compare"]
        if uses:
            link = uses[0].get("raise")
            out[f"{r['method']} {r['path']}"] = (link["status"], link["line"]) if link else None
    return out


def test_every_lookup_form_is_linked_to_its_refusal(tmp_path: Path):
    got = refusals(module(tmp_path))
    assert got == {
        "GET /jobs/:id": (404, 43),
        "GET /jobs/:id/bound": (404, 54),
        "POST /jobs/:id/cancel": (404, 64),
        "GET /jobs/:id/inline": (404, 74),
        "POST /jobs/decoy": None,
    }


def test_a_refusal_near_a_comparison_is_not_linked(tmp_path: Path):
    """The decoy's 422 is two lines below its comparison, but nothing tests the comparison's result: no link, and the
    module's answer stays 404 on exactly the four real lookups (LM16 pairs by line proximity)."""
    root = module(tmp_path)
    got = derive_not_owned(policy_extract(root), JOBS.owners)
    assert (got.status, got.linked) == (404, 4)


def test_disagreeing_raises_are_unclear(tmp_path: Path):
    """One lookup refusing 403 where the others answer 404: no answer, and the note says why (LM17 takes one)."""
    text = answering(MAIN, "// init form", "http.StatusForbidden")
    assert text != MAIN
    got = derive_not_owned(policy_extract(module(tmp_path, text)), JOBS.owners)
    assert (
        got.status is None
        and got.linked == 4
        and "unclear" in got.note
        and "[403, 404]" in got.note
    )


def test_no_linked_refusal_gives_no_answer(tmp_path: Path):
    text = MAIN.split("// init form")[0] + "func main() {}\n"
    got = derive_not_owned(policy_extract(module(tmp_path, text)), JOBS.owners)
    assert (got.status, got.linked) == (None, 0) and "silent" in got.note


def test_only_the_owner_column_of_an_owned_table_counts(tmp_path: Path):
    extract = policy_extract(module(tmp_path))
    assert len(list(linked_uses(extract))) == 4
    assert list(linked_uses(extract, {"Job": "title"})) == []
    assert list(linked_uses(extract, {})) == []


def test_the_default_extract_has_no_link(tmp_path: Path):
    """K8 is opt-in: without ``policy`` no owner use carries a ``raise`` key (byte-compat of the default extract)."""
    root = module(tmp_path)
    out = subprocess.run([str(gocheck.build_gocheck()), "extract"], input=json.dumps({"root": str(root)}),
                         capture_output=True, text=True, check=True).stdout  # fmt: skip
    assert '"raise"' not in out and '"owner_uses":[{"table":"jobs"' in out


def test_a_route_that_answers_another_status_is_a_finding(tmp_path: Path):
    """The checked module's inline lookup answers 403 where the original answers 404: GO805 on that route only."""
    original = module(tmp_path, name="original")
    text = answering(MAIN, "// chain inside the condition", "http.StatusForbidden")
    assert text != MAIN
    copy = module(tmp_path, text, name="copy")
    policy = with_not_owned(GoPolicy(**{**JOBS.__dict__, "rules": ("GO805",)}), original)
    assert policy.not_owned == 404
    assert run_gocheck(original, policy) == []
    (f,) = run_gocheck(copy, policy)
    assert (f.code, f.file, f.line, f.key) == (
        "GO805",
        "main.go",
        74,
        "GO805|GET /jobs/:id/inline|not_owned",
    )
    assert "403" in f.message and "404" in f.message and f.code in GO_CODES


def test_go805_is_silent_without_an_answer_or_a_selection(tmp_path: Path):
    root = module(tmp_path)
    assert not_owned_findings(root, JOBS) == []  # no answer
    asked = GoPolicy(**{**JOBS.__dict__, "not_owned": 422})  # every lookup answers 404, not 422
    assert {f.code for f in all_go_findings(root, asked)} == {"GO805"}
    only = GoPolicy(**{**asked.__dict__, "rules": ("GO801",)})
    assert "GO805" not in {f.code for f in all_go_findings(root, only)}
    unowned = GoPolicy(**{**asked.__dict__, "owners": {}})
    assert not_owned_findings(root, unowned) == []


def test_the_baseline_accepts_a_go805_key(tmp_path: Path):
    root = module(tmp_path)
    policy = GoPolicy(**{**JOBS.__dict__, "not_owned": 422, "rules": ("GO805",)})
    found = run_gocheck(root, policy)
    assert len(found) == 4 and len({f.key for f in found}) == 4
    accepted = GoPolicy(**{**policy.__dict__, "baseline": frozenset(f.key for f in found[:3])})
    assert run_gocheck(root, accepted) == found[3:]


def test_the_answer_of_s2_and_s3_is_404_and_their_originals_have_no_go805():
    for app, owners in (("goginapp", {"Run": "user_id"}), ("gotickets", {"Ticket": "reporter_id"})):
        got = derive_not_owned(policy_extract(SPEC_APPS / app), owners)
        assert got.status == 404 and got.linked >= 2, (app, got)
        policy = with_not_owned(GoPolicy(owners=owners, rules=("GO805",)), SPEC_APPS / app)
        assert run_gocheck(SPEC_APPS / app, policy) == []


def test_s3_copy_whose_get_answers_403_is_a_finding(tmp_path: Path):
    owners = {"Ticket": "reporter_id"}
    policy = with_not_owned(GoPolicy(owners=owners, rules=("GO805",)), SPEC_APPS / "gotickets")
    copy = tmp_path / "gotickets"
    shutil.copytree(SPEC_APPS / "gotickets", copy)
    api = copy / "api" / "api.go"
    text = api.read_text()
    old = 'First(&t).Error; err != nil {\n\t\t\tdetail(c, http.StatusNotFound, "Not found")\n\t\t\treturn\n\t\t}\n\t\tc.JSON(http.StatusOK, t)'
    assert text.count(old) == 1
    api.write_text(text.replace(old, old.replace("StatusNotFound", "StatusForbidden")))
    (f,) = run_gocheck(copy, policy)
    assert (f.code, f.key) == ("GO805", "GO805|GET /tickets/:tid|not_owned")


def test_go805_is_carried_over_not_gated():
    assert "GO805" in GO_CODES and set(GO_GATED) == {"GO804", "GO806"}
    note = gocheck.gated_note()
    assert "GO805" not in note and "GO804 -- route order (PY804) is moot on Gin" in note
    assert "GO806 -- naive/aware datetimes (PY806) cannot be mixed in Go" in note
