"""Fixture git repositories for the marketplace tests and probes: fixed identity and dates
so a commit sha repeats, ``file://`` only (no network), an isolated git configuration."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MARIADB = ROOT / "docs/examples/engines/mariadb.toml"

DATE = "2026-10-15T00:00:00Z"
IDENTITY = {
    "GIT_AUTHOR_NAME": "Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_AUTHOR_DATE": DATE,
    "GIT_COMMITTER_DATE": DATE,
}
CONFIG = (
    "-c",
    "commit.gpgsign=false",
    "-c",
    "init.defaultBranch=main",
    "-c",
    "protocol.file.allow=always",
)


def fixture_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "LC_ALL": "C",
        **IDENTITY,
    }
    if "TMPDIR" in os.environ:
        env["TMPDIR"] = os.environ["TMPDIR"]
    env.update(extra or {})
    return env


def git(
    cwd: Path | str,
    *args: str,
    input: bytes | None = None,
    check: bool = True,
    env: Mapping[str, str] | None = None,
) -> str:
    r = subprocess.run(
        ["git", *CONFIG, *args],
        cwd=str(cwd),
        env=fixture_env(env),
        input=input,
        capture_output=True,
        check=False,
    )
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.decode(errors='replace')}")
    return r.stdout.decode(errors="replace").strip()


def write_files(root: Path, files: Mapping[str, bytes | str]) -> None:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    return path


def commit(path: Path, files: Mapping[str, bytes | str], message: str = "fixture") -> str:
    """Replace the working tree of ``path`` by ``files`` and commit; returns the sha."""
    for p in sorted(path.rglob("*"), reverse=True):
        if ".git" in p.relative_to(path).parts:
            continue
        if p.is_file() or p.is_symlink():
            p.unlink()
        elif p.is_dir():
            try:
                p.rmdir()
            except OSError:
                pass
    write_files(path, files)
    git(path, "add", "-A", "-f")
    git(path, "commit", "-q", "--allow-empty", "-m", message)
    return git(path, "rev-parse", "HEAD")


def module_files(
    name: str = "mariadb-engine",
    version: str = "0.1.0",
    description: str = "MariaDB for the checker",
) -> dict[str, str]:
    return {
        "lattice-module.toml": (
            f'name = "{name}"\nversion = "{version}"\nkind = "engine"\n'
            f'description = "{description}"\ndescriptor = "engine.toml"\n'
        ),
        "engine.toml": MARIADB.read_text(),
        "README.md": "MariaDB, no code.\n",
    }


def make_repo(path: Path, files: Mapping[str, bytes | str] | None = None) -> str:
    init_repo(path)
    return commit(path, files if files is not None else module_files())


def url(path: Path, sha: str) -> str:
    return f"git+file://{path.resolve()}@{sha}"


# ---- a git shim: logs every call's argv and environment, then runs the real git ---------------


def make_shim(
    shim_dir: Path, log: Path, *, version: str | None = None, fail_fetch: bool = False
) -> Path:
    """A ``git`` script in ``shim_dir`` (put it first on PATH): appends each call to ``log``
    (``CALL``, ``A <arg>`` lines, ``E <KEY=VALUE>`` lines), then execs the real git. ``version``
    answers ``--version`` with ``git version <version>``; ``fail_fetch`` fails ``fetch``."""
    import shutil
    import stat

    real = shutil.which("git", path=os.environ.get("PATH"))
    assert real, "git is not installed"
    shim_dir.mkdir(parents=True, exist_ok=True)
    script = shim_dir / "git"
    lines = [
        "#!/bin/sh",
        "{",
        "  printf 'CALL\\n'",
        '  for a in "$@"; do printf \'A %s\\n\' "$a"; done',
        "  env | sort | sed 's/^/E /'",
        f"}} >> '{log}'",
        'for a in "$@"; do',
        "  :",
    ]
    if version is not None:
        lines.append(f'  if [ "$a" = "--version" ]; then echo "git version {version}"; exit 0; fi')
    if fail_fetch:
        lines.append(
            '  if [ "$a" = "fetch" ]; then echo "fatal: shim refuses fetch" >&2; exit 128; fi'
        )
    lines += ["done", f"exec '{real}' \"$@\""]
    script.write_text("\n".join(lines) + "\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


def read_shim_log(log: Path) -> list[dict[str, object]]:
    """The calls ``make_shim`` logged: ``{"argv": [...], "env": {...}}`` each."""
    calls: list[dict[str, object]] = []
    if not log.is_file():
        return calls
    for line in log.read_text(errors="replace").splitlines():
        if line == "CALL":
            calls.append({"argv": [], "env": {}})
        elif line.startswith("A ") and calls:
            calls[-1]["argv"].append(line[2:])  # type: ignore[union-attr]
        elif line.startswith("E ") and calls:
            key, _, value = line[2:].partition("=")
            calls[-1]["env"][key] = value  # type: ignore[index]
    return calls


def subcommand(argv: list[str]) -> str:
    """The git subcommand of a logged call (``--version`` included), skipping ``-c k=v``
    and ``-C dir``."""
    i = 0
    while i < len(argv):
        if argv[i] in ("-c", "-C"):
            i += 2
            continue
        return argv[i]
    return ""


def commit_entries(
    path: Path, entries: list[tuple[str, str, bytes | str]], message: str = "fixture"
) -> str:
    """A commit in the repository ``path`` (initialised if needed) made from ``entries``
    ``(mode, path, content)`` through a scratch index, no working tree: names a file system
    could not hold (``Engine.toml`` beside ``engine.toml``) and modes (``120000``,
    ``160000``: the content is then the link target, or the commit sha of the gitlink)."""
    if not (path / ".git").exists():
        init_repo(path)
    index = path / ".git" / "fixture-index"
    index.unlink(missing_ok=True)
    env = {"GIT_INDEX_FILE": str(index)}
    lines = []
    for mode, name, data in entries:
        if mode == "160000":
            oid = str(data)
        else:
            raw = data if isinstance(data, bytes) else data.encode()
            oid = git(path, "hash-object", "-w", "--stdin", input=raw, env=env)
        lines.append(f"{mode} {oid}\t{name}\n")
    git(path, "update-index", "--add", "--index-info", input="".join(lines).encode(), env=env)
    tree = git(path, "write-tree", env=env)
    index.unlink(missing_ok=True)
    sha = git(path, "commit-tree", tree, "-m", message)
    git(path, "update-ref", "refs/heads/main", sha)
    return sha
