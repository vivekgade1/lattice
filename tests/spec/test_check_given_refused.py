"""A scenario whose given rows the app's own schema refuses is a finding, not a crash."""

from __future__ import annotations

import dataclasses

from lattice.spec import check
from tests.spec import racy


def test_given_rows_the_schema_refuses_become_a_finding():
    base = racy.RUNS_SPEC
    sc = base.scenarios[0]
    bad = dataclasses.replace(
        sc, name="duplicate_given_ids",
        given={"runs": [{"id": 7, "config_id": 1, "status": "queued"},
                        {"id": 7, "config_id": 1, "status": "queued"}]},
    )
    spec = dataclasses.replace(base, scenarios=(bad,))
    report = check(spec, racy.runs_app(), racy.build_runs_db, layers="D", login=racy.login)
    assert report.verdict == "fail"
    assert any("refused the scenario's given rows" in str(f.details) for f in report.findings)
