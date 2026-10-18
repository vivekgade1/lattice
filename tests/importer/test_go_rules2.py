"""The rules-first import for Go, round 2 (Spike J, node J2): E1, E3, E4, E5 of
``lattice.importer.go_rules`` on the committed Gin fixtures and the profile patch. Needs
``go`` for the extractor; nothing here builds or runs an app."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from lattice.gocheck.derive import policy_extract
from lattice.importer import go_rules
from lattice.importer.frontends.go_gin import GoGinFrontEnd
from lattice.importer.profile_gen import ProfileResult, generate_profile

pytestmark = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")

REPO = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO / "tests" / "spec"
FIXTURES = REPO / "tests" / "gocheck" / "fixtures"


def front() -> tuple[GoGinFrontEnd, object]:
    fe = GoGinFrontEnd()
    return fe, fe.extract(SPEC_DIR, "goginapp")


def test_the_inferences_of_goginapp_are_the_seven_the_rulings_name():
    fe = go_rules.InferringFrontEnd(GoGinFrontEnd())
    fe.extract(SPEC_DIR, "goginapp")
    rules = sorted(i["rule"] for i in fe.inferences)
    assert rules == ["bind_status", "json_dash", "owner_uses", "path_parse", "path_parse",
                     "path_parse", "status_facts"]  # fmt: skip
    assert fe.validation_status == 422
    assert fe.private == {"User": ["password", "token"]}


def test_e1_retypes_the_parsed_path_parameters_and_records_the_parse_site():
    pol = policy_extract(SPEC_DIR / "goginapp")
    fe, model = front()
    new, inf = go_rules.infer_path_parse(pol, model)
    path = {(r.method, r.path, p.name): p.type for r in new.routes for p in r.params
            if p.source == "path"}  # fmt: skip
    assert set(path.values()) == {"Int"}
    assert [i["param"] for i in inf] == ["cid", "rid", "rid"]
    assert all(i["evidence"][0].startswith("code:api/api.go:") for i in inf)


def test_e3_one_status_is_stated_and_a_disagreement_is_not():
    assert go_rules.infer_bind_status(policy_extract(SPEC_DIR / "goginapp"))[0] == 422
    assert go_rules.infer_bind_status(policy_extract(FIXTURES / "goparity2"))[0] == 422
    status, inf = go_rules.infer_bind_status(policy_extract(FIXTURES / "goparity2mixed"))
    assert status is None
    assert inf == [{"rule": "bind_status", "unclear": True, "statuses": [400, 422]}]
    assert go_rules.infer_bind_status({"routes": []}) == (None, [])


def test_e4_choices_are_the_default_then_the_written_states():
    fe, model = front()
    wf = fe.extract_json(SPEC_DIR, "goginapp", workflow=True)
    new, inf = go_rules.infer_choices(fe.last_extract, wf, model)
    run = next(t for t in new.tables if t.name == "Run")
    assert {c.name: c.choices for c in run.columns if c.choices} == {
        "status": ("queued", "cancelled")
    }
    assert [(i["table"], i["column"], i["choices"]) for i in inf] == [
        ("Run", "status", ["queued", "cancelled"])
    ]


def test_e5_private_columns_are_the_json_dash_never_a_name():
    private, inf = go_rules.infer_private(policy_extract(SPEC_DIR / "goginapp"))
    assert private == {"User": ["password", "token"]}
    assert inf == [{"rule": "json_dash", "table": "User", "columns": ["password", "token"]}]
    assert go_rules.infer_private(policy_extract(FIXTURES / "goextract"))[0] == {
        "User": ["password_hash"]
    }


def test_the_profile_patch_states_the_private_columns_and_the_bind_status():
    fe, model = front()
    gen = generate_profile(model)
    result = SimpleNamespace(profile=gen, lattice_profile=None, context=None)
    go_rules.patch_profile(result, {"User": ["password", "token"]}, 422)
    api = result.lattice_profile.api
    assert api.validation_status == 422
    assert sorted(api.tables["User"].private) == ["password", "token"]
    assert not api.tables["Run"].private
    # nothing to say, nothing to change
    again = SimpleNamespace(profile=ProfileResult(gen.source), lattice_profile="kept", context=None)
    go_rules.patch_profile(again, {}, None)
    assert again.lattice_profile == "kept"
