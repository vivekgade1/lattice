"""``gocheck extract`` (the Go importer front end's extractor) on fixture apps: the idioms
of ``fixtures/goextract`` (handler factory, generic wrapper, bound structs, middleware
groups, conditional registration, an unreadable route table, a build-tagged file, an
unwired Register) and small modules written per test. Skipped when ``go`` is not on PATH."""

from __future__ import annotations

import hashlib
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

FIXTURE = Path(__file__).parent / "fixtures" / "goextract"


def binary() -> str:
    """The checker binary (GOEXTRACT_BIN: a mutant build, for dogfood/importer_go_mutants.py)."""
    return os.environ.get("GOEXTRACT_BIN") or str(build_gocheck())


def extract(root: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [binary(), "extract"],
        input=json.dumps({"root": str(root)}),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)  # type: ignore[no-any-return]


def route(x: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    (r,) = [r for r in x["routes"] if r["method"] == method and r["path_template"] == path]
    return r  # type: ignore[no-any-return]


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


# ---- the fixture app --------------------------------------------------------------------


def test_routes_are_the_registrations_with_their_full_paths(fx):
    got = sorted((r["method"], r["path_template"], r["auth"]) for r in fx["routes"])
    assert got == [
        ("DELETE", "/api/items/{id}", "Customer"),
        ("GET", "/api/admin/audit", "Admin"),
        ("GET", "/api/items", "Customer"),
        ("GET", "/api/items/{id}", "Customer"),
        ("GET", "/api/stats", "Customer"),
        ("GET", "/api/status", "Public"),
        ("GET", "/debug/ping", "Public"),
        ("POST", "/api/items", "Customer"),
        ("POST", "/api/login", "Public"),
    ]
    assert all(r["path_exact"] for r in fx["routes"])


def test_a_factory_literal_and_a_generic_wrapper_are_resolved(fx):
    login = route(fx, "POST", "/api/login")["handler"]
    assert (login["func"], login["wrappers"]) == ("api.Login.func1", [])
    stats = route(fx, "GET", "/api/stats")["handler"]
    assert (stats["func"], stats["wrappers"]) == ("api.stats.func1", ["api.Handle"])
    plain = route(fx, "GET", "/api/status")["handler"]
    assert (plain["func"], plain["file"]) == ("api.Status", "api/api.go")


def test_unwired_register_is_excluded_not_extracted(fx):
    assert not [r for r in fx["routes"] if r["path_template"] == "/orphan"]
    assert any(
        e["kind"] == "unwired"
        and e["path"] == "/orphan"
        and e["reason"] == "not reachable from main"
        for e in fx["excluded"]
    )


def test_a_file_a_build_constraint_drops_is_excluded(fx):
    assert not [r for r in fx["routes"] if r["path_template"] == "/ignored"]
    assert any(e["kind"] == "file" and e["file"] == "api/debug_routes.go" for e in fx["excluded"])


def test_a_route_table_built_in_a_loop_is_unread_never_guessed(fx):
    assert not [r for r in fx["routes"] if r["path_template"] in ("/api/a", "/api/b")]
    assert any(
        u["what"] == "route" and "not a constant" in u["reason"] and u["file"] == "api/api.go"
        for u in fx["unread"]
    )


def test_a_conditional_registration_keeps_its_condition(fx):
    r = route(fx, "GET", "/debug/ping")
    assert r["condition"] == 'os.Getenv("DEBUG") != ""'
    assert route(fx, "GET", "/api/status")["condition"] is None


def test_the_no_route_handler_is_a_framework_exclusion(fx):
    assert any(e["kind"] == "framework" and "NoRoute" in e["reason"] for e in fx["excluded"])


def test_a_bound_struct_gives_fields_with_bounds_and_choices(fx):
    params = {
        p["name"]: p for p in route(fx, "POST", "/api/items")["params"] if p["source"] == "body"
    }
    assert (
        params["name"]["required"],
        params["name"]["min_length"],
        params["name"]["max_length"],
    ) == (
        True,
        1,
        100,
    )
    assert (params["price"]["ge"], params["price"]["le"], params["price"]["type"]) == (
        0,
        1000,
        "Float",
    )
    assert params["state"]["choices"] == ["draft", "live"]
    assert route(fx, "POST", "/api/items")["body_models"] == ["createItem"]
    login = {p["name"]: p for p in route(fx, "POST", "/api/login")["params"]}
    assert (login["password"]["min_length"], login["password"]["max_length"]) == (8, 72)
    assert {m["name"] for m in fx["models"]} == {"createItem", "loginReq"}


def test_path_parameters_are_str_path_params(fx):
    p = [p for p in route(fx, "GET", "/api/items/{id}")["params"] if p["source"] == "path"]
    assert [(x["name"], x["type"]) for x in p] == [("id", "Str")]


def test_direct_refusals_are_abort_sites_with_the_literal_code(fx):
    sites = {(s["status"], s["code"], s["kind"]) for s in route(fx, "POST", "/api/login")["raises"]}
    assert sites == {(400, "bad_request", "abort"), (401, "bad_credentials", "abort")}
    assert route(fx, "GET", "/api/items/{id}")["raises"][0]["status"] == 404


def test_the_error_writer_maps_a_sentinel_the_closure_returns(fx):
    w = fx["error_writer"]
    assert w["func"] == "api.write" and w["wrappers"] == ["api.Handle"]
    assert [(m["status"], m["code"]) for m in w["maps"]] == [(410, "gone")]
    assert w["catch_all"]["status"] == 500
    r = route(fx, "DELETE", "/api/items/{id}")
    assert [(s["status"], s["kind"]) for s in r["raises"]] == [(410, "handler")]
    assert r["error_writer"] == "api.write"


def test_auth_comes_from_the_middleware_chain(fx):
    assert fx["auth"]["middleware"] == "api.RequireUser.func1"
    assert fx["auth"]["caller_keys"] == {"userID": "scalar"}
    admin = route(fx, "GET", "/api/admin/audit")
    assert [m["func"] for m in admin["middleware"]][-1] == "api.RequireAdmin.func1"
    guards = {(g["kind"], g["status"]) for g in admin["guards"]}
    assert ("authenticated", 401) in guards and ("custom", 403) in guards
    assert route(fx, "GET", "/api/status")["guards"] == []


def test_owner_evidence_follows_the_caller_id(fx):
    use = {
        (o["table"], o["column"], o["how"]) for o in route(fx, "GET", "/api/items")["owner_uses"]
    }
    assert use == {("items", "owner_id", "compare")}
    use = {
        (o["table"], o["column"], o["how"]) for o in route(fx, "POST", "/api/items")["owner_uses"]
    }
    assert ("items", "owner_id", "assign") in use
    assert route(fx, "GET", "/api/items/{id}")["owner_uses"][0]["how"] == "compare"
    assert route(fx, "POST", "/api/login")["owner_uses"] == []


def test_tables_by_migration_by_table_name_and_by_use(fx):
    tables = {t["struct"]: t for t in fx["tables"]}
    assert set(tables) == {"User", "Item", "Tag", "Audit"}  # not Skipped
    assert tables["Tag"]["table"] == "item_tags" and tables["Tag"]["source"] == "migrate"
    assert tables["Audit"]["source"] == "model" and tables["Audit"]["table"] == "audits"
    item = {c["name"]: c for c in tables["Item"]["columns"]}
    assert list(item)[:4] == ["id", "created_at", "updated_at", "deleted_at"]  # gorm.Model
    assert item["id"]["primary_key"] and item["deleted_at"]["nullable"]
    assert (item["name"]["max_length"], item["name"]["required"]) == (100, True)
    assert (item["state"]["max_length"], item["state"]["default"]) == (16, "draft")
    assert item["owner_id"]["index"] and not item["owner_id"]["unique"]
    user = {c["name"]: c for c in tables["User"]["columns"]}
    assert user["email"]["unique"] and user["email"]["max_length"] == 255
    assert user["is_admin"]["default"] == "false"
    tag = {c["name"]: c for c in tables["Tag"]["columns"]}
    assert tables["Tag"]["unique_together"] == [["item_id", "label"]]
    assert not tag["item_id"]["unique"]  # a composite uniqueIndex is not per-column
    assert tag["item_id"]["foreign_key"] == "items.id"  # only from the foreignKey tag
    assert tag["seen"]["type"] == "Date"
    assert not [c for c in user.values() if c["foreign_key"]]  # never from naming


def test_the_output_is_deterministic_and_has_no_absolute_path(tmp_path, fx):
    clone = tmp_path / "clone"
    shutil.copytree(FIXTURE, clone)
    again = subprocess.run(
        [binary(), "extract"],
        input=json.dumps({"root": str(clone)}),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    first = subprocess.run(
        [binary(), "extract"],
        input=json.dumps({"root": str(FIXTURE)}),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert again == first
    assert "/Users" not in first and "/private" not in first and str(clone) not in again


def test_nothing_is_written_into_the_target(tmp_path):
    clone = tmp_path / "clone"
    shutil.copytree(FIXTURE, clone)

    def digest() -> dict[str, str]:
        return {
            p.relative_to(clone).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(clone.rglob("*"))
            if p.is_file()
        }

    before = digest()
    extract(clone)
    assert digest() == before


def test_bad_input_exits_2(tmp_path):
    exe = binary()
    bad = subprocess.run([exe, "extract"], input="not json", capture_output=True, text=True)
    assert bad.returncode == 2
    missing = subprocess.run(
        [exe, "extract"],
        input=json.dumps({"root": str(tmp_path / "nope")}),
        capture_output=True,
        text=True,
    )
    assert missing.returncode == 2


# ---- small modules ----------------------------------------------------------------------


def test_no_main_package_counts_every_function_as_wired(tmp_path):
    root = write_module(
        tmp_path,
        {
            "routes.go": """
        package web

        import "github.com/gin-gonic/gin"

        func Register(r *gin.Engine) {
			r.GET("/ping", func(c *gin.Context) { c.String(200, "pong") })
		}
        """
        },
    )
    x = extract(root)
    assert [r["path_template"] for r in x["routes"]] == ["/ping"]
    assert any("no main package" in n for n in x["notes"])


def test_a_router_parameter_is_read_through_each_call_site(tmp_path):
    root = write_module(
        tmp_path,
        {
            "main.go": """
        package main

        import "github.com/gin-gonic/gin"

        func users(g *gin.RouterGroup) {
			g.GET("/list", func(c *gin.Context) { c.Status(200) })
		}

        func main() {
			r := gin.New()
			users(r.Group("/v1"))
			users(r.Group("/v2"))
			_ = r.Run()
		}
        """
        },
    )
    x = extract(root)
    assert sorted(r["path_template"] for r in x["routes"]) == ["/v1/list", "/v2/list"]


def test_two_literals_of_a_router_struct_make_its_routes_unread(tmp_path):
    root = write_module(
        tmp_path,
        {
            "main.go": """
        package main

        import "github.com/gin-gonic/gin"

        type Routes struct{ Api *gin.RouterGroup }

        func add(r Routes) {
			r.Api.GET("/x", func(c *gin.Context) { c.Status(200) })
		}

        func main() {
			e := gin.New()
			add(Routes{Api: e.Group("/a")})
			add(Routes{Api: e.Group("/b")})
		}
        """
        },
    )
    x = extract(root)
    (r,) = x["routes"]
    assert not r["path_exact"] and r["auth"] == "unclear"
    assert any("composite literals of Routes" in u["reason"] for u in r["unread"])


def test_a_group_use_applies_only_to_routes_after_it(tmp_path):
    root = write_module(
        tmp_path,
        {
            "main.go": """
        package main

        import (
			"net/http"

			"github.com/gin-gonic/gin"
		)

        func guard(c *gin.Context) {
			if c.GetHeader("X-Key") == "" {
				c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"code": "no_key"})
				return
			}
			c.Set("caller", 1)
		}

        func ok(c *gin.Context) { c.Status(200) }

        func main() {
			r := gin.New()
			r.GET("/before", ok)
			r.Use(guard)
			r.GET("/after", ok)
			_ = r.Run()
		}
        """
        },
    )
    x = extract(root)
    assert route(x, "GET", "/before")["auth"] == "Public"
    assert route(x, "GET", "/after")["auth"] == "Customer"


def test_a_file_that_does_not_parse_is_unread_and_the_rest_is_read(tmp_path):
    root = write_module(
        tmp_path,
        {
            "main.go": """
            package main

            import "github.com/gin-gonic/gin"

            func main() {
				r := gin.New()
				r.GET("/ok", func(c *gin.Context) { c.Status(200) })
			}
            """,
            "broken.go": "package main\n\nfunc broken( {\n",
        },
    )
    x = extract(root)
    assert [r["path_template"] for r in x["routes"]] == ["/ok"]
    assert any(u["what"] == "file" and u["file"] == "broken.go" for u in x["unread"])


def test_a_middleware_outside_the_module_that_is_not_known_makes_auth_unclear(tmp_path):
    root = write_module(
        tmp_path,
        {
            "main.go": """
        package main

        import (
			"github.com/gin-gonic/gin"
			"github.com/example/jwtmw"
		)

        func main() {
			r := gin.New()
			r.Use(jwtmw.Verify())
			r.GET("/me", func(c *gin.Context) { c.Status(200) })
		}
        """
        },
    )
    x = extract(root)
    r = route(x, "GET", "/me")
    assert r["auth"] == "unclear" and "jwtmw.Verify" in r["auth_reason"]


TYPICAL = """
    package main

    import (
        "net/http"
        "strings"

        "github.com/gin-gonic/gin"
        "gorm.io/gorm"
    )

    type User struct {
        ID    uint   `gorm:"primarykey"`
        Token string `gorm:"uniqueIndex"`
    }

    type Job struct {
        ID      uint   `gorm:"primarykey"`
        OwnerID uint   `gorm:"index"`
        Status  string `gorm:"default:queued"`
    }

    type API struct{ db *gorm.DB }

    func (a *API) auth() gin.HandlerFunc {
        return func(c *gin.Context) {
            h := c.GetHeader("Authorization")
            if !strings.HasPrefix(h, "Bearer ") {
                c.JSON(http.StatusUnauthorized, gin.H{"detail": "missing token"})
                c.Abort()
                return
            }
            var u User
            tok := strings.TrimPrefix(h, "Bearer ")
            if err := a.db.Where("token = ?", tok).First(&u).Error; err != nil {
                c.AbortWithStatus(http.StatusUnauthorized)
                return
            }
            c.Set("user", u)
            c.Next()
        }
    }

    func (a *API) start(c *gin.Context) {
        u := c.MustGet("user").(User)
        var in struct {
            Note string `json:"note" binding:"max=50"`
        }
        if err := c.ShouldBindJSON(&in); err != nil {
            c.JSON(http.StatusBadRequest, gin.H{"detail": "bad body"})
            return
        }
        var job Job
        q := a.db.Where("id = ? AND owner_id = ?", c.Param("id"), u.ID)
        if err := q.First(&job).Error; err != nil {
            c.JSON(http.StatusNotFound, gin.H{"detail": "job not found"})
            return
        }
        job.Status = "running"
        a.db.Save(&job)
        c.JSON(http.StatusOK, job)
    }

    func (a *API) finish(c *gin.Context) {
        var job Job
        a.db.First(&job, c.Param("id"))
        a.db.Model(&job).Update("status", "done")
        c.Status(http.StatusNoContent)
    }

    func main() {
        db, _ := gorm.Open(nil, &gorm.Config{})
        db.AutoMigrate(&User{}, &Job{})
        a := &API{db: db}
        r := gin.New()
        g := r.Group("/jobs", a.auth())
        g.POST("/:id/start", a.start)
        g.POST("/:id/finish", a.finish)
        _ = r.Run()
    }
"""


def test_a_typical_app_is_read_end_to_end(tmp_path):
    x = extract(write_module(tmp_path, {"main.go": TYPICAL}))
    start = route(x, "POST", "/jobs/{id}/start")
    assert start["auth"] == "Customer" and start["path_exact"]
    assert [(g["kind"], g["status"], g["detail"]) for g in start["guards"]] == [
        ("authenticated", 401, "missing token")
    ]
    # `c.MustGet("user").(User)`: the caller's id is u.ID
    assert [(o["table"], o["column"], o["how"]) for o in start["owner_uses"]] == [
        ("jobs", "owner_id", "compare")
    ]
    # an anonymous struct bound by ShouldBindJSON
    body = [p for p in start["params"] if p["source"] == "body"]
    assert [(p["name"], p["max_length"]) for p in body] == [("note", 50)]
    assert (400, "bad body", "abort") in {
        (s["status"], s["code"], s["kind"]) for s in start["raises"]
    }
    # constant writes to a string column, by root
    writes = {(w["value"], w["root"]) for w in x["status_writes"] if w["column"] == "status"}
    assert writes == {("running", "POST /jobs/{id}/start"), ("done", "POST /jobs/{id}/finish")}
    assert x["auth"]["scheme"] == "bearer" and x["auth"]["users_table"] == "User"
    assert {t["struct"]: t["source"] for t in x["tables"]} == {"User": "migrate", "Job": "migrate"}
    finish = route(x, "POST", "/jobs/{id}/finish")
    assert finish["status_code"] == 204 and finish["owner_uses"] == []


def test_middleware_added_to_a_router_field_in_another_function_is_unread(tmp_path):
    root = write_module(
        tmp_path,
        {
            "main.go": """
            package main

            import (
                "net/http"

                "github.com/gin-gonic/gin"
            )

            type Server struct{ r *gin.Engine }

            func guard(c *gin.Context) {
                if c.GetHeader("X-Key") == "" {
                    c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"code": "no_key"})
                    return
                }
                c.Set("caller", 1)
            }

            func ok(c *gin.Context) { c.Status(200) }

            func (s *Server) routes() {
                s.r.GET("/a", ok)
                s.r.Use(guard)
                s.r.GET("/b", ok)
            }

            func main() {
                s := &Server{r: gin.New()}
                s.r.Use(guard)
                s.routes()
            }
            """
        },
    )
    x = extract(root)
    a, b = route(x, "GET", "/a"), route(x, "GET", "/b")
    assert a["auth"] == "unclear" and "another function" in a["auth_reason"]
    assert b["auth"] == "unclear"  # the other Use is still unordered


def test_a_local_that_names_itself_does_not_recurse_forever(tmp_path):
    """``tt := tt`` is common Go; Module.FuncRef follows a local's value without a bound."""
    root = write_module(
        tmp_path,
        {
            "main.go": """
            package main

            import "github.com/gin-gonic/gin"

            func main() {
                r := gin.New()
                for _, tt := range []string{"a", "b"} {
                    tt := tt
                    go func() { _ = tt }()
                }
                a := b
                b := a
                _ = a
                r.GET("/ok", func(c *gin.Context) { c.Status(200) })
            }

            var b = 1
            """
        },
    )
    x = extract(root)
    assert [r["path_template"] for r in x["routes"]] == ["/ok"]
