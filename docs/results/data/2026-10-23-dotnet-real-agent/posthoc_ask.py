"""POST-HOC reading of ask-result.json (written after the scored run; not part of the registered gates).

The registered audit (prereg section 4) counted a route mention as cited only when a ``code:`` citation named the route's handler
file and line (or quoted the route path). The model cited the route through the inventory element ``impl:route:<METHOD path>``
(whose quote holds ``code:<file>:<line>``), which the library accepted. This script re-counts with the element target accepted as a
citation of its route, and resolves every ``code:<file>:<line>`` token found in any citation quote against D1's source.

    uv run python docs/results/data/2026-10-23-dotnet-real-agent/posthoc_ask.py <D1 module dir> > ask-posthoc.json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "dogfood"))
import dotnet_probes as P  # noqa: E402

HERE = Path(__file__).resolve().parent
module_dir = Path(sys.argv[1])
res = json.loads((HERE / "ask-result.json").read_text())
out = {"registered": res["counts"], "rows": []}
total_unc = 0
for r in res["rows"]:
    cites = r["citations"]
    cited_routes = {m.group(1) for c in cites if (m := re.fullmatch(r"impl:route:(.+)", c["target"]))}
    tokens = sorted({(m.group(1), int(m.group(2))) for c in cites for m in P.CITE_RX.finditer(str(c.get("quote") or "") + " " + c["target"])})
    resolved = P.check_citation_lines(module_dir, tokens)
    unc = 0
    detail = []
    for ref in r["claim_audit"]["references"]:
        norm = re.sub(r"\{[^}]*\}", "{}", ref["ref"])
        ok = ref["cited"] or any(re.sub(r"\{[^}]*\}", "{}", x) == norm for x in cited_routes)
        detail.append({"ref": ref["ref"], "registered_cited": ref["cited"], "posthoc_cited": ok})
        unc += 0 if ok else 1
    total_unc += unc
    out["rows"].append({"question": r["question"], "references": detail, "code_tokens_in_citations": tokens, "code_tokens_resolve": resolved, "citation_targets": [c["target"] for c in cites]})
out["posthoc_uncited_references"] = total_unc
print(json.dumps(out, indent=1))
