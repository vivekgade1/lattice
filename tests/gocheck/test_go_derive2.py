"""Spike J, node J2 (round 2), arm 0: the extractor facts E1-E3 and layer A's E6, on the
parse-only fixtures ``goparity2`` / ``goparity2mixed`` (truth in their ``truth.json``) and on
small modules written to ``tmp_path``. Needs ``go``; nothing here builds or runs an app."""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from lattice.gocheck import GoPolicy, build_gocheck, run_gocheck
from lattice.gocheck.derive import policy_extract

pytestmark = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "gocheck" / "fixtures"
P2, MIXED, GINAPP = (
    FIXTURES / "goparity2",
    FIXTURES / "goparity2mixed",
    REPO / "tests/spec/goginapp",
)
NOTES = GoPolicy(tables={"Note": "notes"}, owners={"Note": "owner_id"})


def routes(extract: dict) -> dict[str, dict]:
    return {f"{r['method']} {r['path_template']}": r for r in extract["routes"]}


def module(tmp_path: Path, body: str, imports: str = "") -> Path:
    (tmp_path / "go.mod").write_text("module example.com/t\n\ngo 1.22\n")
    (tmp_path / "main.go").write_text(
        'package main\n\nimport (\n\t"net/http"\n\t"strconv"\n\n\t"github.com/gin-gonic/gin"\n'
        f"\t{imports}\n)\n\nvar _ = strconv.Itoa\nvar _ = http.StatusOK\n\nfunc main() {{}}\n\n"
        + textwrap.dedent(body)
    )
    return tmp_path


def default_extract(root: Path) -> dict:
    proc = subprocess.run([str(build_gocheck()), "extract"], input=json.dumps({"root": str(root)}),
                          capture_output=True, text=True, check=True)  # fmt: skip
    return json.loads(proc.stdout)


# ---- E1: parse -----------------------------------------------------------------------------


def test_e1_only_a_parse_with_a_bound_error_and_a_4xx_refusal_is_marked():
    r = routes(policy_extract(P2))
    assert [p.get("parse") for p in r["GET /a/{id}"]["params"]] == ["Int"]
    assert "parse" not in r["GET /b/{id}"]["params"][0]  # the error is ignored (`_`)
    assert "parse" not in r["GET /c/{id}"]["params"][0]  # never parsed


def test_e1_on_goginapp_marks_three_parameters():
    parsed = [
        (k, p["name"]) for k, r in routes(policy_extract(GINAPP)).items() for p in r["params"]
        if p.get("parse")
    ]  # fmt: skip
    assert parsed == [("POST /configs/{cid}/runs", "cid"), ("GET /runs/{rid}", "rid"),
                      ("POST /runs/{rid}/cancel", "rid")]  # fmt: skip


def test_e1_reads_a_local_string_and_refuses_a_non_refusing_branch(tmp_path):
    root = module(
        tmp_path,
        """
        func ok(c *gin.Context) {
            raw := c.Param("a")
            if _, err := strconv.ParseInt(raw, 10, 64); err != nil {
                c.JSON(http.StatusBadRequest, gin.H{"detail": "bad"})
                return
            }
        }

        func okay200(c *gin.Context) {
            _, err := strconv.Atoi(c.Param("b"))
            if err != nil {
                c.JSON(http.StatusOK, gin.H{})
                return
            }
        }

        func init() {
            r := gin.New()
            r.GET("/x/:a", ok)
            r.GET("/y/:b", okay200)
        }
    """,
    )
    r = routes(policy_extract(root))
    assert r["GET /x/{a}"]["params"][0].get("parse") == "Int"
    assert "parse" not in r["GET /y/{b}"]["params"][0]  # a 200 is not a refusal


def test_e1_and_e3_keys_are_absent_from_the_default_extract():
    for module_dir in (P2, MIXED, GINAPP):
        text = json.dumps(default_extract(module_dir))
        assert '"parse"' not in text and '"bind"' not in text


# ---- E2: omitempty bounds ----------------------------------------------------------------


def test_e2_omitempty_bounds_on_the_fixture():
    r = routes(policy_extract(P2))
    got = {k: (r[k]["params"][0]["ge"], r[k]["params"][0]["le"], len(r[k]["unread"]))
           for k in ("GET /n1", "GET /n2", "GET /n3")}  # fmt: skip
    assert got == {"GET /n1": (0, 10, 0), "GET /n2": (None, 10, 1), "GET /n3": (1, 10, 0)}
    assert "not an interval" in r["GET /n2"]["unread"][0]["reason"]


def test_e2_on_goginapp_limit_and_strings(tmp_path):
    p = routes(policy_extract(GINAPP))["GET /runs"]["params"][0]
    assert (p["ge"], p["le"]) == (0, 50)
    root = module(
        tmp_path,
        """
        type q struct {
            A int    `form:"a" binding:"omitempty,gt=0,max=9"`
            B int    `form:"b" binding:"omitempty,gt=4"`
            C string `form:"c" binding:"omitempty,min=1,max=9"`
            D string `form:"d" binding:"omitempty,min=2"`
            E string `form:"e" binding:"min=1"`
        }

        func list(c *gin.Context) {
            var v q
            _ = c.ShouldBindQuery(&v)
        }

        func init() { gin.New().GET("/q", list) }
    """,
    )
    r = routes(policy_extract(root))["GET /q"]
    got = {p["name"]: (p["ge"], p["min_length"]) for p in r["params"]}
    assert got == {"a": (0, None), "b": (None, None), "c": (None, None), "d": (None, None),
                   "e": (None, 1)}  # fmt: skip


# ---- E3: bind refusals -------------------------------------------------------------------


def test_e3_marks_the_refusal_of_a_failed_bind():
    for module_dir, statuses in ((P2, {422}), (MIXED, {400, 422}), (GINAPP, {422})):
        bound = {x["status"] for r in policy_extract(module_dir)["routes"] for x in r["raises"]
                 if x.get("bind")}  # fmt: skip
        assert bound == statuses
    login = routes(policy_extract(GINAPP))["POST /login"]["raises"]
    assert [(x["status"], bool(x.get("bind"))) for x in login] == [(422, True), (401, False)]


# ---- E6: owner-neutral Gin library middleware --------------------------------------------


def keys(root: Path) -> list[str]:
    return sorted(f.key for f in run_gocheck(root, NOTES, strict=True))


def test_e6_library_middleware_keeps_a_route_readable_and_other_imports_do_not():
    assert keys(P2) == ["GO801|GET /notes|Note"]  # ext.Guard() (unresolvable): silent


def test_e6_holds_in_use_in_a_group_and_in_leading_handlers(tmp_path):
    handlers = "\n".join(
        f"func {n}(c *gin.Context) {{\n\tvar n []Note\n\tdb.Find(&n)\n}}\n"
        for n in ("leading", "grouped", "used", "foreign")
    )
    body = f"""
        type Note struct {{
            ID      uint
            OwnerID uint
        }}

        var db *gorm.DB

        {handlers}
        func init() {{
            r := gin.New()
            r.GET("/leading", gin.Logger(), leading)
            r.Group("/g", gin.Recovery()).GET("/grouped", grouped)
            r.Use(gin.LoggerWithWriter(nil))
            r.GET("/used", used)
            r.GET("/foreign", other.Recovery(), foreign)
        }}
    """
    root = module(tmp_path, body, imports='"gorm.io/gorm"\n\tother "github.com/acme/other"')
    assert keys(root) == [
        "GO801|GET /g/grouped|Note",
        "GO801|GET /leading|Note",
        "GO801|GET /used|Note",
    ]
