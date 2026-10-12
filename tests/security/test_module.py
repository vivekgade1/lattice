"""The module packaging (S4): the security module is a valid `custom` module with a CLI
`entry`, installable with spike 2's installer and listed."""

from __future__ import annotations

from pathlib import Path

from lattice import modules
from lattice.modules import read_manifest

MODULE_DIR = Path(__file__).resolve().parents[2] / "lattice" / "security" / "module"


def test_manifest_is_a_valid_custom_module():
    m = read_manifest(MODULE_DIR)
    assert m.name == "security"
    assert m.kind == "custom"
    assert "attack" in m.provides
    assert m.entry == "lattice.security"


def test_entry_is_rejected_for_an_engine_module(tmp_path):
    (tmp_path / "lattice-module.toml").write_text(
        'name = "x"\nversion = "0.1.0"\nkind = "engine"\nentry = "x"\n[engine]\nname = "x"\n'
    )
    try:
        read_manifest(tmp_path)
    except modules.ModuleError as err:
        assert "entry" in str(err)
    else:
        raise AssertionError("an engine module with an entry should be refused")


def test_install_and_list(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    entry = modules.install(project, str(MODULE_DIR), yes=True, out=lambda _m: None)
    assert entry.name == "security"
    assert entry.kind == "custom"
    states = modules.verify(project)
    assert len(states) == 1
    assert states[0].status == "ok"
    assert states[0].entry.kind == "custom"
    # list shows it (its manifest re-reads with the entry field)
    assert states[0].manifest is not None
    assert states[0].manifest.entry == "lattice.security"
