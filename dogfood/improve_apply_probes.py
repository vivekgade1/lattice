"""Probe runner for the apply step of spike 3: ``lattice improve apply`` (and ``--discard``).

Implements the probes of ``docs/results/2026-10-15-improve-apply-prereg.md`` (section 3): A0
refusals, A1 the I-3 apply on a clone of this repository's ``dogfood/studio`` and on a clone of
Streak, A2 the gates, A3 safety, A4 the edit's edge cases, A5 ``--discard``, A6 idempotence and
determinism, A7 the loop closes, A8 no regression. The predictions and pass rules are copied
from that document; a probe that does not match is reported as a finding with its raw output,
never adapted.

    uv run python dogfood/improve_apply_probes.py [--runs 10] [--scratch DIR] \
        [--out docs/results/data/2026-10-15-improve-apply] [--only A0,A1a,...]

Every probe calls the real CLI (``python -m lattice.improve``) in a subprocess on scratch
clones or synthetic repositories; the sources (this repository's main checkout and Streak) are
only read by ``git clone``. No LLM, no API key, no network. Every deterministic probe runs
``--runs`` times and all runs must be identical after masking ``applied_at``, the commit sha,
and the gate ``seconds`` and ``output_tail``. ``OUT/<probe>.json`` holds the first run's raw
output and the per-run digests; ``OUT/summary.json`` the table and the disclosed notes.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs/results/data/2026-10-12-spike3"
DEFAULT_OUT = ROOT / "docs/results/data/2026-10-15-improve-apply"
LATTICE_SRC = Path("/Users/vivekgade/Documents/lattice")
STREAK_SRC = Path("/Users/vivekgade/Documents/streak")
BASE_REF = "a4ab4a1"
STREAK_REF = "a0db033"
ID = "imp-3e1cd7f9"
LEVEL_FULL_ID = "imp-50c7f46a"
X = "gate:level_regression:X"
LINE = 'level_gate_args = ["--x-repeats", "2"]'
BASE_TOML = '[project]\nname = "p"\n\n[rebuild]\ngates = ["true"]\n'
CLOSE = "2026-10-12T23:08:00Z"
C = "2026-10-01T12:00:00Z"

DIFF_A1A = """\
@@ -19,5 +19,6 @@ model = "sonnet"
 # The rebuild loop (lattice/studio/rebuild.py, API.md v1.7): the repository is the lattice
 # checkout, the gates run from its root in the rebuild's worktree.
 [rebuild]
+level_gate_args = ["--x-repeats", "2"]
 repo = "../.."
 gates = ["dogfood/studio/check.sh", "uv run pytest tests/studio -q"]
"""
DIFF_A1B = "\n".join(
    [
        '@@ -19,6 +19,7 @@ go_src = "/Users/vivekgade/Documents/streak"',
        " x_repeats = 5",
        " ",
        " [rebuild]",
        '+level_gate_args = ["--x-repeats", "2"]',
        ' repo = "."',
        ' gates = ["./check.sh"]'
        + " " * 26
        + "# writes metrics/last-check.json when Studio runs it",
        ' findings_json = "metrics/last-check.json"'
        + " " * 7
        + "# findings by scenario/rule, so areas can be scoped",
        "",
    ]
)

POST_HOC_SCRIPT_CORRECTIONS: list[str] = [
    "A3, A8 (first complete run): (1) A3 listed the number of new paths in the per-run "
    "observation, which varies by one with the loose-object fan-out directory the new commit "
    "lands in; the count is raw data only now (the unexpected paths are the observation). (2) "
    "A8 ran the base tree's commands with cwd = this tree, so `python -m` imported this tree "
    "for both sides (the identity check was vacuous: only the stateful import-plan and the "
    "help-line filter differed); cwd is the tree being run now. import-plan also ran twice on "
    "one state directory (the second run writes nothing); each tree gets a fresh one. A8 and "
    "A3 were rerun alone (`--only A3,A8`).",
    "A2a (first complete run): the three gated records were compared with an ungated "
    "reference made in another scratch root (`A2a-ref`), so the worktree and `next` paths "
    "differed; all three gates of all three runs had passed. A script bug; the comparison "
    "masks the root path now and A2a was rerun alone (`--only A2a`, summary merged).",
    "A0 (first full run, aborted after A1b): the 10 runs of A0 differed only in the 7-hex commit "
    "sha that the knob_pending detail names (the commit sha is a masked field, 2.9); the script "
    "did not mask it in the observation. A script bug; the sha7 is masked now and the whole "
    "probe run was restarted (nothing of that run is reported).",
    "A3 (first complete debug run, 1 run): the snapshot listed the new branch improve/<id> "
    "among 'branches' and the allowed-new-paths rule did not allow the directory "
    "`.git/worktrees` itself (only `.git/worktrees/<id>/**`); both are script bugs (the rule "
    "text names the branch's ref and worktrees/<id>/), corrected before any reported run.",
]
FINDINGS: list[str] = []
DECISIONS: list[str] = [
    "A0 l-n: `--branch main` is a branch_exists case only when main is not protected, so that "
    'group\'s synthetic studio.toml sets `protected_branches = ["master"]` (master stays '
    "protected, as row l needs); a second observation (default protection, main existing) is "
    "recorded as `bad_branch`: the order of 2.2 (9) puts bad_branch before branch_exists.",
    "A0 r has two cases (an unparseable studio.toml; `--discard ID --no-gates`), which makes "
    "the 24 cases of the rule (o-r list five items).",
    "A4 c: the row's file has a [rebuild] table without the key (so `inserted`) and the key in "
    "[check]; with no [rebuild] at all the same file is row d's created_table.",
    "A2f: SIGTERM is sent 2.0 s after the process starts (the gate has been running since "
    "about 0.7 s).",
    "A8: IM1-IM21 are read from the mutant script's mutants.json (run first); the probe runs "
    "the script itself only when that file is missing.",
]

RUNS = 10
SCRATCH: Path = Path()
ISO = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


# ------------------------------------------------------------------ plumbing


def env_for(tree: Path = ROOT, extra: dict[str, str] | None = None) -> dict[str, str]:
    e = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    e.update(PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1", COLUMNS="100")
    e.update(extra or {})
    return e


def cli(
    argv: list[str],
    *,
    cwd: Path | None = None,
    tree: Path = ROOT,
    extra: dict[str, str] | None = None,
    timeout: float = 1800,
) -> dict[str, Any]:
    r = subprocess.run(
        [sys.executable, "-m", "lattice.improve", *argv],
        cwd=str(cwd or tree),
        env=env_for(tree, extra),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return {"rc": r.returncode, "out": r.stdout, "err": r.stderr}


def jload(text: str) -> dict[str, Any]:
    try:
        v = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return v if isinstance(v, dict) else {}


def git(cwd: Path, *args: str, check: bool = True, env: dict[str, str] | None = None) -> str:
    r = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} in {cwd}: {r.stderr.strip()}")
    return r.stdout


def sha(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def fresh(name: str) -> Path:
    d = SCRATCH / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d.resolve()


def world_hash(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for n in dirnames:
            out[str(Path(dirpath) / n)] = "<dir>"
        for n in filenames:
            p = Path(dirpath) / n
            out[str(p)] = "link" if p.is_symlink() else sha(p.read_bytes())
    return out


def i3_lines(n: int | None = None) -> list[str]:
    lines = (DATA / "i3-events.jsonl").read_text().splitlines()
    return lines[:n] if n else lines


def mk_ev(kind: str, ts: str, n: int, **f: Any) -> dict[str, Any]:
    return {"v": 1, "ts": ts, "project": "t", "kind": kind, "id": f"e-{n:04d}", **f}


def gate_evs(
    gate: str,
    mode: str | None,
    layers: dict[str, float],
    repeats: int = 5,
    stamps: tuple[str, ...] | None = None,
) -> list[str]:
    ts = stamps or ("2026-10-01T10:00:00Z", "2026-10-01T10:30:00Z", "2026-10-01T11:00:00Z")
    out = []
    for n, t in enumerate(ts):
        f: dict[str, Any] = {
            "gate": gate,
            "seconds": sum(layers.values()),
            "x_repeats": repeats,
            "layer_seconds": layers,
        }
        if mode:
            f["mode"] = mode
        out.append(json.dumps(mk_ev("gate_run", t, hash((gate, mode, n)) % 10_000, **f)))
    return out


def spec_sync_evs() -> list[str]:
    return [
        json.dumps(mk_ev("human", f"2026-10-01T0{i}:00:00Z", 9000 + i, what="spec_sync"))
        for i in range(3)
    ]


def pid_of(key: str) -> str:
    return "imp-" + hashlib.sha1(key.encode()).hexdigest()[:8]


class W:
    """One scratch world: a repository, its project directory and a state directory."""

    def __init__(self, root: Path, top: Path, project: Path, state: Path) -> None:
        self.root, self.top, self.project, self.state = root, top, project, state

    @property
    def worktree(self) -> Path:
        return self.top.parent / f"{self.top.name}.improve" / ID

    @property
    def record(self) -> Path:
        return self.state / "applies" / f"{ID}.json"

    def argv(self, *a: str, project: Path | None = None) -> list[str]:
        return [*a, "--project", str(project or self.project), "--state", str(self.state)]

    def call(
        self,
        *a: str,
        iso: bool = True,
        cwd: Path | None = None,
        extra: dict | None = None,
        timeout: float = 1800,
    ) -> dict[str, Any]:
        ex = {**(ISO if iso else {}), "GIT_CEILING_DIRECTORIES": str(SCRATCH)}
        ex.update(extra or {})
        r = cli(self.argv(*a), cwd=cwd, extra=ex, timeout=timeout)
        r["json"] = jload(r["out"])
        return r

    def apply(self, ident: str = ID, *extra: str, **kw: Any) -> dict[str, Any]:
        return self.call("apply", ident, "--json", *extra, **kw)

    def discard(self, ident: str = ID, *extra: str, **kw: Any) -> dict[str, Any]:
        return self.call("apply", "--discard", ident, "--json", *extra, **kw)

    def g(self, *args: str, **kw: Any) -> str:
        return git(self.top, *args, **kw)


def synth(
    root: Path,
    toml: str | bytes | None = BASE_TOML,
    *,
    rel: str = "",
    branch: str = "trunk",
    identity: bool = True,
    commit: bool = True,
    events: list[str] | None = None,
    ledger: list[dict[str, Any]] | None = None,
    name: str = "repo",
) -> W:
    """A synthetic repository (core.autocrlf=false), a project in it and a state directory."""
    top = root / name
    top.mkdir(parents=True)
    git(top, "init", "-q", "-b", branch, env=ISO)
    git(top, "config", "core.autocrlf", "false")
    if identity:
        git(top, "config", "user.name", "Owner")
        git(top, "config", "user.email", "owner@example.com")
    project = top / rel if rel else top
    project.mkdir(parents=True, exist_ok=True)
    if toml is not None:
        (project / "studio.toml").write_bytes(toml if isinstance(toml, bytes) else toml.encode())
    (top / "README").write_text("readme\n")
    if commit:
        git(top, "add", "-A", env=ISO)
        git(
            top,
            "-c",
            "user.name=Owner",
            "-c",
            "user.email=owner@example.com",
            "commit",
            "-qm",
            "init",
            env=ISO,
        )
    state = root / "state"
    state.mkdir(exist_ok=True)
    lines = i3_lines(9) if events is None else events
    (state / "events.jsonl").write_text("".join(x + "\n" for x in lines))
    if ledger is not None:
        (state / "improvements.jsonl").write_text("".join(json.dumps(e) + "\n" for e in ledger))
    return W(root, top, project, state)


def clone_world(root: Path, src: Path, branch: str, ref: str, rel: str, events: list[str]) -> W:
    top = root / src.name
    git(root, "clone", "-q", "--no-hardlinks", str(src), str(top))
    git(top, "checkout", "-q", "-B", branch, ref)
    git(top, "config", "user.name", "Probe")
    git(top, "config", "user.email", "probe@example.com")
    state = root / "state"
    state.mkdir()
    (state / "events.jsonl").write_text("".join(x + "\n" for x in events))
    return W(root, top, top / rel if rel != "." else top, state)


def mask(doc: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    """The record with the fields 2.9 allows to differ masked (and, with `root`, the scratch
    root path, to compare records made in two roots)."""
    d = json.loads(json.dumps(doc))
    s = d["commit"]["sha"]
    d["applied_at"] = "<ts>"
    for r in d["gates"]["results"]:
        r["seconds"], r["output_tail"] = "<s>", "<tail>"
    text = json.dumps(d).replace(s, "<commit>")
    if root is not None:
        text = text.replace(json.dumps(str(root))[1:-1], "<root>")
    return json.loads(text)


def digest(obj: Any) -> str:
    return sha(json.dumps(obj, sort_keys=True, default=str))[:16]


def row(pid: str, predicted: str, observed: str, ok: bool, **extra: Any) -> dict[str, Any]:
    return {"id": pid, "predicted": predicted, "observed": observed, "pass": bool(ok), **extra}


def repeat(runs: int, one: Callable[[int], dict[str, Any]]) -> dict[str, Any]:
    """Run `one` `runs` times (each returns {obs, problems, raw}); all must agree."""
    outs = [one(i) for i in range(runs)]
    digests = [digest(o["obs"]) for o in outs]
    problems = sorted({p for o in outs for p in o["problems"]})
    if len(set(digests)) != 1:
        problems.append(f"runs differ: {len(set(digests))} distinct of {runs}")
    return {
        "obs": outs[0]["obs"],
        "raw": outs[0].get("raw"),
        "problems": problems,
        "digests": digests,
        "runs": runs,
    }


# ------------------------------------------------------------------ A0 refusals


def a0_case(
    label: str,
    root: Path,
    want_rc: int,
    want_reason: str | None = None,
    detail: str | None = None,
    *,
    ident: str = ID,
    extra: tuple[str, ...] = (),
    argv: list[str] | None = None,
    setup: Callable[[W], None] | None = None,
    **kw: Any,
) -> dict[str, Any]:
    w = synth(root, **kw)
    if setup:
        setup(w)
    before = world_hash(root)
    r = w.call(*(argv or ["apply", ident, "--json", *extra]))
    after = world_hash(root)
    d = r["json"]
    probs = []
    if r["rc"] != want_rc:
        probs.append(f"rc {r['rc']} != {want_rc}")
    if want_reason is not None and d.get("reason") != want_reason:
        probs.append(f"reason {d.get('reason')!r} != {want_reason!r}")
    if detail is not None and detail not in str(d.get("detail")):
        probs.append(f"detail {d.get('detail')!r} lacks {detail!r}")
    if want_rc == 3 and r["err"] != f"lattice improve: refused: {d.get('detail')}\n":
        probs.append(f"stderr {r['err']!r}")
    if want_rc in (2, 3) and before != after:
        probs.append("the scratch root changed")
    return {
        "label": label,
        "rc": r["rc"],
        "reason": d.get("reason"),
        "detail": d.get("detail"),
        "err": r["err"].strip()[:200],
        "unchanged": before == after,
        "problems": probs,
    }


def commit_extra(w: W, name: str, text: str) -> None:
    (w.top / name).write_text(text)
    git(w.top, "add", name, env=ISO)
    git(w.top, "-c", "user.name=O", "-c", "user.email=o@e", "commit", "-qm", name, env=ISO)


def a0_run(i: int) -> dict[str, Any]:
    R = fresh("A0")
    n = [0]

    def sub() -> Path:
        n[0] += 1
        d = R / f"c{n[0]:02d}"
        d.mkdir()
        return d

    rows: dict[str, list[dict[str, Any]]] = {}

    def add(key: str, case: dict[str, Any]) -> None:
        rows.setdefault(key, []).append(case)

    add("a", a0_case("unknown id", sub(), 3, "unknown_id", ident="imp-00000000"))
    add(
        "b",
        a0_case(
            "closed",
            sub(),
            3,
            "closed",
            "closed by I-01 at 2026-10-12T23:08:00Z",
            events=i3_lines(),
            ledger=[
                {"id": "I-01", "signal": X, "status": "closed", "commit": "scratch", "ts": CLOSE}
            ],
        ),
    )
    add(
        "c",
        a0_case(
            "needs_owner", sub(), 3, "needs_owner", "lattice/spec/check.py", ident="imp-56861ff8"
        ),
    )
    add("d", a0_case("module", sub(), 3, "not_config", ident="imp-7fad29b4"))
    add("d", a0_case("prereg 1", sub(), 3, "not_config", ident="imp-a8f0a344"))
    add("d", a0_case("prereg (I-3 18)", sub(), 3, "not_config", events=i3_lines()))
    timeouts = [
        json.dumps(mk_ev("node_ended", t, 100 + k, status="gave_up", cause="timeout", wall_s=600.0))
        for k, t in enumerate(
            ("2026-10-01T10:00:00Z", "2026-10-01T10:30:00Z", "2026-10-01T11:00:00Z")
        )
    ] + [json.dumps(mk_ev("node_ended", "2026-10-01T11:10:00Z", 110, status="passed"))]
    add(
        "e",
        a0_case(
            "cause:timeout", sub(), 3, "not_config", ident=pid_of("cause:timeout"), events=timeouts
        ),
    )
    # f: a second apply of an applied id
    w = synth(sub())
    first = w.apply(ID, "--no-gates")
    before = world_hash(w.root)
    rec_bytes = w.record.read_bytes()
    tip = w.g("rev-parse", f"improve/{ID}")
    second = w.apply(ID, "--no-gates")
    f_probs = []
    if first["rc"] != 0:
        f_probs.append(f"first apply rc {first['rc']}")
    if second["rc"] != 3 or second["json"].get("reason") != "already_applied":
        f_probs.append(f"second: rc {second['rc']} {second['json'].get('reason')}")
    if world_hash(w.root) != before or w.record.read_bytes() != rec_bytes:
        f_probs.append("record or tree changed")
    if w.g("rev-parse", f"improve/{ID}") != tip:
        f_probs.append("branch moved")
    add(
        "f",
        {
            "label": "second apply",
            "rc": second["rc"],
            "reason": second["json"].get("reason"),
            "detail": second["json"].get("detail"),
            "err": second["err"].strip()[:200],
            "unchanged": not f_probs,
            "problems": f_probs,
        },
    )
    # g: knob_pending
    both = gate_evs("level", "regression", {"X": 100.0}) + gate_evs("level", "full", {"X": 100.0})
    w = synth(sub(), events=both)
    g1 = w.apply(ID, "--no-gates")
    before = world_hash(w.root)
    g2 = w.apply(LEVEL_FULL_ID, "--no-gates")
    g_probs = []
    if g1["rc"] != 0:
        g_probs.append(f"first apply rc {g1['rc']}")
    if g2["rc"] != 3 or g2["json"].get("reason") != "knob_pending":
        g_probs.append(f"second: rc {g2['rc']} {g2['json'].get('reason')}")
    if ID not in str(g2["json"].get("detail")):
        g_probs.append(f"detail does not name {ID}: {g2['json'].get('detail')!r}")
    if world_hash(w.root) != before:
        g_probs.append("the second call wrote something")
    add(
        "g",
        {
            "label": "knob pending",
            "rc": g2["rc"],
            "reason": g2["json"].get("reason"),
            "detail": g2["json"].get("detail"),
            "err": g2["err"].strip()[:200],
            "unchanged": world_hash(w.root) == before,
            "problems": g_probs,
        },
    )
    add(
        "h",
        a0_case(
            "detached HEAD",
            sub(),
            3,
            "detached_head",
            setup=lambda w: git(w.top, "checkout", "-q", "--detach", env=ISO),
        ),
    )

    def modified(w: W) -> None:
        (w.top / "README").write_text("changed\n")

    def staged(w: W) -> None:
        (w.top / "README").write_text("changed\n")
        git(w.top, "add", "README", env=ISO)

    add("i", a0_case("tracked file modified", sub(), 3, "dirty_checkout", setup=modified))
    add("i", a0_case("staged only", sub(), 3, "dirty_checkout", setup=staged))
    # j: untracked private files are left alone
    w = synth(sub())
    (w.top / "private.txt").write_text("mine\n")
    (w.top / "app.db").write_bytes(b"\x00db\x01")
    snap = {
        n: sha((w.top / n).read_bytes()) for n in ("private.txt", "app.db", "README", "studio.toml")
    }
    j = w.apply(ID, "--no-gates")
    after = {n: sha((w.top / n).read_bytes()) for n in snap}
    j_probs = []
    if j["rc"] != 0:
        j_probs.append(f"rc {j['rc']}")
    if snap != after:
        j_probs.append("an owner file changed")
    add(
        "j",
        {
            "label": "untracked private files",
            "rc": j["rc"],
            "reason": None,
            "detail": None,
            "err": j["err"].strip()[:200],
            "unchanged": snap == after,
            "problems": j_probs,
        },
    )

    def untrack(w: W) -> None:
        git(w.top, "rm", "-q", "--cached", "studio.toml", env=ISO)
        git(w.top, "-c", "user.name=O", "-c", "user.email=o@e", "commit", "-qm", "untrack", env=ISO)

    add("k", a0_case("studio.toml untracked", sub(), 3, "studio_toml_untracked", setup=untrack))
    prot = BASE_TOML.replace(
        'gates = ["true"]', 'gates = ["true"]\nprotected_branches = ["master"]'
    )
    add(
        "l",
        a0_case("bad..name", sub(), 3, "bad_branch", toml=prot, extra=("--branch", "bad..name")),
    )
    add(
        "l",
        a0_case(
            "master (protected)", sub(), 3, "bad_branch", toml=prot, extra=("--branch", "master")
        ),
    )
    add(
        "m",
        a0_case(
            "main pre-created",
            sub(),
            3,
            "branch_exists",
            toml=prot,
            extra=("--branch", "main"),
            setup=lambda w: git(w.top, "branch", "main", env=ISO),
        ),
    )
    add(
        "m",
        a0_case(
            "improve/<id> pre-created",
            sub(),
            3,
            "branch_exists",
            setup=lambda w: git(w.top, "branch", f"improve/{ID}", env=ISO),
        ),
    )
    add(
        "n",
        a0_case(
            "worktree path pre-created",
            sub(),
            3,
            "worktree_exists",
            setup=lambda w: w.worktree.mkdir(parents=True),
        ),
    )
    # o-r: cannot run (exit 2), nothing created
    plain = sub()
    (plain / "proj").mkdir()
    (plain / "state").mkdir()
    (plain / "proj/studio.toml").write_text(BASE_TOML)
    (plain / "state/events.jsonl").write_text("".join(x + "\n" for x in i3_lines(9)))
    before = world_hash(plain)
    r = cli(
        ["apply", ID, "--project", str(plain / "proj"), "--state", str(plain / "state")],
        extra={**ISO, "GIT_CEILING_DIRECTORIES": str(SCRATCH)},
    )
    o_probs = [] if r["rc"] == 2 and world_hash(plain) == before else [f"rc {r['rc']} or changed"]
    add(
        "o",
        {
            "label": "no repository",
            "rc": r["rc"],
            "reason": None,
            "detail": None,
            "err": r["err"].strip()[:200],
            "unchanged": world_hash(plain) == before,
            "problems": o_probs,
        },
    )
    add("p", a0_case("no commits", sub(), 2, commit=False))
    add("q", a0_case("no identity", sub(), 2, identity=False))
    add("r", a0_case("unparseable studio.toml", sub(), 2, toml="[rebuild\nbroken"))
    add(
        "r",
        a0_case(
            "--discard --no-gates",
            sub(),
            2,
            argv=["apply", "--discard", ID, "--no-gates", "--json"],
        ),
    )
    problems = [
        f"{k}/{c['label']}: {p}" for k, cs in rows.items() for c in cs for p in c["problems"]
    ]
    # extra observation (not a prereg row): the default protection puts main before existing
    extra_case = a0_case(
        "main protected by default and existing",
        sub(),
        3,
        "bad_branch",
        extra=("--branch", "main"),
        setup=lambda w: git(w.top, "branch", "main", env=ISO),
    )

    def masked_case(c: dict[str, Any]) -> dict[str, Any]:
        d = {x: c[x] for x in ("label", "rc", "reason", "detail", "unchanged")}
        if isinstance(d["detail"], str):  # the commit sha is a masked field (2.9)
            d["detail"] = re.sub(r"commit [0-9a-f]{7}\b", "commit <sha7>", d["detail"])
        return d

    obs = {k: [masked_case(c) for c in cs] for k, cs in rows.items()}
    return {"obs": obs, "problems": problems, "raw": {"rows": rows, "extra": extra_case}}


def a0(runs: int) -> dict[str, Any]:
    res = repeat(runs, a0_run)
    n_rows = len(res["obs"])
    n_cases = sum(len(v) for v in res["obs"].values())
    ok = not res["problems"] and n_rows == 18 and n_cases == 24
    return row(
        "A0",
        "18 of 18 rows (a-r, 24 cases) exact; hashes unchanged on every exit-2/3 row",
        f"{n_rows} rows, {n_cases} cases; {len(res['problems'])} problem(s)",
        ok,
        problems=res["problems"],
        digests=res["digests"],
        raw=res["raw"],
    )


# ------------------------------------------------------------------ A1 the I-3 apply


def check_a1(
    w: W,
    r: dict[str, Any],
    *,
    base: str,
    branch: str,
    diff: str,
    sha7: str,
    blob: tuple[str, str],
    tree: str,
    rel: str,
    warn_prefix: str | None,
    root_name: str,
) -> list[str]:
    p: list[str] = []
    d = r["json"]
    if r["rc"] != 0:
        return [f"rc {r['rc']}: {r['err'][:200]}"]
    base_sha = w.g("rev-parse", base).strip()
    commit = d["commit"]["sha"]
    e = d["edit"]
    checks = [
        (d["branch"] == f"improve/{ID}", f"branch {d['branch']}"),
        (d["base"]["branch"] == branch and d["base"]["sha"] == base_sha, f"base {d['base']}"),
        (
            w.g("rev-parse", f"improve/{ID}^").strip() == base_sha,
            "branch is not one commit off base",
        ),
        (
            w.g("rev-list", "--count", f"{base_sha}..improve/{ID}").strip() == "1",
            "log base..branch",
        ),
        (d["worktree"] == str(w.root / f"{root_name}.improve" / ID), f"worktree {d['worktree']}"),
        (
            e["action"] == "inserted" and e["line_no"] == 22 and e["file"] == rel,
            f"edit {e['action']} {e['line_no']} {e['file']}",
        ),
        (e["line"] == d["draft"]["toml"].split("\n")[1] == LINE, "edit.line"),
        (d["draft"]["sha256"].startswith(sha7), f"draft.sha256 {d['draft']['sha256'][:16]}"),
        (
            "@@" + e["diff"].partition("\n@@")[2] == diff,
            "the diff hunk differs from the prediction",
        ),
        (d["commit"]["tree"] == tree, f"tree {d['commit']['tree']}"),
        (w.g("rev-parse", f"{base_sha}:{rel}").startswith(blob[0]), "base blob"),
        (w.g("rev-parse", f"{commit}:{rel}").startswith(blob[1]), "new blob"),
        (f"index {blob[0]}..{blob[1]} " in e["diff"], "diff index line"),
        (e["diff"].count("\n+") - e["diff"].count("\n+++") == 1, "not exactly one + line"),
        (e["diff"].count("\n-") - e["diff"].count("\n---") == 0, "a - line"),
        (
            any(s["step"] == "close" and f"--commit {commit}" in s["command"] for s in d["next"]),
            "next.close lacks the commit sha",
        ),
        (w.g("worktree", "list").count("\n") == 2, "worktree list"),
    ]
    p += [m for ok, m in checks if not ok]
    if warn_prefix is None:
        if d["warnings"]:
            p.append(f"warnings {d['warnings']}")
    elif not (len(d["warnings"]) == 1 and d["warnings"][0].startswith(warn_prefix)):
        p.append(f"warnings {d['warnings']}")
    # the message: 2.5 filled in from the draft, computed here from `draft --json`
    dr = jload(w.call("draft", ID, "--json")["out"])
    sections = {s["heading"]: s["body"] for s in dr["sections"]}
    cur = next(ln for ln in sections["What to change"].split("\n") if ln.startswith("current:"))
    want = (
        f"improve: [rebuild] {LINE}\n\nProposal {ID} (change_set), signal {X}.\n"
        f"Draft as of {dr['as_of']}; {cur}.\nPredicted: {sections['Expected']}\n"
        "Applied by `lattice improve apply`; not merged. The owner reviews and merges.\n\n"
        f"Lattice-Improve: {ID}\n"
    )
    if d["commit"]["message"] != want:
        p.append("the commit message differs from 2.5 filled in")
    if w.g("log", "-1", "--format=%B", commit).rstrip("\n") != want.rstrip("\n"):
        p.append("the committed message differs from the record's")
    if sha(dr["text"]) != d["draft"]["sha256"]:
        p.append("draft.sha256 is not the sha256 of `draft` stdout")
    if not w.record.is_file() or jload(w.record.read_text()) != d:
        p.append("the record file is not the --json output")
    return p


def a1_one(
    name: str, src: Path, branch: str, ref: str, rel: str, root_name: str, **kw: Any
) -> Callable[[int], dict[str, Any]]:
    def one(i: int) -> dict[str, Any]:
        R = fresh(name)
        w = clone_world(R, src, branch, ref, rel, i3_lines(9))
        r = w.apply(ID, "--no-gates", iso=False, cwd=w.project)
        probs = check_a1(
            w,
            r,
            base=ref,
            branch=branch,
            root_name=root_name,
            rel=f"{rel}/studio.toml" if rel != "." else "studio.toml",
            **kw,
        )
        obs = mask(r["json"]) if r["json"] else {"rc": r["rc"], "err": r["err"]}
        return {"obs": obs, "problems": probs, "raw": r["json"] or r}

    return one


def a1a(runs: int) -> dict[str, Any]:
    one = a1_one(
        "A1a",
        LATTICE_SRC,
        "next-phases",
        BASE_REF,
        "dogfood/studio",
        "lattice",
        diff=DIFF_A1A,
        sha7="c516bc836f33cdc8",
        blob=("642c32d", "3def11c"),
        tree="ce7f02cf0d0b1156d597a7ca8012591f070c1031",
        warn_prefix=None,
    )
    res = repeat(runs, one)
    obs = res["obs"]
    return row(
        "A1a",
        "exit 0; improve/imp-3e1cd7f9 off next-phases a4ab4a1; inserted line 22; "
        "sha256 c516bc836f33cdc8; blob 642c32d -> 3def11c; tree ce7f02cf0d0b1156; "
        "1 + line, 0 - lines; no warning",
        f"tree {str(obs.get('commit', {}).get('tree'))[:16]}; "
        f"{len(res['problems'])} problem(s) over {runs} runs",
        not res["problems"],
        problems=res["problems"],
        digests=res["digests"],
        raw=res["raw"],
    )


def a1b(runs: int) -> dict[str, Any]:
    one = a1_one(
        "A1b",
        STREAK_SRC,
        "main",
        STREAK_REF,
        ".",
        "streak",
        diff=DIFF_A1B,
        sha7="4c4d6bd878d47f0d",
        blob=("12b2948", "b8a7c2f"),
        tree="c585c3bd71872cd35f7099b3d3097a8d79370038",
        warn_prefix="[rebuild] integration_branch is build/streak; ",
    )
    res = repeat(runs, one)
    obs = res["obs"]
    status = obs.get("gates", {}).get("status")
    probs = list(res["problems"])
    if status != "skipped":
        probs.append(f"gates.status {status}")
    return row(
        "A1b",
        "exit 0; main a0db033; inserted line 22; sha256 4c4d6bd878d47f0d; blob "
        "12b2948 -> b8a7c2f; tree c585c3bd71872cd3; gates skipped; integration_branch "
        "warning",
        f"tree {str(obs.get('commit', {}).get('tree'))[:16]}; gates {status}; "
        f"{len(probs)} problem(s) over {runs} runs",
        not probs,
        problems=probs,
        digests=res["digests"],
        raw=res["raw"],
    )


# ------------------------------------------------------------------ A2 gates


def a2a(runs: int) -> dict[str, Any]:
    """Real gates of dogfood/studio (heavy: about four minutes a run)."""
    probs: list[str] = []
    runs_out = []
    ref_root = fresh("A2a-ref")
    ref = clone_world(ref_root, LATTICE_SRC, "next-phases", BASE_REF, "dogfood/studio", i3_lines(9))
    ref_rec = ref.apply(ID, "--no-gates", iso=False, cwd=ref.project)["json"]
    first_mask: dict[str, Any] | None = mask(ref_rec, ref_root)
    first_mask.pop("gates", None)
    for i in range(runs):
        R = fresh("A2a")
        w = clone_world(R, LATTICE_SRC, "next-phases", BASE_REF, "dogfood/studio", i3_lines(9))
        t0 = time.monotonic()
        r = w.apply(ID, iso=False, cwd=w.project, timeout=3600)
        wall = round(time.monotonic() - t0, 1)
        d = r["json"]
        res = d.get("gates", {}).get("results", [])
        info = {
            "run": i,
            "rc": r["rc"],
            "wall_s": wall,
            "status": d.get("gates", {}).get("status"),
            "gates": [
                {
                    "command": g["command"],
                    "exit_code": g["exit_code"],
                    "seconds": g["seconds"],
                    "tail": g["output_tail"].strip()[-200:],
                }
                for g in res
            ],
            "untracked_after": d.get("gates", {}).get("untracked_after"),
        }
        runs_out.append(info)
        if r["rc"] != 0 or info["status"] != "passed":
            probs.append(f"run {i}: rc {r['rc']} status {info['status']}")
        for g in res:
            if not (g["exit_code"] == 0 and 0 < g["seconds"] < 900):
                probs.append(f"run {i}: {g['command']} exit {g['exit_code']} {g['seconds']} s")
        if len(res) != 2:
            probs.append(f"run {i}: {len(res)} gates")
        elif "PASS: 0 findings" not in res[0]["output_tail"]:
            probs.append(f"run {i}: check.sh tail lacks PASS: 0 findings")
        elif "PASS: 0 findings (D 0, X 0); 435 cases run" not in res[0]["output_tail"]:
            note = f"A2a run {i}: check.sh tail is not the predicted `... 435 cases run`"
            if note not in FINDINGS:
                FINDINGS.append(note)
        if info["untracked_after"] != []:
            probs.append(f"run {i}: untracked_after {info['untracked_after']}")
        m = mask(d, R) if d else {}
        m.pop("gates", None)
        if m != first_mask:
            probs.append(f"run {i}: the record differs from the ungated apply beyond the gates")
    secs = [[g["seconds"] for g in x["gates"]] for x in runs_out]
    return row(
        "A2a",
        "3 of 3 passed, each gate seconds in (0, 900); check.sh tail `PASS: 0 "
        "findings (D 0, X 0); 435 cases run` ~53 s, pytest ~190 s; untracked_after []",
        f"{runs} runs; seconds {secs}; {len(probs)} problem(s)",
        not probs,
        problems=probs,
        raw=runs_out,
    )


def a2_cases(runs: int) -> dict[str, Any]:
    def run_case(i: int) -> dict[str, Any]:
        R = fresh("A2")
        probs: list[str] = []
        obs: dict[str, Any] = {}
        raw: dict[str, Any] = {}

        def world(name: str, gates: str | None, extra: str = "") -> W:
            d = R / name
            d.mkdir()
            toml = f"[rebuild]\ngates = {gates}\n{extra}" if gates is not None else "[project]\n"
            return synth(d, toml)

        # b: both gates run, exit 1, everything kept
        w = world("b", '["true", "false"]')
        r = w.apply(ID)
        g = r["json"].get("gates", {})
        raw["b"] = r["json"]
        ok = (
            r["rc"] == 1
            and g.get("status") == "failed"
            and [x["exit_code"] for x in g.get("results", [])] == [0, 1]
            and w.worktree.is_dir()
            and f"improve/{ID}" in w.g("branch")
            and w.record.is_file()
        )
        obs["b"] = {
            "rc": r["rc"],
            "status": g.get("status"),
            "exits": [x["exit_code"] for x in g.get("results", [])],
        }
        if not ok:
            probs.append(f"b: {obs['b']}")
        # c: timeout
        w = world("c", '["sleep 30"]', "gate_timeout = 2\nstop_grace = 1\n")
        r = w.apply(ID)
        g = r["json"].get("gates", {})
        res = g.get("results", [{}])[0] if g.get("results") else {}
        raw["c"] = r["json"]
        obs["c"] = {
            "rc": r["rc"],
            "exit_code": res.get("exit_code"),
            "timed_out": res.get("timed_out"),
            "status": g.get("status"),
        }
        if not (
            r["rc"] == 1
            and res.get("exit_code") == 124
            and res.get("timed_out") is True
            and 2 <= res.get("seconds", 0) <= 9
        ):
            probs.append(f"c: {obs['c']} seconds {res.get('seconds')}")
        # d: no gates and no check.sh
        w = world("d", None)
        r = w.apply(ID)
        g = r["json"].get("gates", {})
        raw["d"] = r["json"]
        obs["d"] = {"rc": r["rc"], "status": g.get("status"), "warnings": r["json"].get("warnings")}
        if not (
            r["rc"] == 0
            and g.get("status") == "none_configured"
            and any("no gate ran" in x for x in r["json"].get("warnings", []))
        ):
            probs.append(f"d: {obs['d']}")
        # e: --no-gates with a gate configured
        w = world("e", '["false"]')
        r = w.apply(ID, "--no-gates")
        g = r["json"].get("gates", {})
        raw["e"] = r["json"]
        obs["e"] = {"rc": r["rc"], "status": g.get("status"), "results": len(g.get("results", []))}
        if not (r["rc"] == 0 and g.get("status") == "skipped" and g.get("results") == []):
            probs.append(f"e: {obs['e']}")
        # f: SIGTERM after 2 s
        w = world("f", '["sleep 30"]')
        before_pids = sleepers()
        proc = subprocess.Popen(
            [sys.executable, "-m", "lattice.improve", *w.argv("apply", ID, "--json")],
            env=env_for(ROOT, {**ISO, "GIT_CEILING_DIRECTORIES": str(SCRATCH)}),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(2.0)
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=60)
        time.sleep(0.5)
        left = sorted(sleepers() - before_pids)
        rec = jload(w.record.read_text()) if w.record.is_file() else {}
        raw["f"] = {"rc": proc.returncode, "err": err.strip()[:300], "record": rec}
        obs["f"] = {
            "rc": proc.returncode,
            "status": rec.get("gates", {}).get("status"),
            "sleep_left": len(left),
        }
        if not (proc.returncode == 2 and obs["f"]["status"] == "interrupted" and not left):
            probs.append(f"f: {obs['f']}")
        return {"obs": obs, "problems": probs, "raw": raw}

    res = repeat(runs, run_case)
    return row(
        "A2b-f",
        "b exit 1 failed (0,1) kept; c exit 1 124 timed_out 2-9 s; d exit 0 "
        "none_configured + warning; e exit 0 skipped; f exit 2 interrupted, no sleep left "
        "(5 of 5)",
        f"{len(res['obs'])} rows; {len(res['problems'])} problem(s)",
        not res["problems"],
        problems=res["problems"],
        digests=res["digests"],
        raw=res["raw"],
        obs=res["obs"],
    )


def sleepers() -> set[str]:
    out = subprocess.run(["ps", "-A", "-o", "pid=,command="], capture_output=True, text=True).stdout
    return {
        ln.split(None, 1)[0] for ln in out.splitlines() if re.search(r"(^|\s)sleep 30$", ln.strip())
    }


# ------------------------------------------------------------------ A3 safety


GIT_FILES = ("index", "HEAD", "config", "packed-refs", "info/exclude")
FORBIDDEN = {"merge", "push", "update-ref", "reset", "checkout", "stash"}


def snapshot(w: W) -> dict[str, Any]:
    g = w.top / ".git"
    refs = w.g("for-each-ref", "--format=%(refname) %(objectname)").splitlines()
    files = {}
    for dirpath, dirnames, filenames in os.walk(w.top):
        if ".git" in dirnames:
            dirnames.remove(".git")
        for n in filenames:
            p = Path(dirpath) / n
            files[str(p.relative_to(w.top))] = sha(p.read_bytes())
    return {
        "git": {n: sha((g / n).read_bytes()) if (g / n).is_file() else None for n in GIT_FILES},
        "refs": [x for x in refs if "/improve/" not in x],
        "head": w.g("rev-parse", "HEAD").strip(),
        "symbolic_ref": w.g("symbolic-ref", "-q", "HEAD", check=False).strip(),
        "stash": w.g("stash", "list"),
        "branches": [
            b
            for b in w.g("branch", "--format=%(refname:short)").split()
            if not b.startswith("improve/")
        ],
        "files": files,
        "state": {
            n: sha((w.state / n).read_bytes()) if (w.state / n).is_file() else None
            for n in ("events.jsonl", "improvements.jsonl")
        },
    }


def all_paths(root: Path) -> set[str]:
    out = set()
    for dirpath, dirnames, filenames in os.walk(root):
        for n in [*dirnames, *filenames]:
            out.add(str((Path(dirpath) / n).relative_to(root)))
    return out


def allowed_new(rel: str, top_name: str, ident: str) -> bool:
    if rel.startswith(f"{top_name}/.git/worktrees/") and not (
        rel == f"{top_name}/.git/worktrees/{ident}"
        or rel.startswith(f"{top_name}/.git/worktrees/{ident}/")
    ):
        return False
    ok = (
        f"{top_name}.improve",
        "state/applies",
        f"{top_name}/.git/refs/heads/improve",
        f"{top_name}/.git/logs/refs/heads/improve",
        f"{top_name}/.git/worktrees",  # the directory itself and worktrees/<id>/**
        f"{top_name}/.git/objects",
    )
    return any(rel == a or rel.startswith(a + "/") for a in ok)


SHIM = """#!/bin/sh
printf '%s\\n' "$*" >> "$SHIM_LOG"
exec {git} "$@"
"""


def a3_world(kind: str) -> tuple[W, Callable[[W, dict[str, str]], dict[str, Any]], str]:
    """(world, the call, its top-level name) for one of the five calls A3 names."""
    root = fresh(f"A3-{kind}")
    if kind in ("A1a", "A1b"):
        src, br, ref, rel = (
            (LATTICE_SRC, "next-phases", BASE_REF, "dogfood/studio")
            if kind == "A1a"
            else (STREAK_SRC, "main", STREAK_REF, ".")
        )
        w = clone_world(root, src, br, ref, rel, i3_lines(9))
        return w, (lambda w, ex: w.apply(ID, "--no-gates", iso=False, extra=ex)), src.name
    if kind == "A2b":
        w = synth(root, '[rebuild]\ngates = ["true", "false"]\n')
        return w, (lambda w, ex: w.apply(ID, extra=ex)), "repo"
    w = synth(root)
    return w, (lambda w, ex: w.apply(ID, "--no-gates", extra=ex)), "repo"


def a3_one(kind: str) -> dict[str, Any]:
    w, call, top_name = a3_world(kind)
    git(w.top, "branch", "other", env=ISO)
    (w.top / "README").write_text("stashed\n")
    git(w.top, "-c", "user.name=O", "-c", "user.email=o@e", "stash", "push", "-q", env=ISO)
    (w.top / "private.txt").write_text("mine\n")
    if kind == "A0j":
        (w.top / "app.db").write_bytes(b"\x00db")
    shimdir = w.root / "_shim"
    shimdir.mkdir()
    log = w.root / "_git.log"
    (shimdir / "git").write_text(SHIM.format(git=shutil.which("git")))
    (shimdir / "git").chmod(0o755)
    ex = {"PATH": f"{shimdir}{os.pathsep}{os.environ['PATH']}", "SHIM_LOG": str(log)}
    before, paths_before = snapshot(w), all_paths(w.root)
    r = call(w, ex)
    after, paths_after = snapshot(w), all_paths(w.root)
    probs: list[str] = []
    for k in before:
        if before[k] != after[k]:
            probs.append(f"{kind}: {k} changed")
    new = {p for p in paths_after - paths_before if not p.startswith("_git.log")}
    bad = sorted(p for p in new if not allowed_new(p, top_name, ID))
    if bad:
        probs.append(f"{kind}: unexpected new paths {bad[:5]}")
    toks = (
        {t for ln in log.read_text().splitlines() for t in shlex.split(ln)}
        if log.exists()
        else set()
    )
    if toks & FORBIDDEN:
        probs.append(f"{kind}: forbidden git argv {sorted(toks & FORBIDDEN)}")
    if r["rc"] not in (0, 1):
        probs.append(f"{kind}: rc {r['rc']}")
    obs = {
        "rc": r["rc"],
        "changed": [k for k in before if before[k] != after[k]],
        "unexpected_paths": bad,
        "forbidden": sorted(toks & FORBIDDEN),
        "git_calls": log.read_text().count("\n") if log.exists() else 0,
    }
    raw = {"new_paths": len(new), "tokens": sorted(toks)}  # new_paths counts fan-out dirs
    return {"obs": obs, "problems": probs, "raw": raw}


def a3_discard() -> dict[str, Any]:
    """A5a in A3: apply, then discard; every hash is as before the apply."""
    root = fresh("A3-A5a")
    w = clone_world(root, LATTICE_SRC, "next-phases", BASE_REF, "dogfood/studio", i3_lines(9))
    git(w.top, "branch", "other", env=ISO)
    (w.top / "private.txt").write_text("mine\n")
    shimdir = root / "_shim"
    shimdir.mkdir()
    log = root / "_git.log"
    (shimdir / "git").write_text(SHIM.format(git=shutil.which("git")))
    (shimdir / "git").chmod(0o755)
    ex = {"PATH": f"{shimdir}{os.pathsep}{os.environ['PATH']}", "SHIM_LOG": str(log)}
    before = snapshot(w)
    a = w.apply(ID, "--no-gates", iso=False, extra=ex)
    d = w.discard(ID, iso=False, extra=ex)
    after = snapshot(w)
    probs = [f"A5a: {k} differs after apply + discard" for k in before if before[k] != after[k]]
    toks = {t for ln in log.read_text().splitlines() for t in shlex.split(ln)}
    if toks & FORBIDDEN:
        probs.append(f"A5a: forbidden git argv {sorted(toks & FORBIDDEN)}")
    if a["rc"] != 0 or d["rc"] != 0:
        probs.append(f"A5a: rc {a['rc']}/{d['rc']}")
    if w.worktree.exists() or f"improve/{ID}" in w.g("branch"):
        probs.append("A5a: worktree or branch left")
    return {
        "obs": {
            "apply_rc": a["rc"],
            "discard_rc": d["rc"],
            "changed": [k for k in before if before[k] != after[k]],
        },
        "problems": probs,
        "raw": {"tokens": sorted(toks)},
    }


def a3_default_state() -> dict[str, Any]:
    """A3b: the default state dir; the one extra change is info/exclude, as `ledger` makes it."""
    out: dict[str, Any] = {}
    probs: list[str] = []
    for tag in ("apply", "ledger"):
        root = fresh(f"A3b-{tag}")
        w = clone_world(root, LATTICE_SRC, "next-phases", BASE_REF, "dogfood/studio", i3_lines(9))
        default = w.top / ".lattice/state/studio"
        default.mkdir(parents=True)
        (default / "events.jsonl").write_text("".join(x + "\n" for x in i3_lines(9)))
        before = snapshot(w)
        ex = {"GIT_CEILING_DIRECTORIES": str(SCRATCH)}
        if tag == "apply":
            r = cli(["apply", ID, "--no-gates", "--project", str(w.project), "--json"], extra=ex)
        else:
            r = cli(
                ["ledger", "--project", str(w.project), "--signal", X, "--status", "open"], extra=ex
            )
        after = snapshot(w)
        out[tag] = {
            "rc": r["rc"],
            "exclude": (w.top / ".git/info/exclude").read_text(),
            "changed": [k for k in before["git"] if before["git"][k] != after["git"][k]],
            "new_files": sorted(set(after["files"]) - set(before["files"])),
            "refs_changed": before["refs"] != after["refs"] or before["head"] != after["head"],
        }
        if tag == "apply":
            probs += [
                f"A3b: {k} changed"
                for k in before
                if k not in ("git", "files", "state") and before[k] != after[k]
            ]
    if out["apply"]["changed"] != ["info/exclude"]:
        probs.append(f"A3b: changed git files {out['apply']['changed']}")
    if out["apply"]["exclude"] != out["ledger"]["exclude"]:
        probs.append("A3b: info/exclude differs from what `ledger --signal` writes")
    if "/.lattice/state/" not in out["apply"]["exclude"].splitlines():
        probs.append("A3b: no /.lattice/state/ line")
    if out["apply"]["new_files"] != [f".lattice/state/studio/applies/{ID}.json"]:
        probs.append(f"A3b: new working files {out['apply']['new_files']}")
    return {"obs": out, "problems": probs, "raw": out}


def a3_static() -> list[str]:
    src_path = ROOT / "lattice/improve/apply.py"
    src = src_path.read_text()
    tree = ast.parse(src)
    probs: list[str] = []
    for n in tree.body:
        mods = []
        if isinstance(n, ast.Import):
            mods = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            mods = [n.module or ""]
        for m in mods:
            if not (m.split(".")[0] in sys.stdlib_module_names or m.startswith("lattice.improve")):
                probs.append(f"top-level import {m}")
    lazy = {
        n.module
        for f in ast.walk(tree)
        if isinstance(f, ast.FunctionDef)
        for n in ast.walk(f)
        if isinstance(n, ast.ImportFrom)
    }
    if not lazy <= {"lattice.studio.rebuild", "lattice.studio.state"}:
        probs.append(f"lazy imports {sorted(lazy)}")
    for word in ("claude", "anthropic", "lattice.llm"):
        if word in src:
            probs.append(f"{word!r} occurs in apply.py")
    for p in (ROOT / "lattice/improve").glob("*.py"):
        for lit in ("3e1cd7f9", "71.7", "ce7f02c"):
            if lit in p.read_text():
                probs.append(f"{lit!r} occurs in {p.name}")
    return probs


def a3(runs: int) -> dict[str, Any]:
    probs = a3_static()
    rows: dict[str, Any] = {}
    for kind in ("A1a", "A1b", "A2b", "A0j"):
        res = repeat(runs, lambda i, k=kind: a3_one(k))
        rows[kind] = res["obs"]
        probs += res["problems"]
    res = repeat(runs, lambda i: a3_discard())
    rows["A5a"] = res["obs"]
    probs += res["problems"]
    b = a3_default_state()
    rows["A3b"] = b["obs"]["apply"]["changed"]
    probs += b["problems"]
    return row(
        "A3",
        "0 changed hashes, 0 unexpected paths or argv; static: stdlib + "
        "lattice.improve imports, no claude/anthropic/lattice.llm, no I-3 literals; "
        "A3b: only info/exclude",
        f"{len(probs)} problem(s); rows {sorted(rows)}",
        not probs,
        problems=probs,
        raw=rows,
    )


# ------------------------------------------------------------------ A4 edit edge cases


def a4_run(i: int) -> dict[str, Any]:
    R = fresh("A4")
    n = [0]
    probs: list[str] = []
    obs: dict[str, Any] = {}

    def case(
        key: str,
        toml: str | bytes | None,
        *,
        events: list[str] | None = None,
        ident: str = ID,
        want: bytes | None = None,
        action: str | None = None,
        reason: str | None = None,
        line_no: int | None = None,
        warn: list[str] | None = None,
    ) -> None:
        n[0] += 1
        d = R / f"{key}{n[0]}"
        d.mkdir()
        w = synth(d, toml, events=events)
        orig = (
            tomllib.loads(toml if isinstance(toml, str) else (toml or b"").decode()) if toml else {}
        )
        r = w.apply(ident, "--no-gates")
        j = r["json"]
        if reason is not None:
            ok = r["rc"] == 3 and j.get("reason") == reason
            obs[f"{key}{n[0]}"] = {"rc": r["rc"], "reason": j.get("reason")}
            if reason in ("unsupported_layout", "edit_check_failed", "already_set") and (
                w.worktree.parent.exists() or f"improve/{ident}" in w.g("branch")
            ):
                ok = False
            if not ok:
                probs.append(f"{key}: rc {r['rc']} reason {j.get('reason')}")
            return
        if r["rc"] != 0:
            probs.append(f"{key}: rc {r['rc']} {r['err'][:120]}")
            obs[f"{key}{n[0]}"] = {"rc": r["rc"]}
            return
        wt = (w.worktree.parent / ident / "studio.toml").read_bytes()
        commit = j["commit"]["sha"]
        blob = subprocess.run(
            ["git", "-C", str(w.top), "show", f"{commit}:studio.toml"], capture_output=True
        ).stdout
        new = tomllib.loads(blob.decode())
        want_parse = json.loads(json.dumps(orig))
        want_parse.setdefault("rebuild", {})[j["edit"]["key"]] = j["edit"]["new"]
        obs[f"{key}{n[0]}"] = {
            "rc": 0,
            "action": j["edit"]["action"],
            "line_no": j["edit"]["line_no"],
            "warnings": j["warnings"],
        }
        if j["edit"]["action"] != action:
            probs.append(f"{key}: action {j['edit']['action']} != {action}")
        if line_no is not None and j["edit"]["line_no"] != line_no:
            probs.append(f"{key}: line_no {j['edit']['line_no']} != {line_no}")
        if new != want_parse:
            probs.append(f"{key}: new parse != old + the key")
        if want is not None and blob != want:
            probs.append(f"{key}: bytes differ: {blob!r}")
        if blob != wt:
            probs.append(f"{key}: worktree bytes != committed bytes")
        if warn is not None and j["warnings"] != warn:
            probs.append(f"{key}: warnings {j['warnings']}")

    x = i3_lines(9)
    node_x = gate_evs("node", None, {"X": 100.0})
    node_id = pid_of("gate:node:X")
    a_toml = '[rebuild]\ngates = ["true"]\nlevel_gate_args = ["--x-repeats", "3"]  # tuned\n'
    case(
        "a",
        a_toml,
        events=x,
        action="replaced",
        line_no=3,
        want=a_toml.replace('"3"]', '"2"]').encode(),
    )
    b_toml = '[project]\nname = "p"\n\n[rebuild]   # build\n# the gates\ngates = ["true"]\n'
    case(
        "b",
        b_toml,
        events=x,
        action="inserted",
        line_no=5,
        want=b_toml.replace("# build\n", f"# build\n{LINE}\n").encode(),
    )
    c_toml = '[check]\nlevel_gate_args = ["--layers", "D"]\n\n[rebuild]\ngates = ["true"]\n'
    case(
        "c",
        c_toml,
        events=x,
        action="inserted",
        line_no=5,
        want=c_toml.replace("[rebuild]\n", f"[rebuild]\n{LINE}\n").encode(),
        warn=["level_gate_args is also set in [check]; the rebuild loop reads [rebuild] only"],
    )
    d_toml = '[project]\nname = "p"\n'
    case(
        "d",
        d_toml,
        events=x,
        action="created_table",
        line_no=5,
        want=(d_toml + f"\n[rebuild]\n{LINE}\n").encode(),
    )
    case(
        "e", None, events=x, action="created_file", line_no=2, want=f"[rebuild]\n{LINE}\n".encode()
    )
    f_toml = '[rebuild]\nscoped_gate_args = ["--layers", "CD"]  # node\n'
    case(
        "f",
        f_toml,
        events=node_x,
        ident=node_id,
        action="replaced",
        line_no=2,
        want=b'[rebuild]\nscoped_gate_args = ["--layers", "CD", "--x-repeats", "1"]  # node\n',
    )
    case(
        "g",
        BASE_TOML,
        events=spec_sync_evs(),
        ident=pid_of("human:spec_sync"),
        action="inserted",
        line_no=5,
        want=BASE_TOML.replace("[rebuild]\n", '[rebuild]\nsync_spec = "auto"\n').encode(),
    )
    case("h", '[rebuild]\nlevel_gate_args = ["--x-repeats", "2"]\n', events=x, reason="already_set")
    case(
        "h",
        '[rebuild]\nscoped_gate_args = ["--layers", "CD", "--x-repeats", "1", "--z"]\n',
        events=node_x,
        ident=node_id,
        reason="already_set",
    )
    crlf = b'[project]\r\nname = "p"\r\n\r\n[rebuild]\r\ngates = ["true"]\r\n'
    case(
        "i",
        crlf,
        events=x,
        action="inserted",
        line_no=5,
        want=crlf.replace(b"[rebuild]\r\n", b"[rebuild]\r\n" + LINE.encode() + b"\r\n"),
    )
    j_toml = '[rebuild]\ngates = ["true"]\n\n[other]\nx = 1'
    case(
        "j",
        j_toml,
        events=x,
        action="inserted",
        line_no=2,
        want=j_toml.replace("[rebuild]\n", f"[rebuild]\n{LINE}\n").encode(),
    )
    case(
        "k",
        '[rebuild]\nlevel_gate_args = [\n "--x-repeats", "3",\n]\n',
        events=x,
        reason="unsupported_layout",
    )
    case("l", 'rebuild = { gates = ["true"] }\n', events=x, reason="unsupported_layout")
    case("m", 'note = """\n[rebuild]\n"""\n', events=x, reason="edit_check_failed")
    return {"obs": obs, "problems": probs, "raw": obs}


def a4(runs: int) -> dict[str, Any]:
    res = repeat(runs, a4_run)
    return row(
        "A4",
        "13 of 13 rows (a-m); on each exit-0 row new parse = old + the one key and "
        "every other byte identical",
        f"{len(res['obs'])} cases; {len(res['problems'])} problem(s)",
        not res["problems"],
        problems=res["problems"],
        digests=res["digests"],
        raw=res["raw"],
    )


# ------------------------------------------------------------------ A5 discard


def a5_run(i: int) -> dict[str, Any]:
    R = fresh("A5")
    probs: list[str] = []
    obs: dict[str, Any] = {}

    def world(name: str) -> W:
        d = R / name
        d.mkdir()
        return synth(d)

    # a
    w = world("a")
    ref = {k: v for k, v in snapshot(w).items()}
    ap = w.apply(ID, "--no-gates")
    rec = w.record.read_bytes()
    dr = w.discard(ID)
    kept = w.state / "applies" / f"{ID}.discarded-1.json"
    after = snapshot(w)
    a_ok = (
        ap["rc"] == 0
        and dr["rc"] == 0
        and not w.worktree.exists()
        and f"improve/{ID}" not in w.g("branch")
        and w.g("worktree", "list").count("\n") == 1
        and kept.is_file()
        and kept.read_bytes() == rec
        and not w.record.exists()
        and ref == after
    )
    obs["a"] = {"rc": dr["rc"], "renamed": kept.is_file(), "hashes_equal": ref == after}
    if not a_ok:
        probs.append(f"a: {obs['a']}")

    def refused(key: str, w: W, reason: str, *, removes_nothing: bool = True) -> None:
        before = world_hash(w.root)
        r = w.discard(ID)
        obs[key] = {"rc": r["rc"], "reason": r["json"].get("reason")}
        if r["rc"] != 3 or r["json"].get("reason") != reason:
            probs.append(f"{key}: {obs[key]}")
        if removes_nothing and world_hash(w.root) != before:
            probs.append(f"{key}: something was removed or written")

    # b never applied
    refused("b", world("b"), "not_applied")
    # c a commit on the branch
    w = world("c")
    w.apply(ID, "--no-gates")
    (w.worktree / "more.txt").write_text("x\n")
    git(w.worktree, "add", "more.txt", env=ISO)
    git(w.worktree, "-c", "user.name=O", "-c", "user.email=o@e", "commit", "-qm", "more", env=ISO)
    refused("c", w, "branch_moved")
    # d another branch holds the commit
    w = world("d")
    a = w.apply(ID, "--no-gates")
    git(w.top, "branch", "keep", a["json"]["commit"]["sha"], env=ISO)
    refused("d", w, "merged")
    # e a tracked file edited in the worktree
    w = world("e")
    w.apply(ID, "--no-gates")
    (w.worktree / "studio.toml").write_text("# edited\n")
    refused("e", w, "worktree_dirty")
    return {"obs": obs, "problems": probs, "raw": obs}


def a5(runs: int) -> dict[str, Any]:
    res = repeat(runs, a5_run)
    return row(
        "A5",
        "a -> 0 (worktree, branch gone; record renamed byte-identical; hashes = "
        "before apply); b not_applied; c branch_moved; d merged; e worktree_dirty; b-e "
        "remove nothing (5 of 5)",
        f"{len(res['obs'])} rows; {len(res['problems'])} problem(s)",
        not res["problems"],
        problems=res["problems"],
        digests=res["digests"],
        raw=res["raw"],
    )


# ------------------------------------------------------------------ A6 idempotence, determinism


def a6_run(i: int) -> dict[str, Any]:
    R = fresh("A6")
    probs: list[str] = []
    obs: dict[str, Any] = {}
    # a: A0f leaves the record and the branch byte-identical (also in a0)
    (R / "a").mkdir()
    w = synth(R / "a")
    w.apply(ID, "--no-gates")
    rec, tip = w.record.read_bytes(), w.g("rev-parse", f"improve/{ID}")
    again = w.apply(ID, "--no-gates")
    obs["a"] = again["rc"]
    if again["rc"] != 3 or w.record.read_bytes() != rec or w.g("rev-parse", f"improve/{ID}") != tip:
        probs.append("a: the second apply changed something")
    # b: apply, discard, apply
    (R / "b").mkdir()
    w = synth(
        R / "b", rel="dogfood/studio", toml=(ROOT / "dogfood/studio/studio.toml").read_bytes()
    )
    r1 = w.apply(ID, "--no-gates")
    w.discard(ID)
    r2 = w.apply(ID, "--no-gates")
    d1, d2 = r1["json"], r2["json"]
    same = (
        d1["commit"]["tree"] == d2["commit"]["tree"]
        and d1["edit"]["diff"] == d2["edit"]["diff"]
        and d1["commit"]["message"] == d2["commit"]["message"]
        and mask(d1) == mask(d2)
    )
    obs["b"] = {"rc": [r1["rc"], r2["rc"]], "same": same}
    if r1["rc"] != 0 or r2["rc"] != 0 or not same:
        probs.append(f"b: {obs['b']}")
    return {"obs": obs, "problems": probs, "raw": obs}


def a6c(runs: int) -> dict[str, Any]:
    """A1a and A1b on `runs` fresh clones at one path, from two cwds."""
    probs: list[str] = []
    out: dict[str, Any] = {}
    for kind, src, br, ref, rel, tree in (
        (
            "A1a",
            LATTICE_SRC,
            "next-phases",
            BASE_REF,
            "dogfood/studio",
            "ce7f02cf0d0b1156d597a7ca8012591f070c1031",
        ),
        ("A1b", STREAK_SRC, "main", STREAK_REF, ".", "c585c3bd71872cd35f7099b3d3097a8d79370038"),
    ):
        recs = []
        for i in range(runs):
            R = fresh(f"A6c-{kind}")
            w = clone_world(R, src, br, ref, rel, i3_lines(9))
            cwd = w.project if i % 2 == 0 else R  # two cwds
            argv = [
                "apply",
                ID,
                "--json",
                "--no-gates",
                "--state",
                str(w.state),
                "--project",
                "." if i % 2 == 0 else str(w.project),
            ]
            r = cli(argv, cwd=cwd, extra={"GIT_CEILING_DIRECTORIES": str(SCRATCH)})
            d = jload(r["out"])
            if r["rc"] != 0 or not d:
                probs.append(f"{kind} run {i}: rc {r['rc']}")
                continue
            if d["commit"]["tree"] != tree:
                probs.append(f"{kind} run {i}: tree {d['commit']['tree']}")
            recs.append(digest(mask(d)))
        out[kind] = {"records": len(recs), "distinct": len(set(recs))}
        if len(recs) != runs or len(set(recs)) != 1:
            probs.append(f"{kind}: {len(recs)} records, {len(set(recs))} distinct")
    return {"obs": out, "problems": probs, "raw": out}


def a6(runs: int) -> dict[str, Any]:
    res = repeat(runs, a6_run)
    c = a6c(runs)
    probs = res["problems"] + c["problems"]
    return row(
        "A6",
        "a: second apply leaves record and branch byte-identical; b: apply, discard, "
        "apply = same tree, diff, message, records; c: 10 fresh clones x A1a, A1b from "
        "two cwds: one record (masked), tree ids as A1 (3 of 3)",
        f"{len(probs)} problem(s); c {c['obs']}",
        not probs,
        problems=probs,
        digests=res["digests"],
        raw={"ab": res["raw"], "c": c["obs"]},
    )


# ------------------------------------------------------------------ A7 the loop closes


def v2_events(after: tuple[float, ...]) -> tuple[list[str], list[str]]:
    before_ts = ("2026-10-01T10:00:00Z", "2026-10-01T10:30:00Z", "2026-10-01T11:00:00Z")
    after_ts = ("2026-10-01T12:30:00Z", "2026-10-01T13:00:00Z", "2026-10-01T13:30:00Z")

    def gate(t: str, x: float, rep: int, n: int) -> str:
        return json.dumps(
            mk_ev(
                "gate_run",
                t,
                n,
                gate="level",
                mode="regression",
                seconds=x,
                layer_seconds={"X": x},
                x_repeats=rep,
            )
        )

    b = [gate(t, x, 5, k) for k, (t, x) in enumerate(zip(before_ts, (100, 101, 99), strict=True))]
    a = [gate(t, x, 2, 10 + k) for k, (t, x) in enumerate(zip(after_ts, after, strict=True))]
    return b, a


def a7_one(after: tuple[float, ...]) -> tuple[dict[str, Any], W, dict[str, Any]]:
    R = fresh("A7")
    b, a = v2_events(after)
    w = synth(R, events=b)
    ap = w.apply(ID, "--no-gates")
    d = ap["json"]
    git(w.top, "merge", "-q", "--ff-only", f"improve/{ID}", env=ISO)  # the owner merges
    close = {s["step"]: s["command"] for s in d["next"]}["close"]
    argv = shlex.split(close)
    ledger = cli(argv[2:], extra={**ISO, "GIT_CEILING_DIRECTORIES": str(SCRATCH)})
    with (w.state / "events.jsonl").open("a") as f:
        f.writelines(x + "\n" for x in a)
    v = w.call("verify", ID, "--at", C, "--json")
    return {"apply": ap, "ledger": ledger, "verify": v}, w, d


def a7_run(i: int) -> dict[str, Any]:
    probs: list[str] = []
    obs: dict[str, Any] = {}
    parts, w, d = a7_one((40, 41, 39))
    v = parts["verify"]["json"]
    exp = d["draft"]["expected"]
    obs["a"] = {
        "rc": parts["verify"]["rc"],
        "verdict": v.get("verdict"),
        "before": v.get("before", {}).get("cost_per"),
        "after": v.get("after", {}).get("cost_per"),
        "delta": v.get("change", {}).get("delta"),
        "relative": v.get("change", {}).get("relative"),
        "predicted": (v.get("predicted") or {}).get("delta"),
        "error": v.get("error"),
        "commit_is_apply_sha": v.get("entry", {}).get("commit") == d["commit"]["sha"],
        "expected": exp,
    }
    want_a = {
        "rc": 0,
        "verdict": "improved",
        "before": 100.0,
        "after": 40.0,
        "delta": -60.0,
        "relative": -0.6,
        "predicted": -60.0,
        "error": 0.0,
        "commit_is_apply_sha": True,
        "expected": "expected: X 100.0 s -> saving 60.0 s a gate (60%; x_repeats 5 -> 2)",
    }
    if obs["a"] != want_a:
        probs.append(f"a: {obs['a']}")
    if parts["ledger"]["rc"] != 0:
        probs.append(f"a: next.close rc {parts['ledger']['rc']}")
    # c: apply again -> closed ; d: discard -> merged
    again = w.apply(ID, "--no-gates")
    obs["c"] = {
        "rc": again["rc"],
        "reason": again["json"].get("reason"),
        "detail": str(again["json"].get("detail"))[:30],
    }
    if (
        again["rc"] != 3
        or again["json"].get("reason") != "closed"
        or not str(again["json"].get("detail")).startswith(f"closed by {ID} at ")
    ):
        probs.append(f"c: {obs['c']}")
    dr = w.discard(ID)
    obs["d"] = {"rc": dr["rc"], "reason": dr["json"].get("reason")}
    if dr["rc"] != 3 or dr["json"].get("reason") != "merged":
        probs.append(f"d: {obs['d']}")
    # b: no improvement
    parts_b, _, _ = a7_one((100, 99, 101))
    vb = parts_b["verify"]["json"]
    obs["b"] = {
        "rc": parts_b["verify"]["rc"],
        "verdict": vb.get("verdict"),
        "error": vb.get("error"),
    }
    if obs["b"] != {"rc": 1, "verdict": "not_improved", "error": 1.0}:
        probs.append(f"b: {obs['b']}")
    return {"obs": obs, "problems": probs, "raw": {"a": parts["verify"]["json"], "b": vb}}


def a7(runs: int) -> dict[str, Any]:
    res = repeat(runs, a7_run)
    return row(
        "A7",
        "a improved exit 0 100.00 -> 40.00, delta -60.00, rel -0.600, predicted "
        "-60.0, error 0.000, entry.commit = apply sha; b not_improved exit 1 error 1.000; "
        "c closed; d merged (4 of 4)",
        f"a {res['obs'].get('a', {}).get('verdict')} {res['obs'].get('a', {}).get('delta')}; "
        f"b {res['obs'].get('b', {}).get('verdict')}; {len(res['problems'])} problem(s)",
        not res["problems"],
        problems=res["problems"],
        digests=res["digests"],
        raw=res["raw"],
    )


# ------------------------------------------------------------------ A8 no regression


def run_cmd(
    argv: list[str], *, cwd: Path = ROOT, timeout: float = 3600, extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    env.update(PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1", **(extra or {}))
    return subprocess.run(
        argv, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout
    )


def a8(out: Path, with_check_sh: bool = True) -> dict[str, Any]:
    probs: list[str] = []
    raw: dict[str, Any] = {}
    # 1. pytest tests/improve tests/test_cli.py
    t0 = time.monotonic()
    r = run_cmd(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/improve",
            "tests/test_cli.py",
            "-q",
            "-p",
            "no:cacheprovider",
        ]
    )
    tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    raw["pytest"] = {"rc": r.returncode, "tail": tail, "seconds": round(time.monotonic() - t0, 1)}
    if r.returncode != 0:
        probs.append(f"pytest: {tail}")
    # 2. improve2's probes
    t0 = time.monotonic()
    d2 = out / "_improve2"
    r = run_cmd(
        [
            sys.executable,
            "dogfood/improve2_probes.py",
            "--out",
            str(d2),
            "--scratch",
            str(SCRATCH / "A8-improve2"),
        ]
    )
    tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    raw["improve2_probes"] = {
        "rc": r.returncode,
        "tail": tail,
        "seconds": round(time.monotonic() - t0, 1),
    }
    shutil.rmtree(d2, ignore_errors=True)
    if r.returncode != 0 or tail != "probes: 15/15 pass":
        probs.append(f"improve2 probes: {tail}")
    # 3. IM1-IM21 from the mutant script's output
    mj = out / "mutants.json"
    if mj.is_file():
        runs = jload(mj.read_text()).get("runs", {})
        im = {k: v["verdict"] for k, v in runs.items() if k.startswith("IM")}
        raw["IM"] = {"killed": sum(1 for v in im.values() if v == "caught"), "total": len(im)}
        if raw["IM"] != {"killed": 21, "total": 21}:
            probs.append(f"IM mutants: {raw['IM']}")
    else:
        raw["IM"] = "mutants.json not found"
        probs.append("IM mutants: no mutants.json (run dogfood/improve_mutants.py --out first)")
    # 4. byte-identical older commands vs a4ab4a1
    ident = a8_identity()
    raw["identity"] = ident
    probs += ident["issues"]
    # 5. the studio gate
    if with_check_sh:
        t0 = time.monotonic()
        r = run_cmd(["dogfood/studio/check.sh"])
        lines = [x for x in r.stdout.splitlines() if x.strip()]
        tail = lines[-1] if lines else r.stderr.strip()[-200:]
        raw["check_sh"] = {
            "rc": r.returncode,
            "tail": tail,
            "seconds": round(time.monotonic() - t0, 1),
        }
        if (
            r.returncode != 0
            or "PASS: 0 findings" not in r.stdout
            or "435 cases run" not in r.stdout
        ):
            probs.append(f"check.sh: {tail}")
    return row(
        "A8",
        "tests/improve + tests/test_cli.py 0 failures; improve2 15 of 15; IM1-IM21 "
        "21 of 21; check.sh PASS 435 cases; the five older commands and their --help "
        "byte-identical to a4ab4a1; improve --help differs by the apply lines only",
        f"pytest `{raw['pytest']['tail']}`; improve2 `{raw['improve2_probes']['tail']}`; "
        f"IM {raw['IM']}; identity {ident['equal']}/{ident['total']}; "
        f"{len(probs)} problem(s)",
        not probs,
        problems=probs,
        raw=raw,
    )


def a8_identity() -> dict[str, Any]:
    base = fresh("A8-base")
    arch = subprocess.run(
        ["git", "-C", str(LATTICE_SRC), "archive", BASE_REF, "lattice"],
        capture_output=True,
        check=True,
    )
    subprocess.run(["tar", "-x", "-C", str(base)], input=arch.stdout, check=True)
    w = fresh("A8-inputs")
    proj, state = w / "proj", w / "state"
    proj.mkdir()
    (proj / "studio.toml").write_text('[rebuild]\ngates = ["./check.sh"]\n')
    issues: list[str] = []
    rows: list[dict[str, Any]] = []

    def reset(events: str, ledger: str | None = None) -> None:
        shutil.rmtree(state, ignore_errors=True)
        state.mkdir()
        (state / "events.jsonl").write_text(events)
        if ledger is not None:
            (state / "improvements.jsonl").write_text(ledger)

    def both(label: str, argv: list[str]) -> None:
        full = [*argv, "--project", str(proj), "--state", str(state)]
        old, new = cli(full, tree=base), cli(full, tree=ROOT)
        rows.append(
            {
                "label": label,
                "rc": new["rc"],
                "identical": old == new,
                "digest": sha(new["out"] + new["err"])[:16],
            }
        )
        if old != new:
            issues.append(f"{label}: differs from {BASE_REF}")

    nine = "".join(x + "\n" for x in i3_lines(9))
    eighteen = "".join(x + "\n" for x in i3_lines())
    v1a = (
        json.dumps(
            {
                "id": "I-01",
                "signal": X,
                "status": "closed",
                "commit": "scratch-clone studio.toml (not merged)",
                "ts": CLOSE,
            }
        )
        + "\n"
    )
    ir_events = (DATA / "ir-replay-events.jsonl").read_text()
    ir_ledger = (DATA / "ir-replay-improvements.jsonl").read_text()
    reset(nine)
    for name, argv in (
        ("report", ["report"]),
        ("report --json", ["report", "--json"]),
        ("draft D1b", ["draft", ID]),
        ("draft D1b --json", ["draft", ID, "--json"]),
        ("draft refused", ["draft", "imp-56861ff8"]),
        ("draft unknown", ["draft", "imp-00000000", "--json"]),
    ):
        both(f"{name} (I-3 9)", argv)
    reset(eighteen, v1a)
    for name, argv in (
        ("verify V1a", ["verify", "I-01"]),
        ("verify V1a --json", ["verify", "I-01", "--json"]),
        ("report", ["report"]),
        ("ledger", ["ledger"]),
        ("ledger --json", ["ledger", "--json"]),
    ):
        both(f"{name} (I-3 18 + V1a)", argv)
    reset(ir_events, ir_ledger)
    for name, argv in (
        ("report", ["report"]),
        ("report --json", ["report", "--json"]),
        ("ledger", ["ledger"]),
        ("ledger --json", ["ledger", "--json"]),
    ):
        both(f"{name} (I-R)", argv)
    plan = str(ROOT / "docs/results/data/streak-plans-2026-10-11/p2-final.json")
    full = [
        "import-plan",
        plan,
        "--json",
        "--project",
        str(proj),
        "--state",
        str(state),
    ]  # import-plan writes the log: each tree gets a fresh state directory
    reset("")
    old = cli(full, tree=base)
    reset("")
    new = cli(full, tree=ROOT)
    rows.append(
        {
            "label": "import-plan",
            "rc": new["rc"],
            "identical": old == new,
            "digest": sha(new["out"] + new["err"])[:16],
        }
    )
    if old != new:
        issues.append(f"import-plan: differs from {BASE_REF}")
    for sub_ in ("report", "import-plan", "ledger", "verify", "draft"):
        old, new = cli([sub_, "--help"], tree=base), cli([sub_, "--help"], tree=ROOT)
        rows.append(
            {
                "label": f"{sub_} --help",
                "rc": new["rc"],
                "identical": old == new,
                "digest": sha(new["out"])[:16],
            }
        )
        if old != new:
            issues.append(f"{sub_} --help differs")
    # improve --help: only the apply lines differ
    old, new = cli(["--help"], tree=base), cli(["--help"], tree=ROOT)
    kept: list[str] = []
    skip = False
    for ln in new["out"].splitlines():
        if re.match(r"^    apply\b", ln):
            skip = True
            continue
        if skip and ln.startswith("      "):
            continue
        skip = False
        kept.append(ln.replace(",apply}", "}"))
    same = "\n".join(kept) + "\n" == old["out"] and old["rc"] == new["rc"] == 0
    rows.append(
        {
            "label": "improve --help minus the apply lines",
            "rc": new["rc"],
            "identical": same,
            "digest": sha(new["out"])[:16],
        }
    )
    if not same:
        issues.append("improve --help differs by more than the apply lines")
    return {
        "rows": rows,
        "issues": issues,
        "equal": sum(1 for r in rows if r["identical"]),
        "total": len(rows),
    }


# ------------------------------------------------------------------ main


ORDER = ["A0", "A1a", "A1b", "A2a", "A2b-f", "A3", "A4", "A5", "A6", "A7", "A8"]


def main(argv: list[str] | None = None) -> int:
    global SCRATCH
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", type=int, default=RUNS)
    ap.add_argument("--a2a-runs", type=int, default=3, help="runs of the real dogfood gates")
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--only", help="comma-separated probe ids (default: all)")
    ap.add_argument("--no-check-sh", action="store_true", help="A8 without dogfood/studio/check.sh")
    args = ap.parse_args(argv)
    SCRATCH = args.scratch.resolve()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    want = args.only.split(",") if args.only else ORDER
    n = args.runs
    table = {
        "A0": lambda: a0(n),
        "A1a": lambda: a1a(n),
        "A1b": lambda: a1b(n),
        "A2a": lambda: a2a(args.a2a_runs),
        "A2b-f": lambda: a2_cases(n),
        "A3": lambda: a3(n),
        "A4": lambda: a4(n),
        "A5": lambda: a5(n),
        "A6": lambda: a6(n),
        "A7": lambda: a7(n),
        "A8": lambda: a8(args.out, not args.no_check_sh),
    }
    t0 = time.monotonic()
    rows = []
    print("id | predicted | observed | pass", flush=True)
    for pid in ORDER:
        if pid not in want:
            continue
        t1 = time.monotonic()
        r = table[pid]()
        r["seconds"] = round(time.monotonic() - t1, 1)
        rows.append(r)
        print(
            f"{r['id']} | {r['predicted']} | {r['observed']} | {'pass' if r['pass'] else 'FAIL'}",
            flush=True,
        )
        for p in r.get("problems", [])[:10]:
            print(f"    ! {p}", flush=True)
        (args.out / f"{r['id']}.json").write_text(json.dumps(r, indent=1, default=str) + "\n")
    passed = sum(1 for r in rows if r["pass"])
    for f in FINDINGS:
        print(f"finding: {f}")
    print(f"probes: {passed}/{len(rows)} pass")
    summary = {
        "runs": n,
        "a2a_runs": args.a2a_runs,
        "base_ref": BASE_REF,
        "table": [
            {k: r[k] for k in ("id", "predicted", "observed", "pass", "seconds")} for r in rows
        ],
        "probes_passed": passed,
        "probes_total": len(rows),
        "elapsed_s": round(time.monotonic() - t0, 1),
        "findings": FINDINGS,
        "post_hoc_script_corrections": POST_HOC_SCRIPT_CORRECTIONS,
        "decisions": DECISIONS,
    }
    sp = args.out / "summary.json"
    if args.only and sp.is_file():  # a partial rerun replaces its rows of the earlier summary
        prev = json.loads(sp.read_text())
        merged = {r["id"]: r for r in prev["table"]}
        merged.update({r["id"]: r for r in summary["table"]})
        summary["table"] = [merged[i] for i in ORDER if i in merged]
        summary["probes_passed"] = sum(1 for r in summary["table"] if r["pass"])
        summary["probes_total"] = len(summary["table"])
        summary["elapsed_s"] = round(prev.get("elapsed_s", 0) + summary["elapsed_s"], 1)
        summary["reruns"] = [*prev.get("reruns", []), sorted(want)]
        summary["findings"] = [*prev.get("findings", []), *FINDINGS]
        summary["post_hoc_script_corrections"] = prev.get(
            "post_hoc_script_corrections", POST_HOC_SCRIPT_CORRECTIONS
        )
        passed = summary["probes_passed"]
        rows = summary["table"]  # type: ignore[assignment]
    sp.write_text(json.dumps(summary, indent=1) + "\n")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
