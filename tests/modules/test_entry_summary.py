"""The install summary announces a custom module's ``entry`` as CODE (HD-2, pre-registered in
``docs/results/2026-10-16-hardening-findings-prereg.md``, decision D2 and probe P4).

``entry`` is a Python module the manifest declares (``main(argv)``); nothing imports it when the
module is installed, so the line says what it is, not when it runs. A manifest without an
``entry`` prints exactly what it printed before. A value that is not a dotted name is shown
JSON-quoted, so it cannot forge further summary lines.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lattice import modules as mods

ROOT = Path(__file__).resolve().parents[2]
CODE = "(CODE: a Python module, main(argv); not imported by install)"


def custom(tmp_path: Path, entry_line: str = "", extra: str = "") -> Path:
    d = tmp_path / "m"
    d.mkdir()
    (d / mods.MANIFEST).write_text(
        f'name = "custom-mod"\nversion = "1"\nkind = "custom"\n{extra}{entry_line}'
    )
    return d


def summary(d: Path, **kw: object) -> list[str]:
    m = mods.read_manifest(d)
    return mods.describe(m, mods.tree_hash(d), "<src>", **kw).split("\n")  # type: ignore[arg-type]


def test_a_dotted_entry_is_announced_as_code_right_after_requires(tmp_path: Path) -> None:
    lines = summary(custom(tmp_path, 'entry = "evilentry"\n'))
    i = next(n for n, ln in enumerate(lines) if ln.startswith("  requires:"))
    assert lines[i + 1] == f"  entry:    evilentry {CODE}"
    assert sum(ln.startswith("  entry:") for ln in lines) == 1
    assert lines[i + 2] == ""  # the blank line before the manifest text


def test_the_security_module_summary_gains_exactly_one_line() -> None:
    d = ROOT / "lattice/security/module"
    lines = summary(d)
    i = next(n for n, ln in enumerate(lines) if ln.startswith("  requires:"))
    assert lines[i] == "  requires: (nothing) (pip; not installed by Lattice)"
    assert lines[i + 1] == f"  entry:    lattice.security {CODE}"
    without = lines[: i + 1] + lines[i + 2 :]
    assert not any(ln.startswith("  entry:") for ln in without)


def test_a_manifest_without_entry_prints_exactly_as_before(tmp_path: Path) -> None:
    d = custom(tmp_path, extra='description = "a described module"\nprovides = ["audit"]\n')
    m = mods.read_manifest(d)
    sha = mods.tree_hash(d)
    want = "\n".join(
        [
            "module custom-mod 1 (custom)",
            "  a described module",
            "  source:   <src>",
            f"  sha256:   {sha}",
            "  files:    1",
            "  provides: audit",
            "  requires: (nothing) (pip; not installed by Lattice)",
            "",
            f"--- {mods.MANIFEST} ---",
            m.text.rstrip(),
            "---",
        ]
    )
    assert mods.describe(m, sha, "<src>") == want


@pytest.mark.parametrize(
    ("toml_value", "shown"),
    [
        ('"a\\nb"', '"a\\nb"'),  # a newline
        ('""', '""'),  # empty
        ('"x\\n  files:    99"', '"x\\n  files:    99"'),  # forged summary line
        ('"a b"', '"a b"'),
        ('"pkg."', '"pkg."'),
        ('"1abc"', '"1abc"'),
        ('"a..b"', '"a..b"'),
        ('"é"', '"\\u00e9"'),
    ],
)
def test_a_non_identifier_entry_is_json_quoted(tmp_path: Path, toml_value: str, shown: str) -> None:
    lines = summary(custom(tmp_path, f"entry = {toml_value}\n"))
    entries = [ln for ln in lines if ln.startswith("  entry:")]
    assert entries == [f"  entry:    {shown} {CODE}"]
    # one physical line: a newline in the value cannot start another summary line
    assert sum(ln.startswith("  files:") for ln in lines) == 1


@pytest.mark.parametrize("dotted", ["a", "pkg.mod", "_x.y_2.Z", "lattice.security"])
def test_a_dotted_name_is_shown_bare(tmp_path: Path, dotted: str) -> None:
    lines = summary(custom(tmp_path, f'entry = "{dotted}"\n'))
    assert f"  entry:    {dotted} {CODE}" in lines


def test_the_engine_adapter_wording_is_unchanged(tmp_path: Path) -> None:
    d = tmp_path / "e"
    d.mkdir()
    (d / mods.MANIFEST).write_text(
        'name = "mariadb-engine"\nversion = "0.1.0"\nkind = "engine"\ndescriptor = "engine.toml"\n'
    )
    text = (ROOT / "docs/examples/engines/mariadb.toml").read_text()
    (d / "engine.toml").write_text(text.replace('adapter = "mysql"', 'adapter = "evil:Adapter"'))
    lines = summary(d)
    engine = next(ln for ln in lines if ln.startswith("  engine:"))
    assert engine.endswith("(CODE: imported into the checker when the engine is used)")
    assert not any(ln.startswith("  entry:") for ln in lines)


def test_install_prints_the_entry_line_and_still_asks(tmp_path: Path) -> None:
    src = custom(tmp_path, 'entry = "evilentry"\n')
    project = tmp_path / "proj"
    project.mkdir()
    out: list[str] = []
    with pytest.raises(mods.Refused):
        mods.install(project, str(src), confirm=lambda q: False, out=out.append)
    assert f"  entry:    evilentry {CODE}" in out[0].split("\n")
    assert not (project / ".lattice").exists()  # nothing installed
