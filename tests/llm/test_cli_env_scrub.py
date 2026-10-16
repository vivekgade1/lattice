"""``lattice.llm.cli_client.cli_env``: what a ``claude -p`` child may inherit (HD-1, pre-registered
in ``docs/results/2026-10-16-hardening-findings-prereg.md``, decision D1 and probe P2).

Every name starting with ``ANTHROPIC_`` or ``CLAUDE_CODE_USE_`` and exactly
``AWS_BEARER_TOKEN_BEDROCK`` are dropped (case-sensitive: the CLI reads upper case only). The
login variables, ``CLAUDE_CODE_OAUTH_TOKEN``, generic cloud credentials and everything else are
kept. The same table must hold through the four callers that build a child environment from
``cli_env``: the client itself (a fake binary), ``mcp_intake.discovery_env`` and
``rebuild.gate_env`` (the session's own ``cli_env`` call is pinned by the HTTP module).
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

from lattice.llm.cli_client import ClaudeCliClient, cli_env
from lattice.studio.mcp_intake import discovery_env
from lattice.studio.rebuild import gate_env

# the 23 names of the pre-registration, kept and dropped interleaved (value = "v")
DROPPED = (
    # the three that were already dropped, with the CLAUDE_CODE_USE_ switches
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    # the wide three, now dropped
    "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_FOUNDRY_API_KEY",
    "AWS_BEARER_TOKEN_BEDROCK",
    # the rest of the ANTHROPIC_ namespace
    "ANTHROPIC_VERTEX_PROJECT_ID",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_FOUNDRY_RESOURCE",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_G9_UNLISTED",
)
KEPT = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "anthropic_api_key",
    "ANTHROPIC",
    "HOME",
    "PATH",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
)


def planted() -> dict[str, str]:
    """The 23 names, the dropped and the kept interleaved, every value ``v``."""
    names: list[str] = []
    for i in range(max(len(DROPPED), len(KEPT))):
        names += [*DROPPED[i : i + 1], *KEPT[i : i + 1]]
    assert len(names) == 23 and len(set(names)) == 23
    return dict.fromkeys(names, "v")


def kept_in_order(env: dict[str, str]) -> list[str]:
    return [k for k in planted() if k in env]


EXPECTED_KEPT = [k for k in planted() if k in KEPT]


def test_the_table_has_23_names_and_9_are_kept() -> None:
    assert len(planted()) == 23
    assert len(EXPECTED_KEPT) == 9 and set(EXPECTED_KEPT) == set(KEPT)


def test_cli_env_keeps_exactly_the_kept_names_in_input_order() -> None:
    out = cli_env(planted(), None)
    assert list(out) == EXPECTED_KEPT
    assert set(out.values()) == {"v"}


def test_cli_env_matches_case_sensitively_and_by_prefix() -> None:
    base = {
        "ANTHROPIC_": "x",  # the bare prefix is in the namespace
        "anthropic_api_key": "x",
        "Anthropic_Api_Key": "x",
        "XANTHROPIC_API_KEY": "x",
        "ANTHROPIC": "x",
        "claude_code_use_bedrock": "x",
        "CLAUDE_CODE_USE_": "x",
        "CLAUDE_CODE_USE_BEDROCK": "x",
        "CLAUDE_CODE_OAUTH_TOKEN": "x",
        "aws_bearer_token_bedrock": "x",
        "AWS_BEARER_TOKEN_BEDROCK": "x",
        "AWS_BEARER_TOKEN_BEDROCK2": "x",
        "HTTPS_PROXY": "x",
    }
    kept = set(cli_env(base, None))
    assert kept == {
        "anthropic_api_key",
        "Anthropic_Api_Key",
        "XANTHROPIC_API_KEY",
        "ANTHROPIC",
        "claude_code_use_bedrock",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "aws_bearer_token_bedrock",
        "AWS_BEARER_TOKEN_BEDROCK2",
        "HTTPS_PROXY",
    }


def test_cli_env_sets_the_output_cap_after_scrubbing() -> None:
    out = cli_env({"ANTHROPIC_API_KEY": "k", "HOME": "/h"}, 1234)
    assert out == {"HOME": "/h", "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "1234"}


FAKE = """#!{python}
import json, os, sys
sys.stdin.read()
with open({log!r}, "a") as f:
    f.write(json.dumps(dict(os.environ)) + "\\n")
print(json.dumps({{"result": "ok", "is_error": False, "stop_reason": "end_turn",
                   "total_cost_usd": 0, "usage": {{"input_tokens": 1, "output_tokens": 1}},
                   "modelUsage": {{"m": {{}}}}}}))
"""


def test_the_table_holds_through_the_client(tmp_path: Path) -> None:
    log = tmp_path / "env.jsonl"
    binary = tmp_path / "claude"
    binary.write_text(FAKE.format(python=sys.executable, log=str(log)))
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    client = ClaudeCliClient(binary=str(binary), env=planted(), sleep=lambda s: None)
    client.generate("s", [{"role": "user", "content": "u"}])
    seen = json.loads(log.read_text().splitlines()[0])
    # the child's environment may gain nothing the table names; of the 23 it keeps the 9
    assert [k for k in planted() if k in seen] == EXPECTED_KEPT
    assert not [k for k in DROPPED if k in seen]


def test_the_table_holds_through_the_discovery_environment() -> None:
    out = discovery_env(planted())
    assert [k for k in planted() if k in out] == EXPECTED_KEPT


def test_the_table_holds_through_the_rebuild_gate_environment(tmp_path: Path) -> None:
    out = gate_env(planted(), repo=tmp_path, worktree=tmp_path, project_rel=".")
    # gate_env adds PYTHONPATH & co. and rewrites PATH/HOME-independent variables; of the 23 the
    # kept names stay, the dropped never appear
    assert [k for k in planted() if k in out] == EXPECTED_KEPT
