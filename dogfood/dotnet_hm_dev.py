# ruff: noqa: E501
"""DEVELOPMENT run of the 16 held-out mutants HM1-16 on H1 (no freeze, no ledger: nothing scored; H1 and its mutants are dev data once the
blind gate has been scored). It runs ``dotnet_mutants.run_hmut`` -- the control, then the mutants through the layers the registered
detectors name -- with the pipeline as ``lattice check`` runs it on the imported candidate spec (layer A under ``dn_spec_policy`` of the
imported spec, layer R on the imported items).

    python dogfood/dotnet_hm_dev.py --out OUT --scratch SCRATCH [--only 1,2] [--policy-only]

Defaults: H1 ``docs/results/data/2026-10-18-dotnet/h1.tgz``, its truth ``h1-truth.json``, the mutants ``h1-mutants-run/``. The result is
``OUT/hm-dev.json`` and ``OUT/evidence/hmNN.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True

import dotnet_mutants as M  # noqa: E402
import dotnet_probes as G  # noqa: E402

DATA = G.REPO / "docs/results/data/2026-10-18-dotnet"


class DevLib(G.RealLib):
    """``RealLib`` with the two things ``lattice check`` does that the frozen runner does not: layer A under the policy the SPEC states
    (``DnPolicy.from_spec``; ``policy="spec_policy"`` keeps the runner's derived-under-the-spec policy), and layer R logging in again
    in each probe's session for an ASP.NET Core app, a 5xx to the anonymous caller no refusal (``relogin``, ``crash_admits``, as ``check.py`` passes them)."""

    policy = "from_spec"

    def layer_a(self, ctx: G.Ctx, root: Path, kind: str, **kw: object) -> dict:  # type: ignore[override]
        return super().layer_a(ctx, root, self.policy if kind == "spec_policy" else kind, **kw)  # type: ignore[arg-type]

    def r_verdicts(self, ctx: G.Ctx, tgt: dict, cand_dir: Path, variant: str, *, spec: object = None) -> dict[str, str]:  # type: ignore[override]
        from dataclasses import replace

        from lattice.spec.probes import run_probes

        m = self.machinery(ctx, tgt)
        cand = spec if spec is not None else G.load_candidate(cand_dir)
        seeder, res, rules = m["seeder"], m["result"], m["rules"]
        who = rules.callers(res, seeder, variant)
        kw = dict(login=who.login, mint_token=who.mint, scheme=who.scheme, refusals=who.refusals, login_route=who.login_route, auth_headers=who.headers, inactive_login=who.inactive_login, unreachable=m["unreachable"], variant=variant, auth_present=who.present, json_login=getattr(who, "json_login", None), relogin=who.relogin, crash_admits=who.crash_admits)  # fmt: skip
        rr = run_probes(replace(cand.SPEC, users=seeder.credentials(variant)), m["sessions"], seeder.make_db, **kw)
        rules.mark_unclear(cand.SPEC, rr, m["unclear"])
        rules.mark_outside(cand.SPEC, rr, m["outside"])
        rules.mark_uploads(rr, m["uploads"])
        return G.verdict_map(rr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--h1", default=str(DATA / "h1.tgz"))
    ap.add_argument("--h1-truth", default=str(DATA / "h1-truth.json"))
    ap.add_argument("--patches", default=str(DATA / "h1-mutants-run"))
    ap.add_argument("--only", default="")
    ap.add_argument("--policy", default="from_spec", choices=["from_spec", "spec_policy"], help="layer A's policy: the spec's own (lattice check) or derived under the spec (the frozen runner)")
    ap.add_argument("--policy-only", action="store_true", help="import H1 and print the policy of its candidate spec; judge nothing")
    args = ap.parse_args(argv)
    out, scratch = Path(args.out).resolve(), Path(args.scratch).resolve()
    out.mkdir(parents=True, exist_ok=True)
    ns = argparse.Namespace(kind="mutants", freeze="", d1="", h1=args.h1, h1_truth=args.h1_truth, streak="", container_owner="dn-r")
    ctx = M.mutants_ctx(ns, scratch, out)
    ctx.lib = DevLib()
    DevLib.policy = args.policy
    h1, truth_path, patches = Path(args.h1).resolve(), Path(args.h1_truth).resolve(), Path(args.patches).resolve()
    G.setup(ctx, ["H-ROUTES"], None, h1, truth_path)
    tgt = ctx.h_target
    if args.policy_only:
        cand_dir = G.import_dir(ctx, tgt)
        cand = G.load_candidate(cand_dir)
        from lattice.dncheck import derive as drv

        pol = drv.dn_spec_policy(cand.SPEC, tgt["module_dir"], drv.derive_dn_policy(tgt["module_dir"]))
        print(json.dumps(pol.to_json(), indent=2, sort_keys=True))
        return 0
    truth, rows, files = M.validate_inputs(patches, Path(tgt["truth_path"]))
    only = tuple(int(x) for x in args.only.split(",") if x) or M.IDS
    doc = M.run_hmut(ctx, tgt, patches, truth, rows, files, out, only=only)
    doc["pipeline"] = pipeline_view(doc.get("rows", ()))
    (out / "hm-dev.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")
    print(json.dumps(doc.get("gate", {}), default=str))
    print(f"registered detectors: {doc.get('gate', {}).get('killed')} of 16; any new layer A finding or refuted layer R item (what `lattice check` would report): {doc['pipeline']['killed']} of 16")
    for row in doc["pipeline"]["rows"]:
        print(f"  {row['id']:5} registered={row['registered']!s:5} pipeline={row['pipeline']!s:5} A={row['new_layer_a']} R refuted={row['refuted_r']} R lost={row['lost_r']}")
    return 0


def pipeline_view(rows: object) -> dict:
    """Beside the registered detectors (the runner's table, unchanged): a mutant the pipeline reports at all -- a layer A finding that the original does not
    have, or a layer R item that was verified on the original and is refuted on the mutant. Reported for information; it is not the registered gate."""
    out = []
    for r in rows:  # type: ignore[attr-defined]
        flips = r.get("layer_r", {}).get("flipped_items [control, mutant]", {})
        refuted = sorted(i for i, (a, b) in flips.items() if b == "refuted")
        lost = sorted(i for i, (a, b) in flips.items() if b != "refuted" and a in G.VERIFIED)
        new_a = r.get("layer_a", {}).get("new_findings", [])
        out.append({"id": r["id"], "registered": r.get("killed"), "pipeline": bool(new_a or refuted), "new_layer_a": new_a, "refuted_r": refuted, "lost_r": lost, "registered_by": r.get("killed_by")})
    return {"killed": sum(1 for x in out if x["pipeline"]), "rows": out}


if __name__ == "__main__":
    raise SystemExit(main())
