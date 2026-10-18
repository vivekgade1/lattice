"""Probes of the Go parity gaps, round 2 (docs/results/2026-10-18-go-parity-gaps-round2-prereg.md
with Amendment 1; Spike J, node J2). Arm 0 (static), S1, S2 (both development data, still gated),
S3 (blind; NOT RUN until the isolated author delivers ``tests/spec/gotickets``) and the
cross-arm rows. Docker-free; arm M is NOT RUN.

The pass rules are the pre-registration's section 5. Where a rule is ambiguous this file fixes one
reading *before* any scored run and says so in the row's ``reading``; the results list every such
reading as a deviation. The round-1 runner (``go_parity_probes.py``, frozen) supplies the helpers
and the S1 rows that are unchanged.

    "$PY" dogfood/go_parity_probes2.py --arm 0,S1,S2,S3 --base "$SCRATCH/base" \\
        --freeze docs/results/data/2026-10-18-go-parity-gaps-round2/freeze.sha256 --runs 10 \\
        --scratch "$SCRATCH/p" --out docs/results/data/2026-10-18-go-parity-gaps-round2/scored

``--only PI1,PA1`` restricts the rows (the mutants runner uses it). Nothing here edits a fixture or
a target directory: imports write to the scratch, the Go apps are built to the scratch, and
recording ``docker`` / ``claude`` shims come first on ``PATH``.
"""  # noqa: E501

# ruff: noqa: E501, UP031
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import go_parity_probes as P  # noqa: E402  (the round-1 runner: helpers and unchanged rows)

REPO = P.REPO
PREREG = REPO / "docs/results/2026-10-18-go-parity-gaps-round2-prereg.md"
SPEC_DIR, GOAPP, GOGINAPP, GOEXTRACT = P.SPEC_DIR, P.GOAPP, P.GOGINAPP, P.GOEXTRACT
GOTICKETS = SPEC_DIR / "gotickets"
FIX = REPO / "tests/gocheck/fixtures"
P2, P2MIXED = FIX / "goparity2", FIX / "goparity2mixed"

VERIFIED = P.VERIFIED
ARMS = ("0", "S1", "S2", "S3")
S3_WHY = (
    "NOT RUN: tests/spec/gotickets is written by a separate isolated author after the library "
    "freeze (Amendment 1, Q2); it has not been delivered"
)

# the 8 module dirs of PB-X: J1's 7 (goapp is the 7th) plus one more fixture present at 193f7f6
PB_X_DIRS = [*P.PB_X_DIRS, "tests/gocheck/fixtures/runs"]

T_S2_ROUTES = {
    ("GET", "/health"): "Public",
    ("POST", "/login"): "Public",
    ("POST", "/configs/{cid}/runs"): "Customer",
    ("GET", "/runs"): "Customer",
    ("GET", "/runs/{rid}"): "Customer",
    ("POST", "/runs/{rid}/cancel"): "Customer",
    ("GET", "/configs"): "Customer",
}

ALL_IDS = [
    "P0-PARSE", "P0-OMIT", "P0-BIND", "P0-LIBMW", "P0-CHOICES", "P0-PRIV", "PA4", "PG805",
    "PR1", "PR2", "PR4", "PB1", "PB1-CLI", "PB2", "PB3",
    "PI1", "PI2", "PI3", "PI4", "PI5", "PI6", "PI7", "PR3", "PR-T2", "PA1", "PA2", "PA3",
    "PI1-3", "PI2-3", "PI3-3", "PI4-3", "PI5-3", "PI6-3", "PI7-3", "PR3-3", "PA1-3", "PA3-3",
    "PW", "PX", "PB-F", "PB-X", "PB-G", "PB-P", "PB-P2", "PB-H", "PB-T", "PB-D",
]  # fmt: skip
assert len(ALL_IDS) == 47

result, not_run, sha = P.result, P.not_run, P.sha


# ---- helpers -------------------------------------------------------------------------------------


def front(root: Path, package: str = ".") -> Any:
    """The Go front end with the round-1 and round-2 inferences applied, after one extract."""
    from lattice.importer import go_rules
    from lattice.importer.frontends.go_gin import GoGinFrontEnd

    fe = go_rules.InferringFrontEnd(GoGinFrontEnd())
    fe.model = fe.extract(root, package)
    return fe


def pol(root: Path) -> dict[str, Any]:
    from lattice.gocheck.derive import policy_extract

    return policy_extract(root)


def route_map(extract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f"{r['method']} {r['path_template']}": r for r in extract["routes"]}


def parsed_params(fe: Any) -> list[tuple[str, str]]:
    """(route, param) of every path parameter the model types Int."""
    return [(f"{r.method} {r.path}", p.name) for r in fe.model.routes for p in r.params
            if p.source == "path" and p.type == "Int"]  # fmt: skip


def with_s3(row: dict[str, Any]) -> dict[str, Any]:
    row["s3_part"] = "NOT RUN (gotickets not delivered)"
    return row


# ---- arm 0 ---------------------------------------------------------------------------------------


def probe_p0_parse(ctx: P.Ctx) -> dict[str, Any]:
    x = route_map(pol(P2))
    got2 = {k: [p.get("parse") for p in r["params"]] for k, r in x.items() if k.startswith("GET /")
            and k[5] in "abc"}  # fmt: skip
    fe = front(SPEC_DIR, "goginapp")
    s2 = sorted(parsed_params(fe))
    inf = [i for i in fe.inferences if i["rule"] == "path_parse"]
    ok = (got2 == {"GET /a/{id}": ["Int"], "GET /b/{id}": [None], "GET /c/{id}": [None]}
          and s2 == [("GET /runs/{rid}", "rid"), ("POST /configs/{cid}/runs", "cid"),
                     ("POST /runs/{rid}/cancel", "rid")] and len(inf) == 3)  # fmt: skip
    return with_s3(
        result(
            "P0-PARSE",
            "E1 on goparity2, and S2 (extract facts and the model)",
            {"goparity2": "a: Int; b, c: no key", "S2": "cid, rid x2", "S3": "qid, tid x4"},
            {"goparity2": got2, "S2_model_Int": s2, "S2_inferences": len(inf)},
            ok,
        )
    )


def probe_p0_omit(ctx: P.Ctx) -> dict[str, Any]:
    x = route_map(pol(P2))
    got = {k: (x[k]["params"][0]["ge"], x[k]["params"][0]["le"], len(x[k]["unread"]))
           for k in ("GET /n1", "GET /n2", "GET /n3")}  # fmt: skip
    lim = route_map(pol(GOGINAPP))["GET /runs"]["params"][0]
    ok = (got == {"GET /n1": (0, 10, 0), "GET /n2": (None, 10, 1), "GET /n3": (1, 10, 0)}
          and (lim["ge"], lim["le"]) == (0, 50))  # fmt: skip
    return with_s3(
        result(
            "P0-OMIT",
            "E2 on goparity2 and S2 limit",
            {
                "goparity2": "ge 0/le 10; ge null/le 10 + 1 unread; ge 1/le 10",
                "S2": "ge 0/le 50",
                "S3": "ge 0/le 100",
            },
            {"goparity2": got, "S2": [lim["ge"], lim["le"]]},
            ok,
        )
    )


def probe_p0_bind(ctx: P.Ctx) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, root, pkg in (("goparity2", P2, "."), ("goparity2mixed", P2MIXED, "."),
                            ("S2", SPEC_DIR, "goginapp")):  # fmt: skip
        fe = front(root, pkg)
        out[name] = {"validation_status": fe.validation_status,
                     "bind_status": [i for i in fe.inferences if i["rule"] == "bind_status"]}  # fmt: skip
    mixed = out["goparity2mixed"]
    ok = (out["goparity2"]["validation_status"] == 422 and out["S2"]["validation_status"] == 422
          and mixed["validation_status"] is None and len(mixed["bind_status"]) == 1
          and mixed["bind_status"][0].get("unclear") is True
          and mixed["bind_status"][0]["statuses"] == [400, 422])  # fmt: skip
    return with_s3(
        result(
            "P0-BIND",
            "E3 on goparity2, goparity2mixed, S2",
            {"goparity2": 422, "goparity2mixed": "unchanged + unclear entry", "S2": 422, "S3": 422},
            out,
            ok,
        )
    )


def probe_p0_libmw(ctx: P.Ctx) -> dict[str, Any]:
    from lattice.gocheck import GoPolicy, run_gocheck

    policy = GoPolicy(tables={"Note": "notes"}, owners={"Note": "owner_id"})
    keys = sorted(f.key for f in run_gocheck(P2, policy, strict=True))
    return result(
        "P0-LIBMW",
        "layer A on goparity2: gin.Recovery()/Logger vs ext.Guard()",
        ["GO801|GET /notes|Note"],
        keys,
        keys == ["GO801|GET /notes|Note"],
    )


def probe_p0_choices(ctx: P.Ctx) -> dict[str, Any]:
    fe = front(SPEC_DIR, "goginapp")
    got = {f"{t.name}.{c.name}": list(c.choices) for t in fe.model.tables for c in t.columns
           if c.choices}  # fmt: skip
    ok = got == {"Run.status": ["queued", "cancelled"]}
    return with_s3(
        result(
            "P0-CHOICES",
            "E4 on S2: choices from status_facts",
            {
                "S2": {"Run.status": ["queued", "cancelled"]},
                "S3": {"Ticket.status": ["open", "closed"]},
            },
            got,
            ok,
        )
    )


def probe_p0_priv(ctx: P.Ctx) -> dict[str, Any]:
    s2 = front(SPEC_DIR, "goginapp").private
    gx = front(GOEXTRACT, ".").private
    ok = s2 == {"User": ["password", "token"]} and gx == {"User": ["password_hash"]}
    return with_s3(
        result(
            "P0-PRIV",
            'E5 on S2 and the goextract fixture (json:"-")',
            {
                "S2": {"User": ["password", "token"]},
                "goextract": {"User": ["password_hash"]},
                "S3": {"User": ["password_hash"]},
            },
            {"S2": s2, "goextract": gx},
            ok,
        )
    )


def probe_pa4(ctx: P.Ctx) -> dict[str, Any]:
    return P.probe_pa4(ctx)


def probe_pg805(ctx: P.Ctx) -> dict[str, Any]:
    row = P.probe_pg805(ctx)
    row["evidence"]["s3_pairing_count"] = "NOT RUN (gotickets not delivered)"
    return row


# ---- arm S1 (dev) ------------------------------------------------------------------------------


def probe_pb1(ctx: P.Ctx) -> dict[str, Any]:
    row = P.probe_pb1(ctx)
    row["predicted"] = {"cases": "28-40", "findings": 0}
    row["evidence"]["in_predicted_range"] = 28 <= row["observed"]["cases"] <= 40
    row["evidence"]["reading"] = (
        "B takes rules=go_harness_rb.RULES (a 422 is a 422); the CLI row is PB1-CLI"
    )
    return row


def probe_pb1_cli(ctx: P.Ctx) -> dict[str, Any]:
    base = [ctx.py, "-m", "lattice.spec", "check", "--spec", "tests.spec.go_harness_rb:SPEC",
            "--app", "tests.spec.go_harness_rb:make_app", "--db", "tests.spec.go_harness_rb:build_db",
            "--runtime", "tests.spec.go_harness_rb:RUNTIME", "--login",
            "tests.spec.go_harness_rb:LOGIN", "--layers", "B", "--json"]  # fmt: skip
    rc, out, err, _ = P.run([*base, "--rules", "tests.spec.go_harness_rb:RULES"], env=ctx.env())
    rc0, out0, _, _ = P.run(base, env=ctx.env())
    try:
        with_rules = sorted(f"{f['layer']}|{f['endpoint']}|{f['case']}"
                            for f in json.loads(out)["findings"])  # fmt: skip
        default = sorted(f"{f['layer']}|{f['endpoint']}|{f['case']}"
                         for f in json.loads(out0)["findings"])  # fmt: skip
    except ValueError:
        return result(
            "PB1-CLI",
            "python -m lattice.spec check --layers B --rules",
            None,
            {"rc": rc, "stderr": err[-300:]},
            False,
        )
    py = P.finding_set(P.s1_check(ctx, "B"))
    ok = rc == 0 and with_rules == py == []
    return result("PB1-CLI", "layer B on S1 through the CLI, with and without --rules",
                  {"with --rules": "identical to PB1 (0 findings)", "without": "6 F2 findings"},
                  {"with_rules_rc": rc, "with_rules_findings": with_rules, "python_api": py,
                   "without_rules_rc": rc0, "without_rules_findings": len(default)}, ok,
                  reading="pass rule: the --rules run equals PB1 exactly; the run without the "
                  "flag is reported only")  # fmt: skip


# ---- arm S2 (dev) ------------------------------------------------------------------------------


def report_of(d: Path) -> dict[str, Any]:
    return P.load_report(d)


def probe_pi1(ctx: P.Ctx, d: Path) -> dict[str, Any]:
    rep, cand = report_of(d), P.load_candidate(d)
    routes = {(c.method, c.path): c.auth for c in cand.CONTRACTS}
    tables = set(cand.PROFILE.api.table_names.values())
    ok = (rep["rules"]["framework"] == "gin" and routes == T_S2_ROUTES
          and tables == {"users", "configs", "runs"})  # fmt: skip
    return result(
        "PI1",
        "rules-first import of S2: framework, routes, contracts, tables",
        {
            "framework": "gin",
            "routes": 7,
            "contracts": 7,
            "tables": 3,
            "public": ["GET /health", "POST /login"],
            "others": "Customer",
        },
        {
            "framework": rep["rules"]["framework"],
            "routes": len(routes),
            "contracts": len(cand.CONTRACTS),
            "tables": sorted(tables),
            "auth": {f"{m} {p}": a for (m, p), a in routes.items()},
        },
        ok,
    )


def probe_pi2(ctx: P.Ctx, d: Path) -> dict[str, Any]:
    rep, cand = report_of(d), P.load_candidate(d)
    inf = rep["rules"].get("inferences", [])
    x = P.s2_extract(ctx)
    hows = {(o["file"], o["line"]): o["how"] for r in x["routes"] for o in r["owner_uses"]
            if (o["model"], o["column"]) == ("Run", "user_id")}  # fmt: skip
    owner = [i for i in inf if i.get("rule") == "owner_uses"]
    ev = owner[0]["evidence"] if len(owner) == 1 else []
    got = sorted(hows.get((f, int(n)), "?") for f, n in
                 (e.removeprefix("code:").rsplit(":", 1) for e in ev))  # fmt: skip
    counts: dict[str, int] = {}
    for i in inf:
        counts[i["rule"]] = counts.get(i["rule"], 0) + 1
    bind = [i for i in inf if i["rule"] == "bind_status"]
    tables = cand.PROFILE.api.tables
    owners = {t: tables[t].owner for t in tables}
    ok = (len(owner) == 1 and (owner[0]["table"], owner[0]["column"]) == ("Run", "user_id")
          and len(ev) == 4 and got == ["assign", "compare", "compare", "compare"]
          and owners == {"User": None, "Config": None, "Run": "user_id"} and len(inf) == 7
          and counts == {"owner_uses": 1, "path_parse": 3, "bind_status": 1, "status_facts": 1,
                         "json_dash": 1} and bind[0].get("status") == 422)  # fmt: skip
    return result(
        "PI2",
        "owners and inferences[] of the S2 import",
        {
            "owner": "Run: user_id, 4 evidence lines (1 assign, 3 compare)",
            "inferences": {
                "owner_uses": 1,
                "path_parse": 3,
                "bind_status": 1,
                "status_facts": 1,
                "json_dash": 1,
            },
        },
        {"inferences": len(inf), "by_rule": counts, "how": got, "owners": owners},
        ok,
    )


def probe_pi3(ctx: P.Ctx, d: Path) -> dict[str, Any]:
    row = P.probe_pi3(ctx, d)
    ob = row["observed"]
    row["predicted"] = {"items": "38-44", "verified_share": "0.62-0.82", "refuted": "0-1",
                        "corrections": 0, "gate": "share >= 0.60; every refuted item has a "
                        "correction or a baseline entry with its reason; every unexercised item "
                        "has a reason"}  # fmt: skip
    row["evidence"]["in_predicted_range"] = {
        "items": 38 <= ob["items"] <= 44, "share": 0.62 <= ob["verified_share"] <= 0.82,
        "refuted": ob["refuted"] <= 1,
        "corrections": len(report_of(d)["rules"]["corrections"]) == 0}  # fmt: skip
    row["evidence"]["unexercised"] = {i: v["reason"] for i, v in
                                      report_of(d)["rules"]["items"].items()
                                      if v["verdict"] == "unexercised"}  # fmt: skip
    return row


def probe_pi5(ctx: P.Ctx, d: Path) -> dict[str, Any]:
    row = P.probe_pi5(ctx, d)
    row["predicted"] = "2-6 proposals including the machine runs.status queued -> cancelled"
    row["evidence"]["in_predicted_range"] = 2 <= row["observed"]["count"] <= 6
    return row


def correction_map(rep: dict[str, Any]) -> dict[str, str]:
    return {c["item"]: c["corrected"] for c in rep["rules"]["corrections"] if c["corrected"]}


def probe_pi6(ctx: P.Ctx, d: Path) -> dict[str, Any]:
    rev = ctx.scratch / "review"
    shutil.rmtree(rev, ignore_errors=True)
    shutil.copytree(d, rev)
    rc, out, err, _ = P.run([ctx.py, "-m", "lattice.importer", "review", str(rev), "list"],
                            env=ctx.env())  # fmt: skip
    listed = sorted(ln.split("\t")[0] for ln in out.splitlines() if "\trule\t" in ln)
    rep = report_of(d)
    cmap = correction_map(rep)
    expected, seen = [], set()
    for i, v in rep["rules"]["items"].items():
        if v["verdict"] in VERIFIED:
            continue
        name = cmap.get(i, i)
        k = 2
        base = name
        while name in seen or (name != i and name in rep["rules"]["items"]):
            name, k = f"{base} #{k}", k + 1
        seen.add(name)
        expected.append(name)
    ok = rc == 0 and listed == sorted(expected)
    return result("PI6", "owner review: python -m lattice.importer review <dir> list",
                  "exit 0; ids equal the report's not-verified ids after the correction map",
                  {"exit": rc, "listed": len(listed), "expected": len(expected),
                   "listed_not_expected": sorted(set(listed) - set(expected)),
                   "expected_not_listed": sorted(set(expected) - set(listed)),
                   "corrections": len(cmap), "stderr": err[-300:]}, ok,
                  reading="each corrections[] item is mapped to its `corrected` id; a collision "
                  "with an existing id gets review's ` #k` suffix (k from 2)")  # fmt: skip


def machinery(ctx: P.Ctx) -> dict[str, Any]:
    """P.s2_machinery, plus the profile patch import_rules applies (E3, E5)."""
    if "m" in ctx.cache:
        return ctx.cache["m"]  # type: ignore[no-any-return]
    m = P.s2_machinery(ctx)
    ctx.cache["m"] = m
    return m


def probe_pr3(ctx: P.Ctx, d: Path) -> dict[str, Any]:
    cand, rep = P.load_candidate(d), report_of(d)
    mine = P.verdict_map(P.probe_r_on_spec(ctx, cand.SPEC, "seed"))
    cmap = correction_map(rep)
    theirs = {cmap.get(i, i): v["verdict"] for i, v in rep["rules"]["items"].items()}
    diff = {i: [theirs.get(i), mine.get(i)] for i in sorted(set(theirs) | set(mine))
            if theirs.get(i) != mine.get(i)}  # fmt: skip
    return result("PR3", "layer R on S2 with the imported candidate_spec.py",
                  "the same item verdicts as the import's final verdicts (after the correction "
                  "map)", {"items_import": len(theirs), "items_rerun": len(mine),
                           "corrections": len(cmap), "differences [import, rerun]": diff},
                  not diff, reading="literal verdict strings; the import's item ids are mapped "
                  "through corrections[] first")  # fmt: skip


def probe_pr_t2(ctx: P.Ctx, d: Path) -> dict[str, Any]:
    items = report_of(d)["rules"]["items"]
    got = {i: items[i]["verdict"] for i in ("T2 User.password", "T2 User.token") if i in items}
    ok = len(got) == 2 and all(v in VERIFIED for v in got.values())
    return result(
        "PR-T2",
        "T2 items of the S2 import (E5)",
        "T2 User.password and T2 User.token present and verified",
        got,
        ok,
    )


def probe_pa1(ctx: P.Ctx) -> dict[str, Any]:
    from lattice.gocheck.derive import derive_go_policy

    p = derive_go_policy(GOGINAPP)
    keys = P.run_gocheck_keys(GOGINAPP, p)
    got = {"tables": len(p.tables), "users_table": p.users_table, "owners": dict(p.owners),
           "private": {k: list(v) for k, v in p.private.items()},
           "admin_routes": [list(r) for r in p.admin_routes], "machines": dict(p.machines),
           "baseline": sorted(p.baseline)}  # fmt: skip
    exact = (got["tables"] == 3 and got["users_table"] == "User"
             and got["owners"] == {"Run": "user_id"}
             and got["private"] == {"User": ["password", "token"]}
             and got["admin_routes"] == [] and got["machines"] == {}
             and got["baseline"] == ["GO802|POST /login|User.token"])  # fmt: skip
    base_ok = set(p.baseline) == keys
    return result(
        "PA1",
        "derive_go_policy on S2",
        {
            "tables": 3,
            "users_table": "User",
            "owners": {"Run": "user_id"},
            "private": {"User": ["password", "token"]},
            "admin_routes": [],
            "machines": {},
            "baseline": ["GO802|POST /login|User.token"],
        },
        got,
        exact and base_ok,
        baseline_equals_separate_run=base_ok,
    )


def probe_pa3(ctx: P.Ctx, d: Path) -> dict[str, Any]:
    from lattice.gocheck.derive import derive_go_policy, go_spec_policy

    cand = P.load_candidate(d)
    derived = derive_go_policy(GOGINAPP)
    policy = go_spec_policy(cand.SPEC, GOGINAPP, derived)
    orig = P.findings_of(GOGINAPP, policy)
    outside = [f for f in orig if f.key not in derived.baseline]
    sig = {(f.code, f.message) for f in orig}
    rows: dict[str, Any] = {}
    for mid in ("JM5", "JM6"):
        copy = P.mutate_goginapp(ctx, mid)
        new = [f for f in P.findings_of(copy, policy) if (f.code, f.message) not in sig]
        rows[mid] = [{"code": f.code, "key": f.key} for f in new]
    jm5 = [
        x
        for x in rows["JM5"]
        if x["code"] == "GO801" and x["key"] == "GO801|POST /runs/:rid/cancel|Run"
    ]  # noqa: E501
    ok = not outside and bool(jm5)
    return result("PA3", "edits on a scratch copy of S2 under go_spec_policy of its candidate spec",
                  {"JM5": "GO801 on POST /runs/:rid/cancel Run (E6)",
                   "JM6": "0 new findings (C5b, stated in advance)",
                   "original_outside_baseline": 0},
                  {"original_total": len(orig), "original_outside_baseline": len(outside),
                   "original_outside": [f.key for f in outside], "new_findings": rows}, ok,
                  jm6_new_findings=len(rows["JM6"]),
                  reading="a mutant's finding is new when its (code, message) is not among the "
                  "original's; JM6 is reported, not gated (C5b)")  # fmt: skip


# ---- arm S3 --------------------------------------------------------------------------------------


def s3_rows(ids: list[str]) -> list[dict[str, Any]]:
    return [not_run(i, "S3 (blind)", S3_WHY) for i in ids]


# ---- cross-arm: byte compatibility ----------------------------------------------------------------


def frozen_rows() -> list[tuple[str, str]]:
    text = PREREG.read_text()
    sec = text.split("**Frozen (empty diff vs")[1].split("Also frozen, with no hash listed")[0]
    rows = re.findall(r"^\| (\S.*?) \| ([0-9a-f]{64}) \|", sec, re.M)
    out = []
    for f, h in rows:
        rel = f.replace("\\", "")
        out.append((rel if rel.startswith("lattice/gocheck/src/*.go") else rel.split(" (")[0], h))
    return out


def group_files(root: Path) -> list[str]:
    skip = {"goextract.go", "goextract_tables.go", "goextract_closure.go",
            "goextract_assemble.go", "routes.go"}  # fmt: skip
    src = root / "lattice/gocheck/src"
    return sorted(x.name for x in src.iterdir() if x.suffix == ".go" and x.name not in skip)


def file_hash(root: Path, rel: str) -> str:
    if rel.startswith("lattice/gocheck/src/*.go"):
        src = root / "lattice/gocheck/src"
        return hashlib.sha256(
            b"".join((src / n).read_bytes() for n in group_files(root))
        ).hexdigest()
    return sha(root / rel)


def changed_vs_base(base: Path, roots: tuple[str, ...]) -> list[str]:
    """Files under ``roots`` whose bytes differ from the base (added files are not listed)."""
    out = []
    for r in roots:
        for p in sorted((base / r).rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                cur = REPO / p.relative_to(base)
                if not cur.is_file() or sha(cur) != sha(p):
                    out.append(p.relative_to(base).as_posix())
    return out


def probe_pb_f(ctx: P.Ctx) -> dict[str, Any]:
    base = ctx.scratch / "base"
    rows, stale, touched = [], [], []
    for rel, listed in frozen_rows():
        mine, theirs = file_hash(REPO, rel), file_hash(base, rel)
        rows.append({"file": rel, "prereg": listed[:12], "base": theirs[:12], "now": mine[:12]})
        if theirs != listed:
            stale.append(rel)
        if mine != theirs:
            touched.append(rel)
    extra = ["tests/spec/goapp/go.mod", "tests/spec/goapp/go.sum", "tests/spec/goginapp/go.mod",
             "tests/spec/goginapp/go.sum", "tests/spec/goginapp/studio.toml"]  # fmt: skip
    extra_changed = [e for e in extra if sha(REPO / e) != sha(base / e)]
    fixtures = changed_vs_base(base, ("tests/gocheck/fixtures",))
    allowed = {"tests/importer/test_go_rules.py", "tests/gocheck/test_go_derive.py",
               "tests/spec/test_go_layers_rb.py"}  # fmt: skip
    tests = [t for t in changed_vs_base(base, ("tests", "lattice/studio", "lattice/mcp"))
             if t not in allowed and t not in fixtures and "/golden" not in t]  # fmt: skip
    return result("PB-F", "every frozen file has its sha256 at 193f7f6; existing fixtures and tests "
                  "unchanged", "all identical",
                  {"files": len(rows), "prereg_value_differs_from_193f7f6": stale,
                   "changed_since_193f7f6": touched, "extra_frozen_changed": extra_changed,
                   "existing_fixture_files_changed": fixtures,
                   "other_existing_test_files_changed": tests,
                   "edited_j1_tests_allowed_by_section_7": sorted(
                       t for t in allowed if sha(REPO / t) != sha(base / t))},
                  not touched and not extra_changed and not fixtures and not tests, rows=rows,
                  reading="the gate compares the working tree with `git archive 193f7f6` (the "
                  "prereg values that differ from 193f7f6 are listed, never silently replaced)")  # fmt: skip


def probe_pb_x(ctx: P.Ctx) -> dict[str, Any]:
    from lattice.gocheck import build_gocheck

    base_bin = ctx.scratch / "base_gocheck" / "gocheck"
    ok, err, _ = P.go_build(
        ctx.scratch / "base" / "lattice/gocheck/src", base_bin, flags="-mod=mod"
    )
    if not ok:
        return not_run(
            "PB-X", "default gocheck extract byte-identical", f"base build failed: {err}"
        )
    new_bin = build_gocheck()
    rows = []
    for rel in PB_X_DIRS:
        a, b = P.extract_digest(base_bin, rel), P.extract_digest(new_bin, rel)
        rows.append({"dir": rel, "base": a[:16], "now": b[:16], "same_as_base": a == b})
    same = sum(r["same_as_base"] for r in rows)
    return result("PB-X", "default gocheck extract byte-identical on the 7 module dirs and goapp",
                  "8 of 8 identical to 193f7f6", {"identical_to_base": same, "of": len(rows)},
                  same == len(rows), rows=rows,
                  reading="goapp is the 7th of round 1's 7 dirs; the 8th, tests/gocheck/fixtures/"
                  "runs, is the builder's addition (the prereg's '7 dirs and goapp' counts goapp "
                  "twice)")  # fmt: skip


def probe_pb_g(ctx: P.Ctx) -> dict[str, Any]:
    """PB-G with a pristine input: ``inventory`` hashes every file under its root, so the new
    fixtures beside ``goextract_workflow`` change its output by construction. Both trees run on
    a copy of the 193f7f6 fixtures; the literal in-repo difference is reported and must be
    exactly the new fixtures' files."""
    pristine = ctx.scratch / "pbg_fixtures"
    shutil.rmtree(pristine, ignore_errors=True)
    shutil.copytree(ctx.scratch / "base/tests/gocheck/fixtures", pristine)
    rows: dict[str, Any] = {}
    for name, args_ in (
        (
            "workflows --static",
            ["workflows", str(pristine), "--package", "goextract_workflow", "--static"],
        ),
        ("inventory", ["inventory", str(pristine), "--package", "goextract_workflow"]),
    ):
        outs = []
        for tree in (ctx.scratch / "base", P.REPO):
            d = ctx.scratch / "pbg2" / name.split()[0] / ("base" if tree != P.REPO else "now")
            shutil.rmtree(d, ignore_errors=True)
            rc, so, se, _ = P.run([ctx.py, "-m", "lattice.importer", *args_, "--out", str(d)],
                                  cwd=tree, env=ctx.env(PYTHONPATH=str(tree)), timeout=600)  # fmt: skip
            outs.append((rc, P.tree_files(d) if d.exists() else {}, so.replace(str(d), "<out>")))
        rows[name] = {
            "same": outs[0] == outs[1] and outs[0][0] == 0,
            "rc": [outs[0][0], outs[1][0]],
        }
    # the literal in-repo run: the difference may only be the new fixtures' files
    lit = {}
    for tag, tree in (("base", ctx.scratch / "base"), ("now", P.REPO)):
        d = ctx.scratch / "pbg2" / "literal" / tag
        shutil.rmtree(d, ignore_errors=True)
        P.run(
            [
                ctx.py,
                "-m",
                "lattice.importer",
                "inventory",
                "tests/gocheck/fixtures",
                "--package",
                "goextract_workflow",
                "--out",
                str(d),
            ],
            cwd=tree,
            env=ctx.env(PYTHONPATH=str(tree)),
        )
        lit[tag] = json.loads((d / "inventory.json").read_text())

    def paths(inv: Any) -> set[str]:
        return {f["path"] for f in inv["source"]["files"]}

    added = sorted(paths(lit["now"]) - paths(lit["base"]))
    removed = sorted(paths(lit["base"]) - paths(lit["now"]))
    rows["literal inventory: files added by the new fixtures only"] = {
        "added": added,
        "removed": removed,
        "same": bool(added)
        and not removed
        and all(a.startswith(("goparity2/", "goparity2mixed/")) for a in added),
    }
    impl = "tests/spec/fixtures/implementation"
    code = ("import json,sys;sys.path.insert(0,'%s');import importlib;"
            "from lattice.gocheck import run_gocheck,GoPolicy,gated_note;"
            "m=importlib.import_module('fixture_spec');"
            "f=run_gocheck('%s/app',GoPolicy.from_spec(m.SPEC),strict=True);"
            "print(json.dumps([[x.code,x.file,x.line,x.message,x.key] for x in f]));"
            "print(gated_note())" % (impl, impl))  # fmt: skip  # noqa: UP031
    a, b = P.both_trees(ctx, [ctx.py, "-c", code])
    rows["layer A findings and notes (implementation/app + its spec)"] = {
        "same": a == b and a[0] == 0
    }
    fixtures = sorted(p.name for p in (P.REPO / "tests/gocheck/fixtures").iterdir() if p.is_dir())
    layer_a = {}
    for fx in fixtures:
        if fx in ("goparity2", "goparity2mixed"):
            continue
        code2 = (
            "import json;from lattice.gocheck import run_gocheck,GoPolicy;"
            "print(json.dumps([[x.code,x.file,x.line,x.message,x.key] for x in "
            f"run_gocheck('tests/gocheck/fixtures/{fx}',GoPolicy(),strict=True)]))"
        )
        a2, b2 = P.both_trees(ctx, [ctx.py, "-c", code2])
        layer_a[fx] = a2 == b2 and a2[0] == 0
    rows["layer A (empty policy) on every existing gocheck fixture"] = {
        "same": all(layer_a.values()),
        "per_fixture": layer_a,
    }
    ok = all(v["same"] for v in rows.values())
    return result("PB-G", "Go outputs byte-identical to 193f7f6", "all identical", rows, ok,
                  reading="inventory on a pristine copy of the 193f7f6 fixtures (see the docstring); "
                  "test_process_runtime.py's reports are PB-T's")  # fmt: skip


def probe_pb_h(ctx: P.Ctx) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for name, cmd in {
        "lattice --help": [
            ctx.py,
            "-c",
            "import sys;from lattice.cli import main;"
            "sys.argv=['lattice','--help'];sys.exit(main())",
        ],
        "python -m lattice.importer --help": [ctx.py, "-m", "lattice.importer", "--help"],
        "python -m lattice.spec --help": [ctx.py, "-m", "lattice.spec", "--help"],
    }.items():
        a, b = P.both_trees(ctx, cmd)
        rows[name] = {"same": a == b, "rc": [a[0], b[0]]}
    a, b = P.both_trees(ctx, [ctx.py, "-m", "lattice.spec", "check", "--help"])

    def options(text: str) -> set[str]:
        return set(re.findall(r"--[a-z][a-z0-9-]*", text))

    def body(text: str) -> str:
        """The option descriptions, without the --rules item (argparse wraps the usage line)."""
        tail = (
            text.split("options:", 1)[-1]
            if "options:" in text
            else text.split("optional arguments:", 1)[-1]
        )
        return re.sub(r"\n  --rules [^\n]*(\n {20,}[^\n]*)*", "", tail)

    rows["python -m lattice.spec check --help"] = {
        "added_options": sorted(options(b[1]) - options(a[1])),
        "removed_options": sorted(options(a[1]) - options(b[1])),
        "descriptions_otherwise_identical": body(a[1]) == body(b[1]),
        "rc": [a[0], b[0]],
    }
    proj = ctx.scratch / "mcp_empty"
    proj.mkdir(exist_ok=True)
    msgs = "\n".join(json.dumps(m) for m in [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},  # noqa: E501
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]) + "\n"  # fmt: skip
    outs = []
    for tree in (ctx.scratch / "base", REPO):
        rc, so, _, _ = P.run([ctx.py, "-m", "lattice.mcp", "serve", "--project", str(proj)],
                             cwd=proj, env=ctx.env(PYTHONPATH=str(tree)), stdin=msgs.encode(),
                             timeout=120)  # fmt: skip
        outs.append((rc, so))
    rows["MCP tools/list"] = {"same": outs[0] == outs[1] and "tools" in outs[0][1]}
    chk = rows["python -m lattice.spec check --help"]
    ok = (all(rows[k]["same"] for k in rows if "same" in rows[k])
          and chk["added_options"] == ["--rules"] and not chk["removed_options"]
          and chk["descriptions_otherwise_identical"])  # fmt: skip
    return result(
        "PB-H",
        "help texts and MCP tools/list identical; check --help differs by --rules only",
        "as stated",
        rows,
        ok,
    )


def multivendor_import(ctx: P.Ctx, tree: Path, name: str, decoy: bool) -> dict[str, Any]:
    """The multi-vendor Python fixture copied to scratch (``bench`` package layout kept, the
    package dir ``bench.import_repos.multivendor.app`` is the directory the Go harness lookup
    reads), imported with no ``--app-factory``; ``decoy`` puts a ``studio.toml`` there."""
    root = ctx.scratch / "pbp2" / name
    shutil.rmtree(root, ignore_errors=True)
    mv = root / "bench/import_repos"
    mv.mkdir(parents=True)
    shutil.copytree(REPO / "bench/import_repos/multivendor", mv / "multivendor",
                    ignore=shutil.ignore_patterns("__pycache__"))  # fmt: skip
    for pkg in (root / "bench", mv):
        (pkg / "__init__.py").write_text("")
    package = "bench.import_repos.multivendor.app"
    if decoy:
        d = root / package
        d.mkdir()
        (d / "studio.toml").write_text('[app]\napp = "decoyapp:make_app"\n')
        (d / "decoyapp.py").write_text("def make_app(db_path):\n    raise SystemExit('decoy')\n")
    out = ctx.scratch / "pbp2" / f"{name}-out"
    shutil.rmtree(out, ignore_errors=True)
    rc, so, se, _ = P.run([ctx.py, "-m", "lattice.importer", str(root), "--package", package,
                           "--rules-only", "--out", str(out)], cwd=tree,
                          env=ctx.env(PYTHONPATH=str(tree)), timeout=1500)  # fmt: skip
    rep = json.loads((out / "report.json").read_text()) if (out / "report.json").exists() else {}
    return {"rc": rc, "app_factory": rep.get("app_factory"),
            "stderr": se[-300:]}  # fmt: skip


def probe_pb_p2(ctx: P.Ctx) -> dict[str, Any]:
    plain = multivendor_import(ctx, REPO, "plain", False)
    decoy = multivendor_import(ctx, REPO, "decoy", True)
    base_decoy = multivendor_import(ctx, ctx.scratch / "base", "basedecoy", True)
    ok = (plain["rc"] == 0 and decoy["rc"] == 0 and plain["app_factory"]
          and plain["app_factory"] == decoy["app_factory"] == base_decoy["app_factory"]
          and "make_app" in plain["app_factory"] and "decoyapp" not in plain["app_factory"])  # fmt: skip
    return result(
        "PB-P2",
        "a Python import with a decoy studio.toml [app] app",
        "report.json app_factory is the Python make_app, as without the decoy",
        {"plain": plain, "decoy": decoy, "base_with_decoy": base_decoy},
        bool(ok),
    )


def pytest_ids(log: str) -> dict[str, list[str]]:
    ids: dict[str, list[str]] = {"FAILED": [], "ERROR": [], "SKIPPED": []}
    for ln in log.splitlines():
        for k in ids:
            if ln.startswith(k + " "):
                ids[k].append(ln.split(" - ")[0].split()[1] if k != "SKIPPED" else ln)
    summary = re.findall(
        r"(\d+) (failed|passed|skipped|errors?)", log.splitlines()[-1] if log else ""
    )
    return {
        **{k: sorted(set(v)) for k, v in ids.items()},
        "summary": [f"{n} {w}" for n, w in summary],
    }  # type: ignore[dict-item]  # noqa: E501


def probe_pb_t(ctx: P.Ctx, base_log: Path | None) -> dict[str, Any]:
    cmd = [ctx.py, "-m", "pytest", "tests/importer", "tests/pycheck", "tests/gocheck", "tests/spec",
           "tests/mcp", "tests/studio", "-q", "-rsf", "--continue-on-collection-errors", "-p",
           "no:cacheprovider"]  # fmt: skip
    t0 = time.perf_counter()
    rc, out, _, _ = P.run(cmd, env=ctx.env(), timeout=7200)
    now_wall = time.perf_counter() - t0
    (ctx.out / "pytest_now.txt").write_text(out)
    if base_log is not None and base_log.exists():
        base_out = base_log.read_text()
    else:
        _, base_out, _, _ = P.run(
            cmd,
            cwd=ctx.scratch / "base",
            env=ctx.env(PYTHONPATH=str(ctx.scratch / "base")),
            timeout=7200,
        )
    (ctx.out / "pytest_base.txt").write_text(base_out)
    a, b = pytest_ids(base_out), pytest_ids(out)
    skips = lambda x: sorted(re.sub(r"\s+", " ", s) for s in x["SKIPPED"])  # noqa: E731

    def counts(x: dict) -> dict[str, int]:
        return {k.rstrip("s"): int(n) for n, k in (w.split() for w in x["summary"])
                if k.rstrip("s") in ("failed", "skipped", "error")}  # fmt: skip

    same = (a["FAILED"] == b["FAILED"] and a["ERROR"] == b["ERROR"] and skips(a) == skips(b)
            and counts(a) == counts(b))  # fmt: skip
    return result("PB-T", "pytest tests/importer pycheck gocheck spec mcp studio",
                  "the same failing, skipped and error ids as 193f7f6 (plus the new tests passing)",
                  {"base": a["summary"], "now": b["summary"], "wall_s": round(now_wall, 1),
                   "counts_base": counts(a), "counts_now": counts(b),
                   "failed_only_now": sorted(set(b["FAILED"]) - set(a["FAILED"])),
                   "failed_only_base": sorted(set(a["FAILED"]) - set(b["FAILED"])),
                   "errors_only_now": sorted(set(b["ERROR"]) - set(a["ERROR"])),
                   "errors_only_base": sorted(set(a["ERROR"]) - set(b["ERROR"]))}, same,
                  reading="known flaky studio tests: any id that differs is listed above and "
                  "judged in the results, never filtered here")  # fmt: skip


def probe_pb_d(ctx: P.Ctx) -> dict[str, Any]:
    cmd = [ctx.py, "-m", "lattice.spec", "check", "--spec", "spec:SPEC", "--app",
           "harness:make_app", "--db", "harness:build_db", "--runtime", "harness:RUNTIME",
           "--layers", "DX"]  # fmt: skip
    outs = []
    for tree in (ctx.scratch / "base", REPO):
        rc, so, se, _ = P.run(cmd, cwd=tree / "dogfood/studio",
                              env=ctx.env(PYTHONPATH=str(tree)), timeout=1800)  # fmt: skip
        outs.append((rc, so.strip().splitlines()[-1] if so.strip() else "", se[-200:]))
    ok = outs[0][0] == 0 and outs[1][0] == 0
    return result("PB-D", "dogfood/studio check (layers DX, venv python)", "passes",
                  {"base": outs[0], "now": outs[1]}, ok,
                  reading="check.sh is `uv run python -m lattice.spec check ...`; the venv python "
                  "is used instead (J1 deviation 10)")  # fmt: skip


# ---- self-hash check --------------------------------------------------------------------------


def verify_freeze(path: Path) -> dict[str, Any]:
    """Every entry of the freeze file (the runner itself included) is re-hashed before the first
    metric; any mismatch aborts. No entry can be excluded."""
    bad, n, own = [], 0, False
    for ln in path.read_text().splitlines():
        if not ln.strip():
            continue
        digest, rel = ln.split(None, 1)
        rel = rel.strip()
        n += 1
        own = own or (REPO / rel).resolve() == Path(__file__).resolve()
        if not (REPO / rel).is_file() or sha(REPO / rel) != digest:
            bad.append(rel)
    if bad or not own:
        raise SystemExit(f"freeze check failed: mismatch {bad}; runner listed: {own}")
    return {"freeze_file": str(path), "entries": n, "runner_listed": own, "mismatches": 0}


# ---- main ----------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--arm", default="0,S1,S2,S3,X", help="0, S1, S2, S3 and X (cross-arm)")
    ap.add_argument("--base", required=True, help="an existing `git archive 193f7f6`")
    ap.add_argument("--scratch", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--freeze", default="", help="the freeze.sha256 to verify before anything")
    ap.add_argument("--only", default="")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--pytest-base-log", default="", help="an earlier PB-T run of the base")
    args = ap.parse_args(argv)
    freeze: dict[str, Any] = {"checked": False}
    if args.freeze:
        freeze = {"checked": True, **verify_freeze(Path(args.freeze))}
    arms = set(args.arm.split(","))
    out = Path(args.out).resolve()
    scratch = Path(args.scratch).resolve() if args.scratch else out.parent / "scratch"
    ctx = P.Ctx(scratch, out, "193f7f6")
    ctx.base_dir = Path(args.base).resolve()
    if GOTICKETS.is_dir():
        P.SNAPSHOT_ROOTS.append("tests/spec/gotickets")
    P.setup(ctx, "S")
    only = {x for x in args.only.split(",") if x}
    want = lambda pid: (not only or pid in only) and pid in ALL_IDS  # noqa: E731
    results: list[dict[str, Any]] = []

    def add(pid: str, fn: Callable[[], dict[str, Any]], *, needs: str = "") -> None:
        if not want(pid):
            return
        if needs == "s2" and ctx.s2_error:
            results.append(not_run(pid, "S2", f"S2 does not build: {ctx.s2_error[:400]}"))
        elif needs == "s1" and ctx.s1_error:
            results.append(not_run(pid, "S1", f"S1 does not build: {ctx.s1_error[:400]}"))
        else:
            try:
                results.append(fn())
            except Exception as err:  # noqa: BLE001 -- a probe that cannot run is reported
                import traceback

                results.append(
                    result(
                        pid,
                        "(the probe raised)",
                        None,
                        f"{type(err).__name__}: {err}",
                        False,
                        traceback=traceback.format_exc()[-1500:],
                    )
                )
        print(f"{pid}: pass={results[-1]['pass']}", flush=True)

    if "0" in arms:
        for pid, fn in (("P0-PARSE", probe_p0_parse), ("P0-OMIT", probe_p0_omit),
                        ("P0-BIND", probe_p0_bind), ("P0-LIBMW", probe_p0_libmw),
                        ("P0-CHOICES", probe_p0_choices), ("P0-PRIV", probe_p0_priv),
                        ("PA4", probe_pa4), ("PG805", probe_pg805)):  # fmt: skip
            add(pid, lambda fn=fn: fn(ctx))
    if "S1" in arms:
        add("PR1", lambda: P.probe_pr1(ctx), needs="s1")
        add("PR2", lambda: P.probe_pr2(ctx), needs="s1")
        add("PR4", lambda: P.probe_pr4(ctx, args.runs), needs="s1")
        add("PB1", lambda: probe_pb1(ctx), needs="s1")
        add("PB1-CLI", lambda: probe_pb1_cli(ctx), needs="s1")
        add("PB2", lambda: P.probe_pb2(ctx), needs="s1")
        add("PB3", lambda: P.probe_pb3(ctx, args.runs), needs="s1")
    imp = ctx.scratch / "imp" / "r0"
    if "S2" in arms:
        ran: dict[str, Any] = {}
        s2_ids = ("PI1", "PI2", "PI3", "PI4", "PI5", "PI6", "PI7", "PR3", "PR-T2", "PA3")
        if any(want(p) for p in (*s2_ids, "PX")) and not ctx.s2_error:
            t = P.import_s2(ctx, ctx.scratch / "imp" / "lazy", lazy_build=True)
            ctx.timings["s2_import_lazy_build_s"] = t["wall_s"]
            ran = P.import_s2(ctx, imp)
            if ran["rc"] != 0:
                print("the S2 import failed:", ran["stderr"], file=sys.stderr)
        ok_imp = bool(ran) and ran["rc"] == 0
        for pid, fn in (("PI1", probe_pi1), ("PI2", probe_pi2), ("PI3", probe_pi3),
                        ("PI4", P.probe_pi4), ("PI5", probe_pi5), ("PI6", probe_pi6),
                        ("PR3", probe_pr3), ("PR-T2", probe_pr_t2), ("PA3", probe_pa3)):  # fmt: skip
            if ok_imp:
                add(pid, lambda fn=fn: fn(ctx, imp), needs="s2")
            else:
                add(
                    pid,
                    lambda pid=pid: not_run(pid, "S2 import", "the S2 import did not run"),
                    needs="s2",
                )
        add("PI7", lambda: P.probe_pi7(ctx, args.runs), needs="s2")
        add("PA1", lambda: probe_pa1(ctx))
        add("PA2", lambda: P.probe_pa2(ctx))
    if "S3" in arms:
        for r in s3_rows([i for i in ALL_IDS if i.endswith("-3")]):
            if want(r["id"]):
                results.append(r)
                print(f"{r['id']}: pass=None (NOT RUN)", flush=True)
    if "X" in arms:
        add("PW", lambda: P.probe_pw(ctx))

        def px() -> dict[str, Any]:
            row = P.probe_px(ctx)
            row["evidence"]["s3_import"] = "NOT RUN (gotickets not delivered)"
            return row

        add("PX", px)
        add("PB-F", lambda: probe_pb_f(ctx))
        add("PB-X", lambda: probe_pb_x(ctx))
        add("PB-G", lambda: probe_pb_g(ctx))
        add("PB-P", lambda: P.probe_pb_p(ctx))
        add("PB-P2", lambda: probe_pb_p2(ctx))
        add("PB-H", lambda: probe_pb_h(ctx))
        if "PB-T" in only or not only:
            add(
                "PB-T",
                lambda: probe_pb_t(
                    ctx, Path(args.pytest_base_log) if args.pytest_base_log else None
                ),
            )
        add("PB-D", lambda: probe_pb_d(ctx))
    doc = {"arms": sorted(arms), "base_ref": "193f7f6", "freeze": freeze, "timings": ctx.timings,
           "s1_error": ctx.s1_error, "s2_error": ctx.s2_error, "results": results}  # fmt: skip
    (out / "probes2.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")
    if (imp / "report.json").exists():
        for name in ("report.json", "candidate_spec.py", "profile.py", "proposals.json",
                     "go_policy.json"):  # fmt: skip
            shutil.copyfile(imp / name, out / f"s2_import_{name}")
    passed = [r["id"] for r in results if r["pass"] is True]
    failed = [r["id"] for r in results if r["pass"] is False]
    nr = [r["id"] for r in results if r["pass"] is None]
    print(
        f"passed {len(passed)}: {passed}\nfailed {len(failed)}: {failed}\nnot run {len(nr)}: {nr}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
