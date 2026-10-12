"""The guardrails (plan decision 2), each asserted."""

from __future__ import annotations

import pytest

from lattice.security import BANNER
from lattice.security.schema import BLIND_SPOTS, Report
from lattice.security.target import (
    IN_PROCESS,
    LOOPBACK_HOSTS,
    UnsafeTargetError,
    ensure_safe_target,
    target_from_url,
)


def test_loopback_and_in_process_are_allowed():
    for host in (*LOOPBACK_HOSTS, IN_PROCESS):
        ensure_safe_target(host)  # must not raise


@pytest.mark.parametrize("host", ["example.com", "10.0.0.5", "0.0.0.0", "evil.internal", "8.8.8.8"])
def test_non_loopback_host_is_refused(host):
    with pytest.raises(UnsafeTargetError):
        ensure_safe_target(host)


def test_a_url_target_is_always_refused():
    with pytest.raises(UnsafeTargetError):
        target_from_url("http://example.com/api")
    with pytest.raises(UnsafeTargetError):
        target_from_url("http://127.0.0.1:9000/")  # even loopback: no URL target exists


def test_banner_says_owner_only():
    assert "owner's own systems only" in BANNER.lower()


def test_report_states_what_a_spec_driven_attack_cannot_see():
    rep = Report(
        target="t",
        host=IN_PROCESS,
        scope="system",
        classes=(),
        attacks_planned=0,
        classreports=[],
        findings=[],
        wall_seconds=0.0,
        out_dir="",
    )
    d = rep.to_dict()
    assert d["cannot_see"]
    assert any("CVE" in s for s in BLIND_SPOTS)
    assert any("TLS" in s for s in BLIND_SPOTS)
