"""Local module install with pinning (lattice/modules, spike 2 M-L): install shows the
manifest and refuses without confirmation, list / info / uninstall, the lock file, tamper
detection on every load, index resolution, git URLs refused. No network, no Docker."""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest

from lattice.modules import (
    LOCK,
    MANIFEST,
    GitSourceNotSupported,
    ModuleError,
    Refused,
    install,
    load_project_engines,
    read_lock,
    read_manifest,
    register_modules,
    resolve_source,
    tree_hash,
    uninstall,
    verify,
)
from lattice.modules.__main__ import main
from lattice.spec.engines import REGISTRY

ROOT = Path(__file__).resolve().parents[2]
MARIADB = ROOT / "docs/examples/engines/mariadb.toml"


def make_module(
    parent: Path, name: str = "mariadb-engine", version: str = "0.1.0", inline: bool = False
) -> Path:
    d = parent / name
    d.mkdir(parents=True)
    if inline:
        body = MARIADB.read_text()
        (d / MANIFEST).write_text(
            f'name = "{name}"\nversion = "{version}"\nkind = "engine"\n\n[engine]\n'
            + "\n".join(line for line in body.splitlines() if not line.startswith("#"))
            .replace("\n[drivers]", "\n[engine.drivers]")
            .replace("\n[sql]", "\n[engine.sql]")
            .replace("\n[capabilities]", "\n[engine.capabilities]")
            .replace("\n[server]", "\n[engine.server]")
            + "\n"
        )
    else:
        (d / MANIFEST).write_text(
            f'name = "{name}"\nversion = "{version}"\nkind = "engine"\n'
            'description = "MariaDB for the checker"\n'
            'descriptor = "engine.toml"\n'
        )
        (d / "engine.toml").write_text(MARIADB.read_text())
    (d / "README.md").write_text("MariaDB, no code.\n")
    return d


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    return p


@pytest.fixture(autouse=True)
def _scoped_registry():
    with REGISTRY.scoped():
        yield


def no(_q: str) -> bool:
    return False


# ---- the manifest ---------------------------------------------------------------------------


def test_a_manifest_reads_with_its_descriptor(tmp_path):
    m = read_manifest(make_module(tmp_path))
    assert (m.name, m.version, m.kind) == ("mariadb-engine", "0.1.0", "engine")
    assert m.engine is not None and m.engine.name == "mariadb"
    assert m.provides == ("engine:mariadb",)
    assert m.requires == ("pymysql>=1.1",)
    inline = read_manifest(make_module(tmp_path / "i", inline=True))
    assert inline.engine is not None and inline.engine.server.image == "mariadb:11"


@pytest.mark.parametrize(
    "text,message",
    [
        (
            'name = "x"\nversion = "1"\nkind = "resource"\n',
            "kind must be one of engine, custom, not 'resource'",
        ),
        ('name = "X"\nversion = "1"\nkind = "custom"\n', "'X' is not a valid module name"),
        ('version = "1"\nkind = "custom"\n', "missing required field 'name'"),
        ('name = "x"\nversion = "1"\nkind = "custom"\nhooks = []\n', "unknown field 'hooks'"),
        (
            'name = "x"\nversion = "1"\nkind = "engine"\n',
            'an engine module gives exactly one of descriptor = "<file>" or an [engine] table',
        ),
        (
            'name = "x"\nversion = "1"\nkind = "custom"\ndescriptor = "e.toml"\n',
            "only an engine module has a descriptor",
        ),
        (
            'name = "x"\nversion = "1"\nkind = "engine"\ndescriptor = "../e.toml"\n',
            "descriptor '../e.toml' is outside the module",
        ),
    ],
)
def test_manifest_errors(tmp_path, text, message):
    d = tmp_path / "m"
    d.mkdir()
    (d / MANIFEST).write_text(text)
    with pytest.raises(ModuleError, match=message.replace("[", r"\[").replace("(", r"\(")):
        read_manifest(d)


# ---- install --------------------------------------------------------------------------------


def test_install_refuses_without_confirmation_and_writes_nothing(tmp_path, project):
    src = make_module(tmp_path)
    shown: list[str] = []
    with pytest.raises(Refused, match="was not confirmed"):
        install(project, str(src), confirm=no, out=shown.append)
    assert "module mariadb-engine 0.1.0 (engine)" in shown[0]
    assert "engine:   mariadb (--database mariadb), adapter mysql (no code" in shown[0]
    assert 'descriptor = "engine.toml"' in shown[0]  # the manifest itself is printed
    assert f"sha256:   {tree_hash(src)}" in shown[0]
    assert not (project / ".lattice").exists()
    assert not (project / LOCK).exists()


def test_the_cli_refuses_without_yes_when_stdin_is_not_a_terminal(
    tmp_path, project, capsys, monkeypatch
):
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))  # not a TTY: the "y" is never read
    src = make_module(tmp_path)
    rc = main(["install", str(src), "--project", str(project)])
    out, err = capsys.readouterr()
    assert rc == 2
    assert "module mariadb-engine 0.1.0 (engine)" in out
    assert err.startswith("refused: not installed: mariadb-engine 0.1.0 was not confirmed")
    assert not (project / ".lattice").exists() and not (project / LOCK).exists()


def test_an_interactive_yes_installs(tmp_path, project, monkeypatch):
    class Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", Tty("y\n"))
    monkeypatch.setattr("builtins.input", lambda q: "y")
    assert main(["install", str(make_module(tmp_path)), "--project", str(project)]) == 0
    assert [e.name for e in read_lock(project)] == ["mariadb-engine"]


def test_install_with_yes_copies_the_tree_and_writes_the_lock(tmp_path, project):
    src = make_module(tmp_path)
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "junk.pyc").write_bytes(b"\0")
    entry = install(project, str(src), yes=True, out=lambda s: None)
    dest = project / ".lattice/modules/mariadb-engine"
    assert sorted(p.name for p in dest.iterdir()) == sorted([MANIFEST, "README.md", "engine.toml"])
    assert entry.sha256 == tree_hash(dest)
    lock = json.loads((project / LOCK).read_text())
    assert lock == {
        "version": 1,
        "modules": [
            {
                "name": "mariadb-engine",
                "version": "0.1.0",
                "kind": "engine",
                "sha256": entry.sha256,
                "source": str(src.resolve()),
            }
        ],
    }
    with pytest.raises(ModuleError, match="already installed \\(version 0.1.0\\): uninstall"):
        install(project, str(src), yes=True, out=lambda s: None)


def test_the_lock_hash_covers_file_contents_and_paths(tmp_path):
    a = make_module(tmp_path / "a")
    b = make_module(tmp_path / "b")
    assert tree_hash(a) == tree_hash(b)
    (b / "README.md").write_text("MariaDB, no code!\n")  # same length, one byte differs
    assert tree_hash(a) != tree_hash(b)
    c = make_module(tmp_path / "c")
    (c / "README.md").rename(c / "READ_ME.md")
    assert tree_hash(a) != tree_hash(c)
    (a / ".DS_Store").write_bytes(b"finder")
    assert tree_hash(a) == tree_hash(make_module(tmp_path / "d"))


def test_a_symlink_in_a_module_is_refused(tmp_path, project):
    src = make_module(tmp_path)
    (src / "link").symlink_to(src / "README.md")
    with pytest.raises(ModuleError, match="link is a symbolic link"):
        install(project, str(src), yes=True, out=lambda s: None)


def test_file_urls_install_and_git_urls_are_refused(tmp_path, project):
    src = make_module(tmp_path)
    entry = install(project, src.resolve().as_uri(), yes=True, out=lambda s: None)
    assert entry.name == "mariadb-engine"
    for url in (
        "git+https://example.com/m.git",
        "https://github.com/acme/m.git",
        "git@github.com:acme/m.git",
        "ssh://git@host/m",
        "https://example.com/m",
    ):
        with pytest.raises(
            GitSourceNotSupported,
            match="installing from a git URL is not "
            "implemented yet: only local paths and file:// URLs are",
        ):
            resolve_source(url)


def test_an_engine_name_clash_is_refused_before_asking(tmp_path, project):
    src = make_module(tmp_path)
    text = (src / "engine.toml").read_text().replace('name = "mariadb"', 'name = "mysql"')
    (src / "engine.toml").write_text(text)
    asked: list[str] = []
    with pytest.raises(ModuleError, match="engine 'mysql' is built in"):
        install(project, str(src), confirm=lambda q: asked.append(q) or True, out=lambda s: None)
    assert asked == []


# ---- index resolution -------------------------------------------------------------------------


def test_a_name_resolves_through_an_index_directory(tmp_path, project):
    src = make_module(tmp_path / "mods")
    index = tmp_path / "index"
    index.mkdir()
    (index / "mariadb-engine.toml").write_text(
        f'name = "mariadb-engine"\nversion = "0.1.0"\nsource = "../mods/mariadb-engine"\n'
        f'sha256 = "{tree_hash(src)}"\n'
    )
    entry = install(project, "mariadb-engine", index=index, yes=True, out=lambda s: None)
    assert entry.source == str(index / "mariadb-engine.toml")
    assert entry.sha256 == tree_hash(src)


def test_an_index_pin_that_does_not_match_is_refused(tmp_path, project):
    make_module(tmp_path / "mods")
    index = tmp_path / "index"
    index.mkdir()
    (index / "mariadb-engine.toml").write_text(
        'name = "mariadb-engine"\nversion = "0.1.0"\nsource = "../mods/mariadb-engine"\n'
        'sha256 = "' + "0" * 64 + '"\n'
    )
    with pytest.raises(ModuleError, match="pins sha256 0000.*, the source tree hashes to"):
        install(project, "mariadb-engine", index=index, yes=True, out=lambda s: None)
    (index / "mariadb-engine.toml").write_text(
        'name = "mariadb-engine"\nversion = "0.2.0"\nsource = "../mods/mariadb-engine"\n'
    )
    with pytest.raises(
        ModuleError, match="says mariadb-engine 0.2.0, the source is mariadb-engine 0.1.0"
    ):
        install(project, "mariadb-engine", index=index, yes=True, out=lambda s: None)
    with pytest.raises(ModuleError, match="no module 'other'"):
        install(project, "other", index=index, yes=True, out=lambda s: None)
    (index / "gitmod.toml").write_text(
        'name = "gitmod"\nversion = "1"\nsource = "git+https://example.com/gitmod.git"\n'
    )
    with pytest.raises(GitSourceNotSupported):
        install(project, "gitmod", index=index, yes=True, out=lambda s: None)
    assert not (project / LOCK).exists()


# ---- every load re-hashes ---------------------------------------------------------------------


def test_a_tampered_tree_is_refused_on_load(tmp_path, project):
    install(project, str(make_module(tmp_path)), yes=True, out=lambda s: None)
    states = register_modules(project)
    assert [s.status for s in states] == ["ok"]
    assert REGISTRY.entry("mariadb").source == "module"
    REGISTRY.remove("mariadb")
    readme = project / ".lattice/modules/mariadb-engine/README.md"
    data = bytearray(readme.read_bytes())
    data[0] ^= 0x01  # one byte
    readme.write_bytes(bytes(data))
    assert [s.status for s in verify(project)] == ["changed"]
    with pytest.raises(
        ModuleError,
        match="module 'mariadb-engine' \\(0.1.0\\) is changed: its "
        "tree hashes to [0-9a-f]{64}, the lock says [0-9a-f]{64}; it is refused",
    ):
        register_modules(project, strict=True)
    assert REGISTRY.entry("mariadb") is None
    loaded = load_project_engines(project)
    assert [s.status for s in loaded.modules] == ["changed"]
    assert loaded.errors and "is changed" in loaded.errors[0]
    assert REGISTRY.entry("mariadb") is None


def test_a_changed_module_is_dropped_from_the_registry_when_not_strict(tmp_path, project):
    install(project, str(make_module(tmp_path)), yes=True, out=lambda s: None)
    register_modules(project)
    assert REGISTRY.entry("mariadb") is not None
    (project / ".lattice/modules/mariadb-engine/extra.txt").write_text("added")
    states = register_modules(project, strict=False)
    assert [s.status for s in states] == ["changed"]
    assert REGISTRY.entry("mariadb") is None


def test_a_missing_tree_is_reported(tmp_path, project):
    install(project, str(make_module(tmp_path)), yes=True, out=lambda s: None)
    shutil.rmtree(project / ".lattice/modules/mariadb-engine")
    assert [s.status for s in verify(project)] == ["missing"]


def test_the_checker_refuses_a_tampered_module(tmp_path, project, capsys):
    from lattice.spec.__main__ import main as spec_main

    install(project, str(make_module(tmp_path)), yes=True, out=lambda s: None)
    (project / ".lattice/modules/mariadb-engine/engine.toml").write_text("tampered = true\n")
    rc = spec_main(
        [
            "check",
            "--spec",
            "m:S",
            "--app",
            "m:a",
            "--db",
            "m:d",
            "--database",
            "mariadb",
            "--modules",
            str(project),
        ]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert err.startswith("error: ModuleError: module 'mariadb-engine' (0.1.0) is changed")


def test_the_checker_loads_an_intact_module(tmp_path, project, capsys):
    from lattice.spec.__main__ import main as spec_main

    install(project, str(make_module(tmp_path)), yes=True, out=lambda s: None)
    rc = spec_main(
        [
            "check",
            "--spec",
            "no_such_module_q:S",
            "--app",
            "m:a",
            "--db",
            "m:d",
            "--database",
            "mariadb",
            "--modules",
            str(project),
        ]
    )
    assert rc == 2  # past argparse: mariadb was a choice; the spec module does not exist
    assert "No module named 'no_such_module_q'" in capsys.readouterr().err


# ---- list / info / uninstall ------------------------------------------------------------------


def test_list_info_uninstall_through_the_cli(tmp_path, project, capsys):
    assert main(["list", "--project", str(project)]) == 0
    assert "no modules installed" in capsys.readouterr().out
    assert main(["install", str(make_module(tmp_path)), "--project", str(project), "--yes"]) == 0
    capsys.readouterr()
    assert main(["list", "--project", str(project)]) == 0
    line = capsys.readouterr().out.splitlines()[0]
    assert line.split()[:4] == ["mariadb-engine", "0.1.0", "engine", "ok"]
    assert main(["list", "--project", str(project), "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)["modules"]
    assert [(m["name"], m["status"]) for m in listed] == [("mariadb-engine", "ok")]
    assert main(["info", "mariadb-engine", "--project", str(project)]) == 0
    info = capsys.readouterr().out
    assert info.startswith("mariadb-engine 0.1.0 (engine): ok")
    assert 'descriptor = "engine.toml"' in info
    (project / ".lattice/modules/mariadb-engine/README.md").write_text("x")
    assert main(["list", "--project", str(project)]) == 1
    assert "changed" in capsys.readouterr().out
    assert main(["info", "mariadb-engine", "--project", str(project)]) == 1
    assert "sha256 (now):" in capsys.readouterr().out
    assert main(["info", "nope", "--project", str(project)]) == 2
    assert main(["uninstall", "mariadb-engine", "--project", str(project)]) == 0
    assert not (project / ".lattice/modules/mariadb-engine").exists()
    assert not (project / LOCK).exists()  # the last entry gone: no lock file
    assert main(["uninstall", "mariadb-engine", "--project", str(project)]) == 2


def test_uninstall_keeps_the_other_entries(tmp_path, project):
    install(project, str(make_module(tmp_path / "a")), yes=True, out=lambda s: None)
    custom = tmp_path / "b" / "audit"
    custom.mkdir(parents=True)
    (custom / MANIFEST).write_text(
        'name = "audit"\nversion = "2.0"\nkind = "custom"\nprovides = ["check:audit"]\n'
    )
    install(project, str(custom), yes=True, out=lambda s: None)
    assert [e.name for e in read_lock(project)] == ["audit", "mariadb-engine"]
    uninstall(project, "mariadb-engine")
    assert [e.name for e in read_lock(project)] == ["audit"]
    with pytest.raises(ModuleError, match="is not installed"):
        uninstall(project, "mariadb-engine")


def test_the_lattice_command_dispatches_module(tmp_path, capsys):
    from lattice import cli

    assert cli.main(["module", "list", "--project", str(tmp_path)]) == 0
    assert "no modules installed" in capsys.readouterr().out
