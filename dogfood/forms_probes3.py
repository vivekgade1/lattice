# ruff: noqa: E501
"""SCORER of the typed-conditions spike, round 3 (docs/results/2026-10-18-typed-conditions-round3-prereg.md, sections 3, 5, D3).

    uv run python dogfood/forms_probes3.py --stage dev --scratch DIR --out DIR
    uv run python dogfood/forms_probes3.py --stage h2 --shapes DIR --manifest FILE --out DIR --scratch DIR      # PD2, post hoc
    uv run python dogfood/forms_probes3.py --stage round1 --shapes DIR --manifest FILE --out DIR --scratch DIR  # PD1, post hoc
    uv run python dogfood/forms_probes3.py --stage frozen --shapes DIR --manifest FILE --freeze FILE --runner FILE --scratch DIR --out DIR

This file is the *scorer* (D3): every metric, count, pass rule and re-derivation lives here. The *reader*
(``dogfood/forms_reader3.py``) only turns a set's files into records. Both and ``dogfood/forms_mutants3.py``
are hashed into ``runner3.sha256``; the frozen stage refuses to run unless the three hash files match the
files on disk, and checks them again after the run (PH).

Decisions written here, before any gated run (disclosed in the results):

* **Whole-set generation.** ``generate`` is called on the whole set, as ``forms generate`` does (round 2
  generated form by form, which hides every sibling-dependent derivation, Amendment 2 (c)). A form that raises
  on its own is recorded and left out, and the rest are generated together.
* **K7** (Amendment 2 (b)): a cell whose rule's form generated a ``db_unchecked:`` note, where the truth
  says violated and G passes. Reported with K6, outside the PG1t gate; PG1 has no K7 exemption.
* **PG2 layer-D agreement**: among cells where both G and H fail and H includes layer D, the fraction where G
  includes D too.
* **PT2e**: class e recall (found / contradictory) >= 0.75 and at least one pair whose ``path_or_query_bound``
  flag is true, or that holds a bound on ``path.x`` / ``query.x``, found.
* **PX arm**: the wall time of the whole frozen stage (scoring, PA, PW, PG3, PG4) <= 240 s; the scoring part
  alone is reported beside it.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def _load(name: str, rel: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


P1 = _load("forms_probes_r1", "dogfood/forms_probes.py")  # the frozen round-1 runner: helpers only
RD = _load("forms_reader3", "dogfood/forms_reader3.py")  # the reader
FIX1 = REPO / "tests/spec/fixtures/forms/minishop"
FIX2 = REPO / "tests/spec/fixtures/forms_r2/minishop"
FIX3 = REPO / "tests/spec/fixtures/forms_r3/minishop"
IMPACT = "tests/spec/fixtures/impact"
DATA = REPO / "docs/results/data/2026-10-18-typed-conditions-round3"
BASE3 = "3679ab2"  # round 3 starts here (prereg section 1)
ROUND1_BASE = "d7ba7de"
H3_MANIFEST_FILE_SHA = (
    "1d0b8256344a8ac67345de519abf62c76ddb4846c755ce2ef10194fafbec3d98"  # Amendment 2
)
ARM_LIMIT_S = 240
sha256_file, row, run, snapshot = P1.sha256_file, P1.row, P1.run, P1.snapshot
NOT_EXPR_NODE_CODES = ("F001", "F017")

# the frozen files of prereg section 7 (sha256 at 3679ab2)
FROZEN_HASHES = {
    "lattice/spec/impact_report.py": "aa29aede06cce15ea74d32aebb964b71dcbdee5d1f70e02a012ac50028558a41",
    "lattice/spec/impact_tier2.py": "d0e95f902b1ba1f6435a8ffe2624912706815222c46964e687bbfedd063dc8a0",
    "lattice/spec/__main__.py": "94542f34058145783670ca8cf95e83e6016bcc8aa03688b9af259d28af141c24",
    "lattice/spec/model.py": "a7f7b3f834f22a89004f6bfb69484f6c4ff35caf86a32f4254c6a1182c9ec9ce",
    "lattice/spec/check.py": "68f13b288cde4a3fa348412495ba560815e003b342ea9cca90b1a779c1bb525e",
    "lattice/spec/invariants.py": "c3f374756d0b4186bea4df00c1f910ed2942b8bfcb771a8df472793d48354a73",
    "lattice/spec/lint.py": "0462853748f3c0101db4f00633c708e98f209fde526fb7eb6563c665f087b442",
    "lattice/spec/preconditions.py": "f1d8abee4f8d92b26fac557081967757736a7f3c8d0aaaa806134b8136595687",
    "lattice/spec/rules.py": "e3c0bc041595de280abae7c9beebbab64f09e0fd55182e173ee623c4dfddb248",
    "lattice/spec/incremental.py": "29ded6ecbbddcc55a5d94914cf9bce82fb9ee61a10d3453d70c336565c6090e6",
    "lattice/spec/rationale.py": "ea0f05dd47a27934d0e6d5d6e20d0964c1449f7667966e8c5c4e495de9c434e9",
    "lattice/spec/impact_tier3.py": "22228513beba44fcff4b0d5ab48c662d2010e7ac320b73ac860f134ffb6e2faf",
    "lattice/spec/validate.py": "97691c98b66a49f656315dea00225e1465861e99533e5886b10718be1427b012",
    "tests/spec/minishop.py": "90117594e10c61272b8896bfc631623c5d23c4689d8e1853649cf73810baccfd",
    "dogfood/forms_probes.py": "7449106201f93f6108addcd557f54ef353ccbb53322c05a3b4bda04eba3525c0",
    "dogfood/forms_mutants.py": "7b9483ff29f80b91164c594e0312beb50fc0e4b1ba5409cd83360967e2240597",
    "dogfood/forms_probes2.py": "9e3ec0adf13f85c019c16d282072c7ad7b5d204009f8f20a37b8a80b92f4ff4c",
    "dogfood/forms_mutants2.py": "45889ed95d69b292e6a285a2af533d5a33b1c0bed53f1ad000b7aa61d583a2f1",
}
FROZEN_SETS = {
    "lattice/check": "ca52b5a0076a04328c97c1cdafdd50af1a557ac671795697f9ec448146ba57bf",
    "lattice/gocheck": "e9515e859e4aebff1020d8fffa721e8859396b9545d4d7b2b768d4980a367c3f",
    "tests/spec/fixtures/impact": "291eab2058ca5c86079b73287d1feff42e97afcd9a53f2358322df7b75473d1b",
    "tests/spec/fixtures/forms": "ee956fc0472296d5eeb58b743c79bcd8014249032a9699d63ec4a6cd6e5cd0be",
    "tests/spec/fixtures/forms_r2": "b3dafdc48c59c2cc424da2f4655050b27b952ffb33b400d3f6b0702e4957a3a6",
}
# round-2 numbers on H2 (prereg section 1.1, run 3) and on the round-1 set, shown beside PD2 / PD1
ROUND2_ON_H2 = {
    "PG1": "668 / 672 cells G == H; 4 cells G != H with H == T",
    "PG1t": "572 / 576 outside K6; K6 96 cells, 0 misses",
    "PG1o": "exact",
    "PG1k": "G 20, H 22; H-only 2",
    "PG2": "99 / 223 same layer set (44%)",
    "PG5": "0 invalid accepted, 0 valid refused",
    "PT2f": "precision 1.0; recall a-d 11/11; class e 2/4",
    "PT2x": "20 items, 0 failed, 2 weak",
    "PT2w": "1 / 1, tier 2",
    "PNX": "10 / 12 (N12, N13 F004 only)",
}
ROUND2_ON_ROUND1 = {  # round 2's PD1 (prereg section 5.1: 397/400, precision 17/18)
    "PG1t": "397 / 400",
    "PT2f": "precision 17/18",
}


def set_sha(rel: str) -> str:
    """The registered set hash: ``git ls-files -z DIR | sort -z | xargs -0 sha256sum | sha256sum``."""
    files = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z", rel], capture_output=True, check=True
    ).stdout.split(b"\0")
    lines = sorted(f.decode() for f in files if f)
    out = "".join(f"{sha256_file(REPO / f)}  {f}\n" for f in lines)
    return hashlib.sha256(out.encode()).hexdigest()


def frozen_check() -> dict[str, Any]:
    bad = [rel for rel, want in FROZEN_HASHES.items() if sha256_file(REPO / rel) != want]
    sets = {
        d: {"got": (g := set_sha(d)), "want": w, "match": g == w} for d, w in FROZEN_SETS.items()
    }
    return {
        "files_changed": bad,
        "sets": sets,
        "ok": not bad and all(v["match"] for v in sets.values()),
    }


def hash_lines(path: Path) -> list[tuple[str, str]]:
    return [
        (ln.partition("  ")[0], ln.partition("  ")[2].strip())
        for ln in path.read_text().splitlines()
        if ln.strip()
    ]


def check_hash_file(path: Path | None) -> list[str]:
    """The mismatches of a ``sha256sum``-format file against the working tree (paths relative to the repo)."""
    if path is None or not path.is_file():
        return [f"{path}: missing"]
    bad = []
    for digest, rel in hash_lines(path):
        f = REPO / rel
        if not f.is_file() or sha256_file(f) != digest:
            bad.append(rel)
    return bad


def ph_row(
    freeze: Path | None, runner: Path | None, manifest: Path | None, shapes: Path | None, when: str
) -> dict[str, Any]:
    """PH: freeze3, runner3 and shapes3 checked (the manifest file's own sha, the set's file hashes)."""
    detail: dict[str, Any] = {
        "freeze3_mismatch": check_hash_file(freeze),
        "runner3_mismatch": check_hash_file(runner),
    }
    if manifest is not None and manifest.is_file():
        detail["manifest_file_sha"] = sha256_file(manifest)
        detail["manifest_file_sha_expected"] = H3_MANIFEST_FILE_SHA
        detail["manifest_file_sha_ok"] = sha256_file(manifest) == H3_MANIFEST_FILE_SHA
        if shapes is not None and shapes.is_dir():
            ok, why = P1.manifest_ok(shapes, manifest)
            detail["set_files_ok"] = ok
            detail["set_files_detail"] = why
    ok = not detail["freeze3_mismatch"] and not detail["runner3_mismatch"]
    ok = ok and detail.get("manifest_file_sha_ok", True) and detail.get("set_files_ok", True)
    return row("PH", f"hash discipline ({when})", "PASS" if ok else "FAIL", **detail)


# ---- imports of the library under test (after sys.path) -----------------------------------------


def lib() -> Any:
    from lattice.spec import forms as F
    from lattice.spec import forms_tier2 as T2
    from lattice.spec import impact_tier2
    from tests.spec import minishop as M

    return F, T2, impact_tier2, M


def mk(F: Any, kind: str, form: dict[str, Any], fid: str = "F90", rule: str = "R6") -> Any:
    return F.Form(fid, rule, "0" * 64, kind, form, "approved", "hand", "t", F.form_sha(kind, form))


# ---- dev: PRC, PC3, PR1, PR2, PB, PH ------------------------------------------------------------


def prc() -> list[dict[str, Any]]:
    """The RC report exists, names each residual, and was written before the first library edit
    (mtimes: rc.json older than forms.json's edited state is not provable; the log in the results is)."""
    p = DATA / "rc.json"
    if not p.is_file():
        return [row("PRC", "RC report before the first library edit", "FAIL", reason="no rc.json")]
    d = json.loads(p.read_text())
    res = d.get("residuals", {})
    missing = [k for k in "bcde" if k not in res]
    unaddressed = [k for k, v in res.items() if v.get("hypothesis_confirmed") is None]
    ok = not missing and not unaddressed
    return [
        row("PRC", "RC report exists, before code; every residual has a cause or 'not found' with hypotheses ruled out",
            "PASS" if ok else "FAIL", residuals=sorted(res), missing=missing, residuals_without_a_cause=unaddressed,
            written_before_first_library_edit=d.get("written_before_first_library_edit"),
            provenance=d.get("provenance"), mtime_rc=p.stat().st_mtime_ns)
    ]  # fmt: skip


def _witness_form(
    F: Any, M: Any, kind: str, form: dict[str, Any], spec: Any = None, fid: str = "F90"
) -> Any:
    f = mk(F, kind, form, fid)
    return F.generate(spec or M.SPEC, [f])


def pc3() -> list[dict[str, Any]]:
    """One dev case per round-3 change: path bound, query bound, nested route, outcome db values, b, c."""
    F, _T2, _it, M = lib()
    from lattice.spec.rules import Trace

    spec = M.SPEC
    trace = Trace.load(FIX3 / "spec.trace.json")
    fs = F.load_set(trace, F.forms_paths(FIX3 / "spec.trace.json"))
    assert fs is not None
    gen = F.generate(spec, fs.generating())
    sc = {s.name: s for s in gen.scenarios}
    step = lambda n: sc[n].steps[0]  # noqa: E731
    cases: dict[str, dict[str, Any]] = {}

    # a1: path-placeholder bound ge 1: at 1, beyond 0, the other parts of the witness kept
    at, be = step("form:F1:at_bound"), step("form:F1:beyond")
    cases["PC3-path"] = {
        "pass": (at.path, at.expect_status) == ("/orders/1", 200)
        and (be.path, be.expect_status) == ("/orders/0", 404)
        and be.expect_db is not None
        and not any(n.startswith("F1:") for n in gen.notes),
        "at": [at.path, at.expect_status], "beyond": [be.path, be.expect_status, be.expect_db],
    }  # fmt: skip
    # a2: query-key bound
    qa, qb = step("form:F2:at_bound"), step("form:F2:beyond")
    cases["PC3-query"] = {
        "pass": (qa.path, qa.params) == ("/orders", {"limit": 1}) and (qb.path, qb.params) == ("/orders", {"limit": 0}),
        "at": [qa.path, qa.params], "beyond": [qb.path, qb.params],
    }  # fmt: skip
    # a3: a nested route /orders/{order_id}/items/{item_id}, the bound on the SECOND placeholder
    src = '@endpoint("GET", "/orders/{order_id}/items/{item_id}", auth=Authenticated)\ndef read_item(order_id: Int, item_id: Int, user: CurrentUser) -> One:\n    pass\n'
    nested = dataclasses.replace(spec, programs={**spec.programs, "nested": src})
    nform = {
        "field": {"param": "GET /orders/{order_id}/items/{item_id}", "path": "path.item_id"},
        "cond": {"node": "Cmp", "op": "ge", "value": 1}, "caller": "ann",
        "request": {"path": "/orders/5/items/7"}, "refused": {"status": 404}, "accepted": {"status": 200},
    }  # fmt: skip
    ng = _witness_form(F, M, "bound", nform, nested)
    nsc = {s.name: s.steps[0] for s in ng.scenarios}
    cases["PC3-nested"] = {
        "pass": F.check_forms(nested, None, [mk(F, "bound", nform)]) == []
        and nsc["form:F90:at_bound"].path == "/orders/5/items/1"
        and nsc["form:F90:beyond"].path == "/orders/5/items/0",
        "paths": [nsc["form:F90:at_bound"].path, nsc["form:F90:beyond"].path],
    }  # fmt: skip
    # a4: a path value the type cannot hold is F021 at check, never a run-time raise
    empty = dict(nform, cond={"node": "Len", "op": "min_len", "value": 0})
    got = sorted({d.code for d in F.check_forms(nested, None, [mk(F, "bound", empty)])})
    cases["PC3-f021"] = {"pass": got == ["F021"], "codes": got}

    # d: outcome db.equals values: node name -> F017, other non-literal -> F001, a Lit stays well typed
    def odb(v: Any, where: str = "accepted") -> list[str]:
        form = dict(F1_FORM, **{where: {"status": 200, "db": {"equals": {"total": v}}}})
        return sorted({d.code for d in F.check_forms(spec, None, [mk(F, "bound", form)])})

    cases["PC3-outcome-values"] = {
        "pass": odb({"param": "GET /orders/{order_id}", "path": "path.order_id"}) == ["F017"]
        and odb({"sub": [1]}) == ["F017"] and odb([1, 2]) == ["F001"] and odb(1.5) == [],
        "param": odb({"param": "x", "path": "path.y"}), "list": odb([1, 2]), "refused_list": odb([1, 2], "refused"),
    }  # fmt: skip
    # b: a refusal whose db cannot be asserted (no given, no column in the path) is noted, not dropped
    notes = [n for n in gen.notes if n.startswith("F3: db_unchecked:")]
    cases["PC3-db-unchecked"] = {
        "pass": sorted(notes) == ["F3: db_unchecked:anon_post_orders_order_id_cancel", "F3: db_unchecked:other_post_orders_order_id_cancel"],
        "notes": notes,
    }  # fmt: skip
    # c: counted rows of a row_limit are pairwise distinct on a column a sibling row_limit counts by
    f4, f5 = sc["form:F4:at_limit"].given["OrderItem"], sc["form:F5:at_limit"].given["OrderItem"]
    cases["PC3-sibling-limits"] = {
        "pass": len({r["product_id"] for r in f4}) == len(f4) and len({r["order_id"] for r in f5}) == len(f5)
        and all(r["order_id"] == 20 for r in f4) and all(r["product_id"] == 1 for r in f5),
        "f4_product_ids": [r["product_id"] for r in f4], "f5_order_ids": [r["order_id"] for r in f5],
    }  # fmt: skip
    ok = all(c["pass"] for c in cases.values())
    return [row("PC3", "one dev case per round-3 change (fixtures/forms_r3 + inline)", "PASS" if ok else "FAIL",
                passed=f"{sum(c['pass'] for c in cases.values())}/{len(cases)}", cases=cases)]  # fmt: skip


F1_FORM: dict[str, Any] = {
    "field": {"param": "GET /orders/{order_id}", "path": "path.order_id"},
    "cond": {"node": "Cmp", "op": "ge", "value": 1}, "caller": "ann",
    "request": {"path": "/orders/1"}, "refused": {"status": 404},
}  # fmt: skip


def _git_tree(scratch: Path, rev: str, tag: str) -> Path:
    d = scratch / f"tree_{tag}"
    if not d.exists():
        d.mkdir(parents=True)
        tar = subprocess.run(
            ["git", "-C", str(REPO), "archive", rev], capture_output=True, check=True
        )
        subprocess.run(["tar", "-x", "-C", str(d)], input=tar.stdout, check=True)
    return d


def _pq_forms(fix: Path) -> set[str]:
    """Ids of the fixture's forms whose bound is on ``path.x`` / ``query.x`` (the cases that may differ)."""
    data = json.loads(next(fix.glob("*.forms.json")).read_text())
    return {
        e["id"] for e in data["forms"]
        if e["kind"] == "bound" and re.match(r"(path|query)\.", str(e["form"].get("field", {}).get("path", "")))
    }  # fmt: skip


def pr1(scratch: Path) -> list[dict[str, Any]]:
    """`forms generate` bytes on the round-1 and round-2 fixtures vs 3679ab2: only path/query-bound cases differ."""
    orig = _git_tree(scratch, BASE3, "base")
    out = []
    names = lambda d: {x["name"]: x for x in d.get("scenarios", [])}  # noqa: E731
    for tag, fix in (("r1", FIX1), ("r2", FIX2)):
        argv = [
            sys.executable,
            "-m",
            "lattice.spec",
            "forms",
            "generate",
            "--trace",
            str(fix / "spec.trace.json"),
            "--spec",
            "tests.spec.minishop:SPEC",
        ]
        res = {
            t: run(argv, tree, {"PYTHONPATH": str(tree), "PYTHONHASHSEED": "0"})
            for t, tree in (("old", orig), ("new", REPO))
        }
        byte_same = res["old"]["out"] == res["new"]["out"] and res["old"]["rc"] == res["new"]["rc"]
        if res["old"]["rc"] or res["new"]["rc"]:
            out.append(
                row(
                    f"PR1[{tag}]",
                    "generate bytes vs 3679ab2",
                    "FAIL",
                    rc=[res["old"]["rc"], res["new"]["rc"]],
                    err=res["new"]["err"][-300:],
                )
            )
            continue
        a, b = (json.loads(res[t]["out"]) for t in ("old", "new"))
        na, nb = names(a), names(b)
        pq = _pq_forms(fix)
        diff = sorted(n for n in set(na) | set(nb) if na.get(n) != nb.get(n))
        diff_pq = [n for n in diff if n.split(":")[1] in pq]
        diff_other = [n for n in diff if n.split(":")[1] not in pq]
        rest_same = (
            a.get("invariants") == b.get("invariants")
            and a.get("links") == b.get("links")
            and a.get("no_invariant") == b.get("no_invariant")
        )
        added_notes = [n for n in b.get("notes", []) if n not in a.get("notes", [])]
        gone_notes = [n for n in a.get("notes", []) if n not in b.get("notes", [])]
        ruled_notes = [n for n in added_notes if "db_unchecked:" in n]
        other_notes = [n for n in added_notes if "db_unchecked:" not in n] + gone_notes
        ok = not diff_other and rest_same and not added_notes and not gone_notes
        out.append(row(f"PR1[{tag}]", "forms generate bytes vs 3679ab2: only path/query-bound cases differ", "PASS" if ok else "FAIL",
                       bytes_identical=byte_same, differing_cases_path_query_bound=diff_pq, differing_cases_other=diff_other,
                       invariants_links_no_invariant_identical=rest_same, notes_added_db_unchecked_ruled_by_amendment_2=ruled_notes,
                       notes_added_or_gone_other=other_notes))  # fmt: skip
    return out


def pr2(scratch: Path) -> list[dict[str, Any]]:
    """The frozen round-2 runner (9e3ec0ad) --stage dev under the round-3 library: as round 2."""
    sub = scratch / "pr2"
    sub.mkdir(parents=True, exist_ok=True)
    if sha256_file(REPO / "dogfood/forms_probes2.py") != FROZEN_HASHES["dogfood/forms_probes2.py"]:
        return [
            row(
                "PR2",
                "round-2 runner --stage dev",
                "FAIL",
                reason="forms_probes2.py is not the frozen file",
            )
        ]
    p = run(
        [
            sys.executable,
            str(REPO / "dogfood/forms_probes2.py"),
            "--stage",
            "dev",
            "--scratch",
            str(sub / "s"),
            "--out",
            str(sub / "o"),
        ],
        REPO,
        {"PYTHONPATH": str(REPO)},
        timeout=3000,
    )
    try:
        rows = json.loads((sub / "o" / "dev.json").read_text())
    except (OSError, ValueError):
        return [row("PR2", "round-2 runner --stage dev", "FAIL", rc=p["rc"], err=p["err"][-400:])]
    by = {r["probe"]: r["status"] for r in rows}
    not_pass = {k: v for k, v in by.items() if v not in ("PASS", "REPORTED", "NOT RUN")}
    return [
        row(
            "PR2",
            "round-2 runner --stage dev: as round 2",
            "PASS" if not not_pass else "FAIL",
            rc=p["rc"],
            rows=by,
            not_pass=not_pass,
        )
    ]


def pb(scratch: Path, ids_before: Path | None, ids_after: Path | None) -> list[dict[str, Any]]:
    out = []
    for label, base in (("PB", BASE3), ("PB-d7ba7de", ROUND1_BASE)):
        sub = scratch / label
        sub.mkdir(parents=True, exist_ok=True)
        P1.BASE, P1.FROZEN_HASHES, P1.FROZEN_SETS = base, {}, {}
        r = P1.pb(sub)[0]
        fc = frozen_check()
        for c in r[
            "commands"
        ]:  # a wall-clock line on stderr ("impact: 0.1s") is a registered timing line
            lines = c.get("diff_lines", [])
            if (
                not c["identical"]
                and lines
                and all(re.fullmatch(r"[\w -]+: [\d.]+s", x) for x in lines)
            ):
                c["identical"], c["timing_stderr_only"] = True, lines
        cmd_bad = [c["cmd"] for c in r["commands"] if not c["identical"]]
        r["probe"] = label
        r["what"] = (
            f"byte-compat against git archive {base} (no forms file anywhere); frozen hashes and sets"
        )
        r["frozen_check"] = fc
        r["differing_commands"] = cmd_bad
        ids_same = None
        if (
            label == "PB"
            and ids_before is not None
            and ids_after is not None
            and ids_before.is_file()
            and ids_after.is_file()
        ):
            ids_same = ids_before.read_text() == ids_after.read_text()
            r["pytest_failing_ids_identical"] = ids_same
        r["status"] = (
            "PASS"
            if not cmd_bad and fc["ok"] and ids_same is not False
            else ("FAIL" if label == "PB" else "REPORTED")
        )
        (
            r.pop("frozen_sets", None),
            r.pop("frozen_files_changed", None),
            r.pop("git_diff_empty", None),
        )
        out.append(r)
    return out


def _with_fix(fix: Path, fn: Any, scratch: Path, tag: str) -> list[dict[str, Any]]:
    P1.FIX = fix
    sub = scratch / tag
    sub.mkdir(parents=True, exist_ok=True)
    if not (sub / "apps").exists():
        shutil.copytree(scratch / "PB" / "apps", sub / "apps")
    rows = fn(sub)
    for r in rows:
        r["fixture"] = fix.parent.name + "/" + fix.name
        r["probe"] = f"{r['probe']}[{tag}]"
    return rows


def dev(scratch: Path, ids_before: Path | None, ids_after: Path | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    steps: list[Any] = [
        lambda s: prc(), lambda s: pc3(), pr1, pr2, lambda s: pb(s, ids_before, ids_after),
        lambda s: _with_fix(FIX1, P1.pw, s, "r1") + _with_fix(FIX2, P1.pw, s, "r2") + _with_fix(FIX3, P1.pw, s, "r3"),
        lambda s: _with_fix(FIX1, P1.pa, s, "r1"),
        lambda s: _with_fix(FIX1, P1.pg3, s, "r1") + _with_fix(FIX2, P1.pg3, s, "r2") + _with_fix(FIX3, P1.pg3, s, "r3"),
        lambda s: _with_fix(FIX1, P1.pg4_px, s, "r1") + _with_fix(FIX2, P1.pg4_px, s, "r2") + _with_fix(FIX3, P1.pg4_px, s, "r3"),
    ]  # fmt: skip
    for fn in steps:
        t = time.perf_counter()
        rows += fn(scratch)
        print(
            f"{getattr(fn, '__name__', 'step')}: {time.perf_counter() - t:.1f} s", file=sys.stderr
        )
    rows.append(
        ph_row(
            DATA / "freeze3.sha256", DATA / "runner3.sha256", None, None, "dev, files as they stand"
        )
    )
    return rows


# ---- the set stages -------------------------------------------------------------------------------


def check_set(shapes: Path, manifest: Path | None, layout: str) -> str | None:
    if manifest is None or not manifest.is_file():
        return "no committed manifest to check the set against"
    if layout == "h3" and sha256_file(manifest) != H3_MANIFEST_FILE_SHA:
        return "the committed manifest file's sha256 is not the one recorded in Amendment 2"
    ok, detail = P1.manifest_ok(shapes, manifest)
    return None if ok else f"manifest check failed: {detail}"


def _load_set_module(label: str, rec: dict[str, Any]) -> Any:
    ms_spec = importlib.util.spec_from_file_location(f"hshapes_{label}", rec["handwritten"])
    assert ms_spec is not None and ms_spec.loader is not None
    mod = importlib.util.module_from_spec(ms_spec)
    ms_spec.loader.exec_module(mod)
    return mod


def _trace_of(mod: Any, rec: dict[str, Any]) -> Any:
    from lattice.spec.rules import Trace

    t = getattr(mod, "TRACE", None)
    return t if t is not None else Trace.load(rec["trace_json"])


def generate_all(F: Any, spec: Any, resolved: list[Any]) -> tuple[Any, dict[str, str], str | None]:
    """The whole set generated at once; a form that raises on its own is recorded and left out."""
    errors: dict[str, str] = {}
    keep = list(resolved)
    whole_error: str | None = None
    try:
        return F.generate(spec, keep), errors, None
    except Exception as err:  # noqa: BLE001 -- a raise on a form that passed check is itself a finding
        whole_error = f"{type(err).__name__}: {err}"
    for r in list(keep):
        try:
            F.generate(spec, [r])
        except Exception as err:  # noqa: BLE001
            errors[r.form.id] = f"{type(err).__name__}: {err}"
            keep.remove(r)
    return F.generate(spec, keep), errors, whole_error


def is_pq(entry: dict[str, Any]) -> bool:
    f = entry["form"].get("field", {}) if isinstance(entry.get("form"), dict) else {}
    return entry["kind"] == "bound" and bool(re.match(r"(path|query)\.", str(f.get("path", ""))))


def set_rows(
    shapes: Path, scratch: Path, label: str, layout: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """PG1, PG1t, PG1o, PG1k, PG2, PG5, PG5c, PT2f, PT2e, PT2x, PT2w, PNX (the scoring part) on the set."""
    F, T2, impact_tier2, M = lib()
    from lattice.spec.check import check
    from lattice.spec.model import Scenario, Step

    rec = RD.read(shapes, layout)
    mod = _load_set_module(label, rec)
    spec, trace = mod.SPEC, _trace_of(mod, rec)
    forms = tuple(F._parse_form(e, i) for i, e in enumerate(rec["forms"]))  # noqa: SLF001
    resolved_all = F.resolve(trace, forms)
    resolved = F.usable(resolved_all)
    states = {r.form.id: r.state for r in resolved_all}
    gen, errors, whole_error = generate_all(F, spec, resolved)
    diags = F.check_forms(spec, trace, list(forms))
    eff = F.effective_spec(spec, gen)
    truth: dict[str, dict[str, bool]] = {}
    for rid, app, v in rec["truth"]:
        truth.setdefault(rid, {})[app] = v
    root = scratch / "apps"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    apps = [(n.replace("sec:", "sec_", 1), v) for n, v in P1.apps_for(root, M)]
    for mu in rec["mutants"]:
        mf, old, new = mu["edit"]
        apps.append((mu["name"], M.write_app(root, mu["name"], edit=(mf, old, new))))
    form_rule = {r.form.id: r.rule for r in resolved if r.form.id not in errors}
    k6_rules = {
        r.rule for r in resolved
        if r.form.kind == "bound" and "endpoint" not in r.form.form and "column" in r.form.form["field"]
    }  # fmt: skip
    k7_rules = {
        form_rule[m.group(1)] for n in gen.notes
        if (m := re.match(r"(F\d+): db_unchecked:", n)) and m.group(1) in form_rule
    }  # fmt: skip
    links = {t: rid for rid, ts in trace.links.items() for t in ts}
    inv_by_sentence = {i.sentence: i.name for i in spec.invariants}
    inv_names = {i.name for i in spec.invariants}

    def rule_of(text: str) -> tuple[bool, str | None]:
        m = re.search(r"form:(F\d+):", text) or re.search(r"\(form (F\d+)\)", text)
        if m:
            return True, form_rule.get(m.group(1))
        if text.startswith("scenario: "):
            return False, links.get("scenario:" + text[len("scenario: ") :])
        if text in inv_names:
            return False, links.get("invariant:" + text)
        return False, links.get("invariant:" + inv_by_sentence.get(text, ""))

    cells = []
    t_run = time.perf_counter()
    for name, (_d, pkg) in apps:
        rep = check(eff, M.app_factory(pkg), M.build_db, layers="CD", login=M.login)
        gfail: dict[str, set[str]] = {}
        hfail: dict[str, set[str]] = {}
        unattributed = []
        for f in rep.findings:
            is_gen, rid = rule_of(str(f.rule))
            if rid:
                (gfail if is_gen else hfail).setdefault(rid, set()).add(f.layer)
            elif is_gen:
                unattributed.append(str(f.rule))
        for rid in sorted(set(form_rule.values()), key=lambda r: int(r[1:])):
            cells.append({"rule": rid, "app": name, "G": rid in gfail, "H": rid in hfail, "T": truth.get(rid, {}).get(name),
                          "k6": rid in k6_rules, "k7": rid in k7_rules and bool(truth.get(rid, {}).get(name)) and rid not in gfail,
                          "Glayers": sorted(gfail.get(rid, ())), "Hlayers": sorted(hfail.get(rid, ())),
                          "unattributed": unattributed})  # fmt: skip
    run_wall = round(time.perf_counter() - t_run, 2)
    scored = [c for c in cells if c["T"] is not None]
    pg1_bad = [c for c in cells if c["G"] != c["H"] and c["H"] == c["T"]]
    pg1_diff = [c for c in cells if c["G"] != c["H"]]
    pg1t_bad = [c for c in scored if c["G"] != c["T"]]
    pg1t_bad_out = [c for c in pg1t_bad if not c["k6"] and not c["k7"]]
    ok_cells = [c for c in cells if c["app"] == "ok"]
    ok_bad = [c for c in ok_cells if c["G"] != bool(c["T"])]
    ok_g = {c["rule"] for c in ok_cells if c["G"]}
    ok_h = {c["rule"] for c in ok_cells if c["H"]}
    g_k = {c["app"] for c in cells if c["app"] != "ok" and c["G"] and c["rule"] not in ok_g}
    h_k = {c["app"] for c in cells if c["app"] != "ok" and c["H"] and c["rule"] not in ok_h}
    both = [c for c in cells if c["G"] and c["H"]]
    h_d = [c for c in both if "D" in c["Hlayers"]]

    def slim(cs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{k: c[k] for k in ("rule", "app", "G", "H", "T", "k6", "k7")} for c in cs]

    k6_cells = [c for c in scored if c["k6"]]
    out = [
        row("PG1", "G vs H per cell", "PASS" if not pg1_bad else "FAIL", cells=len(cells), apps=len(apps),
            formed_rules=len(set(form_rule.values())), forms_usable=len(resolved), form_states=states,
            generate_whole_set_error=whole_error, forms_that_could_not_generate=errors,
            g_eq_h=sum(1 for c in cells if c["G"] == c["H"]), g_ne_h=slim(pg1_diff),
            bad_gne_h_and_h_eq_t=slim(pg1_bad), notes=list(gen.notes), run_wall_s=run_wall,
            unattributed_generated_findings=sorted({u for c in cells for u in c["unattributed"]})),
        row("PG1t", "G vs truth per cell (K6 and K7 cells apart)", "PASS" if not pg1t_bad_out else "FAIL", scored=len(scored),
            g_eq_t=sum(1 for c in scored if c["G"] == c["T"]), bad_outside_k6_k7=slim(pg1t_bad_out),
            k6_cells=len(k6_cells), k6_misses=slim([c for c in k6_cells if c["G"] != c["T"]]), k6_rules=sorted(k6_rules),
            k7_rules=sorted(k7_rules), k7_cells=slim([c for c in scored if c["k7"]])),
        row("PG1o", "on ok, G fails exactly the rules T marks violated", "PASS" if not ok_bad else "FAIL",
            g_fail_on_ok=sorted(ok_g), t_violated_on_ok=sorted(c["rule"] for c in ok_cells if c["T"]), bad=slim(ok_bad)),
        row("PG1k", "G kills (flips vs ok, formed rules) a superset of H's", "PASS" if h_k <= g_k else "FAIL",
            g_kills=sorted(g_k), h_kills=sorted(h_k), g_only=sorted(g_k - h_k), h_only=sorted(h_k - g_k), apps_non_ok=len(apps) - 1),
        row("PG2", "layer of the failing check where both fail; layer-D agreement", "REPORTED", cells_both_fail=len(both),
            same_layer_sets=sum(1 for c in both if c["Glayers"] == c["Hlayers"]),
            h_includes_d=len(h_d), both_include_d=sum(1 for c in h_d if "D" in c["Glayers"]),
            layer_d_agreement=(sum(1 for c in h_d if "D" in c["Glayers"]) / len(h_d)) if h_d else None,
            diff=[{k: c[k] for k in ("rule", "app", "Glayers", "Hlayers")} for c in both if c["Glayers"] != c["Hlayers"]]),
    ]  # fmt: skip

    # PG5 / PG5c
    wrong, accepted, exact = [], [], []
    for x in rec["invalid"]:
        fs = [F._parse_form(e, 0) for e in x["entries"]]  # noqa: SLF001
        if x["mode"] == "append":
            fs = [*forms, *fs]
        sp = spec
        if x["extra_scenarios"]:
            extra = (
                Scenario(s["name"], s.get("given"), tuple(Step(**t) for t in s["steps"]))
                for s in x["extra_scenarios"]
            )
            sp = dataclasses.replace(spec, scenarios=(*spec.scenarios, *extra))
        got = sorted({d.code for d in F.check_forms(sp, trace, fs)})
        if not got:
            accepted.append(x["id"])
        elif x["code"] in got:
            exact.append(x["id"])
        else:
            wrong.append(
                {
                    "id": x["id"],
                    "want": x["code"],
                    "got": got,
                    "also_acceptable_hit": bool({x["code"], *x["also"]} & set(got)),
                }
            )
    valid_refused = [d.render() for d in diags]
    out += [
        row("PG5", "invalid.json refused; the set's valid forms accepted", "PASS" if not accepted and not valid_refused else "FAIL",
            invalid=len(rec["invalid"]), accepted_invalid=accepted, valid_refused=valid_refused, valid_forms=len(forms)),
        row("PG5c", "exact F-code on invalid.json", "REPORTED", exact=len(exact), of=len(rec["invalid"]), codes_not_exact=wrong),
    ]  # fmt: skip

    # PT2f / PT2e / PT2x
    hand = {f"scenario:{sc.name}" for sc in spec.scenarios}
    res = []
    for p in rec["pairs"]:
        fs = [F._parse_form(e, 0) for e in p["entries"]]  # noqa: SLF001
        named = set(p["named"])
        extra = [
            Scenario(s["name"], s.get("given"), tuple(Step(**t) for t in s["steps"]))
            for s in p["extra"]
        ]
        bad_named = sorted(
            n for n in named if n not in hand and n not in {f"scenario:{e.name}" for e in extra}
        )
        if bad_named:
            raise ValueError(f"pair {p['id']}: no hand-written {bad_named!r}")
        sp = dataclasses.replace(spec, scenarios=(*spec.scenarios, *extra)) if extra else spec
        if len({f.id for f in fs}) < len(fs):
            fs[1] = dataclasses.replace(fs[1], id="F9999")
        err, notes = None, []
        out_of_pair = 0
        try:
            items = T2.contradictions(sp, fs, notes)
            allowed = {f.id for f in fs} | named | {f"scenario:{x.name}" for x in extra}
            kept = [i for i in items if all(x in allowed for x in i["forms"])]
            out_of_pair = len(items) - len(kept)
            items = kept
        except Exception as e:  # noqa: BLE001
            items, err = [], f"{type(e).__name__}: {e}"
        pq = bool(p["path_or_query_bound"]) or any(is_pq(e) for e in p["entries"])
        expect = {"contradictory": "contradict"}.get(p["expect"], p["expect"])
        res.append({"id": p["id"], "class": p["class"], "expect": expect, "items": items, "error": err, "notes": notes,
                    "forms": fs, "spec": sp, "named": sorted(named), "witness": p.get("witness"), "pq": bool(pq),
                    "out_of_pair": out_of_pair})  # fmt: skip
    tp = [r for r in res if r["expect"] == "contradict" and r["items"]]
    fn = [r for r in res if r["expect"] == "contradict" and not r["items"]]
    fp = [r for r in res if r["expect"] == "compatible" and r["items"]]
    tn = [r for r in res if r["expect"] == "compatible" and not r["items"]]

    def by_class(cs: str) -> dict[str, Any]:
        sub = [r for r in res if r["class"] in cs]
        return {"contradict": sum(r["expect"] == "contradict" for r in sub),
                "found": sum(r["expect"] == "contradict" and bool(r["items"]) for r in sub),
                "compatible": sum(r["expect"] == "compatible" for r in sub),
                "false_pos": sum(r["expect"] == "compatible" and bool(r["items"]) for r in sub)}  # fmt: skip

    rabcd = by_class("abcd")
    out.append(row("PT2f", "forms contradictions on pairs.json", "PASS" if not fp and rabcd["found"] == rabcd["contradict"] and not any(r["error"] for r in res) else "FAIL",
                   pairs=len(res), true_pos=len(tp), false_neg=[r["id"] for r in fn],
                   false_pos=[{"id": r["id"], "items": [i["id"] for i in r["items"]]} for r in fp], true_neg=len(tn),
                   errors=[{"id": r["id"], "error": r["error"]} for r in res if r["error"]],
                   undecided=[{"id": r["id"], "notes": r["notes"]} for r in res if r["notes"]],
                   items_outside_the_pair=[{"id": r["id"], "n": r["out_of_pair"]} for r in res if r["out_of_pair"]],
                   precision=(len(tp) / (len(tp) + len(fp))) if tp or fp else None,
                   recall_abcd=f"{rabcd['found']}/{rabcd['contradict']}", class_e=by_class("e"),
                   per_class={c: by_class(c) for c in "abcde"}))  # fmt: skip
    ce = by_class("e")
    ce_pq = [r for r in res if r["class"] == "e" and r["expect"] == "contradict" and r["pq"]]
    ce_pq_found = [r["id"] for r in ce_pq if r["items"]]
    ce_recall = (ce["found"] / ce["contradict"]) if ce["contradict"] else None
    out.append(row("PT2e", "class e recall >= 0.75 and >= 1 path/query-bound pair found", "PASS" if ce_recall is not None and ce_recall >= 0.75 and ce_pq_found else "FAIL",
                   class_e=ce, recall=ce_recall, path_or_query_pairs=[r["id"] for r in ce_pq], path_or_query_found=ce_pq_found,
                   class_e_missed=[r["id"] for r in res if r["class"] == "e" and r["expect"] == "contradict" and not r["items"]]))  # fmt: skip

    weak: list[str] = []
    bad_w: list[str] = []
    n_items = 0
    for r in tp:
        for it in r["items"]:
            n_items += 1
            why = rederive(
                F, impact_tier2, r["spec"], {f.id: f for f in r["forms"]}, it, r.get("witness")
            )
            if why == "weak":
                weak.append(it["id"])
            elif why:
                bad_w.append(f"{it['id']}: {why}")
    out.append(row("PT2x", "witness, strong: the runner re-derives the witness and calls incompatible itself", "PASS" if not bad_w else "FAIL",
                   items=n_items, not_rederived_weak=weak, failed_rederivation=bad_w))  # fmt: skip

    # PNX
    acc, wrong_code, tried = [], [], 0
    for x in rec["not_expressible"]:
        a = x["attempt"]
        if not a:
            continue
        tried += 1
        f = F._parse_form(
            {
                "id": "F901",
                "rule": "R1",
                "anchor": "0" * 64,
                "kind": a["kind"],
                "form": a["form"],
                "status": "approved",
                "author": "hand",
            },
            0,
        )  # noqa: SLF001
        got = sorted({d.code for d in F.check_forms(spec, None, [f])})
        if not got:
            acc.append(x["id"])
        elif not set(got) & set(NOT_EXPR_NODE_CODES):
            wrong_code.append({"id": x["id"], "got": got, "set_expects": x["expect"]})
    out.append(row("PNX", "not_expressible attempts refused with F001 or F017", "PASS" if not acc and not wrong_code else "FAIL",
                   rules=len(rec["not_expressible"]), with_attempt=tried, accepted=acc, refused_with_other_code=wrong_code))  # fmt: skip
    out.append(pt2w_set(rec, scratch, spec, trace, forms, label))
    ctx = {
        "rec": rec,
        "spec": spec,
        "trace": trace,
        "forms": forms,
        "resolved": resolved,
        "gen": gen,
        "mod": mod,
        "run_wall": run_wall,
        "errors": errors,
        "whole_error": whole_error,
    }
    return out, ctx


def rederive(
    F: Any,
    impact_tier2: Any,
    spec: Any,
    by_id: dict[str, Any],
    it: dict[str, Any],
    pair_witness: Any = None,
) -> str | None:
    """None when the item's witness re-derives; 'weak' when no independent derivation is done; else the reason."""
    cls, ids, w = it["class"], it["forms"], it["witness"]
    forms = [by_id.get(i) for i in ids]
    if cls == "a" and all(forms) and "value" in w and "holds" not in w and "accepted_by" not in w:
        held = [F.cond_holds(F.norm_cond(f.form["cond"]), w["value"]) for f in forms]
        return None if sum(held) == 1 else "value does not separate"
    if cls == "a" and all(forms) and "value" in w and "holds" in w:
        holder, failer = by_id[w["holds"]], by_id[w["fails"]]
        return (
            None
            if F.cond_holds(F.norm_cond(holder.form["cond"]), w["value"])
            and not F.cond_holds(F.norm_cond(failer.form["cond"]), w["value"])
            else "value does not separate"
        )
    if cls == "a" and "accepted_by" in w:
        return (
            None
            if F.cond_holds(F.norm_cond(by_id[w["accepted_by"]].form["cond"]), w["value"])
            and not F.cond_holds(F.norm_cond(by_id[w["forbidden_by"]].form["cond"]), w["value"])
            else "stored value is not forbidden"
        )
    if cls == "b" and all(forms):
        small, large = sorted(f.form["max"] for f in forms)
        n = w["counted_rows"]
        return None if n == small and small < large else "counted rows do not separate the limits"
    if cls in ("d", "e"):

        def case(name: str) -> Any:
            fid = name.split(":")[1] if name.startswith("form:") else None
            if fid is None or fid not in by_id:
                return None
            g = F.generate(spec, [by_id[fid]])
            return next((s.steps[0] for s in g.scenarios if s.name == name), None)

        pw = pair_witness if isinstance(pair_witness, dict) else {}
        rq = pw.get("request")
        if cls == "e" and isinstance(rq, dict) and {"method", "path"} <= set(rq):
            # the pair's witness is a request (an item of class e may come from a pair of another class)

            def steps_of(i: str) -> list[Any]:
                if i.startswith("scenario:"):
                    return [
                        st for sc in spec.scenarios if f"scenario:{sc.name}" == i for st in sc.steps
                    ]
                if i not in by_id:
                    return []
                return [st for sc in F.generate(spec, [by_id[i]]).scenarios for st in sc.steps]

            def hit(st: Any) -> bool:
                return (st.method, st.path, st.user) == (
                    rq["method"],
                    rq["path"],
                    rq.get("user"),
                ) and ("json" not in rq or st.json == rq["json"])

            sides = [[_ex(st) for st in steps_of(i) if hit(st)] for i in ids]
            if len(sides) != 2 or not all(sides):
                return "no case for the witness request on a side"
            return (
                None
                if any(impact_tier2.incompatible(a, b, spec) for a in sides[0] for b in sides[1])
                else "cases are not incompatible"
            )
        if cls == "e":
            ex = []
            for n in w.get("cases", []):
                st = case(n)
                if st is None:
                    st = next(
                        (s.steps[0] for s in spec.scenarios if f"scenario:{s.name}" == n), None
                    )
                if st is None:
                    return f"cannot find case {n}"
                ex.append(
                    {
                        "status": st.expect_status,
                        "body": st.expect_body,
                        "db": dict(st.expect_db) if st.expect_db else None,
                    }
                )
            return (
                None
                if len(ex) == 2 and impact_tier2.incompatible(ex[0], ex[1], spec)
                else "cases are not incompatible"
            )
        if all(forms) and "state" in w:
            s = w["state"]
            ex = []
            for f in forms:
                g = F.generate(spec, [f])
                nm = f"form:{f.id}:from_{s}" if s in f.form["from"] else f"form:{f.id}:refused_{s}"
                st = next((x.steps[0] for x in g.scenarios if x.name == nm), None)
                if st is None:
                    return f"no case {nm}"
                ex.append(
                    {
                        "status": st.expect_status,
                        "body": None,
                        "db": dict(st.expect_db) if st.expect_db else None,
                    }
                )
            return (
                None if impact_tier2.incompatible(ex[0], ex[1], spec) else "states not incompatible"
            )
    return "weak"


def _ex(st: Any) -> dict[str, Any]:
    return {
        "status": st.expect_status,
        "body": st.expect_body,
        "db": dict(st.expect_db) if st.expect_db else None,
    }


# ---- a project made from a set (the CLI-driven probes) -------------------------------------------------


def make_project(rec: dict[str, Any], trace: Any, dest: Path) -> Path:
    """A minishop project beside the set's files: a wrapper spec.py (the set's handwritten.py is loaded from
    where it lies, so its REQUIREMENTS.md read works), the trace, the requirements and the forms file."""
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    (dest / "spec.py").write_text(
        "import importlib.util\n"
        f"_s = importlib.util.spec_from_file_location('hw', {str(rec['handwritten'])!r})\n"
        "_m = importlib.util.module_from_spec(_s)\n_s.loader.exec_module(_m)\nSPEC = _m.SPEC\n"
    )
    (dest / "spec.trace.json").write_text(trace.to_json())
    (dest / "REQUIREMENTS.md").write_text(rec["requirements"].read_text())
    (dest / "REQUIREMENTS.forms.json").write_text(rec["forms_json"].read_text())
    return dest


def pt2w_set(
    rec: dict[str, Any], scratch: Path, spec: Any, trace: Any, forms: tuple[Any, ...], label: str
) -> dict[str, Any]:
    """PT2w through the CLI: the set's forms as a minishop project; the candidate adds one form that
    contradicts a base form (a bound with the opposite ordering condition)."""
    F, _T2, _it, _M = lib()
    d = scratch / "pt2w_set"
    shutil.rmtree(d, ignore_errors=True)
    make_project(rec, trace, d / "project")
    shutil.copytree(d / "project", d / "candidate")
    flip = {"ge": "lt", "gt": "le", "le": "gt", "lt": "ge"}
    pick = None
    for f in sorted(forms, key=lambda f: (F._fnum(f.id), f.id)):  # noqa: SLF001
        c = F.norm_cond(f.form.get("cond")) if f.kind == "bound" else None
        if (
            isinstance(c, dict)
            and c.get("node") == "Cmp"
            and c.get("op") in flip
            and F.usable(F.resolve(trace, [f]))
        ):
            pick = f
            break
    if pick is None:
        return row(
            "PT2w",
            "impact CLI gains the form items",
            "NOT RUN",
            reason="no bound form with an ordering condition to contradict",
        )
    cond = {**pick.form["cond"], "op": flip[F.norm_cond(pick.form["cond"])["op"]]}
    entry = {**pick.to_dict(), "id": "F9001", "form": {**pick.form, "cond": cond}}
    entry["approved_sha"] = F.form_sha(entry["kind"], entry["form"])
    fp = d / "candidate" / "REQUIREMENTS.forms.json"
    data = json.loads(fp.read_text())
    data["forms"].append(entry)
    fp.write_text(json.dumps(data, indent=2) + "\n")
    return pt2w_run(d / "project", d / "candidate", spec, trace, fp, "PT2w")


def pt2w_run(
    proj: Path, cand: Path, spec: Any, trace: Any, cand_forms: Path, label: str
) -> dict[str, Any]:
    F, T2, _it, _M = lib()
    cmd = [sys.executable, "-m", "lattice.spec", "impact", "--project", str(proj), "--candidate", str(cand),
           "--tiers", "23", "--json", "--no-flows", "--baseline", "none"]  # fmt: skip
    p = run(cmd, REPO, {"PYTHONPATH": str(REPO)})
    try:
        det = json.loads(p["out"])["tier2"]["determined"]
    except (ValueError, KeyError):
        return row(
            label,
            "impact CLI gains the form items",
            "FAIL",
            exit_code=p["rc"],
            stderr_tail=p["err"][-300:],
        )
    got = sorted(
        F.canon([d["class"], d["forms"], d["witness"]])
        for d in det
        if d.get("kind") == "form_contradiction"
    )
    forms = F.load_forms_file(cand_forms).forms
    base_forms = F.load_forms_file(proj / cand_forms.name).forms
    use = lambda fs: F.usable(F.resolve(trace, fs))  # noqa: E731
    mine = {
        F.canon([i["class"], i["forms"], i["witness"]]) for i in T2.contradictions(spec, use(forms))
    }
    had = {
        F.canon([i["class"], i["forms"], i["witness"]])
        for i in T2.contradictions(spec, use(base_forms))
    }
    want = sorted(mine - had)
    tiers = {d.get("tier") for d in det if d.get("kind") == "form_contradiction"}
    ok = bool(want) and got == want and tiers == {2}
    return row(label, "impact CLI: tier2.determined gains exactly the new form items (tier 2)", "PASS" if ok else "FAIL",
               expected_items=len(want), got_items=len(got), tiers=sorted(t for t in tiers if t is not None),
               determined_total=len(det), exit_code=p["rc"])  # fmt: skip


# ---- PA, PW, PG3, PG4, PX on a set -------------------------------------------------------------------


def _spec_args(project: Path) -> list[str]:
    return ["--trace", str(project / "spec.trace.json"), "--spec", str(project / "spec.py")]


def pw_set(ctx: dict[str, Any], scratch: Path) -> dict[str, Any]:
    F, _T2, _it, M = lib()
    rec, spec, trace = ctx["rec"], ctx["spec"], ctx["trace"]
    project = make_project(rec, trace, scratch / "pw_proj")
    shim = scratch / "shim"
    shim.mkdir(exist_ok=True)
    log = scratch / "shim.log"
    log.write_text("")
    (shim / "claude").write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexit 1\n')
    (shim / "claude").chmod(0o755)
    root = scratch / "pw_apps"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir()
    pkg_dir, pkg = M.write_app(root, "ok")
    env = {
        "PYTHONPATH": str(REPO),
        "PATH": f"{shim}:{os.environ.get('PATH', '')}",
        "PYTHONHASHSEED": "0",
    }
    t = str(project / "spec.trace.json")
    cmds = {
        "show": ["show", "--trace", t], "check": ["check", *_spec_args(project)],
        "generate": ["generate", *_spec_args(project)], "contradictions": ["contradictions", *_spec_args(project)],
        "coverage": ["coverage", "--trace", t],
    }  # fmt: skip
    dirs = {"project": project, "app": pkg_dir}
    before = {k: snapshot(v) for k, v in dirs.items()}
    bad_runs = 0
    for _ in range(10):
        for argv in cmds.values():
            bad_runs += (
                run([sys.executable, "-m", "lattice.spec", "forms", *argv], REPO, env)["rc"] != 0
            )
    old_path = os.environ["PATH"]
    os.environ["PATH"] = env["PATH"]
    try:
        rep, gen = F.check_with_forms(
            spec,
            trace,
            F.forms_paths(project / "spec.trace.json"),
            M.app_factory(pkg),
            M.build_db,
            layers="CD",
            login=M.login,
        )
    finally:
        os.environ["PATH"] = old_path
    after = {k: snapshot(v) for k, v in dirs.items()}
    changed = {k: sorted(x for x in after[k] if after[k][x] != before[k].get(x)) for k in dirs}
    new = {k: sorted(set(after[k]) - set(before[k])) for k in dirs}
    calls = len([x for x in log.read_text().splitlines() if x.strip()])
    ok = (
        not any(changed.values())
        and not any(new.values())
        and calls == 0
        and gen is not None
        and len(rep.findings) >= 0
    )
    return row("PW", "snapshots around show/check/generate/contradictions/coverage x10 and a check run with forms, on the set",
               "PASS" if ok else "FAIL", changed=changed, new=new, shim_calls=calls, nonzero_exits=bad_runs,
               generated=len(gen.names()) if gen else 0, note="a non-zero exit of `forms check` / `contradictions` is reported, not gated (they report findings by exit code)")  # fmt: skip


def pa_set(ctx: dict[str, Any], scratch: Path) -> dict[str, Any]:
    F, _T2, _it, M = lib()
    from lattice.spec.rules import Rule, Trace

    rec, spec, trace = ctx["rec"], ctx["spec"], ctx["trace"]
    project = make_project(rec, trace, scratch / "pa_proj")
    paths = F.forms_paths(project / "spec.trace.json")
    fs = F.load_set(trace, paths)
    assert fs is not None
    auth = project / "REQUIREMENTS.forms.json"
    prop = project / "REQUIREMENTS.forms.proposed.json"
    held = auth.read_bytes()
    data = json.loads(held)
    out: dict[str, Any] = {}
    order = {r.id: i for i, r in enumerate(trace.rules)}
    # approve refuses with no terminal and with CLAUDECODE set; nothing is written
    prop_data = json.loads(held)
    for f in prop_data["forms"]:
        f.update(status="proposed", approved_by=None, approved_sha=None)
    prop.write_text(json.dumps(prop_data))
    auth.unlink()
    fid = data["forms"][0]["id"]
    argv = [
        sys.executable,
        "-m",
        "lattice.spec",
        "forms",
        "approve",
        *_spec_args(project),
        "--from",
        str(prop),
        "--form",
        fid,
        "--approver",
        "probe",
    ]
    r1 = run(argv, REPO, {"PYTHONPATH": str(REPO)})
    r2 = run(argv, REPO, {"PYTHONPATH": str(REPO), "CLAUDECODE": "1"})
    out["approve_no_tty"] = r1["rc"] == 2 and "approve_needs_terminal" in r1["err"]
    out["approve_claudecode"] = r2["rc"] == 2 and "approve_needs_terminal" in r2["err"]
    out["nothing_written"] = not auth.exists()
    fs2 = F.load_set(trace, paths)
    out["proposal_generates_nothing"] = fs2 is not None and fs2.generating() == []
    out["no_door_for_proposals"] = F.generated_for(spec, trace, paths) is None
    auth.write_bytes(held)
    prop.unlink()
    # an approved bound form edited by hand reads `edited` and generates nothing
    idx = next(
        i
        for i, e in enumerate(data["forms"])
        if e["kind"] == "bound"
        and isinstance(e["form"].get("cond"), dict)
        and "value" in e["form"]["cond"]
    )
    d2 = json.loads(held)
    v = d2["forms"][idx]["form"]["cond"]["value"]
    d2["forms"][idx]["form"]["cond"]["value"] = (
        v + 1 if isinstance(v, int | float) and not isinstance(v, bool) else f"{v}x"
    )
    auth.write_text(json.dumps(d2))
    fs3 = F.load_set(trace, paths)
    assert fs3 is not None
    states = {r.form.id: r.state for r in fs3.resolved}
    eid = data["forms"][idx]["id"]
    out["edited_state"] = states[eid]
    out["edited_generates_nothing"] = f"form:{eid}:invariant" not in F.generate(
        spec, fs3.generating()
    ).names() and not any(
        n.startswith(f"form:{eid}:") for n in F.generate(spec, fs3.generating()).names()
    )
    auth.write_bytes(held)
    # a rule rewording makes the form stale; moved text keeps it (a form on a rule that is not first)
    k_form = next(e for e in data["forms"] if order.get(e["rule"], 0) >= 1)
    k = order[k_form["rule"]]
    rules = list(trace.rules)
    rules[k] = Rule(rules[k].id, rules[k].text + " (reworded)", rules[k].section, rules[k].line)
    st = {r.form.id: r.state for r in F.resolve(Trace(trace.source, rules), fs.approved)}
    out["reworded_state"] = st[k_form["id"]]
    shuffled = [trace.rules[k], *trace.rules[:k], *trace.rules[k + 1 :]]
    ren = [Rule(f"R{i + 1}", r.text, r.section, r.line) for i, r in enumerate(shuffled)]
    st2 = {r.form.id: (r.state, r.rule) for r in F.resolve(Trace(trace.source, ren), fs.approved)}
    out["moved_state"] = list(st2[k_form["id"]])
    # draft writes only the proposals file (a recording generator; the claude CLI is not called)
    formed = {e["rule"] for e in data["forms"]}
    free = next(r.id for r in trace.rules if r.id not in formed)
    before = {p.name: p.read_bytes() for p in project.iterdir()}

    class Gen:
        def generate(self, system: str, messages: list[Any]) -> Any:
            from types import SimpleNamespace

            body = {
                "kind": "bound",
                "form": {
                    "field": {"column": "Product.stock"},
                    "cond": {"node": "Cmp", "op": "ge", "value": 0},
                },
                "status": "approved",
            }
            return SimpleNamespace(text=json.dumps(body), cost_usd=0.0)

    rep = F.draft_forms(spec, trace, paths, rules=[free], out=prop, generator=Gen(), model="probe")
    after = {p.name: p.read_bytes() for p in project.iterdir()}
    out["draft_new_files"] = sorted(set(after) - set(before))
    out["draft_changed_files"] = sorted(k_ for k_ in before if before[k_] != after.get(k_))
    out["draft_status"] = json.loads(prop.read_text())["forms"][0]["status"]
    out["draft_drafted"] = rep["drafted"]
    ok = (
        out["approve_no_tty"] and out["approve_claudecode"] and out["nothing_written"] and out["proposal_generates_nothing"]
        and out["no_door_for_proposals"] and out["edited_state"] == "edited" and out["edited_generates_nothing"]
        and out["reworded_state"] == "stale" and out["moved_state"] == ["moved", "R1"] and out["draft_new_files"] == [prop.name]
        and out["draft_changed_files"] == [] and out["draft_status"] == "proposed"
    )  # fmt: skip
    return row(
        "PA",
        "approval states, approve refusals, draft, on the set",
        "PASS" if ok else "FAIL",
        **out,
    )


def worker_cd(variant: str, root: Path, project: Path) -> None:
    """One CD run in a fresh process: the canonical findings of G + H on one app, forms read from the project."""
    F, _T2, _it, M = lib()
    from lattice.spec.check import check
    from lattice.spec.lint import _load_spec
    from lattice.spec.rules import Trace

    spec = _load_spec(str(project / "spec.py"))[0]
    trace = Trace.load(project / "spec.trace.json")
    fs = F.load_set(trace, F.forms_paths(project / "spec.trace.json"))
    assert fs is not None
    eff = F.effective_spec(spec, F.generate(spec, fs.generating()))
    root.mkdir(parents=True, exist_ok=True)
    _d, pkg = M.write_app(root, variant)
    d = check(eff, M.app_factory(pkg), M.build_db, layers="CD", login=M.login).to_dict()
    print(
        json.dumps(
            {"verdict": d["verdict"], "counts": d["counts"], "findings": d["findings"]},
            sort_keys=True,
        )
    )


def pg3_set(ctx: dict[str, Any], scratch: Path) -> dict[str, Any]:
    rec, trace = ctx["rec"], ctx["trace"]
    roots = [
        make_project(rec, trace, scratch / "pg3_a"),
        make_project(rec, trace, scratch / "pg3_b"),
    ]
    gens, cds = (
        set(),
        {"ok": set()},
    )  # ok fails the row-limit rules by construction: its report has findings
    rng = random.Random(20261018)
    leaks = []
    for i in range(10):
        proj = roots[i % 2]
        env = {"PYTHONPATH": str(REPO), "PYTHONHASHSEED": str(rng.randrange(1, 2**31))}
        r = run(
            [sys.executable, "-m", "lattice.spec", "forms", "generate", *_spec_args(proj)],
            REPO,
            env,
        )
        gens.add(r["out"] if r["rc"] == 0 else f"ERR {r['err'][-200:]}")
        if re.search(r"/(tmp|home|Users|private)/|\d{4}-\d\d-\d\dT", r["out"]):
            leaks.append(i)
        for variant in cds:
            w = run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--worker",
                    "cd",
                    "--variant",
                    variant,
                    "--root",
                    str(scratch / f"pg3_apps_{i}"),
                    "--project",
                    str(proj),
                ],
                REPO,
                env,
            )
            cds[variant].add(w["out"] if w["rc"] == 0 else f"ERR {w['err'][-200:]}")
    ok = (
        len(gens) == 1
        and all(len(v) == 1 for v in cds.values())
        and not leaks
        and not any(g.startswith("ERR") for g in gens)
    )
    return row("PG3", "forms generate x10 and CD reports x10 on the set, random PYTHONHASHSEED, two roots", "PASS" if ok else "FAIL",
               generate_distinct=len(gens), cd_distinct={k: len(v) for k, v in cds.items()}, path_or_time_leaks=leaks)  # fmt: skip


def pg4_px_set(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    F, _T2, _it, _M = lib()
    spec, resolved, gen = ctx["spec"], ctx["resolved"], ctx["gen"]
    problems = F.self_check(spec, gen)
    ts = []
    for _ in range(50):
        t0 = time.perf_counter()
        generate_all(F, spec, resolved)
        ts.append(time.perf_counter() - t0)
    gmed = sorted(ts)[len(ts) // 2]
    raised = bool(ctx["errors"]) or ctx["whole_error"] is not None
    return [
        row("PG4", "generated invariants compile (sqlite, mysql); scenarios validate; 0 L011/L012; no raise", "PASS" if not problems and not raised else "FAIL",
            invariants=len(gen.invariants), scenarios=len(gen.scenarios), problems=problems, raises=ctx["errors"], whole_set_error=ctx["whole_error"]),
        row("PX-generate", "generate on the set (median of 50)", "PASS" if gmed <= 1.0 else "FAIL", median_s=round(gmed, 5), gate="<= 1 s"),
    ]  # fmt: skip


# ---- stages -----------------------------------------------------------------------------------------------


def run_set(
    shapes: Path, scratch: Path, label: str, layout: str, extras: bool
) -> list[dict[str, Any]]:
    t0 = time.perf_counter()
    try:
        rows, ctx = set_rows(shapes, scratch, label, layout)
    except Exception as err:  # noqa: BLE001 -- an unrecognised layout is NOT RUN (a reader fix, disclosed, D3)
        names = (
            "PG1",
            "PG1t",
            "PG1o",
            "PG1k",
            "PG2",
            "PG5",
            "PG5c",
            "PT2f",
            "PT2e",
            "PT2x",
            "PNX",
            "PT2w",
            "PA",
            "PW",
            "PG3",
            "PG4",
        )
        import traceback

        return [
            row(
                n,
                "needs the set",
                "NOT RUN",
                reason=f"unrecognised layout: {type(err).__name__}: {err}",
                trace=traceback.format_exc()[-700:],
            )
            for n in names
        ]
    score_s = round(time.perf_counter() - t0, 1)
    if extras:
        for fn in (pa_set, pw_set, pg3_set):
            t = time.perf_counter()
            rows.append(fn(ctx, scratch))
            print(f"{fn.__name__}: {time.perf_counter() - t:.1f} s", file=sys.stderr)
        rows += pg4_px_set(ctx)
    wall = round(time.perf_counter() - t0, 1)
    rows.append(
        row(
            "PX",
            "wall time of the whole arm (scoring, PA, PW, PG3, PG4)",
            "PASS" if wall <= ARM_LIMIT_S else "FAIL",
            seconds=wall,
            scoring_only_s=score_s,
            gate=f"<= {ARM_LIMIT_S} s",
        )
    )
    return rows


def stage_set(
    stage: str,
    shapes: Path | None,
    manifest: Path | None,
    freeze: Path | None,
    runner: Path | None,
    scratch: Path,
) -> list[dict[str, Any]]:
    layout = {"frozen": "h3", "h2": "h2", "round1": "r1"}[stage]
    if shapes is None or not shapes.is_dir():
        return [row("set", "needs the set", "NOT RUN", reason="the set is not present")]
    why = check_set(shapes, manifest, layout)
    if why is not None:
        raise SystemExit(why)
    if stage == "frozen":
        pre = ph_row(freeze, runner, manifest, shapes, "before the scored run")
        if pre["status"] != "PASS":
            raise SystemExit(
                "PH before the run failed: "
                + json.dumps({k: v for k, v in pre.items() if k.endswith(("mismatch", "ok"))})
            )
    rows = run_set(shapes, scratch, stage, layout, extras=True)
    if stage == "frozen":
        rows.append(ph_row(freeze, runner, manifest, shapes, "after the scored run"))
    else:
        beside = ROUND2_ON_H2 if stage == "h2" else ROUND2_ON_ROUND1
        for r in rows:
            r["post_hoc"] = True
            r["round2_value"] = beside.get(r["probe"], "n/a (not run in round 2 on this set)")
            r["status_gate"] = r["status"]
            r["status"] = "REPORTED" if r["status"] != "NOT RUN" else "NOT RUN"
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--stage", choices=("dev", "h2", "round1", "frozen"))
    ap.add_argument("--scratch", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--shapes", type=Path)
    ap.add_argument("--manifest", type=Path)
    ap.add_argument("--freeze", type=Path)
    ap.add_argument("--runner", type=Path)
    ap.add_argument("--pytest-ids-before", type=Path)
    ap.add_argument("--pytest-ids-after", type=Path)
    ap.add_argument("--worker")
    ap.add_argument("--variant", default="ok")
    ap.add_argument("--root", type=Path)
    ap.add_argument("--project", type=Path)
    args = ap.parse_args(argv)
    if args.worker == "cd":
        worker_cd(args.variant, args.root, args.project)
        return 0
    if not args.stage or not args.scratch or not args.out:
        ap.error("--stage, --scratch and --out are required")
    if args.stage == "frozen" and (
        args.freeze is None or args.manifest is None or args.runner is None
    ):
        ap.error("--stage frozen needs --manifest, --freeze and --runner")
    args.scratch.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    if args.stage == "dev":
        rows = dev(args.scratch, args.pytest_ids_before, args.pytest_ids_after)
    else:
        rows = stage_set(
            args.stage, args.shapes, args.manifest, args.freeze, args.runner, args.scratch
        )
    rows.append(row("wall", "total", "REPORTED", seconds=round(time.perf_counter() - t0, 1)))
    (args.out / f"{args.stage}.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True, default=str) + "\n"
    )
    for r in rows:
        print(f"{r['probe']}: {r['status']}" + (f" ({r['reason']})" if "reason" in r else ""))
    return 0 if all(r["status"] in ("PASS", "NOT RUN", "REPORTED") for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
