# ruff: noqa: E501
"""Re-label dnparity1's own truth (lattice.dntruth/1) in the held-out schema of prereg 4.5 (TRUTH_SCHEMA_DOTNET.md), for the self-test's Arm H.

    python dogfood/dn_standin/make_dnparity1_truth.py > dogfood/dn_standin/dnparity1/truth.json

Not a result: the cause of a refusal is a stand-in mapping of its status (404 not_found, 401 and 403 auth/not_owned, 409 conflict, 400/422 invalid)."""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
t = json.loads((REPO / "tests/dncheck/fixtures/dnparity1/truth.json").read_text())
CAUSE = {
    404: "not_found",
    401: "auth",
    403: "not_owned",
    409: "conflict",
    400: "invalid",
    422: "invalid",
}
out = {
    "standin": "dnparity1 re-labelled as the held-out arm (self-test only)",
    "routes": [
        {k: r[k] for k in ("method", "template", "auth", "style", "file", "line")}
        for r in t["routes"]
    ],
    "tables": [
        {
            "entity": tb["entity"],
            "table": tb["table"],
            "columns": [
                {
                    k: c[k]
                    for k in (
                        "name",
                        "wire",
                        "private",
                        "nullable",
                        "unique",
                        "pk",
                        "max_length",
                        "fk",
                    )
                }
                for c in tb["columns"]
            ],
        }
        for tb in t["tables"]
    ],
    "owners": t["owners"],
    "owner_via": t["owner_via"],
    "machines": t["machines"],
    "refusals": [
        {
            "route": r["route"],
            "status": r["status"],
            "detail": r["detail"],
            "cause": CAUSE.get(r["status"], "other"),
            "file": r["file"],
            "line": r["line"],
        }
        for r in t["raises"]
        if r["status"] >= 400
    ],
    "bounds": [
        {
            k: b[k]
            for k in (
                "route",
                "field",
                "required",
                "ge",
                "le",
                "min_length",
                "max_length",
                "enforced_by",
                "file",
                "line",
            )
        }
        for b in t["bounds"]
    ],
    "bind_status": t["bind_status"],
    "json_naming": t["json_naming"],
    "login": {"path": "/login", "fields": ["email", "password"], "token_field": "token"},
    "seed_users": [],
    "not_returned": t["not_returned"],
}
json.dump(out, sys.stdout, indent=1)
sys.stdout.write("\n")
