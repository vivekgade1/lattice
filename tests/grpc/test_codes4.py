"""Round 4, D3: codes through a translation layer. ``fixtures/codes4/<case>`` are small servers whose
handlers return domain sentinels (directly, or wrapped with ``%w``) and whose interceptor turns them
into codes by a map table, a slice of rows or an ``errors.Is`` switch, or looks codes up in a map by
a constant or by a parameter. ``truth.json`` (hashed before any round-4 library code) says the codes
of every handler. An error whose code is not in the source is ``unread``, never ``UNKNOWN``."""

from __future__ import annotations

# ruff: noqa: E501 -- docstrings and data lines
import hashlib
import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.grpc import oracle
from tests.grpc.conftest import needs_go

C4 = oracle.FIXTURES / "codes4"
TRUTH_SHA = "5d4b0fa686235dda2a87831a80493a357301ad35829273a15709906cd3e303d8"
CASES = ["tbl", "nodefault", "keys"]

pytestmark = needs_go


@pytest.fixture(scope="module")
def truth() -> Any:
    return oracle.load(C4 / "truth.json")["cases"]


@pytest.fixture(scope="module")
def ex(front_end: Any) -> dict[str, dict[str, Any]]:
    return {c: front_end.extract_json(C4, c) for c in CASES}


def handlers(extract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        f"{s['name'].rsplit('.', 1)[1]}/{r['name']}": r
        for s in extract["grpc"]["services"]
        for r in s["rpcs"]
    }


def codes_of(r: dict[str, Any]) -> list[str]:
    return sorted({c["name"] for c in r.get("codes", []) if c["kind"] != "unimplemented_stub"})


def test_the_truth_was_hashed_before_the_library_changed() -> None:
    assert hashlib.sha256((C4 / "truth.json").read_bytes()).hexdigest() == TRUTH_SHA


@pytest.mark.parametrize("case", CASES)
def test_the_codes_of_each_handler_equal_the_truth(
    ex: dict[str, Any], truth: Any, case: str
) -> None:
    got = handlers(ex[case])
    assert set(got) == set(truth[case]["rpcs"])
    for k, want in truth[case]["rpcs"].items():
        assert codes_of(got[k]) == want["codes"], k


@pytest.mark.parametrize("case", CASES)
def test_unread_is_where_the_truth_says_it_is(ex: dict[str, Any], truth: Any, case: str) -> None:
    got = handlers(ex[case])
    for k, want in truth[case]["rpcs"].items():
        assert (
            bool([u for u in got[k].get("unread", []) if u["what"] == "grpc_code_not_literal"])
            == want["unread"]
        ), k


@pytest.mark.parametrize("case", CASES)
def test_d3_3_no_untraced_error_is_an_unknown_placeholder(ex: dict[str, Any], case: str) -> None:
    for k, r in handlers(ex[case]).items():
        assert "UNKNOWN" not in codes_of(r), k
        assert not r.get("plain_error"), k


def test_d3_1_a_map_table_a_wrapped_sentinel_and_a_switch_are_read(ex: dict[str, Any]) -> None:
    got = handlers(ex["tbl"])
    assert codes_of(got["Ta/Get"]) == ["NOT_FOUND"]  # map[error]codes.Code, returned directly
    assert codes_of(got["Ta/Put"]) == ["ALREADY_EXISTS"]  # the same table, fmt.Errorf("%w")
    assert codes_of(got["Tb/Get"]) == ["FAILED_PRECONDITION"]  # an errors.Is case clause


def test_d3_1_a_sentinel_missing_from_the_table_takes_the_literal_default(
    ex: dict[str, Any],
) -> None:
    assert codes_of(handlers(ex["tbl"])["Tb/Put"]) == ["INTERNAL"]


def test_d3_1_a_sentinel_missing_without_a_literal_default_is_unread(ex: dict[str, Any]) -> None:
    r = handlers(ex["nodefault"])["Nd/Put"]
    assert (
        codes_of(r) == [] and [u["what"] for u in r["unread"]].count("grpc_code_not_literal") == 1
    )
    assert codes_of(handlers(ex["nodefault"])["Nd/Get"]) == ["ABORTED"]  # a slice of rows


def test_d3_2_a_parameter_key_is_the_union_over_the_calls_and_a_constant_key_is_one(
    ex: dict[str, Any],
) -> None:
    got = handlers(ex["keys"])
    assert codes_of(got["Kc/Get"]) == ["INVALID_ARGUMENT", "NOT_FOUND"]
    assert codes_of(got["Kc/Put"]) == ["ABORTED"]


def test_a_table_driven_translator_is_no_auth_gate(ex: dict[str, Any]) -> None:
    """``status.Error(c, ...)`` with ``c`` ranged out of the table answers only the table's codes:
    the translating interceptor is not taken for a gate that may refuse callers."""
    assert [i["kind"] for i in ex["tbl"]["grpc"]["interceptors"]] == ["other"]
    assert {r["auth"] for r in handlers(ex["tbl"]).values()} == {"Public"}


# ---- small modules ------------------------------------------------------------------------


def variant(tmp_path: Path, case: str, edits: dict[str, tuple[str, str]]) -> Path:
    root = tmp_path / "m"
    shutil.copytree(C4 / case, root / case)
    for rel, (old, new) in edits.items():
        p = root / case / rel
        assert old in p.read_text(), (rel, old)
        p.write_text(p.read_text().replace(old, new))
    return root


def test_without_a_translation_a_sentinel_stays_a_plain_error_as_in_round_3(
    front_end: Any, tmp_path: Path
) -> None:
    root = variant(
        tmp_path,
        "tbl",
        {
            "main.go": ("", ""),
            "internal/app/app.go": (
                "grpc.NewServer(grpc.UnaryInterceptor(fault.Translate))",
                "grpc.NewServer()",
            ),
        },
    )
    shutil.rmtree(root / "tbl/internal/fault")
    (root / "tbl/internal/fault").mkdir()
    (root / "tbl/internal/fault/fault.go").write_text(
        'package fault\n\nimport "errors"\n\nvar (\n\tErrNotFound = errors.New("nf")\n\tErrDup = errors.New("d")\n'
        '\tErrBusy = errors.New("b")\n\tErrStale = errors.New("s")\n)\n\nfunc Noop() {}\n'
    )
    app = (
        (root / "tbl/internal/app/app.go")
        .read_text()
        .replace('\t"example.com/c4tbl/internal/fault"\n', "")
    )
    (root / "tbl/internal/app/app.go").write_text(app)
    got = handlers(front_end.extract_json(root, "tbl"))
    assert codes_of(got["Ta/Get"]) == ["UNKNOWN"]  # no translation exists: the placeholder is true


def test_a_status_error_sentinel_is_not_rewritten_by_the_table(
    front_end: Any, tmp_path: Path
) -> None:
    root = variant(
        tmp_path,
        "tbl",
        {
            "internal/fault/fault.go": (
                'ErrStale    = errors.New("stale")',
                'ErrStale    = status.Error(codes.Aborted, "stale")',
            )
        },
    )
    got = handlers(front_end.extract_json(root, "tbl"))
    assert codes_of(got["Tb/Put"]) == ["ABORTED"]  # a status error passes through Wire unchanged


def test_d3_3_a_plain_error_beside_a_translator_takes_its_literal_default(
    front_end: Any, tmp_path: Path
) -> None:
    root = variant(
        tmp_path,
        "tbl",
        {
            "internal/svc/svc.go": (
                'return nil, fmt.Errorf("get: %w", fault.ErrBusy)',
                'return nil, fmt.Errorf("plain")',
            )
        },
    )
    got = handlers(front_end.extract_json(root, "tbl"))
    assert codes_of(got["Tb/Get"]) == ["INTERNAL"] and not got["Tb/Get"].get("plain_error")


def test_d3_3_a_plain_error_beside_a_translator_without_a_default_is_unread(
    front_end: Any, tmp_path: Path
) -> None:
    root = variant(
        tmp_path,
        "nodefault",
        {
            "internal/svc/svc.go": (
                'return nil, fmt.Errorf("get: %w", fault.ErrOne)',
                'return nil, fmt.Errorf("plain")',
            )
        },
    )
    r = handlers(front_end.extract_json(root, "nodefault"))["Nd/Get"]
    assert codes_of(r) == [] and any(u["what"] == "grpc_code_not_literal" for u in r["unread"])
