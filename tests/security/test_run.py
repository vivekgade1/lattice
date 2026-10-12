"""The runner and judge: the clean app is quiet, each seeded hole is confirmed, and a claim
that does not reproduce on a fresh seed is NOT confirmed."""

from __future__ import annotations

import pytest

from lattice.security import run as runmod
from lattice.security.plan import generate


def _run(make_target, variant, attacks, max_workers=1):
    # workers=1 for deterministic detection: a shared-database phase 1 run concurrently can
    # false-negative a write attack (one attack cancels/exhausts a resource another needs).
    # Concurrency itself is exercised by test_judge_runs_on_a_fresh_seed below.
    return runmod.run(make_target(variant), attacks, scope="system", max_workers=max_workers)


def test_clean_app_has_no_confirmed_findings(make_target, attack_spec):
    rep = _run(make_target, "ok", generate(attack_spec))
    assert rep.confirmed == [], [f.attack_id for f in rep.confirmed]


@pytest.mark.parametrize("variant", ["authz", "auth", "bounds", "mass", "state", "leak"])
def test_each_seeded_hole_is_confirmed(make_target, attack_spec, variant):
    rep = _run(make_target, variant, generate(attack_spec))
    classes = {f.cls for f in rep.confirmed}
    assert variant in classes, f"{variant} not in {classes}"


def test_judge_rejects_a_claim_that_does_not_reproduce(make_target, attack_spec):
    """A mass attack injecting an id the app never stores fires in no isolated replay, so it
    is never confirmed -- the judge, not the attacker, decides."""
    mass_id = next(a for a in generate(attack_spec, ["mass"]) if a.id.endswith("-id"))
    # against the mass variant (which only accepts a client price, never an id)
    rep = runmod.run(make_target("mass"), [mass_id], scope="system")
    assert rep.confirmed == []
    cr = rep.classreports[0]
    assert cr.findings == 0


def test_judge_runs_on_a_fresh_seed_each_time(make_target, attack_spec):
    """A cross-talk false positive -- an oracle that only fires because another attack wrote
    to the shared phase-1 database -- is removed by the judge's isolated replay. The mass
    `id` attack is exactly this: never a finding, though it runs alongside the price attack."""
    mass = generate(attack_spec, ["mass"])
    rep = runmod.run(make_target("mass"), mass, scope="system")
    confirmed_ids = {f.attack_id for f in rep.confirmed}
    assert any("price" in i for i in confirmed_ids)  # the real hole
    assert not any(i.endswith("-id") for i in confirmed_ids)  # the spurious one is filtered


def test_distinct_findings_dedupe(make_target, attack_spec):
    rep = _run(make_target, "auth", generate(attack_spec))
    assert runmod.distinct_findings(rep) <= len(rep.confirmed)


def _fake_targets(fires_on):
    """A factory of fake targets: the first made is the shared phase-1 target ("shared"), each
    later one a fresh replay target ("fresh"); `fires_on` is the set of names whose oracle
    fires. Returns (factory, made_names)."""
    import contextlib
    import types

    made: list[str] = []

    @contextlib.contextmanager
    def factory():
        name = "shared" if not made else "fresh"
        made.append(name)
        yield types.SimpleNamespace(host="127.0.0.1", base_url="http://127.0.0.1:1", name=name)

    factory.fires_on = fires_on  # type: ignore[attr-defined]
    return factory, made


def test_the_judge_replays_on_a_fresh_target_and_confirms_only_what_reproduces(
    monkeypatch, attack_spec
):
    """A claim that fires on the shared phase-1 database but not on a fresh replay is NOT
    confirmed; one that fires on both is. This pins the judge itself (a judge that confirms
    without replaying fails the first half)."""
    attack = generate(attack_spec, ["authz"])[0]

    def attempt(target, a):
        return (target.name in attempt.fires_on, "fired", [], False)

    monkeypatch.setattr(runmod, "_attempt", attempt)

    attempt.fires_on = {"shared"}  # type: ignore[attr-defined]
    factory, made = _fake_targets({"shared"})
    rep = runmod.run(factory, [attack], scope="system")
    assert made == ["shared", "fresh"]  # the claim was replayed on a brand-new target
    assert rep.confirmed == []  # it did not reproduce there, so it is not a finding
    assert rep.classreports[0].findings == 0

    attempt.fires_on = {"shared", "fresh"}  # type: ignore[attr-defined]
    factory, made = _fake_targets({"shared", "fresh"})
    rep = runmod.run(factory, [attack], scope="system")
    assert made == ["shared", "fresh"]
    assert [f.attack_id for f in rep.confirmed] == [attack.id]
