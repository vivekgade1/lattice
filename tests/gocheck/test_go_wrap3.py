"""Spike J, node G3 (round 3), K1 and K2 in layer A: wrapper-closure handlers and middleware,
and method calls of outside types (``lattice/gocheck/src/{routes,reach,owner}.go``). The parse-only
fixture ``goparity3`` (truth in its ``truth.json``) and small modules written to ``tmp_path``.
Needs ``go``; nothing here builds or runs an app."""

from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path

import pytest

from lattice.gocheck import GoPolicy, run_gocheck

pytestmark = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")

REPO = Path(__file__).resolve().parents[2]
GP3 = REPO / "tests" / "gocheck" / "fixtures" / "goparity3"
NOTES = GoPolicy(tables={"Note": "notes"}, owners={"Note": "owner_id"})

HEAD = """\
package main

import (
\t"crypto/hmac"
\t"crypto/sha256"
\t"errors"
\t"net/http"
\t"regexp"
\t"time"

\t"github.com/gin-gonic/gin"
\t"gorm.io/gorm"

\text "github.com/acme/guard"
)

var _ = hmac.New
var _ = sha256.New
var _ = errors.New
var _ = regexp.MustCompile
var _ = time.Now
var _ ext.Verifier

type Note struct {
\tID      uint   `gorm:"primaryKey" json:"id"`
\tOwnerID uint   `gorm:"not null;index" json:"owner_id"`
\tBody    string `json:"body"`
}

type handler struct{ db *gorm.DB }

func Handle(fn func(c *gin.Context) error) gin.HandlerFunc {
\treturn func(c *gin.Context) {
\t\tif err := fn(c); err != nil {
\t\t\tc.AbortWithStatus(http.StatusBadRequest)
\t\t}
\t}
}

func Wrap2(fn func(c *gin.Context) error) func(c *gin.Context) error {
\treturn func(c *gin.Context) error { return fn(c) }
}

// unscoped reads a note by id with no owner check.
func (h handler) unscoped(c *gin.Context) error {
\tvar n Note
\tif err := h.db.First(&n, c.Param("id")).Error; err != nil {
\t\treturn err
\t}
\tc.JSON(http.StatusOK, n)
\treturn nil
}

func Plain(db *gorm.DB) gin.HandlerFunc {
\treturn func(c *gin.Context) {
\t\tvar n Note
\t\tdb.First(&n, c.Param("id"))
\t\tc.JSON(http.StatusOK, n)
\t}
}

func main() {
\tdb, _ := gorm.Open(nil, &gorm.Config{})
\th := handler{db: db}
\tr := gin.New()
\t_, _, _ = h, r, db
"""


def module(tmp_path: Path, routes: str, extra: str = "") -> Path:
    (tmp_path / "go.mod").write_text("module example.com/t\n\ngo 1.22\n")
    (tmp_path / "main.go").write_text(
        HEAD + textwrap.indent(textwrap.dedent(routes), "\t") + "}\n\n" + textwrap.dedent(extra)
    )
    return tmp_path


def keys(root: Path) -> list[str]:
    return sorted(f.key for f in run_gocheck(root, NOTES, strict=True))


# ---- the registered fixture ------------------------------------------------------------------


def test_goparity3_layer_a_is_the_registered_truth():
    truth = json.loads((GP3 / "truth.json").read_text())
    policy = GoPolicy(**{k: truth["policy"][k] for k in ("tables", "owners")})
    got = sorted(f.key for f in run_gocheck(GP3, policy, strict=True))
    assert got == truth["layer_a_keys_expected"]


# ---- K1: the handler behind a one-level wrapper is the route's root -----------------------------


def test_k1_the_argument_of_a_wrapper_is_the_root_and_two_levels_stay_silent(tmp_path):
    root = module(tmp_path, """\
        r.GET("/a/:id", Handle(h.unscoped))
        r.GET("/b/:id", Handle(Wrap2(h.unscoped)))
        """)  # fmt: skip
    assert keys(root) == ["GO801|GET /a/:id|Note"]


def test_k1_every_wrapped_route_is_its_own_root(tmp_path):
    root = module(tmp_path, """\
        r.GET("/a/:id", Handle(h.unscoped))
        r.GET("/b/:id", Handle(h.unscoped))
        r.PUT("/c/:id", Handle(h.unscoped))
        """)  # fmt: skip
    assert keys(root) == ["GO801|GET /a/:id|Note", "GO801|GET /b/:id|Note", "GO801|PUT /c/:id|Note"]


def test_k1_an_unwrapped_handler_registered_twice_keeps_one_root(tmp_path):
    root = module(tmp_path, """\
        r.GET("/a/:id", Plain(db))
        r.GET("/b/:id", Plain(db))
        """)  # fmt: skip
    # the round-2 behaviour: roots are keyed by the handler function, the first route names it
    assert len(keys(root)) == 1


def test_k1_a_wrapper_argument_that_does_not_resolve_stays_silent(tmp_path):
    root = module(tmp_path, """\
        var f func(c *gin.Context) error
        r.GET("/a/:id", Handle(f))
        """)  # fmt: skip
    assert keys(root) == []


def test_k1_a_wrapper_that_calls_two_parameters_binds_nothing(tmp_path):
    root = module(
        tmp_path,
        'r.GET("/a/:id", Both(h.unscoped, h.unscoped))\n',
        """\
        func Both(f, g func(c *gin.Context) error) gin.HandlerFunc {
            return func(c *gin.Context) {
                _ = f(c)
                _ = g(c)
            }
        }
        """,
    )
    assert keys(root) == []


def test_k1_a_guard_returned_through_the_wrapper_is_read_as_the_middleware(tmp_path):
    root = module(
        tmp_path,
        """\
        g := r.Group("/g", Guard())
        g.GET("/a/:id", Plain(db))
        """,
        """\
        func Guard() gin.HandlerFunc {
            return Handle(func(c *gin.Context) error { return nil })
        }
        """,
    )
    assert keys(root) == ["GO801|GET /g/a/:id|Note"]


def test_k1_a_wrapped_middleware_that_scopes_by_owner_is_evidence(tmp_path):
    root = module(
        tmp_path,
        """\
        g := r.Group("/g", Scope(db))
        g.GET("/a/:id", Plain(db))
        """,
        """\
        func Scope(db *gorm.DB) gin.HandlerFunc {
            return Handle(func(c *gin.Context) error {
                var n Note
                return db.Where("owner_id = ?", c.GetUint("uid")).First(&n).Error
            })
        }
        """,
    )
    assert keys(root) == []


# ---- K2: a method of an outside type is opaque only when handed the request, the database or a row


def guarded(tmp_path: Path, guard: str, call: str = "Guard()") -> Path:
    return module(
        tmp_path,
        f"""\
        g := r.Group("/g", {call})
        g.GET("/a/:id", Plain(db))
        """,
        guard,
    )


def test_k2_hmac_methods_on_a_local_bound_to_an_outside_call_are_not_opaque(tmp_path):
    root = guarded(tmp_path, """\
        func Guard() gin.HandlerFunc {
            return func(c *gin.Context) {
                mac := hmac.New(sha256.New, []byte("k"))
                mac.Write([]byte(c.GetHeader("X")))
                if !hmac.Equal([]byte(c.GetHeader("S")), mac.Sum(nil)) {
                    c.AbortWithStatus(http.StatusUnauthorized)
                }
            }
        }
        """)  # fmt: skip
    assert keys(root) == ["GO801|GET /g/a/:id|Note"]


def test_k2_a_method_of_an_outside_type_handed_the_request_stays_opaque(tmp_path):
    root = guarded(tmp_path, """\
        func Guard() gin.HandlerFunc {
            v := ext.NewVerifier()
            return func(c *gin.Context) {
                if !v.Check(c) {
                    c.AbortWithStatus(http.StatusUnauthorized)
                }
            }
        }
        """)  # fmt: skip
    assert keys(root) == []


def test_k2_a_method_of_an_outside_type_handed_the_database_stays_opaque(tmp_path):
    root = guarded(tmp_path, """\
        func Guard(db *gorm.DB) gin.HandlerFunc {
            v := ext.NewVerifier()
            return func(c *gin.Context) { v.CheckDB(db) }
        }
        """, "Guard(db)")  # fmt: skip
    assert keys(root) == []


def test_uninferable_receiver_stays_opaque(tmp_path):
    """A receiver whose origin is a module function (so not an outside type) and whose type the
    checker cannot infer is still unreadable: silence over guessing."""
    root = guarded(tmp_path, """\
        func pick() any { return nil }

        func Guard() gin.HandlerFunc {
            g := pick()
            return func(c *gin.Context) {
                g.Check(c.GetHeader("X"))
            }
        }
        """)  # fmt: skip
    assert keys(root) == []


def test_k2_a_receiver_rooted_at_the_request_and_a_package_regexp_are_not_opaque(tmp_path):
    root = guarded(tmp_path, """\
        var tokenRE = regexp.MustCompile(`^[a-z]+$`)

        func Guard() gin.HandlerFunc {
            return func(c *gin.Context) {
                _ = c.Request.Context()
                if !tokenRE.MatchString(c.GetHeader("X")) {
                    c.AbortWithStatus(http.StatusUnauthorized)
                }
            }
        }
        """)  # fmt: skip
    assert keys(root) == ["GO801|GET /g/a/:id|Note"]


def test_k2_a_time_value_chain_is_not_opaque(tmp_path):
    root = guarded(tmp_path, """\
        func Guard() gin.HandlerFunc {
            return func(c *gin.Context) {
                d := time.Now()
                _ = d.UTC().Format(time.RFC3339)
            }
        }
        """)  # fmt: skip
    assert keys(root) == ["GO801|GET /g/a/:id|Note"]


def test_k2_a_conversion_to_a_type_literal_is_not_opaque_but_a_module_call_still_is(tmp_path):
    root = guarded(tmp_path, """\
        func Guard() gin.HandlerFunc {
            return func(c *gin.Context) {
                _ = []byte(c.GetHeader("X"))
                _ = map[string]string(nil)
            }
        }
        """)  # fmt: skip
    assert keys(root) == ["GO801|GET /g/a/:id|Note"]


def test_k2_a_method_of_an_unknown_module_type_is_opaque_even_with_plain_arguments(tmp_path):
    root = guarded(tmp_path, """\
        type authz interface{ Allows(id string) bool }

        func newAuthz() authz { return nil }

        func Guard() gin.HandlerFunc {
            a := newAuthz()
            return func(c *gin.Context) {
                if !a.Allows(c.Param("id")) {
                    c.AbortWithStatus(http.StatusForbidden)
                }
            }
        }
        """)  # fmt: skip
    assert keys(root) == []
