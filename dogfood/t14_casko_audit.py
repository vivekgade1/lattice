# ruff: noqa: E501
"""The corrected citation audit of T1-4 (docs/results/2026-10-30-t14-casko-corrected-citation-prereg.md, section 4), and the key's scorer (section 6).

Pure: no model, no subprocess, no write; it only reads the digest's elements and, for ``code:`` targets, the files under the app root.

    r = Resolver.from_digest(digest)              # lattice.spec.ask.build_digest(spec, trace, inventory)
    audit(text, citations, r)                     # the claims of an answer's text and which citation cites each
    score(answer, key)                            # the mechanical A-CORRECT score of one answer against the gold key

A **route claim** is every ``METHOD /path`` that ``implementation.routes_in_text`` finds (keyed by ``route_key``: parameters become ``{}``);
a **rule claim** every id ``claim_check.RULE_RE`` finds. A claim is *known* when the digest has it (``impl:route:K`` or a ``cell:...|K``;
``rule:Rn``); unknown mentions are listed apart. A known claim is **cited** iff one of the given citations *resolves* (the target exists:
an ``impl:*`` element built from the inventory, a spec element, ``code:<path>:<line>`` with the file and the line) and *binds* the claim
(the table of section 4.3). ``rationale:``/``term:`` targets and any other prefix, ``spec:`` included, never resolve.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lattice.spec import implementation as impl
from lattice.spec.claim_check import RULE_RE, scorer_claims

# the target kinds lattice.spec.ask.check_citation accepts (prereg 4.1): prefix -> the Element kind it must be
IMPL_KINDS = {"impl:route:": "impl_route", "impl:rule:": "impl_rule", "impl:machine:": "impl_machine", "impl:flow:": "impl_flow"}
SPEC_KINDS = {
    "profile:": "profile",
    "cell:": "cell",
    "scenario:": "scenario",
    "invariant:": "invariant",
    "machine:": "machine",
    "service:": "service",
    "unchecked:": "unchecked",
    "rule:": "rule",
}
NOTE_PREFIXES = ("rationale:", "term:")  # accepted by the library, but they never ground an answer: never a citation of a claim here


def route_claims(text: str) -> list[str]:
    return list(dict.fromkeys(impl.routes_in_text(text or "")))


def rule_claims(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(1) for m in RULE_RE.finditer(text or "")))


def _routes_of(*texts: str) -> set[str]:
    return set(impl.routes_in_text(" ".join(texts)))


@dataclass
class Resolver:
    """What the audit needs of a digest: its elements, the files of each route's registration and handler (the inventory's evidence),
    the routes a rule needs (``ImplContext.target_endpoints``), and the app root the ``code:`` targets are read under."""

    elements: Mapping[str, Any]
    route_files: Mapping[str, set[str]]
    rule_endpoints: Callable[[str], set[str]]
    app_root: Path | None

    @classmethod
    def from_digest(cls, digest: Any, app_root: Path | str | None = None) -> Resolver:
        ctx = digest.impl
        files: dict[str, set[str]] = {}
        if ctx is None:
            return cls(digest.elements, files, lambda rid: set(), Path(app_root) if app_root else None)
        for r in ctx.report["routes"]:
            parsed = (impl.parse_code_target(str(ev)) for ev in r.get("evidence") or ())
            files[r["key"]] = {p[0] for p in parsed if p is not None}
        root = Path(app_root) if app_root is not None else Path(ctx.inv.app_root)
        return cls(digest.elements, files, lambda rid: set(ctx.target_endpoints(f"rule:{rid}")), root)

    # -- known claims
    def route_known(self, key: str) -> bool:
        return f"impl:route:{key}" in self.elements or any(t.startswith("cell:") and t.partition("|")[2] == key for t in self.elements)

    def rule_known(self, rid: str) -> bool:
        return f"rule:{rid}" in self.elements

    # -- resolving (existence only; the library's quote check is separate)
    def resolves(self, target: str) -> tuple[bool, str]:
        target = str(target or "")
        if target.startswith("code:"):
            return self._code(target)
        if target.startswith(NOTE_PREFIXES):
            return False, "a rationale or term note never grounds a claim"
        for kinds in (IMPL_KINDS, SPEC_KINDS):
            for prefix, kind in kinds.items():
                if target.startswith(prefix):
                    el = self.elements.get(target)
                    if el is None:
                        return False, f"no {kind} element {target!r}"
                    if el.kind != kind:
                        return False, f"{target!r} is a {el.kind}, not a {kind}"
                    return True, ""
        return False, f"{target!r} is not a target kind the library accepts"

    def _code(self, target: str) -> tuple[bool, str]:
        parsed = impl.parse_code_target(target)
        if parsed is None:
            return False, "not code:<relative path>:<line>"
        rel, line = parsed
        if self.app_root is None:
            return False, "no app root"
        if rel.startswith("/") or "\\" in rel or any(c in ("", ".", "..") for c in rel.split("/")):
            return False, f"{rel!r} is not a relative, normalised path"
        f = self.app_root / rel
        if not f.is_file():
            return False, f"no file {rel!r}"
        n = len(f.read_text(errors="replace").splitlines())
        if not 1 <= line <= n:
            return False, f"{rel!r} has no line {line} (it has {n})"
        return True, ""

    # -- binding (prereg 4.3)
    def binds(self, citation: Mapping[str, Any], kind: str, claim: str) -> bool:
        target = str(citation.get("target") or "")
        if kind == "rule":
            if target in (f"rule:{claim}", f"impl:rule:{claim}"):
                return True
            if target.startswith("unchecked:"):
                el = self.elements.get(target)
                return el is not None and re.match(rf"{re.escape(claim)}\b", str(el.content)) is not None
            return False
        if target == f"impl:route:{claim}":
            return True
        if target.startswith("cell:"):
            return target.partition("|")[2] == claim
        if target.startswith("code:"):
            parsed = impl.parse_code_target(target)
            return parsed is not None and parsed[0] in self.route_files.get(claim, set())
        el = self.elements.get(target)
        if el is None:
            return False
        if target.startswith(("rule:", "impl:rule:")):
            rid = target.rpartition(":")[2]
            rule = self.elements.get(f"rule:{rid}")
            return claim in self.rule_endpoints(rid) or (rule is not None and claim in _routes_of(str(rule.content)))
        if target.startswith("scenario:"):
            steps = list(el.raw or ())
            step = citation.get("step")
            if isinstance(step, int) and not isinstance(step, bool):
                steps = steps[step - 1 : step] if 1 <= step <= len(steps) else []
            return any(
                hasattr(s, "method") and hasattr(s, "path") and impl.route_key(str(s.method), str(s.path)) == claim for s in steps
            )
        if target.startswith("impl:route:"):
            return False
        return claim in _routes_of(str(el.content), *map(str, el.steps))


def audit(
    text: str,
    citations: Sequence[Mapping[str, Any]],
    resolver: Resolver,
    *,
    raw_text: str | None = None,
) -> dict[str, Any]:
    """The claims of `text` and the citation (of `citations`) that cites each. `raw_text`: the same answer's raw model text; a known claim of
    `text` absent from it is Lattice-inserted (reported apart, never uncited)."""
    res = [(c, *resolver.resolves(str(c.get("target") or ""))) for c in citations]
    raw_claims = None if raw_text is None else {*route_claims(raw_text), *rule_claims(raw_text)}
    claims: list[dict[str, Any]] = []
    unknown_routes: list[str] = []
    unknown_rules: list[str] = []
    for kind, found, known in (
        ("route", route_claims(text), resolver.route_known),
        ("rule", rule_claims(text), resolver.rule_known),
    ):
        for claim in found:
            if not known(claim):
                (unknown_routes if kind == "route" else unknown_rules).append(claim)
                continue
            binders = [str(c.get("target")) for c, ok, _ in res if ok and resolver.binds(c, kind, claim)]
            inserted = raw_claims is not None and claim not in raw_claims
            claims.append({"claim": claim, "kind": kind, "known": True, "cited": bool(binders), "binders": binders, "inserted": inserted})
    counted = [c for c in claims if not c["inserted"]]
    return {
        "claims": claims,
        "known": len(counted),
        "uncited": sum(1 for c in counted if not c["cited"]),
        "uncited_claims": [c["claim"] for c in counted if not c["cited"]],
        "inserted": [c["claim"] for c in claims if c["inserted"]],
        "unknown_routes": unknown_routes,
        "unknown_rules": unknown_rules,
        "citations": [{"target": str(c.get("target")), "resolves": ok, "reason": why} for c, ok, why in res],
        "nonresolving": sum(1 for _, ok, _ in res if not ok),
        "failure_claims": scorer_claims(text or "", set()),
    }


def _text(answer: Mapping[str, Any]) -> str:
    return f"{answer.get('answer') or ''}\n{answer.get('explanation') or ''}"


def score(answer: Mapping[str, Any], key: Mapping[str, Any], citations: Iterable[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """A-CORRECT of one answer (prereg section 6). `citations`: the arm's citations (default: the answer's own)."""
    text = _text(answer)
    cites = list(answer.get("citations") or ()) if citations is None else list(citations)
    hay = text + "\n" + "\n".join(f"{c.get('target') or ''} {c.get('quote') or ''}" for c in cites)
    why: list[str] = []
    kind = str(answer.get("kind") or "")
    if kind not in key["kinds"]:
        why.append(f"kind {kind!r} not in {key['kinds']}")
    abstained = kind == "not_specified" and key.get("abstain_ok")
    if not abstained:
        for group in key.get("must") or ():
            if not any(re.search(rx, hay, re.IGNORECASE) for rx in group):
                why.append(f"missing {group}")
        have = set(route_claims(text))
        missing = [r for r in key.get("routes_required") or () if r not in have]
        if missing:
            why.append(f"routes missing {missing}")
    for rx in key.get("must_not") or ():
        if re.search(rx, text, re.IGNORECASE):
            why.append(f"must_not {rx!r}")
    return {"correct": not why, "why": why}
