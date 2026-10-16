"""P3: `claude auth status --json` (no model call) under planted variables, three arms.

usage: python p3_auth.py --runs N     (PYTHONPATH = the tree whose cli_env is tested)
Prints JSON; "NOT RUN" when claude is absent or logged out.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess

from lattice.llm.cli_client import cli_env

ap = argparse.ArgumentParser()
ap.add_argument("--runs", type=int, default=10)
a = ap.parse_args()

CORE = {
    "ANTHROPIC_API_KEY": "sk-ant-G9PROBE-k",
    "ANTHROPIC_AUTH_TOKEN": "G9PROBE-t",
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:9/G9PROBE",
    "CLAUDE_CODE_USE_BEDROCK": "1",
    "CLAUDE_CODE_USE_VERTEX": "1",
    "CLAUDE_CODE_USE_FOUNDRY": "1",
}
WIDE = {
    "ANTHROPIC_CUSTOM_HEADERS": "Authorization: Bearer G9WIDE",
    "ANTHROPIC_FOUNDRY_API_KEY": "G9WIDE",
    "AWS_BEARER_TOKEN_BEDROCK": "G9WIDE",
}
EXTRA = {
    "ANTHROPIC_VERTEX_PROJECT_ID": "G9WIDE-vertex",
    "ANTHROPIC_BEDROCK_BASE_URL": "http://127.0.0.1:9/G9WIDE",
    "ANTHROPIC_FOUNDRY_RESOURCE": "G9WIDE-foundry",
    "ANTHROPIC_MODEL": "G9WIDE-model",
    "ANTHROPIC_G9_UNLISTED": "G9WIDE-unlisted",
}
claude = shutil.which("claude")
if claude is None:
    print(json.dumps({"status": "NOT RUN", "reason": "claude not on PATH"}))
    raise SystemExit(0)
login = {k: os.environ[k] for k in ("HOME", "PATH", "USER", "TMPDIR") if k in os.environ}
planted = {**login, **CORE, **WIDE, **EXTRA}


def status(env: dict[str, str]) -> dict[str, object]:
    r = subprocess.run([claude, "auth", "status", "--json"], env=env, capture_output=True,
                       text=True, timeout=60)
    try:
        d = json.loads(r.stdout)
    except ValueError:
        return {"exit": r.returncode, "raw": r.stdout[-300:], "stderr": r.stderr[-300:]}
    return {k: d.get(k) for k in ("loggedIn", "authMethod", "apiProvider")}


arms = {
    "a": cli_env(planted, None),
    "b": dict(planted),
    "c": {**cli_env(planted, None), "CLAUDE_CODE_OAUTH_TOKEN": "G9KEEP-fake-oauth"},
}
want = {
    "a": lambda s: s == {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"},
    "b": lambda s: s.get("apiProvider") not in (None, "firstParty"),
    "c": lambda s: s.get("authMethod") == "oauth_token",
}
base = status(login)  # reference: login only, nothing planted
if base.get("loggedIn") is not True:
    print(json.dumps({"status": "NOT RUN", "reason": "claude not logged in", "reference": base}))
    raise SystemExit(0)
res = {"reference_login_only": base, "claude": claude, "arms": {}}
for name, env in arms.items():
    obs = [status(env) for _ in range(a.runs)]
    res["arms"][name] = {
        "env_names_sorted": sorted(env),
        "observed": obs[0],
        "identical_across_runs": all(o == obs[0] for o in obs),
        "pass": all(want[name](o) for o in obs),
    }
res["status"] = "RAN"
res["pass"] = all(v["pass"] and v["identical_across_runs"] for v in res["arms"].values())
print(json.dumps(res, indent=1, sort_keys=True))
