"""Module indexes that name pinned git sources, and an index that is itself a pinned git
repository (marketplace prereg 2.6; M-index I1-I8). ``file://`` repositories only."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from lattice.modules import (
    LOCK,
    GitSourceNotSupported,
    ModuleError,
    install,
    install_many,
    read_lock,
    tree_hash,
)
from lattice.spec.engines import REGISTRY
from tests.modules import gitfix as gf

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture(autouse=True)
def _scoped_registry():
    with REGISTRY.scoped():
        yield


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    return p


def quiet(_s: str) -> None:
    return None


class World:
    """Repos a (engine mariadb), b (custom), c (a symlink) and an index repo over them."""

    def __init__(self, root: Path) -> None:
        self.root = root
        r = root / "repos"
        self.a = r / "a"
        self.b = r / "b"
        self.c = r / "c"
        self.sa = gf.make_repo(self.a, gf.module_files("mod-a", "0.1.0"))
        self.sb = gf.make_repo(
            self.b, {"lattice-module.toml": 'name = "mod-b"\nversion = "1.0"\nkind = "custom"\n'}
        )
        gf.init_repo(self.c)
        gf.write_files(
            self.c, {"lattice-module.toml": 'name = "mod-c"\nversion = "1"\nkind = "custom"\n'}
        )
        (self.c / "link").symlink_to("/etc/passwd")
        gf.git(self.c, "add", "-A")
        gf.git(self.c, "commit", "-q", "-m", "c")
        self.sc = gf.git(self.c, "rev-parse", "HEAD")
        self.d = r / "d"
        self.sd = gf.make_repo(self.d, gf.module_files("mod-d", "0.1.0"))
        self.wrong = "0" * 64
        self.idx = r / "idx"
        self.sx = gf.make_repo(
            self.idx,
            {
                "mod-a.toml": self.entry("mod-a", "0.1.0", gf.url(self.a, self.sa)),
                "mod-b.toml": self.entry("mod-b", "1.0", gf.url(self.b, self.sb)),
                "mod-c.toml": self.entry("mod-c", "1", gf.url(self.c, self.sc)),
                "mod-d.toml": self.entry("mod-d", "0.1.0", gf.url(self.d, self.sd), self.wrong),
                "mod-e.toml": self.entry("mod-e", "1", "../a"),
                "mod-f.toml": self.entry("mod-f", "1", f"git+file://{self.a}"),
            },
        )

    @staticmethod
    def entry(name: str, version: str, source: str, sha256: str | None = None) -> str:
        pin = f'sha256 = "{sha256}"\n' if sha256 else ""
        return f'name = "{name}"\nversion = "{version}"\nsource = "{source}"\n{pin}'

    @property
    def index(self) -> str:
        return gf.url(self.idx, self.sx)


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def test_a_git_index_installs_by_name_and_records_commit_and_index(world, project):
    entries = install_many(project, ["mod-a", "mod-b"], index=world.index, yes=True, out=quiet)
    assert [e.name for e in entries] == ["mod-a", "mod-b"]
    lock = json.loads((project / LOCK).read_text())["modules"]
    assert [m["name"] for m in lock] == ["mod-a", "mod-b"]
    assert [list(m) for m in lock] == [
        ["name", "version", "kind", "sha256", "source", "commit", "index"]
    ] * 2
    assert lock[0]["commit"] == world.sa and lock[1]["commit"] == world.sb
    assert lock[0]["index"] == world.index
    assert lock[0]["source"] == gf.url(world.a, world.sa)


def test_a_symlink_in_one_module_refuses_the_whole_install(world, project):
    with pytest.raises(ModuleError, match="link is a symbolic link"):
        install_many(project, ["mod-a", "mod-b", "mod-c"], index=world.index, yes=True, out=quiet)
    assert not (project / ".lattice").exists() and not (project / LOCK).exists()


def test_a_wrong_pin_in_one_entry_refuses_the_whole_install(world, project):
    with pytest.raises(ModuleError, match="pins sha256 0000.*, the source tree hashes to"):
        install_many(project, ["mod-a", "mod-b", "mod-d"], index=world.index, yes=True, out=quiet)
    assert not (project / ".lattice").exists() and not (project / LOCK).exists()


def test_an_installed_module_refuses_the_batch_and_keeps_the_lock(world, project):
    install(project, "mod-a", index=world.index, yes=True, out=quiet)
    before = (project / LOCK).read_bytes()
    with pytest.raises(ModuleError, match="module 'mod-a' is already installed"):
        install_many(project, ["mod-a", "mod-b"], index=world.index, yes=True, out=quiet)
    assert (project / LOCK).read_bytes() == before
    assert not (project / ".lattice/modules/mod-b").exists()


def test_a_name_twice_is_refused(world, project):
    with pytest.raises(ModuleError, match="module mod-a appears twice in this install"):
        install_many(project, ["mod-a", "mod-a"], index=world.index, yes=True, out=quiet)


def test_a_directory_index_may_name_a_pinned_git_source(tmp_path, world, project):
    index = tmp_path / "dirindex"
    index.mkdir()
    (index / "mod-a.toml").write_text(world.entry("mod-a", "0.1.0", gf.url(world.a, world.sa)))
    (index / "mod-u.toml").write_text(world.entry("mod-u", "1", f"git+file://{world.a}"))
    entry = install(project, "mod-a", index=index, yes=True, out=quiet)
    assert entry.commit == world.sa and entry.index == str(index.resolve())
    assert entry.source == gf.url(world.a, world.sa)
    with pytest.raises(GitSourceNotSupported, match="pin a full lowercase commit sha"):
        install(project, "mod-u", index=index, yes=True, out=quiet)


def test_a_git_index_names_pinned_git_sources_only(world, project):
    with pytest.raises(ModuleError, match="a git index names pinned git sources only"):
        install(project, "mod-e", index=world.index, yes=True, out=quiet)
    with pytest.raises(GitSourceNotSupported, match="pin a full lowercase commit sha"):
        install(project, "mod-f", index=world.index, yes=True, out=quiet)
    assert not (project / ".lattice").exists()


@pytest.mark.parametrize("ref", ["main", "v1", "abc1234"])
def test_an_unpinned_index_is_refused(world, project, ref):
    with pytest.raises(GitSourceNotSupported, match="pin a full lowercase commit sha"):
        install(project, "mod-a", index=f"git+file://{world.idx}@{ref}", yes=True, out=quiet)
    assert not (project / ".lattice").exists()


def test_the_index_and_the_module_hash_like_directories(world, project):
    entry = install(project, "mod-a", index=world.index, yes=True, out=quiet)
    assert entry.sha256 == tree_hash(project / ".lattice/modules/mod-a")
    assert [e.name for e in read_lock(project)] == ["mod-a"]
