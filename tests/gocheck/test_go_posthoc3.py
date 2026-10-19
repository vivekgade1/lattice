"""Spike J, node G3 post hoc (np-go3-posthoc): three library defects found by diagnosing the blind
failures of the held-out S4 (``shelfie``). Post hoc: written after the scored round, never blind.

1. the login route of an app that stores the password as typed (no bcrypt) was not found, so layer R
   ran without callers (H-PI3 1 of 119); the extractor now reads a plain compare of a password column
   (``subtle.ConstantTimeCompare``, ``bytes.Equal``, ``==``) in a Public POST whose body has a
   password field, only when there is no bcrypt compare (``goextract_owner.go``, ``goextract_assemble.go``);
2. GORM's default table name of ``Shelf`` is ``shelves`` (jinzhu/inflection ``([lr])f$`` and
   ``([^f])fe$`` -> ``ves``), the checker said ``shelfs`` (``util.go`` ``plural``);
3. GO801 did not see an owner check whose compared value was carried from the owner field through a
   helper's return (``book, owner, err := s.bookWithOwner(id)`` ... ``owner != userID(c)``) (``owner.go``).

Needs ``go``; nothing here builds or runs an app."""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from lattice.gocheck import GoPolicy, run_gocheck
from lattice.importer.frontends.go_gin import GoGinFrontEnd

pytestmark = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")

LOGIN_HEAD = """\
package main

import (
\t"bytes"
\t"crypto/subtle"
\t"net/http"

\t"github.com/gin-gonic/gin"
\t"golang.org/x/crypto/bcrypt"
\t"gorm.io/gorm"
)

var _ = bytes.Equal
var _ = subtle.ConstantTimeCompare
var _ = bcrypt.CompareHashAndPassword

type User struct {
\tID       uint   `gorm:"primaryKey" json:"id"`
\tUsername string `json:"username"`
\tPassword string `json:"-"`
\tNickname string `json:"nickname"`
}

type loginIn struct {
\tUsername string `json:"username" binding:"required"`
\tPassword string `json:"password" binding:"required"`
}

type nameIn struct {
\tUsername string `json:"username" binding:"required"`
\tNickname string `json:"nickname" binding:"required"`
}

type srv struct{ db *gorm.DB }

func (s *srv) me(c *gin.Context) { c.JSON(http.StatusOK, gin.H{}) }

func (s *srv) auth(c *gin.Context) {
\tif c.GetHeader("Authorization") == "" {
\t\tc.AbortWithStatus(http.StatusUnauthorized)
\t\treturn
\t}
\tc.Set("uid", uint(1))
\tc.Next()
}

func main() {
\ts := &srv{}
\tr := gin.New()
\tr.POST("/login", s.login)
\tr.GET("/me", s.auth, s.me)
\t_ = r
}
"""


def _module(base: Path, login_body: str, *, bind: str = "loginIn") -> Path:
    tmp = base / "m"
    tmp.mkdir()
    (tmp / "go.mod").write_text("module x\n\ngo 1.22\n")
    body = textwrap.indent(textwrap.dedent(login_body), "\t")
    (tmp / "main.go").write_text(
        LOGIN_HEAD
        + f"""
func (s *srv) login(c *gin.Context) {{
\tvar in {bind}
\tif err := c.ShouldBindJSON(&in); err != nil {{
\t\tc.AbortWithStatus(http.StatusUnprocessableEntity)
\t\treturn
\t}}
\tvar users []User
\tif err := s.db.Where("username = ?", in.Username).Limit(1).Find(&users).Error; err != nil {{
\t\tc.AbortWithStatus(http.StatusInternalServerError)
\t\treturn
\t}}
{body}
\tc.JSON(http.StatusOK, gin.H{{"access_token": "t"}})
}}
"""
    )
    return tmp


def _auth(tmp: Path) -> dict:
    return GoGinFrontEnd().extract_json(tmp.parent, tmp.name)["auth"]


def _refuse(cond: str) -> str:
    return f"""
    if len(users) == 0 || {cond} {{
    \tc.AbortWithStatus(http.StatusUnauthorized)
    \treturn
    }}
    """


# ---- 1. the login route of a plain-password app --------------------------------------------------


@pytest.mark.parametrize(
    "cond",
    [
        "subtle.ConstantTimeCompare([]byte(users[0].Password), []byte(in.Password)) != 1",
        "!bytes.Equal([]byte(users[0].Password), []byte(in.Password))",
        "users[0].Password != in.Password",
        "in.Password != users[0].Password",
    ],
)
def test_a_plain_password_compare_names_the_login_route(tmp_path, cond):
    a = _auth(_module(tmp_path, _refuse(cond)))
    assert a["login_path"] == "/login"
    assert a["password_column"] == "password"
    assert a["users_table"] == "User"
    assert (a["username_field"], a["password_field"]) == ("username", "password")
    assert a["login_encoding"] == "json" and a["token_field"] == "access_token"


def test_a_bcrypt_compare_still_wins_over_a_plain_one(tmp_path):
    body = """
    if len(users) == 0 || bcrypt.CompareHashAndPassword([]byte(users[0].Password), []byte(in.Password)) != nil {
    \tc.AbortWithStatus(http.StatusUnauthorized)
    \treturn
    }
    _ = users[0].Password == in.Password
    """
    a = _auth(_module(tmp_path, body))
    assert a["login_path"] == "/login" and a["password_column"] == "password"


def test_a_compare_of_another_column_is_not_a_login(tmp_path):
    a = _auth(_module(tmp_path, _refuse("users[0].Nickname != in.Password")))
    assert a["login_path"] is None


def test_a_plain_compare_needs_a_password_field_in_the_body(tmp_path):
    a = _auth(_module(tmp_path, _refuse("users[0].Password != in.Nickname"), bind="nameIn"))
    assert a["login_path"] is None


def test_no_compare_at_all_is_not_a_login(tmp_path):
    assert _auth(_module(tmp_path, "_ = users"))["login_path"] is None


def test_shelfie_names_its_login_and_the_importer_gets_callers():
    shelfie = Path(__file__).resolve().parents[2] / "tests" / "spec" / "shelfie"
    if not shelfie.is_dir():  # the held-out module is staged in the post hoc tree only
        pytest.skip("tests/spec/shelfie is not staged")
    a = GoGinFrontEnd().extract_json(shelfie.parent, "shelfie")["auth"]
    assert a["login_path"] == "/login" and a["password_column"] == "password"


# ---- 2. GORM's table names for f / fe endings ---------------------------------------------------


def test_gorms_plural_of_f_and_fe_endings(tmp_path):
    """The expected names are what ``inflection.Plural`` of jinzhu/inflection v1.0.0 (the library GORM's
    NamingStrategy calls) answers, checked by running it: ``leaf`` -> ``leafs`` and ``cafe`` -> ``caves``
    are its own answers."""
    (tmp_path / "go.mod").write_text("module x\n\ngo 1.22\n")
    structs = ["Shelf", "Wolf", "Knife", "Life", "Staff", "Roof", "Chief", "Leaf", "Scarf", "Cafe", "Half"]
    text = 'package main\n\nimport "gorm.io/gorm"\n\n' + "".join(
        f'type {n} struct {{\n\tID   uint   `gorm:"primaryKey" json:"id"`\n\tName string `json:"name"`\n}}\n\n' for n in structs
    )
    migrate = ", ".join(f"&{n}{{}}" for n in structs)
    (tmp_path / "main.go").write_text(
        text + f"func main() {{\n\tvar db *gorm.DB\n\tdb.AutoMigrate({migrate})\n}}\n"
    )
    from lattice.gocheck.derive import policy_extract

    got = {t["struct"]: t["table"] for t in policy_extract(tmp_path)["tables"]}
    assert got == {
        "Shelf": "shelves", "Wolf": "wolves", "Knife": "knives", "Life": "lives",
        "Staff": "staffs", "Roof": "roofs", "Chief": "chiefs", "Leaf": "leafs",
        "Scarf": "scarves", "Cafe": "caves", "Half": "halves",
    }  # fmt: skip


# ---- 3. GO801: the owner value carried to the comparison -----------------------------------------

OWNER_HEAD = """\
package main

import (
\t"net/http"

\t"github.com/gin-gonic/gin"
\t"gorm.io/gorm"
)

type Shelf struct {
\tID     uint   `gorm:"primaryKey" json:"id"`
\tUserID uint   `gorm:"not null;index" json:"user_id"`
\tName   string `json:"name"`
}

type Book struct {
\tID      uint   `gorm:"primaryKey" json:"id"`
\tShelfID uint   `gorm:"not null;index" json:"shelf_id"`
\tTitle   string `json:"title"`
}

type srv struct{ db *gorm.DB }

func userID(c *gin.Context) uint { return c.GetUint("uid") }

// bookWithOwner loads a book and the id of the user who owns it through its shelf.
func (s *srv) bookWithOwner(id uint) (Book, uint, error) {
\tvar book Book
\tif err := s.db.First(&book, id).Error; err != nil {
\t\treturn book, 0, err
\t}
\tvar shelf Shelf
\tif err := s.db.First(&shelf, book.ShelfID).Error; err != nil {
\t\treturn book, 0, err
\t}
\treturn book, shelf.UserID, nil
}

func (s *srv) viaVar(id uint) (Book, uint, error) {
\tvar book Book
\tvar shelf Shelf
\tif err := s.db.First(&shelf, book.ShelfID).Error; err != nil {
\t\treturn book, 0, err
\t}
\towner := shelf.UserID
\treturn book, owner, nil
}

func (s *srv) elsewhere(id uint) (Book, uint, error) {
\tvar book Book
\tvar shelf Shelf
\tif err := s.db.First(&shelf, book.ShelfID).Error; err != nil {
\t\treturn book, 0, err
\t}
\treturn book, shelf.ID, nil
}

"""
OWNER_POLICY = GoPolicy(tables={"Shelf": "shelves", "Book": "books"}, owners={"Shelf": "user_id"})


def _routes(handlers: dict[str, str]) -> str:
    regs = "".join(f'\tr.GET("/{name}/:id", s.{name})\n' for name in handlers)
    funcs = "".join(
        f"func (s *srv) {name}(c *gin.Context) {{\n{body}}}\n\n" for name, body in handlers.items()
    )
    return funcs + "func main() {\n\ts := &srv{}\n\tr := gin.New()\n" + regs + "\t_ = r\n}\n"


def _go801(tmp: Path, handlers: dict[str, str]) -> set[str]:
    (tmp / "go.mod").write_text("module x\n\ngo 1.22\n")
    (tmp / "main.go").write_text(OWNER_HEAD + _routes(handlers))
    return {f.key for f in run_gocheck(tmp, OWNER_POLICY, strict=True) if f.code == "GO801"}


CHECK = """\
\t_, owner, err := s.%s(1)
\tif err != nil {
\t\tc.AbortWithStatus(http.StatusNotFound)
\t\treturn
\t}
\tif owner != userID(c) {
\t\tc.AbortWithStatus(http.StatusForbidden)
\t\treturn
\t}
\tc.Status(http.StatusOK)
"""
NOCHECK = """\
\t_, owner, err := s.%s(1)
\t_ = owner
\tif err != nil {
\t\tc.AbortWithStatus(http.StatusNotFound)
\t\treturn
\t}
\tc.Status(http.StatusOK)
"""


def test_an_owner_value_returned_by_a_helper_and_compared_is_an_owner_check(tmp_path):
    got = _go801(tmp_path, {"a": CHECK % "bookWithOwner", "b": CHECK % "viaVar"})
    assert got == set()


def test_without_the_comparison_the_route_is_still_reported(tmp_path):
    got = _go801(tmp_path, {"a": NOCHECK % "bookWithOwner", "b": NOCHECK % "viaVar"})
    assert got == {"GO801|GET /a/:id|Shelf", "GO801|GET /b/:id|Shelf"}


def test_a_returned_value_that_is_not_the_owner_field_does_not_count(tmp_path):
    got = _go801(tmp_path, {"a": CHECK % "elsewhere"})
    assert got == {"GO801|GET /a/:id|Shelf"}


def test_a_direct_owner_field_compare_is_unchanged(tmp_path):
    direct = """\
\tvar shelf Shelf
\tif err := s.db.First(&shelf, 1).Error; err != nil {
\t\treturn
\t}
\tif shelf.UserID != userID(c) {
\t\tc.AbortWithStatus(http.StatusForbidden)
\t\treturn
\t}
"""
    assert _go801(tmp_path, {"a": direct}) == set()


def test_shelfie_baseline_is_empty_under_its_derived_policy():
    shelfie = Path(__file__).resolve().parents[2] / "tests" / "spec" / "shelfie"
    if not shelfie.is_dir():
        pytest.skip("tests/spec/shelfie is not staged")
    policy = GoPolicy(
        tables={"Book": "books", "Note": "notes", "Shelf": "shelves", "User": "users"},
        users_table="User",
        owners={"Note": "user_id", "Shelf": "user_id"},
        private={"User": ("password",)},
    )
    assert [f.key for f in run_gocheck(shelfie, policy, strict=True)] == []
