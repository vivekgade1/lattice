"""The importer's side of Phase 2 W3b (plan 2026-10-05, "Measurement decisions after the
dev-app gate"):

* **tables outside the API**: a table no route reads or writes, by the front end's
  evidence (route tables, owner evidence, returned rows) -- never the probes -- is
  ``outside API``: listed, left out of the table denominator (report summary and
  ``bench/phase2_exit.py``'s recomputation);
* **a link between users** with a composite key (microblog's ``followers``): the profile
  owns it by the side its routes set from the caller (``follower_id``), and a route
  comparing the other side states ``owner(followed_id)``;
* **owner or public** (JustNotes' ``isPublic``): a route checking ``row.owner != caller
  and not row.<Bool column>`` states ``owner_or_public(<column>)``.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

from bench.phase2_exit import agreement, recompute
from lattice.importer import rules
from lattice.importer.emit_spec import public_scopes, route_contracts
from lattice.importer.profile_gen import generate_profile
from lattice.spec import Contract, Spec
from lattice.spec.probes import OUTSIDE_API, ItemVerdict, RuleItem, RuleResult

REPO_ROOT = Path(__file__).resolve().parents[2]


def _profile(model):
    result = generate_profile(model)
    module = ModuleType("generated_profile")
    exec(compile(result.source, "<generated_profile>", "exec"), module.__dict__)
    return result, module.profile()


@pytest.fixture(scope="module")
def microblog():
    pytest.importorskip("flask")
    from lattice.importer.frontends.flask_app import FRONT_END

    return FRONT_END.extract(REPO_ROOT, "bench.import_repos.microblog.api")


@pytest.fixture(scope="module")
def flasky():
    pytest.importorskip("flask")
    from lattice.importer.frontends.flask_app import FRONT_END

    return FRONT_END.extract(REPO_ROOT, "bench.import_repos.flasky")


@pytest.fixture(scope="module")
def justnotes():
    from lattice.importer.extract import extract

    return extract(REPO_ROOT, "bench.import_repos.justnotes.app")


# ---- tables outside the API ---------------------------------------------------------------------


def test_tables_no_route_reads_or_writes_are_outside_the_api(flasky, microblog):
    outside = rules.outside_api(flasky)
    assert set(outside) == {"Role", "Follow"}
    assert all("no route" in why for why in outside.values())
    # every table of microblog is read or written by some route; the users table never
    # is outside (logging in reads it)
    assert rules.outside_api(microblog) == {}


def test_the_fastapi_apps_have_no_table_outside_the_api(justnotes):
    assert rules.outside_api(justnotes) == {}


def _items(*records: tuple[str, str, str, str]) -> dict[str, ItemVerdict]:
    out = {}
    for item_id, kind, subject, verdict in records:
        # a verified record names a decisive probe (the self-check refuses one without)
        decisive = ["p"] if verdict == "verified" else []
        out[item_id] = ItemVerdict(RuleItem(item_id, kind, subject, ""), verdict, "",
                                   decisive=decisive)  # fmt: skip
    return out


def test_an_outside_table_is_listed_and_left_out_of_the_denominator():
    from lattice.profile import ApiProfile, Profile, TokenColumn
    from lattice.typesys import Scalar, Schema

    users = Schema.from_pairs([("id", Scalar.INT), ("token", Scalar.STR)], keys=[("id",)])
    roles = Schema.from_pairs([("id", Scalar.INT), ("name", Scalar.STR)], keys=[("id",)])
    api = ApiProfile(tables={"User": users, "Role": roles}, table_names={"User": "users",
                     "Role": "roles"}, roles=frozenset({"Public", "Customer"}),
                     users_table="User", auth=TokenColumn("token"))  # fmt: skip
    spec = Spec(Profile(schemas={}, api=api), contracts=(Contract("GET", "/me", "Authenticated",
                table="User", kind="read"),))  # fmt: skip
    result = RuleResult(items=_items(
        ("T1 User", "T1", "User", "verified"),
        ("T1 Role", "T1", "Role", "unclear"),
        ("T5 Role (id)", "T5", "Role", "unexercised"),
        ("R1 GET /me", "R1", "GET /me", "verified"),
    ))  # fmt: skip
    rules.mark_outside(spec, result, {"Role": "no route reads or writes it"})
    assert result.items["T1 Role"].verdict == "unexercised"
    assert result.items["T1 Role"].reason.startswith(OUTSIDE_API)
    assert result.items["T5 Role (id)"].reason.startswith(OUTSIDE_API)
    assert result.tables["Role"]["verdict"] == OUTSIDE_API
    assert result.summary["tables"] == {"verified": 1, "total": 1, "outside_api": 1}
    # the exit script's recomputation reads the same from the item records alone
    data = result.to_dict()
    assert agreement(data) == []
    assert recompute(data["items"])["tables"]["Role"]["verdict"] == OUTSIDE_API


def test_a_table_the_probes_reached_is_never_set_outside():
    """The evidence may miss a route (the veto only ever keeps a table in): a table with
    a verified or refuted item stays in the denominator, with a note."""
    from lattice.profile import ApiProfile, Profile, TokenColumn
    from lattice.typesys import Scalar, Schema

    users = Schema.from_pairs([("id", Scalar.INT), ("token", Scalar.STR)], keys=[("id",)])
    api = ApiProfile(tables={"User": users}, table_names={"User": "users"},
                     roles=frozenset({"Public", "Customer"}), users_table="User",
                     auth=TokenColumn("token"))  # fmt: skip
    spec = Spec(Profile(schemas={}, api=api))
    result = RuleResult(items=_items(("T1 User", "T1", "User", "refuted")))
    rules.mark_outside(spec, result, {"User": "no route reads or writes it"})
    assert result.items["T1 User"].verdict == "refuted"
    assert result.tables["User"]["verdict"] == "unverified"
    assert any("User" in n and "probes reached" in n for n in result.notes)


def test_recompute_leaves_outside_tables_out_of_the_count():
    def rec(item_id, kind, subject, verdict, reason=""):
        return {"id": item_id, "kind": kind, "subject": subject, "verdict": verdict,
                "reason": reason}  # fmt: skip

    items = {r["id"]: r for r in [
        rec("T1 Note", "T1", "Note", "verified"),
        rec("T1 Role", "T1", "Role", "unexercised", f"{OUTSIDE_API}: no route reads it"),
        rec("T5 Role (id)", "T5", "Role", "unexercised", f"{OUTSIDE_API}: no route reads it"),
        rec("T1 Tag", "T1", "Tag", "unexercised", "no probe reached it"),
    ]}  # fmt: skip
    got = recompute(items)["tables"]
    assert {k: v["verdict"] for k, v in got.items()} == {
        "Note": "verified", "Role": OUTSIDE_API, "Tag": "unverified"}  # fmt: skip
    from bench.phase2_exit import _count

    assert _count(got) == {"verified": 1, "total": 2, "pct": 50.0, "outside_api": 1}


# ---- a link between users -----------------------------------------------------------------------


def test_a_users_link_is_owned_by_the_side_its_routes_set(microblog):
    result, profile = _profile(microblog)
    assert result.ownership["followers"] == "owner"
    assert "follower_id" in result.ownership_reasons["followers"]
    followers = profile.api.tables["followers"]
    assert followers.owner == "follower_id"
    assert followers.keys == (("follower_id", "followed_id"),)
    assert not any(u.topic == "owner" and u.subject == "followers" for u in result.unclear)


def test_link_routes_state_their_side(microblog):
    _, profile = _profile(microblog)
    c = {x.endpoint: x for x in route_contracts(microblog, profile.api)}
    assert c["GET /api/me/following"].owner_scope == "owner"
    assert c["GET /api/me/followers"].owner_scope == "owner(followed_id)"
    follow = c["POST /api/me/following/{id}"]
    assert (follow.table, follow.kind, follow.owner_scope) == ("followers", "create", "owner")
    assert [(p.name, p.ref) for p in follow.params] == [("id", "User")]
    assert [(e.status, e.when) for e in follow.errors] == [(404, "not_found"), (409, "duplicate")]
    unfollow = c["DELETE /api/me/following/{id}"]
    assert (unfollow.kind, unfollow.owner_scope) == ("delete", "owner")
    assert [(p.name, p.ref) for p in unfollow.params] == [("id", "User")]


def test_a_two_owner_table_that_is_no_link_stays_unclear(flasky):
    """flasky's ``follows`` has two users foreign keys as its key, but no route sets
    either from the caller: no evidence, so its owner stays unclear."""
    result, _ = _profile(flasky)
    assert result.ownership["Follow"] == "unclear"


# ---- owner or public ----------------------------------------------------------------------------


def test_owner_or_public_reads_come_from_the_routes_checks(justnotes):
    found = public_scopes(REPO_ROOT, "bench.import_repos.justnotes.app", justnotes)
    assert found["GET /notes/{id}"] == ("Note", "isPublic")
    assert found["GET /notes/{note_id}/tags"] == ("Note", "isPublic")
    # through the helper it calls (_check_note_access)
    assert found["GET /notes/{id}/summarize"] == ("Note", "isPublic")
    # owner-only routes compare the owner alone
    assert "PUT /notes/{id}" not in found and "DELETE /notes/{id}" not in found
    assert "POST /notes/{note_id}/tags/{tag_id}" not in found


def test_owner_or_public_contracts(justnotes):
    from bench.import_repos.justnotes import make_app
    from lattice.importer.lift import lift_repo

    result = lift_repo(REPO_ROOT, "bench.import_repos.justnotes.app", make_app, only=())
    public = public_scopes(REPO_ROOT, "bench.import_repos.justnotes.app", result.model)
    api = result.lattice_profile.api
    c = {x.endpoint: x for x in route_contracts(result.model, api, public=public)}
    assert c["GET /notes/{id}"].owner_scope == "owner_or_public(isPublic)"
    assert c["GET /notes/{note_id}/tags"].owner_scope == "owner_or_public(isPublic)"
    assert c["PUT /notes/{id}"].owner_scope == "owner"
    from lattice.spec import validate

    spec = Spec(result.lattice_profile, contracts=tuple(c.values()))
    assert [p.render() for p in validate(spec)] == []
