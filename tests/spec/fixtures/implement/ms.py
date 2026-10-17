"""The mini-shop corpus of the implement spike (docs/results/2026-10-17-implement-verify-prereg.md,
section 3): the app, its gate and the base spec in a fresh git repository; the candidate spec
directories (copies of Spike D's minishop impact fixtures, made self-contained), the scripted
rebuild rounds and Spike D's report for a candidate.

Loaded by path (``importlib``) by ``tests/studio/test_implement.py`` and ``dogfood/
implement_probes.py``; it reuses ``tests/spec/minishop.py`` (sources and ``build_db``) read-only.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.spec import minishop  # noqa: E402

CANDIDATES = (
    "status_changed",
    "reword",
    "removed_rule",
    "new_request",
    "conflict",
    "bad_invariant",
)
STUBS = HERE / "stubs"
FLOWS = HERE / "repo" / "flows.json"
E_OK_DIFF = HERE / "E_ok.diff"

# the rebuild rounds and the candidate of each dev change (prereg section 3)
CHANGES: dict[str, dict[str, Any]] = {
    "i1": {"candidate": "status_changed", "stub": "i1", "max_rounds": 3},
    "i2": {"candidate": "status_changed", "stub": "i2", "max_rounds": 3},
    "i3": {"candidate": "status_changed", "stub": "i3", "max_rounds": 3},
    "i4": {"candidate": "status_changed", "stub": "i4", "max_rounds": 3},
    "i5": {"candidate": "reword", "stub": "none", "max_rounds": 3},
    "i6": {"candidate": "removed_rule", "stub": "none", "max_rounds": 3},
    "i7": {"candidate": "new_request", "stub": "none", "max_rounds": 3},
    "i8": {"candidate": "status_changed", "stub": "i8", "max_rounds": 2},
    "i9": {"candidate": "status_changed", "stub": "i9", "max_rounds": 2},
    "i10": {"candidate": "conflict", "stub": "none", "max_rounds": 3},
    "i11": {"candidate": "status_changed", "stub": None, "max_rounds": 3, "agent": "oracle"},
}


def git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


def candidate_dir(name: str) -> Path:
    return HERE / name


def build_ms_repo(dest: Path, stub: str | None = None) -> Path:
    """The `ok` app, the harness, the gate and the base spec in a new git repository at `dest`
    (project dir = repository root), committed on branch ``main``. `stub`: the name of a script in
    ``stubs/``, written as an UNTRACKED ``stub-rebuild.json`` (the stub agent reads it from the
    owner's project dir)."""
    dest.mkdir(parents=True)
    pkg = dest / "shop"
    pkg.mkdir()
    for rel, src in minishop.sources("ok").items():
        (pkg / rel).write_text(src.replace("PKG", "shop"))
    for f in (HERE / "repo").iterdir():
        if f.name == "flows.json":
            continue
        shutil.copy2(f, dest / f.name)
    for f in ("REQUIREMENTS.md", "spec.py", "spec.trace.json"):
        shutil.copy(HERE / "base" / f, dest / f)
    git(dest, "init", "-q", "-b", "main")
    git(dest, "config", "user.email", "implement@example.com")
    git(dest, "config", "user.name", "Implement Fixture")
    git(dest, "add", "-A")
    git(dest, "commit", "-q", "-m", "mini shop")
    if stub:
        shutil.copy(STUBS / f"{stub}.json", dest / "stub-rebuild.json")
    return dest


def commit_stub(repo: Path) -> None:
    """Track the stub script in the owner's HEAD (as a project that keeps its scripted rounds in git
    would): nothing untracked then stands in the way of a fast-forward merge of the build."""
    git(repo, "add", "stub-rebuild.json")
    git(repo, "commit", "-q", "-m", "stub script")


def flows_doc() -> dict[str, Any]:
    return json.loads(FLOWS.read_text())  # type: ignore[no-any-return]


def standin_inventory(flows_path: Path = FLOWS) -> Any:
    """A duck-typed inventory carrying only the verified flows (the mini shop is Python: no
    extract), enough for tiers 1 and 2 of Spike D's report."""
    return SimpleNamespace(
        workflows=json.loads(flows_path.read_text()),
        stale=None,
        mismatch=None,
        extract_sha256=None,
        workflows_sha256=hashlib.sha256(flows_path.read_bytes()).hexdigest(),
        directory=None,
        app_root=None,
        package="",
    )


def d_report(project: Path, candidate: Path, shop_root: Path, tiers: str = "12") -> dict[str, Any]:
    """Spike D's report for `candidate` against `project`'s spec, from D's API, tiers 1 and 2, the
    verified flows through the stand-in inventory; the checks run in this process on an app
    written under `shop_root` (a fresh package name, so no import collides)."""
    from lattice.spec import impact_report as ir
    from lattice.spec.check import check

    app_dir, package = minishop.write_app(shop_root, "ok")

    def run(spec: Any, layers: str) -> Any:
        return check(
            spec,
            minishop.app_factory(package),
            minishop.build_db,
            layers=layers,
            app_dir=app_dir,
            package=package,
            login=minishop.login,
        )

    inp = ir.build_inputs(project=project, candidate=candidate, inventory=None)
    inp.inventory = standin_inventory()
    return ir.build_report(inp, tiers=tiers, runner=run, baseline="run", layers="ACD")


# ---- running one dev change, with what a guard needs to see -----------------------------------


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def file_snapshot(repo: Path) -> dict[str, tuple[Any, ...]]:
    """sha256, mode and mtime of every file and directory of the owner's worktree (hidden files
    included, ``.git`` excluded), and of ``.git/{HEAD,index,packed-refs,config,info,refs}``."""
    out: dict[str, tuple[Any, ...]] = {}
    for p in sorted(repo.rglob("*")):
        rel = p.relative_to(repo).as_posix()
        if rel == ".git" or rel.startswith(".git/"):
            continue
        st = p.lstat()
        digest = _sha256(p) if p.is_file() and not p.is_symlink() else "-"
        out[rel] = (digest, st.st_mode, st.st_mtime_ns)
    g = repo / ".git"
    for name in ("HEAD", "index", "packed-refs", "config"):
        p = g / name
        if p.is_file():
            out[f".git/{name}"] = (_sha256(p), p.stat().st_mode, p.stat().st_mtime_ns)
    for sub in ("info", "refs"):
        for p in sorted((g / sub).rglob("*")):
            if p.is_file():
                st = p.stat()
                out[f".git/{p.relative_to(g).as_posix()}"] = (
                    _sha256(p),
                    st.st_mode,
                    st.st_mtime_ns,
                )
    return out


def snapshot_changes(
    a: dict[str, tuple[Any, ...]], b: dict[str, tuple[Any, ...]], allowed_new: set[str]
) -> dict[str, list[str]]:
    """What differs between two :func:`file_snapshot` s: changed, removed, and new entries beyond
    `allowed_new` (``.git/refs/heads/implement/<eid>`` and ``.../rebuild/<eid>``)."""
    return {
        "changed": sorted(k for k in a if k in b and a[k] != b[k]),
        "removed": sorted(k for k in a if k not in b),
        "new": sorted(k for k in b if k not in a and k not in allowed_new),
    }


def worktrees(repo: Path) -> list[str]:
    out = git(repo, "worktree", "list", "--porcelain")
    return sorted(
        ln.removeprefix("worktree ") for ln in out.splitlines() if ln.startswith("worktree ")
    )


class Run:
    """One dev change run through :func:`lattice.studio.implement.implement`."""

    def __init__(self) -> None:
        self.cid = ""
        self.repo: Path = Path()
        self.work: Path = Path()
        self.tmp: Path = Path()
        self.impact_path: Path = Path()
        self.report_in: dict[str, Any] = {}
        self.out: Any = None
        self.refusal: Any = None
        self.seconds = 0.0
        self.files_before: dict[str, tuple[Any, ...]] = {}
        self.files_after: dict[str, tuple[Any, ...]] = {}
        self.worktrees_before: list[str] = []
        self.worktrees_after: list[str] = []
        self.head = ""
        self.eid = ""

    @property
    def tier4(self) -> dict[str, Any]:
        return self.out.report["tier4"]  # type: ignore[no-any-return]

    def drift(self) -> dict[str, list[str]]:
        allowed = {f".git/refs/heads/implement/{self.eid}", f".git/refs/heads/rebuild/{self.eid}"}
        return snapshot_changes(self.files_before, self.files_after, allowed)

    def review_worktrees(self) -> list[str]:
        return sorted(set(self.worktrees_after) - set(self.worktrees_before))

    def cleanup(self) -> None:
        for wt in self.review_worktrees():
            subprocess.run(["git", "-C", str(self.repo), "worktree", "remove", "--force", wt],
                           capture_output=True)  # fmt: skip
            shutil.rmtree(wt, ignore_errors=True)
        subprocess.run(["git", "-C", str(self.repo), "worktree", "prune"], capture_output=True)


def run_change(
    scratch: Path,
    cid: str,
    *,
    side_runner: Any = None,
    agent: str | None = None,
    report_hook: Any = None,
    repo_hook: Any = None,
    opts_hook: Any = None,
    tag: str = "",
    d_cache: dict[str, Any] | None = None,
) -> Run:
    """Build the mini-shop repo for dev change `cid` under `scratch`, Spike D's report for its
    candidate, and run ``implement`` on it (the Python API), measuring the owner's files and
    worktrees around the call. `report_hook(report)` edits D's report before it is written;
    `repo_hook(repo)` edits the repo before the snapshot; `opts_hook(opts)` the options.
    Temporary directories (the rebuild's kept worktree) go under ``scratch/tmp``."""
    import tempfile
    import time

    from lattice.spec import impact_report as ir
    from lattice.spec.impact_tier4 import Refusal
    from lattice.studio import implement as im
    from lattice.studio.rebuild import STUB_FILE  # noqa: F401

    c = CHANGES[cid]
    r = Run()
    r.cid = cid
    root = scratch / f"{cid}{tag}"
    r.repo = build_ms_repo(root / "repo", c["stub"])
    r.tmp = root / "tmp"
    r.tmp.mkdir()
    r.work = root / "work"
    cand = candidate_dir(c["candidate"])
    key = c["candidate"]
    if d_cache is not None and key in d_cache:
        rep = json.loads(json.dumps(d_cache[key]))
    else:
        rep = d_report(r.repo, cand, root / "shop")
        if d_cache is not None:
            d_cache[key] = json.loads(json.dumps(rep))
    if report_hook:
        report_hook(rep)
    r.report_in = rep
    r.impact_path = root / "impact.json"
    r.impact_path.write_text(ir.report_json(rep))
    if repo_hook:
        repo_hook(r.repo)
    agent = agent or c.get("agent") or "stub"
    opts = im.Options(
        project=r.repo,
        impact=r.impact_path,
        candidate=cand,
        flows=FLOWS,
        agent=agent,
        max_rounds=c["max_rounds"],
        work=r.work,
        ref_diff=E_OK_DIFF if agent == "oracle" else None,
    )
    if opts_hook:
        opts_hook(opts)
    r.head = git(r.repo, "rev-parse", "HEAD")
    r.files_before = file_snapshot(r.repo)
    r.worktrees_before = worktrees(r.repo)
    old_tmp = tempfile.tempdir
    tempfile.tempdir = str(r.tmp)
    t0 = time.perf_counter()
    try:
        from lattice.spec.impact_tier4 import eid_of

        r.eid = eid_of(rep["input"]["base"], rep["input"]["candidate"])
        r.out = im.implement(opts, side_runner=side_runner)
    except Refusal as err:
        r.refusal = err
    finally:
        r.seconds = time.perf_counter() - t0
        tempfile.tempdir = old_tmp
    r.files_after = file_snapshot(r.repo)
    r.worktrees_after = worktrees(r.repo)
    return r
