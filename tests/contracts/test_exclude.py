"""A provider's route exclusion (``[[project]] exclude`` in the workspace manifest): validation,
normalisation, the filter in the export (before the hash), the findings (an import of an excluded
endpoint is BROKEN pinned or not; a list change is CHANGED), ``exclude_unmatched``, the CLI and the
live replay."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from lattice import workspace as wsmod
from lattice.contracts import check as ck
from lattice.contracts import export as ex
from lattice.contracts.__main__ import main as contracts_main

from .helpers import BASE, IMPORTS, World, copy_data, kinds, make_spec, st
from .test_live import CLEAN_IMPORTS, Live

EX = "the provider excludes it from its contract (exclude in the workspace manifest)"
ME = {"endpoint": "GET /users/me", "status": [200], "auth": "token", "response_fields": ["id"]}
LOGIN = IMPORTS[0]


def excl(*keys: str) -> str:
    return "exclude = " + json.dumps(list(keys)) + "\n"


def cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = contracts_main(list(argv))
    return rc, out.getvalue(), err.getvalue()


def link_of(out: dict) -> dict:
    assert len(out["links"]) == 1, out
    return out["links"][0]


# ---- the export ------------------------------------------------------------------------------


def test_without_an_exclusion_the_export_has_exactly_the_old_keys(tmp_path: Path) -> None:
    w = World(tmp_path)
    plain = ex.compute_export(w.provider, "spec:SPEC")
    assert set(plain) == {"export", "spec_ref", "version"}
    assert set(plain["export"]) == {"basis", "endpoints", "error_body", "format"}
    assert ex.compute_export(w.provider, "spec:SPEC", exclude=[]) == plain
    assert ex.compute_export(w.provider, "spec:SPEC", exclude=()) == plain


def test_the_filter_runs_before_the_hash(tmp_path: Path) -> None:
    w = World(tmp_path)
    base = ex.compute_export(w.provider)
    out = ex.compute_export(w.provider, exclude=["POST /auth/login", "GET /users/me"])
    gone = {"POST /auth/login", "GET /users/me"}
    assert out["export"]["excluded"] == ["GET /users/me", "POST /auth/login"]  # sorted
    assert out["export"]["endpoints"] == {
        k: v for k, v in base["export"]["endpoints"].items() if k not in gone
    }
    assert out["exclude_unmatched"] == []
    assert out["version"]["spec_hash"] == ex.spec_hash(out["export"])
    assert out["version"]["spec_hash"] != base["version"]["spec_hash"]


def test_keys_are_normalised_like_an_import(tmp_path: Path) -> None:
    w = World(tmp_path)
    a = ex.compute_export(w.provider, exclude=[" get  /jobs/7/ "])
    b = ex.compute_export(w.provider, exclude=["GET /jobs/{id}"])
    assert a == b and a["export"]["excluded"] == ["GET /jobs/{}"]
    assert "GET /jobs/{}" not in a["export"]["endpoints"]
    assert "GET /jobs" in a["export"]["endpoints"]
    # the path keeps its case: it matches nothing
    c = ex.compute_export(w.provider, exclude=["GET /Jobs"])
    assert c["exclude_unmatched"] == ["GET /Jobs"]
    assert set(c["export"]["endpoints"]) == set(
        ex.compute_export(w.provider)["export"]["endpoints"]
    )


def test_a_key_the_export_does_not_hold_is_kept_and_reported(tmp_path: Path) -> None:
    d = copy_data(BASE)
    d[1][1].append(st("PATCH", "/jobs/7", "ann", 405))  # a probe with no 2xx step
    w = World(tmp_path, data=d)
    out = ex.compute_export(w.provider, exclude=["PATCH /jobs/{id}", "GET /users/me", "GET /nope"])
    assert out["export"]["excluded"] == ["GET /nope", "GET /users/me", "PATCH /jobs/{}"]
    assert out["exclude_unmatched"] == ["GET /nope", "PATCH /jobs/{}"]
    assert len(out["export"]["endpoints"]) == 4


def test_invalid_lists_are_refused_by_the_export(tmp_path: Path) -> None:
    w = World(tmp_path)
    with pytest.raises(ValueError, match="names GET /users/me twice"):
        ex.build_export(w.provider, "spec:SPEC", ["GET /users/me", "get /users/me/"])
    with pytest.raises(ex.ExportFailed, match="twice"):
        ex.compute_export(w.provider, exclude=["GET /users/me", "GET /users/me"])
    with pytest.raises(ex.ExportFailed, match="is not one of"):
        ex.compute_export(w.provider, exclude=["FETCH /users/me"])


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("GET /health", "exclude must be a list of 'METHOD /path' strings"),
        (42, "exclude must be a list of 'METHOD /path' strings"),
        ([42], "exclude entry #1: endpoint must be a string, not int"),
        (["GET"], "exclude entry #1: endpoint 'GET' must be 'METHOD /path'"),
        (["GET /health", "get /health/"], "exclude names GET /health twice"),
        (["GET /v1/habits?limit=1"], "a query string is not part of an endpoint"),
        (["FETCH /health"], "is not one of GET, POST"),
        (["GET health"], "the path must start with '/'"),
    ],
)
def test_check_exclude_reasons(raw: object, reason: str) -> None:
    keys, reasons = ex.check_exclude(raw)
    assert len(reasons) == 1 and reason in reasons[0], reasons
    assert keys == () or isinstance(raw, list)


def test_check_exclude_keeps_declared_order_and_empty_is_valid() -> None:
    assert ex.check_exclude([]) == ((), [])
    assert ex.check_exclude(["PUT /b", "get /a/"]) == (("PUT /b", "GET /a"), [])


def test_apply_exclude_is_the_identity_without_keys() -> None:
    full = ex.export_spec(make_spec(copy_data(BASE)))
    assert ex.apply_exclude(full, []) == (full, [])
    out, unmatched = ex.apply_exclude(full, ["GET /users/me", "GET /users/me", "GET /z"])
    assert out["excluded"] == ["GET /users/me", "GET /z"] and unmatched == ["GET /z"]
    assert "excluded" not in full and "GET /users/me" in full["endpoints"]  # not mutated


def test_hash_differs_exactly_when_the_diff_is_non_empty() -> None:
    full = ex.export_spec(make_spec(copy_data(BASE)))
    lists = [[], ["GET /users/me"], ["GET /nope"], ["GET /users/me", "GET /nope"]]
    exports = [ex.apply_exclude(full, ex.check_exclude(k)[0])[0] for k in lists]
    for a in exports:
        for b in exports:
            assert (ex.spec_hash(a) != ex.spec_hash(b)) == bool(ck.diff_exports(a, b))


# ---- the manifest ----------------------------------------------------------------------------


def _parse(w: World, raw: object) -> wsmod.Workspace:
    w.write_manifest(extra_project=f"exclude = {json.dumps(raw)}\n")
    return wsmod.parse(w.root / wsmod.MANIFEST)


@pytest.mark.parametrize(
    "raw",
    ["GET /health", 42, [42], ["GET"], ["GET /health", "get /health/"],
     ["GET /v1/habits?limit=1"], ["FETCH /health"], ["GET health"]],
)  # fmt: skip
def test_a_bad_exclude_is_one_error_and_the_project_stays_listed(tmp_path: Path, raw: Any) -> None:
    w = World(tmp_path)
    ws = _parse(w, raw)
    assert [p.code for p in ws.problems] == ["bad_exclude"]
    assert ws.problems[0].severity == wsmod.ERROR
    assert ws.problems[0].message.startswith("project prov: exclude ")
    prov = ws.project("prov")
    assert prov is not None and prov.exclude is None and "exclude" not in prov.to_dict()
    assert ws.names() == ["prov", "cons"] and len(ws.links) == 1
    assert wsmod.main(["validate", "--workspace", str(w.root)]) == 1
    rc, out = w.check()
    assert rc == 2 and out["links"] == []
    assert [p["code"] for p in out["problems"]] == ["bad_exclude"]
    rc, out = w.pin()
    assert rc == 2 and out["links"] == [] and not w.lock.exists()


def test_every_reason_is_in_the_one_message(tmp_path: Path) -> None:
    ws = _parse(World(tmp_path), ["GET", "FETCH /x", "get /y", "GET /y/"])
    assert len(ws.problems) == 1
    msg = ws.problems[0].message
    assert msg.count("; ") == 2  # three reasons
    assert "entry #1" in msg and "entry #2" in msg and "names GET /y twice" in msg


def test_a_valid_exclude_is_normalised_in_declared_order(tmp_path: Path) -> None:
    w = World(tmp_path)
    ws = _parse(w, ["put /b/", "GET /a/{id}"])
    assert ws.problems == []
    prov = ws.project("prov")
    assert prov is not None and prov.exclude == ("PUT /b", "GET /a/{}")
    assert prov.to_dict()["exclude"] == ["PUT /b", "GET /a/{}"]
    assert "exclude" not in ws.project("cons").to_dict()  # type: ignore[union-attr]


def test_an_empty_list_is_valid_and_shown(tmp_path: Path, capsys: pytest.CaptureFixture[str]
                                          ) -> None:  # fmt: skip
    w = World(tmp_path)
    ws = _parse(w, [])
    prov = ws.project("prov")
    assert ws.problems == [] and prov is not None and prov.exclude == ()
    assert prov.to_dict()["exclude"] == []
    assert wsmod.main(["show", "--workspace", str(w.root)]) == 0
    assert "exclude = []" in capsys.readouterr().out


def test_show_prints_the_list_and_a_manifest_without_it_prints_as_before(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    w = World(tmp_path)
    w.write_manifest(extra_project=excl("GET /health", "put /test/clock"))
    assert wsmod.main(["show", "--workspace", str(w.root)]) == 0
    assert 'exclude = ["GET /health", "PUT /test/clock"]' in capsys.readouterr().out
    w.write_manifest()
    ws = wsmod.parse(w.root / wsmod.MANIFEST)
    assert all("exclude" not in p.to_dict() for p in ws.projects)
    assert wsmod.main(["show", "--workspace", str(w.root)]) == 0
    assert "exclude" not in capsys.readouterr().out
    assert wsmod.main(["show", "--json", "--workspace", str(w.root)]) == 0
    assert "exclude" not in capsys.readouterr().out


def test_exclude_on_a_project_that_is_never_a_provider_is_silent(tmp_path: Path) -> None:
    w = World(tmp_path)
    w.write_manifest(extra_project="")
    text = (
        (w.root / wsmod.MANIFEST)
        .read_text()
        .replace('role = "consumer"\n', 'role = "consumer"\nexclude = ["GET /health"]\n')
    )
    (w.root / wsmod.MANIFEST).write_text(text)
    assert w.pin()[0] == 0
    rc, out = w.check()
    assert rc == 0 and out["problems"] == []


# ---- check and pin ---------------------------------------------------------------------------


def test_a_list_change_is_a_visible_change(tmp_path: Path) -> None:
    w = World(tmp_path, imports=[LOGIN])
    w.write_manifest(extra_project=excl("GET /users/me"))
    assert w.pin()[0] == 0
    rc, out = w.check()
    assert rc == 0 and link_of(out)["findings"] == [] and link_of(out)["status"] == "ok"

    w.write_manifest(extra_project=excl("GET /users/me", "GET /jobs"))  # a: add
    rc, out = w.check()
    lk = link_of(out)
    assert rc == 1 and kinds(lk) == [("exclusion_added", "GET /jobs", "")]
    assert lk["findings"][0]["relied"] is False and lk["findings"][0]["class"] == "CHANGED"
    assert lk["spec_hash_changed"] is True and lk["version_changed"] is False

    w.write_manifest()  # b: removed
    rc, out = w.check()
    assert rc == 1
    assert kinds(link_of(out)) == [("exclusion_removed", "GET /users/me", "")]

    w.write_manifest(extra_project=excl("get /users/me/"))  # c: the same list, spelt otherwise
    rc, out = w.check()
    assert rc == 0 and link_of(out)["spec_hash_changed"] is False

    w.write_manifest(extra_project=excl("GET /users/me", "GET /nope"))  # d: names nothing
    rc, out = w.check()
    assert rc == 1 and kinds(link_of(out)) == [("exclusion_added", "GET /nope", "")]
    assert [(p["code"], p["severity"]) for p in out["problems"]] == [
        ("exclude_unmatched", "warning")
    ]
    assert out["problems"][0]["message"] == (
        "prov: exclude names GET /nope, which its export does not hold (no 2xx step in its spec)"
    )


def test_an_exclusion_absorbs_the_endpoint_diff_both_ways(tmp_path: Path) -> None:
    w = World(tmp_path, imports=[LOGIN])
    assert w.pin()[0] == 0
    w.write_manifest(extra_project=excl("GET /users/me"))
    rc, out = w.check()
    assert rc == 1 and kinds(link_of(out)) == [("exclusion_added", "GET /users/me", "")]
    assert w.pin()[0] == 0  # accept it
    w.write_manifest()
    rc, out = w.check()
    assert rc == 1 and kinds(link_of(out)) == [("exclusion_removed", "GET /users/me", "")]


def test_a_removed_endpoint_that_was_not_excluded_is_still_endpoint_removed(tmp_path: Path) -> None:
    w = World(tmp_path, imports=[LOGIN])
    w.write_manifest(extra_project=excl("GET /users/me"))
    assert w.pin()[0] == 0
    d = copy_data(BASE)
    d[1][1].pop(6)  # GET /users/me goes from the spec; it stays excluded
    d[1][1].pop(2)  # GET /jobs/{} goes too, and was not excluded
    w.edit(d)
    rc, out = w.check()
    assert rc == 1 and kinds(link_of(out)) == [("endpoint_removed", "GET /jobs/{}", "")]


def test_unmatched_is_once_per_provider_and_key_and_never_fails(tmp_path: Path) -> None:
    w = World(tmp_path, imports=[LOGIN])
    (tmp_path / "cons2").mkdir()
    (tmp_path / "cons2" / ck.IMPORTS_FILE).write_text((w.consumer / ck.IMPORTS_FILE).read_text())
    w.write_manifest(
        extra_link='\n[[project]]\nname = "cons2"\npath = "cons2"\n\n'
        '[[link]]\nfrom = "cons2"\nto = "prov"\nkind = "uses"\ncontract = "static"\n',
        extra_project=excl("GET /zzz", "GET /aaa", "GET /users/me"),
    )
    assert w.run("pin", "cons")[0] == 0 and w.run("pin", "cons2")[0] == 0
    rc, out = w.run("check")
    assert rc == 0 and out["checked"] == 2
    got = [p["message"] for p in out["problems"] if p["code"] == "exclude_unmatched"]
    assert got == [
        "prov: exclude names GET /aaa, which its export does not hold (no 2xx step in its spec)",
        "prov: exclude names GET /zzz, which its export does not hold (no 2xx step in its spec)",
    ]
    rc, out = w.run("pin", "cons")
    assert rc == 0 and [p["code"] for p in out["problems"]] == ["exclude_unmatched"] * 2


def test_the_workspace_validate_does_not_load_a_spec(tmp_path: Path) -> None:
    w = World(tmp_path, imports=[LOGIN])
    w.write_manifest(extra_project=excl("GET /nope"))
    assert wsmod.parse(w.root / wsmod.MANIFEST).problems == []
    assert wsmod.main(["validate", "--workspace", str(w.root)]) == 0


# ---- an import of an excluded endpoint --------------------------------------------------------


def test_an_import_of_an_endpoint_excluded_after_the_pin_is_broken(tmp_path: Path) -> None:
    w = World(tmp_path, imports=[LOGIN, ME])
    assert w.pin()[0] == 0
    before = w.lock.read_bytes()
    w.write_manifest(extra_project=excl("GET /users/me", "GET /jobs"))
    rc, out = w.check()
    lk = link_of(out)
    assert rc == 1 and lk["status"] == "broken"
    broken = [f for f in lk["findings"] if f["class"] == "BROKEN"]
    assert broken == [{"class": "BROKEN", "provider": "prov", "endpoint": "GET /users/me",
                       "kind": "endpoint_missing", "item": "", "detail": EX}]  # fmt: skip
    # the clock's exclusion is folded; the other one is a CHANGED nobody relies on
    assert [
        (f["class"], f["kind"], f["endpoint"], f["relied"])
        for f in lk["findings"]
        if f["class"] == "CHANGED"
    ] == [("CHANGED", "exclusion_added", "GET /jobs", False)]
    assert lk["spec_hash_changed"] is True

    rc, out = w.pin()  # refused
    assert rc == 1 and link_of(out)["status"] == "refused" and w.lock.read_bytes() == before
    assert link_of(out)["broken"][0]["detail"] == EX

    rc, out = w.pin("--force")  # written, but it does not launder the import
    assert rc == 0 and link_of(out)["forced"] is True and w.lock.read_bytes() != before
    rc, out = w.check()
    lk = link_of(out)
    assert rc == 1 and lk["status"] == "broken"
    assert kinds(lk) == [("endpoint_missing", "GET /users/me", "")]  # no CHANGED left
    assert lk["findings"][0]["detail"] == EX


def test_an_import_of_an_endpoint_excluded_before_any_pin_is_broken(tmp_path: Path) -> None:
    w = World(tmp_path, imports=[LOGIN, ME])
    w.write_manifest(extra_project=excl("GET /users/me"))
    rc, out = w.pin()
    lk = link_of(out)
    assert rc == 1 and lk["status"] == "refused" and not w.lock.exists()
    assert lk["replaced"] is False
    assert [(f["kind"], f["endpoint"], f["item"], f["detail"]) for f in lk["broken"]] == [
        ("endpoint_missing", "GET /users/me", "", EX)
    ]
    rc, out = w.pin("--force")
    assert rc == 0 and w.lock.exists()
    rc, out = w.check()
    lk = link_of(out)
    assert rc == 1 and kinds(lk) == [("endpoint_missing", "GET /users/me", "")]
    assert lk["findings"][0]["detail"] == EX and lk["findings"][0]["class"] == "BROKEN"


def test_evaluate_reports_an_excluded_import_with_no_pinned_export() -> None:
    imp = ck.Import("prov", "GET /a", (200,), "token", None, ("x",), ("y",))
    cur = {"endpoints": {}, "excluded": ["GET /a"]}
    broken, gaps = ck.evaluate([imp], cur, None)
    assert [(f["kind"], f["item"], f["detail"]) for f in broken] == [("endpoint_missing", "", EX)]
    assert gaps == []
    assert ck.evaluate([imp], cur, {"endpoints": {}}) == (broken, [])
    # not excluded and never pinned: only a gap
    broken, gaps = ck.evaluate([imp], {"endpoints": {}}, None)
    assert broken == [] and [g["kind"] for g in gaps] == ["endpoint_unobserved"]


def test_an_import_of_an_endpoint_that_is_not_excluded_is_as_before(tmp_path: Path) -> None:
    w = World(tmp_path, imports=[LOGIN, ME])
    w.write_manifest(extra_project=excl("GET /jobs"))
    rc, out = w.pin()
    assert rc == 0
    rc, out = w.check()
    assert rc == 0 and link_of(out)["findings"] == []


# ---- the CLI ---------------------------------------------------------------------------------


def test_export_with_flags_and_with_a_name_agree(tmp_path: Path) -> None:
    w = World(tmp_path)
    w.write_manifest(extra_project=excl("get /users/me/", "GET /nope"))
    rc, by_name, _ = cli("export", "prov", "--workspace", str(w.root))
    assert rc == 0
    rc, by_dir, _ = cli("export", "--project", str(w.provider), "--spec", "spec:SPEC",
                        "--exclude", "get /users/me/", "--exclude", "GET /nope")  # fmt: skip
    assert rc == 0 and by_name == by_dir
    got = json.loads(by_name)
    assert got["exclude_unmatched"] == ["GET /nope"] and got["export"]["excluded"] == [
        "GET /nope",
        "GET /users/me",
    ]
    assert got == ex.compute_export(w.provider, exclude=["GET /users/me", "GET /nope"])


def test_export_without_a_list_prints_no_new_key(tmp_path: Path) -> None:
    w = World(tmp_path)
    rc, out, _ = cli("export", "prov", "--workspace", str(w.root))
    assert rc == 0 and set(json.loads(out)) == {"export", "spec_ref", "version"}
    assert "exclude" not in out


def test_export_refuses_a_flag_with_a_name_and_bad_flags(tmp_path: Path) -> None:
    w = World(tmp_path)
    rc, out, err = cli("export", "prov", "--exclude", "GET /health", "--workspace", str(w.root))
    assert rc == 2 and out == "" and "comes from its manifest" in err
    rc, out, err = cli("export", "--project", str(w.provider), "--exclude", "GET")
    assert rc == 2 and out == "" and "must be 'METHOD /path'" in err
    rc, out, err = cli("export", "--project", str(w.provider), "--exclude", "GET /a",
                       "--exclude", "get /a/")  # fmt: skip
    assert rc == 2 and "names GET /a twice" in err


def test_pin_text_says_an_excluded_import_stays_broken(tmp_path: Path) -> None:
    w = World(tmp_path, imports=[LOGIN, ME])
    w.write_manifest(extra_project=excl("GET /users/me"))
    rc, out, _ = cli("pin", "cons", "--workspace", str(w.root))
    assert rc == 1 and "REFUSED" in out and "stays BROKEN even when forced" in out
    rc, out, _ = cli("pin", "cons", "--force", "--workspace", str(w.root))
    assert rc == 0 and "stays BROKEN" in out


# ---- live ------------------------------------------------------------------------------------


def test_live_does_not_replay_an_excluded_import(tmp_path: Path) -> None:
    w = Live(tmp_path)
    full = w.run()
    assert full["replayed"] == 6 and full["not_replayed"] == []
    narrowed, unmatched = ex.apply_exclude(w.export(), ["GET /admin"])
    assert unmatched == [] and "GET /admin" not in narrowed["endpoints"]
    (tmp_path / "prov" / "requests.log").unlink(missing_ok=True)
    res = w.run(export=narrowed)
    assert res["replayed"] == 5
    assert res["not_replayed"] == [
        {"endpoint": "GET /admin", "reason": "excluded by the provider (exclude)"}
    ]
    assert res["findings"] == []
    assert [r["endpoint"] for r in res["replays"]] == [
        r["endpoint"] for r in full["replays"] if r["endpoint"] != "GET /admin"
    ]
    assert not any("/admin" in line for line in w.log)  # nothing was sent for it


def test_live_without_an_exclusion_is_unchanged(tmp_path: Path) -> None:
    w = Live(tmp_path, imports=CLEAN_IMPORTS[:2])
    assert "excluded" not in w.export()
    res = w.run()
    assert res["replayed"] == 2 and res["not_replayed"] == []
