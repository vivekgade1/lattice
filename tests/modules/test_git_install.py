"""Installing a module from a git repository pinned to a commit (lattice/modules/git.py,
marketplace prereg 2.1-2.5): the accepted and refused forms, the isolated git invocation
(exact arguments and environment), what the install writes, and that nothing of the fetched
repository runs. ``file://`` repositories only: no network."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from lattice.modules import (
    LOCK,
    GitSourceNotSupported,
    ModuleError,
    install,
    install_many,
    read_lock,
    register_modules,
    resolve_source,
    tree_hash,
    uninstall,
    verify,
)
from lattice.modules import git as mgit
from lattice.modules.__main__ import main
from lattice.spec.engines import REGISTRY
from tests.modules import gitfix as gf

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

C1 = "a" * 40


@pytest.fixture(autouse=True)
def _scoped_registry():
    with REGISTRY.scoped():
        yield


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "repos" / "maria"
    return path, gf.make_repo(path)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    return p


@pytest.fixture
def tmpdir_env(tmp_path: Path, monkeypatch) -> Path:
    t = tmp_path / "tmp"
    t.mkdir()
    monkeypatch.setenv("TMPDIR", str(t))
    monkeypatch.setattr("tempfile.tempdir", None)
    return t


def quiet(_s: str) -> None:
    return None


# ---- the source forms (2.1) -------------------------------------------------------------------

PIN_MSG = (
    "pin a full lowercase commit sha (git+<url>@<40 or 64 hex>); branch and tag names are refused"
)
FORM_MSG = "only git+file:// and git+https:// sources are installed"
CRED_MSG = "a git source must not carry credentials (user[:password]@host), a ?query or a #fragment"


@pytest.mark.parametrize(
    "source,message",
    [
        ("git+file:///tmp/r", PIN_MSG),
        ("git+file:///tmp/r@main", PIN_MSG),
        ("git+file:///tmp/r@v0.1.0", PIN_MSG),
        (f"git+file:///tmp/r@{C1[:7]}", PIN_MSG),
        (f"git+file:///tmp/r@{'A' * 40}", PIN_MSG),
        (f"git+file:///tmp/r@{'a' * 41}", PIN_MSG),
        ("git@github.com:o/r.git", PIN_MSG),
        (f"git+ssh://h/r@{C1}", FORM_MSG),
        (f"git+http://h/r@{C1}", FORM_MSG),
        (f"git://h/r@{C1}", FORM_MSG),
        (f"ssh://git@h/r@{C1}", FORM_MSG),
        (f"https://h/r.git@{C1}", FORM_MSG),
        (f"git+https://user:pw@h/r@{C1}", CRED_MSG),
        (f"git+https://h/r?x=1@{C1}", CRED_MSG),
        (f"git+https://h/r#x@{C1}", CRED_MSG),
        (f"git+https://example.invalid/r@{C1}", "remote sources need --allow-remote"),
    ],
)
def test_refused_forms_run_nothing(source, message, tmp_path, monkeypatch, project):
    log = tmp_path / "shim.log"
    shim = gf.make_shim(tmp_path / "bin", log, fail_fetch=True)  # never reaches a network
    monkeypatch.setenv("PATH", f"{shim.parent}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(GitSourceNotSupported) as err:
        mgit.parse_git_source(source)
    assert str(err.value) == message
    with pytest.raises(GitSourceNotSupported) as err2:
        install(project, source, yes=True, out=quiet)
    assert str(err2.value) == message
    assert gf.read_shim_log(log) == []  # not one git call
    assert not (project / ".lattice").exists() and not (project / LOCK).exists()


def test_a_file_source_must_be_an_absolute_directory(tmp_path):
    with pytest.raises(GitSourceNotSupported, match="is not a directory"):
        mgit.parse_git_source(f"git+file://{tmp_path}/missing@{C1}")
    with pytest.raises(GitSourceNotSupported, match="needs an absolute path"):
        mgit.parse_git_source(f"git+file://relative/path@{C1}")
    src = mgit.parse_git_source(f"git+file://{tmp_path}@{C1}")
    assert (src.url, src.sha, src.scheme, src.object_format) == (
        f"file://{tmp_path}",
        C1,
        "file",
        "sha1",
    )
    assert mgit.parse_git_source(f"git+file://{tmp_path}@{'b' * 64}").object_format == "sha256"
    remote = mgit.parse_git_source(f"git+https://h.example/p/r@{C1}", allow_remote=True)
    assert (remote.url, remote.scheme) == ("https://h.example/p/r", "https")


def test_resolve_source_keeps_refusing_git(tmp_path):
    for url in (f"git+file://{tmp_path}@{C1}", f"git+https://h/r@{C1}"):
        with pytest.raises(GitSourceNotSupported, match="installing from a git URL is not"):
            resolve_source(url)


# ---- the install (2.3-2.5) --------------------------------------------------------------------


def test_a_pinned_git_install_writes_the_tree_and_the_lock(tmp_path, repo, project, tmpdir_env):
    path, c1 = repo
    source = gf.url(path, c1)
    shown: list[str] = []
    entry = install(project, source, yes=True, out=shown.append)
    dest = project / ".lattice/modules/mariadb-engine"
    names = sorted(p.name for p in dest.iterdir())
    assert names == ["README.md", "engine.toml", "lattice-module.toml"]
    for name in names:
        blob = subprocess.run(
            ["git", "cat-file", "blob", f"{c1}:{name}"],
            cwd=path,
            capture_output=True,
            check=True,
            env=gf.fixture_env(),
        ).stdout
        assert (dest / name).read_bytes() == blob
        assert (dest / name).stat().st_mode & 0o777 == 0o644
    # the same bytes as a directory install of `git archive`
    archive = tmp_path / "archive"
    archive.mkdir()
    tar = subprocess.run(
        ["git", "archive", c1], cwd=path, capture_output=True, check=True, env=gf.fixture_env()
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(archive)], input=tar, check=True)
    assert entry.sha256 == tree_hash(archive) == tree_hash(dest)
    assert entry.commit == c1 and entry.index is None and entry.source == source
    lock = json.loads((project / LOCK).read_text())
    assert lock == {
        "version": 1,
        "modules": [
            {
                "name": "mariadb-engine",
                "version": "0.1.0",
                "kind": "engine",
                "sha256": entry.sha256,
                "source": source,
                "commit": c1,
            }
        ],
    }
    assert list(lock["modules"][0]) == ["name", "version", "kind", "sha256", "source", "commit"]
    text = (project / LOCK).read_text()
    assert text == json.dumps(lock, indent=2) + "\n"
    assert any(
        f"  commit:   {c1} (fetched without hooks, filters, submodules, LFS; 3 files" in s
        for s in shown
    )
    assert [s.status for s in verify(project)] == ["ok"]
    assert [s.status for s in register_modules(project)] == ["ok"]
    assert REGISTRY.entry("mariadb").source == "module"
    assert not list(tmpdir_env.glob("lattice-module-*"))
    assert not list((project / ".lattice/modules").glob(".staging-*"))
    e = uninstall(project, "mariadb-engine")
    assert e.commit == c1 and not (project / LOCK).exists() and not dest.exists()


def test_the_cli_reports_the_commit(tmp_path, repo, project, capsys):
    path, c1 = repo
    assert main(["install", gf.url(path, c1), "--yes", "--project", str(project)]) == 0
    capsys.readouterr()
    assert main(["info", "mariadb-engine", "--project", str(project)]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    i = next(n for n, ln in enumerate(lines) if ln.startswith("  source:"))
    assert lines[i + 1] == f"  commit:          {c1}"
    assert main(["info", "mariadb-engine", "--json", "--project", str(project)]) == 0
    assert json.loads(capsys.readouterr().out)["commit"] == c1
    assert main(["list", "--project", str(project)]) == 0
    assert " ok " in capsys.readouterr().out


def test_every_git_call_is_hardened_and_isolated(tmp_path, repo, project, monkeypatch):
    path, c1 = repo
    log = tmp_path / "shim.log"
    shim = gf.make_shim(tmp_path / "bin", log)
    monkeypatch.setenv("PATH", f"{shim.parent}{os.pathsep}{os.environ['PATH']}")
    # a hostile caller environment: none of it may reach git
    for k, v in {
        "GIT_TRACE": str(tmp_path / "trace"),
        "GIT_DIR": str(tmp_path / "gd"),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": str(tmp_path / "hooks"),
        "GIT_CONFIG_PARAMETERS": "'core.hooksPath=/x'",
        "GIT_SSH_COMMAND": "touch " + str(tmp_path / "ssh"),
        "GIT_EXEC_PATH": str(tmp_path / "exec"),
    }.items():
        monkeypatch.setenv(k, v)
    install(project, gf.url(path, c1), yes=True, out=quiet)
    calls = gf.read_shim_log(log)
    assert [gf.subcommand(c["argv"]) for c in calls] == [  # type: ignore[arg-type]
        "--version",
        "init",
        "fetch",
        "count-objects",
        "cat-file",
        "ls-tree",
        "cat-file",
    ]
    pairs = [  # written out, not read from mgit.HARDENING: the test pins the contract
        "core.hooksPath=/dev/null",
        "core.fsmonitor=false",
        "core.symlinks=false",
        "core.protectHFS=true",
        "core.protectNTFS=true",
        "protocol.allow=never",
        "submodule.recurse=false",
        "fetch.recurseSubmodules=false",
        "transfer.fsckObjects=true",
        "fetch.fsckObjects=true",
        "gc.auto=0",
        "maintenance.auto=false",
        "credential.helper=",
        "http.followRedirects=false",
        "protocol.file.allow=always",
    ]
    for call in calls:
        argv: list[str] = call["argv"]  # type: ignore[assignment]
        cfg = [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]
        assert cfg == pairs  # every pair, in order, on every call
        assert argv[0] == "-c"
        for banned in ("clone", "checkout", "submodule", "lfs", "pull", "worktree"):
            assert banned not in argv
        env: dict[str, str] = call["env"]  # type: ignore[assignment]
        assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_CONFIG_GLOBAL"] == "/dev/null"
        assert env["GIT_TERMINAL_PROMPT"] == "0" and env["GIT_ALLOW_PROTOCOL"] == "file"
        assert env["GIT_NO_REPLACE_OBJECTS"] == "1" and env["GIT_OPTIONAL_LOCKS"] == "0"
        assert env["GIT_LFS_SKIP_SMUDGE"] == "1" and env["GIT_NO_LAZY_FETCH"] == "1"
        assert env["GIT_PROTOCOL_FROM_USER"] == "0" and env["GIT_ATTR_NOSYSTEM"] == "1"
        assert env["GIT_ASKPASS"] == env["SSH_ASKPASS"] and env["GIT_ASKPASS"].endswith("false")
        assert env["LC_ALL"] == "C"
        assert env["HOME"] == env["XDG_CONFIG_HOME"] and env["HOME"].endswith("/home")
        leaked = {
            k
            for k in env
            if k.startswith("GIT_")
            and k
            not in {
                "GIT_CONFIG_NOSYSTEM",
                "GIT_CONFIG_GLOBAL",
                "GIT_TERMINAL_PROMPT",
                "GIT_ASKPASS",
                "GIT_LFS_SKIP_SMUDGE",
                "GIT_NO_LAZY_FETCH",
                "GIT_NO_REPLACE_OBJECTS",
                "GIT_ATTR_NOSYSTEM",
                "GIT_OPTIONAL_LOCKS",
                "GIT_PROTOCOL_FROM_USER",
                "GIT_ALLOW_PROTOCOL",
            }
        }
        assert leaked == set()
    init, fetch = calls[1]["argv"], calls[2]["argv"]
    assert "--bare" in init and "--template=" in init and "--object-format=sha256" not in init
    tail = fetch[fetch.index("fetch") :]  # type: ignore[union-attr]
    assert tail[:6] == [
        "fetch",
        "-q",
        "--depth=1",
        "--no-tags",
        "--no-recurse-submodules",
        "--no-write-fetch-head",
    ]
    assert tail[6:] == [f"file://{path.resolve()}", c1]
    assert not (tmp_path / "trace").exists() and not (tmp_path / "ssh").exists()


def test_a_sha256_repository_is_initialised_with_its_object_format(tmp_path, project, monkeypatch):
    path = tmp_path / "repos" / "s256"
    path.mkdir(parents=True)
    r = subprocess.run(
        ["git", "init", "-q", "--object-format=sha256", str(path)],
        capture_output=True,
        env=gf.fixture_env(),
    )
    if r.returncode != 0:
        pytest.skip("this git cannot create a sha256 repository")
    gf.write_files(path, gf.module_files())
    gf.git(path, "add", "-A")
    gf.git(path, "commit", "-q", "-m", "x")
    sha = gf.git(path, "rev-parse", "HEAD")
    assert len(sha) == 64
    log = tmp_path / "shim.log"
    shim = gf.make_shim(tmp_path / "bin", log)
    monkeypatch.setenv("PATH", f"{shim.parent}{os.pathsep}{os.environ['PATH']}")
    entry = install(project, gf.url(path, sha), yes=True, out=quiet)
    assert entry.commit == sha
    init = next(c["argv"] for c in gf.read_shim_log(log) if gf.subcommand(c["argv"]) == "init")  # type: ignore[arg-type]
    assert "--object-format=sha256" in init  # type: ignore[operator]


def test_an_old_git_is_refused_before_any_fetch(tmp_path, repo, project, monkeypatch, tmpdir_env):
    path, c1 = repo
    log = tmp_path / "shim.log"
    shim = gf.make_shim(tmp_path / "bin", log, version="2.39.0")
    monkeypatch.setenv("PATH", f"{shim.parent}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(ModuleError, match="git 2.39.0 is older than 2.45.1"):
        install(project, gf.url(path, c1), yes=True, out=quiet)
    assert [gf.subcommand(c["argv"]) for c in gf.read_shim_log(log)] == ["--version"]  # type: ignore[arg-type]
    assert not list(tmpdir_env.glob("lattice-module-*"))
    assert not (project / ".lattice").exists()
    assert mgit.git_version("git version 2.54.0 (Apple Git-157)") == (2, 54, 0)
    assert mgit.git_version("nonsense") is None


def test_the_remote_form_gets_https_only_and_the_shim_failure_is_reported(
    tmp_path, project, monkeypatch, tmpdir_env
):
    log = tmp_path / "shim.log"
    shim = gf.make_shim(tmp_path / "bin", log, fail_fetch=True)
    monkeypatch.setenv("PATH", f"{shim.parent}{os.pathsep}{os.environ['PATH']}")
    source = f"git+https://example.invalid/acme/m@{C1}"
    with pytest.raises(ModuleError, match="^git fetch of .* failed: fatal: shim refuses fetch$"):
        install(project, source, yes=True, allow_remote=True, out=quiet)
    calls = gf.read_shim_log(log)
    fetch = next(c for c in calls if gf.subcommand(c["argv"]) == "fetch")  # type: ignore[arg-type]
    assert fetch["argv"][-2:] == ["https://example.invalid/acme/m", C1]  # type: ignore[index]
    assert fetch["env"]["GIT_ALLOW_PROTOCOL"] == "https"  # type: ignore[index]
    assert "protocol.https.allow=always" in fetch["argv"]  # type: ignore[operator]
    assert "protocol.file.allow=always" not in fetch["argv"]  # type: ignore[operator]
    assert not list(tmpdir_env.glob("lattice-module-*"))
    assert not (project / ".lattice").exists()


# ---- what the tree may hold (2.4) -------------------------------------------------------------


def _entry(path: str, mode: str = "100644", size: int = 1) -> mgit.Entry:
    return mgit.Entry(mode, "0" * 40, size, path)


@pytest.mark.parametrize(
    "entries,message",
    [
        ([_entry("link", "120000")], "link is a symbolic link \\(mode 120000\\): refused"),
        ([_entry("sub", "160000")], "sub is a submodule \\(mode 160000\\): refused"),
        ([_entry("a/.hidden")], "not an allowed path name"),
        ([_entry(" a.toml")], "not an allowed path name"),
        ([_entry("é.toml")], "not an allowed path name"),
        ([_entry("-x")], "not an allowed path name"),
        # the 2.4 pattern starts a name with a letter or digit: a package cannot come from git
        ([_entry("pkg/__init__.py")], "not an allowed path name"),
        ([_entry("_private.py")], "not an allowed path name"),
        ([_entry(".github/x")], "not an allowed path name"),
        ([_entry("a/b/../c")], "not an allowed path name"),
        ([_entry("a/b/c/d/e/f/g/h/i")], "nested more than 8 levels"),
        ([_entry("x" * 201)], "is longer than 200 bytes"),
        ([_entry("Engine.toml"), _entry("engine.toml")], "differ only in case: refused"),
        ([_entry("A"), _entry("a/b")], "differ only in case: refused"),
        ([_entry("big.bin", size=2 * 1024 * 1024)], "big.bin is 2097152 bytes \\(cap 1048576\\)"),
        ([_entry(f"f{i}") for i in range(201)], "201 files \\(cap 200\\)"),
        (
            [_entry(f"f{i}", size=1_000_000) for i in range(9)],
            "9000000 bytes \\(cap 8388608\\)",
        ),
    ],
)
def test_the_listing_is_refused_whole(entries, message):
    with pytest.raises(ModuleError, match=message):
        mgit.validate_listing(entries)


def test_attribute_files_are_dropped_and_listed():
    keep, dropped = mgit.validate_listing(
        [_entry(".gitattributes"), _entry("a/.gitmodules"), _entry(".lfsconfig"), _entry("m.toml")]
    )
    assert [e.path for e in keep] == ["m.toml"]
    assert dropped == [".gitattributes", ".lfsconfig", "a/.gitmodules"]


def test_a_refused_tree_leaves_nothing_behind(tmp_path, project, tmpdir_env):
    path = tmp_path / "repos" / "bad"
    gf.init_repo(path)
    gf.write_files(path, gf.module_files())
    (path / "link").symlink_to("/etc/passwd")
    gf.git(path, "add", "-A")
    gf.git(path, "commit", "-q", "-m", "x")
    sha = gf.git(path, "rev-parse", "HEAD")
    with pytest.raises(ModuleError, match="link is a symbolic link \\(mode 120000\\): refused"):
        install(project, gf.url(path, sha), yes=True, out=quiet)
    assert not list(tmpdir_env.glob("lattice-module-*"))
    assert not (project / ".lattice").exists() and not (project / LOCK).exists()


def test_a_missing_commit_is_a_git_failure(tmp_path, repo, project, tmpdir_env):
    path, _c1 = repo
    with pytest.raises(ModuleError, match="^git fetch of .* failed: .*not our ref"):
        install(project, gf.url(path, "deadbeef" * 5), yes=True, out=quiet)
    assert not list(tmpdir_env.glob("lattice-module-*"))


def test_a_tag_object_is_not_a_commit(tmp_path, repo, project):
    path, _c1 = repo
    gf.git(path, "tag", "-a", "-m", "release", "v1")
    tag = gf.git(path, "rev-parse", "v1")
    with pytest.raises(ModuleError, match=f"{tag} is a tag, not a commit"):
        install(project, gf.url(path, tag), yes=True, out=quiet)


# ---- nothing from the module runs (2.2) -------------------------------------------------------


def test_hostile_repository_config_hooks_and_modules_do_not_run(tmp_path, project):
    canary = tmp_path / "canary"
    canary.mkdir()
    path = tmp_path / "repos" / "evil"
    files = {
        **gf.module_files(),
        ".gitattributes": "* filter=evil eol=crlf\n",
        ".gitmodules": f'[submodule "s"]\n\tpath = s\n\turl = ext::sh -c touch {canary}/sub\n',
        ".lfsconfig": "[lfs]\n\turl = https://example.invalid/lfs\n",
    }
    sha = gf.make_repo(path, files)
    touch = f"touch {canary}/%s"
    hooks = path / ".git" / "hooks"
    for hook in (
        "post-checkout",
        "post-merge",
        "reference-transaction",
        "pre-commit",
        "fsmonitor-watchman",
    ):
        h = hooks / hook
        h.write_text("#!/bin/sh\n" + touch % hook + "\n")
        h.chmod(0o755)
    for key, value in (
        ("core.hooksPath", str(hooks)),
        ("core.fsmonitor", touch % "fsmonitor"),
        ("uploadpack.packObjectsHook", touch % "packobjects"),
        ("filter.evil.smudge", touch % "smudge"),
        ("filter.evil.clean", touch % "clean"),
        ("filter.evil.process", touch % "process"),
    ):
        gf.git(path, "config", key, value)
    entry = install(project, gf.url(path, sha), yes=True, out=quiet)
    assert entry.commit == sha
    assert list(canary.iterdir()) == []
    d = project / ".lattice/modules/mariadb-engine"
    assert sorted(p.name for p in d.iterdir()) == [
        "README.md",
        "engine.toml",
        "lattice-module.toml",
    ]


def test_a_module_that_names_code_installs_without_importing_it(tmp_path, project):
    canary = tmp_path / "canary"
    canary.mkdir()
    code = f"open({str(canary / 'import')!r}, 'w').write('ran')\n"
    desc = gf.MARIADB.read_text().replace('adapter = "mysql"', 'adapter = "evilpkg:Adapter"')
    path = tmp_path / "repos" / "code"
    sha = gf.make_repo(
        path,
        {**gf.module_files(), "engine.toml": desc, "evilpkg.py": code},
    )
    shown: list[str] = []
    install(project, gf.url(path, sha), yes=True, out=shown.append)
    assert list(canary.iterdir()) == []
    assert any("CODE: imported into the checker when the engine is used" in s for s in shown)
    custom = tmp_path / "repos" / "custom"
    manifest = 'name = "custom-mod"\nversion = "1"\nkind = "custom"\nentry = "evilentry"\n'
    sha2 = gf.make_repo(
        custom, {"lattice-module.toml": manifest, "evilentry.py": code, "README.md": "x\n"}
    )
    install(project, gf.url(custom, sha2), yes=True, out=quiet)
    assert list(canary.iterdir()) == []


# ---- several sources, atomically (2.5) --------------------------------------------------------


def _two_repos(tmp_path: Path) -> tuple[str, str]:
    a = tmp_path / "repos" / "a"
    b = tmp_path / "repos" / "b"
    sa = gf.make_repo(a, gf.module_files("mod-a", "0.1.0"))
    custom = {
        "lattice-module.toml": 'name = "mod-b"\nversion = "1.0"\nkind = "custom"\n',
        "README.md": "b\n",
    }
    sb = gf.make_repo(b, custom)
    return gf.url(a, sa), gf.url(b, sb)


def test_several_sources_are_confirmed_once_and_locked_together(tmp_path, project):
    ua, ub = _two_repos(tmp_path)
    asked: list[str] = []
    shown: list[str] = []
    entries = install_many(
        project, [ua, ub], confirm=lambda q: asked.append(q) or True, out=shown.append
    )
    assert asked == [f"Install 2 modules (mod-a 0.1.0, mod-b 1.0) into {project.resolve()}? [y/N] "]
    assert [e.name for e in entries] == ["mod-a", "mod-b"]
    assert sum(1 for s in shown if s.startswith("module ")) == 2
    assert [e.name for e in read_lock(project)] == ["mod-a", "mod-b"]
    assert all(e.commit for e in read_lock(project))


def test_a_duplicate_name_in_one_install_is_refused(tmp_path, project):
    ua, _ub = _two_repos(tmp_path)
    with pytest.raises(ModuleError, match="module mod-a appears twice in this install"):
        install_many(project, [ua, ua], yes=True, out=quiet)
    assert not (project / ".lattice").exists() and not (project / LOCK).exists()


def test_a_second_refusal_writes_nothing_for_the_first(tmp_path, project):
    ua, _ub = _two_repos(tmp_path)
    with pytest.raises(GitSourceNotSupported):
        install_many(project, [ua, f"git+file://{tmp_path}@main"], yes=True, out=quiet)
    assert not (project / ".lattice").exists() and not (project / LOCK).exists()


def test_a_failing_rename_removes_what_the_install_made(tmp_path, project, monkeypatch):
    """I9: the second rename raises; the first module is removed, no staging directory is
    left and the lock is unchanged."""
    ua, ub = _two_repos(tmp_path)
    first = install_many(project, [ua], yes=True, out=quiet)
    lock_before = (project / LOCK).read_bytes()
    uninstall(project, "mod-a")
    install_many(project, [ua], yes=True, out=quiet)
    assert (project / LOCK).read_bytes() == lock_before and first
    uninstall(project, "mod-a")
    assert not (project / LOCK).exists()
    calls: list[tuple[str, str]] = []
    real = os.rename

    def flaky(src, dst, *a, **k):
        calls.append((str(src), str(dst)))
        if len(calls) == 2:
            raise OSError(28, "No space left on device")
        return real(src, dst, *a, **k)

    monkeypatch.setattr("lattice.modules.os.rename", flaky)
    with pytest.raises(OSError, match="No space left"):
        install_many(project, [ua, ub], yes=True, out=quiet)
    monkeypatch.undo()
    assert len(calls) == 2
    assert not (project / LOCK).exists()
    assert list((project / ".lattice/modules").iterdir()) == []  # no module dir, no staging


def test_a_failing_second_install_keeps_the_first_and_its_lock(tmp_path, project, monkeypatch):
    ua, ub = _two_repos(tmp_path)
    install_many(project, [ua], yes=True, out=quiet)
    before = (project / LOCK).read_bytes()
    tree_before = tree_hash(project / ".lattice/modules/mod-a")
    real = os.replace

    def broken(src, dst):
        if str(dst).endswith(LOCK):
            raise OSError(5, "I/O error")
        return real(src, dst)

    monkeypatch.setattr("lattice.modules.os.replace", broken)
    with pytest.raises(OSError, match="I/O error"):
        install_many(project, [ub], yes=True, out=quiet)
    monkeypatch.undo()
    assert (project / LOCK).read_bytes() == before
    assert not (project / ".lattice/modules/mod-b").exists()
    assert not list((project / ".lattice/modules").glob(".staging-*"))
    assert not (project / (LOCK + ".tmp")).exists()
    assert tree_hash(project / ".lattice/modules/mod-a") == tree_before


def test_directory_installs_keep_the_a4ab4a1_lock_bytes(tmp_path, project):
    src = tmp_path / "mariadb-engine"
    src.mkdir()
    gf.write_files(src, gf.module_files())
    entry = install(project, str(src), yes=True, out=quiet)
    expected = {
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
    assert (project / LOCK).read_text() == json.dumps(expected, indent=2) + "\n"
    assert entry.commit is None and entry.index is None
    digest = hashlib.sha256((project / LOCK).read_bytes()).hexdigest()
    assert len(digest) == 64
