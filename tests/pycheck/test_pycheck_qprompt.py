"""Arm Q's prompt: arm P's system prompt plus a short statement of the policies pycheck
checks, carrying the same facts arm L's Lattice guide and profile give about the same
guarantees -- no more (nothing ticket-specific, no fix recipes beyond the one-line idiom
the Lattice guide also shows), no less (every owner, private column and convention the
checks enforce)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bench.edit_slice import prompts, qcheck, run
from bench.edit_slice.qcheck import derived_policy, repo_policy, start_ownership
from bench.edit_slice.tickets import ROUND4_TICKETS
from lattice.catalog import PAGE_LIMIT_MAX

ROOT = Path(__file__).resolve().parents[2]
APPS = ("template", "taskflow", "justnotes")


def lattice_profile(name: str):
    """Arm L's start profile of the app: the template's lifted import, a held-out
    repo's committed import (``bench/imports/<repo>/profile.py``), read now."""
    _, source = run.start_lattice(name)
    return run.load_profile_source(source)


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
    for table in policy.shared:
        assert f"`{table}`" in text
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


# ---- the ownership facts come from arm L's start profile (review C2) ----------------------------


@pytest.mark.parametrize("name", APPS)
def test_fairness_q_states_exactly_the_owners_of_arm_ls_start_profile(name):
    """Q's owners, owner_via and shared tables are the ones arm L's start profile
    declares -- not a separate inference -- and its not-owned answers cover only those
    owned tables."""
    policy = repo_policy(name)
    api = lattice_profile(name).api
    owners = {t: s.owner for t, s in api.tables.items() if s.owner}
    owner_via = {t: s.owner_via for t, s in api.tables.items() if s.owner_via}
    assert dict(policy.owners) == owners
    assert dict(policy.owner_via) == owner_via
    unowned = {t for t in api.tables if t not in owners and t not in owner_via}
    assert set(policy.shared) == unowned - {api.users_table} - set(policy.links)
    assert set(policy.not_owned) <= set(owners) | set(owner_via)
    assert set(policy.examples) <= set(owners)
    text = prompts.q_policy_statement(name)
    for table, col in owners.items():
        assert f"`{table}` rows belong to the user in `{table}.{col}`" in text
    for table in policy.shared:
        assert f"`{table}`" in text


def test_the_template_ownership_is_template_profile_and_agrees_with_pycheck():
    """The template's facts come from ``bench/template_profile.py``; pycheck's own
    derivation (the fallback) agrees with it."""
    from bench.template_profile import template_profile

    start = start_ownership("template")
    assert start is not None and start.source == "bench/template_profile.py"
    tables = template_profile().api.tables
    assert dict(start.owners) == {t: s.owner for t, s in tables.items() if s.owner}
    derived = derived_policy("template")
    assert dict(derived.owners) == dict(start.owners)
    assert dict(derived.owner_via) == dict(start.owner_via)
    assert tuple(derived.shared) == tuple(start.shared)


def test_a_held_out_repo_reads_its_committed_import_at_call_time(tmp_path, monkeypatch):
    """No build-time cache: the ownership is read from ``bench/imports/<repo>`` each time
    (the round-5 re-import rewrites it), and a missing import falls back to pycheck's own
    derivation."""
    src = (run.import_dir("taskflow") / "profile.py").read_text()
    fake = tmp_path / "imports"
    (fake / "taskflow").mkdir(parents=True)
    monkeypatch.setattr(run, "IMPORTS", fake)

    assert start_ownership("taskflow") is None  # no import: the fallback
    assert dict(repo_policy("taskflow").owners) == dict(derived_policy("taskflow").owners)

    unowned = re.sub(r"\n\s*owner='owner_id',", "", src)
    at = unowned.index("keys=[('id',)],", unowned.index("TABLE_TASK = "))
    owned = unowned[:at] + "owner='owner_id', " + unowned[at:]
    for text, expected in ((owned, {"Task": "owner_id"}), (unowned, {})):
        (fake / "taskflow" / "profile.py").write_text(text)
        got = start_ownership("taskflow")
        assert got is not None and dict(got.owners) == expected
        assert dict(repo_policy("taskflow").owners) == expected
        statement = prompts.q_policy_statement("taskflow")
        assert ("`Task.owner_id`" in statement) == bool(expected)
    assert "Task" in repo_policy("taskflow").shared
    assert qcheck.check_files("taskflow", {}, strict=True) == []
