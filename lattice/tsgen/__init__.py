"""TypeScript client target (spec docs/superpowers/specs/2026-09-29-lattice-ui-a-client-design.md):
a client whose request validation is proven identical to the server's."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from lattice.api.ir import EndpointPlan, lower_api
from lattice.check import CheckResult, check_source
from lattice.profile import Profile
from lattice.tsgen.emit import api_ts, hooks_ts

RUNTIME = Path(__file__).resolve().parent / "rt" / "lattice-rt.ts"


def generate_ts(plans: Sequence[EndpointPlan]) -> dict[str, str]:
    """{filename: source} for the client of `plans` (raises TsGenRefusal, L893)."""
    return {
        "lattice-rt.ts": RUNTIME.read_text(),
        "api.ts": api_ts(plans),
        "hooks.ts": hooks_ts(plans),
    }


def compile_source_to_ts(
    source: str, profile: Profile
) -> tuple[CheckResult, dict[str, str] | None]:
    """Check -> lower -> generate. (check result, None) when the program is rejected."""
    result = check_source(source, profile)
    if result.program is None:
        return result, None
    return result, generate_ts(lower_api(result.program))
