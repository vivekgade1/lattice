"""Probes of Spike G (rationale notes and a business glossary): section 3 and Amendment 1 of
``docs/results/2026-10-16-rationale-glossary-prereg.md``.

The worlds: **S** Streak a0679e9 (``git archive``, read-only), **W** S plus the frozen fixture files
of Appendix B (5 approved notes, 2 proposals, 2 stale notes, a 2-term glossary), **K0..K4** S plus
the anchor fixtures of PR, **base** ``git archive 8931d46`` of this repository. The seeded lint set
(``dogfood/glossary_seeded.json``, Appendix A) and the agent world
(``dogfood/rationale_agent_world.json``, Appendix B) are hash-checked before anything runs.

Nothing here tunes a detector: a probe that misses is reported as a finding. The fixtures' anchors
and approval hashes are computed with this file's own ``hashlib`` code (D2), not the library's.

Run (see the pre-registration, section 6)::

    "$DRV" dogfood/rationale_probes.py --streak-ref a0679e9 --streak-ref2 1d00ede \
        --base 8931d46 --seeded dogfood/glossary_seeded.json \
        --world dogfood/rationale_agent_world.json --runs 10 \
        --workflows-dyn "$WF" --inv-s4stat "$S4STAT" --scratch "$RG/p" --out "$RG/p-data"

``--only PS,PR`` runs a subset; ``--reuse-world`` keeps the scratch worlds of an earlier run (the
mutant runner builds them once); ``--base-tree DIR`` uses an existing ``git archive`` of the base;
``--inv-s4stat DIR`` is round 1's frozen S4-stat inventory (a regenerated static run differs now).
``--child`` is the determinism child (internal: it reads a Roots JSON on stdin).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import random
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import implementation_probes as P1  # noqa: E402

REPO = P1.REPO
sys.path.insert(0, str(REPO))

SEEDED_SHA = "1f285734716d5f8c8f14e5df30a997f6674b39bf243f0c69b6adad662b175a10"
WORLD_SHA = "b5bb61a06a2cc2555da585db153ecc85c3457e3123c94c03e2e0173f3f354d5e"
S_TRACE_SHA = "627a2b3f4c9cfafc3ad6cd5edb01f355fb76b53c248c629e7a77fd1bd8ee82ed"
S_REQ_SHA = "dcac6deb3ad3ec4ab7381af040cb86064aae00eb3ed38c05237657156fbad7b1"
S_SPEC_SHA = "28dfc2e6c6a2aa27168079740d28a94de7f3efcb7cf8234d795d48b7960f5624"
OWNER_STREAK = Path("/Users/vivekgade/Documents/streak")
MAIN_CHECKOUT = Path("/Users/vivekgade/Documents/lattice")
STEM = "REQUIREMENTS"
RAT, PROP = f"{STEM}.rationale.json", f"{STEM}.rationale.proposed.json"
GLOS, GLOS_PROP = f"{STEM}.glossary.json", f"{STEM}.glossary.proposed.json"
PROBES = (
    "PS",
    "PB",
    "PBp",
    "PR",
    "PC",
    "PL",
    "PG",
    "PV",
    "PAP",
    "PDR",
    "PA",
    "PI",
    "PM",
    "PD10",
    "PW",
    "PX",
)
GATED = tuple(p for p in PROBES if p != "PL")
result, not_run, sha = P1.result, P1.not_run, P1.sha
PROPOSAL_MARKERS = ("FIXTURE PROPOSAL", "FIXTURE: soft deletes", "FIXTURE: emails are compared")
R1_REPORT_SHA = json.loads((HERE / "implementation_agent_scorer_cases.json").read_text())[
    "report_sha256"
]


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def h(x: Any) -> str:
    return hashlib.sha256(x.encode() if isinstance(x, str) else x).hexdigest()


def output_sha(obj: Any) -> str:
    return h(canon(obj))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n")


def run_main(fn: Callable[..., int], argv: Sequence[str], **kw: Any) -> tuple[int, str, str]:
    """Call a CLI ``main`` in this process: (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(list(argv), **kw)
    return rc, out.getvalue(), err.getvalue()


# ---- the fixtures, with this file's own hashing (D2) --------------------------------------------


def norm(text: str) -> str:
    return " ".join(text.split())


def anchor(text: str) -> str:
    return h(norm(text))


def approved_sha_of(why: str, tradeoff: str | None, owner: str | None) -> str:
    raw = json.dumps(
        {"why": why, "tradeoff": tradeoff, "owner": owner},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return h(raw)


def term_sha_of(definition: str, owner: str | None) -> str:
    raw = json.dumps(
        {"definition": definition, "owner": owner},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return h(raw)


def rat_note(
    rule: str, text: str, why: str, tradeoff: str | None, owner: str, by: str, author: str = "owner"
) -> dict[str, Any]:
    return {
        "rule": rule,
        "anchor": anchor(text),
        "why": why,
        "tradeoff": tradeoff,
        "owner": owner,
        "status": "approved",
        "author": author,
        "approved_by": by,
        "approved_sha": approved_sha_of(why, tradeoff, owner),
        "basis": [],
        "refs": [],
    }


def rat_file(notes: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema": "lattice.rationale/1", "source": f"{STEM}.md", "notes": notes}


def gl_term(
    term: str,
    definition: str,
    aliases: Sequence[str] = (),
    owner: str = "fixture",
    by: str = "fixture",
    author: str = "owner",
    basis: list | None = None,
) -> dict[str, Any]:
    return {
        "term": term,
        "aliases": list(aliases),
        "definition": definition,
        "owner": owner,
        "status": "approved",
        "author": author,
        "approved_by": by,
        "approved_sha": term_sha_of(definition, owner),
        "basis": basis or [],
    }


def gl_file(terms: list[dict[str, Any]], ignore: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "schema": "lattice.glossary/1",
        "source": f"{STEM}.md",
        "terms": terms,
        "ignore": list(ignore),
    }


def world_files(
    trace_rules: dict[str, str], world: dict[str, Any], extra_r88: bool = False, stale: bool = True
) -> tuple[dict, dict, dict]:
    """(rationale.json, rationale.proposed.json, glossary.json) objects of Appendix B (W); with
    `extra_r88` K0's sixth approved note (K's files hold no stale notes)."""
    notes = []
    for rid, e in world["approved"].items():
        notes.append(
            rat_note(rid, trace_rules[rid], e["why"], e["tradeoff"], e["owner"], e["approved_by"])
        )
    for rid, e in world["stale"].items() if stale else ():
        notes.append(rat_note(rid, e["anchor_text"], e["why"], None, e["owner"], e["approved_by"]))
    if extra_r88:
        notes.append(
            rat_note(
                "R88",
                trace_rules["R88"],
                "FIXTURE: an unknown cursor is refused rather than restarting the list.",
                None,
                "fixture-owner",
                "fixture-owner",
            )
        )
    notes.sort(key=lambda n: int(n["rule"][1:]))
    props = []
    for rid, e in world["proposed"].items():
        props.append(
            {
                "rule": rid,
                "anchor": anchor(trace_rules[rid]),
                "why": e["why"],
                "tradeoff": None,
                "owner": None,
                "status": "proposed",
                "author": "hand",
                "approved_by": None,
                "approved_sha": None,
                "basis": [],
                "refs": [],
            }
        )
    props.sort(key=lambda n: int(n["rule"][1:]))
    terms = []
    for term, rid in world["terms"].items():
        terms.append(
            gl_term(
                term,
                trace_rules[rid],
                owner="fixture-owner",
                by="fixture-owner",
                basis=[{"target": f"rule:{rid}", "quote": trace_rules[rid]}],
            )
        )
    return rat_file(notes), rat_file(props), gl_file(terms)


def write_world_files(dst: Path, trace_rules: dict[str, str], world: dict[str, Any]) -> None:
    r, p, g = world_files(trace_rules, world)
    (dst / RAT).write_text(json.dumps(r, indent=2) + "\n")
    (dst / PROP).write_text(json.dumps(p, indent=2) + "\n")
    (dst / GLOS).write_text(json.dumps(g, indent=2) + "\n")


def copy_world(src: Path, dst: Path) -> None:
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst, symlinks=True, ignore=shutil.ignore_patterns("bin", ".git"))


def build_fixture_worlds(s: Path, w: Path, k: dict[str, Path], world: dict[str, Any]) -> None:
    """W and K0..K4 from the S clone `s` (PR): K1..K4 edit REQUIREMENTS.md and re-extract the rules
    (context stays R1, R2)."""
    from lattice.spec.rules import Trace, extract_rules

    spec_py = (s / "spec.py").read_text()
    md = (s / "REQUIREMENTS.md").read_text()
    tr = json.loads((s / "spec.trace.json").read_text())
    rules0 = {r["id"]: r["text"] for r in tr["rules"]}
    copy_world(s, w)
    write_world_files(w, rules0, world)
    lines = md.splitlines()

    def edit(kind: str) -> str:
        ls = list(lines)
        if kind == "K1":  # a rule inserted as the first rule of "Errors and versioning" (R3)
            line = next(r["line"] for r in tr["rules"] if r["id"] == "R3")
            ls.insert(line - 1, "- FIXTURE: every response carries an `X-Request-Id` header.")
        elif kind == "K2":  # R75's 60 edited to 90
            line = next(r["line"] for r in tr["rules"] if r["id"] == "R75")
            assert "60" in ls[line - 1]
            ls[line - 1] = ls[line - 1].replace("60", "90", 1)
        elif kind == "K4":  # R133 deleted from the requirements
            line = next(r["line"] for r in tr["rules"] if r["id"] == "R133")
            del ls[line - 1]
        return "\n".join(ls) + ("\n" if md.endswith("\n") else "")

    for name, d in k.items():
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        new_md = edit(name)
        (d / "REQUIREMENTS.md").write_text(new_md)
        (d / "spec.py").write_text(spec_py)
        rules = extract_rules(new_md)
        if name in ("K0", "K3"):
            assert [r.text for r in rules] == [r["text"] for r in tr["rules"]]
        Trace(source="REQUIREMENTS.md", rules=rules, context=["R1", "R2"]).save(
            d / "spec.trace.json"
        )
        r, _p, _g = world_files(rules0, world, extra_r88=True, stale=False)
        if name == "K3":  # R63's why edited without re-approval
            for n in r["notes"]:
                if n["rule"] == "R63":
                    n["why"] += " (edited)"
        (d / RAT).write_text(json.dumps(r, indent=2) + "\n")


# ---- the worlds ---------------------------------------------------------------------------------


class Roots:
    """The directories a computation reads: S, W, K0..K4, an inventory, and a scratch for writes."""

    def __init__(
        self,
        s: Path,
        w: Path,
        k: dict[str, Path],
        inv: Path,
        tmp: Path,
        seeded: dict[str, Any],
        world: dict[str, Any],
    ) -> None:
        self.s, self.W, self.K, self.inv, self.tmp = s, w, k, inv, tmp
        self.seeded, self.world = seeded, world

    def spec_trace(self, root: Path):
        from lattice.spec.draft import load_spec
        from lattice.spec.rules import Trace

        spec, errs = load_spec((root / "spec.py").read_text())
        assert spec is not None, errs
        return spec, Trace.from_json((root / "spec.trace.json").read_text())

    def fresh(self, name: str) -> Path:
        d = self.tmp / name
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        return d

    def to_json(self) -> dict[str, Any]:
        return {
            "s": str(self.s),
            "W": str(self.W),
            "K": {k: str(v) for k, v in self.K.items()},
            "inv": str(self.inv),
            "tmp": str(self.tmp),
            "seeded": self.seeded,
            "world": self.world,
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Roots:
        return cls(
            Path(d["s"]),
            Path(d["W"]),
            {k: Path(v) for k, v in d["K"].items()},
            Path(d["inv"]),
            Path(d["tmp"]),
            d["seeded"],
            d["world"],
        )


class Ctx:
    """The scratch worlds and the arguments."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.w = P1.World(args)
        self.scratch = self.w.scratch
        self.out = self.w.out
        self.seeded = json.loads(Path(args.seeded).read_text())
        self.world = json.loads(Path(args.world).read_text())
        self.base = self.scratch / "base"
        self.W = self.scratch / "W"
        self.K = {k: self.scratch / k for k in ("K0", "K1", "K2", "K3", "K4")}
        self.r2 = self.scratch / "r2"
        self.K2 = {k: self.r2 / k for k in self.K}
        self.notes: list[str] = []

    @property
    def s(self) -> Path:
        return self.w.s

    def roots(self) -> Roots:
        return Roots(
            self.s,
            self.W,
            self.K,
            self.w.inv["S-dyn"],
            self.scratch / "tmp-main",
            self.seeded,
            self.world,
        )

    def roots2(self) -> Roots:
        """The second archive root (S-root2): the same worlds at other paths."""
        return Roots(
            self.w.s2,
            self.r2 / "W",
            self.K2,
            self.w.inv["S-dyn-r2"],
            self.scratch / "tmp-r2",
            self.seeded,
            self.world,
        )

    def world_dirs(self) -> dict[str, Path]:
        return {
            "S": self.s,
            "S4": self.w.s4,
            "W": self.W,
            **self.K,
            "S-root2": self.w.s2,
            "W-r2": self.r2 / "W",
            **{f"{k}-r2": v for k, v in self.K2.items()},
        }


def check_frozen(args: argparse.Namespace) -> None:
    for path, want, name in (
        (args.seeded, SEEDED_SHA, "seeded set"),
        (args.world, WORLD_SHA, "world"),
    ):
        got = h(canon(json.loads(Path(path).read_text())))
        if got != want:
            raise SystemExit(f"the {name} {path} hashes to {got}, not the registered {want}")


def setup(c: Ctx) -> None:
    a = c.args
    marker = c.scratch / "world.ok"
    if a.reuse_world and marker.is_file():
        for name in ("S-dyn", "S-stat", "S4-stat", "S-dyn-r2"):
            c.w.inv[name] = c.scratch / "inv" / name
        c.w.s4, c.w.s2 = c.scratch / "S4", c.scratch / "S-root2"
    else:
        P1.setup(c.w, need_docker=False)
        marker.write_text("ok\n")
    if a.inv_s4stat:  # round 1's frozen S4-stat inventory (a regenerated static run differs now)
        dst = c.scratch / "inv" / "S4-stat"
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(a.inv_s4stat, dst)
        c.w.inv["S4-stat"] = dst
        c.notes.append("S4-stat inventory copied from --inv-s4stat (frozen)")
    if a.base_tree:  # the mutant runner: an archive made by an earlier run (a copy has no git)
        c.base = Path(a.base_tree).resolve()
    else:
        shutil.rmtree(c.base, ignore_errors=True)
        c.base.mkdir(parents=True)
        arch = subprocess.run(
            ["git", "-C", str(REPO), "archive", a.base], capture_output=True, check=True
        ).stdout
        subprocess.run(["tar", "-x", "-C", str(c.base)], input=arch, check=True)
    assert sha((c.s / "spec.trace.json").read_bytes()) == S_TRACE_SHA
    assert sha((c.s / "REQUIREMENTS.md").read_bytes()) == S_REQ_SHA
    assert sha((c.s / "spec.py").read_bytes()) == S_SPEC_SHA
    build_fixture_worlds(c.s, c.W, c.K, c.world)
    build_fixture_worlds(c.w.s2, c.r2 / "W", c.K2, c.world)


# ---- PS: glossary lint precision and recall on the seeded set ------------------------------------


def seeded_case(R: Roots, case: dict[str, Any], spec: Any, trace0: Any) -> dict[str, Any]:
    from lattice.spec import glossary as gl
    from lattice.spec.rationale import notes_paths
    from lattice.spec.rules import Rule, Trace

    terms = {t: gl_term(t, "fixture") for t in R.seeded["G0"]}
    ops = case["ops"]
    for t in ops.get("remove", []):
        del terms[t]
    for add in ops.get("add", []):
        terms[add["term"]] = gl_term(add["term"], "fixture", aliases=add["aliases"])
    rules = list(trace0.rules)
    for i, text in enumerate(case["plant"]):
        rules.append(Rule(f"R{149 + i}", text, "Seeded", 0))
    trace = Trace(
        source=trace0.source,
        rules=rules,
        links=dict(trace0.links),
        unchecked=dict(trace0.unchecked),
        context=list(trace0.context),
    )
    gdir = R.fresh("ps-" + case["id"])
    (gdir / GLOS).write_text(json.dumps(gl_file(list(terms.values()), ops.get("ignore", []))))
    (gdir / "spec.trace.json").write_text(json.dumps({"source": f"{STEM}.md"}))
    paths = notes_paths(gdir / "spec.trace.json")
    res = gl.run_lint(spec, trace, gl.load_notes(trace, paths), paths)
    shutil.rmtree(gdir, ignore_errors=True)
    g001 = [f for f in res.findings if f["code"] == "G001"]
    return {
        "pairs": sorted({(f["rule"], f["normal"]) for f in g001}),
        "findings": [
            {k: f[k] for k in ("rule", "term", "normal", "source", "match")} for f in g001
        ],
        "exit": res.exit_code,
    }


def compute_ps(R: Roots) -> dict[str, Any]:
    from lattice.spec import glossary as gl

    spec, trace0 = R.spec_trace(R.s)
    raw = {case["id"]: seeded_case(R, case, spec, trace0) for case in R.seeded["cases"]}
    b0 = {tuple(p) for p in raw["B0"]["pairs"]}
    out: dict[str, Any] = {"cases": {}}
    tp = fp = fn = 0
    for case in R.seeded["cases"]:
        got = {tuple(p) for p in raw[case["id"]]["pairs"]} - b0
        truth = {(r, gl.normal_text(t)) for r, t in case["truth"]}
        ctp, cfp, cfn = got & truth, got - truth, truth - got
        tp, fp, fn = tp + len(ctp), fp + len(cfp), fn + len(cfn)
        out["cases"][case["id"]] = {
            "truth": len(truth),
            "tp": sorted(ctp),
            "fp": sorted(cfp),
            "fn": sorted(cfn),
            "n_findings": len(raw[case["id"]]["pairs"]),
        }
    out |= {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "b0_findings": len(b0),
        "b0_pairs": sorted(b0),
        "raw": {k: v["pairs"] for k, v in raw.items()},
    }
    out["precision"] = round(tp / (tp + fp), 4) if tp + fp else 1.0
    out["recall"] = round(tp / (tp + fn), 4) if tp + fn else 1.0
    return out


def probe_ps(c: Ctx) -> dict[str, Any]:
    o = compute_ps(c.roots())
    ok = o["precision"] >= 0.85 and o["recall"] >= 0.90 and o["b0_findings"] <= 2
    summary = {k: o[k] for k in ("tp", "fp", "fn", "precision", "recall", "b0_findings")}
    # D6 hand-predicted: the two registered misses and the six false positives
    r = result(
        "PS",
        "glossary lint precision and recall on the hash-frozen seeded set (25 cases)",
        "TP 51, FP 6, FN 2; precision 0.895, recall 0.962; B0 0-2 findings "
        "(pass: precision>=0.85, recall>=0.90, B0<=2)",
        summary,
        ok,
        per_case=o["cases"],
        b0_pairs=o["b0_pairs"],
        output=o["raw"],
    )
    r["output_sha256"] = output_sha(o["raw"])
    return r


# ---- PR: anchors ---------------------------------------------------------------------------------

EXPECT_PR = {
    "K0": {
        "states": {r: ("current", r) for r in ("R133", "R29", "R56", "R63", "R75", "R88")},
        "RC": (6, 146),
    },
    "K1": {
        "states": {
            "R63": ("moved", "R64"),
            "R56": ("moved", "R57"),
            "R75": ("moved", "R76"),
            "R29": ("moved", "R30"),
            "R133": ("moved", "R134"),
            "R88": ("ambiguous", "R88"),
        },
        "RC": (5, 147),
    },
    "K2": {
        "states": {
            **{r: ("current", r) for r in ("R133", "R29", "R56", "R63", "R88")},
            "R75": ("stale", "R75"),
        },
        "RC": (5, 146),
    },
    "K3": {
        "states": {
            **{r: ("current", r) for r in ("R133", "R29", "R56", "R75", "R88")},
            "R63": ("edited", "R63"),
        },
        "RC": (5, 146),
    },
    "K4": {
        "states": {
            **{r: ("current", r) for r in ("R29", "R56", "R63", "R75", "R88")},
            "R133": ("stale", "R133"),
        },
        "RC": (5, 145),
    },
}


def compute_pr(R: Roots) -> dict[str, Any]:
    from lattice.spec import rationale as rt
    from lattice.spec.rules import Trace

    out = {}
    for k, d in R.K.items():
        trace = Trace.load(d / "spec.trace.json")
        rs = rt.load_set(trace, rt.notes_paths(d / "spec.trace.json"))
        m = rs.metrics()
        out[k] = {
            "states": {r.note.rule: [r.state, r.rule] for r in rs.resolved},
            "matches": {
                r.note.rule: list(r.matches) for r in rs.resolved if r.state == "ambiguous"
            },
            "RC": [m["RC"]["n"], m["RC"]["d"], m["RC"]["pct"]],
            "RC_all": [m["RC_all"]["n"], m["RC_all"]["d"]],
            "RN": m["RN"],
        }
    return out


def probe_pr(c: Ctx) -> dict[str, Any]:
    o = compute_pr(c.roots())
    checks = {}
    for k, want in EXPECT_PR.items():
        got = o[k]
        checks[k] = {r: tuple(v) for r, v in got["states"].items()} == want["states"] and (
            got["RC"][0],
            got["RC"][1],
        ) == want["RC"]
    checks["K0_pct"] = o["K0"]["RC"][2] == 4.1
    checks["K1_pct"] = o["K1"]["RC"][2] == 3.4
    checks["K1_R88_matches"] = o["K1"]["matches"].get("R88") == ["R89", "R95"]
    ok = all(checks.values())
    r = result(
        "PR",
        "anchors on K0..K4: current, moved, ambiguous (R88/R94), stale, edited; RC",
        "K0 6 current RC 6/146 = 4.1%; K1 5 moved + R88 ambiguous RC 5/147 = 3.4%; K2 R75 stale "
        "5/146; K3 R63 edited 5/146; K4 R133 stale 5/145",
        o,
        ok,
        checks=checks,
    )
    r["output_sha256"] = output_sha(o)
    return r


# ---- PC, PL: metrics -----------------------------------------------------------------------------


def compute_pc(R: Roots) -> dict[str, Any]:
    from lattice.spec import glossary as gl
    from lattice.spec import rationale as rt

    out = {}
    for name, d in (("W", R.W), ("S", R.s)):
        rc, so, se = run_main(
            rt.main, ["coverage", "--trace", str(d / "spec.trace.json"), "--json"]
        )
        gc, go, ge = run_main(
            gl.main,
            ["lint", "--trace", str(d / "spec.trace.json"), "--spec", str(d / "spec.py"), "--json"],
        )
        m = json.loads(so)["metrics"]
        gm = json.loads(go)["metrics"]
        out[name] = {"rc": rc, "gl_rc": gc, "metrics": m, "gmetrics": gm, "err": se + ge}
    return out


def probe_pc(c: Ctx) -> dict[str, Any]:
    o = compute_pc(c.roots())
    w, s = o["W"]["metrics"], o["S"]["metrics"]
    checks = {
        "W_RC": (w["RC"]["n"], w["RC"]["d"], w["RC"]["pct"]) == (5, 146, 3.4),
        "W_RP": w["RP"] == 2,
        "W_stale": w["RN"]["stale"] == 2 and w["RN"]["current"] == 5,
        "W_GA": o["W"]["gmetrics"]["GA"] == 2,
        "S_RC": (s["RC"]["n"], s["RC"]["d"]) == (0, 146) and s["file"] is None,
        "S_GA": o["S"]["gmetrics"]["GA"] == 0 and o["S"]["gmetrics"]["file"] is None,
        "exit_ok": o["W"]["rc"] == 0 and o["S"]["rc"] == 0,
    }
    r = result(
        "PC",
        "metrics on W and S",
        "W: RC 5/146 = 3.4%, RP 2, stale 2, GA 2; S: RC 0/146, file null",
        {k: o[k]["metrics"] for k in o},
        all(checks.values()),
        checks=checks,
        glossary_metrics={k: o[k]["gmetrics"] for k in o},
    )
    r["output_sha256"] = output_sha(o)
    return r


def probe_pl(c: Ctx) -> dict[str, Any]:
    from lattice.spec import glossary as gl
    from lattice.spec.rationale import notes_paths

    R = c.roots()
    d = R.fresh("pl")
    (d / GLOS).write_text(json.dumps(gl_file([])))
    (d / "spec.trace.json").write_text((R.s / "spec.trace.json").read_text())
    spec, trace = R.spec_trace(R.s)
    paths = notes_paths(d / "spec.trace.json")
    res = gl.run_lint(spec, trace, gl.load_notes(trace, paths), paths)
    shutil.rmtree(d, ignore_errors=True)
    m = res.metrics
    r = result(
        "PL",
        "S with an empty authoritative glossary (terms: []), report-only",
        "GU 300 (250-350), GT 37 (34-40), GR 115 (95-135), GC 0/GR",
        m,
        None,
        in_range={
            "GU": 250 <= m["GU"] <= 350,
            "GT": 34 <= m["GT"] <= 40,
            "GR": 95 <= m["GR"] <= 135,
            "GC": m["GC"]["n"] == 0,
        },
    )
    r["output_sha256"] = output_sha([m, sorted((f["rule"], f["normal"]) for f in res.findings)])
    return r


# ---- PG: glossary draft without a model ----------------------------------------------------------


def compute_pg(R: Roots) -> dict[str, Any]:
    from lattice.spec import glossary as gl

    spec, trace = R.spec_trace(R.s)
    d = R.fresh("pg")
    out = d / "proposals.json"
    shutil.copy(R.s / "spec.trace.json", d / "spec.trace.json")
    rc, so, se = run_main(
        gl.main,
        [
            "draft",
            "--trace",
            str(d / "spec.trace.json"),
            "--spec",
            str(R.s / "spec.py"),
            "--out",
            str(out),
            "--json",
        ],
    )
    data = json.loads(out.read_text())
    rules = {r.id: r.text for r in trace.rules}
    terms = data["terms"]
    verbatim = [
        t
        for t in terms
        if t["author"] == "extracted"
        and t["definition"] in rules.values()
        and t["basis"]
        and t["basis"][0]["target"].startswith("rule:")
        and rules[t["basis"][0]["target"][5:]] == t["definition"]
    ]
    enum = [t for t in terms if t["definition"].startswith("From the spec: one of the values of")]
    table = [t for t in terms if t["definition"].startswith("From the spec: the table")]
    service = [t for t in terms if t["term"] == "nudger"]
    shutil.rmtree(d, ignore_errors=True)
    return {
        "rc": rc,
        "total": len(terms),
        "verbatim": len(verbatim),
        "enum": len(enum),
        "table": len(table),
        "service": len(service),
        "service_doc_verbatim": bool(service) and service[0]["definition"] == spec.services[0].doc,
        "approved": sum(1 for t in terms if t["status"] == "approved"),
        "owners": sorted({str(t["owner"]) for t in terms}),
        "verbatim_terms": sorted(t["term"] for t in verbatim),
        "terms": sorted(t["term"] for t in terms),
    }


def probe_pg(c: Ctx) -> dict[str, Any]:
    o = compute_pg(c.roots())
    checks = {
        "total_34_42": 34 <= o["total"] <= 42,
        "verbatim_10_of_10": o["verbatim"] == 10,
        "enum_20": o["enum"] == 20,
        "table_7": o["table"] == 7,
        "service_1": o["service"] == 1 and o["service_doc_verbatim"],
        "approved_0": o["approved"] == 0 and o["owners"] == ["None"],
        "exit_0": o["rc"] == 0,
    }
    r = result(
        "PG",
        "glossary draft on S without a model",
        "38 proposals (34-42): 10 C6 verbatim, 20 enum stubs, 7 table stubs, 1 service; 0 approved",
        o,
        all(checks.values()),
        checks=checks,
    )
    r["output_sha256"] = output_sha(o)
    return r


# ---- PV: invalid files (D14) ---------------------------------------------------------------------


def invalid_fixtures(trace_rules: dict[str, str]) -> list[dict[str, Any]]:
    """14 invalid-file fixtures: (id, kind 'rationale'|'glossary', file name, content)."""
    t63 = trace_rules["R63"]
    good = rat_note("R63", t63, "FIXTURE: why.", "FIXTURE: trade.", "o", "o")
    prop = {
        **good,
        "status": "proposed",
        "owner": None,
        "approved_by": None,
        "approved_sha": None,
        "author": "hand",
    }
    gt = gl_term("grace day", "FIXTURE definition.", ["allowance day"])
    prop_t = {**gt, "status": "proposed", "owner": None, "approved_by": None, "approved_sha": None}
    return [
        {"id": "VR1", "kind": "rationale", "file": RAT, "text": "{not json"},
        {
            "id": "VR2",
            "kind": "rationale",
            "file": RAT,
            "obj": {**rat_file([good]), "schema": "lattice.rationale/2"},
        },
        {"id": "VR3", "kind": "rationale", "file": RAT, "obj": rat_file([{**good, "why": ""}])},
        {"id": "VR4", "kind": "rationale", "file": RAT, "obj": rat_file([good, good])},
        {
            "id": "VR5",
            "kind": "rationale",
            "file": PROP,
            "obj": rat_file([{**good, "status": "approved"}]),
        },
        {"id": "VR6", "kind": "rationale", "file": RAT, "obj": rat_file([prop])},
        {"id": "VR7", "kind": "rationale", "file": RAT, "obj": rat_file([{**good, "owner": None}])},
        {"id": "VG1", "kind": "glossary", "file": GLOS, "text": "[]"},
        {
            "id": "VG2",
            "kind": "glossary",
            "file": GLOS,
            "obj": {**gl_file([gt]), "schema": "lattice.glossary/0"},
        },
        {
            "id": "VG3",
            "kind": "glossary",
            "file": GLOS,
            "obj": gl_file([gt, gl_term("Grace days", "FIXTURE other.")]),
        },
        {
            "id": "VG4",
            "kind": "glossary",
            "file": GLOS,
            "obj": gl_file([gt, gl_term("allowance day", "FIXTURE other.")]),
        },
        {"id": "VG5", "kind": "glossary", "file": GLOS_PROP, "obj": gl_file([gt])},
        {"id": "VG6", "kind": "glossary", "file": GLOS, "obj": gl_file([prop_t])},
        {
            "id": "VG7",
            "kind": "glossary",
            "file": GLOS,
            "obj": gl_file([{**gt, "definition": " "}]),
        },
    ]


class StubStudioGen:
    def generate(self, system, messages):  # noqa: ANN001
        raise AssertionError("no model call on a read path")


def mini_project(R: Roots, name: str) -> Path:
    """S's three spec files and a studio.toml with a state directory outside the project."""
    d = R.fresh(name)
    for f in ("spec.py", "spec.trace.json", "REQUIREMENTS.md"):
        shutil.copy(R.s / f, d / f)
    state = R.fresh(name + "-state")
    (d / "studio.toml").write_text(f'[project]\nname = "mini"\n\n[state]\ndir = "{state}"\n')
    return d


def probe_pv(c: Ctx) -> dict[str, Any]:
    from lattice.mcp.tools import LocalBackend, Toolbox, ToolError
    from lattice.spec import ask as A
    from lattice.spec import glossary as gl
    from lattice.spec import implementation as impl
    from lattice.spec import rationale as rt
    from lattice.studio.server import ApiError, Studio

    R = c.roots()
    spec, trace = R.spec_trace(R.s)
    rules = {r.id: r.text for r in trace.rules}
    rows = []
    for fx in invalid_fixtures(rules):
        code = "bad_rationale" if fx["kind"] == "rationale" else "bad_glossary"
        d = mini_project(R, "pv-" + fx["id"])
        body = fx.get("text") if "text" in fx else json.dumps(fx["obj"], indent=2)
        (d / fx["file"]).write_text(body)
        tr = str(d / "spec.trace.json")
        sp = str(d / "spec.py")
        surf: dict[str, Any] = {}
        # the CLIs: show / lint, ask, implementation
        if fx["kind"] == "rationale":
            rc, _o, e = run_main(rt.main, ["show", "--trace", tr])
        else:
            rc, _o, e = run_main(gl.main, ["lint", "--trace", tr, "--spec", sp])
        surf["cli"] = rc == 2 and code in e
        sys.path.insert(0, str(d))  # ask and implementation take module:attr
        try:
            rc, _o, e = run_main(
                A.main,
                ["--spec", "spec:SPEC", "--trace", tr, "--json", "q?"],
                generator=StubStudioGen(),
            )
            surf["ask"] = rc == 2 and code in e
            rc, _o, e = run_main(
                impl.main,
                [
                    "--spec",
                    "spec:SPEC",
                    "--trace",
                    tr,
                    "--inventory",
                    str(R.inv),
                    "--app-root",
                    str(R.s),
                ],
            )
            surf["implementation"] = rc == 2 and code in e
        finally:
            sys.path.remove(str(d))
        # MCP and Studio
        tb = Toolbox(LocalBackend(d), readonly=True)
        tool = "rationale_get" if fx["kind"] == "rationale" else "glossary_get"
        try:
            tb.call(tool, {})
            surf["mcp"] = False
        except ToolError as err:
            surf["mcp"] = str(err).startswith(code)
        st = Studio(d, model="stub", generator=StubStudioGen())
        try:
            st.ask({"question": "q?"})
            surf["studio_ask"] = False
        except ApiError as err:
            surf["studio_ask"] = int(err.status) == 422 and str(err.detail).startswith(code)
        key = "rationale" if fx["kind"] == "rationale" else "glossary"
        surf["studio_model"] = str(st.get_model().get(key, {}).get("error", "")).startswith(code)
        rows.append({"id": fx["id"], "code": code, "surfaces": surf, "ok": all(surf.values())})
        shutil.rmtree(d, ignore_errors=True)
    ok = len(rows) == 14 and all(r["ok"] for r in rows)
    return result(
        "PV",
        "14 invalid-file fixtures (D14) through the CLIs, ask, implementation, MCP, Studio",
        "each answers its bad_* code; exit 2; 422",
        f"{sum(r['ok'] for r in rows)}/{len(rows)} exact",
        ok,
        rows=rows,
    )


# ---- PAP: the approval guard ---------------------------------------------------------------------


def run_in_pty(argv: list[str], env: dict[str, str], cwd: Path, timeout: int = 120):
    """Run `argv` with a pseudo-terminal as stdin and stdout; (exit code, stdout text, stderr)."""
    import pty
    import select

    master, slave = pty.openpty()
    proc = subprocess.Popen(
        argv, stdin=slave, stdout=slave, stderr=subprocess.PIPE, env=env, cwd=cwd, text=True
    )
    os.close(slave)
    chunks = []
    deadline = time.time() + timeout
    while True:
        r, _, _ = select.select([master], [], [], 0.2)
        if r:
            try:
                data = os.read(master, 4096)
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
        if proc.poll() is not None and not r:
            break
        if time.time() > deadline:
            proc.kill()
            break
    _out, err = proc.communicate()
    os.close(master)
    return proc.returncode, b"".join(chunks).decode(errors="replace"), err


def tree_files(root: Path) -> dict[str, str]:
    out = {}
    for dp, _dns, fns in os.walk(root):
        for fn in sorted(fns):
            p = Path(dp) / fn
            out[p.relative_to(root).as_posix()] = sha(p.read_bytes())
    return out


def pap_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    env.pop("ANTHROPIC_API_KEY", None)
    env.update(PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE="1", NO_COLOR="1")
    env.update(extra or {})
    return env


def compute_pap(R: Roots) -> dict[str, Any]:
    spec, trace = R.spec_trace(R.s)
    rules = {r.id: r.text for r in trace.rules}
    base = R.fresh("pap")
    for f in ("spec.py", "spec.trace.json", "REQUIREMENTS.md"):
        shutil.copy(R.s / f, base / f)
    _r, p, _g = world_files(rules, R.world)
    (base / PROP).write_text(json.dumps(p, indent=2) + "\n")
    tr = str(base / "spec.trace.json")
    py = sys.executable
    rat = [py, "-m", "lattice.spec", "rationale"]
    gls = [py, "-m", "lattice.spec", "glossary"]
    approve = [
        *rat,
        "approve",
        "--trace",
        tr,
        "--from",
        str(base / PROP),
        "--rule",
        "R20",
        "--approver",
        "tester",
        "--owner",
        "tester",
    ]
    add = [
        *rat,
        "add",
        "--trace",
        tr,
        "--rule",
        "R26",
        "--why",
        "FIXTURE: a hand why.",
        "--owner",
        "tester",
        "--approver",
        "tester",
    ]
    g_add = [
        *gls,
        "add",
        "--trace",
        tr,
        "--term",
        "grace day",
        "--definition",
        "FIXTURE def.",
        "--owner",
        "tester",
        "--approver",
        "tester",
    ]
    out: dict[str, Any] = {}
    before = tree_files(base)

    def refused(argv, code, *, pty_run=False, env=None):
        if pty_run:
            rc, so, se = run_in_pty(argv, pap_env(env), base)
        else:
            p_ = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                env=pap_env(env),
                cwd=base,
                input="",
                timeout=120,
                check=False,
            )
            rc, se = p_.returncode, p_.stderr
        return {"rc": rc, "code_seen": code in se, "unchanged": tree_files(base) == before}

    out["approve_no_tty"] = refused(approve, "approve_needs_terminal")
    out["add_no_tty"] = refused(add, "approve_needs_terminal")
    out["glossary_add_no_tty"] = refused(g_add, "approve_needs_terminal")
    out["claudecode_pty"] = refused(
        approve, "approve_needs_terminal", pty_run=True, env={"CLAUDECODE": "1"}
    )
    from_auth = [*approve]
    from_auth[from_auth.index("--from") + 1] = str(base / RAT)
    out["from_authoritative"] = refused(from_auth, "approve_from_authoritative", pty_run=True)
    unknown = [*approve]
    unknown[unknown.index("--rule") + 1] = "R9999"
    out["unknown_rule"] = refused(unknown, "unknown_rule", pty_run=True)
    # then under a pty with neither condition: exactly one file is written
    rc, so, se = run_in_pty(approve, pap_env(), base)
    after = tree_files(base)
    new = sorted(set(after) - set(before))
    changed = sorted(k for k in before if k in after and before[k] != after[k])
    note = {}
    if (base / RAT).is_file():
        data = json.loads((base / RAT).read_text())
        note = next((n for n in data["notes"] if n["rule"] == "R20"), {})
    out["approve_pty"] = {
        "rc": rc,
        "new": new,
        "changed": changed,
        "status": note.get("status"),
        "owner": note.get("owner"),
        "sha_ok": note.get("approved_sha")
        == approved_sha_of(note.get("why", ""), note.get("tradeoff"), note.get("owner")),
        "anchor_ok": note.get("anchor") == anchor(rules["R20"]),
        "approved_by": note.get("approved_by"),
        "author": note.get("author"),
    }
    # add under a pty writes the same single file (replacing nothing)
    before2 = tree_files(base)
    rc2, _so2, _se2 = run_in_pty(add, pap_env(), base)
    after2 = tree_files(base)
    out["add_pty"] = {
        "rc": rc2,
        "new": sorted(set(after2) - set(before2)),
        "changed": sorted(k for k in before2 if before2[k] != after2.get(k)),
    }
    shutil.rmtree(base, ignore_errors=True)
    return out


def probe_pap(c: Ctx) -> dict[str, Any]:
    o = compute_pap(c.roots())
    refusals = (
        "approve_no_tty",
        "add_no_tty",
        "glossary_add_no_tty",
        "claudecode_pty",
        "from_authoritative",
        "unknown_rule",
    )
    checks = {k: o[k]["rc"] == 2 and o[k]["code_seen"] and o[k]["unchanged"] for k in refusals}
    a = o["approve_pty"]
    checks["pty_writes_one_file"] = (
        a["rc"] == 0
        and a["new"] == [RAT]
        and a["changed"] == []
        and a["status"] == "approved"
        and a["sha_ok"]
        and a["anchor_ok"]
        and a["owner"] == "tester"
        and a["approved_by"] == "tester"
    )
    b = o["add_pty"]
    checks["add_pty_writes_same_file"] = b["rc"] == 0 and b["new"] == [] and b["changed"] == [RAT]
    r = result(
        "PAP",
        "approval guard: approve/add without a TTY, with CLAUDECODE under a pty, --from = "
        "authoritative, unknown rule; then under a pty with neither",
        "refusals (exit 2, codes as D4); the pty run writes exactly 1 file whose note "
        "verifies approved_sha",
        o,
        all(checks.values()),
        checks=checks,
    )
    r["output_sha256"] = output_sha(o)
    return r


# ---- PDR: drafting with a stub generator ---------------------------------------------------------


def compute_pdr(R: Roots) -> dict[str, Any]:
    from lattice.llm.client import ScriptedGenerator
    from lattice.spec import glossary as gl
    from lattice.spec import rationale as rt

    spec, trace = R.spec_trace(R.s)
    rules = {r.id: r.text for r in trace.rules}
    d = R.fresh("pdr")
    copy_world(R.W, d / "proj")
    proj = d / "proj"
    tr, sp = str(proj / "spec.trace.json"), str(proj / "spec.py")
    # a rule with a marked phrase, so the glossary has a term only a model can propose
    tj = json.loads((proj / "spec.trace.json").read_text())
    tj["rules"].append(
        {
            "id": "R149",
            "text": 'A "grace day" lets a user miss one day a week.',
            "section": "Seeded",
            "line": 0,
        }
    )
    (proj / "spec.trace.json").write_text(json.dumps(tj, indent=2))
    auth_before = (proj / RAT).read_bytes()
    gauth_before = (proj / GLOS).read_bytes()
    quote = rules["R63"][:30]
    r1 = json.dumps(
        {
            "why": "FIXTURE DRAFT: the period fixes how history is bucketed.",
            "tradeoff": None,
            "basis": [
                {"target": "rule:R63", "quote": quote},
                {"target": "rule:R63", "quote": "this sentence is nowhere in the rule"},
                {"target": "scenario:no_such_scenario", "quote": "whatever text here"},
            ],
            "owner": "Alice Owner",
            "status": "approved",
            "approved_by": "Bob",
            "author": "owner",
        }
    )
    r2 = json.dumps(
        {"why": "FIXTURE DRAFT: a cap keeps the home screen small.", "tradeoff": "x", "basis": []}
    )
    out_file = proj / "draft.proposed.json"
    gen = ScriptedGenerator([r1, r2])
    rc, so, se = run_main(
        rt.main,
        [
            "draft",
            "--trace",
            tr,
            "--spec",
            sp,
            "--rule",
            "R63",
            "--rule",
            "R56",
            "--out",
            str(out_file),
            "--model",
            "stubmodel",
            "--json",
        ],
        generator=gen,
    )
    data = json.loads(out_file.read_text()) if out_file.is_file() else {"notes": []}
    entries = data["notes"]
    e63 = next((e for e in entries if e["rule"] == "R63"), {})
    res: dict[str, Any] = {
        "rc": rc,
        "n": len(entries),
        "statuses": sorted({e["status"] for e in entries}),
        "owners": sorted({str(e["owner"]) for e in entries}),
        "authors": sorted({e["author"] for e in entries}),
        "approved_by": sorted({str(e["approved_by"]) for e in entries}),
        "dropped_r63": e63.get("dropped"),
        "rejected_basis_r63": [
            b["target"] + "|" + b["quote"][:12] for b in e63.get("rejected_basis", [])
        ],
        "basis_r63": [b["target"] for b in e63.get("basis", [])],
        "auth_unchanged": (proj / RAT).read_bytes() == auth_before,
        "anchor_ok": e63.get("anchor") == anchor(rules["R63"]),
    }
    # --out = the authoritative file is refused and nothing is written
    gen2 = ScriptedGenerator([r2])
    rc2, _so2, se2 = run_main(
        rt.main,
        [
            "draft",
            "--trace",
            tr,
            "--spec",
            sp,
            "--rule",
            "R56",
            "--out",
            str(proj / RAT),
            "--model",
            "stubmodel",
        ],
        generator=gen2,
    )
    res["out_is_authoritative"] = {
        "rc": rc2,
        "code": "draft_out_is_authoritative" in se2,
        "unchanged": (proj / RAT).read_bytes() == auth_before,
        "no_model_call": len(gen2.calls) == 0,
    }
    # glossary draft --model with a forged owner and status
    g1 = json.dumps(
        {
            "definition": "FIXTURE DRAFT: a stub definition.",
            "basis": [{"target": "rule:R97", "quote": "no such words here at all"}],
            "owner": "Alice Owner",
            "status": "approved",
        }
    )
    gen3 = ScriptedGenerator([g1] * 40)
    gout = proj / "gdraft.json"
    rc3, _o3, _e3 = run_main(
        gl.main,
        [
            "draft",
            "--trace",
            tr,
            "--spec",
            sp,
            "--out",
            str(gout),
            "--model",
            "stubmodel",
            "--json",
        ],
        generator=gen3,
    )
    gdata = json.loads(gout.read_text())["terms"] if gout.is_file() else []
    llm = [t for t in gdata if t["author"].startswith("llm:")]
    res["glossary_model"] = {
        "rc": rc3,
        "llm": len(llm),
        "statuses": sorted({t["status"] for t in gdata}),
        "owners": sorted({str(t["owner"]) for t in gdata}),
        "dropped_has_owner": all("owner" in t.get("dropped", []) for t in llm) if llm else None,
        "bad_basis_listed": all(t.get("rejected_basis") for t in llm) if llm else None,
        "gauth_unchanged": (proj / GLOS).read_bytes() == gauth_before,
    }
    gen4 = ScriptedGenerator([g1])
    rc4, _o4, e4 = run_main(
        gl.main,
        ["draft", "--trace", tr, "--spec", sp, "--out", str(proj / GLOS), "--model", "stubmodel"],
        generator=gen4,
    )
    res["glossary_out_is_authoritative"] = {
        "rc": rc4,
        "code": "draft_out_is_authoritative" in e4,
        "unchanged": (proj / GLOS).read_bytes() == gauth_before,
    }
    shutil.rmtree(d, ignore_errors=True)
    return res


def probe_pdr(c: Ctx) -> dict[str, Any]:
    o = compute_pdr(c.roots())
    g = o["glossary_model"]
    checks = {
        "two_entries_proposed": o["n"] == 2 and o["statuses"] == ["proposed"],
        "owner_null_and_unapproved": o["owners"] == ["None"] and o["approved_by"] == ["None"],
        "author_llm": o["authors"] == ["llm:stubmodel"],
        "forged_dropped_and_noted": bool(o["dropped_r63"])
        and {"owner", "status", "approved_by", "author"} <= set(o["dropped_r63"]),
        "bad_basis_listed": len(o["rejected_basis_r63"]) == 2 and o["basis_r63"] == ["rule:R63"],
        "authoritative_unchanged": o["auth_unchanged"] and g["gauth_unchanged"],
        "anchor": o["anchor_ok"],
        "out_is_authoritative_refused": o["out_is_authoritative"]["rc"] == 2
        and o["out_is_authoritative"]["code"]
        and o["out_is_authoritative"]["unchanged"]
        and o["out_is_authoritative"]["no_model_call"],
        "glossary_proposed_owner_null": g["rc"] == 0
        and g["llm"] > 0
        and g["owners"] == ["None"]
        and "approved" not in g["statuses"]
        and g["dropped_has_owner"]
        and g["bad_basis_listed"],
        "glossary_out_is_authoritative_refused": o["glossary_out_is_authoritative"]["rc"] == 2
        and o["glossary_out_is_authoritative"]["code"]
        and o["glossary_out_is_authoritative"]["unchanged"],
    }
    r = result(
        "PDR",
        "rationale draft and glossary draft --model with a stub generator (forged owner, "
        "bad citation, status: approved)",
        "entries proposed, owner null (forged one dropped "
        "and noted), bad basis in rejected_basis; authoritative files byte-unchanged; --out = "
        "authoritative refused",
        o,
        all(checks.values()),
        checks=checks,
    )
    r["output_sha256"] = output_sha(o)
    return r


# ---- PA: chat with a stub generator on W ---------------------------------------------------------


def compute_pa(R: Roots) -> dict[str, Any]:
    from lattice.llm.client import ScriptedGenerator
    from lattice.spec import ask as A
    from lattice.spec import glossary as gl

    spec, trace = R.spec_trace(R.W)
    notes = gl.discover_notes(trace, R.W / "spec.trace.json")
    rules = {r.id: r.text for r in trace.rules}
    sspec, strace = R.spec_trace(R.s)
    scen = "scenario:the_51st_active_habit_is_refused"
    why63 = R.world["approved"]["R63"]["why"]

    def reply(kind: str, cites: list[dict], answer: str = "a") -> str:
        return json.dumps({"kind": kind, "answer": answer, "explanation": "e", "citations": cites})

    def cite(target: str, quote: str, step: int | None = None) -> dict:
        return {"target": target, "step": step, "quote": quote}

    cases = [
        ("cite_rule_R63", reply("value", [cite("rule:R63", rules["R63"][:30])]), 1),
        ("cite_rule_R26", reply("value", [cite("rule:R26", rules["R26"][:30])]), 1),
        ("cite_rule_R8", reply("value", [cite("rule:R8", rules["R8"][:30])]), 1),
        ("cite_rule_R132", reply("value", [cite("rule:R132", rules["R132"][:30])]), 1),
        ("scenario_via_R56", reply("value", [cite(scen, "the_51st_active_habit_is_refused")]), 1),
        ("valid_rationale_R63", reply("not_specified", [cite("rationale:R63", why63[:34])]), 1),
        (
            "bad_quote",
            reply("not_specified", [cite("rationale:R63", "the owner wanted fraud stopped")]),
            2,
        ),
        (
            "rationale_R26_no_element",
            reply("not_specified", [cite("rationale:R26", "FIXTURE PROPOSAL: account")]),
            2,
        ),
        (
            "valid_term_active_window",
            reply("not_specified", [cite("term:active-window", rules["R97"][:30])]),
            1,
        ),
        (
            "term_daily_pass_no_element",
            reply("not_specified", [cite("term:daily-pass", "a daily pass is")]),
            2,
        ),
        ("yes_rationale_only", reply("yes", [cite("rationale:R63", why63[:34])]), 2),
        ("no_valid_citation", reply("not_specified", []), 1),
    ]
    answers: dict[str, Any] = {}
    for name, text, n in cases:
        g = ScriptedGenerator([text] * n)
        a = A.ask(spec, "Why?", trace=trace, generator=g, model="m", notes=notes)
        d = a.to_dict()
        d["usage"] = {k: v for k, v in d["usage"].items() if k not in ("seconds", "calls")}
        answers[name] = d
    # files absent: S, no notes: the answer equals the same ask without the notes argument
    g1 = ScriptedGenerator([reply("value", [cite("rule:R63", rules["R63"][:30])])])
    base = A.ask(sspec, "Why?", trace=strace, generator=g1, model="m").to_dict()
    g2 = ScriptedGenerator([reply("value", [cite("rule:R63", rules["R63"][:30])])])
    n_s = gl.discover_notes(strace, R.s / "spec.trace.json")
    absent = A.ask(sspec, "Why?", trace=strace, generator=g2, model="m", notes=n_s).to_dict()
    for d in (base, absent):
        d["usage"] = {k: v for k, v in d["usage"].items() if k not in ("seconds", "calls")}
    answers["files_absent"] = absent
    return {
        "answers": answers,
        "absent_equals_base": base == absent,
        "notes_none": n_s is None,
        "absent_has_key": "rationale" in absent,
        "why63": why63,
    }


def probe_pa(c: Ctx) -> dict[str, Any]:
    o = compute_pa(c.roots())
    ans = o["answers"]
    w = c.world

    def att(name: str) -> list[dict]:
        return ans[name].get("rationale") or []

    def cited_ok(name: str) -> bool:
        return len(ans[name]["citations"]) == 1 and not ans[name]["rejected_citations"]

    def rejected(name: str, fragment: str) -> bool:
        rj = ans[name]["rejected_citations"]
        return len(rj) == 1 and fragment in rj[0]["reason"] and not ans[name]["citations"]

    r63 = w["approved"]["R63"]
    via = [e for e in att("scenario_via_R56") if e["rule"] == "R56"]
    texts = canon(ans)
    checks = {
        "1_cite_R63_approved_with_text": att("cite_rule_R63")
        == [
            {
                "rule": "R63",
                "status": "approved",
                "via": None,
                "why": r63["why"],
                "tradeoff": r63["tradeoff"],
                "owner": r63["owner"],
                "approved_by": r63["approved_by"],
            }
        ]
        and cited_ok("cite_rule_R63"),
        "2_R26_proposed_only_no_text": att("cite_rule_R26")
        == [{"rule": "R26", "status": "proposed_only", "via": None}],
        "3_R8_stale_no_text": att("cite_rule_R8")
        == [{"rule": "R8", "status": "stale", "via": None}],
        "4_R132_none": att("cite_rule_R132") == [{"rule": "R132", "status": "none", "via": None}],
        "5_scenario_via_R56": len(via) == 1
        and via[0]["via"] == "scenario:the_51st_active_habit_is_refused"
        and via[0]["status"] == "approved"
        and len(att("scenario_via_R56")) <= 5,
        "6_valid_rationale_quote": cited_ok("valid_rationale_R63"),
        "7_bad_quote_rejected": rejected("bad_quote", "not in the note's text"),
        "8_rationale_R26_no_element": rejected("rationale_R26_no_element", "no element"),
        "9_valid_term": cited_ok("valid_term_active_window"),
        "10_term_daily_pass_no_element": rejected("term_daily_pass_no_element", "no element"),
        "11_yes_rationale_only_downgraded": ans["yes_rationale_only"]["kind"] == "not_specified"
        and ans["yes_rationale_only"]["answer"].startswith("Not specified"),
        "12_no_valid_citation_attachment_empty": att("no_valid_citation") == []
        and "rationale" in ans["no_valid_citation"],
        "13_files_absent_no_key_and_equals_base": o["notes_none"]
        and not o["absent_has_key"]
        and o["absent_equals_base"],
        "no_proposal_or_stale_text": not any(m in texts for m in PROPOSAL_MARKERS),
    }
    ok = all(checks.values())
    r = result(
        "PA",
        "chat with a stub generator on W, 13 cases",
        "13/13 as stated",
        f"{sum(v for k, v in checks.items() if k[0].isdigit())}/13",
        ok,
        checks=checks,
        answers=ans,
    )
    r["output_sha256"] = output_sha(o)
    return r


# ---- PI: the implementation report ---------------------------------------------------------------


def compute_pi(R: Roots) -> dict[str, Any]:
    from lattice.spec import glossary as gl
    from lattice.spec import implementation as impl

    spec, trace = R.spec_trace(R.W)
    inv = impl.load_inventory(R.inv, R.s)
    notes = gl.discover_notes(trace, R.W / "spec.trace.json")
    plain = impl.build_report(spec, trace, inv)
    rich = impl.build_report(spec, trace, inv, notes=notes)
    stripped = json.loads(json.dumps(rich))
    for e in stripped["rules"]:
        e.pop("terms", None)
        e.pop("rationale", None)
    stripped.pop("glossary", None)
    rules = {e["id"]: e for e in rich["rules"]}
    return {
        "plain_sha": h(impl.report_json(plain)),
        "stripped_equals_plain": canon(stripped) == canon(plain),
        "glossary": rich["glossary"],
        "R97_terms": rules["R97"]["terms"],
        "R145_terms": rules["R145"]["terms"],
        "rationale": {r: rules[r]["rationale"] for r in ("R63", "R56", "R26", "R8", "R132", "R70")},
        "every_rule_has_keys": all("terms" in e and "rationale" in e for e in rich["rules"]),
        "terms_used_by": sorted(e["id"] for e in rich["rules"] if e["terms"]),
    }


def probe_pi(c: Ctx) -> dict[str, Any]:
    o = compute_pi(c.roots())
    checks = {
        "report_minus_keys_is_base": o["stripped_equals_plain"],
        "base_report_sha_84624894": o["plain_sha"].startswith(R1_REPORT_SHA["S-dyn"][:8]),
        "two_glossary_terms": sorted(o["glossary"]) == ["active-window", "past-period"],
        "R97_active_window": o["R97_terms"] == ["active-window"],
        "R145_past_period": "past-period" in o["R145_terms"],
        "rationale_states": o["rationale"]
        == {
            "R63": "current",
            "R56": "current",
            "R26": "proposed_only",
            "R8": "stale",
            "R132": "none",
            "R70": "stale",
        },
        "every_rule_has_keys": o["every_rule_has_keys"],
    }
    r = result(
        "PI",
        "implementation report with the S-dyn inventory + W",
        "terms and rationale on rules, top-level glossary with 2 terms; minus keys = base",
        o,
        all(checks.values()),
        checks=checks,
    )
    r["output_sha256"] = output_sha(o)
    return r


# ---- PM: MCP on W and S --------------------------------------------------------------------------


def compute_pm(R: Roots) -> dict[str, Any]:
    from lattice.mcp.tools import LocalBackend, Toolbox, ToolError

    out: dict[str, Any] = {}
    for name, d in (("S", R.s), ("W", R.W)):
        tb = Toolbox(LocalBackend(d), readonly=False)
        tbr = Toolbox(LocalBackend(d), readonly=True)
        impl_tools = ("implementation_get", "workflow_get", "workflow_find")
        names, names_ro = tb.names(), tbr.names()
        # the implementation tools exist only with [implementation] in the clone's studio.toml
        # (the second archive root has none): compare the rest, and where the new tools sit
        out[name] = {
            "names": [n for n in names if n not in impl_tools],
            "names_ro": [n for n in names_ro if n not in impl_tools],
            "new_last": names[-2:] == ["rationale_get", "glossary_get"],
            "new_last_ro": names_ro[-2:] == ["rationale_get", "glossary_get"],
        }
        if name == "W":
            calls = {
                "r_R63": ("rationale_get", {"rule": "R63"}),
                "r_R26": ("rationale_get", {"rule": "R26"}),
                "r_R8": ("rationale_get", {"rule": "R8"}),
                "r_all": ("rationale_get", {}),
                "r_stale": ("rationale_get", {"state": "stale"}),
                "g_term": ("glossary_get", {"term": "active-window"}),
                "g_alias": ("glossary_get", {"term": "Past Periods"}),
                "g_all": ("glossary_get", {}),
                "g_lint": ("glossary_get", {"lint": True, "limit": 5}),
                "rule_R97": ("rule_get", {"id": "R97"}),
                "rule_R63": ("rule_get", {"id": "R63"}),
                "spec_lint": ("spec_lint", {}),
            }
            for key, (tool, args) in calls.items():
                res = tb.call(tool, args)
                if tool == "rule_get":  # code links hold file contents of the clone: keep the keys
                    res = {k: res[k] for k in ("id", "status", "rationale", "terms") if k in res}
                out[name][key] = res
            errs = {}
            for key, (tool, args) in {
                "bad_limit": ("rationale_get", {"limit": 500}),
                "unknown_rule": ("rationale_get", {"rule": "R9999"}),
                "unknown_term": ("glossary_get", {"term": "daily pass"}),
                "bad_state": ("rationale_get", {"state": "weird"}),
            }.items():
                try:
                    tb.call(tool, args)
                    errs[key] = "no error"
                except ToolError as e:
                    errs[key] = str(e).split(":")[0]
            out[name]["errors"] = errs
        else:
            out[name]["rule_R63_keys"] = sorted(tb.call("rule_get", {"id": "R63"}))
            out[name]["spec_lint_count"] = tb.call("spec_lint", {})["count"]
    return out


def probe_pm(c: Ctx) -> dict[str, Any]:
    o = compute_pm(c.roots())
    s, w = o["S"], o["W"]
    new = ["rationale_get", "glossary_get"]
    text = canon(w)
    r63, r26, r8 = w["r_R63"], w["r_R26"], w["r_R8"]
    checks = {
        "S_lists_neither": not set(new) & set(s["names"]) and not set(new) & set(s["names_ro"]),
        "W_lists_both_after_existing": w["names"] == s["names"] + new
        and w["new_last"]
        and not s["new_last"],
        "readonly_lists_them": w["names_ro"] == s["names_ro"] + new and w["new_last_ro"],
        "rationale_get_R63": r63["rule_state"] == "approved"
        and r63["notes"][0]["state"] == "current"
        and "why" in r63["notes"][0]
        and r63["metrics"]["RC"]["n"] == 5,
        "rationale_get_R26_no_text": r26["rule_state"] == "proposed_only"
        and r26["notes"] == []
        and r26["proposals"]["proposed"] == 2,
        "rationale_get_R8_stale_no_text": r8["rule_state"] == "stale"
        and r8["notes"][0]["state"] == "stale"
        and "why" not in r8["notes"][0],
        "glossary_get_term": w["g_term"]["term"]["slug"] == "active-window"
        and w["g_alias"]["term"]["slug"] == "past-period",
        "glossary_get_lint": w["g_lint"]["metrics"]["GA"] == 2 and w["g_lint"]["returned"] <= 5,
        "rule_get_adds_keys": w["rule_R97"]["terms"][0]["slug"] == "active-window"
        and w["rule_R63"]["rationale"]["state"] == "approved"
        and "why" in w["rule_R63"]["rationale"]
        and "rationale" not in s["rule_R63_keys"]
        and "terms" not in s["rule_R63_keys"],
        "errors": w["errors"]
        == {
            "bad_limit": "bad_limit",
            "unknown_rule": "unknown_rule",
            "unknown_term": "unknown_term",
            "bad_state": "bad_state",
        },
        "no_proposal_text": not any(m in text for m in PROPOSAL_MARKERS),
        "spec_lint_unchanged": w["spec_lint"]["count"] == s["spec_lint_count"],
    }
    r = result(
        "PM",
        "MCP on W and S: tools listed only when present; outputs; no proposal text; "
        "--readonly lists them",
        "as D12",
        {"checks": len(checks), "tools_W": w["names"][-4:]},
        all(checks.values()),
        checks=checks,
    )
    r["output_sha256"] = output_sha(o)
    return r


# ---- the byte-compat capture (PB) ----------------------------------------------------------------

PB_SCRIPT_G = r"""
import contextlib, hashlib, io, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

cfg = json.load(sys.stdin)
tree = Path(cfg["tree"])
sys.path.insert(0, str(tree))
os.chdir(tree)

def h(x):
    return hashlib.sha256(x.encode() if isinstance(x, str) else x).hexdigest()

def hj(o):
    return h(json.dumps(o, sort_keys=True, default=str))

def run(fn, argv, **kw):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(list(argv), **kw)
    return [rc, h(out.getvalue()), h(err.getvalue())]

out = {}
from lattice.llm.client import ScriptedGenerator
from lattice.spec import ask as A
from lattice.spec import implementation as impl
from lattice.spec import lint as L
from lattice.spec.draft import load_spec
from lattice.spec.rules import Trace, extract_rules

def load(root):
    root = Path(root)
    spec, errs = load_spec((root / "spec.py").read_text())
    return spec, Trace.from_json((root / "spec.trace.json").read_text())

worlds = {"S": cfg["S"], "S4": cfg["S4"]}
# 1. lint: text and JSON
for name, root in worlds.items():
    for js in (False, True):
        sp, tp = str(Path(root) / "spec.py"), str(Path(root) / "spec.trace.json")
        argv = ["--spec", sp, "--trace", tp]
        out[f"lint:{name}:json={int(js)}"] = run(L.main, argv + (["--json"] if js else []))
from tests.spec import racy
md = "# Runs\n\n- A config has at most one live run.\n- Runs are fast.\n"
utrace = Trace("REQ.md", extract_rules(md), links={"R1": ["invariant:one_live_run_per_config"]},
               unchecked={"R2": "no timing in the spec"})
from lattice.spec import lint as L2
out["lint:unit"] = hj([f.to_dict() for f in L2.lint(racy.RUNS_SPEC, utrace)])

# 2. digest, core, retrieve, answers
QUESTIONS = ["Can a user check in a habit?", "How many active habits can a user have?",
             "What happens to a nudge in quiet hours?", "Is there a delete endpoint?",
             "Can ben cancel?", "Why can't the period of a habit be changed?"]

def replies(d):
    rule = next(e for e in d.elements.values() if e.kind == "rule") if d.with_rules else None
    cell = next((e for e in d.elements.values() if e.kind == "cell"), None)
    scen = next((e for e in d.elements.values() if e.kind == "scenario" and e.steps), None)
    def r(kind, cites):
        return json.dumps({"kind": kind, "answer": "a", "explanation": "e", "citations": cites})
    def c(el, quote, step=None):
        return {"target": el.target, "step": step, "quote": quote}
    rq = (rule or cell or scen)
    bad = {"target": "rule:R99999", "step": None, "quote": "nothing"}
    seqs = [
        [r("yes", [c(rq, rq.content[:30])])],
        [r("no", [c(cell, cell.content)] if cell else [])] * 2,
        [r("value", [c(scen, scen.steps[0], 1)] if scen else [])] * 2,
        [r("not_specified", [])],
        [r("yes", [bad]), r("yes", [c(rq, rq.content[:30])])],
        [json.dumps({"need": [scen.target if scen else "x"], "search": ["habit"]}),
         r("yes", [c(rq, rq.content[:30])])],
    ]
    return seqs

def digest_block(key, spec, trace, inv, model_inv):
    d = A.build_digest(spec, trace, model_inv)
    out[key + ":digest"] = h(d.text)
    out[key + ":core"] = h(A.core_text(d))
    for q in QUESTIONS:
        rt = A.retrieve(d, q, [])
        out[f"{key}:retrieve:{q}"] = [h(rt.system), h(rt.context), h(json.dumps(sorted(rt.sent)))]
    for i, seq in enumerate(replies(d)):
        g = ScriptedGenerator(seq)
        a = A.ask(spec, QUESTIONS[i], trace=trace, generator=g, model="m", inventory=model_inv)
        dd = a.to_dict()
        dd["usage"] = {k: v for k, v in dd["usage"].items() if k not in ("seconds", "calls")}
        out[f"{key}:answer{i}"] = [h(json.dumps(dd, sort_keys=True)),
                                    h(json.dumps([[c[0], c[1]] for c in g.calls], sort_keys=True))]

for name, root in worlds.items():
    spec, trace = load(root)
    digest_block(f"{name}:noinv", spec, trace, None, None)
inv_s = impl.load_inventory(Path(cfg["inv_sdyn"]), Path(cfg["S"]))
inv_s4 = impl.load_inventory(Path(cfg["inv_s4stat"]), Path(cfg["S4"]))
spec, trace = load(cfg["S"])
digest_block("S:sdyn", spec, trace, None, inv_s)
spec4, trace4 = load(cfg["S4"])
digest_block("S4:s4stat", spec4, trace4, None, inv_s4)
udigest = A.build_digest(racy.RUNS_SPEC, utrace)
out["unit:digest"] = h(udigest.text)
out["unit:core"] = h(A.core_text(udigest))
for q in QUESTIONS[:3]:
    rt = A.retrieve(udigest, q, [])
    out[f"unit:retrieve:{q}"] = [h(rt.system), h(rt.context)]
# 3. the implementation report
out["report:S-dyn"] = h(impl.report_json(impl.build_report(spec, trace, inv_s)))
out["report:S4-stat"] = h(impl.report_json(impl.build_report(spec4, trace4, inv_s4)))
# 4. MCP: tool outputs of the single toolbox over the S clone
from lattice.mcp.tools import LocalBackend, Toolbox
tb = Toolbox(LocalBackend(Path(cfg["S"])))
out["mcp:tools"] = [tb.names(), h(json.dumps(tb.list_tools(), sort_keys=True))]
tbr = Toolbox(LocalBackend(Path(cfg["S"])), readonly=True)
out["mcp:tools_ro"] = [tbr.names(), h(json.dumps(tbr.list_tools(), sort_keys=True))]
rg = tb.call("rule_get", {"id": "R63"})
out["mcp:rule_get"] = hj(rg)
out["mcp:model_get"] = hj(tb.call("model_get", {}))
out["mcp:spec_lint"] = hj(tb.call("spec_lint", {}))
# 5. Studio: GET /api/model (minus root, git_head) and /api/ask with a stub
from lattice.studio import server as SV
from lattice.studio.server import Studio
reply = json.dumps({"kind": "yes", "answer": "a", "explanation": "e", "citations": [
    {"target": "rule:R63", "step": None, "quote": "A `PATCH` body that contains"}]})
st = Studio(Path(cfg["S"]), model="stub", generator=ScriptedGenerator([reply]))
m = st.get_model()
m = {**m, "project": {k: v for k, v in m["project"].items() if k not in ("root", "git_head")}}
out["studio:model"] = hj(m)
ans = st.ask({"question": "Why can't the period change?", "selected": []})
ans["usage"] = {k: v for k, v in ans["usage"].items() if k not in ("seconds", "calls")}
out["studio:ask"] = hj(ans)
out["files_hash:S"] = SV.files_hash(Path(cfg["S"]))
out["files_hash:S4"] = SV.files_hash(Path(cfg["S4"]))
# 6. sessions: profiles, prompts, argv
from lattice.studio import session as SS
out["session:profiles"] = hj({m_: [list(p.allowed), p.prompt, list(p.builtin), list(p.disallowed),
                                   p.readonly, p.enabled] for m_, p in SS.MODE_PROFILES.items()})
for mode in ("ask", "explain", "change"):
    out[f"session:prompt:{mode}"] = h(SS.system_prompt(mode, Path(cfg["S"]), ["rule:R63"], None))
    out[f"session:argv:{mode}"] = hj(SS.build_argv(
        claude_bin="claude", mode=mode, text="hi", model="sonnet", mcp_config=Path("/m.json"),
        settings=Path("/s.json"), prompt="p", resume=None))
tmp = Path(tempfile.mkdtemp())
mc, sc = SS.write_configs(tmp / "cfg", "ask", Path(cfg["S"]), "python")
out["session:configs"] = [h(mc.read_text()), h(sc.read_text())]
# 7. rebuild: the brief and the protected set
from lattice.studio import rebuild as RB
repo = tmp / "repo"
shutil.copytree(cfg["S"], repo, ignore=shutil.ignore_patterns("bin", ".git"), symlinks=True)
def git(*a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True)
git("init", "-q"); git("add", "-A")
git("-c", "user.email=a@b", "-c", "user.name=n", "commit", "-qm", "x")
head = git("rev-parse", "HEAD").stdout.strip()
rb = RB.Rebuilder(repo, None, name="p", model="m")
run_ = RB._Run(record={}, changes=[], repo=repo, worktree=repo, project_rel=".", base=head,
               branch="b", gates=["./check.sh"], targets=[], trace_json="")
out["rebuild:protected"] = rb._protected_paths(run_)
out["rebuild:spec_names"] = rb.spec_names()
out["rebuild:brief"] = h(RB.brief([{"id": "c1", "request": "x", "rules": {"added": ["R1"]}}],
    trace_json=(Path(cfg["S"]) / "spec.trace.json").read_text(), gates=["./check.sh"],
    targets=[], project_rel="."))
# 8. guard decisions on a 40-event corpus
from lattice.mcp.guard import Guard, MultiGuard
S = cfg["S"]
bash = ["ls", "cat spec.py", "echo hi > REQUIREMENTS.md", "sed -i s/a/b/ spec.py",
        "cp x spec.trace.json", "mv a spec.py", "rm spec.py", "rm -rf .", "tee spec.py",
        "python -c \"open('spec.py','w').write('')\"", "git checkout -- spec.py", "git status",
        "echo x >> notes.txt", "touch REQUIREMENTS.md", "cat spec.trace.json | head",
        "grep foo spec.py",
        "rm -rf /tmp/x", "cd .. && ls", "mkdir a", "echo x > b.txt", "cp spec.py /tmp/",
        "dd of=spec.py", "perl -pi -e s/a/b/ REQUIREMENTS.md", "ln -s a spec.py",
        "truncate -s0 spec.py",
        "echo > $PWD/spec.py", "chmod +x spec.py", "echo x > REQUIREMENTS.rationale.json",
        "sed -i s/a/b/ REQUIREMENTS.glossary.json", "cat REQUIREMENTS.rationale.json",
        "python -m lattice.spec check --help", "git diff"]
edits = [("Edit", "spec.py"), ("Write", "REQUIREMENTS.md"), ("MultiEdit", "internal/x.go"),
         ("Write", "../spec.trace.json"), ("Edit", str(Path(S) / "spec.trace.json")),
         ("Write", "REQUIREMENTS.rationale.json"), ("Write", "REQUIREMENTS.glossary.json"),
         ("NotebookEdit", "n.ipynb")]
events = [{"tool_name": "Bash", "tool_input": {"command": c}, "cwd": S} for c in bash]
events += [{"tool_name": t, "tool_input": {"file_path": f}, "cwd": S} for t, f in edits]
assert len(events) == 40
g = Guard(Path(S)); mg = MultiGuard([Path(S)])
out["guard"] = [[g.decide(e), mg.decide(e)] for e in events]
# 9. the check command's help
r = subprocess.run([sys.executable, "-m", "lattice.spec", "check", "--help"], capture_output=True,
                   text=True, cwd=tree, env={**os.environ, "PYTHONPATH": str(tree)})
out["check_help"] = [r.returncode, h(r.stdout), h(r.stderr)]
print(json.dumps(out, sort_keys=True))
"""


def run_pb_script(c: Ctx, tree: Path, label: str, pythonpath: Path) -> dict[str, Any]:
    script = c.scratch / "pb_script_g.py"
    script.write_text(PB_SCRIPT_G)
    cfg = json.dumps(
        {
            "tree": str(tree),
            "S": str(c.s),
            "S4": str(c.w.s4),
            "inv_sdyn": str(c.w.inv["S-dyn"]),
            "inv_s4stat": str(c.w.inv["S4-stat"]),
        }
    )
    p = P1.run_py(
        [str(script)],
        P1.fresh_tmp(c.scratch, "pbg-" + label),
        pythonpath=pythonpath,
        cwd=tree,
        stdin=cfg,
        timeout=1800,
    )
    assert p.returncode == 0, p.stderr[-1500:]
    return json.loads(p.stdout)


def probe_pb(c: Ctx) -> dict[str, Any]:
    new = run_pb_script(c, REPO, "new", REPO)
    old = run_pb_script(c, c.base, "old", c.base)
    diff = sorted(k for k in set(new) | set(old) if new.get(k) != old.get(k))
    checks = {"capture_identical": not diff, "keys": len(new) == len(old) and len(new) >= 80}
    checks["report_hashes_expected"] = (
        new["report:S-dyn"].startswith(R1_REPORT_SHA["S-dyn"][:8])
        and old["report:S-dyn"].startswith(R1_REPORT_SHA["S-dyn"][:8])
        and new["report:S4-stat"].startswith(R1_REPORT_SHA["S4-stat"][:8])
        and old["report:S4-stat"].startswith(R1_REPORT_SHA["S4-stat"][:8])
    )
    # MCP initialize instructions and tools/list: round 2's PB configs (8) + the single toolbox
    script = c.scratch / "pb_script_r1.py"
    script.write_text(P1.PB_SCRIPT)
    mn = P1.run_py(
        [str(script), str(REPO)], P1.fresh_tmp(c.scratch, "pbg-mcp-new"), pythonpath=REPO
    )
    mo = P1.run_py(
        [str(script), str(c.base)],
        P1.fresh_tmp(c.scratch, "pbg-mcp-old"),
        pythonpath=c.base,
        cwd=c.base,
    )
    assert mn.returncode == 0 and mo.returncode == 0, (mn.stderr[-600:], mo.stderr[-600:])
    jn, jo = json.loads(mn.stdout), json.loads(mo.stdout)
    mdiff = sorted(k for k in set(jn) | set(jo) if jn.get(k) != jo.get(k))
    checks["mcp_instructions_tools_list_identical"] = not mdiff
    ok = all(checks.values())
    r = result(
        "PB",
        "byte-compat, files absent, new code vs 8931d46 on S, S4 and the unit fixtures: lint, "
        "digest, core, retrieve, answers, report, MCP, Studio, files_hash, sessions, rebuild, "
        "guard, check --help",
        "all identical",
        {"checks": checks, "keys_compared": len(new), "differing": diff, "mcp_differing": mdiff},
        ok,
    )
    r["output_sha256"] = output_sha({"new": new, "mcp": jn})
    return r


def probe_pbp(c: Ctx) -> dict[str, Any]:
    """Invariants with W's files present (D13): lint identical, the old digest a prefix, the report
    minus D11's keys identical, spec_lint unchanged."""
    from lattice.llm.client import ScriptedGenerator  # noqa: F401
    from lattice.mcp.tools import LocalBackend, Toolbox
    from lattice.spec import ask as A
    from lattice.spec import glossary as gl
    from lattice.spec import implementation as impl
    from lattice.spec import lint as L

    R = c.roots()
    spec, trace = R.spec_trace(R.s)
    wspec, wtrace = R.spec_trace(R.W)
    notes = gl.discover_notes(wtrace, R.W / "spec.trace.json")
    checks = {}
    checks["lint_identical"] = canon([f.to_dict() for f in L.lint(spec, trace)]) == canon(
        [f.to_dict() for f in L.lint(wspec, wtrace)]
    )
    rc, lo_s, _ = run_main(
        L.main, ["--spec", str(R.s / "spec.py"), "--trace", str(R.s / "spec.trace.json"), "--json"]
    )
    rc2, lo_w, _ = run_main(
        L.main, ["--spec", str(R.W / "spec.py"), "--trace", str(R.W / "spec.trace.json"), "--json"]
    )
    checks["lint_cli_identical"] = (rc, lo_s) == (rc2, lo_w)
    d_old = A.build_digest(wspec, wtrace)
    d_new = A.build_digest(wspec, wtrace, None, notes)
    checks["old_digest_is_prefix"] = d_new.text.startswith(d_old.text) and len(d_new.text) > len(
        d_old.text
    )
    old_tail = d_new.text[len(d_old.text) :]
    checks["new_sections_only_at_end"] = (
        "## Rationale (owner-approved" in old_tail
        and "## Glossary (owner-approved" in old_tail
        and "## Rationale" not in d_old.text
    )
    old_rule_lines = [ln for ln in d_old.text.splitlines() if ln.startswith("[rule:")]
    new_rule_lines = [ln for ln in d_new.text.splitlines() if ln.startswith("[rule:")]
    checks["rule_lines_byte_identical"] = old_rule_lines == new_rule_lines
    checks["old_elements_unchanged"] = all(
        d_new.elements[t] == e for t, e in d_old.elements.items()
    )
    inv = impl.load_inventory(R.inv, R.s)
    plain = impl.build_report(wspec, wtrace, inv)
    rich = json.loads(json.dumps(impl.build_report(wspec, wtrace, inv, notes=notes)))
    for e in rich["rules"]:
        e.pop("terms"), e.pop("rationale")
    rich.pop("glossary")
    checks["report_minus_keys_identical"] = canon(rich) == canon(plain)
    tb_s = Toolbox(LocalBackend(R.s), readonly=True)
    tb_w = Toolbox(LocalBackend(R.W), readonly=True)
    checks["spec_lint_identical"] = canon(tb_s.call("spec_lint", {})) == canon(
        tb_w.call("spec_lint", {})
    )
    return result(
        "PBp",
        "invariants with W's files present (D13)",
        "lint identical; old digest a prefix; report minus D11 keys identical",
        checks,
        all(checks.values()),
    )


# ---- PD10: determinism ---------------------------------------------------------------------------


def compute_all(R: Roots) -> dict[str, Any]:
    ps = compute_ps(R)
    return {
        "ps": ps["raw"],
        "pr": compute_pr(R),
        "pc": compute_pc(R),
        "pg": compute_pg(R),
        "pa": compute_pa(R),
        "pi": compute_pi(R),
        "pm": compute_pm(R),
    }


def child_main() -> int:
    R = Roots.from_json(json.load(sys.stdin))
    print(canon(compute_all(R)))
    return 0


def probe_pd10(c: Ctx) -> dict[str, Any]:
    runs = c.args.runs
    main_roots = c.roots()
    here = canon(compute_all(main_roots))
    hashes = []
    seeds = []
    rng = random.Random(1016)
    script = Path(__file__).resolve()
    for i in range(runs):
        seed = str(rng.randint(1, 4_000_000_000))
        seeds.append(seed)
        p = P1.run_py(
            [str(script), "--child"],
            P1.fresh_tmp(c.scratch, f"pd10-{i}"),
            stdin=json.dumps(main_roots.to_json()),
            timeout=1800,
            extra_env={"PYTHONHASHSEED": seed},
        )
        assert p.returncode == 0, p.stderr[-1500:]
        hashes.append(h(p.stdout.strip()))
    p2 = P1.run_py(
        [str(script), "--child"],
        P1.fresh_tmp(c.scratch, "pd10-root2"),
        stdin=json.dumps(c.roots2().to_json()),
        timeout=1800,
    )
    assert p2.returncode == 0, p2.stderr[-1500:]
    root2 = h(p2.stdout.strip())
    want = h(here)
    ok = all(x == want for x in hashes) and root2 == want
    return result(
        "PD10",
        f"{runs} processes (random PYTHONHASHSEED) + a second archive root: PS outputs (25 "
        "cases), PR, PC, PG, PA answers, PI report, PM outputs",
        f"identical {runs}/{runs} + root",
        {
            "identical": sum(x == want for x in hashes),
            "runs": runs,
            "second_root": root2 == want,
            "canonical_sha256": want,
            "seeds": seeds,
        },
        ok,
    )


# ---- PW, PX --------------------------------------------------------------------------------------


def tree_hash(root: Path) -> str:
    items = []
    for dp, dns, fns in os.walk(root):
        dns.sort()
        for fn in sorted(fns):
            p = Path(dp) / fn
            if p.is_symlink():
                items.append((p.relative_to(root).as_posix(), "link:" + os.readlink(p)))
            else:
                items.append((p.relative_to(root).as_posix(), sha(p.read_bytes())))
    return h(canon(items))


def git_state(repo: Path) -> dict[str, Any]:
    st = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    files = sorted(
        str(p.relative_to(repo))
        for p in repo.rglob("*")
        if p.is_file()
        and ".git" not in p.relative_to(repo).parts
        and ".venv" not in p.relative_to(repo).parts
        and "node_modules" not in p.relative_to(repo).parts
        and "__pycache__" not in p.relative_to(repo).parts
    )
    return {"status": st.stdout, "files_sha": h(canon(files)), "n_files": len(files)}


def probe_px(c: Ctx) -> dict[str, Any]:
    from lattice.spec import ask as A
    from lattice.spec import glossary as gl
    from lattice.spec import rationale as rt

    R = c.roots()
    spec, trace = R.spec_trace(R.s)
    wspec, wtrace = R.spec_trace(R.W)
    notes = gl.discover_notes(wtrace, R.W / "spec.trace.json")
    paths = rt.notes_paths(R.W / "spec.trace.json")
    gl.run_lint(spec, trace, None, None)  # warm up
    t = {}
    t0 = time.perf_counter()
    gl.run_lint(spec, trace, None, None)
    t["glossary_lint_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    t0 = time.perf_counter()
    rt.load_set(wtrace, paths).metrics()
    t["rationale_coverage_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    reps = 5
    t0 = time.perf_counter()
    for _ in range(reps):
        A.build_digest(wspec, wtrace)
    plain = (time.perf_counter() - t0) / reps
    t0 = time.perf_counter()
    for _ in range(reps):
        A.build_digest(wspec, wtrace, None, notes)
    rich = (time.perf_counter() - t0) / reps
    t["digest_plain_ms"], t["digest_with_notes_ms"] = round(plain * 1000, 1), round(rich * 1000, 1)
    t["digest_overhead_pct"] = round((rich - plain) / plain * 100, 1)
    ok = (
        t["glossary_lint_ms"] <= 1000 and t["rationale_coverage_ms"] <= 1000 and rich - plain <= 1.0
    )
    return result(
        "PX",
        "wall, one call after load on S",
        "glossary lint 20-150 ms; rationale "
        "coverage 5-50 ms; digest build overhead <= 15% (pass: each <= 1 s)",
        t,
        ok,
        in_predicted_range={
            "glossary_lint": 20 <= t["glossary_lint_ms"] <= 150,
            "coverage": 5 <= t["rationale_coverage_ms"] <= 50,
            "digest_overhead_le_15pct": t["digest_overhead_pct"] <= 15,
        },
    )


# ---- the runner ----------------------------------------------------------------------------------

PROBE_FNS: dict[str, Callable[[Ctx], dict[str, Any]]] = {
    "PS": probe_ps,
    "PB": probe_pb,
    "PBp": probe_pbp,
    "PR": probe_pr,
    "PC": probe_pc,
    "PL": probe_pl,
    "PG": probe_pg,
    "PV": probe_pv,
    "PAP": probe_pap,
    "PDR": probe_pdr,
    "PA": probe_pa,
    "PI": probe_pi,
    "PM": probe_pm,
    "PD10": probe_pd10,
    "PX": probe_px,
}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--streak-ref", default="a0679e9")
    p.add_argument("--streak-ref2", default="1d00ede")
    p.add_argument("--base", default="8931d46")
    p.add_argument("--seeded", default=str(REPO / "dogfood/glossary_seeded.json"))
    p.add_argument("--world", default=str(REPO / "dogfood/rationale_agent_world.json"))
    p.add_argument("--runs", type=int, default=10)
    p.add_argument("--workflows-dyn", help="Spike B's frozen S-dyn workflows.json")
    p.add_argument("--inv-s4stat", help="round 1's frozen S4-stat inventory directory")
    p.add_argument("--scratch", required=False)
    p.add_argument("--out", required=False)
    p.add_argument("--only", help="comma-separated probe ids")
    p.add_argument("--reuse-world", action="store_true")
    p.add_argument("--base-tree", help="an existing git archive of the base (default: make one)")
    p.add_argument("--child", action="store_true")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    a = parser().parse_args(argv)
    if a.child:
        return child_main()
    if not a.scratch or not a.out:
        raise SystemExit("--scratch and --out are required")
    check_frozen(a)
    c = Ctx(a)
    c.out.mkdir(parents=True, exist_ok=True)
    (c.out / "probes").mkdir(exist_ok=True)
    t0 = time.time()
    owner_before = git_state(OWNER_STREAK) if OWNER_STREAK.is_dir() else None
    main_before = git_state(MAIN_CHECKOUT) if MAIN_CHECKOUT.is_dir() else None
    setup(c)
    wanted = [x for x in (a.only.split(",") if a.only else [*PROBES]) if x in PROBES]
    worlds_before = {k: tree_hash(v) for k, v in c.world_dirs().items() if v.is_dir()}
    results = []
    for pid in wanted:
        if pid == "PW":
            continue
        t1 = time.time()
        try:
            r = PROBE_FNS[pid](c)
        except Exception as err:  # noqa: BLE001 -- a probe that crashes is a failed probe
            import traceback

            r = result(
                pid,
                "crashed",
                "runs",
                f"{type(err).__name__}: {err}",
                False,
                traceback=traceback.format_exc()[-3000:],
            )
        r["seconds"] = round(time.time() - t1, 1)
        write_json(c.out / "probes" / f"{pid}.json", r)
        results.append(r)
        print(
            f"{pid}: {'PASS' if r['pass'] else ('report' if r['pass'] is None else 'FAIL')} "
            f"({r['seconds']}s)",
            flush=True,
        )
    if "PW" in wanted:
        worlds_after = {k: tree_hash(v) for k, v in c.world_dirs().items() if v.is_dir()}
        changed = sorted(k for k in worlds_before if worlds_before[k] != worlds_after.get(k))
        owner_after = git_state(OWNER_STREAK) if OWNER_STREAK.is_dir() else None
        main_after = git_state(MAIN_CHECKOUT) if MAIN_CHECKOUT.is_dir() else None
        pw_checks = {
            "worlds_unchanged": not changed,
            "owner_streak_unchanged": owner_before == owner_after,
            "main_checkout_unchanged": main_before == main_after,
        }
        r = result(
            "PW",
            "tree sha256 of each scratch world before vs after all read paths; git "
            "status + file listing of the owner's Streak and the main checkout before vs "
            "after",
            "0 changed, 0 new; exactly the named path",
            {
                "worlds": len(worlds_before),
                "changed": changed,
                "checks": pw_checks,
                "owner_before": owner_before,
                "owner_after": owner_after,
                "main_before": main_before,
                "main_after": main_after,
            },
            all(pw_checks.values()),
        )
        write_json(c.out / "probes" / "PW.json", r)
        results.append(r)
        print(f"PW: {'PASS' if r['pass'] else 'FAIL'}", flush=True)
    summary = {
        "ran": [r["id"] for r in results],
        "pass": {r["id"]: r["pass"] for r in results},
        "gated_failed": [r["id"] for r in results if r["id"] in GATED and r["pass"] is not True],
        "output_sha256": {r["id"]: r.get("output_sha256") for r in results},
        "seconds": round(time.time() - t0, 1),
        "frozen": {"seeded": SEEDED_SHA, "world": WORLD_SHA},
    }
    write_json(c.out / "summary.json", summary)
    return 1 if summary["gated_failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
