"""The CLI: exit codes (0 no confirmed findings, 1 confirmed, 2 cannot run / refused),
the plan command, the banner, and the --url refusal."""

from __future__ import annotations

import json

from lattice.security.__main__ import main


def test_url_argument_is_refused(capsys):
    assert main(["run", "--project", ".", "--url", "http://example.com"]) == 2
    err = capsys.readouterr().err
    assert "--url is refused" in err


def test_unknown_class_errors(capsys, make_project):
    proj = make_project("ok")
    code = 0
    try:
        main(["plan", "--project", str(proj), "--classes", "nope"])
    except SystemExit as exc:
        code = exc.code
    assert code != 0


def test_missing_project_is_exit_2(capsys):
    assert main(["run", "--project", "/no/such/dir/xyz"]) == 2
    assert "no project directory" in capsys.readouterr().err


def test_plan_lists_attacks_without_traffic(capsys, make_project):
    proj = make_project("ok")
    assert main(["plan", "--project", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "owner's own systems only" in out.lower()
    assert "attacks planned" in out.lower()


def test_run_clean_app_exits_0(capsys, make_project):
    proj = make_project("ok")
    assert main(["run", "--project", str(proj), "--classes", "authz,auth"]) == 0


def test_run_holed_app_exits_1(capsys, make_project):
    proj = make_project("authz")
    code = main(["run", "--project", str(proj), "--classes", "authz", "--json"])
    assert code == 1
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["confirmed_findings"], "the seeded authz hole must be a confirmed finding"
