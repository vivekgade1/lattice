"""Stage 1 of the .NET spike (docs/results/2026-10-18-dotnet-prereg.md, 5.2): `dncheck extract` on the Arm 0
fixture (P0-*, exact against its truth.json), the determinism rows and D1 (the dev rows).

The helper is built once per session into a temporary cache (``LATTICE_DNCHECK_CACHE``); the targets are only
read. Needs the .NET SDK (the tests skip without it).
"""

# ruff: noqa: E501
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Any

import pytest

from lattice import dncheck

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "dncheck" / "fixtures" / "dnparity1"
D1_TGZ = ROOT / "docs" / "results" / "data" / "2026-10-18-dotnet" / "d1.tgz"
D1_TRUTH = ROOT / "tests" / "dncheck" / "d1" / "truth.json"
GO_SRC = ROOT / "lattice" / "gocheck" / "src"

pytestmark = pytest.mark.skipif(shutil.which("dotnet") is None, reason="needs the .NET SDK")


@pytest.fixture(scope="session", autouse=True)
def helper(tmp_path_factory: pytest.TempPathFactory) -> Path:
    cache = tmp_path_factory.mktemp("dncheck-cache")
    old = os.environ.get("LATTICE_DNCHECK_CACHE")
    os.environ["LATTICE_DNCHECK_CACHE"] = str(cache)
    try:
        yield dncheck.build_dncheck()
    finally:
        if old is None:
            os.environ.pop("LATTICE_DNCHECK_CACHE", None)
        else:
            os.environ["LATTICE_DNCHECK_CACHE"] = old


@pytest.fixture(scope="session")
def fx() -> dict[str, Any]:
    return dncheck.extract_json(FIXTURE)


@pytest.fixture(scope="session")
def fx_policy() -> dict[str, Any]:
    return dncheck.extract_json(FIXTURE, policy=True)


@pytest.fixture(scope="session")
def truth() -> dict[str, Any]:
    return json.loads((FIXTURE / "truth.json").read_text())


@pytest.fixture(scope="session")
def d1_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("d1")
    with tarfile.open(D1_TGZ) as tf:
        tf.extractall(out, filter="data")
    for junk in out.rglob("._*"):  # AppleDouble entries of a tarball made on macOS
        junk.unlink()
    return next(out.rglob("dnstreak.csproj")).parent


@pytest.fixture(scope="session")
def d1(d1_dir: Path) -> dict[str, Any]:
    return dncheck.extract_json(d1_dir, policy=True)


# ---- the 4.5 matching rules ------------------------------------------------------------------------


def norm(template: str) -> str:
    t = re.sub(r"\{[^}]*\}", "{}", template)
    t = "/".join(s if s == "{}" else s.lower() for s in t.split("/"))
    return t.rstrip("/") or "/"


def rkey(method: str, template: str) -> tuple[str, str]:
    return method, norm(template)


def route_id_key(route_id: str) -> tuple[str, str]:
    m, t = route_id.split(" ", 1)
    return rkey(m, t)


# ---- schema ----------------------------------------------------------------------------------------


def go_keys(struct: str) -> set[str]:
    """The JSON keys of one goextract struct (read from the Go source, so the check cannot drift)."""
    for f in sorted(GO_SRC.glob("goextract*.go")):
        m = re.search(r"type " + struct + r" struct \{(.*?)\n\}", f.read_text(), re.S)
        if m:
            return set(re.findall(r'`json:"([a-z_]+)', m.group(1)))
    raise AssertionError(struct)


def test_schema_is_the_goextract_key_set_plus_dotnet(fx: dict[str, Any]) -> None:
    assert fx["schema"] == "lattice.dnextract/1"
    top = go_keys("xResult") - {"grpc"}  # gRPC for .NET is out of scope in round 1
    assert top <= set(fx), top - set(fx)
    assert "dotnet" in fx and fx["go"] == "" and fx["dotnet"]["sdk"]
    for struct, items in [
        ("xRoute", fx["routes"]),
        ("xTable", fx["tables"]),
        ("xColumn", [c for t in fx["tables"] for c in t["columns"]]),
        ("xParam", [p for r in fx["routes"] for p in r["params"]]),
        ("xRaise", [x for r in fx["routes"] for x in r["raises"]]),
        ("xOwner", [o for r in fx["routes"] for o in r["owner_uses"]]),
        ("xHandler", [r["handler"] for r in fx["routes"]]),
        ("xStatusW", fx["status_writes"]),
        ("xUnread", fx["unread"]),
        ("xExcluded", fx["excluded"]),
    ]:
        assert items, struct
        missing = {k for k in go_keys(struct) if any(k not in i for i in items)}
        optional = {
            "parse",
            "parse_at",
            "bind",
            "sentinel",
            "json",
            "method",
            "path",
        }  # omitempty/opt-in
        assert not (missing - optional), (struct, missing - optional)
    assert fx["routes"][0]["style"] in ("controller", "minimal") and fx["routes"][0]["template"]


# ---- P0-* ------------------------------------------------------------------------------------------


def test_p0_routes(fx: dict[str, Any], truth: dict[str, Any]) -> None:
    t = {rkey(r["method"], r["template"]): r for r in truth["routes"]}
    e = {rkey(r["method"], r["path"]): r for r in fx["routes"]}
    assert sorted(t) == sorted(e)
    for k, a in t.items():
        b = e[k]
        assert (a["style"], a["condition"], a["path_template"]) == (
            b["style"],
            b["condition"],
            b["path_template"],
        ), k
        assert a["template"] == b["path"] == b["template"], k
        assert a["success"] == b["status_codes"], k
        assert a["handler"] == b["handler"]["func"], k


def test_p0_auth(fx: dict[str, Any], truth: dict[str, Any]) -> None:
    t = {rkey(r["method"], r["template"]): r["auth"] for r in truth["routes"]}
    e = {rkey(r["method"], r["path"]): r["auth"] for r in fx["routes"]}
    assert t == e
    assert sorted(set(t.values())) == ["Admin", "Member", "Public"]


def test_p0_excluded_and_unread_routes(fx: dict[str, Any], truth: dict[str, Any]) -> None:
    assert {(x["kind"], x["file"], x["line"]) for x in truth["excluded"]} == {
        (x["kind"], x["file"], x["line"]) for x in fx["excluded"]
    }
    t = {(x["file"], x["line"]) for x in truth["unread"] if x["category"] == "route"}
    e = {(x["file"], x["line"]) for x in fx["unread"] if x.get("category") == "route"}
    assert t == e


def test_p0_bind(fx: dict[str, Any], truth: dict[str, Any]) -> None:
    by_route = {rkey(r["method"], r["path"]): r for r in fx["routes"]}
    for t in truth["routes"]:
        k = rkey(t["method"], t["template"])
        r = by_route[k]
        want = sorted(
            (p["name"], p["source"], p.get("type"), p.get("parse"))
            for p in t["params"]
            if not p.get("dropped") and p["source"] != "body"
        )
        got = sorted(
            (p["name"], p["bind_source"], p["type"], p.get("parse"))
            for p in r["params"]
            if p["source"] != "body"
        )
        assert want == got, k
        want_b = sorted(
            (
                b["field"],
                b["required"] or None,
                b["ge"],
                b["le"],
                b["min_length"],
                b["max_length"],
                b["enforced_by"],
            )
            for b in truth["bounds"]
            if route_id_key(b["route"]) == k
        )
        got_b = sorted(
            (
                p["name"],
                p["required"] or None,
                p["ge"],
                p["le"],
                p["min_length"],
                p["max_length"],
                p["enforced_by"],
            )
            for p in r["params"]
            if p["source"] == "body"
            and (
                p["required"]
                or any(p[f] is not None for f in ("ge", "le", "min_length", "max_length"))
            )
        )
        assert want_b == got_b, k
    want_u = sorted(
        (route_id_key(x["route"]), x["file"], x["line"], x["what"], x["reason"])
        for x in truth["unread"]
        if x["category"] == "bind"
    )
    got_u = sorted(
        (route_id_key(x["route"]), x["file"], x["line"], x["what"], x["reason"])
        for x in fx["unread"]
        if x.get("category") == "bind"
    )
    assert want_u == got_u
    assert fx["dotnet"]["bind_status"] == truth["bind_status"] == 400


def test_p0_tables(fx: dict[str, Any], truth: dict[str, Any]) -> None:
    tt = {t["table"]: t for t in truth["tables"]}
    et = {t["table"]: t for t in fx["tables"]}
    assert sorted(tt) == sorted(et)
    for name, t in tt.items():
        e = et[name]
        assert t["entity"] == e["struct"]
        assert [c["name"] for c in t["columns"]] == [c["name"] for c in e["columns"]], name
        for c, d in zip(t["columns"], e["columns"], strict=True):
            for tf, ef in [
                ("csharp_type", "go_type"), ("wire", "wire"), ("private", "private"), ("nullable", "nullable"),
                ("unique", "unique"), ("pk", "primary_key"), ("max_length", "max_length"), ("fk", "foreign_key"),
                ("default", "default"), ("enum_storage", "enum_storage"), ("choices", "choices"),
            ]:  # fmt: skip
                assert c[tf] == d[ef], (name, c["name"], tf)
        assert [u["columns"] for u in t["unique_together"]] == e["unique_together"], name
    want = sorted((x["file"], x["line"]) for x in truth["unread"] if x["category"] == "table")
    got = sorted((x["file"], x["line"]) for x in fx["unread"] if x.get("category") == "table")
    assert want == got  # the TPH pair and the owned type: unread, no table entry


def test_p0_owner(fx: dict[str, Any], truth: dict[str, Any]) -> None:
    def via(v: dict[str, Any] | None) -> str | None:
        return (
            json.dumps({k: x for k, x in v.items() if x is not None}, sort_keys=True) if v else None
        )

    want = sorted(
        (
            route_id_key(o["route"]),
            o["table"],
            o["column"],
            o["kind"],
            o["file"],
            o["line"],
            o["value"],
            via(o.get("via")),
        )
        for o in truth["owner_uses"]
    )
    got = sorted(
        (
            rkey(r["method"], r["path"]),
            o["model"],
            o["column"],
            o["how"],
            o["file"],
            o["line"],
            o["value"],
            via(o.get("via")),
        )
        for r in fx["routes"]
        for o in r["owner_uses"]
    )
    assert want == got
    qf = truth["dotnet"]["query_filters"][0]
    mine = fx["dotnet"]["query_filters"]
    assert [(q["table"], q["column"], q["owner"], q["line"]) for q in mine] == [
        (qf["table"], qf["column"], True, qf["line"])
    ]
    assert sorted(route_id_key(i["route"]) for i in qf["ignored_by"]) == sorted(
        route_id_key(x) for x in mine[0]["ignored_by"]
    )


def test_p0_state(fx: dict[str, Any], truth: dict[str, Any]) -> None:
    def fr(x: list[str] | None) -> tuple[str, ...] | None:
        return tuple(x) if x else None

    want = sorted(
        (
            route_id_key(w["route"]),
            w["table"],
            w["column"],
            w["value"],
            w["stored"],
            w["kind"],
            fr(w["from"]),
            w["file"],
            w["line"],
        )
        for w in truth["status_writes"]
    )
    got = sorted(
        (
            route_id_key(w["route"]),
            w["model"],
            w["column"],
            w["value"],
            w["stored"],
            w["kind"],
            fr(w["from"]),
            w["file"],
            w["line"],
        )
        for w in fx["status_writes"]
    )
    assert want == got
    wf = sorted(
        (
            route_id_key(f["route"]),
            f["table"],
            f["column"],
            f["to"],
            fr(f["from"]),
            f["file"],
            f["line"],
        )
        for f in truth["status_facts"]
    )
    gf = sorted(
        (
            route_id_key(f["route"]),
            f["model"],
            f["column"],
            f["to"],
            fr(f["from"]),
            f["file"],
            f["line"],
        )
        for f in fx["status_facts"]
    )
    assert wf == gf
    assert any(w["guard"] for w in fx["status_writes"] if w["kind"] != "initial")


def test_p0_raise(fx: dict[str, Any], truth: dict[str, Any]) -> None:
    want = sorted(
        (
            route_id_key(x["route"]),
            x["status"],
            x["code"],
            x["detail"],
            x["kind"],
            x["sentinel"],
            x["file"],
            x["line"],
        )
        for x in truth["raises"]
    )
    got = sorted(
        (
            rkey(r["method"], r["path"]),
            x["status"],
            x["code"],
            x["detail"],
            x["kind"],
            x.get("sentinel"),
            x["file"],
            x["line"],
        )
        for r in fx["routes"]
        for x in r["raises"]
    )
    assert want == got
    wu = sorted(
        (route_id_key(x["route"]), x["file"], x["line"], x["what"], x["reason"])
        for x in truth["unread"]
        if x["category"] == "raise"
    )
    gu = sorted(
        (route_id_key(x["route"]), x["file"], x["line"], x["what"], x["reason"])
        for x in fx["unread"]
        if x.get("category") == "raise"
    )
    assert wu == gu


def test_p0_di(fx: dict[str, Any], truth: dict[str, Any]) -> None:
    t, e = truth["di"], fx["dotnet"]["di"]
    assert sorted(
        (r["interface"], r["implementation"], r["lifetime"], r["file"], r["line"])
        for r in t["registrations"]
    ) == sorted(
        (r["interface"], r["implementation"], r["lifetime"], r["file"], r["line"])
        for r in e["registrations"]
    )
    assert sorted(t["single"]) == sorted(e["single"]) and sorted(t["multiple"]) == sorted(
        e["multiple"]
    )
    assert sorted(
        (route_id_key(c["route"]), c["call"], c["resolved_to"], c["file"], c["line"])
        for c in t["resolved_calls"]
    ) == sorted(
        (route_id_key(c["route"]), c["call"], c["resolved_to"], c["file"], c["line"])
        for c in e["resolved_calls"]
    )
    want = sorted(
        (route_id_key(x["route"]), x["file"], x["line"], x["what"], x["reason"])
        for x in truth["unread"]
        if x["category"] == "di"
    )
    got = sorted(
        (route_id_key(x["route"]), x["file"], x["line"], x["what"], x["reason"])
        for x in fx["unread"]
        if x.get("category") == "di"
    )
    assert want == got  # two registrations: never the first match


def test_p0_json(fx_policy: dict[str, Any], truth: dict[str, Any]) -> None:
    assert fx_policy["dotnet"]["json_naming"] == truth["json_naming"] == "SnakeCaseLower"
    tt = {t["table"]: t for t in truth["tables"]}
    for t in fx_policy["tables"]:
        for c in t["columns"]:
            want = next(x["wire"] for x in tt[t["table"]]["columns"] if x["name"] == c["name"])
            assert c["wire"] == want
            assert c["json"] == (
                want if want is not None else "-"
            )  # policy extract: "-" = never serialized
    parse = [p for r in fx_policy["routes"] for p in r["params"] if p.get("parse")]
    assert parse and all(p["parse"] == "Int" for p in parse)
    assert any(
        x.get("bind") for r in fx_policy["routes"] for x in r["raises"]
    )  # the policy-only bind refusals


def test_p0_auth_object(fx: dict[str, Any], truth: dict[str, Any]) -> None:
    assert fx["auth"]["scheme"] == truth["auth"]["scheme"] == "Bearer"
    assert fx["auth"]["caller_keys"] == truth["auth"]["caller_keys"] == ["sub"]
    assert fx["dotnet"]["auth"]["admin_role"] == truth["auth"]["admin_role"] == "Admin"
    assert fx["dotnet"]["auth"]["caller_funcs"] == sorted(truth["auth"]["caller_funcs"])
    assert fx["dialect"] == "mysql" and fx["dotnet"]["providers"] == ["mysql"]
    assert [x["type"] for x in fx["dotnet"]["exception_map"]] == [
        x["type"] for x in truth["dotnet"]["exception_map"]
    ]


# ---- DET-* -----------------------------------------------------------------------------------------


def raw_extract(root: Path, **env: str) -> bytes:
    dll = dncheck.build_dncheck()
    payload = json.dumps({"root": str(root), "sdk": dncheck.sdk_version(), "policy": True})
    proc = subprocess.run(  # noqa: S603
        ["dotnet", str(dll), "extract"],
        input=payload.encode(),
        capture_output=True,
        check=True,
        env={**os.environ, **dncheck.DOTNET_ENV, **env},
    )
    return proc.stdout


def test_det_10_on_the_fixture() -> None:
    assert len({hashlib.sha256(raw_extract(FIXTURE)).hexdigest() for _ in range(10)}) == 1


def test_det_path_and_locale(tmp_path: Path) -> None:
    base = raw_extract(FIXTURE)
    a, b = tmp_path / "x" / "one", tmp_path / "yy" / "zzz" / "another-name"
    for dst in (a, b):
        shutil.copytree(FIXTURE, dst)
    assert raw_extract(a) == base == raw_extract(b)
    assert raw_extract(a, LANG="de_DE.UTF-8", LC_ALL="de_DE.UTF-8") == base
    assert str(tmp_path).encode() not in base and str(ROOT).encode() not in base  # no absolute path


def test_the_target_and_the_source_tree_are_never_written() -> None:
    def tree_hash(p: Path) -> str:
        h = hashlib.sha256()
        for f in sorted(x for x in p.rglob("*") if x.is_file()):
            h.update(f.relative_to(p).as_posix().encode() + f.read_bytes())
        return h.hexdigest()

    before = tree_hash(FIXTURE)
    dncheck.extract_json(FIXTURE)
    assert tree_hash(FIXTURE) == before
    assert not (dncheck.SRC / "bin").exists() and not (dncheck.SRC / "obj").exists()
    assert not list(FIXTURE.rglob("bin")) and not list(FIXTURE.rglob("obj"))


def test_the_build_is_cached_and_keyed_by_the_sources(helper: Path) -> None:
    t0 = time.perf_counter()
    assert dncheck.build_dncheck() == helper
    assert time.perf_counter() - t0 < 1.0  # warm: a lookup, no build
    assert helper.parent.name == dncheck.source_hash()[:24] and helper.name == "dncheck.dll"


# ---- D1 (dev) --------------------------------------------------------------------------------------


def test_d1_routes_auth_unread(d1: dict[str, Any]) -> None:
    truth = json.loads(D1_TRUTH.read_text())
    t = {rkey(r["method"], r["template"]): r for r in truth["routes"]}
    e = {rkey(r["method"], r["path"]): r for r in d1["routes"]}
    assert sorted(t) == sorted(e)  # D-ROUTES: recall 1.00, 0 extra
    auth = sum(t[k]["auth"] == e[k]["auth"] for k in t) / len(t)
    assert auth >= 0.95  # D-AUTH
    for k in t:
        assert t[k]["style"] == e[k]["style"]
    named = {(x["file"], x["line"]) for x in d1["unread"]} | {
        (x["file"], x["line"]) for x in d1["excluded"]
    }
    assert all(
        k in e or any(f == t[k]["file"] for f, _ in named) for k in t
    )  # D-UNREAD: 0 silent drops
    assert d1["dialect"] == "" and d1["dotnet"]["providers"] == ["mysql", "sqlite"]
    assert d1["auth"]["login_path"] == truth["login"]["path"]
    assert d1["auth"]["token_field"] == truth["login"]["token_field"]
    assert {
        p["name"] for p in next(r for r in d1["routes"] if r["path"] == "/v1/auth/login")["params"]
    } == set(truth["login"]["fields"])


def test_d1_tables_and_wire(d1: dict[str, Any]) -> None:
    truth = json.loads(D1_TRUTH.read_text())
    tt = {t["table"]: t for t in truth["tables"]}
    et = {t["table"]: t for t in d1["tables"]}
    assert sorted(tt) == sorted(et) and len(tt) == 9  # D-TABLES 9/9
    total = good = wire_total = wire_good = 0
    for name, t in tt.items():
        ec = {c["name"]: c for c in et[name]["columns"]}
        for c in t["columns"]:
            e = ec[c["name"]]
            for f, ef in [
                ("nullable", "nullable"),
                ("pk", "primary_key"),
                ("max_length", "max_length"),
                ("fk", "foreign_key"),
                ("private", "private"),
            ]:
                total += 1
                good += c[f] == e[ef]
            total += 1
            good += c["unique"] == (
                e["unique"] or e["primary_key"]
            )  # the truth counts a primary key as unique
            if c["wire"] is not None:
                wire_total += 1
                wire_good += e["wire"] == c["wire"]
    assert good / total >= 0.95
    assert wire_good / wire_total >= 0.98  # D-WIRE
    assert d1["dotnet"]["json_naming"] == "SnakeCaseLower"


def test_d1_owner_and_raise(d1: dict[str, Any]) -> None:
    truth = json.loads(D1_TRUTH.read_text())
    owned = set(truth["owners"]) | set(truth["owner_via"])
    seen = {o["model"] for r in d1["routes"] for o in r["owner_uses"]}
    assert not (seen - owned)  # 0 evidence on a table truth says is not owned
    member = [r for r in d1["routes"] if r["auth"] != "Public"]
    assert sum(bool(r["owner_uses"]) for r in member) / len(member) >= 0.85  # D-OWN
    tp = {(route_id_key(x["route"]), x["status"]) for x in truth["refusals"]}
    ep = {(rkey(r["method"], r["path"]), x["status"]) for r in d1["routes"] for x in r["raises"]}
    assert len(tp & ep) / len(ep) >= 0.95 and len(tp & ep) / len(tp) >= 0.70  # D-RAISE


def test_d1_x_wall_time_and_det(d1_dir: Path) -> None:
    times, digests = [], set()
    for _ in range(10):
        t0 = time.perf_counter()
        digests.add(hashlib.sha256(raw_extract(d1_dir)).hexdigest())
        times.append(time.perf_counter() - t0)
    assert len(digests) == 1  # DET-10 on D1
    assert max(times) <= 60  # D-X (RC6: the prediction is 2-8 s)


def test_det_restored_and_path_on_d1(d1_dir: Path, tmp_path: Path) -> None:
    clean = raw_extract(d1_dir)
    other = tmp_path / "somewhere" / "else"
    shutil.copytree(d1_dir, other)
    assert raw_extract(other) == clean  # DET-PATH
    restored = tmp_path / "restored"
    shutil.copytree(d1_dir, restored)
    proc = subprocess.run(  # noqa: S603  (a scratch copy: the extractor never builds a target)
        ["dotnet", "build", "-c", "Release", "-p:RestoreLockedMode=true", "-v", "q", "-nologo"],
        cwd=restored, capture_output=True, text=True, check=False, env={**os.environ, **dncheck.DOTNET_ENV},
    )  # fmt: skip
    if proc.returncode != 0 or not (restored / "obj").exists():
        pytest.skip("restore of the D1 copy needs its NuGet packages: " + proc.stdout[-200:])
    assert (restored / "bin").exists()
    assert raw_extract(restored) == clean  # DET-RESTORED
