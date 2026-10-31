#!/usr/bin/env python3
# ruff: noqa: E501
"""T1-4: the real-model C-ASK on the Streak Go spec with the corrected citation definition (the ``claude`` CLI only, never an API key).

Pre-registration: docs/results/2026-10-30-t14-casko-corrected-citation-prereg.md (committed before this driver; the guard refuses a
model call otherwise). Questions and gold key: dogfood/t14_casko_questions.json. The audit: dogfood/t14_casko_audit.py.

    env -u ANTHROPIC_API_KEY uv run python dogfood/t14_casko_run.py ask --out docs/results/data/2026-10-30-t14 --confirm-spend   # ONCE
    uv run python dogfood/t14_casko_run.py ask --dry-run gold      --out docs/results/data/2026-10-30-t14/dry-run/gold        # no model
    uv run python dogfood/t14_casko_run.py ask --dry-run wrongcite --out docs/results/data/2026-10-30-t14/dry-run/wrongcite   # no model

Every ``claude`` call goes through dogfood/t14_casko_shim.py (ledger ``OUT/ledger.jsonl``, ``--max-budget-usd`` per call, refusal
near the cap). Total cap USD 0.60; a question starts only with USD 0.12 left. ``--dry-run`` swaps the real ``claude`` for
dogfood/t14_casko_fake_claude.py behind the same shim: same path, same ledger, spend 0.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (str(REPO), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import t14_casko_audit as A  # noqa: E402

PREREG = REPO / "docs/results/2026-10-30-t14-casko-corrected-citation-prereg.md"
QUESTIONS = HERE / "t14_casko_questions.json"
SHIM = HERE / "t14_casko_shim.py"
FAKE = HERE / "t14_casko_fake_claude.py"
DATA = REPO / "docs/results/data/2026-10-30-t14"
STREAK = Path.home() / "Documents" / "streak"
STREAK_REV = "a0679e9"
STREAK_SHA256 = {  # prereg section 2
    "spec.py": "28dfc2e6c6a2aa27168079740d28a94de7f3efcb7cf8234d795d48b7960f5624",
    "spec.trace.json": "627a2b3f4c9cfafc3ad6cd5edb01f355fb76b53c248c629e7a77fd1bd8ee82ed",
    "REQUIREMENTS.md": "dcac6deb3ad3ec4ab7381af040cb86064aae00eb3ed38c05237657156fbad7b1",
    "CONTEXT.md": "61b8040f8a88f2a53fa4270cd36f4eacc0228310a006bdc8f82c411f1b862ce6",
}

MODEL = "sonnet"
CAP = 0.60  # USD, total, hard
CALL_CAP = 0.30  # the --max-budget-usd ceiling of one call
START_RESERVE = 0.12  # a question starts only with this much of the cap left (two later calls and the margin)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).stdout.strip()


def load_questions() -> list[dict[str, Any]]:
    return json.loads(QUESTIONS.read_text())["questions"]


def may_start(spent: float) -> bool:
    return round(CAP - spent, 6) >= START_RESERVE


# ---- the pre-call guard (prereg section 7) -------------------------------------------------------------------------------------------------------------


def frozen_files() -> list[Path]:
    return [PREREG, QUESTIONS, Path(__file__).resolve(), HERE / "t14_casko_audit.py", SHIM, HERE / "dn_real_agent_shim.py", FAKE]


def _claude_version() -> str:
    return subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()


def ledger_lines(ledger: Path) -> list[dict[str, Any]]:
    out = []
    if ledger.is_file():
        for line in ledger.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def spent(out: Path) -> float:
    return round(sum(float(d.get("cost_usd") or 0.0) for d in ledger_lines(out / "ledger.jsonl")), 6)


def guard(
    out: Path,
    *,
    confirm_spend: bool,
    repo: Path = REPO,
    prereg: Path = PREREG,
    driver: Path | None = None,
    files: Sequence[Path] | None = None,
    claude_version: Callable[[], str] = _claude_version,
) -> dict[str, Any]:
    """Refuses (SystemExit) a model call unless: --confirm-spend; every frozen file committed and unchanged; the prereg added in a commit
    older than (an ancestor of) the driver's; no result and no model call in `out` yet. Strips ANTHROPIC_* and writes the freeze."""
    driver = driver or Path(__file__).resolve()
    files = list(files if files is not None else frozen_files())
    if not confirm_spend:
        raise SystemExit("refusing to call the model: pass --confirm-spend (the paid run happens once; prereg section 7)")
    rel = {str(p.resolve().relative_to(repo.resolve())): p for p in files}
    dirty = [f for f in rel if git(repo, "status", "--porcelain", "--", f) or not git(repo, "ls-files", "--", f)]
    if dirty:
        raise SystemExit(f"refusing to call the model: not committed (prereg first, then the runner): {dirty}")

    def added(p: Path) -> str:
        shas = git(repo, "log", "--diff-filter=A", "--format=%H", "--", str(p.resolve().relative_to(repo.resolve()))).split()
        return shas[-1] if shas else ""

    pre, drv = added(prereg), added(driver)
    if not pre or not drv or pre == drv or subprocess.run(["git", "merge-base", "--is-ancestor", pre, drv], cwd=repo).returncode != 0:
        raise SystemExit("refusing to call the model: the prereg must be committed before the driver, in its own commit")
    if (out / "ask-result.json").exists():
        raise SystemExit(f"{out / 'ask-result.json'} exists: the run happens once (a failure is a result)")
    calls = [d for d in ledger_lines(out / "ledger.jsonl") if not d.get("refused")]
    if calls:
        raise SystemExit(f"refusing: the ledger already holds {len(calls)} model call(s); a relaunch is allowed only before the first model call")
    for k in list(os.environ):
        if k.startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_")) or k == "AWS_BEARER_TOKEN_BEDROCK":
            del os.environ[k]
    info = {"head": git(repo, "rev-parse", "HEAD"), "prereg_commit": pre, "driver_commit": drv, "files": {f: sha(p.read_bytes()) for f, p in rel.items()}}
    out.mkdir(parents=True, exist_ok=True)
    (out / "ask-freeze.json").write_text(json.dumps({**info, "claude_version": claude_version(), "cap_usd": CAP, "model": MODEL, "at": time.time()}, indent=1))
    return info


def shim_env(out: Path, *, real: str) -> dict[str, str]:
    return {
        "DNRA_REAL": real,
        "DNRA_LEDGER": str(out / "ledger.jsonl"),
        "DNRA_CAP": f"{CAP:.2f}",
        "DNRA_PHASE": "ask",
        "DNRA_PHASE_CAP": f"{CAP:.2f}",
        "DNRA_CALL_CAP": f"{CALL_CAP:.2f}",
        "DNRA_LOGDIR": str(out / "ask-raw"),
    }


# ---- the target: Streak at a0679e9, its inventory, digest and app map ----------------------------------------------------------------------------------


def prepare_target(scratch: Path) -> dict[str, Any]:
    from lattice.importer.inventory import capture
    from lattice.spec import appmap as appmap_mod
    from lattice.spec.ask import build_digest
    from lattice.spec.implementation import load_inventory
    from lattice.spec.rules import Trace

    app, inv_dir = scratch / "app", scratch / "inv"
    shutil.rmtree(app, ignore_errors=True)
    shutil.rmtree(inv_dir, ignore_errors=True)
    app.mkdir(parents=True)
    arch = subprocess.run(["git", "-C", str(STREAK), "archive", STREAK_REV], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(arch)) as tf:
        tf.extractall(app, filter="data")
    got = {f: sha((app / f).read_bytes()) for f in STREAK_SHA256}
    if got != STREAK_SHA256:
        raise SystemExit(f"Streak at {STREAK_REV} differs from the prereg's pinned hashes: {got}")
    meta = capture(app, inv_dir, package=".")
    sp = importlib.util.spec_from_file_location(f"t14_streak_spec_{abs(hash(str(app)))}", app / "spec.py")
    assert sp and sp.loader
    mod = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(mod)
    spec, trace = mod.SPEC, Trace.load(app / "spec.trace.json")
    inv = load_inventory(inv_dir, app)
    digest = build_digest(spec, trace, inv)
    try:
        am, am_err = appmap_mod.build(spec, trace, inv), None
    except Exception as err:  # noqa: BLE001 -- no map: the checker still runs; recorded
        am, am_err = None, f"{type(err).__name__}: {err}"
    return {"app": app, "spec": spec, "trace": trace, "inventory": inv, "digest": digest, "appmap": am, "appmap_error": am_err,
            "inventory_meta": {"extract_sha256": meta["extract_sha256"], "source_files": len(meta["source"]["files"])}}


# ---- one question --------------------------------------------------------------------------------------------------------------------------------------


class Recorder:
    """The generator ask() calls, recording every reply text (the raw arm is the first answer reply)."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.texts: list[str] = []

    def generate(self, system: str, messages: Sequence[Any]) -> Any:
        gen = self.inner.generate(system, messages)
        self.texts.append(gen.text)
        return gen


def raw_reply(texts: list[str], with_impl: bool) -> dict[str, Any]:
    """The model's first answer reply: the first reply, or the second when the first asked for more (prereg section 5)."""
    from lattice.spec.ask import _json_object, _need_request, _parse_answer

    if not texts:
        return {"kind": "", "answer": "", "explanation": "", "citations": [], "parsed": False}
    pick = texts[0]
    try:
        if _need_request(_json_object(pick)) is not None and len(texts) > 1:
            pick = texts[1]
    except (ValueError, json.JSONDecodeError):
        pass
    parsed, err = _parse_answer(pick, with_impl)
    if parsed is None:
        return {"kind": "", "answer": pick, "explanation": "", "citations": [], "parsed": False, "error": err}
    return {**parsed, "parsed": True}


def ask_one(q: dict[str, Any], t: dict[str, Any], client: Any, resolver: A.Resolver) -> dict[str, Any]:
    from lattice.spec import chat_route, claim_check
    from lattice.spec.ask import ask, check_citation

    routed = None
    if t["appmap"] is not None:
        try:
            r = chat_route.route(q["question"], appmap=t["appmap"])
            routed = r.route if r is not None else None
        except Exception as err:  # noqa: BLE001
            routed = f"error: {type(err).__name__}"
    rec = Recorder(client)
    t0 = time.time()
    ans = ask(t["spec"], q["question"], trace=t["trace"], generator=rec, model=MODEL, inventory=t["inventory"], digest=t["digest"])
    d = ans.to_dict()
    checked = claim_check.check_answer(d, claim_check.CheckContext(q["question"], appmap=t["appmap"], digest=t["digest"]))
    raw = raw_reply(rec.texts, True)
    raw_text = f"{raw['answer']}\n{raw['explanation']}"
    checked_text = f"{checked.get('answer') or ''}\n{checked.get('explanation') or ''}"
    raw_lib = [{"target": c["target"], "reason": check_citation(t["digest"], c)} for c in raw["citations"]]
    return {
        "id": q["id"], "category": q["category"], "question": q["question"], "status": "ANSWERED", "seconds": round(time.time() - t0, 1),
        "would_be_routed_model_free_by": routed, "model_replies": rec.texts,
        "raw": raw, "raw_library_rejects": [x for x in raw_lib if x["reason"] is not None],
        "checked": {k: checked.get(k) for k in ("kind", "answer", "explanation", "citations", "rejected_citations", "check", "implementation", "implementation_basis")},
        "downgraded_by_library": d["kind"] == "not_specified" and raw.get("kind") not in ("", "not_specified"),
        "usage": d.get("usage"),
        "audit_raw": A.audit(raw_text, raw["citations"], resolver),
        "audit_checked": A.audit(checked_text, checked.get("citations") or [], resolver, raw_text=raw_text),
        "score_raw": A.score(raw, q["key"]),
        "score_checked": A.score(checked, q["key"]),
        "key_implementation": q["key"].get("implementation"),
    }


# ---- the run -------------------------------------------------------------------------------------------------------------------------------------------


def cmd_ask(a: argparse.Namespace) -> int:
    from lattice.llm.cli_client import ClaudeCliClient, CliUsageLimitError

    out, scratch = Path(a.out), Path(a.scratch)
    dry = a.dry_run
    if dry:
        if out.resolve() == DATA.resolve():
            raise SystemExit("a dry run writes under its own directory (e.g. OUT/dry-run/<variant>), never the paid run's")
        for name in ("ledger.jsonl", "ask-result.json"):
            (out / name).unlink(missing_ok=True)
        shutil.rmtree(out / "ask-raw", ignore_errors=True)
        info = {"head": git(REPO, "rev-parse", "HEAD"), "dry_run": dry, "files": {str(p.relative_to(REPO)): sha(p.read_bytes()) for p in frozen_files()}}
        real = str(FAKE)
        os.environ.update({"T14_FAKE_MODE": dry, "T14_FAKE_COST": "0"})
    else:
        info = guard(out, confirm_spend=a.confirm_spend)
        real = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
    out.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    t = prepare_target(scratch)
    resolver = A.Resolver.from_digest(t["digest"], t["app"])
    os.environ.update(shim_env(out, real=real))
    empty = scratch / "ask-cwd"
    empty.mkdir(exist_ok=True)
    client = ClaudeCliClient(model=MODEL, binary=str(SHIM), cwd=empty, max_retries=1, refusal_retries=0, timeout=600.0)
    rows: list[dict[str, Any]] = []
    stop = ""
    for q in load_questions():
        if stop:
            rows.append({"id": q["id"], "question": q["question"], "status": "NOT RUN", "why": stop})
            continue
        if not may_start(spent(out)):
            stop = f"cap: USD {spent(out):.4f} spent, a question starts only with USD {START_RESERVE:.2f} of the {CAP:.2f} cap left"
            rows.append({"id": q["id"], "question": q["question"], "status": "NOT RUN", "why": stop})
            continue
        try:
            rows.append(ask_one(q, t, client, resolver))
        except CliUsageLimitError as err:
            stop = f"usage limit or the shim's cap refusal: {err}"
            rows.append({"id": q["id"], "question": q["question"], "status": "NOT RUN", "why": stop})
        except Exception as err:  # noqa: BLE001 -- a failure is a result
            rows.append({"id": q["id"], "question": q["question"], "status": "ERROR", "error": f"{type(err).__name__}: {err}"})
    result = summarise(rows, info, t, out, dry)
    (out / "ask-result.json").write_text(json.dumps(result, indent=1, default=str))
    print(json.dumps({"verdicts": result["verdicts"], "counts": result["counts"]}, indent=1))
    return 0


def summarise(rows: list[dict[str, Any]], info: dict[str, Any], t: dict[str, Any], out: Path, dry: str | None) -> dict[str, Any]:
    ok = [r for r in rows if r.get("status") == "ANSWERED"]
    n = len(load_questions())
    total = spent(out)

    def tot(arm: str, key: str) -> int:
        return sum(r[arm][key] if isinstance(r[arm][key], int) else len(r[arm][key]) for r in ok)

    counts = {
        "questions": n, "answered": len(ok),
        "not_run": sum(1 for r in rows if r.get("status") == "NOT RUN"), "errors": sum(1 for r in rows if r.get("status") == "ERROR"),
        "known_claims": tot("audit_checked", "known"), "uncited_known_claims": tot("audit_checked", "uncited"),
        "rejected_citations": sum(len(r["checked"]["rejected_citations"] or []) for r in ok),
        "checked_nonresolving_citations": tot("audit_checked", "nonresolving"),
        "raw_known_claims": tot("audit_raw", "known"), "raw_uncited_known_claims": tot("audit_raw", "uncited"),
        "raw_nonresolving_citations": tot("audit_raw", "nonresolving"), "raw_library_rejects": sum(len(r["raw_library_rejects"]) for r in ok),
        "failure_claims_raw": tot("audit_raw", "failure_claims"), "failure_claims_checked": tot("audit_checked", "failure_claims"),
        "unknown_route_mentions": tot("audit_checked", "unknown_routes"), "unknown_rule_mentions": tot("audit_checked", "unknown_rules"),
        "lattice_inserted_claims": tot("audit_checked", "inserted"),
        "downgraded_by_library": sum(1 for r in ok if r["downgraded_by_library"]),
        "correct_checked": sum(1 for r in ok if r["score_checked"]["correct"]), "correct_raw": sum(1 for r in ok if r["score_raw"]["correct"]),
        "damage": sum(1 for r in ok if r["score_raw"]["correct"] and not r["score_checked"]["correct"]),
        "total_usd": total, "model_calls": sum(1 for d in ledger_lines(out / "ledger.jsonl") if not d.get("refused")),
    }
    gates = {
        "A-ANSWERED": f"every question answered by the model: {n} of {n}",
        "A-CITED": "checked arm, prereg section 4: 0 known route / rule claims without a resolving, binding, accepted citation",
        "A-COST": f"ledger total <= USD {CAP:.2f}",
    }
    verdicts = {
        "A-ANSWERED": len(ok) == n,
        "A-CITED": counts["uncited_known_claims"] == 0,
        "A-COST": total <= CAP,
    }
    return {
        "dry_run": dry, "data_status": "development data, exploratory (post hoc citation definition; dev spec)", **info,
        "model": MODEL, "cap_usd": CAP, "streak_rev": STREAK_REV, "inventory": {**t["inventory_meta"], "observed_workflows": False},
        "appmap_error": t["appmap_error"], "gates": gates, "verdicts": verdicts, "counts": counts,
        "reported": {"A-REJECTED": counts["rejected_citations"], "A-CORRECT": f"{counts['correct_checked']}/{len(ok)} checked, {counts['correct_raw']}/{len(ok)} raw, damage {counts['damage']}"},
        "rows": rows,
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("ask",))
    ap.add_argument("--out", required=True, help="the data directory (ledger, raw CLI output, result)")
    ap.add_argument("--scratch", default="", help="scratch directory (default: a new temporary one)")
    ap.add_argument("--confirm-spend", action="store_true", help="required for the paid run (prereg section 7)")
    ap.add_argument("--dry-run", choices=("gold", "wrongcite"), default=None, help="the fake claude: no model call, spend 0")
    a = ap.parse_args(argv)
    if not a.scratch:
        a.scratch = tempfile.mkdtemp(prefix="t14-casko-")
    # absolute: the shim's ledger and log paths must not depend on any cwd (the .NET build's irregularity 2)
    a.out, a.scratch = str(Path(a.out).resolve()), str(Path(a.scratch).resolve())
    return {"ask": cmd_ask}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
