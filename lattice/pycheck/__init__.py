"""pycheck: Lattice's guarantee checks on ordinary FastAPI + SQLModel code (arm Q of the
round-5 decision experiment, spec 2026-09-28 §3).

Usage: derive the app's policy once from the original code, then check each edited
copy against it::

    policy = derive_policy(original_app_dir, "bench.template_app.app")
    findings = run_pycheck(edited_app_dir, policy)   # [Finding(code, file, line, message, hint)]

The checks and what each cannot see are in :mod:`lattice.pycheck.checks`.
"""

from lattice.pycheck.checks import all_findings, run_pycheck
from lattice.pycheck.derive import derive_policy
from lattice.pycheck.describe import describe_policy
from lattice.pycheck.findings import CODES, Finding, render_findings
from lattice.pycheck.overlay import materialize
from lattice.pycheck.policy import AdminConcept, ErrorAnswer, Policy

__all__ = [
    "CODES",
    "AdminConcept",
    "ErrorAnswer",
    "Finding",
    "Policy",
    "all_findings",
    "derive_policy",
    "describe_policy",
    "materialize",
    "render_findings",
    "run_pycheck",
]
