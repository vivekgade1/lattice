"""lattice import inventory (Spike C, D1): the capture writes only --out, refuses an --out under
the app root, hashes the source set, validates the workflows file and is deterministic."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from lattice.importer.cli import main as importer_main
from lattice.importer.inventory import capture
from lattice.importer.inventory import main as inventory_main
from lattice.spec.implementation import (
    ImplementationError,
    canonical_json,
    load_inventory,
    sha256_bytes,
    source_files,
)

FIX = Path(__file__).resolve().parents[1] / "spec" / "fixtures" / "implementation"
APP = FIX / "app"
EXTRACT = json.loads((FIX / "extract.json").read_text())
WORKFLOWS = FIX / "workflows.json"


def tree(root: Path) -> dict[str, str]:
    """Every path under root (hidden files included): relative path -> sha256 or 'dir'."""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        out[rel] = "dir" if p.is_dir() else sha256_bytes(p.read_bytes())
    return out


@pytest.fixture
def app(tmp_path: Path) -> Path:
    dest = tmp_path / "app"
    shutil.copytree(APP, dest)
    return dest


def test_capture_writes_three_files_and_nothing_in_the_app(app: Path, tmp_path: Path) -> None:
    before = tree(app)
    out = tmp_path / "inv"
    meta = capture(app, out, extract=EXTRACT, workflows=WORKFLOWS)
    assert sorted(p.name for p in out.iterdir()) == [
        "extract.json",
        "inventory.json",
        "workflows.json",
    ]
    assert tree(app) == before  # the target is untouched
    assert meta["schema"] == "lattice.implementation-inventory/1"
    assert meta["frontend"] == "go_gin"
    ex = (out / "extract.json").read_bytes()
    assert ex == canonical_json(EXTRACT).encode() and ex.endswith(b"\n")
    assert meta["extract_sha256"] == sha256_bytes(ex)
    assert meta["workflows_sha256"] == sha256_bytes(WORKFLOWS.read_bytes())
    assert (out / "workflows.json").read_bytes() == WORKFLOWS.read_bytes()
    assert json.loads((out / "inventory.json").read_text()) == meta


def test_inventory_json_is_canonical_and_holds_no_absolute_path_or_time(
    app: Path, tmp_path: Path
) -> None:
    out = tmp_path / "inv"
    capture(app, out, extract=EXTRACT)
    text = (out / "inventory.json").read_text()
    assert text == canonical_json(json.loads(text))
    assert str(tmp_path) not in text and "/Users" not in text and "/private" not in text
    meta = json.loads(text)
    assert meta["workflows_sha256"] is None
    assert [f["path"] for f in meta["source"]["files"]] == sorted(
        f["path"] for f in meta["source"]["files"]
    )


def test_two_captures_are_byte_identical_even_from_another_root(app: Path, tmp_path: Path) -> None:
    other = tmp_path / "elsewhere" / "app"
    shutil.copytree(APP, other)
    a, b = tmp_path / "a", tmp_path / "b"
    capture(app, a, extract=EXTRACT, workflows=WORKFLOWS)
    capture(other, b, extract=EXTRACT, workflows=WORKFLOWS)
    assert tree(a) == tree(b)


def test_source_set_is_go_files_and_modules_minus_skipped_directories(app: Path) -> None:
    for rel in (
        "vendor/x/x.go",
        "build/y.go",
        ".hidden/z.go",
        "testdata/t.go",
        "node_modules/n.go",
    ):
        p = app / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("package x\n")
    (app / "notes.txt").write_text("not source\n")
    (app / "go.sum").write_text("")
    files = source_files(app)
    assert files == sorted(files)
    assert "main.go" in files and "go.mod" in files and "go.sum" in files
    assert "api/api.go" in files and "models/models.go" in files
    assert not any(
        f.startswith(("vendor/", "build/", ".hidden/", "testdata/", "node_modules/")) for f in files
    )
    assert "notes.txt" not in files


def test_symbolic_links_are_not_part_of_the_source_set(app: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.go"
    outside.write_text("package outside\n")
    (app / "link.go").symlink_to(outside)
    assert "link.go" not in source_files(app)


def test_out_under_the_app_root_is_refused_and_nothing_is_written(app: Path) -> None:
    before = tree(app)
    for out in (app / "inv", app / "api" / "inv", app):
        with pytest.raises(ValueError, match="under the app root"):
            capture(app, out, extract=EXTRACT)
    assert tree(app) == before
    assert inventory_main([str(app), "--out", str(app / "inv")]) == 2
    assert tree(app) == before


def test_the_cli_refuses_an_out_under_the_app_through_the_importer_dispatch(
    app: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = tree(app)
    assert importer_main(["inventory", str(app), "--out", str(app / ".inv")]) == 2
    assert "under the app root" in capsys.readouterr().err
    assert tree(app) == before


def test_a_workflows_file_with_another_schema_is_bad_workflows(app: Path, tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "lattice.workflows/2", "flows": []}))
    with pytest.raises(ImplementationError) as e:
        capture(app, tmp_path / "inv", extract=EXTRACT, workflows=bad)
    assert e.value.code == "bad_workflows" and str(e.value).startswith("bad_workflows")
    assert not (tmp_path / "inv").exists()
    assert inventory_main([str(app), "--out", str(tmp_path / "inv"), "--workflows", str(bad)]) == 2
    notjson = tmp_path / "x.json"
    notjson.write_text("{")
    with pytest.raises(ImplementationError):
        capture(app, tmp_path / "inv2", extract=EXTRACT, workflows=notjson)


def test_recapture_without_workflows_removes_the_old_copy(app: Path, tmp_path: Path) -> None:
    out = tmp_path / "inv"
    capture(app, out, extract=EXTRACT, workflows=WORKFLOWS)
    capture(app, out, extract=EXTRACT)
    assert not (out / "workflows.json").exists()
    inv = load_inventory(out, app)
    assert inv.workflows is None


def test_a_captured_inventory_loads_and_is_not_stale(app: Path, tmp_path: Path) -> None:
    capture(app, tmp_path / "inv", extract=EXTRACT, workflows=WORKFLOWS)
    inv = load_inventory(tmp_path / "inv", app)
    assert inv.stale is None
    assert inv.workflows is not None and inv.workflows["mode"] == "static"


def test_real_extraction_of_the_fixture_app_is_the_committed_extract(tmp_path: Path) -> None:
    """The committed extract.json is what Spike A's extractor reads from app/ (needs go)."""
    if shutil.which("go") is None:
        pytest.skip("go is not installed")
    out = tmp_path / "inv"
    assert inventory_main([str(APP), "--out", str(out)]) == 0
    assert json.loads((out / "extract.json").read_text()) == EXTRACT
