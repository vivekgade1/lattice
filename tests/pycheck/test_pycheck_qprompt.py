"""Arm Q's prompt: arm P's system prompt plus a short statement of the policies pycheck
checks, carrying the same facts arm L's Lattice guide and profile give about the same
guarantees -- no more (nothing ticket-specific, no fix recipes beyond the one-line idiom
the Lattice guide also shows), no less (every owner, private column and convention the
checks enforce)."""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from bench.edit_slice import prompts
from bench.edit_slice.apps import repo
from bench.edit_slice.qcheck import repo_policy
from bench.edit_slice.tickets import ROUND4_TICKETS
from lattice.catalog import PAGE_LIMIT_MAX
from lattice.importer.extract import extract
from lattice.importer.profile_gen import generate_profile

ROOT = Path(__file__).resolve().parents[2]
APPS = ("template", "taskflow", "justnotes")


def lattice_profile(name: str):
    """The importer's profile of the original app: what arm L is shown."""
    app = repo(name)
    source = generate_profile(extract(ROOT, app.package)).source
    module = types.ModuleType(f"_qprompt_profile_{name}")
    exec(compile(source, "<profile.py>", "exec"), module.__dict__)  # noqa: S102
    return module.profile()


@pytest.mark.parametrize("ticket", ROUND4_TICKETS, ids=lambda t: t.id)
def test_q_system_prompt_is_p_plus_the_policy_statement(ticket):
    q = prompts.q_system_prompt(ticket)
    p = prompts.p_system(ticket.repo)
    assert q.startswith(p)
    assert q[len(p) :].strip() == prompts.q_policy_statement(ticket.repo).strip()


@pytest.mark.parametrize("name", APPS)
def test_the_statement_is_the_apps_not_the_tickets(name):
    """Identical for every ticket of the app, and naming none of their routes."""
    text = prompts.q_policy_statement(name)
    for t in ROUND4_TICKETS:
        if t.repo != name:
            continue
        assert prompts.q_system_prompt(t).endswith(text.rstrip("\n") + "\n")
        assert t.id not in text
        for route in t.routes:
            path = route.split(" ", 1)[1]
            assert path.rstrip("/") not in text or path.count("/") <= 1, path


@pytest.mark.parametrize("name", APPS)
def test_the_statement_covers_the_six_guarantees(name):
    text = prompts.q_policy_statement(name)
    for topic in ("Ownership", "Private columns", "Bounds", "Route order", "Errors", "Datetimes"):
        assert f"- {topic}:" in text, topic
    assert len(text) < 2200  # a short statement, not a second guide


@pytest.mark.parametrize("name", APPS)
def test_fairness_the_statement_states_what_arm_ls_profile_states(name):
    """Every fact in Q's statement is one arm L is given too (the owner columns, the
    private columns, the admin flag, the page-size bound), and every fact pycheck checks
    is in the statement."""
    policy = repo_policy(name)
    text = prompts.q_policy_statement(name)
    api = lattice_profile(name).api
    for table, col in policy.owners.items():
        assert api.tables[table].owner == col  # L's profile names the same owner
        assert f"`{table}.{col}`" in text
    for table, cols in policy.private.items():
        assert set(cols) <= set(api.tables[table].private)  # L's private columns
        for col in cols:
            assert f"`{table}.{col}`" in text
    # Q's private columns are L's, minus the ones the original app itself returns
    returned = {c for t, s in api.tables.items() for c in s.private} - {
        c for cols in policy.private.values() for c in cols
    }
    assert returned <= {"owner_id"}, returned
    if policy.admin is not None:
        assert api.roles_from.column == policy.admin.column  # L: AdminFlag(<column>, ...)
        assert f"`{policy.caller}.{policy.admin.column}`" in text
        assert f"`{policy.admin.dependency}`" in text
    assert policy.page_limit_max == PAGE_LIMIT_MAX  # L831's bound
    assert f"at most {PAGE_LIMIT_MAX}" in text
    answers = [s for s in text.split(". ") if s.startswith("Another user's")]
    for table, answer in policy.not_owned.items():
        assert any(f"`{table}`" in s and f"answers {answer.status}" in s for s in answers), table
