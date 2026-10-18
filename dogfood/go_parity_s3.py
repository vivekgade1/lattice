"""Arm S3 of the Go parity gaps, round 2 (docs/results/2026-10-18-go-parity-gaps-round2-prereg.md,
section 4 T-S3, section 5.4, Amendment 5; Spike J, node J2). The ten blind S3 rows PI1-3 .. PA3-3 and the
S3 sub-claims of P0-PARSE, P0-OMIT, P0-BIND, P0-CHOICES, P0-PRIV, PG805 and PX (ids ``P0-PARSE:S3`` ..).

The frozen runners (``go_parity_probes.py``, ``go_parity_probes2.py``) are imported read-only and are
never edited: their helpers (``result``, ``front``, ``pol``, ``correction_map``, ``probe_pi6``,
``verify_freeze`` ...) are reused. Every pass rule and every prediction below is the pre-registration's
section 5.4 / 5.1 text; where a rule is ambiguous this file fixes one reading *before* the scored run and
says so in the row's ``reading``. The fixture ``tests/spec/gotickets`` and its harness are delivered by a
separate author; the builder of this file never opened them (Amendment 5).

    "$PY" dogfood/go_parity_s3.py --base "$SCRATCH/base" --scratch "$SCRATCH/s3" \\
        --out docs/results/data/2026-10-18-go-parity-gaps-round2/scored --runs 10

Two checks run before any metric and cannot be switched off: the Amendment 3 freeze
(``freeze.sha256``) and this runner's own ``s3runner.sha256`` (both runners and the test file).
``--fixture`` / ``--harness`` point the same functions at another module under ``tests/spec`` (a
stand-in such as ``goginapp`` for a smoke test); the orchestrator does not use them, and the output is
then marked ``stand_in``. The output is ``<out>/probes-s3.json``. Nothing here edits a fixture:
imports write to the scratch and the Go app is built to the scratch.
"""  # noqa: E501

# ruff: noqa: E501, UP031
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import go_parity_probes as P1  # noqa: E402  (round 1, frozen: helpers)
import go_parity_probes2 as P2  # noqa: E402  (round 2, frozen: helpers, verify_freeze)

REPO = P1.REPO
SPEC_DIR = P1.SPEC_DIR
VERIFIED = P1.VERIFIED
DATA = REPO / "docs/results/data/2026-10-18-go-parity-gaps-round2"
AMENDMENT3_FREEZE = DATA / "freeze.sha256"
S3_FREEZE = DATA / "s3runner.sha256"
OWN_FILES = ("dogfood/go_parity_s3.py", "dogfood/go_parity_s3_mutants.py",
             "tests/spec/test_go_parity_s3.py")  # fmt: skip
DEFAULT_PACKAGE = "gotickets"
DEFAULT_HARNESS = "tests.spec.gotickets_harness"
DEFAULT_BINARY_ENV = "LATTICE_GOTICKETS_BIN"

result, not_run, sha = P1.result, P1.not_run, P1.sha

# ---- T-S3 (section 4), as machine data -------------------------------------------------------------

T_S3_ROUTES = {
    ("GET", "/health"): "Public",
    ("POST", "/login"): "Public",
    ("GET", "/queues"): "Public",
    ("GET", "/me"): "Customer",
    ("POST", "/queues/{qid}/tickets"): "Customer",
    ("GET", "/tickets"): "Customer",
    ("GET", "/tickets/{tid}"): "Customer",
    ("POST", "/tickets/{tid}/close"): "Customer",
    ("POST", "/tickets/{tid}/reopen"): "Customer",
    ("POST", "/tickets/{tid}/handoffs"): "Customer",
    ("GET", "/handoffs"): "Customer",
}
T_S3_TABLES = {"users", "queues", "tickets", "handoffs"}
T_S3_PARSED = [  # E1: (route, param), sorted
    ("GET /tickets/{tid}", "tid"),
    ("POST /queues/{qid}/tickets", "qid"),
    ("POST /tickets/{tid}/close", "tid"),
    ("POST /tickets/{tid}/handoffs", "tid"),
    ("POST /tickets/{tid}/reopen", "tid"),
]
T_S3_INFERENCES = {"owner_uses": 2, "path_parse": 5, "bind_status": 1, "status_facts": 1,
                   "json_dash": 1}  # fmt: skip
T_S3_TICKET_OWNER = ("Ticket", "reporter_id")
T_S3_TICKET_HOWS = ["assign"] + ["compare"] * 6  # 7 evidence lines
T_S3_HANDOFF_COLUMNS = {"from_user_id", "to_user_id"}
T_S3_CHOICES = {"Ticket.status": ["open", "closed"]}
T_S3_PRIVATE = {"User": ["password_hash"]}
T_S3_MACHINE_EDGES = [["open", "closed"], ["closed", "open"]]

ROW_IDS = ["PI1-3", "PI2-3", "PI3-3", "PI4-3", "PI5-3", "PI6-3", "PI7-3", "PR3-3", "PA1-3",
           "PA3-3"]  # fmt: skip
SUB_IDS = ["P0-PARSE:S3", "P0-OMIT:S3", "P0-BIND:S3", "P0-CHOICES:S3", "P0-PRIV:S3", "PG805:S3",
           "PX:S3"]  # fmt: skip
ALL_IDS = [*ROW_IDS, *SUB_IDS]
# the rows that read the import (PI7-3 repeats it by itself; PA1-3 and the P0 rows are static)
IMPORT_ROWS = ["PI1-3", "PI2-3", "PI3-3", "PI4-3", "PI5-3", "PI6-3", "PR3-3", "PA3-3", "PX:S3"]


@dataclass(frozen=True)
class Fx:
    """The fixture under test: the module ``tests/spec/<package>`` and its harness module."""

    package: str = DEFAULT_PACKAGE
    harness: str = DEFAULT_HARNESS

    @property
    def module_dir(self) -> Path:
        return SPEC_DIR / self.package

    @property
    def stand_in(self) -> bool:
        return (self.package, self.harness) != (DEFAULT_PACKAGE, DEFAULT_HARNESS)


# ---- the two self-hash checks (never disabled) ---------------------------------------------------


def verify_listed(path: Path, *, must_list: Sequence[str], this: Path) -> dict[str, Any]:
    """Every entry of a sha256sum-style file is re-hashed; any mismatch, a missing file, a missing
    required entry or the running script not being listed aborts. Nothing can be excluded."""
    if not path.is_file():
        raise SystemExit(f"freeze check failed: {path} does not exist")
    bad, listed, n = [], set(), 0
    for ln in path.read_text().splitlines():
        if not ln.strip():
            continue
        digest, rel = ln.split(None, 1)
        rel = rel.strip()
        listed.add(rel)
        n += 1
        if not (REPO / rel).is_file() or sha(REPO / rel) != digest:
            bad.append(rel)
    missing = [m for m in must_list if m not in listed]
    own = any((REPO / r).resolve() == this.resolve() for r in listed)
    if bad or missing or not own:
        raise SystemExit(
            f"freeze check failed ({path.name}): mismatch {bad}; not listed {missing}; "
            f"running script listed: {own}"
        )
    return {"file": str(path), "entries": n, "mismatches": 0, "running_script_listed": own}


def verify_all(this: Path) -> dict[str, Any]:
    """Amendment 3's freeze (round-2 library, tests, frozen runners) and this runner's own freeze."""
    a3 = P2.verify_freeze(AMENDMENT3_FREEZE)
    own = verify_listed(S3_FREEZE, must_list=OWN_FILES, this=this)
    return {"amendment3": a3, "s3runner": own}


# ---- the context -----------------------------------------------------------------------------------


class Ctx(P1.Ctx):
    """Round 1's context, with the fixture's binary exported under the harness' variable."""

    fx_bin: Path | None = None
    fx_env: str = DEFAULT_BINARY_ENV
    fx: Fx = Fx()
    fx_error: str | None = None

    def env(self, **extra: str) -> dict[str, str]:
        env = super().env()
        if self.fx_bin:
            env[self.fx_env] = str(self.fx_bin)
        env.update(extra)
        return env


def harness_env_name(harness: str) -> str:
    """The variable the harness reads for its binary (``BINARY_ENV``, as gogin_harness), else the
    default name; the lookup never builds anything."""
    try:
        return str(getattr(importlib.import_module(harness), "BINARY_ENV", DEFAULT_BINARY_ENV))
    except Exception:  # noqa: BLE001 -- reported through the build / import rows
        return DEFAULT_BINARY_ENV


def setup(ctx: Ctx, fx: Fx, base_dir: Path) -> None:
    """Shims, scratch, the base link, the fixtures' snapshot, and the fixture's binary built to the
    scratch. A missing fixture or a failing build is ``ctx.fx_error`` (the rows are NOT RUN)."""
    P1.make_shims(ctx)
    ctx.scratch.mkdir(parents=True, exist_ok=True)
    ctx.out.mkdir(parents=True, exist_ok=True)
    ctx.fx, ctx.base_dir = fx, base_dir
    link = ctx.scratch / "base"
    if link.is_symlink():
        link.unlink()
    shutil.rmtree(link, ignore_errors=True)
    link.symlink_to(base_dir, target_is_directory=True)
    root = f"tests/spec/{fx.package}"
    if root not in P1.SNAPSHOT_ROOTS and fx.module_dir.is_dir():
        P1.SNAPSHOT_ROOTS.append(root)
    ctx.snap0 = P1.snapshots()
    ctx.fx_env = harness_env_name(fx.harness)
    if not fx.module_dir.is_dir():
        ctx.fx_error = f"{root} does not exist"
        return
    ok, err, wall = P1.go_build(fx.module_dir, ctx.scratch / "fxbin" / fx.package)
    ctx.timings["build_fixture_s"] = wall
    if ok:
        ctx.fx_bin = ctx.scratch / "fxbin" / fx.package
        os.environ[ctx.fx_env] = str(ctx.fx_bin)
    else:
        ctx.fx_error = err


# ---- the import and the replicator on the fixture --------------------------------------------------


def import_fx(ctx: Ctx, out_dir: Path, *, lazy_build: bool = False) -> dict[str, Any]:
    """The rules-first import of the fixture by the CLI (P1.import_s2 for another package)."""
    shutil.rmtree(out_dir, ignore_errors=True)
    env = ctx.env()
    if lazy_build:
        env.pop(ctx.fx_env, None)
    rc, out, err, wall = P1.run([ctx.py, "-m", "lattice.importer", "tests/spec", "--package",
                                 ctx.fx.package, "--rules-only", "--out", str(out_dir)], env=env)  # fmt: skip
    return {"rc": rc, "stdout": out[-600:], "stderr": err[-1500:], "wall_s": wall, "dir": out_dir}


def machinery(ctx: Ctx) -> dict[str, Any]:
    """What ``rules.import_rules`` builds around the probes for the fixture (front end, factory,
    runtime, the profile patch of E3/E5, seed, callers, unreachable routes, and the marks it applies
    to the verdicts), rebuilt here so the imported candidate spec can be probed again. It repeats
    import_rules' wiring and is a probe-side copy by design (as P1.s2_machinery)."""
    if "m" in ctx.cache:
        return ctx.cache["m"]  # type: ignore[no-any-return]
    from lattice.importer import go_rules, rules
    from lattice.importer.frontends import select
    from lattice.importer.lift import lift_repo

    sys.path.insert(0, str(REPO))
    root, package = SPEC_DIR, ctx.fx.package
    fe = go_rules.InferringFrontEnd(select("gin", root, package))
    factory, name = go_rules.harness_factory(root, package)  # type: ignore[misc]
    model = fe.extract(root, package)
    runtime = rules._runtime(fe, root, package, model)
    result_ = lift_repo(root, package, factory, only=(), frontend=fe, runtime=runtime)
    go_rules.patch_profile(result_, fe.private, fe.validation_status)
    c = result_.context
    api = result_.lattice_profile.api
    seeder = rules.Seeder(result_.model, c.inputs.tables, c.inputs.auth, c.inputs.insert_hook,
                          runtime, factory, api.uuid_storage, api.datetime_storage)  # fmt: skip
    unreachable = {f"{e.method} {e.path}": e.reason for e in result_.model.excluded_routes
                   if e.kind == "unreachable"}  # fmt: skip
    for ep, why in rules.network_routes(root, package, result_.model).items():
        unreachable.setdefault(ep, why)
    for ep, why in rules.shadowed_routes(result_.model).items():
        unreachable.setdefault(ep, why)
    unclear = {t: result_.profile.ownership_reasons.get(t, "")
               for t, d in result_.profile.ownership.items() if d == "unclear"}  # fmt: skip
    m = {"result": result_, "seeder": seeder, "sessions": rules._sessions(runtime, factory),
         "unreachable": unreachable, "rules": rules, "unclear": unclear,
         "outside": rules.outside_api(result_.model),
         "uploads": rules.upload_routes(result_.model)}  # fmt: skip
    ctx.cache["m"] = m
    return m


def r_on_spec(ctx: Ctx, spec: Any, variant: str, only: list[str] | None = None) -> Any:
    """Layer R of ``spec`` on the fixture, then the three marks the import applies."""
    from lattice.spec.probes import run_probes

    m = machinery(ctx)
    seeder, result_, rules = m["seeder"], m["result"], m["rules"]
    who = rules.callers(result_, seeder, variant)
    kw = dict(login=who.login, mint_token=who.mint, scheme=who.scheme, refusals=who.refusals,
              login_route=who.login_route, auth_headers=who.headers,
              inactive_login=who.inactive_login, unreachable=m["unreachable"], variant=variant,
              auth_present=who.present)  # fmt: skip
    if only:
        kw["only"] = only
    rr = run_probes(replace(spec, users=seeder.credentials(variant)), m["sessions"],
                    seeder.make_db, **kw)  # fmt: skip
    rules.mark_unclear(spec, rr, m["unclear"])
    rules.mark_outside(spec, rr, m["outside"])
    rules.mark_uploads(rr, m["uploads"])
    return rr


# ---- helpers on the front end and the policy extract ------------------------------------------------


def front(ctx: Ctx) -> Any:
    if "fe" not in ctx.cache:
        ctx.cache["fe"] = P2.front(SPEC_DIR, ctx.fx.package)
    return ctx.cache["fe"]


def pol(ctx: Ctx) -> dict[str, Any]:
    if "pol" not in ctx.cache:
        ctx.cache["pol"] = P2.pol(ctx.fx.module_dir)
    return ctx.cache["pol"]  # type: ignore[no-any-return]


def edge_pairs(spec_object: Mapping[str, Any]) -> list[list[str]]:
    return [list(e) for e in spec_object.get("edges", ())]


# ---- scoring functions (pure: the unit tests feed them synthetic inputs) ----------------------------


def score_pi1(framework: str, routes: Mapping[tuple[str, str], str], n_contracts: int,
              tables: set[str]) -> bool:  # fmt: skip
    return (framework == "gin" and dict(routes) == T_S3_ROUTES and n_contracts == 11
            and tables == T_S3_TABLES)  # fmt: skip


def score_pi2(inferences: Sequence[Mapping[str, Any]], hows: Mapping[tuple[str, int], str],
              owners: Mapping[str, str | None]) -> tuple[bool, dict[str, Any]]:  # fmt: skip
    """PI2-3: ``Ticket: reporter_id`` with 7 evidence lines (1 assign, 6 compare); ``Handoff`` unclear,
    naming both columns; ``Queue`` shared, ``User`` not owned; 10 inferences by rule."""
    counts: dict[str, int] = {}
    for i in inferences:
        counts[i["rule"]] = counts.get(i["rule"], 0) + 1
    clear = [i for i in inferences if i.get("rule") == "owner_uses" and not i.get("unclear")]
    unclear = [i for i in inferences if i.get("rule") == "owner_uses" and i.get("unclear")]
    ticket = [i for i in clear if (i.get("table"), i.get("column")) == T_S3_TICKET_OWNER]
    ev = ticket[0]["evidence"] if len(ticket) == 1 else []
    got = sorted(hows.get((f, int(n)), "?") for f, n in
                 (e.removeprefix("code:").rsplit(":", 1) for e in ev))  # fmt: skip
    handoff = (len(unclear) == 1 and unclear[0].get("table") == "Handoff"
               and set(unclear[0].get("columns") or ()) == T_S3_HANDOFF_COLUMNS)  # fmt: skip
    bind = [i for i in inferences if i.get("rule") == "bind_status"]
    ok = (len(clear) == 1 and len(ticket) == 1 and len(ev) == 7 and got == sorted(T_S3_TICKET_HOWS)
          and handoff and owners.get("Ticket") == "reporter_id" and owners.get("Queue") is None
          and owners.get("User") is None and len(inferences) == 10 and counts == T_S3_INFERENCES
          and len(bind) == 1 and bind[0].get("status") == 422 and not bind[0].get("unclear"))  # fmt: skip
    return ok, {"inferences": len(inferences), "by_rule": counts, "ticket_evidence": len(ev),
                "how": got, "handoff_unclear_both_columns": handoff, "owners": dict(owners)}  # fmt: skip


def score_pi3(items: Mapping[str, Any]) -> dict[str, bool]:
    n = len(items)
    verified = sum(1 for v in items.values() if v["verdict"] in VERIFIED)
    share = verified / n if n else 0.0
    refuted = sum(1 for v in items.values() if v["verdict"] == "refuted")
    return {"items": 50 <= n <= 80, "share": 0.60 <= share <= 0.85, "refuted": refuted <= 3}


def proposal_machine(props: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The machine proposals about ``tickets.status`` and which edges they state."""
    targeted = []
    for p in props:
        so = p.get("spec_object") or {}
        if (p.get("kind") == "machine" and so.get("type") == "Machine"
                and so.get("table") in ("Ticket", "tickets") and so.get("column") == "status"):  # fmt: skip
            targeted.append(edge_pairs(so))
    edges = [e for es in targeted for e in es]
    return {"machines": len(targeted), "open_to_closed": ["open", "closed"] in edges,
            "closed_to_open": ["closed", "open"] in edges, "edges": edges}  # fmt: skip


def score_pi5(props: Sequence[Mapping[str, Any]]) -> tuple[bool, dict[str, Any]]:
    m = proposal_machine(props)
    pending = all(p.get("status") == "pending" for p in props)
    return bool(m["open_to_closed"]) and pending, {
        "count": len(props), "kinds": sorted({str(p.get("kind")) for p in props}),
        "machine_tickets_status": m, "all_pending": pending,
        "in_predicted_range": 2 <= len(props) <= 8,
        "sentences": [p.get("sentence") for p in props]}  # fmt: skip


def score_pa1(got: Mapping[str, Any], baseline_equals_run: bool) -> bool:
    return (got["tables"] == 4 and got["users_table"] == "User"
            and got["owners"] == {"Ticket": "reporter_id"}
            and got["private"] == {"User": ["password_hash"]} and got["admin_routes"] == []
            and got["machines"] == {} and got["baseline"] == [] and baseline_equals_run)  # fmt: skip


PA3_EXPECT = {  # mutant -> (code, key)
    "JM31": ("GO801", "GO801|POST /tickets/:tid/close|Ticket"),
    "JM32": ("GO802", "GO802|GET /me|User.password_hash"),
}


def score_pa3(outside: Sequence[str], new: Mapping[str, Sequence[Mapping[str, str]]]) -> tuple[bool, dict[str, bool]]:  # fmt: skip
    """The original has 0 findings outside its baseline; each mutant's new findings contain its
    (code, key). Further new findings are reported, not gated (the PA3 precedent)."""
    hit = {mid: any((x["code"], x["key"]) == want for x in new.get(mid, ()))
           for mid, want in PA3_EXPECT.items()}  # fmt: skip
    return not outside and all(hit.values()), hit


def score_p0_parse(extract_pairs: Sequence[tuple[str, str]], model_pairs: Sequence[tuple[str, str]],
                   n_inferences: int) -> bool:  # fmt: skip
    return (sorted(extract_pairs) == T_S3_PARSED and sorted(model_pairs) == T_S3_PARSED
            and n_inferences == 5)  # fmt: skip


def score_p0_omit(page_size: tuple[Any, Any]) -> bool:
    return tuple(page_size) == (0, 100)


def score_p0_bind(validation_status: int | None, bind: Sequence[Mapping[str, Any]]) -> bool:
    return (validation_status == 422 and len(bind) == 1 and bind[0].get("status") == 422
            and not bind[0].get("unclear"))  # fmt: skip


def score_p0_choices(got: Mapping[str, Sequence[str]]) -> bool:
    return {k: list(v) for k, v in got.items()} == T_S3_CHOICES


def score_p0_priv(private: Mapping[str, Sequence[str]]) -> bool:
    return {k: list(v) for k, v in private.items()} == T_S3_PRIVATE


def score_px(prebuilt_import_s: float | None) -> bool:
    return prebuilt_import_s is not None and prebuilt_import_s <= 180


# ---- Go source editing for the mutants (JM31-JM34): structural, on scratch copies ------------------

BRACKETS = {"(": ")", "[": "]", "{": "}"}


def skip_token(t: str, i: int) -> int:
    """The index after the string, rune, raw string or comment that starts at ``i`` (``i`` if none)."""
    c = t[i]
    if c in "\"'":
        j = i + 1
        while j < len(t):
            if t[j] == "\\":
                j += 2
                continue
            if t[j] == c:
                return j + 1
            j += 1
        return len(t)
    if c == "`":
        j = t.find("`", i + 1)
        return len(t) if j < 0 else j + 1
    if t.startswith("//", i):
        j = t.find("\n", i)
        return len(t) if j < 0 else j
    if t.startswith("/*", i):
        j = t.find("*/", i + 2)
        return len(t) if j < 0 else j + 2
    return i


def match_close(t: str, i: int) -> int:
    """The index of the bracket that closes the one at ``t[i]``."""
    stack: list[str] = []
    j = i
    while j < len(t):
        k = skip_token(t, j)
        if k != j:
            j = k
            continue
        c = t[j]
        if c in BRACKETS:
            stack.append(BRACKETS[c])
        elif c in ")]}":
            if not stack or stack.pop() != c:
                raise ValueError(f"unbalanced {c!r} at {j}")
            if not stack:
                return j
        j += 1
    raise ValueError(f"no closing bracket for {t[i]!r} at {i}")


def split_args(t: str, open_i: int, close_i: int) -> list[tuple[int, int]]:
    """(start, end) of each top-level comma-separated argument between ``t[open_i]`` and ``t[close_i]``."""
    out, depth, start, j = [], 0, open_i + 1, open_i + 1
    while j < close_i:
        k = skip_token(t, j)
        if k != j:
            j = k
            continue
        c = t[j]
        if c in BRACKETS:
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            out.append((start, j))
            start = j + 1
        j += 1
    if t[start:close_i].strip():
        out.append((start, close_i))
    return out


REG = re.compile(r"(\w+)\s*\.\s*(GET|POST|PUT|PATCH|DELETE)\s*\(")
GRP = re.compile(r'(\w+)\s*:?=\s*(\w+)\s*\.\s*Group\s*\(\s*"([^"]*)"')


def group_prefix(src: str, var: str, seen: tuple[str, ...] = ()) -> str:
    if var in seen:
        return ""
    for m in GRP.finditer(src):
        if m.group(1) == var:
            return group_prefix(src, m.group(2), (*seen, var)) + m.group(3)
    return ""


def routes_of(src: str) -> list[tuple[str, str, int, int]]:
    """(method, full path, index of the call's ``(``, index of its ``)``) of every ``x.VERB("p", ..)``."""
    out = []
    for m in REG.finditer(src):
        op = m.end() - 1
        try:
            cl = match_close(src, op)
        except ValueError:
            continue
        args = split_args(src, op, cl)
        if not args:
            continue
        lit = src[args[0][0] : args[0][1]].strip()
        if not (lit.startswith('"') and lit.endswith('"')):
            continue
        path = (group_prefix(src, m.group(1)) + lit[1:-1]).replace("//", "/") or "/"
        out.append((m.group(2), path, op, cl))
    return out


def handler_body(sources: Mapping[str, str], method: str, path: str) -> tuple[str, int, int]:
    """``(file, lbrace, rbrace)`` of the handler registered for ``method path``: an inline ``func``
    literal, or the named function (possibly called, ``h(db)``, or a method value ``s.h``)."""
    hits = [(f, s, op, cl) for f, s in sources.items() for m, p, op, cl in routes_of(s)
            if (m, p) == (method, path)]  # fmt: skip
    if len(hits) != 1:
        raise LookupError(f"{method} {path}: {len(hits)} registrations")
    f, src, op, cl = hits[0]
    args = split_args(src, op, cl)
    h = src[args[-1][0] : args[-1][1]].strip()
    if h.startswith("func"):
        start = src.index(h, args[-1][0])
        po = src.index("(", start)
        lb = src.index("{", match_close(src, po))
        return f, lb, match_close(src, lb)
    name = h.split("(")[0].split(".")[-1].strip()
    defs = [(g, m) for g, s in sources.items()
            for m in re.finditer(rf"func\s*(?:\([^)]*\)\s*)?{re.escape(name)}\s*\(", s)]  # fmt: skip
    if len(defs) != 1:
        raise LookupError(f"{method} {path}: handler {name!r} has {len(defs)} definitions")
    g, m = defs[0]
    s = sources[g]
    lb = s.index("{", match_close(s, m.end() - 1))
    return g, lb, match_close(s, lb)


def drop_owner_clause(body: str, column: str) -> str:
    """``body`` with the first ``.Where("... AND <column> = ?", ..., owner)`` clause for ``column``
    removed (its placeholder's argument too); a ``Where`` that is only that clause is removed whole."""
    clause = rf"(?:\w+\.)?{re.escape(column)}\s*=\s*\?"
    for m in re.finditer(r"\.\s*Where\s*\(", body):
        op = m.end() - 1
        cl = match_close(body, op)
        args = split_args(body, op, cl)
        if not args:
            continue
        a0, a1 = args[0]
        lit = body[a0:a1].strip()
        if not (lit[:1] in ('"', "`") and re.search(clause, lit)):
            continue
        quote, inner = lit[0], lit[1:-1]
        if re.fullmatch(rf"\s*{clause}\s*", inner):
            return body[: m.start()] + body[cl + 1 :]
        tail = re.search(rf"\s+AND\s+{clause}", inner, re.I)
        head = re.search(rf"{clause}\s+AND\s+", inner, re.I)
        found = tail or head
        if found is None:
            continue
        k = inner[: found.start()].count("?")  # the placeholder's index
        new_lit = quote + inner[: found.start()] + inner[found.end() :] + quote
        parts = [body[a:b].strip() for a, b in args]
        parts[0] = new_lit
        if 1 + k < len(parts):
            del parts[1 + k]
        return body[: op + 1] + ", ".join(parts) + body[cl:]
    raise ValueError(f"no Where clause on {column}")


def swap_status(body: str, old: str, new: str) -> str:
    """The first ``http.StatusX`` / numeric ``old`` status after the first ``First(``/``Take(``/``Find(``."""
    names = {"404": "http.StatusNotFound", "403": "http.StatusForbidden"}
    m = re.search(r"\.(First|Take|Last)\s*\(", body)
    start = m.end() if m else 0
    pat = re.compile(rf"{re.escape(names[old])}\b|\b{old}\b")
    hit = pat.search(body, start) or pat.search(body)
    if hit is None:
        raise ValueError(f"no {old} status in the handler")
    rep = names[new] if hit.group(0) == names[old] else new
    return body[: hit.start()] + rep + body[hit.end() :]


def skip_conflict_check(body: str) -> str:
    """The ``if`` whose branch answers 409 can no longer be taken: ``if C {`` -> ``if false && (C) {``."""
    m = re.search(r"http\.StatusConflict\b|\b409\b", body)
    if m is None:
        raise ValueError("no 409 in the handler")
    cands = list(re.finditer(r"(?m)^[ \t]*(?:\}\s*else\s+)?if\b", body[: m.start()]))
    if not cands:
        raise ValueError("no `if` before the 409")
    ifm = cands[-1]
    kw = body.index("if", ifm.start())
    lb = body.index("{", kw)
    cond = body[kw + 2 : lb]
    if ";" in cond:
        init, c = cond.rsplit(";", 1)
        new = f"{init};" + f" false && ({c.strip()}) "
    else:
        new = f" false && ({cond.strip()}) "
    return body[: kw + 2] + new + body[lb:]


def drop_json_dash(src: str, field: str, column: str) -> str:
    """The struct field line of ``field`` / ``column`` loses its ``json:"-"`` tag element."""
    out, n = [], 0
    for ln in src.split("\n"):
        if re.search(rf"\b{re.escape(field)}\b|{re.escape(column)}", ln) and 'json:"-"' in ln:
            tag = re.search(r"`([^`]*)`", ln)
            if tag is not None:
                rest = " ".join(x for x in tag.group(1).split() if x != 'json:"-"')
                ln = ln[: tag.start()].rstrip() + (f" `{rest}`" if rest else "") + ln[tag.end() :]
                n += 1
        out.append(ln)
    if n != 1:
        raise ValueError(f'{field}: the json:"-" tag matched {n} lines')
    return "\n".join(out)


# mutant -> what it does; ``op`` names the edit above, the rest are its arguments
MUTANTS: dict[str, dict[str, Any]] = {
    "JM31": {
        "what": "gotickets copy: close drops AND reporter_id = ?",
        "op": "drop_owner",
        "method": "POST",
        "path": "/tickets/:tid/close",
        "column": "reporter_id",
    },
    "JM32": {
        "what": 'gotickets copy: password_hash loses json:"-"',
        "op": "drop_json_dash",
        "field": "PasswordHash",
        "column": "password_hash",
    },
    "JM33": {
        "what": "gotickets copy: get answers 403 for another caller's ticket",
        "op": "swap_status",
        "method": "GET",
        "path": "/tickets/:tid",
    },
    "JM34": {
        "what": "gotickets copy: reopen accepts an open ticket (no 409)",
        "op": "skip_409",
        "method": "POST",
        "path": "/tickets/:tid/reopen",
    },
}
EDIT_OVERRIDE: dict[str, list[dict[str, str]]] = {}  # --edits FILE: literal edits, by mutant id


def load_sources(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): p.read_text() for p in sorted(root.rglob("*.go"))}


def apply_mutant(root: Path, mid: str) -> list[str]:
    """Edit the Go sources under ``root`` (a scratch copy) for mutant ``mid``; the files changed.
    Raises when the edit does not apply exactly once (the mutant is then NOT RUN, never guessed)."""
    if mid in EDIT_OVERRIDE:
        changed = []
        for e in EDIT_OVERRIDE[mid]:
            f = root / e["file"]
            text = f.read_text()
            if text.count(e["old"]) != 1:
                raise ValueError(f"{e['file']}: the edit matches {text.count(e['old'])} times")
            f.write_text(text.replace(e["old"], e["new"], 1))
            changed.append(e["file"])
        return changed
    spec = MUTANTS[mid]
    src = load_sources(root)
    op = spec["op"]
    if op == "drop_json_dash":
        for f in (f for f, s in src.items() if 'json:"-"' in s):
            try:
                (root / f).write_text(drop_json_dash(src[f], spec["field"], spec["column"]))
                return [f]
            except ValueError:
                continue
        raise ValueError(f'{mid}: no struct field {spec["field"]} with json:"-"')
    f, lb, rb = handler_body(src, spec["method"], spec["path"])
    body = src[f][lb : rb + 1]
    if op == "drop_owner":
        new = drop_owner_clause(body, spec["column"])
    elif op == "swap_status":
        new = swap_status(body, "404", "403")
    elif op == "skip_409":
        new = skip_conflict_check(body)
    else:
        raise ValueError(f"unknown op {op}")
    (root / f).write_text(src[f][:lb] + new + src[f][rb + 1 :])
    return [f]


def mutate_fixture(ctx: Ctx, mid: str) -> Path:
    """A scratch copy of the fixture with mutant ``mid`` applied (the fixture is never edited)."""
    dest = ctx.scratch / "mut" / mid.lower() / ctx.fx.package
    shutil.rmtree(dest.parent, ignore_errors=True)
    shutil.copytree(ctx.fx.module_dir, dest)
    apply_mutant(dest, mid)
    return dest


def layer_a_new(cand: Any, fx: Fx, copy: Path) -> dict[str, Any]:
    """Layer A on ``copy`` under ``go_spec_policy`` of the candidate spec, against the original."""
    from lattice.gocheck.derive import derive_go_policy, go_spec_policy

    derived = derive_go_policy(fx.module_dir)
    policy = go_spec_policy(cand.SPEC, fx.module_dir, derived)
    orig = P1.findings_of(fx.module_dir, policy)
    outside = [f for f in orig if f.key not in derived.baseline]
    sig = {(f.code, f.message) for f in orig}
    new = [{"code": f.code, "key": f.key, "message": f.message[:160]}
           for f in P1.findings_of(copy, policy) if (f.code, f.message) not in sig]  # fmt: skip
    return {"original_total": len(orig), "outside": [f.key for f in outside], "new": new}


# ---- the S3 rows ------------------------------------------------------------------------------------


def probe_pi1(ctx: Ctx, d: Path) -> dict[str, Any]:
    rep, cand = P2.report_of(d), P1.load_candidate(d)
    routes = {(c.method, c.path): c.auth for c in cand.CONTRACTS}
    tables = set(cand.PROFILE.api.table_names.values())
    ok = score_pi1(rep["rules"]["framework"], routes, len(cand.CONTRACTS), tables)
    return result("PI1-3", "rules-first import of S3: framework, routes, contracts, tables",
                  {"framework": "gin", "routes": 11, "contracts": 11, "tables": 4,
                   "public": ["GET /health", "POST /login", "GET /queues"], "others": "Customer (8)"},
                  {"framework": rep["rules"]["framework"], "routes": len(routes),
                   "contracts": len(cand.CONTRACTS), "tables": sorted(tables),
                   "auth": {f"{m} {p}": a for (m, p), a in routes.items()}}, ok,
                  reading="exact against T-S3 section 4 (the authenticated role is `Customer`)")  # fmt: skip


def probe_pi2(ctx: Ctx, d: Path) -> dict[str, Any]:
    rep, cand = P2.report_of(d), P1.load_candidate(d)
    inf = rep["rules"].get("inferences", [])
    hows = {(o["file"], o["line"]): o["how"] for r in pol(ctx)["routes"] for o in r["owner_uses"]
            if (o["model"], o["column"]) == T_S3_TICKET_OWNER}  # fmt: skip
    tables = cand.PROFILE.api.tables
    owners = {t: tables[t].owner for t in tables}
    ok, ev = score_pi2(inf, hows, owners)
    return result("PI2-3", "owners and inferences[] of the S3 import",
                  {"owner": "Ticket: reporter_id, 7 evidence lines (1 assign, 6 compare)",
                   "Handoff": "unclear, naming from_user_id and to_user_id",
                   "Queue": "shared", "User": "not owned",
                   "inferences": {"owner_uses": "2 (1 unclear)", "path_parse": 5, "bind_status": "1 (422)",
                                  "status_facts": 1, "json_dash": 1, "total": 10}},
                  ev, ok, reading="the profile owner of Handoff is reported (observed.owners), not gated: "
                  "section 5.4 states it only as `unclear` in the inference entry")  # fmt: skip


def probe_pi3(ctx: Ctx, d: Path) -> dict[str, Any]:
    row = P1.probe_pi3(ctx, d)
    rep = P2.report_of(d)
    row["id"] = "PI3-3"
    row["probe"] = "rule items of the S3 import on the original (seed)"
    row["predicted"] = {"items": "50-80", "verified_share": "0.60-0.85", "refuted": "0-3",
                        "gate": "share >= 0.60; every refuted item has a correction or a baseline "
                        "entry with its reason; every unexercised item has a reason"}  # fmt: skip
    row["evidence"]["in_predicted_range"] = score_pi3(rep["rules"]["items"])
    row["evidence"]["unexercised"] = {i: v["reason"] for i, v in rep["rules"]["items"].items()
                                      if v["verdict"] == "unexercised"}  # fmt: skip
    row["evidence"]["corrections"] = len(rep["rules"]["corrections"])
    return row


def probe_pi4(ctx: Ctx, d: Path) -> dict[str, Any]:
    cand = P1.load_candidate(d)
    seed = P1.verdict_map(r_on_spec(ctx, cand.SPEC, "seed"))
    v2 = P1.verdict_map(r_on_spec(ctx, cand.SPEC, "variant2"))
    base = [i for i, v in seed.items() if v in VERIFIED]
    still = [i for i in base if v2.get(i) in VERIFIED]
    frac = len(still) / len(base) if base else 0.0
    lost = {i: v2.get(i) for i in base if i not in still}
    return result("PI4-3", "the corrected S3 spec re-probed on variant2",
                  "every seed-verified item still verified (>= 0.95)",
                  {"seed_verified": len(base), "still_verified": len(still),
                   "fraction": round(frac, 4), "lost": lost}, frac >= 0.95)  # fmt: skip


def probe_pi5(ctx: Ctx, d: Path) -> dict[str, Any]:
    props = json.loads((d / "proposals.json").read_text())
    ok, ev = score_pi5(props)
    return result("PI5-3", "proposals.json (mining) of the S3 import",
                  "2-8 proposals including the machine tickets.status open -> closed and closed -> open",
                  ev, ok, reading="pass: `open -> closed` is among the edges of a Machine proposal on "
                  "Ticket.status and every proposal is pending; `closed -> open` and the 2-8 range are "
                  "reported")  # fmt: skip


def probe_pi6(ctx: Ctx, d: Path) -> dict[str, Any]:
    row = P2.probe_pi6(ctx, d)
    row["id"] = "PI6-3"
    row["probe"] = "owner review of the S3 import: python -m lattice.importer review <dir> list"
    return row


def probe_pi7(ctx: Ctx, n: int) -> dict[str, Any]:
    trees, walls = [], []
    for i in range(n):
        d = ctx.scratch / "imp7" / f"r{i}"
        r = import_fx(ctx, d)
        walls.append(round(r["wall_s"], 2))
        trees.append(P1.normalised_tree(d) if r["rc"] == 0 else {"error": r["stderr"][-200:]})
    first = trees[0]
    same = sum(1 for t in trees if t == first)
    diff = sorted({k for t in trees for k in set(t) | set(first) if t.get(k) != first.get(k)})
    ctx.timings["pi7_import_walls"] = sum(walls) / len(walls)
    return result("PI7-3", "the S3 import 10x into separate scratch dirs",
                  "byte-identical after the timing normaliser",
                  {"identical": same, "of": n, "differing_files": diff, "walls_s": walls},
                  same == n, reading="the J1 normaliser: report.json cost.wall_s and rules.timing "
                  "replaced; report.md left out")  # fmt: skip


def probe_pr3(ctx: Ctx, d: Path) -> dict[str, Any]:
    cand, rep = P1.load_candidate(d), P2.report_of(d)
    mine = P1.verdict_map(r_on_spec(ctx, cand.SPEC, "seed"))
    cmap = P2.correction_map(rep)
    theirs = {cmap.get(i, i): v["verdict"] for i, v in rep["rules"]["items"].items()}
    diff = {i: [theirs.get(i), mine.get(i)] for i in sorted(set(theirs) | set(mine))
            if theirs.get(i) != mine.get(i)}  # fmt: skip
    return result("PR3-3", "layer R on S3 with the imported candidate_spec.py",
                  "the same item verdicts as the import's final verdicts (after the correction map)",
                  {"items_import": len(theirs), "items_rerun": len(mine),
                   "corrections": len(cmap), "differences [import, rerun]": diff}, not diff,
                  reading="literal verdict strings; the import's item ids are mapped through "
                  "corrections[] first; the rerun applies the marks the import applies "
                  "(unclear owner, outside API, uploads) so that its verdicts are the import's "
                  "final ones")  # fmt: skip


def probe_pa1(ctx: Ctx) -> dict[str, Any]:
    from lattice.gocheck.derive import derive_go_policy

    p = derive_go_policy(ctx.fx.module_dir)
    keys = P1.run_gocheck_keys(ctx.fx.module_dir, p)
    got = {"tables": len(p.tables), "users_table": p.users_table, "owners": dict(p.owners),
           "private": {k: list(v) for k, v in p.private.items()},
           "admin_routes": [list(r) for r in p.admin_routes], "machines": dict(p.machines),
           "baseline": sorted(p.baseline)}  # fmt: skip
    base_ok = set(p.baseline) == keys
    return result("PA1-3", "derive_go_policy on S3",
                  {"tables": 4, "users_table": "User", "owners": {"Ticket": "reporter_id"},
                   "private": {"User": ["password_hash"]}, "admin_routes": [], "machines": {},
                   "baseline": []}, got, score_pa1(got, base_ok),
                  baseline_equals_separate_run=base_ok)  # fmt: skip


def probe_pa3(ctx: Ctx, d: Path) -> dict[str, Any]:
    cand = P1.load_candidate(d)
    rows: dict[str, Any] = {}
    outside: list[str] = []
    total = 0
    for mid in PA3_EXPECT:
        try:
            copy = mutate_fixture(ctx, mid)
        except (LookupError, ValueError) as err:  # the edit does not apply: never guessed
            return not_run("PA3-3", "JM31 and JM32 on scratch copies of S3",
                           f"{mid}: the structural edit does not apply ({type(err).__name__}: "
                           f"{err}); supply literal edits with go_parity_s3_mutants.py --edits")  # fmt: skip
        a = layer_a_new(cand, ctx.fx, copy)
        rows[mid] = a["new"]
        outside, total = a["outside"], a["original_total"]
    ok, hit = score_pa3(outside, rows)
    return result("PA3-3", "JM31 and JM32 on scratch copies of S3 under go_spec_policy of its candidate spec",
                  {"JM31": "GO801 on POST /tickets/:tid/close Ticket",
                   "JM32": "GO802 on GET /me User.password_hash", "original_outside_baseline": 0},
                  {"original_total": total, "original_outside_baseline": len(outside),
                   "original_outside": outside, "new_findings": rows, "expected_found": hit}, ok,
                  reading="a mutant's finding is new when its (code, message) is not among the "
                  "original's; the expected (code, key) must be among them; further new findings are "
                  "reported, not gated (the PA3 precedent)")  # fmt: skip


# ---- the S3 sub-claims of arm 0 ---------------------------------------------------------------------


def sub_parse(ctx: Ctx) -> dict[str, Any]:
    fe = front(ctx)
    ext = sorted((f"{k}", p["name"]) for k, r in P2.route_map(pol(ctx)).items()
                 for p in r["params"] if p.get("parse") == "Int")  # fmt: skip
    model = sorted(P2.parsed_params(fe))
    n = len([i for i in fe.inferences if i["rule"] == "path_parse"])
    return result("P0-PARSE:S3", "E1 on S3 (extract facts and the model)",
                  {"S3": "qid, tid x4 (5 params)"}, {"extract_Int": ext, "model_Int": model,
                                                     "inferences": n},
                  score_p0_parse(ext, model, n))  # fmt: skip


def sub_omit(ctx: Ctx) -> dict[str, Any]:
    r = P2.route_map(pol(ctx)).get("GET /tickets")
    prm = next((p for p in (r or {}).get("params", ()) if p["name"] == "page_size"), None)
    got = (prm["ge"], prm["le"]) if prm else None
    return result("P0-OMIT:S3", "E2 on S3 page_size", {"S3": "ge 0/le 100"},
                  {"page_size": got, "unread": (r or {}).get("unread")},
                  got is not None and score_p0_omit(got))  # fmt: skip


def sub_bind(ctx: Ctx) -> dict[str, Any]:
    fe = front(ctx)
    bind = [i for i in fe.inferences if i["rule"] == "bind_status"]
    return result("P0-BIND:S3", "E3 on S3", {"S3": 422},
                  {"validation_status": fe.validation_status, "bind_status": bind},
                  score_p0_bind(fe.validation_status, bind))  # fmt: skip


def sub_choices(ctx: Ctx) -> dict[str, Any]:
    fe = front(ctx)
    got = {f"{t.name}.{c.name}": list(c.choices) for t in fe.model.tables for c in t.columns
           if c.choices}  # fmt: skip
    return result(
        "P0-CHOICES:S3",
        "E4 on S3: choices from status_facts",
        {"S3": {"Ticket.status": ["open", "closed"]}},
        got,
        score_p0_choices(got),
    )


def sub_priv(ctx: Ctx) -> dict[str, Any]:
    priv = front(ctx).private
    return result("P0-PRIV:S3", 'E5 on S3 (json:"-")', {"S3": {"User": ["password_hash"]}}, priv,
                  score_p0_priv(priv))  # fmt: skip


def sub_g805(ctx: Ctx) -> dict[str, Any]:
    from lattice.gocheck import GO_GATED, gated_note

    base = ctx.scratch / "base"
    rc, out, _, _ = P1.run([ctx.py, "-c", "import json;from lattice.gocheck import GO_GATED,"
                            "gated_note;print(json.dumps([gated_note(),GO_GATED],sort_keys=True))"],
                           cwd=base, env=ctx.env(PYTHONPATH=str(base)))  # fmt: skip
    same = rc == 0 and out.strip() == json.dumps([gated_note(), GO_GATED], sort_keys=True)
    count = P1.pairing_count(pol(ctx))
    return result("PG805:S3", "GO805 gated text byte-identical; pairing count on S3 (informational)",
                  {"gated": "byte-identical", "S3": "reported"},
                  {"identical": same, "pairing_count_informational": {"S3": count}}, same,
                  reading="the S3 sub-claim is that the count is reported; the identity check repeats "
                  "the frozen row's")  # fmt: skip


def sub_px(ctx: Ctx) -> dict[str, Any]:
    t = ctx.timings
    pre = t.get("s3_import_prebuilt_s")
    got = {"s3_import_prebuilt_s": pre, "s3_import_pi7_mean_s": t.get("pi7_import_walls"),
           "s3_import_with_go_build_s": t.get("s3_import_lazy_build_s"),
           "build_fixture_s": t.get("build_fixture_s")}  # fmt: skip
    return result("PX:S3", "wall time of the S3 import",
                  {"s3_import": "8-40 s (binary prebuilt; the cold build is measured separately)",
                   "gate": "<= 180 s"},
                  {k: (round(v, 2) if v is not None else None) for k, v in got.items()},
                  score_px(pre), reading="the gate is the prebuilt import of the first run (r0); the "
                  "cold-build import and the PI7-3 mean are reported")  # fmt: skip


# ---- main -----------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--arm", default="S3", choices=["S3"], help="only S3 is scored here")
    ap.add_argument("--base", required=True, help="an existing `git archive 193f7f6`")
    ap.add_argument("--scratch", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="", help="comma-separated row ids")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument(
        "--fixture", default=DEFAULT_PACKAGE, help="stand-in only (a dir of tests/spec)"
    )
    ap.add_argument("--harness", default=DEFAULT_HARNESS, help="stand-in only (its harness module)")
    args = ap.parse_args(argv)
    freeze = verify_all(Path(__file__))  # before anything, and not optional
    fx = Fx(args.fixture, args.harness)
    out = Path(args.out).resolve()
    scratch = Path(args.scratch).resolve() if args.scratch else out.parent / "scratch-s3"
    ctx = Ctx(scratch, out, "193f7f6")
    setup(ctx, fx, Path(args.base).resolve())
    only = {x for x in args.only.split(",") if x}
    want = lambda pid: (not only or pid in only) and pid in ALL_IDS  # noqa: E731
    results: list[dict[str, Any]] = []

    def add(pid: str, fn: Callable[[], dict[str, Any]], *, needs_bin: bool = True) -> None:
        if not want(pid):
            return
        if not fx.module_dir.is_dir():
            results.append(not_run(pid, "S3", f"tests/spec/{fx.package} is absent"))
        elif needs_bin and ctx.fx_error:
            results.append(not_run(pid, "S3", f"S3 does not build: {ctx.fx_error[:400]}"))
        else:
            try:
                results.append(fn())
            except Exception as err:  # noqa: BLE001 -- a probe that cannot run is reported
                import traceback

                results.append(result(pid, "(the probe raised)", None,
                                      f"{type(err).__name__}: {err}", False,
                                      traceback=traceback.format_exc()[-1500:]))  # fmt: skip
        print(f"{pid}: pass={results[-1]['pass']}", flush=True)

    imp = ctx.scratch / "imp" / "r0"
    ran: dict[str, Any] = {}
    if not ctx.fx_error and any(want(p) for p in IMPORT_ROWS):
        t = import_fx(ctx, ctx.scratch / "imp" / "lazy", lazy_build=True)
        ctx.timings["s3_import_lazy_build_s"] = t["wall_s"]
        ran = import_fx(ctx, imp)
        ctx.timings["s3_import_prebuilt_s"] = ran["wall_s"]
        if ran["rc"] != 0:
            print("the S3 import failed:", ran["stderr"], file=sys.stderr)
    ok_imp = bool(ran) and ran["rc"] == 0
    for pid, fn in (("PI1-3", probe_pi1), ("PI2-3", probe_pi2), ("PI3-3", probe_pi3),
                    ("PI4-3", probe_pi4), ("PI5-3", probe_pi5), ("PI6-3", probe_pi6),
                    ("PR3-3", probe_pr3), ("PA3-3", probe_pa3)):  # fmt: skip
        if ok_imp or ctx.fx_error:
            add(pid, lambda fn=fn: fn(ctx, imp))
        else:
            add(pid, lambda pid=pid: not_run(pid, "S3 import", "the S3 import did not run: "
                                             + ran.get("stderr", "")[-300:]))  # fmt: skip
    add("PI7-3", lambda: probe_pi7(ctx, args.runs))
    add("PA1-3", lambda: probe_pa1(ctx), needs_bin=False)
    for pid, fn in (("P0-PARSE:S3", sub_parse), ("P0-OMIT:S3", sub_omit), ("P0-BIND:S3", sub_bind),
                    ("P0-CHOICES:S3", sub_choices), ("P0-PRIV:S3", sub_priv),
                    ("PG805:S3", sub_g805)):  # fmt: skip
        add(pid, lambda fn=fn: fn(ctx), needs_bin=False)
    add("PX:S3", lambda: sub_px(ctx), needs_bin=False)
    changed = P1.snapshot_diff(ctx.snap0, P1.snapshots())
    calls = [ln for ln in ctx.shim_log.read_text().splitlines() if ln.strip()]
    doc = {"runner": "go_parity_s3", "fixture": fx.package, "harness": fx.harness,
           "stand_in": fx.stand_in, "base_ref": "193f7f6", "freeze": freeze,
           "binary_env": ctx.fx_env, "timings": ctx.timings, "fx_error": ctx.fx_error,
           "fixtures_changed": changed, "shim_calls": calls, "results": results}  # fmt: skip
    out.mkdir(parents=True, exist_ok=True)
    (out / "probes-s3.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")
    if (imp / "report.json").exists():
        for name in ("report.json", "candidate_spec.py", "profile.py", "proposals.json",
                     "go_policy.json"):  # fmt: skip
            if (imp / name).exists():
                shutil.copyfile(imp / name, out / f"s3_import_{name}")
    passed = [r["id"] for r in results if r["pass"] is True]
    failed = [r["id"] for r in results if r["pass"] is False]
    nr = [r["id"] for r in results if r["pass"] is None]
    print(
        f"passed {len(passed)}: {passed}\nfailed {len(failed)}: {failed}\nnot run {len(nr)}: {nr}"
    )
    print(f"fixtures changed: {changed}; docker/claude shim calls: {calls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
