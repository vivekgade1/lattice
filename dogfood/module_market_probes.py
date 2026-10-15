"""The marketplace probes and mutants (node G3, ``docs/results/2026-10-15-marketplace-prereg.md``
with its Amendment 1): installing a module from a pinned git commit, hostile repositories,
module indexes in git, ``--engine-file``/``--modules`` on ask and draft, engines of several
projects in one process, and the regression guard.

Every deterministic probe runs ``--runs`` times (default 10) on fixed paths under ``--scratch``
and passes only when all runs are byte-identical and its own rule holds. Predictions are
fixed in this file BEFORE the first run (``PREDICTED``); the runner prints
``id | predicted | observed | pass`` and ends ``probes: P/T pass``. No LLM, no API key, no network:
fixture repositories are ``file://``; the only ``https`` source meets a git shim that refuses
the fetch.

    uv run python dogfood/module_market_probes.py --scratch DIR [--runs 10] \
        [--groups git,tamper,index,flag,hub,regress]
    uv run python dogfood/module_market_probes.py --scratch DIR --mutants [MG1 ...] [--jobs 3]

Mutant mode copies the code to a scratch tree per mutant (the working tree is never patched),
applies the edit(s), runs the pre-registered tests and the probes there, and prints
``mutants: N/N killed`` (exit 0 only when the control run adds no failure of its own and every
mutant adds one).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.modules import gitfix as gf  # noqa: E402

DATA = ROOT / "docs/results/data/2026-10-15-marketplace"
GROUPS = ("git", "tamper", "index", "flag", "hub", "regress")
BASE_COMMIT = "a4ab4a1"
REAL_GIT = shutil.which("git") or "/usr/bin/git"

# the exact messages of section 2.1, written out here (not read from the code under test)
PIN_MSG = (
    "pin a full lowercase commit sha (git+<url>@<40 or 64 hex>); branch and tag names are refused"
)
FORM_MSG = "only git+file:// and git+https:// sources are installed"
REMOTE_MSG = "remote sources need --allow-remote"
CRED_PART = "must not carry credentials"
# section 2.3, written out
HARDEN_PAIRS = [
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
]
ENV_VALUES = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_LFS_SKIP_SMUDGE": "1",
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_ATTR_NOSYSTEM": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_PROTOCOL_FROM_USER": "0",
    "LC_ALL": "C",
}
ENV_KEYS = set(ENV_VALUES) | {
    "PATH",
    "TMPDIR",
    "HOME",
    "XDG_CONFIG_HOME",
    "GIT_ASKPASS",
    "SSH_ASKPASS",
    "GIT_ALLOW_PROTOCOL",
}
SHELL_ADDED = {"PWD", "SHLVL", "_", "OLDPWD", "__CF_USER_TEXT_ENCODING"}

# ---- predictions (blind: from the pre-registration, before any run) ---------------------------

PREDICTED: dict[str, str] = {
    "M-git-a": "exit 0; 3 files = git show C1; sha256 = git archive dir install; lock exact "
    "(+commit); list ok; info commit; registers mariadb; uninstall clean; 7 hardened git calls, "
    "0 clone/checkout/submodule/lfs, file:// only",
    "M-git-b": "@C2 into a second project: exit 0, version 0.2.0, commit C2",
    "M-git-c": "11 refusals: exit 2, 2.1's message each, 0 git calls, nothing written",
    "M-git-d": "git+https with --allow-remote: one fetch argv with https://, "
    "GIT_ALLOW_PROTOCOL=https; shim fails it: exit 2 'git fetch ... failed'",
    "M-git-e": "a under sandbox-exec (deny network*): identical to a",
    "M-tamper-T1": "exit 2, git fetch ... failed: ... not our ref ...",
    "M-tamper-T2": "exit 2, pins sha256 0000..., the source tree hashes to ...: refused",
    "M-tamper-T3": "exit 0, 0.1.0, sha256 = M-git a's (main advanced to C2)",
    "M-tamper-T4": "a: exit 0 (unreachable, not gc'd) = M-git a; "
    "b: after gc --prune=now exit 2 not our ref",
    "M-tamper-T5": "exit 2, link is a symbolic link (mode 120000): refused; 0 blobs read",
    "M-tamper-T6": "exit 2, the fetch fails on fsck (hasDotdot/hasDotgit) for each of "
    "..,.git/config,.GIT/hooks/x",
    "M-tamper-T7": "exit 2, sub is a submodule (mode 160000): refused; canary empty",
    "M-tamper-T8": "exit 2 each: (cap 1048576); 201 files (cap 200); 9000000 bytes (cap 8388608); "
    "size-pack ... (cap 32 MiB)",
    "M-tamper-T9": "exit 0, installs; hooks/config/filters of the source never run (canary empty)",
    "M-tamper-T10": "exit 0; .gitattributes/.lfsconfig dropped and listed; user git config inert; "
    "engine.toml = blob",
    "M-tamper-T11": "exit 0, installs; GIT_TRACE/GIT_DIR/GIT_CONFIG_*/GIT_SSH_COMMAND of the caller "  # noqa: E501
    "inert",
    "M-tamper-T12": "exit 0 each; canary empty; summary says CODE (adapter and entry)",
    "M-tamper-T13": "exit 2, differ only in case: refused",
    "M-tamper-T14": "exit 2, is a tag, not a commit; is a tree, not a commit",
    "M-tamper-T15": "exit 2 for ' a.toml', 'é.toml', '-x', '.github/x' (2.4)",
    "M-tamper-T16": "exit 2, older than 2.45.1 ...; no fetch",
    "M-index-I1": "install a b --index git+file://idx@X: exit 0; 2 lock entries by name with "
    "commit and index",
    "M-index-I2": "a b c: exit 2, nothing written",
    "M-index-I3": "a b d: exit 2, nothing written",
    "M-index-I4": "a installed, then a b: exit 2 already installed, b absent, lock byte-identical",
    "M-index-I5": "a a: exit 2, appears twice",
    "M-index-I6": "dir index naming a pinned git source: exit 0; unpinned: exit 2 "
    "(GitSourceNotSupported)",
    "M-index-I7": "e (source ../a) in a git index: exit 2, a git index names pinned git "
    "sources only",
    "M-index-I8": "--index ...idx@main: exit 2 (2.1)",
    "M-index-I9": "pytest: the 2nd rename raises: 1st removed, no .staging-*, lock unchanged",
    "M-hub-H1": "per-project /api/engines bodies identical in ABAB, BABA, AABB and to the project alone; "  # noqa: E501
    "B: 5 engines, mariadb:10.11, errors []; A lists pga",
    "M-hub-H2": "one thread A then B: mariadb:11 + pga, then mariadb:10.11 + no pga; "
    "_BASE = 3 built-ins",
    "M-hub-H3": "8 threads, 200 interleaved requests: every body = H1's",
    "M-hub-H4": "A resolves its adapter; B raises 'already imported from ... distinct name'",
    "M-hub-H5": "static: the 3 load_project_engines sites precede their lookups",
    "M-hub-M6": "a worker thread that never loaded sees only the built-ins after another "
    "thread loaded A",
    "M-hub-M6b": "a pooled thread serves A then B without B seeing A; built-ins only after "
    "each scope",
    "M-regress-R1": "directory install/list/info/uninstall: stdout, stderr, exit, lock bytes, trees "  # noqa: E501
    "byte-identical to a4ab4a1",
    "M-regress-R2": "a4ab4a1's list and info on M-git a's project: ok",
    "M-regress-R3": "test_modules.py unchanged, 26 passed; named suites unchanged and passing; only "  # noqa: E501
    "test_engines_api.py:130 changed; check.sh PASS",
    "M-regress-R4": "module --help and lattice --help differ from a4ab4a1 only by new options "
    "and usage",
}
for _cmd in ("check", "ask", "draft"):
    PREDICTED[f"M-flag-F1-{_cmd}"] = "--help lists {sqlite,postgres,mysql,clickhouse,mariadb}"
    PREDICTED[f"M-flag-F2-{_cmd}"] = "parsed: database == mariadb, engine_file == [F]"
    PREDICTED[f"M-flag-F3-{_cmd}"] = (
        "exit 2, 'error: EngineError: ...' (unknown field), same first line in all three"
    )
    PREDICTED[f"M-flag-F4-{_cmd}"] = (
        "exit 2, 'error: EngineError: ...' (missing file), same first line in all three"
    )
    PREDICTED[f"M-flag-F5-{_cmd}"] = (
        "exit 2, 'error: EngineError: ...' (name mysql), same first line in all three"
    )
    PREDICTED[f"M-flag-F6-{_cmd}"] = (
        "exit 2, 'error: ModuleError: ...' (tampered module), same first line in all three"
    )
    PREDICTED[f"M-flag-F7-{_cmd}"] = "--engine-file=F form: --help lists mariadb"
    PREDICTED[f"M-flag-F8-{_cmd}"] = "exit 2, invalid choice: 'mariadb'"

# a4ab4a1 (blind baseline, section 3): does the row's rule hold there? Only the rows the prereg
# predicts a baseline for; used with --code-root <a4ab4a1 tree>.
BASELINE_EXPECT: dict[str, bool] = {
    # check's cells hold, except that F3-F6 also require the same first line from ask and draft
    **{
        f"M-flag-{f}-check": f in ("F1", "F2", "F7", "F8")
        for f in ("F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8")
    },
    **{
        f"M-flag-{f}-{cmd}": False
        for f in ("F1", "F2", "F3", "F4", "F5", "F6", "F7")
        for cmd in ("ask", "draft")
    },
    **{f"M-flag-F8-{cmd}": True for cmd in ("ask", "draft")},
    "M-hub-H1": False,  # B's errors hold 'already registered ... with a different descriptor'
    "M-hub-H2": False,  # B gets mariadb:11 and pga
    "M-hub-H4": False,  # B silently gets A's class
}

# the killers section 4 names, by row id or test file (for the mutant table)
MG_PREDICTED: dict[str, str] = {
    "MG1": "M-git-c, M-index-I8",
    "MG2": "M-tamper-T11",
    "MG3": "M-tamper-T10",
    "MG4": "M-tamper-T5",
    "MG5": "M-tamper-T8",
    "MG6": "M-tamper-T8",
    "MG7": "M-tamper-T14",
    "MG8": "every M-tamper row",
    "MG9": "M-index-I2, I3, I4",
    "MG10": "M-tamper-T10",
    "MG11": "M-git-a",
    "MG12": "M-regress-R1",
    "MG13": "M-git-c",
    "MG14": "M-hub-H1, H2",
    "MG15": "test_hub_engines",
    "MG16": "M-flag-F1-ask, F2-ask",
    "MG17": "M-hub-H4",
    "MG18": "M-index-I7",
    "MG19": "M-tamper-T6",
    "MG20": "survives every behavioural probe; dies by the exact-argv test of test_git_install.py",
    "MG21": "M-hub-M6b",
    "MG22": "M-hub-M6",
}

# ---- mutants (section 4 and Amendment 1): id -> (behaviour, [(file, old, new), ...]) ----------

G = "lattice/modules/git.py"
M = "lattice/modules/__init__.py"
E = "lattice/spec/engines.py"
MUTANTS: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    "MG1": (
        "a branch or tag accepted as the pin",
        [(G, "if not at or not _PIN.fullmatch(pin):", "if not at or not pin:")],
    ),
    "MG2": (
        "the git environment inherits os.environ",
        [
            (
                G,
                '    env = {"PATH": os.environ.get("PATH", os.defpath)}\n',
                "    env = dict(os.environ)\n",
            )
        ],
    ),
    "MG3": (
        "GIT_CONFIG_GLOBAL / GIT_CONFIG_NOSYSTEM not set",
        [
            (
                G,
                '            "GIT_CONFIG_NOSYSTEM": "1",\n            "GIT_CONFIG_GLOBAL": "/dev/null",\n',  # noqa: E501
                "",
            )
        ],
    ),
    "MG4": (
        "a symlink written as a file holding its target",
        [
            (
                G,
                '        if e.mode == "120000":\n            raise ModuleError(f"{e.path} is a symbolic link (mode 120000): refused")\n',  # noqa: E501
                "",
            ),
            (
                G,
                'if e.mode not in ("100644", "100755"):',
                'if e.mode not in ("100644", "100755", "120000"):',
            ),
        ],
    ),
    "MG5": (
        "the per-blob cap raised from 1 MiB to 100 MiB",
        [(G, "MAX_BLOB = 1_048_576", "MAX_BLOB = 104_857_600")],
    ),
    "MG6": (
        "the file-count cap dropped",
        [(G, "    if len(keep) > MAX_FILES:\n", "    if False:\n")],
    ),
    "MG7": (
        "cat-file -t unchecked",
        [(G, '        if kind_text != "commit":\n', "        if False:\n")],
    ),
    "MG8": (
        "the temp dir is kept on refusal",
        [(G, "        shutil.rmtree(tmp, ignore_errors=True)\n", "        pass\n")],
    ),
    "MG9": (
        "the lock is written per module (each module is installed as soon as it validates)",
        [
            (
                M,
                "            prepared.append(p)\n",
                "            prepared.append(p)\n            _write_modules(root, [p])\n",
            ),
            (
                M,
                "        entries = _write_modules(root, prepared)\n",
                "        entries = [e for e in read_lock(root) if e.name in {q.manifest.name for q in prepared}]\n",  # noqa: E501
            ),
        ],
    ),
    "MG10": (
        ".gitattributes kept",
        [(G, "        if parts[-1] in DROPPED_NAMES:\n", "        if False:\n")],
    ),
    "MG11": (
        "the lock without commit",
        [
            (
                M,
                "        if self.commit is not None:  # directory installs gain no key\n",
                "        if False:\n",
            )
        ],
    ),
    "MG12": (
        'a directory entry gains "commit": null',
        [
            (
                M,
                '        if self.commit is not None:  # directory installs gain no key\n            out["commit"] = self.commit\n',  # noqa: E501
                '        out["commit"] = self.commit\n',
            )
        ],
    ),
    "MG13": (
        "the --allow-remote gate dropped",
        [
            (
                G,
                "        if not allow_remote:\n            raise GitSourceNotSupported(_REMOTE_MESSAGE)\n",  # noqa: E501
                "        pass\n",
            )
        ],
    ),
    "MG14": (
        "load_project_engines registers into the process registry (a4ab4a1)",
        [
            (
                M,
                "    out = _load_into(root, engines_table, _engines.project_registry())\n"
                "    assert out.registry is not None\n    _engines.activate(out.registry)\n",
                "    out = _load_into(root, engines_table, _engines._BASE)\n"
                "    assert out.registry is not None\n",
            )
        ],
    ),
    "MG15": (
        "scoped() leaves the activated registry",
        [
            (
                E,
                "            finally:\n                _ACTIVE.set(active)\n",
                "            finally:\n                pass\n",
            )
        ],
    ),
    "MG16": (
        "ask's engine preload skipped",
        [
            (
                "lattice/spec/ask.py",
                "    problem = _preload_engines(argv)  # before the parser: engine names are --database choices\n",  # noqa: E501
                "    problem = None\n",
            )
        ],
    ),
    "MG17": (
        "the adapter sys.modules check removed",
        [(E, "    if not present or loaded is None:\n        return\n", "    return\n")],
    ),
    "MG18": (
        "a git index accepts a relative source",
        [(M, "        elif index_label is not None:\n", "        elif False:\n")],
    ),
    "MG19": (
        "transfer.fsckObjects dropped",
        [(G, '    ("transfer.fsckObjects", "true"),\n', "")],
    ),
    "MG20": (
        "-c core.hooksPath=/dev/null dropped",
        [(G, '    ("core.hooksPath", "/dev/null"),\n', "")],
    ),
    "MG21": (
        "activation not restored on scope exit",
        [(E, "    finally:\n        _ACTIVE.set(previous)\n", "    finally:\n        pass\n")],
    ),
    "MG22": (
        "an unactivated lookup falls back to the last loaded registry",
        [
            (
                E,
                "_BASE = EngineRegistry()\n",
                "_BASE = EngineRegistry()\n_LAST: list[Any] = [None]\n",
            ),
            (
                E,
                "        return _ACTIVE.get() or _BASE\n",
                "        return _ACTIVE.get() or _LAST[0] or _BASE\n",
            ),
            (
                E,
                "    _ACTIVE.set(registry)\n\n\n@contextlib.contextmanager\ndef using",
                "    _ACTIVE.set(registry)\n    _LAST[0] = registry\n\n\n@contextlib.contextmanager\ndef using",  # noqa: E501
            ),
        ],
    ),
}
TEST_SUITE = [
    "tests/modules",
    "tests/spec/test_engines.py",
    "tests/studio/test_engines_api.py",
]
# the one failure the control run is known to have (see the results: the amended line 130)
KNOWN_FAILURES = ("tests/studio/test_engines_api.py::test_project_engines_and_modules_are_listed",)
# and the one probe row whose prediction the run refutes (a custom module's `entry` is not
# announced as CODE in the install summary)
KNOWN_PROBE_FAILURES = ("M-tamper-T12",)


# ---- plumbing ---------------------------------------------------------------------------------


@dataclass
class Ctx:
    scratch: Path  # <root> of the prereg ("g3")
    code: Path  # the tree whose `lattice` the subprocesses import
    base: Path | None = None  # a4ab4a1's lattice + docs/examples

    @property
    def tmp(self) -> Path:
        return self.scratch / "tmp"

    @property
    def canary(self) -> Path:
        return self.scratch / "canary"

    @property
    def repos(self) -> Path:
        return self.scratch / "repos"

    def work(self, *parts: str) -> Path:
        return self.scratch.joinpath("work", *parts)


def wipe(p: Path) -> Path:
    if p.is_symlink() or p.is_file():
        p.unlink()
    elif p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True)
    return p


def reset_run(c: Ctx) -> None:
    for d in (c.tmp, c.canary, c.scratch / "work", c.scratch / "bin", c.scratch / "log"):
        wipe(d)
    wipe(c.repos)
    (c.scratch / "home").mkdir(exist_ok=True)


@dataclass
class Res:
    rc: int
    out: str
    err: str


def base_env(
    c: Ctx, *, shim_dir: Path | None = None, extra: dict[str, str] | None = None
) -> dict[str, str]:
    path = [str(shim_dir)] if shim_dir else []
    path += [str(Path(REAL_GIT).parent), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    env = {
        "PATH": ":".join(dict.fromkeys(path)),
        "PYTHONPATH": str(c.code),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(c.tmp),
        "HOME": str(c.scratch / "home"),
        "LC_ALL": "C",
    }
    env.update(extra or {})
    return env


def run(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float = 600) -> Res:
    r = subprocess.run(
        argv,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    return Res(r.returncode, r.stdout, r.stderr)


def module_cmd(
    c: Ctx,
    args: list[str],
    *,
    cwd: Path,
    shim: Path | None = None,
    env_extra: dict[str, str] | None = None,
    code: Path | None = None,
    sandbox: bool = False,
) -> Res:
    code_c = Ctx(c.scratch, code or c.code)
    env = base_env(code_c, shim_dir=shim.parent if shim else None, extra=env_extra)
    argv = [sys.executable, "-m", "lattice.modules", *args]
    if sandbox:
        argv = ["sandbox-exec", "-p", "(version 1)(allow default)(deny network*)", *argv]
    return run(argv, cwd=cwd, env=env)


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def snapshot(project: Path) -> dict[str, Any]:
    """The project's listing with a sha256 per file (links as their target)."""
    out: dict[str, str] = {}
    if project.exists():
        for p in sorted(project.rglob("*")):
            rel = p.relative_to(project).as_posix()
            if p.is_symlink():
                out[rel] = "-> " + os.readlink(p)
            elif p.is_file():
                out[rel] = sha256_file(p)
            else:
                out[rel + "/"] = ""
    lock = project / "lattice.modules.lock"
    return {"tree": out, "lock": lock.read_text() if lock.is_file() else None}


def leftovers(c: Ctx) -> list[str]:
    return sorted(n for n in os.listdir(c.tmp) if n.startswith("lattice-module-"))


def canary_files(c: Ctx) -> list[str]:
    return sorted(p.name for p in c.canary.rglob("*"))


def shim_calls(log: Path) -> list[dict[str, Any]]:
    return gf.read_shim_log(log)  # type: ignore[return-value]


def subs(calls: list[dict[str, Any]]) -> list[str]:
    return [gf.subcommand(call["argv"]) for call in calls]


def first_line(text: str) -> str:
    return text.splitlines()[0] if text.strip() else ""


def blob(repo: Path, sha: str, name: str) -> bytes:
    return subprocess.run(
        [REAL_GIT, "cat-file", "blob", f"{sha}:{name}"],
        cwd=repo,
        capture_output=True,
        check=True,
        env=gf.fixture_env(),
    ).stdout


def stream(seed: str, n: int) -> bytes:
    """Deterministic incompressible bytes."""
    return hashlib.shake_256(seed.encode()).digest(n)


def new_shim(c: Ctx, name: str, **kw: Any) -> tuple[Path, Path]:
    log = c.scratch / "log" / f"{name}.log"
    log.unlink(missing_ok=True)
    shim = gf.make_shim(c.scratch / "bin" / name, log, **kw)
    return shim, log


def installed_hash(project: Path, name: str = "mariadb-engine") -> str | None:
    d = project / ".lattice/modules" / name
    if not d.is_dir():
        return None
    from lattice.modules import tree_hash

    return tree_hash(d)


@dataclass
class Row:
    id: str
    ok: bool
    observed: str
    detail: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "ok": self.ok, "observed": self.observed, "detail": self.detail}


def lock_expect(name: str, version: str, sha: str, source: str, commit: str, **extra: str) -> str:
    entry: dict[str, str] = {
        "name": name,
        "version": version,
        "kind": "engine",
        "sha256": sha,
        "source": source,
        "commit": commit,
    }
    entry.update(extra)
    return json.dumps({"version": 1, "modules": [entry]}, indent=2) + "\n"


def hardened(calls: list[dict[str, Any]], scheme: str = "file") -> tuple[bool, list[str]]:
    """Whether every call carries every -c pair and the exact environment of 2.3."""
    problems: list[str] = []
    want = [*HARDEN_PAIRS, f"protocol.{scheme}.allow=always"]
    for i, call in enumerate(calls):
        argv = call["argv"]
        cfg = [argv[j + 1] for j, a in enumerate(argv) if a == "-c"]
        if cfg != want:
            problems.append(f"call {i} ({gf.subcommand(argv)}): -c pairs {cfg}")
        env = {k: v for k, v in call["env"].items() if k not in SHELL_ADDED}
        if set(env) != ENV_KEYS | ({"https_proxy", "HTTPS_PROXY", "no_proxy"} & set(env)):
            problems.append(f"call {i}: env keys {sorted(set(env) ^ ENV_KEYS)}")
        for k, v in ENV_VALUES.items():
            if env.get(k) != v:
                problems.append(f"call {i}: {k}={env.get(k)!r}")
        if env.get("GIT_ALLOW_PROTOCOL") != scheme:
            problems.append(f"call {i}: GIT_ALLOW_PROTOCOL={env.get('GIT_ALLOW_PROTOCOL')!r}")
        if not str(env.get("GIT_ASKPASS", "")).endswith("false") or env.get(
            "GIT_ASKPASS"
        ) != env.get("SSH_ASKPASS"):
            problems.append(f"call {i}: askpass {env.get('GIT_ASKPASS')!r}")
        if env.get("HOME") != env.get("XDG_CONFIG_HOME") or not str(env.get("HOME")).endswith(
            "/home"
        ):
            problems.append(f"call {i}: HOME {env.get('HOME')!r}")
        if any(a in ("clone", "checkout", "submodule", "lfs", "pull", "worktree") for a in argv):
            problems.append(f"call {i}: a forbidden subcommand")
    return not problems, problems


# ---- M-git ------------------------------------------------------------------------------------


def group_git(c: Ctx) -> list[Row]:
    rows: list[Row] = []
    g = c.work("mgit")
    repo = c.repos / "maria"
    c1 = gf.make_repo(repo, gf.module_files(version="0.1.0"))
    c2 = gf.commit(repo, gf.module_files(version="0.2.0"))
    src1, src2 = gf.url(repo, c1), gf.url(repo, c2)

    # a
    p1 = wipe(g / "p1")
    shim, log = new_shim(c, "mgit-a")
    r = module_cmd(c, ["install", src1, "--yes", "--project", str(p1)], cwd=g, shim=shim)
    calls = shim_calls(log)
    snap = snapshot(p1)
    dest = p1 / ".lattice/modules/mariadb-engine"
    files = sorted(p.name for p in dest.iterdir()) if dest.is_dir() else []
    bytes_ok = (
        bool(files)
        and all((dest / n).read_bytes() == blob(repo, c1, n) for n in files)
        and files == sorted(["README.md", "engine.toml", "lattice-module.toml"])
    )
    arch = wipe(g / "archive")
    tar = subprocess.run(
        [REAL_GIT, "archive", c1], cwd=repo, capture_output=True, check=True, env=gf.fixture_env()
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(arch)], input=tar, check=True)
    p_arch = wipe(g / "p-arch")
    ra = module_cmd(c, ["install", str(arch), "--yes", "--project", str(p_arch)], cwd=g)
    sha_arch = installed_hash(p_arch)
    sha_git = installed_hash(p1)
    lock_ok = snap["lock"] == lock_expect("mariadb-engine", "0.1.0", sha_git or "", src1, c1)
    ls = module_cmd(c, ["list", "--project", str(p1)], cwd=g)
    info = module_cmd(c, ["info", "mariadb-engine", "--project", str(p1)], cwd=g)
    reg = run(
        [
            sys.executable,
            "-c",
            "import sys; from lattice.modules import register_modules; "
            "from lattice.spec.engines import REGISTRY; register_modules(sys.argv[1]); "
            "e = REGISTRY.entry('mariadb'); print(e.source if e else None)",
            str(p1),
        ],
        cwd=g,
        env=base_env(c),
    )
    seq = subs(calls)
    want_seq = ["--version", "init", "fetch", "count-objects", "cat-file", "ls-tree", "cat-file"]
    harden_ok, harden_problems = hardened(calls)
    urls = [a for call in calls for a in call["argv"] if "://" in a]
    a_sig = {"rc": r.rc, "out": r.out, "err": r.err, "snap": snap}
    un = module_cmd(c, ["uninstall", "mariadb-engine", "--project", str(p1)], cwd=g)
    after = snapshot(p1)
    checks = {
        "exit0": r.rc == 0,
        "bytes_equal_git_show": bytes_ok,
        "sha_equals_archive_install": ra.rc == 0 and sha_arch is not None and sha_arch == sha_git,
        "lock_exact": lock_ok,
        "list_ok": ls.rc == 0 and " ok " in ls.out,
        "info_commit": f"  commit:          {c1}" in info.out.splitlines(),
        "registers_mariadb": reg.out.strip() == "module",
        "seven_calls": seq == want_seq,
        "hardened": harden_ok,
        "urls_file_only": bool(urls) and all(u.startswith("file://") for u in urls),
        "uninstall_clean": un.rc == 0 and not dest.exists() and after["lock"] is None,
        "no_leftover_tmp": leftovers(c) == [],
    }
    ok = all(checks.values())
    rows.append(
        Row(
            "M-git-a",
            ok,
            "all 12 checks hold"
            if ok
            else "failed: " + ", ".join(k for k, v in checks.items() if not v),
            {
                "checks": checks,
                "stdout": r.out,
                "stderr": r.err,
                "lock": snap["lock"],
                "sha256": sha_git,
                "calls": seq,
                "harden_problems": harden_problems,
                "list": ls.out,
                "files": files,
                "info_head": info.out.splitlines()[:4],
            },
        )
    )

    # b
    p2 = wipe(g / "p2")
    rb = module_cmd(c, ["install", src2, "--yes", "--project", str(p2)], cwd=g)
    sb = snapshot(p2)
    sha_b = installed_hash(p2)
    ok_b = rb.rc == 0 and sb["lock"] == lock_expect(
        "mariadb-engine", "0.2.0", sha_b or "", src2, c2
    )
    rows.append(
        Row(
            "M-git-b",
            ok_b,
            f"exit {rb.rc}; lock version/commit " + ("exact" if ok_b else "differ"),
            {"stdout": rb.out, "stderr": rb.err, "lock": sb["lock"], "c1": c1, "c2": c2},
        )
    )

    # c
    short = c1[:7]
    cases = [
        ("no @", f"git+file://{repo}", PIN_MSG),
        ("@main", f"git+file://{repo}@main", PIN_MSG),
        ("@v0.1.0", f"git+file://{repo}@v0.1.0", PIN_MSG),
        ("abbreviated sha", f"git+file://{repo}@{short}", PIN_MSG),
        ("upper-case sha", f"git+file://{repo}@{c1.upper()}", PIN_MSG),
        ("git+ssh", f"git+ssh://h/r@{c1}", FORM_MSG),
        ("git@h:r", "git@h:r", PIN_MSG),
        ("git+http", f"git+http://h/r@{c1}", FORM_MSG),
        ("userinfo", f"git+https://user:pw@h/r@{c1}", CRED_PART),
        ("https without --allow-remote", f"git+https://example.invalid/r@{c1}", REMOTE_MSG),
        ("#fragment", f"git+https://example.invalid/r@{c1}#x", PIN_MSG),
    ]
    sub: list[dict[str, Any]] = []
    all_ok = True
    for name, source, msg in cases:
        pc = wipe(g / "pc")
        shim_c, log_c = new_shim(c, "mgit-c", fail_fetch=True)  # a refusal never reaches git
        before = snapshot(pc)
        rc_ = module_cmd(c, ["install", source, "--yes", "--project", str(pc)], cwd=g, shim=shim_c)
        calls_c = shim_calls(log_c)
        line = first_line(rc_.err)
        said = line == f"error: {msg}" if msg != CRED_PART else CRED_PART in line
        good = (
            rc_.rc == 2 and said and calls_c == [] and snapshot(pc) == before and leftovers(c) == []
        )
        all_ok = all_ok and good
        sub.append(
            {
                "case": name,
                "source": source,
                "rc": rc_.rc,
                "stderr": rc_.err,
                "git_calls": len(calls_c),
                "message_ok": said,
                "ok": good,
            }
        )
    rows.append(
        Row(
            "M-git-c",
            all_ok,
            f"{sum(s['ok'] for s in sub)}/11 refused with the 2.1 message, 0 git calls",
            {"cases": sub},
        )
    )

    # d
    pd_ = wipe(g / "pd")
    shim_d, log_d = new_shim(c, "mgit-d", fail_fetch=True)
    src_d = f"git+https://example.invalid/r@{c1}"
    rd = module_cmd(
        c, ["install", src_d, "--allow-remote", "--yes", "--project", str(pd_)], cwd=g, shim=shim_d
    )
    calls_d = shim_calls(log_d)
    fetches = [x for x in calls_d if gf.subcommand(x["argv"]) == "fetch"]
    f_argv = fetches[0]["argv"] if fetches else []
    f_env = fetches[0]["env"] if fetches else {}
    h_ok, h_probs = hardened(calls_d, scheme="https")
    ok_d = (
        rd.rc == 2
        and rd.err == f"error: git fetch of {src_d} failed: fatal: shim refuses fetch\n"
        and len(fetches) == 1
        and f_argv[-2:] == ["https://example.invalid/r", c1]
        and f_env.get("GIT_ALLOW_PROTOCOL") == "https"
        and subs(calls_d) == ["--version", "init", "fetch"]
        and h_ok
        and snapshot(pd_) == {"tree": {}, "lock": None}
        and leftovers(c) == []
    )
    rows.append(
        Row(
            "M-git-d",
            ok_d,
            f"exit {rd.rc}; fetch argv tail {f_argv[-2:]}; allow_protocol={f_env.get('GIT_ALLOW_PROTOCOL')}",  # noqa: E501
            {"stderr": rd.err, "calls": subs(calls_d), "harden_problems": h_probs},
        )
    )

    # e
    pe = wipe(g / "pe")
    re_ = module_cmd(c, ["install", src1, "--yes", "--project", str(pe)], cwd=g, sandbox=True)
    snap_e = snapshot(pe)
    e_sig = {"rc": re_.rc, "out": re_.out, "err": re_.err, "snap": snap_e}
    ok_e = e_sig == a_sig
    rows.append(
        Row(
            "M-git-e",
            ok_e,
            "identical to a" if ok_e else f"differs: exit {re_.rc}; {first_line(re_.err)}",
            {"rc": re_.rc, "stderr": re_.err, "same_as_a": ok_e},
        )
    )
    return rows


# ---- M-tamper ---------------------------------------------------------------------------------


def manifest_only(name: str = "mariadb-engine") -> dict[str, str]:
    return gf.module_files(name)


def attempt(
    c: Ctx,
    tag: str,
    source: str,
    *,
    env_extra: dict[str, str] | None = None,
    shim_opts: dict[str, Any] | None = None,
    args: list[str] | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    proj = wipe(c.work("mtamper", project or tag))
    shim, log = new_shim(c, tag, **(shim_opts or {}))
    before = snapshot(proj)
    r = module_cmd(
        c,
        ["install", source, *(args or []), "--yes", "--project", str(proj)],
        cwd=c.work("mtamper"),
        shim=shim,
        env_extra=env_extra,
    )
    calls = shim_calls(log)
    after = snapshot(proj)
    return {
        "rc": r.rc,
        "stdout": r.out,
        "stderr": r.err,
        "err_line": first_line(r.err),
        "calls": subs(calls),
        "batch_reads": sum(1 for x in calls if "--batch" in x["argv"]),
        "written": after != before,
        "snapshot": after,
        "leftover": leftovers(c),
        "project": proj,
    }


def refused(a: dict[str, Any], *needles: str) -> bool:
    return (
        a["rc"] == 2
        and a["err_line"].startswith("error: ")
        and all(n in a["err_line"] for n in needles)
        and not a["written"]
        and a["leftover"] == []
    )


def slim(a: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in a.items() if k not in ("project", "snapshot")}


def group_tamper(c: Ctx) -> list[Row]:
    rows: list[Row] = []
    g = c.work("mtamper")
    mf = gf.module_files()

    def repo_of(name: str, files: dict[str, bytes | str] | None = None) -> tuple[Path, str]:
        path = c.repos / name
        return path, gf.make_repo(path, files if files is not None else gf.module_files())

    # reference: M-git a's sha256
    ref_repo, c1 = repo_of("maria")
    ref = attempt(c, "ref", gf.url(ref_repo, c1))
    ref_sha = installed_hash(ref["project"])

    # T1
    a = attempt(c, "T1", gf.url(ref_repo, "deadbeef" * 5))
    ok = refused(a, "git fetch of ", " failed: ", "not our ref")
    rows.append(Row("M-tamper-T1", ok, f"exit {a['rc']}: {a['err_line'][:90]}", slim(a)))

    # T2
    idx = wipe(g / "T2idx")
    (idx / "mariadb-engine.toml").write_text(
        'name = "mariadb-engine"\nversion = "0.1.0"\n'
        f'source = "{gf.url(ref_repo, c1)}"\nsha256 = "{"0" * 64}"\n'
    )
    a = attempt(c, "T2", "mariadb-engine", args=["--index", str(idx)])
    ok = refused(a, "pins sha256 " + "0" * 64, "the source tree hashes to", ": refused")
    rows.append(Row("M-tamper-T2", ok, f"exit {a['rc']}: {a['err_line'][:90]}", slim(a)))

    # T3
    t3, t3c1 = repo_of("t3")
    t3c2 = gf.commit(t3, gf.module_files(version="0.2.0"))
    first = attempt(c, "T3a", gf.url(t3, t3c1))
    second = attempt(c, "T3b", gf.url(t3, t3c1))
    head = gf.git(t3, "rev-parse", "HEAD")
    ok = (
        head == t3c2
        and first["rc"] == second["rc"] == 0
        and installed_hash(second["project"]) == installed_hash(first["project"]) == ref_sha
        and '"version": "0.1.0"' in (snapshot(second["project"])["lock"] or "")
    )
    rows.append(
        Row(
            "M-tamper-T3",
            ok,
            f"main at C2; @C1 exit {second['rc']}, sha256 = M-git a's: {installed_hash(second['project']) == ref_sha}",  # noqa: E501
            {"first": slim(first), "second": slim(second)},
        )
    )

    # T4
    t4 = c.repos / "t4"
    t4c1 = gf.make_repo(t4, gf.module_files())
    gf.git(t4, "commit", "-q", "--amend", "-m", "rewritten")
    gf.git(t4, "reflog", "expire", "--expire=now", "--expire-unreachable=now", "--all")
    (t4 / ".git" / "ORIG_HEAD").unlink(missing_ok=True)
    a4a = attempt(c, "T4a", gf.url(t4, t4c1))
    gf.git(t4, "gc", "-q", "--prune=now")
    a4b = attempt(c, "T4b", gf.url(t4, t4c1))
    ok = (
        t4c1 == c1
        and a4a["rc"] == 0
        and installed_hash(a4a["project"]) == ref_sha
        and refused(a4b, " failed: ", "not our ref")
    )
    rows.append(
        Row(
            "M-tamper-T4",
            ok,
            f"a: exit {a4a['rc']} (= M-git a: {installed_hash(a4a['project']) == ref_sha}); "
            f"b after gc: exit {a4b['rc']} {a4b['err_line'][-40:]}",
            {"a": slim(a4a), "b": slim(a4b)},
        )
    )

    # T5
    t5 = c.repos / "t5"
    s5 = gf.commit_entries(
        t5, [("100644", n, d) for n, d in mf.items()] + [("120000", "link", "/etc/passwd")]
    )
    a = attempt(c, "T5", gf.url(t5, s5))
    ok = refused(a, "link is a symbolic link (mode 120000): refused") and a["batch_reads"] == 0
    rows.append(
        Row(
            "M-tamper-T5",
            ok,
            f"exit {a['rc']}: {a['err_line'][7:70]}; blobs read: {a['batch_reads']}",
            slim(a),
        )
    )

    # T6
    sub6: list[dict[str, Any]] = []
    manifest_blob = mf["lattice-module.toml"].encode()
    cases6 = ("..", ".git/config", ".GIT/hooks/x")
    for label in cases6:
        repo6 = c.repos / ("t6-" + hashlib.sha256(label.encode()).hexdigest()[:6])
        gf.init_repo(repo6)
        env6 = {"GIT_INDEX_FILE": str(repo6 / ".git" / "unused")}

        def wb(data: bytes, repo6: Path = repo6, env6: dict[str, str] = env6) -> str:
            return gf.git(repo6, "hash-object", "-w", "--stdin", input=data, env=env6)

        def wt(
            entries: list[tuple[str, str, str]], repo6: Path = repo6, env6: dict[str, str] = env6
        ) -> str:
            raw = b"".join(
                m.encode() + b" " + n.encode() + b"\0" + bytes.fromhex(o) for m, n, o in entries
            )
            return gf.git(
                repo6,
                "hash-object",
                "-t",
                "tree",
                "--literally",
                "-w",
                "--stdin",
                input=raw,
                env=env6,
            )

        x, m = wb(b"x\n"), wb(manifest_blob)
        if label == "..":
            tree = wt([("100644", "..", x), ("100644", "lattice-module.toml", m)])
        elif label == ".git/config":
            inner = wt([("100644", "config", x)])
            tree = wt([("40000", ".git", inner), ("100644", "lattice-module.toml", m)])
        else:
            leaf = wt([("100644", "x", x)])
            hooks = wt([("40000", "hooks", leaf)])
            tree = wt([("40000", ".GIT", hooks), ("100644", "lattice-module.toml", m)])
        sha6 = gf.git(repo6, "commit-tree", tree, "-m", "literal", env=env6)
        gf.git(repo6, "update-ref", "refs/heads/main", sha6)
        a = attempt(c, "T6-" + hashlib.sha256(label.encode()).hexdigest()[:6], gf.url(repo6, sha6))
        # why git fails: the same fetch run directly, with and without fsck (the install's
        # message keeps only git's last stderr line, which names index-pack, not the entry)
        why = {}
        for fsck in ("true", "false"):
            bare = wipe(c.work("mtamper", "T6-direct"))
            direct_env = {
                "PATH": base_env(c)["PATH"],
                "HOME": str(bare),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_ALLOW_PROTOCOL": "file",
            }
            subprocess.run(
                [REAL_GIT, "init", "-q", "--bare", "--template=", str(bare / "r")],
                env=direct_env,
                check=True,
                capture_output=True,
            )
            d = subprocess.run(
                [
                    REAL_GIT,
                    "-c",
                    "protocol.allow=never",
                    "-c",
                    "protocol.file.allow=always",
                    "-c",
                    f"transfer.fsckObjects={fsck}",
                    "-c",
                    f"fetch.fsckObjects={fsck}",
                    "-C",
                    str(bare / "r"),
                    "fetch",
                    "-q",
                    "--depth=1",
                    "--no-tags",
                    f"file://{repo6}",
                    sha6,
                ],
                env=direct_env,
                capture_output=True,
                text=True,
            )
            why["fsck_" + fsck] = {"rc": d.returncode, "stderr": d.stderr.strip().splitlines()[:3]}
        fsck_cause = (
            why["fsck_true"]["rc"] != 0
            and why["fsck_false"]["rc"] == 0
            and any("hasDot" in ln for ln in why["fsck_true"]["stderr"])
        )
        sub6.append(
            {
                "entry": label,
                **slim(a),
                "direct_git": why,
                "fsck_is_the_cause": fsck_cause,
                "ok": refused(a, "git fetch of ", " failed: ") and fsck_cause,
            }
        )
    ok = all(s["ok"] for s in sub6)
    rows.append(
        Row(
            "M-tamper-T6",
            ok,
            "; ".join(
                f"{s['entry']}: exit {s['rc']}, last line {s['err_line'][-26:]!r}, "
                f"fsck is the cause: {s['fsck_is_the_cause']}"
                for s in sub6
            ),
            {"cases": sub6},
        )
    )

    # T7
    t7 = c.repos / "t7"
    gm = f'[submodule "s"]\n\tpath = sub\n\turl = ext::sh -c touch {c.canary}/sub\n'
    s7 = gf.commit_entries(
        t7,
        [("100644", n, d) for n, d in mf.items()]
        + [("100644", ".gitmodules", gm), ("160000", "sub", "1" * 40)],
    )
    a = attempt(c, "T7", gf.url(t7, s7))
    ok = refused(a, "sub is a submodule (mode 160000): refused") and canary_files(c) == []
    rows.append(
        Row(
            "M-tamper-T7",
            ok,
            f"exit {a['rc']}: {a['err_line'][7:70]}; canary {canary_files(c)}",
            slim(a),
        )
    )

    # T8
    big = {**mf, "big.bin": stream("t8a", 2 * 1024 * 1024)}
    many = {**mf, **{f"f{i:03d}.txt": f"{i}\n" for i in range(198)}}
    nine = {**mf, **{f"b{i}.bin": stream(f"t8c{i}", 1_000_000) for i in range(9)}}
    huge = {**mf, "huge.bin": stream("t8d", 40 * 1024 * 1024)}
    sub8: list[dict[str, Any]] = []
    for label, files, needles in (
        ("a 2 MiB blob", big, ("big.bin is 2097152 bytes (cap 1048576)",)),
        ("b 201 files", many, ("201 files (cap 200)",)),
        ("c 9 x 1,000,000 B", nine, ("bytes (cap 8388608)",)),
        ("d 40 MiB blob", huge, ("size-pack", "(cap 32 MiB)")),
    ):
        r8 = c.repos / ("t8" + label[0])
        s8 = gf.make_repo(r8, files)
        a = attempt(c, "T8" + label[0], gf.url(r8, s8))
        sub8.append({"case": label, **slim(a), "ok": refused(a, *needles)})
    ok = all(s["ok"] for s in sub8)
    rows.append(
        Row(
            "M-tamper-T8",
            ok,
            "; ".join(f"{s['case'][0]}: exit {s['rc']} {s['err_line'][7:80]}" for s in sub8),
            {"cases": sub8},
        )
    )

    # T9
    t9 = c.repos / "t9"
    s9 = gf.make_repo(t9, gf.module_files())
    touch = f"touch {c.canary}/%s"
    hooks = t9 / ".git" / "hooks"
    for hook in (
        "post-checkout",
        "post-merge",
        "reference-transaction",
        "pre-commit",
        "fsmonitor-watchman",
    ):
        h = hooks / hook
        h.write_text("#!/bin/sh\n" + touch % ("hook-" + hook) + "\n")
        h.chmod(0o755)
    for key, value in (
        ("core.hooksPath", str(hooks)),
        ("core.fsmonitor", touch % "fsmonitor"),
        ("uploadpack.packObjectsHook", touch % "packobjectshook"),
        ("filter.evil.smudge", touch % "smudge"),
        ("filter.evil.clean", touch % "clean"),
        ("filter.evil.process", touch % "process"),
    ):
        gf.git(t9, "config", key, value)
    a = attempt(c, "T9", gf.url(t9, s9))
    ok = (
        a["rc"] == 0
        and installed_hash(a["project"]) == ref_sha
        and canary_files(c) == []
        and a["leftover"] == []
    )
    rows.append(Row("M-tamper-T9", ok, f"exit {a['rc']}; canary {canary_files(c)}", slim(a)))

    # T10
    evil = c.repos / "evil"
    gf.make_repo(evil, gf.module_files("evil-module", "6.6.6"))
    t10 = c.repos / "t10"
    s10 = gf.commit_entries(
        t10,
        [("100644", n, d) for n, d in mf.items()]
        + [
            ("100644", ".gitattributes", "* filter=evil eol=crlf\n"),
            ("100644", ".lfsconfig", "[lfs]\n\turl = https://example.invalid/lfs\n"),
        ],
    )
    u = wipe(g / "T10user")
    hooks_user = wipe(u / "hooks")
    for hook in ("post-checkout", "post-merge", "pre-commit"):
        h = hooks_user / hook
        h.write_text("#!/bin/sh\n" + touch % ("userhook-" + hook) + "\n")
        h.chmod(0o755)
    user_cfg = (
        f'[filter "evil"]\n\tsmudge = {touch % "usersmudge"}\n\tclean = {touch % "userclean"}\n'
        f"\tprocess = {touch % 'userprocess'}\n"
        f"[core]\n\thooksPath = {hooks_user}\n\tfsmonitor = {touch % 'userfsmonitor'}\n"
        f"[credential]\n\thelper = !{touch % 'usercred'}\n"
        f"[trace2]\n\teventTarget = {c.canary}/t2\n"
        f'[url "file://{evil}"]\n\tinsteadOf = file://{t10}\n'
    )
    (u / "gitconfig").write_text(user_cfg)
    (u / ".gitconfig").write_text(user_cfg)
    (u / "xdg" / "git").mkdir(parents=True)
    (u / "xdg" / "git" / "config").write_text(user_cfg)
    a = attempt(
        c,
        "T10",
        gf.url(t10, s10),
        env_extra={
            "GIT_CONFIG_GLOBAL": str(u / "gitconfig"),
            "HOME": str(u),
            "XDG_CONFIG_HOME": str(u / "xdg"),
        },
    )
    d10 = a["project"] / ".lattice/modules/mariadb-engine"
    ok = (
        a["rc"] == 0
        and "  dropped:  .gitattributes, .lfsconfig\n" in a["stdout"]
        and d10.is_dir()
        and not (d10 / ".gitattributes").exists()
        and not (d10 / ".lfsconfig").exists()
        and (d10 / "engine.toml").read_bytes() == mf["engine.toml"].encode()
        and installed_hash(a["project"]) == ref_sha
        and canary_files(c) == []
        and a["leftover"] == []
    )
    rows.append(
        Row(
            "M-tamper-T10",
            ok,
            f"exit {a['rc']}; dropped listed: {'dropped:  .gitattributes, .lfsconfig' in a['stdout']}; canary {canary_files(c)}",  # noqa: E501
            slim(a),
        )
    )

    # T11
    env11 = {
        "GIT_TRACE": f"{c.canary}/trace",
        "GIT_DIR": f"{c.canary}/gd",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": f"{c.canary}/hooks",
        "GIT_CONFIG_PARAMETERS": f"'core.hooksPath={c.canary}/hooks'",
        "GIT_SSH_COMMAND": f"touch {c.canary}/ssh",
        "GIT_EXEC_PATH": f"{c.canary}/exec",
    }
    a = attempt(c, "T11", gf.url(ref_repo, c1), env_extra=env11)
    ok = a["rc"] == 0 and installed_hash(a["project"]) == ref_sha and canary_files(c) == []
    rows.append(Row("M-tamper-T11", ok, f"exit {a['rc']}; canary {canary_files(c)}", slim(a)))

    # T12
    code = f"open({str(c.canary / 'import')!r}, 'w').write('ran')\n"
    t12a = c.repos / "t12a"
    s12a = gf.make_repo(
        t12a,
        {
            **mf,
            "engine.toml": gf.MARIADB.read_text().replace(
                'adapter = "mysql"', 'adapter = "evil:Adapter"'
            ),
            "evil.py": code,
        },
    )
    t12b = c.repos / "t12b"
    s12b = gf.make_repo(
        t12b,
        {
            "lattice-module.toml": 'name = "custom-mod"\nversion = "1"\nkind = "custom"\nentry = "evilentry"\n',  # noqa: E501
            "evilentry.py": code,
        },
    )
    aa = attempt(c, "T12a", gf.url(t12a, s12a))
    ab = attempt(c, "T12b", gf.url(t12b, s12b))
    says_a, says_b = "CODE" in aa["stdout"], "CODE" in ab["stdout"]
    ok = aa["rc"] == 0 and ab["rc"] == 0 and canary_files(c) == [] and says_a and says_b
    rows.append(
        Row(
            "M-tamper-T12",
            ok,
            f"exit {aa['rc']}/{ab['rc']}; canary {canary_files(c)}; summary says CODE: adapter {says_a}, entry {says_b}",  # noqa: E501
            {
                "adapter": slim(aa),
                "entry": slim(ab),
                "says_code_adapter": says_a,
                "says_code_entry": says_b,
            },
        )
    )

    # T13
    t13 = c.repos / "t13"
    s13 = gf.commit_entries(
        t13, [("100644", n, d) for n, d in mf.items()] + [("100644", "Engine.toml", "x\n")]
    )
    a = attempt(c, "T13", gf.url(t13, s13))
    ok = refused(a, "differ only in case: refused")
    rows.append(Row("M-tamper-T13", ok, f"exit {a['rc']}: {a['err_line'][7:90]}", slim(a)))

    # T14
    tag_repo = c.repos / "t14"
    gf.make_repo(tag_repo, gf.module_files())
    gf.git(tag_repo, "tag", "-a", "-m", "release", "v1")
    tag_sha = gf.git(tag_repo, "rev-parse", "v1")
    tree_sha = gf.git(tag_repo, "rev-parse", "HEAD^{tree}")
    at = attempt(c, "T14a", gf.url(tag_repo, tag_sha))
    ao = attempt(c, "T14b", gf.url(tag_repo, tree_sha))
    ok = refused(at, "is a tag, not a commit") and refused(ao, "is a tree, not a commit")
    rows.append(
        Row(
            "M-tamper-T14",
            ok,
            f"tag: exit {at['rc']} {at['err_line'][-44:]}; tree: exit {ao['rc']} {ao['err_line'][-46:]}",  # noqa: E501
            {"tag": slim(at), "tree": slim(ao)},
        )
    )

    # T15
    sub15: list[dict[str, Any]] = []
    for i, name in enumerate((" a.toml", "é.toml", "-x", ".github/x")):
        r15 = c.repos / f"t15-{i}"
        s15 = gf.commit_entries(
            r15, [("100644", n, d) for n, d in mf.items()] + [("100644", name, "x\n")]
        )
        a = attempt(c, f"T15-{i}", gf.url(r15, s15))
        sub15.append({"name": name, **slim(a), "ok": refused(a, "is not an allowed path name")})
    ok = all(s["ok"] for s in sub15)
    rows.append(
        Row(
            "M-tamper-T15",
            ok,
            "; ".join(f"{s['name']!r}: exit {s['rc']}" for s in sub15),
            {"cases": sub15},
        )
    )

    # T16
    a = attempt(c, "T16", gf.url(ref_repo, c1), shim_opts={"version": "2.39.0"})
    ok = refused(a, "older than 2.45.1") and a["calls"] == ["--version"]
    rows.append(
        Row(
            "M-tamper-T16",
            ok,
            f"exit {a['rc']}: {a['err_line'][7:80]}; git calls {a['calls']}",
            slim(a),
        )
    )
    return rows


# ---- M-index ----------------------------------------------------------------------------------


def group_index(c: Ctx) -> list[Row]:
    rows: list[Row] = []
    g = c.work("mindex")
    repos = c.repos
    ra = repos / "a"
    sa = gf.make_repo(ra, gf.module_files("a", "0.1.0"))
    rb = repos / "b"
    sb = gf.make_repo(rb, {"lattice-module.toml": 'name = "b"\nversion = "1.0"\nkind = "custom"\n'})
    rc_ = repos / "c"
    sc = gf.commit_entries(
        rc_,
        [
            ("100644", "lattice-module.toml", 'name = "c"\nversion = "1"\nkind = "custom"\n'),
            ("120000", "link", "/etc/passwd"),
        ],
    )
    rd = repos / "d"
    sd = gf.make_repo(rd, gf.module_files("d", "0.1.0"))

    def entry(name: str, version: str, source: str, sha: str | None = None) -> str:
        pin = f'sha256 = "{sha}"\n' if sha else ""
        return f'name = "{name}"\nversion = "{version}"\nsource = "{source}"\n{pin}'

    ri = repos / "idx"
    sx = gf.make_repo(
        ri,
        {
            "a.toml": entry("a", "0.1.0", gf.url(ra, sa)),
            "b.toml": entry("b", "1.0", gf.url(rb, sb)),
            "c.toml": entry("c", "1", gf.url(rc_, sc)),
            "d.toml": entry("d", "0.1.0", gf.url(rd, sd), "0" * 64),
            "e.toml": entry("e", "1", "../a"),
        },
    )
    index = gf.url(ri, sx)

    def inst(
        tag: str, names: list[str], idx: str, pre: list[str] | None = None
    ) -> tuple[Res, Path, dict[str, Any]]:
        proj = wipe(g / tag)
        if pre:
            module_cmd(c, ["install", *pre, "--index", idx, "--yes", "--project", str(proj)], cwd=g)
        before = snapshot(proj)
        r = module_cmd(
            c, ["install", *names, "--index", idx, "--yes", "--project", str(proj)], cwd=g
        )
        return r, proj, before

    # I1
    r, proj, _ = inst("I1", ["a", "b"], index)
    lock = (
        json.loads((proj / "lattice.modules.lock").read_text())
        if (proj / "lattice.modules.lock").is_file()
        else {"modules": []}
    )
    mods = lock["modules"]
    ok = (
        r.rc == 0
        and [m["name"] for m in mods] == ["a", "b"]
        and all(
            list(m) == ["name", "version", "kind", "sha256", "source", "commit", "index"]
            for m in mods
        )
        and [m["commit"] for m in mods] == [sa, sb]
        and all(m["index"] == index for m in mods)
    )
    rows.append(
        Row(
            "M-index-I1",
            ok,
            f"exit {r.rc}; lock names {[m['name'] for m in mods]}",
            {"stdout": r.out, "stderr": r.err, "lock": mods},
        )
    )

    def refusal_row(
        rid: str,
        names: list[str],
        needle: str,
        *,
        pre: list[str] | None = None,
        tag: str | None = None,
    ) -> None:
        r, proj, before = inst(tag or rid, names, index, pre)
        snap = snapshot(proj)
        unchanged = snap == before
        ok = r.rc == 2 and needle in r.err and unchanged and leftovers(c) == []
        rows.append(
            Row(
                f"M-index-{rid}",
                ok,
                f"exit {r.rc}: {first_line(r.err)[:80]}; written: {not unchanged}",
                {
                    "stdout": r.out,
                    "stderr": r.err,
                    "unchanged": unchanged,
                    "installed": sorted(snap["tree"])[:6],
                },
            )
        )

    refusal_row("I2", ["a", "b", "c"], "link is a symbolic link")
    refusal_row("I3", ["a", "b", "d"], "pins sha256 " + "0" * 64)
    # I4: a installed first, then `a b`
    r4, p4, before4 = inst("I4", ["a", "b"], index, pre=["a"])
    snap4 = snapshot(p4)
    ok4 = (
        r4.rc == 2
        and "module 'a' is already installed" in r4.err
        and snap4 == before4
        and not (p4 / ".lattice/modules/b").exists()
    )
    rows.append(
        Row(
            "M-index-I4",
            ok4,
            f"exit {r4.rc}: {first_line(r4.err)[:80]}; lock unchanged: {snap4['lock'] == before4['lock']}",  # noqa: E501
            {
                "stdout": r4.out,
                "stderr": r4.err,
                "lock_unchanged": snap4["lock"] == before4["lock"],
            },
        )
    )
    refusal_row("I5", ["a", "a"], "module a appears twice in this install")

    # I6
    dix = wipe(g / "dirindex")
    (dix / "a.toml").write_text(entry("a", "0.1.0", gf.url(ra, sa)))
    (dix / "u.toml").write_text(entry("u", "1", f"git+file://{ra}"))
    p6 = wipe(g / "I6")
    r6a = module_cmd(c, ["install", "a", "--index", str(dix), "--yes", "--project", str(p6)], cwd=g)
    lock6 = (
        json.loads((p6 / "lattice.modules.lock").read_text())["modules"]
        if (p6 / "lattice.modules.lock").is_file()
        else []
    )
    p6u = wipe(g / "I6u")
    r6u = module_cmd(
        c, ["install", "u", "--index", str(dix), "--yes", "--project", str(p6u)], cwd=g
    )
    ok6 = (
        r6a.rc == 0
        and len(lock6) == 1
        and lock6[0].get("commit") == sa
        and lock6[0].get("index") == str(dix)
        and r6u.rc == 2
        and PIN_MSG in r6u.err
        and snapshot(p6u) == {"tree": {}, "lock": None}
    )
    rows.append(
        Row(
            "M-index-I6",
            ok6,
            f"pinned: exit {r6a.rc}; unpinned: exit {r6u.rc} {first_line(r6u.err)[:70]}",
            {
                "pinned": {"stdout": r6a.out, "stderr": r6a.err, "lock": lock6},
                "unpinned": {"stderr": r6u.err},
            },
        )
    )

    # I7
    p7 = wipe(g / "I7")
    r7 = module_cmd(c, ["install", "e", "--index", index, "--yes", "--project", str(p7)], cwd=g)
    ok7 = (
        r7.rc == 2
        and "a git index names pinned git sources only" in r7.err
        and snapshot(p7) == {"tree": {}, "lock": None}
    )
    rows.append(
        Row("M-index-I7", ok7, f"exit {r7.rc}: {first_line(r7.err)[-70:]}", {"stderr": r7.err})
    )

    # I8
    p8 = wipe(g / "I8")
    r8 = module_cmd(
        c,
        ["install", "a", "--index", f"git+file://{ri}@main", "--yes", "--project", str(p8)],
        cwd=g,
    )
    ok8 = (
        r8.rc == 2
        and first_line(r8.err) == f"error: {PIN_MSG}"
        and snapshot(p8) == {"tree": {}, "lock": None}
    )
    rows.append(
        Row("M-index-I8", ok8, f"exit {r8.rc}: {first_line(r8.err)[:70]}", {"stderr": r8.err})
    )

    # I9: the test
    node = "tests/modules/test_git_install.py::test_a_failing_rename_removes_what_the_install_made"
    rr = run(
        [sys.executable, "-m", "pytest", node, "-q", "-p", "no:cacheprovider", "--no-header"],
        cwd=c.code,
        env=base_env(c, extra={"PYTHONPATH": str(c.code)}),
    )
    ok9 = rr.rc == 0 and "1 passed" in rr.out
    rows.append(
        Row(
            "M-index-I9",
            ok9,
            "pytest: " + ("1 passed" if ok9 else (rr.out.strip().splitlines() or [""])[-1][:70]),
            {"tail": re.sub(r" in [0-9.]+s", "", rr.out[-800:])},
        )
    )
    return rows


# ---- M-flag -----------------------------------------------------------------------------------

F_COMMANDS = ("check", "ask", "draft")


def flag_argv(cmd: str) -> list[str]:
    if cmd == "check":
        return ["check", "--spec", "x:SPEC", "--app", "a:b", "--db", "c:d"]
    if cmd == "ask":
        return ["ask", "question?", "--spec", "x:SPEC"]
    return ["draft", "REQ.md", "--out", "o.py"]


def choices_of(help_text: str) -> list[str] | None:
    m = re.search(r"--database\s+\{([^}]*)\}", " ".join(help_text.split()))
    return m.group(1).split(",") if m else None


def group_flag(c: Ctx) -> list[Row]:
    g = wipe(c.work("mflag"))
    (g / "mariadb.toml").write_text(gf.MARIADB.read_text())
    (g / "bogus.toml").write_text(gf.MARIADB.read_text() + "\nbogus = 1\n")
    (g / "mysql.toml").write_text(
        gf.MARIADB.read_text().replace('name = "mariadb"', 'name = "mysql"')
    )
    # F6's project: a module installed, then one byte changed
    proj = wipe(g / "proj")
    mod = wipe(g / "m")
    gf.write_files(mod, gf.module_files("m", "1"))
    module_cmd(c, ["install", str(mod), "--yes", "--project", str(proj)], cwd=g, code=c.code)
    (proj / ".lattice/modules/m/README.md").write_text("MariaDB, no code!\n")
    env = base_env(c)

    def spec(cmd: str, extra: list[str]) -> Res:
        return run([sys.executable, "-m", "lattice.spec", *flag_argv(cmd), *extra], cwd=g, env=env)

    want = ["sqlite", "postgres", "mysql", "clickhouse", "mariadb"]
    snippet = (
        "import json, sys\n"
        "from lattice.spec.__main__ import _preload_engines, _parser as check_parser\n"
        "cmd, F = sys.argv[1], sys.argv[2]\n"
        "assert _preload_engines(['--engine-file', F]) is None\n"
        "if cmd == 'check':\n"
        "    ns = check_parser().parse_args(['check', '--spec', 'x:S', '--app', 'a:b', '--db', 'c:d',\n"  # noqa: E501
        "                                    '--database', 'mariadb', '--engine-file', F])\n"
        "elif cmd == 'ask':\n"
        "    from lattice.spec.ask import _parser\n"
        "    ns = _parser().parse_args(['q?', '--spec', 'x:S', '--database', 'mariadb', '--engine-file', F])\n"  # noqa: E501
        "else:\n"
        "    from lattice.spec.draft import _parser\n"
        "    ns = _parser().parse_args(['R.md', '--out', 'o.py', '--database', 'mariadb', '--engine-file', F])\n"  # noqa: E501
        "print(json.dumps({'database': ns.database, 'engine_file': ns.engine_file}))\n"
    )
    rows: list[Row] = []
    first_lines: dict[str, dict[str, str]] = {"F3": {}, "F4": {}, "F5": {}, "F6": {}}
    results: dict[tuple[str, str], tuple[Res, Any]] = {}
    for cmd in F_COMMANDS:
        results[("F1", cmd)] = (spec(cmd, ["--engine-file", "mariadb.toml", "--help"]), None)
        results[("F2", cmd)] = (
            run([sys.executable, "-c", snippet, cmd, str(g / "mariadb.toml")], cwd=g, env=env),
            None,
        )
        results[("F3", cmd)] = (spec(cmd, ["--engine-file", "bogus.toml"]), None)
        results[("F4", cmd)] = (spec(cmd, ["--engine-file", "missing.toml"]), None)
        results[("F5", cmd)] = (spec(cmd, ["--engine-file", "mysql.toml"]), None)
        results[("F6", cmd)] = (spec(cmd, ["--modules", str(proj)]), None)
        results[("F7", cmd)] = (spec(cmd, ["--engine-file=mariadb.toml", "--help"]), None)
        results[("F8", cmd)] = (spec(cmd, ["--database", "mariadb"]), None)
    for f in first_lines:
        for cmd in F_COMMANDS:
            first_lines[f][cmd] = first_line(results[(f, cmd)][0].err)
    for cmd in F_COMMANDS:
        for f in ("F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8"):
            r = results[(f, cmd)][0]
            if f in ("F1", "F7"):
                ch = choices_of(r.out)
                ok = r.rc == 0 and ch == want
                obs = f"exit {r.rc}; --database choices {ch}"
            elif f == "F2":
                try:
                    got = json.loads(r.out.strip().splitlines()[-1])
                except (ValueError, IndexError):
                    got = None
                ok = r.rc == 0 and got == {
                    "database": "mariadb",
                    "engine_file": [str(g / "mariadb.toml")],
                }
                obs = (
                    f"exit {r.rc}; parsed {got}"
                    if got
                    else f"exit {r.rc}: {first_line(r.err)[-70:]}"
                )
            elif f in ("F3", "F4", "F5", "F6"):
                prefix = "error: ModuleError: " if f == "F6" else "error: EngineError: "
                line = first_lines[f][cmd]
                same = len({first_lines[f][x] for x in F_COMMANDS}) == 1
                ok = r.rc == 2 and line.startswith(prefix) and same
                obs = f"exit {r.rc}: {line[:70]}; same in all three: {same}"
            else:
                ok = r.rc == 2 and "invalid choice: 'mariadb'" in r.err
                obs = f"exit {r.rc}: {first_line(r.err.splitlines()[-1] if r.err else '')[-60:]}"
            rows.append(
                Row(
                    f"M-flag-{f}-{cmd}",
                    ok,
                    obs,
                    {"rc": r.rc, "stdout": r.out[-600:], "stderr": r.err[-600:]},
                )
            )
    return rows


# ---- M-hub (in a worker process) --------------------------------------------------------------


def hub_worker(scratch: Path) -> dict[str, Any]:
    """Runs in its own process (``--worker hub``): two projects in one hub, the checks of
    M-hub H1-H5 and M-hub6. Prints one JSON object."""
    import ast
    import tempfile
    import threading

    os.environ["TMPDIR"] = str(scratch / "tmp")
    tempfile.tempdir = None
    head = os.environ.get("PYTHONPATH", "").split(os.pathsep)[0]
    if head:  # the tree under test goes first (a baseline run), before ROOT's
        sys.path.insert(0, head)
    from lattice.modules import install, load_project_engines
    from lattice.spec import engines as eng
    from lattice.studio.server import StubGenerator, make_hub_server, make_server
    from tests.studio.test_server import make_project, request

    root = wipe(scratch / "work" / "mhub")
    code_root = Path(eng.__file__).resolve().parents[2]
    maria = gf.MARIADB.read_text()

    def pg_module(parent: Path) -> Path:
        d = parent / "pg-a"
        d.mkdir(parents=True)
        text = (eng.HERE / "postgres.toml").read_text()
        text = (
            text.replace('name = "postgres"', 'name = "pga"')
            .replace('aliases = ["postgresql"]', "aliases = []")
            .replace('url_schemes = ["postgres", "postgresql"]', 'url_schemes = ["pga"]')
        )
        (d / "engine.toml").write_text(text)
        (d / "lattice-module.toml").write_text(
            'name = "pg-a"\nversion = "1.0.0"\nkind = "engine"\ndescriptor = "engine.toml"\n'
        )
        return d

    a = make_project(root / "a", app_limit=5, repo=False)
    b = make_project(root / "b", app_limit=5, repo=False)
    for p, image in ((a, "mariadb:11"), (b, "mariadb:10.11")):
        (p / "engines").mkdir()
        (p / "engines" / "mariadb.toml").write_text(maria.replace("mariadb:11", image))
        with (p / "studio.toml").open("a") as f:
            f.write('\n[engines]\npaths = ["engines/mariadb.toml"]\n')
    install(a, str(pg_module(root / "mods")), yes=True, out=lambda s: None)
    table = {"paths": ["engines/mariadb.toml"]}

    def serve(srv: Any) -> str:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{srv.server_address[1]}"

    def stop(srv: Any) -> None:
        srv.shutdown()
        srv.server_close()

    def hub() -> Any:
        return make_hub_server(
            [("a", a), ("b", b)], 0, generator_factory=lambda _m: StubGenerator()
        )

    def body(base: str, name: str) -> dict[str, Any]:
        status, out = request("GET", f"{base}/p/{name}/api/engines")
        assert status == 200, (status, out)
        return out  # type: ignore[no-any-return]

    def names(bd: dict[str, Any]) -> list[str]:
        return [r["name"] for r in bd["engines"]]

    def image(bd: dict[str, Any]) -> str | None:
        for r in bd["engines"]:
            if r["name"] == "mariadb":
                return r["server"]["image"]  # type: ignore[no-any-return]
        return None

    out: dict[str, Any] = {}
    # H1
    seen: dict[str, list[dict[str, Any]]] = {"a": [], "b": []}
    for order in ("ABAB", "BABA", "AABB"):
        srv = hub()
        base = serve(srv)
        try:
            for ch in order:
                seen[ch.lower()].append(body(base, ch.lower()))
        finally:
            stop(srv)
    alone: dict[str, dict[str, Any]] = {}
    for name, proj in (("a", a), ("b", b)):
        srv = make_server(proj, 0, generator=StubGenerator())
        base = serve(srv)
        try:
            alone[name] = request("GET", base + "/api/engines")[1]
        finally:
            stop(srv)
    same_orders = all(x == seen[n][0] for n in seen for x in seen[n])
    b0, a0 = seen["b"][0], seen["a"][0]
    h1 = {
        "same_in_every_order": same_orders,
        "same_as_alone": seen["a"][0] == alone["a"] and seen["b"][0] == alone["b"],
        "b_names": names(b0),
        "b_image": image(b0),
        "b_errors": [e[:100] for e in b0["errors"]],
        "a_names": names(a0),
        "a_image": image(a0),
        "a_errors": [e[:100] for e in a0["errors"]],
    }
    h1["ok"] = (
        same_orders
        and h1["same_as_alone"]
        and h1["b_names"] == ["sqlite", "postgres", "mysql", "clickhouse", "mariadb"]
        and h1["b_image"] == "mariadb:10.11"
        and b0["errors"] == []
        and "pga" in h1["a_names"]
    )
    out["H1"] = h1
    # H2
    srv = hub()
    try:
        h = srv.hub
        h.get("a").harness.check_kwargs()
        a_img = eng.find("mariadb").server.image
        a_pga = eng.find("pga") is not None
        h.get("b").harness.check_kwargs()
        b_img = eng.find("mariadb").server.image
        b_pga = eng.find("pga") is not None
        base_names = list(eng._BASE.names()) if hasattr(eng, "_BASE") else None
        b_dbs = "pga" in eng.database_names()
    finally:
        srv.server_close()
    out["H2"] = {
        "a_image": a_img,
        "a_pga": a_pga,
        "b_image": b_img,
        "b_pga": b_pga,
        "b_pga_in_database_names": b_dbs,
        "base_names": base_names,
        "ok": (a_img, a_pga, b_img, b_pga, b_dbs)
        == ("mariadb:11", True, "mariadb:10.11", False, False)
        and base_names == ["postgres", "mysql", "clickhouse"],
    }
    # H3
    srv = hub()
    base = serve(srv)
    try:
        want = {"a": body(base, "a"), "b": body(base, "b")}
        bad: list[str] = []

        def worker(k: int) -> None:
            for i in range(25):
                n = "ab"[(i + k) % 2]
                try:
                    if body(base, n) != want[n]:
                        bad.append(f"t{k} r{i} {n}")
                except Exception as exc:  # noqa: BLE001
                    bad.append(f"t{k} r{i} {type(exc).__name__}")

        ts = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    finally:
        stop(srv)
    out["H3"] = {
        "requests": 200,
        "mismatches": sorted(bad),
        "equal_to_h1": want["a"] == seen["a"][0] and want["b"] == seen["b"][0],
        "ok": bad == [] and want["a"] == seen["a"][0] and want["b"] == seen["b"][0],
    }

    # H4
    def adapter_project(path: Path, who: str) -> Path:
        (path / "engines").mkdir(parents=True)
        (path / "engines" / "adapter.py").write_text(
            "from lattice.spec.db import Database\n\n\nclass FakeDb(Database):\n"
            f'    dialect = "fakedb"\n    origin = "{who}"\n\n    def __init__(self, url):\n'
            "        self.url = url\n\n    def close(self):\n        pass\n"
        )
        text = (eng.HERE / "mysql.toml").read_text()
        text = (
            text.replace('name = "mysql"', 'name = "fakedb"')
            .replace('url_schemes = ["mysql", "mariadb"]', 'url_schemes = ["fakedb"]')
            .replace('url_template = "mysql://', 'url_template = "fakedb://')
            .replace('adapter = "mysql"', 'adapter = "adapter:FakeDb"')
        )
        (path / "engines" / "fakedb.toml").write_text(text)
        return path

    pa_ = adapter_project(root / "pa", "A")
    pb_ = adapter_project(root / "pb", "B")
    sys.modules.pop("adapter", None)
    t4 = {"paths": ["engines/fakedb.toml"]}
    err_b: str | None = None
    origin_a = origin_b = None
    errors_b: list[str] = []
    la = load_project_engines(pa_, t4)
    try:
        origin_a = eng.find("fakedb").adapter_class().origin
    except Exception as exc:  # noqa: BLE001
        origin_a = f"{type(exc).__name__}: {exc}"[:100]
    lb = load_project_engines(pb_, t4)
    errors_b = [e[:110] for e in lb.errors]
    try:
        origin_b = eng.find("fakedb").adapter_class().origin
    except Exception as exc:  # noqa: BLE001
        err_b = f"{type(exc).__name__}: {exc}"
    sys.modules.pop("adapter", None)
    out["H4"] = {
        "a_origin": origin_a,
        "b_origin": origin_b,
        "b_error": err_b,
        "b_load_errors": errors_b,
        "a_load_errors": la.errors,
        "ok": origin_a == "A"
        and err_b is not None
        and "already imported from" in err_b
        and "give this engine's adapter module a distinct name" in err_b,
    }
    # H5
    src = (code_root / "lattice/studio/server.py").read_text()
    sites: list[dict[str, Any]] = []
    for fn in ast.walk(ast.parse(src)):
        if not isinstance(fn, ast.FunctionDef):
            continue
        loads = [
            n.lineno
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", "")) == "load_project_engines"
        ]
        if not loads:
            continue
        looks = [
            n.lineno
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and getattr(n.func, "attr", "")
            in ("find", "resolve", "entries", "database_names", "engine", "by_scheme")
        ]
        sites.append(
            {
                "function": fn.name,
                "load": min(loads),
                "lookups": looks,
                "ok": all(min(loads) < x for x in looks),
            }
        )
    out["H5"] = {"sites": sites, "ok": len(sites) == 3 and all(s["ok"] for s in sites)}
    # M6 / M6b: need the scoped API
    try:
        from lattice.modules import project_engines
    except ImportError:
        out["M6"] = {"ok": False, "note": "no scoped activation in this code"}
        out["M6b"] = {"ok": False, "note": "no scoped activation in this code"}
        return out
    res: dict[str, Any] = {}

    def loader() -> None:
        res["loaded"] = [r.engine.name for r in load_project_engines(a, table).registry.entries()]

    t = threading.Thread(target=loader)
    t.start()
    t.join()

    def never() -> None:
        try:
            res["worker_names"] = list(eng.REGISTRY.names())
            res["worker_maria"] = eng.find("mariadb") is not None
            res["worker_pga"] = eng.find("pga") is not None
            res["worker_dbs"] = list(eng.database_names())
        except Exception as exc:  # noqa: BLE001
            res["worker_error"] = f"{type(exc).__name__}: {exc}"

    t = threading.Thread(target=never)
    t.start()
    t.join()
    out["M6"] = {
        **res,
        "ok": res.get("loaded") == ["postgres", "mysql", "clickhouse", "mariadb", "pga"]
        and res.get("worker_names") == ["postgres", "mysql", "clickhouse"]
        and res.get("worker_maria") is False
        and res.get("worker_pga") is False
        and res.get("worker_dbs") == ["sqlite", "postgres", "mysql", "clickhouse"]
        and "worker_error" not in res,
    }
    import concurrent.futures

    seq: list[Any] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:

        def job_a() -> Any:
            with project_engines(a, table):
                return [eng.find("mariadb").server.image, eng.find("pga") is not None]

        def job_b() -> Any:
            with project_engines(b, table):
                return [eng.find("mariadb").server.image, eng.find("pga") is not None]

        def idle() -> Any:
            return list(eng.REGISTRY.names())

        def boom() -> Any:
            try:
                with project_engines(a, table):
                    raise RuntimeError("boom")
            except RuntimeError:
                return "raised"

        for fn in (job_a, idle, job_b, idle, boom, idle):
            seq.append(pool.submit(fn).result())
    builtin = ["postgres", "mysql", "clickhouse"]
    out["M6b"] = {
        "sequence": seq,
        "ok": seq
        == [["mariadb:11", True], builtin, ["mariadb:10.11", False], builtin, "raised", builtin],
    }
    return out


def group_hub(c: Ctx) -> list[Row]:
    wipe(c.work("mhub"))
    r = run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "hub",
            "--scratch",
            str(c.scratch),
        ],
        cwd=c.code,
        env=base_env(c, extra={"PYTHONPATH": f"{c.code}"}),
        timeout=900,
    )
    try:
        data = json.loads(r.out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        data = {}
    rows: list[Row] = []
    for key, rid in (
        ("H1", "H1"),
        ("H2", "H2"),
        ("H3", "H3"),
        ("H4", "H4"),
        ("H5", "H5"),
        ("M6", "M6"),
        ("M6b", "M6b"),
    ):
        d = data.get(key)
        if d is None:
            rows.append(
                Row(
                    f"M-hub-{rid}",
                    False,
                    f"worker produced nothing (exit {r.rc})",
                    {"stderr": r.err[-1200:]},
                )
            )
            continue
        obs = {k: v for k, v in d.items() if k != "ok"}
        rows.append(
            Row(f"M-hub-{rid}", bool(d.get("ok")), json.dumps(obs, sort_keys=True)[:160], d)
        )
    return rows


# ---- M-regress --------------------------------------------------------------------------------


def r1_script(c: Ctx, code: Path, tag: str) -> list[dict[str, Any]]:
    """The one CLI script of R1 on fixed paths, against ``code``'s lattice."""
    g = wipe(c.scratch / "work" / "r1")
    src = g / "src" / "mariadb-engine"
    gf.write_files(src, gf.module_files())
    index = g / "index"
    index.mkdir()
    from lattice.modules import tree_hash

    (index / "mariadb-engine.toml").write_text(
        'name = "mariadb-engine"\nversion = "0.1.0"\nsource = "../src/mariadb-engine"\n'
        f'sha256 = "{tree_hash(src)}"\n'
    )
    proj = g / "proj"
    proj.mkdir()
    steps: list[dict[str, Any]] = []

    def step(label: str, args: list[str]) -> None:
        r = module_cmd(c, [*args, "--project", str(proj)], cwd=g, code=code)
        steps.append(
            {"step": label, "rc": r.rc, "stdout": r.out, "stderr": r.err, "state": snapshot(proj)}
        )

    step("dir install, no --yes, no tty", ["install", str(src)])
    step("dir install --yes", ["install", str(src), "--yes"])
    step("list", ["list"])
    step("list --json", ["list", "--json"])
    step("info", ["info", "mariadb-engine"])
    step("info --json", ["info", "mariadb-engine", "--json"])
    step("uninstall", ["uninstall", "mariadb-engine"])
    step("file:// install", ["install", src.as_uri(), "--yes"])
    step("uninstall", ["uninstall", "mariadb-engine"])
    step("dir-index install", ["install", "mariadb-engine", "--index", str(index), "--yes"])
    step("list again", ["list"])
    readme = proj / ".lattice/modules/mariadb-engine/README.md"
    data = bytearray(readme.read_bytes())
    data[0] ^= 1
    readme.write_bytes(bytes(data))
    step("list after a 1-byte tamper", ["list"])
    step("info after tamper", ["info", "mariadb-engine"])
    step("uninstall (tampered)", ["uninstall", "mariadb-engine"])
    step("uninstall (absent)", ["uninstall", "mariadb-engine"])
    del tag
    return steps


def extract_base(dst: Path) -> Path:
    wipe(dst)
    tar = subprocess.run(
        [REAL_GIT, "archive", BASE_COMMIT, "lattice", "docs/examples"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(dst)], input=tar, check=True)
    return dst


def help_text(c: Ctx, code: Path, argv: list[str]) -> str:
    r = run([sys.executable, "-m", *argv], cwd=c.scratch, env=base_env(Ctx(c.scratch, code)))
    return r.out + r.err


def group_regress(c: Ctx, heavy: bool = True) -> list[Row]:
    rows: list[Row] = []
    base = c.base
    assert base is not None, "no a4ab4a1 tree"
    # R1
    new_steps = r1_script(c, c.code, "new")
    old_steps = r1_script(c, base, "base")
    diffs = [
        {"step": n["step"], "new": n, "base": o}
        for n, o in zip(new_steps, old_steps, strict=True)
        if n != o
    ]
    rows.append(
        Row(
            "M-regress-R1",
            not diffs and len(new_steps) == len(old_steps) == 15,
            f"{len(new_steps) - len(diffs)}/{len(new_steps)} steps byte-identical to a4ab4a1",
            {
                "differing": diffs[:3],
                "steps": [s["step"] for s in new_steps],
                "exit_codes": [s["rc"] for s in new_steps],
            },
        )
    )
    # R2: a4ab4a1's list and info on a git-installed project
    g = c.work("r2")
    repo = c.repos / "maria_r2"
    c1 = gf.make_repo(repo, gf.module_files())
    proj = wipe(g / "proj")
    ri = module_cmd(c, ["install", gf.url(repo, c1), "--yes", "--project", str(proj)], cwd=g)
    rl = module_cmd(c, ["list", "--project", str(proj)], cwd=g, code=base)
    rf = module_cmd(c, ["info", "mariadb-engine", "--project", str(proj)], cwd=g, code=base)
    ok2 = (
        ri.rc == 0
        and rl.rc == 0
        and " ok " in rl.out
        and rf.rc == 0
        and "mariadb-engine 0.1.0 (engine): ok" in rf.out
    )
    rows.append(
        Row(
            "M-regress-R2",
            ok2,
            f"a4ab4a1 list exit {rl.rc}, info exit {rf.rc}: {first_line(rf.out)}",
            {"list": rl.out, "info_head": rf.out.splitlines()[:5], "stderr": rl.err + rf.err},
        )
    )
    # R3
    if heavy:
        rows.append(r3(c))
    # R4
    rep: dict[str, Any] = {}
    all_ok = True
    for label, argv in (
        ("module --help", ["lattice.modules", "install", "--help"]),
        ("module top --help", ["lattice.modules", "--help"]),
        ("lattice --help", ["lattice.cli", "--help"]),
    ):
        new_t = help_text(c, c.code, argv)
        old_t = help_text(c, base, argv)
        diff = [
            ln
            for ln in difflib.unified_diff(
                old_t.splitlines(), new_t.splitlines(), "a4ab4a1", "now", lineterm="", n=0
            )
            if ln[:1] in "+-" and not ln.startswith(("+++", "---"))
        ]
        allowed = re.compile(
            r"--index|--allow-remote|source|install|module|DIR\|URL|SOURCE|pinned|sha|\[--", re.I
        )
        stray = [ln for ln in diff if not allowed.search(ln)]
        rep[label] = {"diff": diff, "stray": stray}
        all_ok = all_ok and not stray and bool(old_t.strip())
    rows.append(
        Row(
            "M-regress-R4",
            all_ok,
            "; ".join(
                f"{k}: {len(v['diff'])} changed lines, {len(v['stray'])} unexplained"
                for k, v in rep.items()
            ),
            rep,
        )
    )
    return rows


def pytest_counts(c: Ctx, targets: list[str]) -> tuple[int, str]:
    r = run(
        [sys.executable, "-m", "pytest", *targets, "-q", "-p", "no:cacheprovider", "--no-header"],
        cwd=c.code,
        env=base_env(c, extra={"PYTHONPATH": str(c.code)}),
        timeout=3600,
    )
    tail = next(
        (ln for ln in reversed(r.out.splitlines()) if re.search(r"\d+ (passed|failed)", ln)), ""
    )
    failed = re.findall(r"^FAILED (\S+)", r.out, re.M)
    tail = re.sub(r" in [0-9.]+s.*$", "", tail.strip())  # the timing is not part of the result
    return r.rc, tail + (f" [failed: {', '.join(failed)}]" if failed else "")


def r3(c: Ctx) -> Row:
    detail: dict[str, Any] = {}
    unchanged = {}
    for f in (
        "tests/modules/test_modules.py",
        "tests/spec/test_engines.py",
        "tests/studio/test_projects.py",
        "tests/test_cli.py",
        "tests/spec/test_ask.py",
        "tests/spec/test_draft.py",
    ):
        d = subprocess.run(
            [REAL_GIT, "diff", BASE_COMMIT, "--", f], cwd=ROOT, capture_output=True, text=True
        ).stdout
        unchanged[f] = d == ""
    d = subprocess.run(
        [REAL_GIT, "diff", "-U0", BASE_COMMIT, "--", "tests/studio/test_engines_api.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout
    changed = [ln for ln in d.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
    detail["unchanged"] = unchanged
    detail["engines_api_diff_lines"] = changed
    expect = {
        "tests/modules/test_modules.py": 26,
        "tests/spec/test_engines.py": 60,
        "tests/studio/test_projects.py": 24,
        "tests/test_cli.py": 21,
        "tests/spec/test_ask.py": 34,
        "tests/spec/test_draft.py": 31,
        "tests/studio/test_engines_api.py": 5,
    }
    counts: dict[str, str] = {}
    all_pass = True
    for f, n in expect.items():
        rc, tail = pytest_counts(c, [f])
        passed = re.search(r"(\d+) passed", tail)
        skipped = re.search(r"(\d+) skipped", tail)
        total = (int(passed.group(1)) if passed else 0) + (int(skipped.group(1)) if skipped else 0)
        counts[f] = f"rc {rc}: {tail} (collected {n})"
        all_pass = all_pass and rc == 0 and total == n and "failed" not in tail
    detail["pytest"] = counts
    chk = run(
        ["sh", str(ROOT / "dogfood/studio/check.sh")],
        cwd=ROOT / "dogfood/studio",
        env={
            **base_env(
                c, extra={"PYTHONPATH": f"{c.code}", "LATTICE_STUDIO_PYTHONPATH": str(c.code)}
            ),
            "UV_NO_SYNC": "1",
            "UV_PROJECT_ENVIRONMENT": os.environ.get("UV_PROJECT_ENVIRONMENT", ""),
            "PATH": os.environ.get("PATH", ""),
        },
        timeout=3600,
    )
    detail["check_sh"] = {
        "rc": chk.rc,
        "head": first_line(chk.out),
        "tail": chk.out[-300:] + chk.err[-300:],
    }
    ok = all(unchanged.values()) and all_pass and chk.rc == 0 and len(changed) == 2
    return Row(
        "M-regress-R3",
        ok,
        f"unchanged files {sum(unchanged.values())}/{len(unchanged)}; suites "
        f"{sum(v.startswith('rc 0') for v in counts.values())}/{len(counts)} pass; "
        f"check.sh exit {chk.rc}: {first_line(chk.out)[:60]}",
        detail,
    )


# ---- the runner -------------------------------------------------------------------------------

RUNNERS = {
    "git": group_git,
    "tamper": group_tamper,
    "index": group_index,
    "flag": group_flag,
    "hub": group_hub,
    "regress": group_regress,
}


def run_group(name: str, c: Ctx, runs: int, heavy: bool) -> list[dict[str, Any]]:
    """``runs`` identical executions of one group; one result per row (the heavy R3 of
    M-regress runs once, in the first execution: pytest timings are not bytes)."""
    per_run: list[dict[str, Row]] = []
    order: list[str] = []
    for i in range(runs):
        reset_run(c)
        fn = RUNNERS[name]
        got = fn(c, heavy and i == 0) if name == "regress" else fn(c)  # type: ignore[call-arg]
        per_run.append({r.id: r for r in got})
        for r in got:
            if r.id not in order:
                order.append(r.id)
    out: list[dict[str, Any]] = []
    for rid in order:
        row = next(r[rid] for r in per_run if rid in r)
        sigs = [json.dumps(r[rid].to_json(), sort_keys=True) for r in per_run if rid in r]
        once = rid == "M-regress-R3"
        deterministic = once or (len(set(sigs)) == 1 and len(sigs) == len(per_run))
        differing = None
        if not deterministic:  # keep the evidence: the first run that is not run 0
            ref = (
                json.dumps(per_run[0][rid].to_json(), sort_keys=True) if rid in per_run[0] else None
            )
            for k, r in enumerate(per_run):
                got = json.dumps(r[rid].to_json(), sort_keys=True) if rid in r else None
                if got != ref:
                    differing = {"run": k, "row": json.loads(got) if got else None}
                    break
        out.append(
            {
                "id": rid,
                "predicted": PREDICTED.get(rid, ""),
                "observed": row.observed,
                "rule_holds": row.ok,
                "runs": 1 if once else len(per_run),
                "identical": deterministic,
                "pass": row.ok and deterministic,
                "detail": row.detail,
                **({"differing_run": differing} if differing else {}),
            }
        )
    return out


def print_table(rows: list[dict[str, Any]]) -> None:
    for r in rows:
        pred = r["predicted"].replace("\n", " ")
        obs = str(r["observed"]).replace("\n", " ")
        flag = "PASS" if r["pass"] else "FAIL" + ("" if r["identical"] else " (runs differ)")
        print(f"{r['id']} | {pred[:78]} | {obs[:100]} | {flag}")


def probes_main(args: argparse.Namespace) -> int:
    scratch = Path(args.scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    groups = [g for g in args.groups.split(",") if g]
    code = Path(args.code_root).resolve() if args.code_root else ROOT
    base = Path(args.base_tree).resolve() if args.base_tree else None
    c = Ctx(scratch, code, base)
    if "regress" in groups and base is None:
        c.base = extract_base(scratch / "base-tree")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict[str, Any]] = []
    t0 = time.monotonic()
    for grp in groups:
        t1 = time.monotonic()
        rows = run_group(grp, c, args.runs, not args.skip_heavy)
        (out_dir / f"M-{grp}.json").write_text(
            json.dumps({"group": grp, "runs": args.runs, "rows": rows}, indent=1)
        )
        all_rows += rows
        print(
            f"# group {grp}: {sum(r['pass'] for r in rows)}/{len(rows)} pass ({time.monotonic() - t1:.0f}s)",  # noqa: E501
            file=sys.stderr,
            flush=True,
        )
    if args.code_root:  # a baseline run: the prediction is that the rule FAILS where listed
        for r in all_rows:
            if r["id"] in BASELINE_EXPECT:
                exp = BASELINE_EXPECT[r["id"]]
                r["predicted"] = f"baseline: the rule {'holds' if exp else 'fails'}"
                r["pass"] = r["rule_holds"] == exp
            else:
                r["predicted"] = "(no baseline prediction)"
                r["pass"] = True
    print_table(all_rows)
    passed = sum(1 for r in all_rows if r["pass"])
    summary = {
        "probes": [
            {
                "id": r["id"],
                "predicted": r["predicted"],
                "observed": r["observed"],
                "pass": r["pass"],
                "identical_runs": r["identical"],
            }
            for r in all_rows
        ],
        "pass": passed,
        "total": len(all_rows),
        "runs": args.runs,
        "seconds": round(time.monotonic() - t0, 1),
        "git": subprocess.run(
            [REAL_GIT, "--version"], capture_output=True, text=True
        ).stdout.strip(),
        "failing": [r["id"] for r in all_rows if not r["pass"]],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    print(f"probes: {passed}/{len(all_rows)} pass")
    return 0 if passed == len(all_rows) else 1


# ---- mutant mode ------------------------------------------------------------------------------


def copy_tree(dst: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".ruff_cache")
    for rel in ("lattice", "tests", "docs/examples"):
        shutil.copytree(ROOT / rel, dst / rel, ignore=ignore)
    (dst / "dogfood").mkdir()
    shutil.copy(ROOT / "dogfood/module_market_probes.py", dst / "dogfood/module_market_probes.py")


def apply_edits(dst: Path, mid: str) -> None:
    _what, edits = MUTANTS[mid]
    for rel, old, new in edits:
        path = dst / rel
        text = path.read_text(encoding="utf-8")
        n = text.count(old)
        if n != 1:
            raise SystemExit(
                f"{mid}: the patched text occurs {n} times in {rel} (must be once): {old[:60]!r}"
            )
        path.write_text(text.replace(old, new), encoding="utf-8")


def run_one(mid: str, args: argparse.Namespace, base_tree: Path) -> dict[str, Any]:
    t0 = time.monotonic()
    work = Path(args.scratch).resolve() / "mutants" / mid
    wipe(work)
    code = work / "code"
    code.mkdir()
    copy_tree(code)
    if mid != "control":
        if mid in MUTANTS:
            apply_edits(code, mid)
        else:  # an a4ab4a1 mutant MM1-MM5
            sys.path.insert(0, str(ROOT / "dogfood"))
            import module_mutants as mm  # noqa: PLC0415

            _w, rel, old, new = mm.MUTANTS[mid]
            p = code / rel
            text = p.read_text()
            assert text.count(old) == 1, f"{mid}: patched text occurs {text.count(old)} times"
            p.write_text(text.replace(old, new))
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    env["PYTHONPATH"] = str(code)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TMPDIR"] = str(wipe(work / "pytmp"))  # pytest's tmp_path stays inside the scratch root
    pt = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *TEST_SUITE,
            "-q",
            "-p",
            "no:cacheprovider",
            "-rf",
            "--no-header",
        ],
        cwd=code,
        env=env,
        capture_output=True,
        text=True,
        timeout=3600,
    )
    failed = sorted({m.group(1) for m in re.finditer(r"^FAILED (\S+)", pt.stdout, re.M)})
    summary = next(
        (ln for ln in reversed(pt.stdout.splitlines()) if re.search(r"\d+ (passed|failed)", ln)), ""
    ).strip()
    out_dir = work / "data"
    pr = subprocess.run(
        [
            sys.executable,
            str(code / "dogfood/module_market_probes.py"),
            "--scratch",
            str(work / "g3"),
            "--runs",
            str(args.mutant_runs),
            "--out",
            str(out_dir),
            "--skip-heavy",
            "--base-tree",
            str(base_tree),
            "--groups",
            args.mutant_groups,
        ],
        cwd=code,
        env=env,
        capture_output=True,
        text=True,
        timeout=7200,
    )
    try:
        probe_rows = json.loads((out_dir / "summary.json").read_text())["probes"]
    except (OSError, ValueError):
        probe_rows = []
    failing = sorted(r["id"] for r in probe_rows if not r["pass"])
    return {
        "id": mid,
        "pytest_failed": failed,
        "pytest_summary": summary,
        "probes_failing": failing,
        "probe_rows": len(probe_rows),
        "probe_exit": pr.returncode,
        "probe_stderr_tail": pr.stderr[-400:] if not probe_rows else "",
        "seconds": round(time.monotonic() - t0, 1),
    }


def mutants_main(args: argparse.Namespace) -> int:
    scratch = Path(args.scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    base_tree = extract_base(scratch / "base-tree")
    ids = args.mutants or [*MUTANTS]
    with_mm = "MM" if args.with_mm else ""
    if with_mm:
        sys.path.insert(0, str(ROOT / "dogfood"))
        import module_mutants as mm  # noqa: PLC0415

        ids = [*ids, *(m for m in mm.MUTANTS if m not in ids)]
    for mid in ids:  # every patch must apply exactly once before anything runs
        if mid in MUTANTS:
            with_dir = scratch / "dry"
            wipe(with_dir)
            copy_tree(with_dir)
            apply_edits(with_dir, mid)
    t0 = time.monotonic()
    control = run_one("control", args, base_tree)
    known = set(KNOWN_FAILURES)
    control_new_tests = [t for t in control["pytest_failed"] if t not in known]
    control_new_probes = [p for p in control["probes_failing"] if p not in KNOWN_PROBE_FAILURES]
    control_ok = not control_new_tests and not control_new_probes and control["probe_rows"] > 0
    print(
        f"control: pytest {control['pytest_summary']}; failed {control['pytest_failed']}; "
        f"probes failing {control['probes_failing']} of {control['probe_rows']}; "
        f"{'PASS' if control_ok else 'NOT CLEAN'} (known findings, excluded: "
        f"{sorted(known)} {list(KNOWN_PROBE_FAILURES)})",
        flush=True,
    )
    results: dict[str, dict[str, Any]] = {"control": control}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        futs = {ex.submit(run_one, mid, args, base_tree): mid for mid in ids}
        for fut in concurrent.futures.as_completed(futs):
            res = fut.result()
            results[res["id"]] = res
    killed = 0
    lines = []
    for mid in ids:
        res = results[mid]
        by_tests = [t for t in res["pytest_failed"] if t not in control["pytest_failed"]]
        by_probes = [p for p in res["probes_failing"] if p not in control["probes_failing"]]
        dead = bool(by_tests or by_probes) or res["probe_exit"] not in (0, 1)
        killed += dead
        res["killed"] = dead
        res["by_tests"], res["by_probes"] = by_tests, by_probes
        what = MUTANTS[mid][0] if mid in MUTANTS else "a4ab4a1 mutant"
        lines.append(
            f"{mid} | {what} | predicted: {MG_PREDICTED.get(mid, 'caught by the tests')} | "
            f"{'killed' if dead else 'SURVIVED'} | probes: {', '.join(by_probes) or '-'} | "
            f"tests: {', '.join(t.split('::')[-1] for t in by_tests) or '-'}"
        )
    print("\n".join(lines))
    if args.out_json:
        Path(args.out_json).write_text(
            json.dumps(
                {
                    "control": control,
                    "mutants": results,
                    "seconds": round(time.monotonic() - t0, 1),
                },
                indent=1,
            )
        )
    print(f"mutants: {killed}/{len(ids)} killed")
    return 0 if control_ok and killed == len(ids) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scratch", required=True, help="the scratch root (fixed paths live here)")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--groups", default=",".join(GROUPS))
    ap.add_argument("--out", default=str(DATA))
    ap.add_argument(
        "--code-root", default=None, help="import lattice from this tree (baseline runs)"
    )
    ap.add_argument(
        "--base-tree", default=None, help="a4ab4a1's lattice and docs/examples, extracted"
    )
    ap.add_argument("--skip-heavy", action="store_true", help="skip M-regress R3 (the test suites)")
    ap.add_argument("--worker", choices=["hub"], default=None)
    ap.add_argument(
        "--mutants", nargs="*", default=None, help="run mutant mode (ids, default all MG)"
    )
    ap.add_argument("--with-mm", action="store_true", help="mutant mode: also a4ab4a1's MM1-MM5")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--mutant-runs", type=int, default=2)
    ap.add_argument("--mutant-groups", default="git,tamper,index,flag,hub,regress")
    ap.add_argument("--out-json", default=None)
    args = ap.parse_args(argv)
    if args.worker == "hub":
        print(json.dumps(hub_worker(Path(args.scratch).resolve())))
        return 0
    if args.mutants is not None:
        return mutants_main(args)
    return probes_main(args)


if __name__ == "__main__":
    sys.exit(main())
