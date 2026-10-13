"""``lattice contracts pin`` and ``check`` (lattice/contracts/check.py): BROKEN, GAP and CHANGED
semantics, folding, the lock, the import file, exit codes and the live interface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lattice.contracts import check as ck
from lattice.contracts import live

from .helpers import BASE, IMPORTS, World, copy_data, kinds, st


def data(fn=None) -> list:
    d = copy_data(BASE)
    if fn:
        fn(d)
    return d


def steps_of(d: list, scenario: str) -> list:
    return dict(d)[scenario]


def pinned_world(tmp_path: Path, imports: list[dict] | None = None, **kw) -> World:
    w = World(tmp_path, imports=imports, **kw)
    rc, out = w.pin()
    assert rc == 0, out
    return w


def link_of(out: dict) -> dict:
    return out["links"][0]


def test_clean_pin_and_check(tmp_path: Path) -> None:
    w = pinned_world(tmp_path)
    rc, out = w.check()
    link = link_of(out)
    assert rc == 0 and link["status"] == "ok" and out["checked"] == 1
    assert link["findings"] == [] and link["gaps"] == []
    assert link["imports"] == 4 and link["version_changed"] is False
    assert link["live"] is None and link["error"] is None


def test_the_lock_has_no_timestamps_or_paths_and_is_reproducible(tmp_path: Path) -> None:
    w = pinned_world(tmp_path)
    first = w.lock.read_bytes()
    lock = json.loads(first)
    assert set(lock) == {"consumer", "format", "providers"} and lock["consumer"] == "cons"
    entry = lock["providers"]["prov"]
    assert set(entry) == {"export", "spec_ref", "version"}
    assert str(tmp_path) not in first.decode()
    assert entry["version"]["spec_hash"] == ck.spec_hash(entry["export"])
    assert w.pin()[0] == 0
    assert w.lock.read_bytes() == first


# ---- BROKEN ----------------------------------------------------------------------------------

LOGIN = "POST /auth/login"
JOBS = "GET /jobs"
JOB = "GET /jobs/{}"
RUNS = "POST /jobs/{}/runs"


def _drop_runs(d: list) -> None:
    d[1] = (d[1][0], [s for s in d[1][1] if "/runs" not in s["path"]])


def _no_404(d: list) -> None:
    d[1] = (d[1][0], [s for s in d[1][1] if s["expect_status"] != 404])


def _login_without_password(d: list) -> None:
    d[0][1][0]["json"] = {"email": "a"}


def _no_limit(d: list) -> None:
    del steps_of(d, "jobs")[1]["params"]


def _no_access_token(d: list) -> None:
    d[0][1][0]["expect_body"] = {"token_type": "bearer"}


def _login_needs_a_token(d: list) -> None:
    d[0][1][0]["user"] = "ann"


def _ann_refused(d: list) -> None:  # eve keeps the endpoint alive; ann is refused
    steps_of(d, "jobs")[1]["expect_status"] = 403
    steps_of(d, "jobs").append(st("GET", "/jobs", "eve", 200, expect_body=[{"id": 1, "name": "n"}]))


def _nobody_granted(d: list) -> None:  # the 2xx are anonymous now: no handle is granted
    for s in steps_of(d, "jobs"):
        if s["path"].startswith("/jobs/") and s["user"] == "ann" and s["expect_status"] == 200:
            s["user"] = None


BROKEN_CASES = {
    "endpoint_missing": (_drop_runs, ("endpoint_missing", RUNS, "")),
    "status_not_observed": (_no_404, ("status_not_observed", JOB, "404")),
    "request_field_missing": (
        _login_without_password,
        ("request_field_missing", LOGIN, "password"),
    ),
    "request_field_missing_query": (_no_limit, ("request_field_missing", JOBS, "limit")),
    "response_field_missing": (_no_access_token, ("response_field_missing", LOGIN, "access_token")),
    "auth_changed_public": (_login_needs_a_token, ("auth_changed", LOGIN, "public")),
    "auth_changed_as_user": (_ann_refused, ("auth_changed", JOBS, "as_user:ann")),
    "auth_changed_token": (_nobody_granted, ("auth_changed", JOB, "token")),
}


@pytest.mark.parametrize("name", list(BROKEN_CASES))
def test_each_broken_kind(tmp_path: Path, name: str) -> None:
    fn, want = BROKEN_CASES[name]
    imports = list(IMPORTS)
    if name == "request_field_missing_query":
        imports.append({"endpoint": "GET /jobs", "status": [200], "auth": "token",
                        "request_fields": ["limit"]})  # fmt: skip
    w = pinned_world(tmp_path, imports=imports)
    w.edit(data(fn))
    rc, out = w.check()
    link = link_of(out)
    assert rc == 1 and link["status"] == "broken"
    assert want in kinds(link, "BROKEN")
    # a BROKEN is exactly what it says: the other claims still hold
    assert len(kinds(link, "BROKEN")) == 1, kinds(link)
    assert link["gaps"] == []


def test_a_broken_folds_its_diff_item(tmp_path: Path) -> None:
    w = pinned_world(tmp_path)
    w.edit(data(_no_404))  # status_removed 404 is the BROKEN's cause: not repeated as CHANGED
    link = link_of(w.check()[1])
    assert kinds(link) == [("status_not_observed", JOB, "404")]

    def to_201(d: list) -> None:  # 201 -> 200 on the relied endpoint
        steps_of(d, "jobs")[4]["expect_status"] = 200

    w.edit(data(to_201))
    link = link_of(w.check()[1])
    assert kinds(link, "BROKEN") == [("status_not_observed", RUNS, "201")]
    assert kinds(link, "CHANGED") == [("status_added", RUNS, "200")]
    assert [f["relied"] for f in link["findings"] if f["class"] == "CHANGED"] == [True]


def test_endpoint_missing_is_reported_once(tmp_path: Path) -> None:
    w = pinned_world(tmp_path)
    w.edit(data(_drop_runs))
    link = link_of(w.check()[1])
    # the endpoint claim only: not also its status, fields and auth
    assert kinds(link, "BROKEN") == [("endpoint_missing", RUNS, "")]
    assert kinds(link, "CHANGED") == []  # endpoint_removed folded


# ---- GAP -------------------------------------------------------------------------------------


def test_gaps_are_informational(tmp_path: Path) -> None:
    imports = [
        *IMPORTS,
        {"endpoint": "GET /nope", "status": [200], "auth": "public"},
        {"endpoint": "GET /jobs", "status": [200, 500], "auth": "token", "as_user": "bob",
         "request_fields": ["cursor"], "response_fields": ["zzz"]},
    ]  # fmt: skip
    w = pinned_world(tmp_path, imports=imports)
    rc, out = w.check()
    link = link_of(out)
    assert rc == 0 and link["status"] == "ok" and link["findings"] == []
    assert [(g["kind"], g["endpoint"], g["item"]) for g in link["gaps"]] == [
        ("auth_unobserved", JOBS, "as_user:bob"),
        ("request_field_unobserved", JOBS, "cursor"),
        ("response_field_unobserved", JOBS, "zzz"),
        ("status_unobserved", JOBS, "500"),
        ("endpoint_unobserved", "GET /nope", ""),
    ]
    assert all(g["class"] == "GAP" for g in link["gaps"])


def test_a_gap_is_not_a_broken_when_the_endpoint_goes_away(tmp_path: Path) -> None:
    w = pinned_world(
        tmp_path, imports=[{"endpoint": "GET /nope", "status": [200], "auth": "public"}]
    )
    w.edit(data(lambda d: d.pop()))
    rc, out = w.check()
    link = link_of(out)
    assert [g["kind"] for g in link["gaps"]] == ["endpoint_unobserved"]
    assert kinds(link, "BROKEN") == [] and rc == 1  # the removed endpoints are CHANGED


# ---- CHANGED ---------------------------------------------------------------------------------


def _changed(tmp_path: Path, fn, imports: list[dict] | None = None) -> set[tuple]:
    w = pinned_world(tmp_path, imports=imports)
    w.edit(data(fn))
    rc, out = w.check()
    link = link_of(out)
    assert kinds(link, "BROKEN") == [], link["findings"]
    assert rc == (1 if link["findings"] else 0)
    return {(f["kind"], f["endpoint"], f["item"], f.get("where"), f["relied"])
            for f in link["findings"]}  # fmt: skip


def test_changed_endpoint_added_and_removed(tmp_path: Path) -> None:
    got = _changed(tmp_path, lambda d: d[1][1].append(st("GET", "/health", None, 200)))
    assert got == {("endpoint_added", "GET /health", "", None, False)}
    got = _changed(tmp_path / "b", lambda d: d[1][1].__delitem__(6))  # GET /users/me
    assert got == {("endpoint_removed", "GET /users/me", "", None, False)}


def test_changed_relied_follows_the_imports(tmp_path: Path) -> None:
    # a status on a relied endpoint is relied; the same on an unrelied one is not
    got = _changed(tmp_path, lambda d: d[1][1].append(st("GET", "/jobs/7", "ann", 429)))
    assert got == {("status_added", JOB, "429", None, True)}
    got = _changed(tmp_path / "b", lambda d: d[1][1].append(st("GET", "/users/me", "ann", 429)))
    assert got == {("status_added", "GET /users/me", "429", None, False)}


def test_changed_status_removed(tmp_path: Path) -> None:
    imports = [{"endpoint": "GET /jobs/{id}", "status": [200], "auth": "token"}]
    got = _changed(tmp_path, _no_404, imports)  # the import never relied on 404
    assert got == {("status_removed", JOB, "404", None, True)}


def test_changed_field_added_and_removed_with_where(tmp_path: Path) -> None:
    def add(d: list) -> None:
        steps_of(d, "login")[0]["json"] = {"email": "a", "password": "b", "otp": "1"}
        steps_of(d, "login")[0]["expect_body"]["expires"] = 1
        steps_of(d, "jobs")[1]["params"] = {"limit": 5, "cursor": "c"}

    got = _changed(tmp_path, add)
    assert got == {
        ("field_added", LOGIN, "otp", "request", True),
        ("field_added", LOGIN, "expires", "response", True),
        ("field_added", JOBS, "cursor", "query", True),
    }

    def remove(d: list) -> None:
        steps_of(d, "jobs")[6]["expect_body"] = {}  # GET /users/me: id is not relied on

    got = _changed(tmp_path / "b", remove)
    assert got == {("field_removed", "GET /users/me", "id", "response", False)}


def test_changed_auth_tightened_and_loosened(tmp_path: Path) -> None:
    got = _changed(tmp_path, lambda d: d[1][1].append(st("GET", "/users/me", None, 401)))
    assert got == {
        ("status_added", "GET /users/me", "401", None, False),
        ("auth_tightened", "GET /users/me", "auth", None, False),  # unknown -> token
    }
    got = _changed(tmp_path / "b", lambda d: d[1][1].append(st("GET", "/jobs", None, 200)))
    assert got == {("auth_loosened", JOBS, "auth", None, True)}  # token -> public
    # one handle's move: bob was refused on the runs, now granted
    got = _changed(tmp_path / "c", lambda d: steps_of(d, "jobs")[5].update(expect_status=201))
    assert got == {
        ("auth_loosened", RUNS, "as_user:bob", None, True),
        ("status_removed", RUNS, "403", None, True),  # the only 403 step moved to 201
    }


def test_changed_body_shape_and_error_body(tmp_path: Path) -> None:
    got = _changed(tmp_path, lambda d: steps_of(d, "jobs")[6].update(expect_body=[{"id": 1}]))
    assert got == {("body_shape_changed", "GET /users/me", "body", None, False)}
    w = pinned_world(tmp_path / "b")
    w.edit(data(), error_body="drf")
    link = link_of(w.check()[1])
    assert [(f["kind"], f["endpoint"], f["relied"]) for f in link["findings"]] == [
        ("error_body_changed", "*", True)
    ]


def test_version_changed_is_informational(tmp_path: Path) -> None:
    from .helpers import git

    w = pinned_world(tmp_path, repo=True)
    git(w.provider, "commit", "--allow-empty", "-qm", "readme only")
    rc, out = w.check()
    link = link_of(out)
    assert rc == 0 and link["findings"] == []
    assert link["version_changed"] is True and link["spec_hash_changed"] is False
    assert link["pinned"]["git_ref"] != link["current"]["git_ref"]
    (w.provider / "spec.py").write_text((w.provider / "spec.py").read_text() + "\n# dirty\n")
    link = link_of(w.check()[1])
    assert link["current"]["dirty"] is True and link["version_changed"] is True


# ---- pin -------------------------------------------------------------------------------------


def test_pin_refuses_on_broken_unless_forced(tmp_path: Path) -> None:
    w = pinned_world(tmp_path)
    before = w.lock.read_bytes()
    w.edit(data(_no_404))
    rc, out = w.pin()
    assert rc == 1 and out["written"] is False
    assert out["links"][0]["status"] == "refused"
    assert [(b["kind"], b["item"]) for b in out["links"][0]["broken"]] == [
        ("status_not_observed", "404")
    ]
    assert w.lock.read_bytes() == before
    rc, out = w.pin("--force")
    assert rc == 0 and out["links"][0]["forced"] is True and w.lock.read_bytes() != before
    # the disclosed cost of --force: the reliance is now a gap, not BROKEN
    rc, out = w.check()
    link = link_of(out)
    assert rc == 0 and link["findings"] == []
    assert [(g["kind"], g["item"]) for g in link["gaps"]] == [("status_unobserved", "404")]


def test_pin_with_changed_only_writes(tmp_path: Path) -> None:
    w = pinned_world(tmp_path)
    w.edit(data(lambda d: d[1][1].append(st("GET", "/health", None, 200))))
    assert w.check()[0] == 1
    assert w.pin()[0] == 0  # CHANGED is acknowledged by a re-pin
    assert w.check()[0] == 0


def test_pin_with_nothing_opted_in_is_an_error(tmp_path: Path) -> None:
    w = World(tmp_path, contract=None)
    rc, out = w.pin()
    assert rc == 2 and [p["code"] for p in out["problems"]] == ["no_links"]
    assert not w.lock.exists()


def test_pin_keeps_the_other_providers_pins(tmp_path: Path) -> None:
    w = pinned_world(tmp_path)
    lock = json.loads(w.lock.read_text())
    lock["providers"]["elsewhere"] = lock["providers"]["prov"]
    w.lock.write_text(ck.lock_text(lock))
    assert w.pin()[0] == 0
    assert set(json.loads(w.lock.read_text())["providers"]) == {"elsewhere", "prov"}


# ---- no_pin, bad_pin, bad_imports --------------------------------------------------------------


def test_no_pin_names_the_lock_and_the_pin_command(tmp_path: Path) -> None:
    w = World(tmp_path)
    rc, out = w.check()
    link = link_of(out)
    assert rc == 2 and link["status"] == "error" and link["error"]["code"] == "no_pin"
    assert str(w.lock) in link["error"]["message"]
    assert "lattice contracts pin" in link["error"]["message"]
    # a lock without this provider
    pinned_world(tmp_path / "b")
    w2 = World(tmp_path / "c")
    w2.lock.write_text(ck.lock_text({"format": 1, "consumer": "cons", "providers": {}}))
    rc, out = w2.check()
    assert rc == 2 and link_of(out)["error"]["code"] == "no_pin"
    assert "prov" in link_of(out)["error"]["message"]


def test_bad_pin(tmp_path: Path) -> None:
    w = pinned_world(tmp_path)
    lock = json.loads(w.lock.read_text())
    h = lock["providers"]["prov"]["version"]["spec_hash"]
    lock["providers"]["prov"]["version"]["spec_hash"] = ("0" if h[0] != "0" else "1") + h[1:]
    w.lock.write_text(ck.lock_text(lock))
    rc, out = w.check()
    assert rc == 2 and link_of(out)["error"]["code"] == "bad_pin"
    assert w.pin()[0] == 2  # a bad lock is never overwritten silently
    for text in ("not json", "[]", json.dumps({"format": 2}),
                 json.dumps({"format": 1, "consumer": "other", "providers": {}})):  # fmt: skip
        w.lock.write_text(text)
        rc, out = w.check()
        assert rc == 2 and link_of(out)["error"]["code"] == "bad_pin", text


_OK_IMPORT = {"provider": "prov", "endpoint": "GET /a", "status": [200], "auth": "public"}


def _toml(**changes: object) -> str:
    imp = {k: v for k, v in {**_OK_IMPORT, **changes}.items() if v is not None}
    return "[[import]]\n" + "".join(f"{k} = {json.dumps(v)}\n" for k, v in imp.items())


BAD_IMPORTS = {
    "unknown_key": _toml(x=1),
    "unknown_top": _toml() + "[meta]\n",
    "empty_status": _toml(status=[]),
    "string_status": _toml(status="200"),
    "bool_status": _toml(status=[True]),
    "bad_auth": _toml(auth="admin"),
    "no_auth": _toml(auth=None),
    "as_user_public": _toml(as_user="ann"),
    "bad_endpoint": _toml(endpoint="GET a"),
    "query_endpoint": _toml(endpoint="GET /a?b=1"),
    "bad_fields": _toml(response_fields="id"),
    "no_provider": _toml(provider=None),
    "unknown_provider": _toml(provider="nobody"),
    "not_toml": "[[import\n",
    "no_imports_at_all": "# nothing\n",
}


@pytest.mark.parametrize("name", list(BAD_IMPORTS))
def test_bad_imports(tmp_path: Path, name: str) -> None:
    w = pinned_world(tmp_path)
    (w.consumer / ck.IMPORTS_FILE).write_text(BAD_IMPORTS[name])
    rc, out = w.check()
    assert rc == 2 and link_of(out)["error"]["code"] == "bad_imports", link_of(out)["error"]


def test_no_imports_file(tmp_path: Path) -> None:
    w = pinned_world(tmp_path)
    (w.consumer / ck.IMPORTS_FILE).unlink()
    rc, out = w.check()
    assert rc == 2 and link_of(out)["error"]["code"] == "no_imports"
    assert ck.IMPORTS_FILE in link_of(out)["error"]["message"]


def test_imports_are_normalised() -> None:
    from .helpers import imports_text

    text = imports_text({"endpoint": "post /jobs/{job_id}/runs/", "status": [201, 201],
                         "auth": "token", "response_fields": ["b", "a", "b"]})  # fmt: skip
    p = Path(__file__).parent / "_tmp_imports.toml"
    try:
        p.write_text(text)
        (imp,) = ck.parse_imports(p)
    finally:
        p.unlink(missing_ok=True)
    assert imp.endpoint == "POST /jobs/{}/runs" and imp.status == (201,)
    assert imp.response_fields == ("b", "a") and imp.request_fields == ()


# ---- exit codes ------------------------------------------------------------------------------


def test_exit_codes_and_precedence(tmp_path: Path) -> None:
    w = pinned_world(tmp_path)
    assert w.check()[0] == 0
    w.edit(data(_no_404))
    assert w.check()[0] == 1
    # a second link that cannot run: 2 takes precedence over 1
    (tmp_path / "other").mkdir()
    (tmp_path / "cons" / ck.IMPORTS_FILE).write_text(
        (tmp_path / "cons" / ck.IMPORTS_FILE).read_text()
        + "\n[[import]]\nprovider='other'\nendpoint='GET /x'\nstatus=[200]\nauth='public'\n"
    )
    manifest = tmp_path / "lattice.workspace.toml"
    manifest.write_text(
        manifest.read_text() + '\n[[project]]\nname = "other"\npath = "other"\n\n'
        '[[link]]\nfrom = "cons"\nto = "other"\nkind = "uses"\ncontract = "static"\n'
    )
    rc, out = w.check()
    assert rc == 2 and out["checked"] == 2
    assert [lk["status"] for lk in out["links"]] == ["broken", "error"]
    assert out["links"][1]["error"]["code"] == "export_failed"


def test_unknown_consumer_and_manifest_errors_exit_2(tmp_path: Path) -> None:
    w = pinned_world(tmp_path)
    rc, out = w.run("check", "nobody")
    assert rc == 2 and [p["code"] for p in out["problems"]] == ["unknown_project"]
    w.write_manifest(extra_link='\n[[link]]\nfrom = "cons"\nto = "ghost"\nkind = "uses"\n')
    rc, out = w.check()
    assert rc == 2 and any(p["code"] == "unknown_project" for p in out["problems"])
    assert out["links"] == []


def test_text_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from lattice.contracts.__main__ import main

    w = pinned_world(tmp_path, imports=[*IMPORTS, {"endpoint": "GET /nope", "status": [200],
                                                    "auth": "public"}])  # fmt: skip
    w.edit(data(_no_404))
    assert main(["check", "cons", "--workspace", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "BROKEN" in out and "status_not_observed" in out and "gap" in out
    assert "1 link(s) checked: 1 broken, 0 changed, 1 gap(s)" in out


# ---- live ------------------------------------------------------------------------------------


def test_the_live_stub_raises_not_built() -> None:
    with pytest.raises(NotImplementedError, match="live check not built"):
        live.run_live(None, None, [], {})  # type: ignore[arg-type]


def test_live_link_reports_not_built_cleanly(tmp_path: Path) -> None:
    w = pinned_world(tmp_path, contract="live")
    rc, out = w.check()
    link = link_of(out)
    assert rc == 0 and link["status"] == "ok" and link["contract"] == "live"
    assert link["live"] == {"status": "not_built", "reason": "live check not built"}
    assert link["findings"] == [] and link["gaps"] == []


def test_static_only_skips_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = pinned_world(tmp_path, contract="live")

    def boom(*a, **k):
        raise AssertionError("live must not run")

    monkeypatch.setattr(live, "run_live", boom)
    rc, out = w.check("--static-only")
    assert rc == 0 and link_of(out)["live"] == {"status": "not_run", "reason": "static-only"}


def test_live_findings_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = pinned_world(tmp_path, contract="live")
    seen = {}

    def fake(workspace, link, imports, current_export, *, clone_env=None):
        seen.update(link=link.target, n=len(imports), eps=len(current_export["endpoints"]),
                    env=clone_env)  # fmt: skip
        return {
            "status": "ok",
            "replayed": 4,
            "not_replayed": [],
            "findings": [{"class": "BROKEN", "provider": "prov", "endpoint": RUNS,
                          "kind": "status_not_observed", "item": "201", "detail": "answered 200"}],
            "verified_live": [{"provider": "prov", "endpoint": LOGIN,
                               "kind": "response_field_unobserved", "item": "access_token"}],
        }  # fmt: skip

    monkeypatch.setattr(live, "run_live", fake)
    rc, out = w.check()
    link = link_of(out)
    assert rc == 1 and link["status"] == "broken"
    assert link["findings"] == [] and link["live"]["findings"][0]["kind"] == "status_not_observed"
    assert link["live"]["verified_live"][0]["item"] == "access_token"
    assert seen == {"link": "prov", "n": 4, "eps": 5, "env": None}


def test_live_error_is_exit_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = pinned_world(tmp_path, contract="live")

    def refuse(*a, **k):
        raise live.LiveError("not_loopback", "live_url http://example.com is not loopback")

    monkeypatch.setattr(live, "run_live", refuse)
    rc, out = w.check()
    assert rc == 2 and link_of(out)["error"]["code"] == "not_loopback"
