"""The code: citation checker (Spike C, D8): path, containment, case, file, line, quote and
symbol, deterministic and read-only; valid, invalid and forged citations."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from lattice.importer.inventory import capture
from lattice.spec.implementation import (
    ImplContext,
    Inventory,
    check_code,
    evidence_locations,
    load_inventory,
    parse_code_target,
    sha256_bytes,
)

FIX = Path(__file__).resolve().parent / "fixtures" / "implementation"
EXTRACT = json.loads((FIX / "extract.json").read_text())
WF = FIX / "workflows.json"

REG = "api/api.go"  # r.POST("/api/login", Login(db)) is registered here
LOGIN_HANDLER = "api/api.go"


@pytest.fixture
def app(tmp_path: Path) -> Path:
    dest = tmp_path / "app"
    shutil.copytree(FIX / "app", dest)
    return dest


@pytest.fixture
def inv(app: Path, tmp_path: Path) -> Inventory:
    capture(app, tmp_path / "inv", extract=EXTRACT, workflows=WF)
    return load_inventory(tmp_path / "inv", app)


def line_of(app: Path, rel: str, needle: str) -> int:
    for i, ln in enumerate((app / rel).read_text().split("\n"), 1):
        if needle in ln:
            return i
    raise AssertionError(needle)


def cite(rel: str, line: int | str, quote: str, **extra: Any) -> dict[str, Any]:
    return {"target": f"code:{rel}:{line}", "step": None, "quote": quote, **extra}


def verdict(app: Path, inv: Inventory | None, c: dict[str, Any]) -> str | None:
    return check_code(app, inv, c)


# ---- valid --------------------------------------------------------------------------------


def test_the_registration_line_holds(app: Path, inv: Inventory) -> None:
    n = line_of(app, REG, 'r.POST("/api/login"')
    assert verdict(app, inv, cite(REG, n, 'r.POST("/api/login", Login(db))')) is None


def test_the_handler_line_holds(app: Path, inv: Inventory) -> None:
    n = line_of(app, LOGIN_HANDLER, "func Login(db *gorm.DB)")
    assert (
        verdict(app, inv, cite(LOGIN_HANDLER, n, "func Login(db *gorm.DB) gin.HandlerFunc")) is None
    )


def test_a_whitespace_variant_of_the_quote_holds(app: Path, inv: Inventory) -> None:
    n = line_of(app, REG, 'r.POST("/api/login"')
    assert verdict(app, inv, cite(REG, n, '  r.POST("/api/login",\t  Login(db))  ')) is None


def test_a_symbol_that_is_a_whole_word_on_the_line_holds(app: Path, inv: Inventory) -> None:
    n = line_of(app, REG, 'r.POST("/api/login"')
    assert verdict(app, inv, cite(REG, n, 'r.POST("/api/login"', symbol="Login")) is None


def test_the_inventorys_symbol_at_the_handler_line_holds(app: Path, inv: Inventory) -> None:
    locs = evidence_locations(inv)
    handler_locs = {loc: s for loc, s in locs.items() if "Login" in s}
    assert handler_locs, "the extract names Login's handler"
    loc = sorted(handler_locs)[0]
    rel, n = loc.rsplit(":", 1)
    text = (app / rel).read_text().split("\n")[int(n) - 1]
    # a symbol the inventory names at that line, though not necessarily a word on it
    assert verdict(app, inv, cite(rel, n, text.strip()[:30], symbol="Login")) is None


def test_a_code_file_outside_the_source_set_holds_and_go_mod_is_in_it(
    app: Path, inv: Inventory
) -> None:
    (app / "extra.go").write_text("package main\n\nfunc extraFunc() {}\n")
    assert verdict(app, inv, cite("extra.go", 3, "func extraFunc() {}")) is None
    assert verdict(app, inv, cite("go.mod", 1, "module example.com/wf")) is None


def test_a_valid_citation_at_an_evidence_location_is_evidence(app: Path, inv: Inventory) -> None:
    n = line_of(app, REG, 'r.POST("/api/login"')
    ctx = ImplContext.of(_spec(), None, inv)
    assert ctx.is_evidence(f"code:{REG}:{n}")
    other = line_of(app, REG, "type loginReq struct")
    assert verdict(app, inv, cite(REG, other, "type loginReq struct")) is None
    assert not ctx.is_evidence(f"code:{REG}:{other}")


def _spec():
    import importlib.util
    import sys

    sp = importlib.util.spec_from_file_location("cc_fixture_spec", FIX / "fixture_spec.py")
    mod = importlib.util.module_from_spec(sp)
    sys.modules["cc_fixture_spec"] = mod
    sp.loader.exec_module(mod)
    return mod.SPEC


# ---- invalid ------------------------------------------------------------------------------


def test_line_zero_and_line_past_the_end(app: Path, inv: Inventory) -> None:
    assert "not line 0" in verdict(app, inv, cite(REG, 0, "package api"))
    n = len((app / REG).read_text().rstrip("\n").split("\n"))
    assert f"not line {n + 1}" in verdict(app, inv, cite(REG, n + 1, "package api"))


def test_a_missing_file(app: Path, inv: Inventory) -> None:
    assert "does not exist" in verdict(app, inv, cite("api/missing.go", 1, "package api"))
    assert "does not exist" in verdict(app, inv, cite("nodir/x.go", 1, "package api"))


def test_an_empty_or_short_quote(app: Path, inv: Inventory) -> None:
    n = line_of(app, REG, 'r.POST("/api/login"')
    assert "fewer than 8" in verdict(app, inv, cite(REG, n, ""))
    assert "fewer than 8" in verdict(app, inv, cite(REG, n, "r.POS"))  # 5 characters
    assert "fewer than 8" in verdict(app, inv, cite(REG, n, "r . P O S T"))  # 7 non-space
    assert verdict(app, inv, cite(REG, n, 'r.POST("')) is None  # 8 non-space characters


def test_a_quote_from_the_next_line_is_not_on_this_one(app: Path, inv: Inventory) -> None:
    n = line_of(app, REG, 'r.POST("/api/login"')
    nxt = (app / REG).read_text().split("\n")[n].strip()
    assert nxt and "not in api/api.go" in verdict(app, inv, cite(REG, n, nxt))
    assert verdict(app, inv, cite(REG, n + 1, nxt)) is None


def test_a_wrong_symbol_or_part_of_a_word(app: Path, inv: Inventory) -> None:
    n = line_of(app, REG, 'r.POST("/api/login"')
    assert "symbol" in verdict(app, inv, cite(REG, n, 'r.POST("/api/login"', symbol="Logout"))
    assert "symbol" in verdict(app, inv, cite(REG, n, 'r.POST("/api/login"', symbol="Logi"))


def test_a_target_that_is_not_code_path_line(app: Path, inv: Inventory) -> None:
    for t in ("code:api/api.go", "code:api/api.go:x", "code::3", "scenario:x", "code:a.go:12:3"):
        assert verdict(app, inv, {"target": t, "quote": "package api", "step": None}), t
    assert parse_code_target("code:a/b.go:7") == ("a/b.go", 7)
    assert parse_code_target("code:a.go:12:3") is None  # a path with a colon


# ---- forged -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    [
        "../outside.go",
        "api/../../outside.go",
        "/etc/passwd",
        "/Users/someone/x.go",
        "api\\api.go",
        "./api/api.go",
        "api//api.go",
        "api/./api.go",
        "api/api.go\x00.txt",
        "api/\x07api.go",
    ],
)
def test_forged_paths_are_refused(app: Path, inv: Inventory, rel: str) -> None:
    assert verdict(app, inv, cite(rel, 1, "package api")) is not None


def test_a_symlink_out_of_the_root_is_refused(app: Path, inv: Inventory, tmp_path: Path) -> None:
    outside = tmp_path / "secret.go"
    outside.write_text("package secret // top secret\n")
    (app / "link.go").symlink_to(outside)
    assert "symbolic link" in verdict(app, inv, cite("link.go", 1, "package secret"))
    (tmp_path / "out_dir").mkdir()
    (tmp_path / "out_dir" / "z.go").write_text("package z // zzzzzzzz\n")
    (app / "linkdir").symlink_to(tmp_path / "out_dir")
    assert "symbolic link" in verdict(app, inv, cite("linkdir/z.go", 1, "package z"))
    (app / "inner.go").symlink_to(app / "main.go")  # even a link that stays inside
    assert "symbolic link" in verdict(app, inv, cite("inner.go", 1, "package main"))


def test_a_case_variant_is_refused_although_the_filesystem_may_open_it(
    app: Path, inv: Inventory
) -> None:
    assert "differs in case" in verdict(app, inv, cite("API/api.go", 1, "package api"))
    assert "differs in case" in verdict(app, inv, cite("api/API.go", 1, "package api"))
    assert "differs in case" in verdict(app, inv, cite("Main.go", 1, "package main"))


def test_vendor_git_and_env_files_are_refused(app: Path, inv: Inventory) -> None:
    for rel, content in (
        ("vendor/x/x.go", "package x // vendored code\n"),
        (".git/config", "[core] repositoryformatversion = 0\n"),
        (".env", "SECRET_TOKEN=abcdefgh\n"),
        ("node_modules/m/m.go", "package m // node module\n"),
    ):
        p = app / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        reason = verdict(app, inv, cite(rel, 1, content.strip()[:20]))
        assert reason and "not in the source set" in reason, rel


def test_spec_text_quoted_as_code_is_refused(app: Path, inv: Inventory) -> None:
    (app / "REQUIREMENTS.md").write_text("A user signs in with POST /api/login.\n")
    reason = verdict(app, inv, cite("REQUIREMENTS.md", 1, "A user signs in with POST /api/login"))
    assert reason and "not in the source set" in reason


def test_a_directory_and_a_big_file_are_refused(app: Path, inv: Inventory) -> None:
    assert "not a file" in verdict(app, inv, cite("api", 1, "package api"))
    (app / "big.go").write_text("package big\n" + "// " + "x" * 1_000_100 + "\n")
    assert "larger than 1 MB" in verdict(app, inv, cite("big.go", 1, "package big"))


# ---- determinism and reading --------------------------------------------------------------


def test_the_checker_is_deterministic_and_writes_nothing(
    app: Path, inv: Inventory, tmp_path: Path
) -> None:
    def snap(root: Path) -> dict[str, str]:
        return {
            p.relative_to(root).as_posix(): sha256_bytes(p.read_bytes()) if p.is_file() else "dir"
            for p in sorted(root.rglob("*"))
        }

    before = snap(tmp_path)
    n = line_of(app, REG, 'r.POST("/api/login"')
    cases = [
        cite(REG, n, 'r.POST("/api/login"'),
        cite(REG, 0, "package api"),
        cite("../x.go", 1, "package x"),
        cite("API/api.go", 1, "package api"),
    ]
    first = [verdict(app, inv, c) for c in cases]
    for _ in range(10):
        assert [verdict(app, inv, c) for c in cases] == first
    assert first[0] is None and all(isinstance(r, str) for r in first[1:])
    assert snap(tmp_path) == before


def test_without_an_inventory_the_source_set_is_just_the_code_extensions(app: Path) -> None:
    n = line_of(app, REG, 'r.POST("/api/login"')
    assert check_code(app, None, cite(REG, n, 'r.POST("/api/login"')) is None
    assert check_code(app, None, cite("go.mod", 1, "module example.com/wf")) is not None
