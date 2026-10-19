"""Mutants of Go parity, round 3 (docs/results/2026-10-18-go-parity-round3-prereg.md, sections 6.1 and 6.3
and 10; Spike J, node G3, builder B1): the 18 library mutants LM1-LM18, the two runner mutants RM1 and RM2,
the dev mutants of the regression arms (ST1-ST3, SM1-SM4, JSM1, JSM2) and the 12 round-2 mutants
re-applied on the G3 runner (JM5, JM17, JM18, JM19, JM22, JM24, JM26-JM29, JM32, JM33).

    "$PY" dogfood/go_parity_mutants3.py --base "$SCRATCH/base" --scratch "$SCRATCH/m" \\
        --freeze "$SCRATCH/g3.sha256" --streak "$SCRATCH/streak" --jobsched "$SCRATCH/jobsched" \\
        --out docs/results/data/2026-10-18-go-parity-round3/mutants [IDS ...]

The control (no change) runs first; a mutant is killed when a detector that passes on the control stops
passing on it. Library mutants are source edits to a scratch copy of the tree (``lattice``, ``tests``,
``bench``, ``dogfood``) judged by the G3 probe rows of *that copy* (``go_parity_probes3.rows_cli`` in a
subprocess: rows only, no scored output) and by named unit tests. The self-hash check of this runner
(``--freeze``, every entry, this script included) runs first and cannot be skipped; the copies are never
checked against the freeze, because the library under mutation is exactly what differs.

LM16 and LM17 mutate K8 (builder B2's ``notowned.py``); they run only when that tree is merged in and
``dogfood/g3_standin/k8_mutants.json`` (``{"LM16": {"edits": [[file, old, new]], "probes": [...], "tests":
[...]}}``) supplies their edits; otherwise they are NOT RUN with that named prerequisite.
"""  # noqa: E501

# ruff: noqa: E501
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import go_parity_mutants2 as M2  # noqa: E402  (round 2, frozen: the JM edits)
import go_parity_probes as P1  # noqa: E402
import go_parity_probes3 as G  # noqa: E402
import go_parity_s3 as S3M  # noqa: E402
import go_parity_s3r as S3R  # noqa: E402

REPO = G.REPO
TREE_DIRS = ("lattice", "tests", "bench", "dogfood")
K8_EDITS = HERE / "g3_standin/k8_mutants.json"
T_WRAP = "tests/gocheck/test_go_wrap3.py"
T_RULES = "tests/importer/test_go_rules3.py"
T_DERIVE = "tests/gocheck/test_go_derive3.py"

R, F, D = (
    "lattice/gocheck/src/routes.go",
    "lattice/gocheck/src/reach.go",
    "lattice/gocheck/derive.py",
)
GR, RU = "lattice/importer/go_rules.py", "lattice/importer/rules.py"

# id -> {what, killed_by (the registered prediction), edits [(file, old, new)], probes [G3 row ids], tests [node ids]}
LM: dict[str, dict[str, Any]] = {
    "LM1": {
        "what": "K1 off (the handler root is the wrapper literal again: round-2 behaviour)",
        "killed_by": "P0-WRAP; M-SM1",
        "edits": [(R, "func (c *checker) bind(e *Env, x ast.Expr) (*Func, *Func) {\n", "func (c *checker) bind(e *Env, x ast.Expr) (*Func, *Func) {\n\tif true {\n\t\treturn nil, nil\n\t}\n")],
        "probes": ["P0-WRAP", "P0-OPQ", "M-SM1"],
        "tests": [f"{T_WRAP}::test_k1_the_argument_of_a_wrapper_is_the_root_and_two_levels_stay_silent"],
    },
    "LM2": {
        "what": "K1 roots de-duplicated per handler function again (a function wrapped on two routes is judged once)",
        "killed_by": "unit test (the prereg names M-SM2, which Streak's distinct handlers cannot show)",
        "edits": [(R, "if r.Handler != nil && (r.Bound || !seen[r.Handler]) {", "if r.Handler != nil && !seen[r.Handler] {")],
        "probes": [],
        "tests": [f"{T_WRAP}::test_k1_every_wrapped_route_is_its_own_root"],
    },
    "LM3": {
        "what": "K1 binds two levels (w3 judged)",
        "killed_by": "P0-WRAP",
        "edits": [(R, "\t\t\tif arg == nil || arg.Lit != nil && g.wrapperParam(arg.Parent) >= 0 {\n\t\t\t\treturn nil, nil\n\t\t\t}\n",
                   "\t\t\tif b2, w2 := c.bind(e, call.Args[i]); b2 != nil {\n\t\t\t\treturn b2, w2\n\t\t\t}\n\t\t\tif arg == nil {\n\t\t\t\treturn nil, nil\n\t\t\t}\n")],
        "probes": ["P0-WRAP"],
        "tests": [f"{T_WRAP}::test_k1_the_argument_of_a_wrapper_is_the_root_and_two_levels_stay_silent"],
    },
    "LM4": {
        "what": "K1 for handlers only, not middleware",
        "killed_by": "P0-OPQ (the prereg names M-SM1, but Streak's middleware is never read: its routers come through a struct field)",
        "edits": [(R, "if b, wl := c.bind(mw.e, mw.x); b != nil {", "if b, wl := c.bind(mw.e, mw.x); false && b != nil {")],
        "probes": ["P0-OPQ"],
        "tests": [f"{T_WRAP}::test_k1_a_guard_returned_through_the_wrapper_is_read_as_the_middleware"],
    },
    "LM5": {
        "what": "K2 off (a method of an outside type is opaque again)",
        "killed_by": "P0-OPQ; PS-JM31",
        "edits": [(F, 'if pkg || xt.Kind == "ext" || xt.Kind == "unknown" && c.outside(e, f.X, 4) {', "if pkg {")],
        "probes": ["P0-OPQ", "PS-JM31"],
        "tests": [f"{T_WRAP}::test_k2_hmac_methods_on_a_local_bound_to_an_outside_call_are_not_opaque"],
    },
    "LM6": {
        "what": "K2 too broad: a receiver handed *gin.Context is not opaque",
        "killed_by": "P0-OPQ (the m2 route gains GO801)",
        "edits": [(F, 'if t.is("gin.Context") || t.is("gorm.DB") || c.ModelOf(t) != nil {', 'if t.is("gorm.DB") || c.ModelOf(t) != nil {')],
        "probes": ["P0-OPQ"],
        "tests": [f"{T_WRAP}::test_k2_a_method_of_an_outside_type_handed_the_request_stays_opaque"],
    },
    "LM7": {
        "what": "K2 too broad: an uninferable receiver is not opaque",
        "killed_by": "unit uninferable_receiver_stays_opaque",
        "edits": [(F, 'xt.Kind == "unknown" && c.outside(e, f.X, 4)', 'xt.Kind == "unknown"')],
        "probes": [],
        "tests": [f"{T_WRAP}::test_uninferable_receiver_stays_opaque", f"{T_WRAP}::test_k2_a_method_of_an_unknown_module_type_is_opaque_even_with_plain_arguments"],
    },
    "LM8": {
        "what": "K3 off (no body models)",
        "killed_by": "P0-BODY (PS-PI3-3 does not pass on the control, so it cannot flip)",
        "edits": [(GR, "model, body = infer_body_models(data, model)", "body = []")],
        "probes": ["P0-BODY"],
        "tests": [f"{T_RULES}::test_k3_the_contract_carries_the_body_field_with_its_bounds"],
    },
    "LM9": {
        "what": "K3 drops `required`",
        "killed_by": "P0-BODY",
        "edits": [(GR, "required=p.required,", "required=False,"), (GR, 'and not bound[0].startswith("struct@")', "and False")],
        "probes": ["P0-BODY"],
        "tests": [f"{T_RULES}::test_k3_the_contract_carries_the_body_field_with_its_bounds"],
    },
    "LM10": {
        "what": "K4 off (the Seeder runs on a server-dialect app)",
        "killed_by": "M-I2 (M-I1 does not pass on the control, so it cannot flip)",
        "edits": [(RU, "if fe.name == \"gin\" and (server := go_rules.server_dialect(root, package)):  # K4", "if False and fe.name == \"gin\" and (server := go_rules.server_dialect(root, package)):  # K4")],
        "probes": ["M-I2"],
        "tests": [f"{T_RULES}::test_k4_the_import_of_a_server_app_skips_layer_r_and_says_why"],
    },
    "LM11": {
        "what": "K4 on for a SQLite studio.toml (S2 reports not_run)",
        "killed_by": "PS-S2 (PI3)",
        "edits": [(RU, "(server := go_rules.server_dialect(root, package))", '(server := go_rules.server_dialect(root, package) or "sqlite")')],
        "probes": ["PS-S2"],
        "tests": [f"{T_RULES}::test_k4_a_sqlite_studio_toml_does_not_skip"],
    },
    "LM12": {
        "what": "K5 off (one-value choices are back)",
        "killed_by": "P0-CHOICES2; M-I2",
        "edits": [(GR, "if len(set(choices)) < 2:  # K5: one value is not a state set", "if False:  # K5: one value is not a state set")],
        "probes": ["P0-CHOICES2", "M-I2"],
        "tests": [f"{T_RULES}::test_k5_one_constant_is_not_a_state_set_and_two_are"],
    },
    "LM13": {
        "what": "K5 needs three values",
        "killed_by": "P0-CHOICES2 (S2 and goparity3 lose their choices)",
        "edits": [(GR, "if len(set(choices)) < 2:  # K5: one value is not a state set", "if len(set(choices)) < 3:  # K5: one value is not a state set")],
        "probes": ["P0-CHOICES2"],
        "tests": [f"{T_RULES}::test_k5_keeps_the_states_of_goginapp_and_gotickets"],
    },
    "LM14": {
        "what": "K6 off (tables matched by schema name only, none dropped)",
        "killed_by": "M-P1",
        "edits": [(D, "same, dropped = _identity(base, stated)", "same, dropped = set(), []")],
        "probes": ["M-P1"],
        "tests": [f"{T_DERIVE}::test_a_derived_table_whose_sql_name_is_a_declared_tables_is_that_table"],
    },
    "LM15": {
        "what": "K6 drops an undeclared table that carries an owner",
        "killed_by": "unit undeclared_owned_table_is_kept",
        "edits": [(D, "facts = (base.owners, base.owner_via, base.private, base.machines)", "facts = (base.private, base.machines)")],
        "probes": [],
        "tests": [f"{T_DERIVE}::test_undeclared_owned_table_is_kept"],
    },
    "LM16": {"what": "K8 pairs a raise to its comparison by line proximity (needs B2's tree)", "killed_by": "P2-805A (goparity3 decoy)", "edits": None, "probes": [], "tests": []},
    "LM17": {"what": "K8 derives a status from a disagreeing module (needs B2's tree)", "killed_by": "unit disagreeing_raises_are_unclear", "edits": None, "probes": [], "tests": []},
    "LM18": {
        "what": "K7 off in the runner (the import root is not put on sys.path: the bare-module crash returns)",
        "killed_by": "PS-PI4-3 and PS-PR3-3 on S3's bare harness module (the prereg's T0-CTL is Tier 0's)",
        "edits": [("dogfood/go_parity_s3r.py", "    if str(root) not in sys.path:\n        sys.path.insert(0, str(root))\n", "    if False and str(root) not in sys.path:\n        sys.path.insert(0, str(root))\n")],
        "probes": ["PS-PI4-3", "PS-PR3-3"],
        "tests": [],
    },
}  # fmt: skip

# round 2's mutants re-applied: the same edits, the G3 detectors
REAPPLIED: dict[str, dict[str, Any]] = {}
for _id, _probes in {"JM17": ["P0-REG"], "JM18": ["P0-REG"], "JM19": ["P0-REG", "PS-S2"], "JM22": ["P0-REG"], "JM24": ["P0-REG"],
                     "JM26": ["P0-CHOICES2"], "JM27": ["P0-CHOICES2"], "JM28": ["P0-REG"], "JM29": ["P0-REG"]}.items():  # fmt: skip
    _spec = M2.NEW[_id]
    REAPPLIED[_id] = {"what": _spec["what"], "killed_by": "G3 " + "; ".join(_probes) + " (round 2: " + _spec["killed_by"] + ")",
                      "edits": _spec["edits"], "probes": _probes, "tests": []}  # fmt: skip

# program-level mutants, judged by the probe rows that make them (a row that passes detected its mutant)
DEV_ROWS = {
    "ST1": ("PS-JM31", True), "ST2": ("PS-ST2", True), "ST3": ("PS-ST3", True),
    "SM1": ("M-SM1", True), "SM2": ("M-SM2", True), "SM3": ("M-SM3", True), "SM4": ("M-SM4", False),
    "JSM1": ("J-SM1", False), "JSM2": ("J-SM2", False),
}  # fmt: skip
APP_REAPPLIED = {"JM5": "PS-S2", "JM32": None, "JM33": None}
RUNNER_MUTANTS = ("RM1", "RM2")
LIB_IDS = [f"LM{i}" for i in range(1, 19)]
DEFAULT_IDS = [*LIB_IDS, *RUNNER_MUTANTS, *DEV_ROWS, *REAPPLIED, *APP_REAPPLIED]
CODE = "import sys; sys.path.insert(0, 'dogfood'); import go_parity_probes3 as G; sys.exit(G.rows_cli(sys.argv[1:]))"


def make_tree(dest: Path) -> Path:
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    for d in TREE_DIRS:
        shutil.copytree(REPO / d, dest / d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copyfile(REPO / "pyproject.toml", dest / "pyproject.toml")
    (dest / "docs/results").mkdir(parents=True)
    shutil.copyfile(G.PREREG, dest / "docs/results" / G.PREREG.name)
    return dest


def apply(tree: Path, edits: list[tuple[str, str, str]]) -> None:
    """Each edit must match exactly once: a mutant whose edit does not apply is NOT RUN, never guessed."""
    for rel, old, new in edits:
        f = tree / rel
        text = f.read_text()
        if text.count(old) != 1:
            raise ValueError(f"{rel}: the edit matches {text.count(old)} times: {old[:70]!r}")
        f.write_text(text.replace(old, new))


def run_rows(
    tree: Path, ids: list[str], base: Path, scratch: Path, out: Path, extra: list[str]
) -> dict[str, Any]:
    """The G3 rows ``ids`` of the runner in ``tree`` (a subprocess); {row id: pass}."""
    env = dict(os.environ, PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1", GOTOOLCHAIN="auto")
    for k in ("LATTICE_GOAPP_BIN", "LATTICE_GOGINAPP_BIN", "LATTICE_GOTICKETS_BIN"):
        env.pop(k, None)
    out.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-c",
        CODE,
        "--base",
        str(base),
        "--scratch",
        str(scratch),
        "--out",
        str(out),
        "--ids",
        ",".join(ids),
        "--runs",
        "2",
        "--no-docker",
        *extra,
    ]
    proc = subprocess.run(cmd, cwd=tree, env=env, capture_output=True, text=True, check=False)
    f = out / "rows.json"
    if not f.exists():
        return {"error": (proc.stdout + proc.stderr)[-600:]}
    return {r["id"]: r["pass"] for r in json.loads(f.read_text())}


def run_tests(tree: Path, ids: list[str]) -> dict[str, Any]:
    if not ids:
        return {}
    env = dict(os.environ, PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider", *ids],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    got: dict[str, Any] = {}
    for ln in proc.stdout.splitlines():
        m = re.match(r"(PASSED|FAILED|ERROR) (\S+)", ln)
        if m:
            got[m.group(2)] = m.group(1) == "PASSED"
    return {i: got.get(i, False) for i in ids}


def detectors(
    tree: Path, base: Path, spec: dict[str, Any], scratch: Path, out: Path, extra: list[str]
) -> dict[str, Any]:
    d: dict[str, Any] = {}
    if spec["probes"]:
        d["probes"] = run_rows(tree, spec["probes"], base, scratch, out, extra)
    d["tests"] = run_tests(tree, spec["tests"])
    return d


def flat(d: dict[str, Any]) -> dict[str, Any]:
    out = {f"probe:{k}": v for k, v in d.get("probes", {}).items()}
    out.update({f"test:{k.split('::')[-1]}": v for k, v in d.get("tests", {}).items()})
    return out


# ---- the runner mutants ------------------------------------------------------------------------------------


def runner_mutant(mid: str) -> dict[str, Any]:
    """RM1: a runner that reports a row NOT RUN with every prerequisite present must fail the self-test.
    RM2: a self-hash check that skips one entry must fail the freeze test."""
    if mid == "RM1":
        ctx = G.Ctx(Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp()))
        ctx.streak_src = ctx.jobsched_src = (
            REPO / "tests/spec/goapp"
        )  # any directory with a go.mod stands in
        (ctx.streak_src / "go.mod").is_file() or (_ for _ in ()).throw(
            RuntimeError("no go.mod to stand in")
        )
        ctx.h_target = {"label": "x"}
        ctx.docker, ctx.driver_ok = True, True
        rows = [
            {"id": "P0-WRAP", "pass": None},
            {"id": "M-A0", "pass": None},
            {"id": "H-PI1[S2]", "pass": None},
            {"id": "M-R1", "pass": None},
        ]
        problems = G.self_test_problems(rows, ctx)
        killed = len(problems) == len(rows)
        return {"killed": killed, "problems": problems}
    tmp = Path(tempfile.mkdtemp())
    entries = []
    for rel in G.MUST_LIST:
        f = REPO / rel
        if f.is_file():
            entries.append((rel, G.sha(f)))
    this = (REPO / G.RUNNER_REL).resolve()
    out = {}
    for name, drop in (
        ("skips the running script's entry", G.RUNNER_REL),
        ("skips a library file's entry", "lattice/gocheck/src/reach.go"),
        ("skips the freeze entry of this runner's mutants file", G.MUTANTS_REL),
    ):
        fp = tmp / f"{uuid.uuid4().hex}.sha256"
        fp.write_text("".join(f"{h}  {rel}\n" for rel, h in entries if rel != drop))
        try:
            G.verify_freeze(fp, this)
            out[name] = "NOT caught"
        except S3R.FreezeError as err:
            out[name] = f"caught: {str(err)[:80]}"
    # a hash edited in an otherwise complete list
    fp = tmp / "edited.sha256"
    fp.write_text(
        "".join(
            f"{'0' * 64 if rel == 'lattice/gocheck/src/reach.go' else h}  {rel}\n"
            for rel, h in entries
        )
    )
    try:
        G.verify_freeze(fp, this)
        out["one edited hash"] = "NOT caught"
    except S3R.FreezeError as err:
        out["one edited hash"] = f"caught: {str(err)[:80]}"
    return {"killed": all(v.startswith("caught") for v in out.values()), "checks": out}


# ---- main ----------------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", required=True, help="an existing `git archive 978b468`")
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--freeze",
        required=True,
        help="the freeze file: every entry is verified first, this script included",
    )
    ap.add_argument("--streak", default="")
    ap.add_argument("--jobsched", default="")
    ap.add_argument("ids", nargs="*", default=DEFAULT_IDS)
    args = ap.parse_args(argv)
    this = Path(__file__).resolve()
    freeze = G.verify_freeze(Path(args.freeze).resolve(), this)  # before anything, and not optional
    lattice_file = G.lattice_file_inside()
    scratch, out, base = (
        Path(args.scratch).resolve(),
        Path(args.out).resolve(),
        Path(args.base).resolve(),
    )
    scratch.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    extra: list[str] = []
    if args.streak:
        extra += ["--streak", str(Path(args.streak).resolve())]
    if args.jobsched:
        extra += ["--jobsched", str(Path(args.jobsched).resolve())]
    run_id = uuid.uuid4().hex[:12]
    base_entry = {"run_id": run_id, "runner": "go_parity_mutants3", "runner_sha256": G.sha(this), "freeze_sha256": G.sha(Path(args.freeze)), "arm": "G3:mutants",
                  "kind": "mutants", "out": str(out), "lattice_file": lattice_file}  # fmt: skip
    S3R.append_ledger({"ts": G.now(), "status": "started", **base_entry}, G.LEDGER)
    k8 = json.loads(K8_EDITS.read_text()) if K8_EDITS.is_file() else {}
    for mid, spec in k8.items():
        if mid in LM:
            LM[mid].update(
                edits=[tuple(e) for e in spec["edits"]],
                probes=spec.get("probes", []),
                tests=spec.get("tests", []),
            )
    ids = [i for i in args.ids if i in DEFAULT_IDS]
    rows: list[dict[str, Any]] = []
    lib = {**LM, **REAPPLIED}

    # ---- the control: every detector on an unmutated copy of the tree ----
    t0 = time.perf_counter()
    lib_ids = [i for i in ids if i in lib and lib[i]["edits"]]
    probe_ids = {p for i in lib_ids for p in lib[i]["probes"]}
    probe_ids |= {DEV_ROWS[i][0] for i in ids if i in DEV_ROWS}
    probe_ids |= {"PS-S2"} if "JM5" in ids else set()
    all_probes = sorted(probe_ids)
    all_tests = sorted({t for i in lib_ids for t in lib[i]["tests"]})
    ctl_tree = make_tree(scratch / "tree-control")
    ctl = detectors(
        ctl_tree,
        base,
        {"probes": all_probes, "tests": all_tests},
        scratch / "ctl",
        out / "control",
        extra,
    )
    control = flat(ctl)
    bad_tests = [k for k, v in control.items() if k.startswith("test:") and v is not True]
    ctl_row = {"id": "control", "detectors": control, "tests_failing_on_control": bad_tests,
               "probes_not_passing_on_control": sorted(k for k, v in control.items() if k.startswith("probe:") and v is not True),
               "passes": not bad_tests and "error" not in ctl.get("probes", {}), "wall_s": round(time.perf_counter() - t0, 1)}  # fmt: skip
    print(
        "control:", json.dumps({k: v for k, v in ctl_row.items() if k != "detectors"}), flush=True
    )
    rows.append(ctl_row)

    def finish(code: int = 0) -> int:
        mut = [r for r in rows if r["id"] != "control"]
        killed = [r["id"] for r in mut if r.get("killed")]
        nr = [r["id"] for r in mut if r.get("killed") is None]
        survived = [r["id"] for r in mut if r.get("killed") is False]
        doc = {"runner": "go_parity_mutants3", "runner_sha256": G.sha(this), "freeze": freeze, "lattice_file": lattice_file, "rows": rows,
               "killed": killed, "survived": survived, "not_run": nr, "total": len(mut)}  # fmt: skip
        (out / "mutants3.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")
        S3R.append_ledger({"ts": G.now(), "status": "completed", **base_entry}, G.LEDGER)
        print(f"killed {len(killed)}/{len(mut)}; survived {survived}; not run {nr}")
        return code

    if not ctl_row["passes"]:
        print("the control does not pass: no mutant is judged", file=sys.stderr)
        return finish(1)

    for mid in ids:
        r: dict[str, Any] = {"id": mid}
        try:
            if mid in RUNNER_MUTANTS:
                r.update(
                    mutant=(
                        "the runner reports an arm NOT RUN with every prerequisite present"
                        if mid == "RM1"
                        else "the self-hash check skips an entry"
                    ),
                    **runner_mutant(mid),
                )
            elif mid in lib:
                spec = lib[mid]
                r["mutant"], r["killed_by"] = spec["what"], spec["killed_by"]
                if not spec["edits"]:
                    r.update(
                        killed=None,
                        not_run="K8 is builder B2's (lattice/gocheck/notowned.py) and its edits come from dogfood/g3_standin/k8_mutants.json: neither is in this tree",
                    )
                else:
                    tree = make_tree(scratch / f"tree-{mid.lower()}")
                    apply(tree, spec["edits"])
                    got = flat(
                        detectors(tree, base, spec, scratch / mid.lower(), out / mid.lower(), extra)
                    )
                    flips = {
                        k: [control.get(k), v]
                        for k, v in got.items()
                        if control.get(k) is True and v is not True
                    }
                    r.update(detectors=got, flipped=flips, killed=bool(flips))
                    shutil.rmtree(tree, ignore_errors=True)
            elif mid in DEV_ROWS:
                row_id, gated = DEV_ROWS[mid]
                got = ctl["probes"].get(row_id)
                r.update(mutant=f"the program mutant judged by row {row_id}", killed=bool(got) if got is not None else None, detector=row_id, gated=gated,
                         reading="the row applies its mutant to a scratch copy of the program and reports whether the checker saw it; a gated=False mutant is reported, not gated")  # fmt: skip
            elif mid == "JM5":
                rows_json = json.loads((out / "control" / "rows.json").read_text())
                pa3 = next(x for x in rows_json if x["id"] == "PS-S2")["evidence"]["sub_rows"][
                    "PA3"
                ]["pass"]
                r.update(
                    mutant="goginapp copy: cancel drops AND user_id = ? (detector: PS-S2's PA3 sub-row, GO801 on POST /runs/:rid/cancel)",
                    killed=bool(pa3),
                    detector="PS-S2 / PA3",
                )
            else:  # JM32, JM33: gotickets copies, judged by round 2's detectors on the G3 machinery
                r.update(s3_mutant(mid, scratch, out, extra, base))
        except Exception as err:  # noqa: BLE001 -- a mutant that cannot run is reported, not guessed
            r.update(
                killed=None if isinstance(err, ValueError) else False,
                not_run=f"{type(err).__name__}: {err}"[:500],
            )
        print(
            f"{mid}: killed={r.get('killed')} {r.get('flipped', r.get('detector', r.get('not_run', '')))}",
            flush=True,
        )
        rows.append(r)
    return finish()


def s3_mutant(mid: str, scratch: Path, out: Path, extra: list[str], base: Path) -> dict[str, Any]:
    """JM32 (password_hash loses json:"-": GO802 on GET /me) and JM33 (get answers 403: the 404 item of GET /tickets/{tid}
    refuted), on scratch copies of S3 in this tree, with round 2's detectors and K7 (the import root on sys.path)."""
    ctx = G.Ctx(scratch / f"s3-{mid.lower()}", out / mid.lower())
    ctx.base_dir = base
    G.setup(ctx)
    imp = ctx.scratch / "imp"
    t = S3R.import_cli(ctx, "tests/spec", imp)
    if t["rc"] != 0:
        raise RuntimeError(f"the S3 import failed: {t['stderr'][-300:]}")
    cand = P1.load_candidate(imp)
    copy = S3M.mutate_fixture(ctx, mid)
    if mid == "JM32":
        a = S3M.layer_a_new(cand, ctx.fx, copy)
        want = S3M.PA3_EXPECT[mid]
        hit = any((x["code"], x["key"]) == want for x in a["new"])
        return {
            "mutant": S3M.MUTANTS[mid]["what"],
            "killed": hit and not a["outside"],
            "detector": "layer A (GO802 on GET /me)",
            "new_findings": [f"{x['code']} {x['key']}" for x in a["new"]],
        }
    binary = ctx.scratch / "mut" / mid.lower() / "fxbin"
    ok, err, _ = P1.go_build(copy, binary)
    if not ok:
        raise RuntimeError(f"the mutant does not build: {err[-300:]}")

    def r_map(bin_: Path) -> dict[str, str]:
        keep = os.environ.get(ctx.fx_env)
        os.environ[ctx.fx_env] = str(bin_)
        try:
            return P1.verdict_map(S3R.r_on_spec(ctx, cand.SPEC, "seed", G.SPEC_DIR, mid.lower()))
        finally:
            if keep is None:
                os.environ.pop(ctx.fx_env, None)
            else:
                os.environ[ctx.fx_env] = keep

    control, mutant = r_map(ctx.fx_bin), r_map(binary)  # type: ignore[arg-type]
    hit = [
        i
        for i in control
        if "GET /tickets/{tid}" in i
        and "404" in i
        and control[i] in P1.VERIFIED
        and mutant.get(i) == "refuted"
    ]
    return {
        "mutant": S3M.MUTANTS[mid]["what"],
        "killed": bool(hit),
        "detector": "layer R: the 404 item of GET /tickets/{tid}",
        "refuted_after_verified": hit,
    }


if __name__ == "__main__":
    raise SystemExit(main())
