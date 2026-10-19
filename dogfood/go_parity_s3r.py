"""Tier 0 of Go parity round 3 (docs/results/2026-10-18-go-parity-round3-prereg.md, section 5.1 and
Amendment 1 Q7; Spike J, node T0): the S3 re-score on the round-2 library (978b468), before any G3
library edit. Rows T0-PI4, T0-PR3, T0-CTL, T0-FORM and T0-FREEZE.

Why this runner exists (K7, G-c). Round 2's PI4-3 and PR3-3 crashed with ``ModuleNotFoundError:
gotickets_harness``: the importer CLI puts the import root on ``sys.path`` (``cli.py`` and
``rules.import_rules``), the frozen S3 runner's in-process ``machinery()`` did not, and the S3
``studio.toml`` names the harness with a bare module. This runner puts the import root on
``sys.path`` exactly as the CLI does (:func:`add_import_root`) before any in-process machinery. The
library is not touched (Amendment 1 Q5: K7 stays runner-only).

    "$PY" dogfood/go_parity_s3r.py --kind scored \\
        --out docs/results/data/2026-10-18-go-parity-round3/t0 --scratch "$SCRATCH/t0"

What cannot be switched off (prereg section 10, rules 2 and 3):

* the self-hash checks, before any metric: ``freeze.sha256`` (21 entries), ``s3runner.sha256`` (3),
  ``s3.freeze.sha256`` (8 files of S3 plus ``FILES.txt`` from the author archive) of round 2, each of
  those list files pinned by its own sha256, and ``t0-freeze.sha256`` with this runner's own entry
  (and its test file's). There is no flag to skip or narrow a check; a mismatch aborts, no metric;
* the library under test is the round-2 library: ``lattice/`` is byte-identical to 978b468;
* ``lattice.__file__`` must lie inside this worktree (the shared venv must not import the main
  checkout's ``lattice``);
* the global run ledger ``docs/results/data/2026-10-18-go-parity-round3/ledger.jsonl`` (a fixed path,
  not derived from ``--out``): a start and an end entry per run; a ``scored`` run refuses to start
  when the arm already has a scored run; a ``rerun`` is allowed once, only after a scored run ended
  ``crash_before_metric`` with identical runner and freeze bytes; ``verify`` is allowed once.

Nothing here edits a fixture or a library file: imports write to the scratch, the Go app is built to
the scratch, and recording ``docker`` / ``claude`` shims come first on ``PATH``.
"""  # noqa: E501

# ruff: noqa: E501
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import traceback
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import go_parity_probes as P1  # noqa: E402  (round 1, frozen: helpers)
import go_parity_probes2 as P2  # noqa: E402  (round 2, frozen: report_of, correction_map)

REPO = HERE.parent
SPEC_DIR = REPO / "tests/spec"
RUNNER_NAME = "go_parity_s3r"
RUNNER_REL = "dogfood/go_parity_s3r.py"
TEST_REL = "tests/spec/test_go_parity_s3r.py"
ARM = "T0"
BASE_REF = "978b468"
PACKAGE = "gotickets"
BARE_HARNESS = "gotickets_harness"
DOTTED_HARNESS = "tests.spec.gotickets_harness"
BINARY_ENV = "LATTICE_GOTICKETS_BIN"

R2_DATA = REPO / "docs/results/data/2026-10-18-go-parity-gaps-round2"
R3_DATA = REPO / "docs/results/data/2026-10-18-go-parity-round3"
LEDGER = R3_DATA / "ledger.jsonl"  # fixed here, never derived from --out
T0_FREEZE = R3_DATA / "t0-freeze.sha256"
ARCHIVE = REPO / "docs/results/data/archive-frozen-sets-2026-10-18/s3-gotickets-author-out.tgz"
ARCHIVE_PREFIX = "out/"
ARCHIVE_ONLY = ("FILES.txt",)  # entries of s3.freeze.sha256 that live only in the author archive

# round 2's list files: entries expected, and the list file's own sha256 (so a list cannot be
# shortened or edited without this runner's hash, which t0-freeze.sha256 pins, changing)
ROUND2_LISTS: dict[str, tuple[int, str]] = {
    "freeze.sha256": (21, "800fc1f7131ca9969bef0496541e3ff9e7a232ab9aa5a4cc0d9f23d0322891f0"),
    "s3runner.sha256": (3, "35524804d13e541a5f8cde1e625907f740ca383511465283692a77b4055a461b"),
    "s3.freeze.sha256": (9, "0a27192ddd13b9baf9b97f22603424db9e56a41a5218290a00fc332c635fc85d"),
}
ROUND2_MUST_LIST = {
    "s3runner.sha256": (
        "dogfood/go_parity_s3.py",
        "dogfood/go_parity_s3_mutants.py",
        "tests/spec/test_go_parity_s3.py",
    ),
    "s3.freeze.sha256": (
        "tests/spec/gotickets/api/api.go",
        "tests/spec/gotickets/go.mod",
        "tests/spec/gotickets/go.sum",
        "tests/spec/gotickets/main.go",
        "tests/spec/gotickets/models/models.go",
        "tests/spec/gotickets/studio.toml",
        "tests/spec/gotickets/truth.json",
        "tests/spec/gotickets_harness.py",
        "FILES.txt",
    ),
}
T0_MUST_LIST = (RUNNER_REL, TEST_REL)
KINDS = ("shakedown", "scored", "rerun", "verify")

VERIFIED = P1.VERIFIED
result, not_run, sha = P1.result, P1.not_run, P1.sha

# ---- predictions (prereg 5.1; fixed before any run) -------------------------------------------------

PREDICT_SEED_VERIFIED = 35
PREDICT_STILL_VERIFIED = (34, 35)
PREDICT_ITEMS = 63
PREDICT_CORRECTIONS = 0
PASS_PI4 = 0.95  # round 2's rule

ROW_IDS = ("T0-PI4", "T0-PR3", "T0-CTL", "T0-FORM", "T0-FREEZE")


# ---- the self-hash checks (never disabled) -----------------------------------------------------------


class FreezeError(SystemExit):
    """A freeze check failed: the run aborts before any metric."""


def read_entries(path: Path) -> list[tuple[str, str]]:
    """``(digest, relative path)`` of a sha256sum-style file; a missing file aborts."""
    if not path.is_file():
        raise FreezeError(f"freeze check failed: {path} does not exist")
    out = []
    for ln in path.read_text().splitlines():
        if ln.strip():
            digest, rel = ln.split(None, 1)
            out.append((digest, rel.strip()))
    return out


def verify_listed(
    path: Path,
    *,
    root: Path,
    must_list: Sequence[str] = (),
    expect_entries: int | None = None,
    pinned_sha: str | None = None,
    alt_roots: Mapping[str, Path] | None = None,
    this: Path | None = None,
) -> dict[str, Any]:
    """Every entry of a sha256sum-style file is re-hashed under ``root`` (``alt_roots`` maps an entry
    to the directory it lives in instead); any mismatch, missing file, missing required entry, wrong
    entry count, wrong list-file hash, or ``this`` (the running script) not being listed aborts.
    Nothing can be excluded."""
    entries = read_entries(path)
    problems: list[str] = []
    bad = []
    for digest, rel in entries:
        f = (alt_roots or {}).get(rel, root) / rel
        if not f.is_file() or sha(f) != digest:
            bad.append(rel)
    listed = {rel for _, rel in entries}
    missing = [m for m in must_list if m not in listed]
    own = this is None or any((root / r).resolve() == this.resolve() for r in listed)
    if bad:
        problems.append(f"mismatch {bad}")
    if missing:
        problems.append(f"not listed {missing}")
    if not own:
        problems.append("running script not listed")
    if expect_entries is not None and len(entries) != expect_entries:
        problems.append(f"{len(entries)} entries, expected {expect_entries}")
    if pinned_sha is not None and sha(path) != pinned_sha:
        problems.append(f"list file sha256 {sha(path)[:16]} != pinned {pinned_sha[:16]}")
    if problems:
        raise FreezeError(f"freeze check failed ({path.name}): " + "; ".join(problems))
    return {
        "file": str(path),
        "list_sha256": sha(path),
        "entries": len(entries),
        "mismatches": 0,
        "running_script_listed": bool(own) if this is not None else None,
    }


def extract_archive(archive: Path, dest: Path) -> Path:
    """The S3 author archive, extracted to ``dest`` (scratch). The archive is only read."""
    if not archive.is_file():
        raise FreezeError(f"freeze check failed: {archive} does not exist")
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    with tarfile.open(archive) as tf:
        tf.extractall(dest, filter="data")
    return dest / ARCHIVE_PREFIX.rstrip("/")


def library_unchanged(repo: Path, ref: str = BASE_REF) -> dict[str, Any]:
    """``lattice/`` equals ``ref``: no tracked difference (``git diff``) and no untracked file. The
    tracked files of ``dogfood/`` and ``tests/`` are compared too (new files of this node are
    untracked and so not part of the comparison)."""

    def git(*a: str) -> str:
        p = subprocess.run(
            ["git", "-C", str(repo), *a], capture_output=True, text=True, check=False
        )
        if p.returncode != 0:
            raise FreezeError(f"freeze check failed: git {' '.join(a)}: {p.stderr.strip()[:300]}")
        return p.stdout

    git("rev-parse", "--verify", f"{ref}^{{commit}}")
    diff = [x for x in git("diff", "--name-only", ref, "--", "lattice", "dogfood", "tests").split()]
    untracked = [
        x for x in git("ls-files", "--others", "--exclude-standard", "--", "lattice").split()
    ]
    if diff or untracked:
        raise FreezeError(
            f"freeze check failed: the tree differs from {ref}: tracked {diff[:10]} "
            f"untracked-under-lattice {untracked[:10]}"
        )
    return {"ref": ref, "head": git("rev-parse", "HEAD").strip(), "tracked_diff": 0, "untracked": 0}


def verify_all(
    this: Path,
    scratch: Path,
    *,
    repo: Path = REPO,
    r2_data: Path = R2_DATA,
    t0_freeze: Path = T0_FREEZE,
    archive: Path = ARCHIVE,
) -> dict[str, Any]:
    """T0-FREEZE: round 2's three list files, the S3 archive, this runner's own freeze file and the
    round-2 library. Called before the first metric; raises :class:`FreezeError` on any mismatch."""
    out: dict[str, Any] = {}
    for name, (n, pin) in ROUND2_LISTS.items():
        kw: dict[str, Any] = {}
        if name == "s3.freeze.sha256":
            extracted = extract_archive(archive, scratch / "archive-s3")
            kw["alt_roots"] = {rel: extracted for rel in ARCHIVE_ONLY}
        out[name] = verify_listed(
            r2_data / name,
            root=repo,
            must_list=ROUND2_MUST_LIST.get(name, ()),
            expect_entries=n,
            pinned_sha=pin,
            **kw,
        )
        if name == "s3.freeze.sha256":
            out[name]["archive_copies_equal"] = check_archive_copies(
                r2_data / name, extracted, ARCHIVE_ONLY
            )
    out["t0-freeze.sha256"] = verify_listed(t0_freeze, root=repo, must_list=T0_MUST_LIST, this=this)
    out["library"] = library_unchanged(repo)
    return out


def check_archive_copies(listing: Path, extracted: Path, skip: Sequence[str]) -> int:
    """Each repository file of ``s3.freeze.sha256`` has a byte-equal copy in the author archive."""
    n = 0
    for digest, rel in read_entries(listing):
        if rel in skip:
            continue
        f = extracted / rel
        if not f.is_file() or sha(f) != digest:
            raise FreezeError(f"freeze check failed: the archive copy of {rel} differs")
        n += 1
    return n


# ---- the environment guard ---------------------------------------------------------------------------


def lattice_file_inside(repo: Path = REPO) -> str:
    """``lattice.__file__`` if it lies under ``repo``; else abort (the shared venv must not import the
    main checkout's ``lattice``)."""
    import lattice

    f = Path(lattice.__file__).resolve()
    if not f.is_relative_to(repo.resolve()):
        raise SystemExit(
            f"refusing to run: lattice.__file__ = {f} is not inside the worktree {repo} "
            "(set PYTHONPATH to the worktree)"
        )
    return str(f)


# ---- the global run ledger ---------------------------------------------------------------------------


def read_ledger(path: Path = LEDGER) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def append_ledger(entry: Mapping[str, Any], path: Path = LEDGER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(dict(entry), sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def runs_of(entries: Sequence[Mapping[str, Any]], arm: str, kind: str) -> list[dict[str, Any]]:
    """The runs of ``arm`` and ``kind``, in order: ``{run_id, start, status}`` (the last entry of the
    run_id gives the status; a run with only its start entry is ``started``)."""
    order: list[str] = []
    by: dict[str, dict[str, Any]] = {}
    for e in entries:
        if e.get("arm") != arm or e.get("kind") != kind:
            continue
        rid = e.get("run_id") or f"legacy-{len(order)}"
        if rid not in by:
            by[rid] = {"run_id": rid, "start": dict(e), "status": e.get("status")}
            order.append(rid)
        else:
            by[rid]["status"] = e.get("status")
    return [by[r] for r in order]


def ledger_refusal(
    entries: Sequence[Mapping[str, Any]],
    arm: str,
    kind: str,
    runner_sha: str,
    freeze_sha: str,
) -> str | None:
    """None when a run of ``kind`` for ``arm`` may start, else the reason it may not."""
    if kind not in KINDS:
        return f"unknown kind {kind!r}"
    if kind == "shakedown":
        return None
    scored = runs_of(entries, arm, "scored")
    if kind == "scored":
        if scored:
            return (
                f"arm {arm} already has a scored run ({scored[-1]['status']}); "
                "a crash_before_metric is repeated with --kind rerun, once"
            )
        return None
    reruns = runs_of(entries, arm, "rerun")
    if kind == "rerun":
        if not scored:
            return f"arm {arm} has no scored run to repeat"
        if scored[-1]["status"] != "crash_before_metric":
            return f"the scored run ended {scored[-1]['status']}, not crash_before_metric"
        if reruns:
            return f"arm {arm} already had its one rerun ({reruns[-1]['status']})"
        prev = scored[-1]["start"]
        if prev.get("runner_sha256") != runner_sha or prev.get("freeze_sha256") != freeze_sha:
            return "the frozen bytes (runner or freeze file) differ from the scored run's"
        return None
    # verify: once, after a completed scored (or rerun) run, same frozen bytes
    if runs_of(entries, arm, "verify"):
        return f"arm {arm} already had its one verify run"
    done = [r for r in [*scored, *reruns] if r["status"] == "completed"]
    if not done:
        return f"arm {arm} has no completed scored run to verify"
    if done[-1]["start"].get("runner_sha256") != runner_sha:
        return "the runner bytes differ from the scored run's"
    return None


# ---- K7: the import root on sys.path, as the CLI does ----------------------------------------------------


def add_import_root(root: Path | str) -> None:
    """``cli.py`` and ``rules.import_rules`` do exactly this before any harness is imported; the
    frozen S3 runner's in-process ``machinery()`` did not (G-c)."""
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def dotted_studio_toml(text: str) -> str:
    """The ``studio.toml`` text with every ``gotickets_harness:`` reference in the dotted form
    ``tests.spec.gotickets_harness:`` (the form the S4 brief requires)."""
    return text.replace(f'"{BARE_HARNESS}:', f'"{DOTTED_HARNESS}:')


def make_form_copy(src: Path, root: Path) -> Path:
    """A scratch import root holding a copy of ``src`` (the S3 module) whose ``studio.toml`` uses the
    dotted harness form. ``src`` is only read."""
    shutil.rmtree(root, ignore_errors=True)
    dest = root / src.name
    shutil.copytree(src, dest)
    toml = dest / "studio.toml"
    new = dotted_studio_toml(toml.read_text())
    if new == toml.read_text() or BARE_HARNESS + ":" in new.replace(DOTTED_HARNESS + ":", ""):
        raise ValueError("studio.toml: the bare harness form did not convert exactly")
    toml.write_text(new)
    return dest


# ---- the context, the import and the replicator -------------------------------------------------------


class Ctx(P1.Ctx):
    fx_bin: Path | None = None
    fx_error: str | None = None

    def env(self, **extra: str) -> dict[str, str]:
        env = super().env()
        env["LATTICE_CONTAINER_OWNER"] = "g3-t0"
        if self.fx_bin:
            env[BINARY_ENV] = str(self.fx_bin)
        env.update(extra)
        return env


def setup(ctx: Ctx) -> None:
    """Shims, scratch, the snapshot of the fixtures, the fixture's binary built to the scratch. A
    failing build is ``ctx.fx_error`` (the rows are NOT RUN: a named prerequisite is missing)."""
    P1.make_shims(ctx)
    ctx.scratch.mkdir(parents=True, exist_ok=True)
    ctx.out.mkdir(parents=True, exist_ok=True)
    root = f"tests/spec/{PACKAGE}"
    if root not in P1.SNAPSHOT_ROOTS:
        P1.SNAPSHOT_ROOTS.append(root)
    ctx.snap0 = P1.snapshots()
    ok, err, wall = P1.go_build(SPEC_DIR / PACKAGE, ctx.scratch / "fxbin" / PACKAGE)
    ctx.timings["build_fixture_s"] = wall
    if ok:
        ctx.fx_bin = ctx.scratch / "fxbin" / PACKAGE
        os.environ[BINARY_ENV] = str(ctx.fx_bin)
    else:
        ctx.fx_error = err


def subprocess_lattice_file(ctx: Ctx) -> str:
    """Where the import's subprocess finds ``lattice`` (it must be this worktree too)."""
    rc, out, err, _ = P1.run(
        [ctx.py, "-c", "import lattice;print(lattice.__file__)"], env=ctx.env()
    )
    f = Path(out.strip()).resolve() if rc == 0 and out.strip() else None
    if f is None or not f.is_relative_to(REPO.resolve()):
        raise SystemExit(
            f"refusing to run: the import subprocess imports lattice from {out!r} {err[-200:]}"
        )
    return str(f)


def import_cli(ctx: Ctx, root_arg: str, out_dir: Path) -> dict[str, Any]:
    """The rules-first import of S3 by the CLI, from the repository root, as round 2's ``import_fx``."""
    shutil.rmtree(out_dir, ignore_errors=True)
    rc, out, err, wall = P1.run(
        [ctx.py, "-m", "lattice.importer", root_arg, "--package", PACKAGE, "--rules-only",
         "--out", str(out_dir)],
        env=ctx.env(),
    )  # fmt: skip
    return {"rc": rc, "stdout": out[-600:], "stderr": err[-1500:], "wall_s": wall, "dir": out_dir}


def machinery(ctx: Ctx, root: Path, key: str) -> dict[str, Any]:
    """What ``rules.import_rules`` builds around the probes (front end, factory, runtime, the profile
    patch of E3/E5, seed, callers, unreachable routes, the marks it applies), rebuilt so the imported
    candidate spec can be probed again. It repeats ``import_rules``' wiring, with K7: the import
    ``root`` is put on ``sys.path`` first. ``key`` names the cache slot (one per import root)."""
    if key in ctx.cache:
        return ctx.cache[key]  # type: ignore[no-any-return]
    from lattice.importer import go_rules, rules
    from lattice.importer.frontends import select
    from lattice.importer.lift import lift_repo

    add_import_root(root)  # K7 (looked up by name at call time)
    # the dotted harness form (tests.spec.<x>) resolves from the repository root, as in the CLI process
    add_import_root(REPO)
    fe = go_rules.InferringFrontEnd(select("gin", root, PACKAGE))
    found = go_rules.harness_factory(root, PACKAGE)
    if found is None:
        raise RuntimeError(f"{root}/{PACKAGE}/studio.toml names no [app] app")
    factory, _name = found
    model = fe.extract(root, PACKAGE)
    runtime = rules._runtime(fe, root, PACKAGE, model)
    result_ = lift_repo(root, PACKAGE, factory, only=(), frontend=fe, runtime=runtime)
    go_rules.patch_profile(result_, fe.private, fe.validation_status)
    c = result_.context
    api = result_.lattice_profile.api
    seeder = rules.Seeder(
        result_.model, c.inputs.tables, c.inputs.auth, c.inputs.insert_hook, runtime, factory,
        api.uuid_storage, api.datetime_storage,
    )  # fmt: skip
    unreachable = {
        f"{e.method} {e.path}": e.reason
        for e in result_.model.excluded_routes
        if e.kind == "unreachable"
    }
    for ep, why in rules.network_routes(root, PACKAGE, result_.model).items():
        unreachable.setdefault(ep, why)
    for ep, why in rules.shadowed_routes(result_.model).items():
        unreachable.setdefault(ep, why)
    unclear = {
        t: result_.profile.ownership_reasons.get(t, "")
        for t, d in result_.profile.ownership.items()
        if d == "unclear"
    }
    m = {
        "result": result_,
        "seeder": seeder,
        "sessions": rules._sessions(runtime, factory),
        "unreachable": unreachable,
        "rules": rules,
        "unclear": unclear,
        "outside": rules.outside_api(result_.model),
        "uploads": rules.upload_routes(result_.model),
    }
    ctx.cache[key] = m
    return m


def r_on_spec(ctx: Ctx, spec: Any, variant: str, root: Path, key: str) -> Any:
    """Layer R of ``spec`` on the fixture, then the three marks the import applies."""
    from lattice.spec.probes import run_probes

    m = machinery(ctx, root, key)
    seeder, result_, rules = m["seeder"], m["result"], m["rules"]
    who = rules.callers(result_, seeder, variant)
    kw = dict(
        login=who.login, mint_token=who.mint, scheme=who.scheme, refusals=who.refusals,
        login_route=who.login_route, auth_headers=who.headers, inactive_login=who.inactive_login,
        unreachable=m["unreachable"], variant=variant, auth_present=who.present,
    )  # fmt: skip
    rr = run_probes(
        replace(spec, users=seeder.credentials(variant)), m["sessions"], seeder.make_db, **kw
    )
    rules.mark_unclear(spec, rr, m["unclear"])
    rules.mark_outside(spec, rr, m["outside"])
    rules.mark_uploads(rr, m["uploads"])
    return rr


# ---- scoring functions (pure: the unit tests feed them synthetic inputs) ---------------------------------


def final_verdicts(rep: Mapping[str, Any]) -> dict[str, str]:
    """The import's final item verdicts, by the item ids as the report spells them."""
    return {i: v["verdict"] for i, v in rep["rules"]["items"].items()}


def mapped_verdicts(rep: Mapping[str, Any]) -> dict[str, str]:
    """The same, with each item id mapped through ``corrections[]`` first (round 2's rule)."""
    cmap = {c["item"]: c["corrected"] for c in rep["rules"]["corrections"] if c["corrected"]}
    return {cmap.get(i, i): v for i, v in final_verdicts(rep).items()}


def diff_maps(theirs: Mapping[str, str], mine: Mapping[str, str]) -> dict[str, list[str | None]]:
    """``{item: [import, rerun]}`` for every item whose verdict string differs or is missing."""
    return {
        i: [theirs.get(i), mine.get(i)]
        for i in sorted(set(theirs) | set(mine))
        if theirs.get(i) != mine.get(i)
    }


def count_verified(verdicts: Mapping[str, str]) -> int:
    return sum(1 for v in verdicts.values() if v in VERIFIED)


def score_pi4(seed: Mapping[str, str], v2: Mapping[str, str]) -> dict[str, Any]:
    """Round 2's PI4-3: every seed-verified item still verified on variant2, fraction >= 0.95."""
    base = [i for i, v in seed.items() if v in VERIFIED]
    still = [i for i in base if v2.get(i) in VERIFIED]
    frac = len(still) / len(base) if base else 0.0
    return {
        "seed_verified": len(base),
        "still_verified": len(still),
        "fraction": round(frac, 4),
        "lost": {i: v2.get(i) for i in base if i not in still},
        "pass": frac >= PASS_PI4,
        "prediction_met": len(base) == PREDICT_SEED_VERIFIED
        and PREDICT_STILL_VERIFIED[0] <= len(still) <= PREDICT_STILL_VERIFIED[1],
    }


def score_pr3(rep: Mapping[str, Any], rerun: Mapping[str, str]) -> dict[str, Any]:
    """Round 2's PR3-3: the rerun's verdict map equals the import's after the correction map."""
    theirs = mapped_verdicts(rep)
    diff = diff_maps(theirs, rerun)
    n_corr = len(rep["rules"]["corrections"])
    return {
        "items_import": len(theirs),
        "items_rerun": len(rerun),
        "corrections": n_corr,
        "differences [import, rerun]": diff,
        "pass": not diff,
        "prediction_met": not diff
        and len(theirs) == len(rerun) == PREDICT_ITEMS
        and n_corr == PREDICT_CORRECTIONS,
    }


def score_ctl(rep: Mapping[str, Any], rerun: Mapping[str, str]) -> dict[str, Any]:
    """T0-CTL: the in-process rerun against the CLI import's final verdicts, literally (no map)."""
    theirs = final_verdicts(rep)
    diff = diff_maps(theirs, rerun)
    return {
        "items_import": len(theirs),
        "items_rerun": len(rerun),
        "verified_import": count_verified(theirs),
        "verified_rerun": count_verified(rerun),
        "differences [import, rerun]": diff,
        "pass": not diff,
        "prediction_met": not diff
        and len(theirs) == len(rerun) == PREDICT_ITEMS
        and count_verified(theirs) == PREDICT_SEED_VERIFIED,
    }


def score_form(
    rep_form: Mapping[str, Any], rerun_form: Mapping[str, str], rep_bare: Mapping[str, Any]
) -> dict[str, Any]:
    """T0-FORM: on the dotted-form copy, the rerun equals the CLI import's final verdicts, and those
    equal the bare form's (T0-CTL's) final verdicts."""
    theirs = final_verdicts(rep_form)
    within = diff_maps(theirs, rerun_form)
    across = diff_maps(final_verdicts(rep_bare), theirs)
    return {
        "items_import": len(theirs),
        "items_rerun": len(rerun_form),
        "verified_import": count_verified(theirs),
        "verified_rerun": count_verified(rerun_form),
        "differences within the dotted copy [import, rerun]": within,
        "differences bare vs dotted import [bare, dotted]": across,
        "pass": not within and not across,
        "prediction_met": not within
        and not across
        and len(theirs) == len(rerun_form) == PREDICT_ITEMS
        and count_verified(theirs) == PREDICT_SEED_VERIFIED,
    }


# ---- the rows --------------------------------------------------------------------------------------------

PREDICTED = {
    "T0-PI4": "35 seed-verified; 34-35 still verified on variant2; fraction >= 0.95",
    "T0-PR3": "identical verdict map after round 2's correction map; 63 = 63; 0 corrections",
    "T0-CTL": "in-process machinery() (root on sys.path) == the CLI import's final verdicts on the "
    "seed; 63 items, 35 verified",
    "T0-FORM": "the same on a scratch copy of S3 whose studio.toml uses the dotted harness form; "
    "identical to T0-CTL",
    "T0-FREEZE": "freeze.sha256 (21), s3runner.sha256 (3), s3.freeze.sha256 (8 files + FILES.txt) and "
    "the runner's own entry all OK before any metric",
}


def row(
    pid: str, probe: str, observed: Any, scored: Mapping[str, Any], **ev: Any
) -> dict[str, Any]:
    ev["prediction_met"] = scored["prediction_met"]
    return result(pid, probe, PREDICTED[pid], observed, bool(scored["pass"]), **ev)


def row_freeze(freeze: Mapping[str, Any]) -> dict[str, Any]:
    return result(
        "T0-FREEZE", "every self-hash entry, before any metric", PREDICTED["T0-FREEZE"],
        {k: v for k, v in freeze.items()}, True, prediction_met=True,
        reading="the run aborts (no metric) on any mismatch, so a row that exists is a pass",
    )  # fmt: skip


def crash_row(pid: str, probe: str, err: BaseException) -> dict[str, Any]:
    """A row whose code raised counts as FAIL (round-2 Amendment 6 rule a), never NOT RUN."""
    return result(
        pid, probe, PREDICTED[pid], f"{type(err).__name__}: {err}", False, prediction_met=False,
        traceback=traceback.format_exc()[-1500:],
    )  # fmt: skip


def not_run_row(pid: str, why: str) -> dict[str, Any]:
    return not_run(pid, "S3 re-score", why, PREDICTED[pid])


# ---- main -------------------------------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Go parity round 3, Tier 0: the S3 re-score on the round-2 library"
    )
    ap.add_argument("--kind", default="shakedown", choices=list(KINDS))
    ap.add_argument("--out", required=True)
    ap.add_argument("--scratch", default="")
    ap.add_argument("--base", default="", help="accepted for the prereg's command line; not used")
    ap.add_argument("--freeze", default="", help="accepted; must be the fixed t0-freeze.sha256")
    args = ap.parse_args(argv)
    if args.freeze and Path(args.freeze).resolve() != T0_FREEZE.resolve():
        raise SystemExit(f"--freeze must be {T0_FREEZE} (the self-hash check is not configurable)")
    this = Path(__file__).resolve()
    lattice_file = lattice_file_inside()
    out = Path(args.out).resolve()
    scratch = Path(args.scratch).resolve() if args.scratch else out.parent / "scratch-t0"
    run_id = uuid.uuid4().hex[:12]
    runner_sha = sha(this)
    freeze_sha = sha(T0_FREEZE) if T0_FREEZE.is_file() else "missing"
    refusal = ledger_refusal(read_ledger(), ARM, args.kind, runner_sha, freeze_sha)
    if refusal:
        raise SystemExit(f"the ledger refuses this run: {refusal}")

    def entry(status: str, **extra: Any) -> dict[str, Any]:
        return {
            "ts": _now(), "run_id": run_id, "runner": RUNNER_NAME, "runner_sha256": runner_sha,
            "freeze_sha256": freeze_sha, "arm": ARM, "kind": args.kind, "out": str(out),
            "lattice_file": lattice_file, "status": status, **extra,
        }  # fmt: skip

    append_ledger(entry("started"))
    metric_started = False
    try:
        # T0-FREEZE first: nothing is measured before every entry has been re-hashed
        freeze = verify_all(this, scratch)
        freeze["lattice_file"] = lattice_file
        freeze["import_subprocess_lattice_file"] = None
        ctx = Ctx(scratch, out, BASE_REF)
        setup(ctx)
        freeze["import_subprocess_lattice_file"] = subprocess_lattice_file(ctx)
        results: list[dict[str, Any]] = [row_freeze(freeze)]
        metric_started = True  # the first import below is the first metric
        results.extend(measure(ctx))
    except BaseException as err:  # noqa: BLE001 -- recorded in the ledger, then re-raised
        append_ledger(
            entry(
                "crash_after_metric" if metric_started else "crash_before_metric",
                error=repr(err)[:400],
            )
        )
        if isinstance(err, FreezeError):
            print(err, file=sys.stderr)
            out.mkdir(parents=True, exist_ok=True)
            (out / "probes-t0.json").write_text(
                json.dumps(
                    {"runner": RUNNER_NAME, "aborted": str(err), "kind": args.kind,
                     "results": [result("T0-FREEZE", "every self-hash entry", PREDICTED["T0-FREEZE"],
                                        str(err), False, prediction_met=False)]},
                    indent=2, default=str,
                ) + "\n"
            )  # fmt: skip
            return 2
        raise
    changed = P1.snapshot_diff(ctx.snap0, P1.snapshots())
    calls = [ln for ln in ctx.shim_log.read_text().splitlines() if ln.strip()]
    doc = {
        "runner": RUNNER_NAME, "runner_sha256": runner_sha, "kind": args.kind, "run_id": run_id,
        "base_ref": BASE_REF, "lattice_file": lattice_file, "freeze": freeze,
        "timings": ctx.timings, "fx_error": ctx.fx_error, "fixtures_changed": changed,
        "shim_calls": calls, "python": sys.version.split()[0], "results": results,
    }  # fmt: skip
    out.mkdir(parents=True, exist_ok=True)
    (out / "probes-t0.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")
    for tag in ("bare", "dotted"):
        d = scratch / "imp" / tag
        for name in ("report.json", "candidate_spec.py", "profile.py", "proposals.json",
                     "go_policy.json"):  # fmt: skip
            if (d / name).exists():
                shutil.copyfile(d / name, out / f"t0_import_{tag}_{name}")
    append_ledger(entry("completed"))
    passed = [r["id"] for r in results if r["pass"] is True]
    failed = [r["id"] for r in results if r["pass"] is False]
    nr = [r["id"] for r in results if r["pass"] is None]
    print(
        f"passed {len(passed)}: {passed}\nfailed {len(failed)}: {failed}\nnot run {len(nr)}: {nr}"
    )
    print(f"fixtures changed: {changed}; docker/claude shim calls: {calls}")
    print(f"ledger sha256: {sha(LEDGER)}")
    return 0


def measure(ctx: Ctx) -> list[dict[str, Any]]:
    """The four metric rows. Imports (CLI) and reruns (in process, K7) of S3 in its two harness forms."""
    if ctx.fx_error:
        return [
            not_run_row(p, f"S3 does not build: {ctx.fx_error[:400]}")
            for p in ("T0-PI4", "T0-PR3", "T0-CTL", "T0-FORM")
        ]
    rows: dict[str, dict[str, Any]] = {}
    state: dict[str, Any] = {}

    def guarded(pid: str, probe: str, fn: Callable[[], dict[str, Any]]) -> None:
        try:
            rows[pid] = fn()
        except Exception as err:  # noqa: BLE001 -- a row that cannot run is a FAIL, not a skip
            rows[pid] = crash_row(pid, probe, err)
        print(f"{pid}: pass={rows[pid]['pass']}", flush=True)

    bare_dir = ctx.scratch / "imp" / "bare"
    imp = import_cli(ctx, "tests/spec", bare_dir)
    ctx.timings["import_bare_s"] = imp["wall_s"]
    state["bare"] = imp

    def need_bare() -> tuple[dict[str, Any], Any]:
        if state["bare"]["rc"] != 0:
            raise RuntimeError(f"the S3 import failed: {state['bare']['stderr'][-400:]}")
        return P2.report_of(bare_dir), P1.load_candidate(bare_dir)

    def seed_bare() -> dict[str, str]:
        if "seed_bare" not in state:
            _, cand = need_bare()
            state["seed_bare"] = P1.verdict_map(r_on_spec(ctx, cand.SPEC, "seed", SPEC_DIR, "bare"))
        return state["seed_bare"]  # type: ignore[no-any-return]

    def t_pi4() -> dict[str, Any]:
        _, cand = need_bare()
        seed = seed_bare()
        v2 = P1.verdict_map(r_on_spec(ctx, cand.SPEC, "variant2", SPEC_DIR, "bare"))
        sc = score_pi4(seed, v2)
        return row(
            "T0-PI4", "the corrected S3 spec re-probed on variant2",
            {k: sc[k] for k in ("seed_verified", "still_verified", "fraction", "lost")}, sc,
        )  # fmt: skip

    def t_pr3() -> dict[str, Any]:
        rep, _ = need_bare()
        sc = score_pr3(rep, seed_bare())
        return row(
            "T0-PR3", "layer R on S3 with the imported candidate_spec.py (correction map applied)",
            {k: v for k, v in sc.items() if k not in ("pass", "prediction_met")}, sc,
        )  # fmt: skip

    def t_ctl() -> dict[str, Any]:
        rep, _ = need_bare()
        sc = score_ctl(rep, seed_bare())
        return row(
            "T0-CTL", "in-process machinery (root on sys.path) vs the CLI import, literally",
            {k: v for k, v in sc.items() if k not in ("pass", "prediction_met")}, sc,
        )  # fmt: skip

    def t_form() -> dict[str, Any]:
        rep_bare, _ = need_bare()
        form_root = ctx.scratch / "form-root"
        make_form_copy(SPEC_DIR / PACKAGE, form_root)
        dotted_dir = ctx.scratch / "imp" / "dotted"
        t = import_cli(ctx, str(form_root), dotted_dir)
        ctx.timings["import_dotted_s"] = t["wall_s"]
        if t["rc"] != 0:
            raise RuntimeError(f"the dotted-form import failed: {t['stderr'][-400:]}")
        cand = P1.load_candidate(dotted_dir)
        mine = P1.verdict_map(r_on_spec(ctx, cand.SPEC, "seed", form_root, "dotted"))
        sc = score_form(P2.report_of(dotted_dir), mine, rep_bare)
        return row(
            "T0-FORM", "the same on a scratch copy whose studio.toml uses the dotted form",
            {k: v for k, v in sc.items() if k not in ("pass", "prediction_met")}, sc,
            studio_toml=(form_root / PACKAGE / "studio.toml").read_text(),
        )  # fmt: skip

    for pid, probe, fn in (
        ("T0-PI4", "PI4-3", t_pi4),
        ("T0-PR3", "PR3-3", t_pr3),
        ("T0-CTL", "CTL", t_ctl),
        ("T0-FORM", "FORM", t_form),
    ):
        guarded(pid, probe, fn)
    return [rows[p] for p in ("T0-PI4", "T0-PR3", "T0-CTL", "T0-FORM")]


if __name__ == "__main__":
    raise SystemExit(main())
