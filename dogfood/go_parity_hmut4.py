"""The held-out mutants HM1-HM16 of Go parity, round 3, with the operator classifier corrected: the runner
``go_parity_hmut4`` (a NEW file; ``go_parity_hmut3.py`` is frozen, was scored once and is not edited).

The defect. ``go_parity_hmut3.classify_operator`` reads the operator column of MUTANTS.md with first-match-wins
patterns over the whole text. S4's writer put a description of the code edit in parentheses, "drop owner filter of an
owner-scoped READ (removes the 403 owner check)", and the pattern of ``status_403`` (``\\b403\\b``) matched the *edit*
before the owner-filter fallback could match the *operator*. HM1-4 were therefore judged with the R3 404 detector of
HM9-10 and scored as survivors, although layer A had found GO801 on their routes (docs/results/2026-10-19-go-parity-
round3-posthoc.md: 8 of 16 killed as scored, 11 of 16 with a scratch-only classifier correction).

The correction. The head clause of the text (what stays after the parentheses are removed) names the operator; the
parenthetical describes the edit and is read only when the head names nothing, and then without a "removes the 403"
that is the check being removed. The patterns, the operators, the detectors, the class table of prereg 6.2, the gates
and every other function are the frozen runner's own, imported unchanged: the corrected classifier is swapped into
``go_parity_hmut3`` for the duration of a run (``corrected_classifier``) and put back afterwards.

Same command line as ``go_parity_hmut3`` (see its docstring); the differences are the names: output ``hmut4.json``,
the fixed ledger ``ledger-hmut4.jsonl`` (arm ``G3:HMUT4``) and the freeze, which must list this runner, its test and
the frozen runner. The S4 data is development data (it was seen, scored and diagnosed): a run of this file is a
re-judgement of known mutants, not a blind or a registered result, and nothing is re-scored by importing it."""  # noqa: E501

# ruff: noqa: E501
from __future__ import annotations

import json
import re
import shutil
import sys
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import go_parity_hmut3 as H3  # noqa: E402  (frozen: the whole machinery)
import go_parity_probes3 as P3  # noqa: E402  (frozen)
import go_parity_s3r as S3R  # noqa: E402  (frozen: freeze, ledger)

P1 = H3.P1
REPO = P3.REPO
RUNNER_NAME = "go_parity_hmut4"
RUNNER_REL = "dogfood/go_parity_hmut4.py"
TEST_REL = "tests/spec/test_go_parity_hmut4.py"
LEDGER = (
    S3R.R3_DATA / "ledger-hmut4.jsonl"
)  # fixed here, never derived from --out, never the ledger of the scored run
ARM = "G3:HMUT4"
KINDS = H3.KINDS
IDS = H3.IDS
OPERATORS = H3.OPERATORS
INDEX_OPERATOR = H3.INDEX_OPERATOR
CLASS_GATES = H3.CLASS_GATES
GATE_OVERALL = H3.GATE_OVERALL
MUST_LIST = tuple(
    dict.fromkeys((RUNNER_REL, TEST_REL, *H3.MUST_LIST))
)  # the frozen runner and its test stay listed

_FROZEN_CLASSIFY = H3.classify_operator  # the frozen patterns, whatever is swapped into H3 later
_PAREN = re.compile(r"\([^()]*\)")
_REMOVED_STATUS = re.compile(r"\bremoves? the \d{3}\b", re.I)


# ---- the corrected classifier -----------------------------------------------------------------------------------------


def classify_operator(text: str) -> str | None:
    """The operator key named by ``text`` (the operator column of MUTANTS.md), or None. The head clause decides; a
    parenthetical describes the edit and is consulted only when the head names no operator (then "removes the 403 ..."
    is the check that was removed, not a status the route answers)."""
    head = text
    while (shorter := _PAREN.sub(" ", head)) != head:
        head = shorter
    key = _FROZEN_CLASSIFY(head) if head.strip() else None
    return key or _FROZEN_CLASSIFY(_REMOVED_STATUS.sub("removes the", text))


@contextmanager
def corrected_classifier() -> Iterator[None]:
    """``go_parity_hmut3.operator_of`` (and so ``run_hmut``) calls the module-level ``classify_operator`` of the frozen
    runner; inside this block that name is the corrected one. Always restored."""
    was = H3.classify_operator
    H3.classify_operator = classify_operator
    try:
        yield
    finally:
        H3.classify_operator = was


def operator_of(num: int, text: str) -> dict[str, Any]:
    """The frozen ``operator_of`` with the corrected classifier."""
    with corrected_classifier():
        return H3.operator_of(num, text)


# ---- the self-hash check and the ledger (this runner's names) -----------------------------------------------------------


def verify_freeze(
    path: Path, this: Path, required: Sequence[Path] = (), *, root: Path | None = None
) -> dict[str, Any]:
    """:func:`go_parity_hmut3.verify_freeze` with :data:`MUST_LIST` of this runner (which adds this file, its test)."""
    root = root or REPO
    info = S3R.verify_listed(path, root=root, must_list=MUST_LIST, this=this)
    listed = {(root / rel).resolve() for _, rel in S3R.read_entries(path)}
    absent = sorted(str(p) for p in required if p.resolve() not in listed)
    if absent:
        raise S3R.FreezeError(
            f"freeze check failed ({path.name}): not listed {absent[:6]}{' ...' if len(absent) > 6 else ''} ({len(absent)} files)"
        )
    info["required_files_listed"] = len(required)
    return info


def ledger_gate(
    kind: str, runner_sha: str, freeze_sha: str, path: Path | None = None
) -> str | None:
    """None when a run of ``kind`` may start, else why not (one scored run; one rerun only after crash_before_metric)."""
    return S3R.ledger_refusal(S3R.read_ledger(path or LEDGER), ARM, kind, runner_sha, freeze_sha)


# ---- the command line ---------------------------------------------------------------------------------------------------


def parser():
    ap = H3.parser()
    ap.description = "Go parity round 3: the held-out mutants HM1-HM16 on a scratch copy of S4, with the corrected operator classifier"
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    this = Path(__file__).resolve()
    only = tuple(int(x) for x in args.only.split(",") if x.strip()) if args.only else IDS
    if args.only and args.kind != "shakedown":
        raise SystemExit(
            "--only restricts the mutants of a shakedown; a scored, rerun or verify run covers all 16"
        )
    if any(i not in IDS for i in only):
        raise SystemExit("--only takes numbers 1..16")
    s4, patches, truth_path = (
        Path(args.s4).resolve(),
        Path(args.patches).resolve(),
        Path(args.truth).resolve(),
    )
    out, scratch, freeze_path = (
        Path(args.out).resolve(),
        Path(args.scratch).resolve(),
        Path(args.freeze).resolve(),
    )
    lattice_file = S3R.lattice_file_inside(REPO)
    runner_sha = P1.sha(this)
    freeze_sha = P1.sha(freeze_path) if freeze_path.is_file() else "missing"
    why = ledger_gate(args.kind, runner_sha, freeze_sha)
    if why:
        raise SystemExit(f"the ledger refuses this run: {why}")
    run_id = uuid.uuid4().hex[:12]

    def entry(status: str, **extra: Any) -> dict[str, Any]:
        return {"ts": H3.now(), "run_id": run_id, "runner": RUNNER_NAME, "runner_sha256": runner_sha, "freeze_sha256": freeze_sha, "arm": ARM,
                "kind": args.kind, "out": str(out), "lattice_file": lattice_file, "status": status, **extra}  # fmt: skip

    S3R.append_ledger(entry("started"), LEDGER)
    metric_started = False
    try:
        # the self-hash check first: nothing is measured before every entry has been re-hashed
        freeze = verify_freeze(freeze_path, this, H3.required_paths(s4, patches, truth_path))
        freeze["lattice_file"] = lattice_file
        truth, rows, files = H3.validate_inputs(s4, patches, truth_path)
        tgt = P3.make_target(s4.parent, s4.name, truth_path, "S4")
        tgt["truth_path"] = truth_path
        ctx = H3.make_ctx({"base": args.base}, scratch, out)
        ctx.h_target = tgt
        extra = H3.setup(ctx, tgt, patches)
        freeze["import_subprocess_lattice_file"] = S3R.subprocess_lattice_file(ctx)

        def control_done(c: Any) -> None:  # the control verdict is the first metric
            nonlocal metric_started
            metric_started = True

        with corrected_classifier():
            doc = H3.run_hmut(
                ctx, tgt, patches, truth, rows, files, out, only=only, on_control=control_done
            )
    except BaseException as err:  # noqa: BLE001 -- recorded in the ledger, then re-raised
        S3R.append_ledger(
            entry(
                "crash_after_metric" if metric_started else "crash_before_metric",
                error=repr(err)[:400],
            ),
            LEDGER,
        )
        if isinstance(err, S3R.FreezeError):
            print(err, file=sys.stderr)
            out.mkdir(parents=True, exist_ok=True)
            (out / "hmut4.json").write_text(
                json.dumps(
                    {"runner": RUNNER_NAME, "aborted": str(err), "kind": args.kind, "rows": []},
                    indent=2,
                )
                + "\n"
            )
            return 2
        raise
    changed = H3.finish_snapshots(ctx, extra, tgt, patches)
    shim_calls = [ln for ln in ctx.shim_log.read_text().splitlines() if ln.strip()]
    doc.update(runner=RUNNER_NAME, runner_sha256=runner_sha, kind=args.kind, run_id=run_id, base_ref=H3.BASE_REF, lattice_file=lattice_file, freeze=freeze,
               only=list(only), timings=ctx.timings, python=sys.version.split()[0], fixtures_changed=changed, shim_calls=shim_calls,
               operator_classifier=f"{RUNNER_NAME}.classify_operator (head clause first; the frozen runner's patterns)")  # fmt: skip
    out.mkdir(parents=True, exist_ok=True)
    (out / "hmut4.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")
    imp = ctx.scratch / "imp" / f"h-{tgt['label']}"
    for name in (
        "report.json",
        "candidate_spec.py",
        "profile.py",
        "proposals.json",
        "go_policy.json",
    ):
        if (imp / name).exists():
            shutil.copyfile(imp / name, out / f"hmut_import_{name}")
    control_ok = bool(doc["control"].get("passes"))
    S3R.append_ledger(
        entry(
            "completed",
            control_passed=control_ok,
            s4_tree_sha256=doc["s4_tree_sha256"],
            killed=doc.get("gate", {}).get("killed"),
        ),
        LEDGER,
    )
    H3.summarise(doc)
    print(f"fixtures changed: {changed}; docker/claude shim calls: {shim_calls}")
    print(f"ledger sha256: {P1.sha(LEDGER)}")
    return 0 if control_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
