"""The provider's export (lattice/contracts/export.py): path templates, what is derived from a
Spec, determinism, and the version hash (the hash differs exactly when the diff is non-empty)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from lattice.contracts import check as ck
from lattice.contracts import export as ex

from .helpers import BASE, copy_data, export_of, make_spec, st, write_spec

UUID = "123e4567-e89b-12d3-a456-426614174000"


@pytest.mark.parametrize(
    ("path", "want"),
    [
        ("/jobs/7", "/jobs/{}"),
        ("/applications/1/jobs/22/runs", "/applications/{}/jobs/{}/runs"),
        (f"/habits/{UUID}", "/habits/{}"),
        (f"/habits/{UUID.upper()}/completions", "/habits/{}/completions"),
        ("/jobs/{job_id}/runs/{}", "/jobs/{}/runs/{}"),
        ("/users/me", "/users/me"),  # a literal stays: it does not collide with /users/{}
        ("/users/me/", "/users/me"),  # no trailing slash
        ("/jobs?limit=5", "/jobs"),  # no query string
        ("/v2/jobs", "/v2/jobs"),  # only all-digit segments are ids
        ("/reports/2024", "/reports/{}"),
        ("/", "/"),
    ],
)
def test_template_path(path: str, want: str) -> None:
    assert ex.template_path(path) == want


def test_endpoint_key_uppercases_the_method() -> None:
    assert ex.endpoint_key("post", "/jobs/3") == "POST /jobs/{}"
    assert ex.parse_endpoint("get /jobs/{job_id}/") == "GET /jobs/{}"
    assert ex.parse_endpoint("GET /users/me") != ex.parse_endpoint("GET /users/{id}")


@pytest.mark.parametrize(
    "text",
    ["GET", "GET /a /b", "FETCH /a", "GET jobs", "GET /a?x=1", "GET /a//b", 5, "/a GET"],
)
def test_parse_endpoint_refuses(text: object) -> None:
    with pytest.raises(ValueError):
        ex.parse_endpoint(text)


def test_export_facts() -> None:
    data = copy_data(BASE) + [
        ("misc", [
            st("PATCH", "/jobs/7", "ann", 405),  # no 2xx: left out
            {"service": "worker", "expect_db": {"runs": {"count": 1}}},  # an Await: ignored
            st("GET", "/jobs?cursor=a&limit=2", "ann", 200, expect_body=[{"id": 1}, {"x": 1}]),
            st("GET", "/jobs", "bob", 403),
            st("GET", "/jobs", "eve", 200, expect_body=[]),
        ]),
    ]  # fmt: skip
    out = ex.export_spec(make_spec(data))
    eps = out["endpoints"]
    assert out["format"] == 1 and out["basis"] == "observed" and out["error_body"] == "fastapi"
    assert "PATCH /jobs/{}" not in eps
    jobs = eps["GET /jobs"]
    assert jobs["statuses"] == [200, 401, 403]
    assert jobs["query_fields"] == ["cursor", "limit"]  # Step.params and ?a=b names
    assert jobs["response_fields"] == ["id", "name", "x"]  # the keys of the list's items
    assert jobs["body"] == ["list"]
    assert jobs["granted"] == ["ann", "eve"] and jobs["refused"] == ["bob"]
    assert jobs["auth"] == "token"  # every anonymous step got 401
    login = eps["POST /auth/login"]
    assert login["auth"] == "public" and login["granted"] == []
    assert login["request_fields"] == ["email", "password"]  # the 401 step adds nothing new
    assert eps["GET /jobs/{}"]["auth"] == "unknown"  # no anonymous step at all
    runs = eps["POST /jobs/{}/runs"]
    assert runs["granted"] == ["ann"] and runs["refused"] == ["bob"]


def test_fields_come_from_2xx_steps_only() -> None:
    data = [("s", [
        st("POST", "/a", "ann", 200, json={"ok": 1}, expect_body={"id": 1}),
        st("POST", "/a", "ann", 422, json={"bad": 1}, expect_body={"detail": "x"}),
    ])]  # fmt: skip
    ep = ex.export_spec(make_spec(data))["endpoints"]["POST /a"]
    assert ep["request_fields"] == ["ok"] and ep["response_fields"] == ["id"]
    assert ep["statuses"] == [200, 422]


def test_auth_classes() -> None:
    data = [("s", [
        st("GET", "/pub", None, 200),
        st("GET", "/pub", None, 401),  # public wins over a 401
        st("GET", "/tok", None, 401),
        st("GET", "/tok", "ann", 200),
        st("GET", "/mixed", None, 401),
        st("GET", "/mixed", None, 403),
        st("GET", "/mixed", "ann", 200),
        st("GET", "/none", "ann", 200),
    ])]  # fmt: skip
    eps = ex.export_spec(make_spec(data))["endpoints"]
    assert {k.split()[1]: v["auth"] for k, v in eps.items()} == {
        "/pub": "public", "/tok": "token", "/mixed": "unknown", "/none": "unknown"
    }  # fmt: skip


def test_the_export_is_canonical_and_ordered() -> None:
    a = ex.export_spec(make_spec(copy_data(BASE)))
    text = ex.canonical(a)
    assert text == ex.canonical(a) and text.isascii()
    assert ", " not in text and '": ' not in text  # compact separators
    assert text.index('"basis"') < text.index('"endpoints"') < text.index('"format"')  # sorted
    assert ex.spec_hash(a) == ex.spec_hash(ex.export_spec(make_spec(copy_data(BASE))))


def _variants() -> dict[str, list]:
    base = copy_data(BASE)
    out: dict[str, list] = {}

    def v(name: str, fn) -> None:
        d = copy_data(BASE)
        fn(d)
        out[name] = d

    v("identical", lambda d: None)
    v("scenario_reorder", lambda d: d.reverse())
    v("step_reorder", lambda d: d[1][1].reverse())
    v("duplicate_step", lambda d: d[1][1].append(dict(d[1][1][1])))
    v("other_values", lambda d: d[0][1][0].update(json={"email": "zzz", "password": "yyy"}))
    v("status_added", lambda d: d[1][1].append(st("GET", "/jobs", "ann", 429)))
    v("status_removed", lambda d: d[1][1].pop(3))
    v("field_added", lambda d: d[0][1][0].update(json={"email": "a", "password": "b", "otp": "1"}))
    v("response_field", lambda d: d[1][1][2].update(expect_body={"id": 7}))
    v("auth_loosened", lambda d: d[1][1].append(st("GET", "/jobs", None, 200)))
    v("handle_refused", lambda d: d[1][1].append(st("GET", "/jobs", "bob", 403)))
    v("handle_granted", lambda d: d[1][1].append(st("GET", "/jobs", "eve", 200)))
    v("body_shape", lambda d: d[1][1][6].update(expect_body=[{"id": 1}]))
    v("endpoint_added", lambda d: d[1][1].append(st("GET", "/health", None, 200)))
    v("endpoint_removed", lambda d: d[1][1].pop(6))
    assert base == copy_data(BASE)
    return out


@pytest.mark.parametrize("name", list(_variants()))
def test_hash_differs_exactly_when_the_diff_is_non_empty(name: str) -> None:
    pinned = ex.export_spec(make_spec(copy_data(BASE)))
    now = ex.export_spec(make_spec(_variants()[name]))
    changed = bool(ck.diff_exports(pinned, now))
    assert (ex.spec_hash(pinned) != ex.spec_hash(now)) == changed
    quiet = {"identical", "scenario_reorder", "step_reorder", "duplicate_step", "other_values"}
    assert changed == (name not in quiet)


def test_error_body_is_part_of_the_hash_and_the_diff() -> None:
    a = ex.export_spec(make_spec(copy_data(BASE), "fastapi"))
    b = ex.export_spec(make_spec(copy_data(BASE), "drf"))
    assert ex.spec_hash(a) != ex.spec_hash(b)
    assert [d["kind"] for d in ck.diff_exports(a, b)] == ["error_body_changed"]


def test_a_comment_only_edit_does_not_change_the_hash(tmp_path: Path) -> None:
    write_spec(tmp_path / "p", BASE)
    before = export_of(tmp_path / "p")
    write_spec(tmp_path / "p", BASE, comment="\n\n# nothing but a comment   \n")
    after = export_of(tmp_path / "p")
    assert before["version"]["spec_hash"] == after["version"]["spec_hash"]


def test_scenario_reorder_does_not_change_the_hash(tmp_path: Path) -> None:
    write_spec(tmp_path / "p", BASE)
    before = export_of(tmp_path / "p")
    write_spec(tmp_path / "p", list(reversed(copy_data(BASE))))
    assert export_of(tmp_path / "p")["version"]["spec_hash"] == before["version"]["spec_hash"]


def test_export_is_byte_identical_over_three_runs(tmp_path: Path) -> None:
    write_spec(tmp_path / "p", BASE)
    outs = [
        subprocess.run(
            [sys.executable, "-m", "lattice.contracts", "export", "--project", str(tmp_path / "p")],
            capture_output=True, text=True, check=True,
        ).stdout
        for _ in range(3)
    ]  # fmt: skip
    assert outs[0] == outs[1] == outs[2] and outs[0].endswith("\n")
    assert '"spec_ref":"spec:SPEC"' in outs[0]


def test_version_has_git_ref_and_dirty(tmp_path: Path) -> None:
    from .helpers import git, git_init

    p = tmp_path / "p"
    write_spec(p, BASE)
    assert export_of(p)["version"]["git_ref"] is None  # not a repository
    git_init(p)
    v = export_of(p)["version"]
    assert v["git_ref"] == git(p, "rev-parse", "HEAD").strip() and v["dirty"] is False
    (p / "untracked.txt").write_text("x")  # untracked files do not count
    assert export_of(p)["version"]["dirty"] is False
    write_spec(p, BASE, comment="\n# edit\n")  # a tracked file differs from HEAD
    v2 = export_of(p)["version"]
    assert v2["dirty"] is True and v2["spec_hash"] == v["spec_hash"]


def test_a_spec_that_cannot_load_is_export_failed(tmp_path: Path) -> None:
    p = tmp_path / "p"
    p.mkdir()
    (p / "spec.py").write_text('raise RuntimeError("not loaded")\n')
    with pytest.raises(ex.ExportFailed, match="not loaded"):
        ex.compute_export(p)
    with pytest.raises(ex.ExportFailed, match="not a directory"):
        ex.compute_export(tmp_path / "missing")
