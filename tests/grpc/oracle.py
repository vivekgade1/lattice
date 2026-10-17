"""The comparators the gRPC tests and ``dogfood/grpc_probes.py`` share (Spike I).

Each takes what the system under test produced and the independent ground truth (the
protocompile descriptors, ``truth.json``, ``expected_unread.json``, ``expected_bounds.json``,
all written by hand before the extractor ran) and answers the disagreements, as text. An
empty list is agreement. Nothing here reads the extractor to decide what is expected.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOTES = FIXTURES / "notes"
PROTOS = FIXTURES / "protos"
GIN_CLIENT_ONLY = FIXTURES / "gin_client_only"
# proto files read with the proto3 grammar and flagged unread: not trusted, so not compared
UNTRUSTED = ("legacy2.proto", "ed2023.proto")


def load(path: Path) -> Any:
    return json.loads(path.read_text())


# ---- P0-PARSE: the reader against protocompile ------------------------------------------


def contract_view(grpc: Mapping[str, Any], skip_files: Sequence[str] = ()) -> dict[str, Any]:
    """The supported constructs of a ``grpc`` block in one comparable form: services with
    their rpcs (types, streaming flags, the first http binding), messages with their fields
    (number, type, label, map key / value, oneof) and enums with their values."""
    skip = set(skip_files)
    files = {p["file"] for p in grpc["protos"] if p["file"] not in skip}
    services = {}
    for s in grpc["services"]:
        if s["file"] in skip:
            continue
        services[s["name"]] = {
            r["name"]: [
                r["input"],
                r["output"],
                r["client_streaming"],
                r["server_streaming"],
                [r["http"]["method"].lower(), r["http"]["path"], r["http"]["body"]]
                if r.get("http")
                else None,
            ]
            for r in s["rpcs"]
        }
    # messages and enums carry no file in the bridge's answer: select by the files' names
    messages = {}
    for m in grpc["messages"]:
        if m.get("file") in skip:
            continue
        messages[m["name"]] = [
            [
                f["name"],
                f["number"],
                f["type"],
                f["label"],
                f.get("key"),
                f.get("value"),
                f.get("oneof"),
            ]
            for f in m["fields"]
        ]
    enums = {}
    for e in grpc["enums"]:
        if e.get("file") in skip:
            continue
        enums[e["name"]] = [[v["name"], v["number"]] for v in e["values"]]
    return {"files": sorted(files), "services": services, "messages": messages, "enums": enums}


def compare_parse(reader: Mapping[str, Any], bridge: Mapping[str, Any]) -> list[str]:
    """Disagreements between the stdlib reader's ``grpc`` block and the bridge's (protocompile)
    on the constructs the reader supports; elements of the untrusted files are left out of
    both. Returns one line per disagreement."""
    # the bridge's messages carry no file: drop the untrusted files' messages by name
    pkgs = tuple(p["package"] + "." for p in reader["protos"] if p["file"] in UNTRUSTED)
    a = contract_view(reader, UNTRUSTED)
    b = contract_view(bridge)
    out: list[str] = []
    for kind in ("services", "messages", "enums"):
        # the untrusted files' packages (protocompile also builds a message for a group)
        b[kind] = {k: v for k, v in b[kind].items() if not k.startswith(pkgs)}
        for name in sorted(set(a[kind]) | set(b[kind])):
            x, y = a[kind].get(name), b[kind].get(name)
            if x != y:
                out.append(f"{kind[:-1]} {name}: reader {x!r} != protocompile {y!r}")
    return out


# ---- P0-UNREAD / P0-VAL ------------------------------------------------------------------


def compare_unread(extract: Mapping[str, Any], expected: Mapping[str, Any]) -> list[str]:
    """The proto unread items of ``extract`` against ``expected_unread.json``: eight unread
    constructs by kind, file and line, and the two read-and-listed ones (additional
    bindings, client streaming)."""
    out: list[str] = []
    got = {
        (u["what"], u["file"], u["line"])
        for u in extract["unread"]
        if str(u["what"]).startswith("proto_") and u["what"] != "proto_import_unresolved"
    }
    want = {(e["kind"], e["file"], e["line"]) for e in expected["unread"]}
    out += [f"missing unread {w}" for w in sorted(want - got)]
    out += [f"unexpected unread {g}" for g in sorted(got - want)]
    grpc = extract["grpc"]
    rpcs = {f"{s['name']}/{r['name']}": r for s in grpc["services"] for r in s["rpcs"]}
    for item in expected["listed"]:
        r = rpcs.get(item["rpc"])
        if item["kind"] == "additional_bindings":
            extra = (r or {}).get("http", {}).get("additional_bindings") or []
            if len(extra) != item["count"]:
                out.append(f"{item['rpc']}: additional_bindings {len(extra)} != {item['count']}")
        elif item["kind"] == "client_streaming":
            ex = [
                x
                for x in extract["excluded"]
                if x["kind"] == "streaming"
                and x["file"] == item["file"]
                and x["line"] == item["line"]
            ]
            if not ex or not (r or {}).get("client_streaming"):
                where = f"{item['file']}:{item['line']}"
                out.append(f"{item['rpc']}: client streaming is not excluded at {where}")
    # an unread construct adds nothing to its element
    msgs = {m["name"]: {f["name"]: f for f in m["fields"]} for m in grpc["messages"]}
    for field, forbidden in (("t.v1.Odd.a", "bounds"), ("t.v1.Odd.b", "bounds")):
        msg, _, name = field.rpartition(".")
        f = msgs.get(msg, {}).get(name)
        if f is None or f.get(forbidden):
            out.append(f"{field} gained {forbidden} from an unread construct: {f}")
    if "Result" in {f for f in msgs.get("t.v2.Legacy", {})} or "result" in msgs.get(
        "t.v2.Legacy", {}
    ):
        out.append("t.v2.Legacy kept the group as a field")
    return out


def compare_bounds(extract: Mapping[str, Any], expected: Mapping[str, Any]) -> list[str]:
    got = {
        f"{m['name']}.{f['name']}": f.get("bounds")
        for m in extract["grpc"]["messages"]
        for f in m["fields"]
    }
    return [
        f"{k}: bounds {got.get(k)!r} != {v!r}"
        for k, v in expected["bounds"].items()
        if got.get(k) != v
    ]


# ---- P0-EXT: the extractor against the hand-written truth -------------------------------


def _names(codes: Sequence[Mapping[str, Any]] | None) -> list[str]:
    return sorted({c["name"] for c in codes or ()})


def compare_extract(extract: Mapping[str, Any], truth: Mapping[str, Any]) -> list[str]:
    """``extract`` (``gocheck extract`` on the notes app) against ``truth.json``."""
    out: list[str] = []
    grpc = extract["grpc"]
    got = {f"{s['name']}/{r['name']}": r for s in grpc["services"] for r in s["rpcs"]}
    for key, want in truth["rpcs"].items():
        r = got.get(key)
        if r is None:
            out.append(f"{key}: not found")
            continue
        checks = {
            "state": (r.get("state"), want["state"]),
            "auth": (r.get("auth"), want["auth"]),
            "codes": (_names(r.get("codes")), want["codes"]),
            "plain_error": (bool(r.get("plain_error")), want["plain_error"]),
            "tables": (sorted(r.get("tables") or []), want["tables"]),
            "owner_uses": (
                sorted((o["table"], o["column"], o["how"]) for o in r.get("owner_uses") or []),
                sorted((o["table"], o["column"], o["how"]) for o in want["owner_uses"]),
            ),
            "http_bindings": (
                sorted((b["method"], b["path"]) for b in r.get("http_bindings") or []),
                sorted((b["method"], b["path"]) for b in want["http_bindings"]),
            ),
            "unread": (
                sorted(
                    (u["what"], u["file"], u["line"])
                    for u in r.get("unread") or []
                    if u["what"].startswith("grpc")
                ),
                sorted((u["kind"], u["file"], u["line"]) for u in want["unread"]),
            ),
        }
        if want["handler_line"]:
            h = r.get("handler") or {}
            checks["handler"] = (
                (h.get("file"), h.get("line")),
                (want["handler_file"], want["handler_line"]),
            )
        for name, (g, w) in checks.items():
            if g != w:
                out.append(f"{key}: {name} {g!r} != {w!r}")
    extra = sorted(set(got) - set(truth["rpcs"]) - set(truth["excluded"]))
    out += [f"{k}: not in the truth" for k in extra]
    for key, why in truth["excluded"].items():
        r = got.get(key)
        ex = [x for x in extract["excluded"] if x["kind"] == "streaming" and x["path"] == "/" + key]
        if r is None or r.get("state") != "streaming" or not ex or ex[0]["reason"] != why:
            out.append(f"{key}: not excluded as {why}")
    tables = sorted(t["table"] for t in extract["tables"])
    if tables != truth["tables"]:
        out.append(f"tables {tables} != {truth['tables']}")
    writes = sorted((w["value"], w["root"], w["file"], w["line"]) for w in grpc["status_writes"])
    want_w = sorted((w["value"], w["root"], w["file"], w["line"]) for w in truth["status_writes"])
    if writes != want_w:
        out.append(f"status writes {writes} != {want_w}")
    auth = [i for i in grpc["interceptors"] if i["kind"] != "other"]
    pub = sorted(auth[0]["public"]) if len(auth) == 1 else None
    if pub != sorted(truth["interceptor"]["public"]):
        out.append(f"interceptor public set {pub} != {truth['interceptor']['public']}")
    for field, want in truth["bounds"].items():
        msg, _, name = field.rpartition(".")
        f = next(
            (
                x
                for m in grpc["messages"]
                if m["name"] == msg
                for x in m["fields"]
                if x["name"] == name
            ),
            None,
        )
        if f is None or f.get("bounds") != want:
            out.append(f"{field}: bounds {f and f.get('bounds')!r} != {want!r}")
    plain = sorted(k for k, r in got.items() if r.get("plain_error"))
    if plain != truth["plain_error_rpcs"]:
        out.append(f"plain_error rpcs {plain} != {truth['plain_error_rpcs']}")
    nl = sum(1 for u in extract["unread"] if u["what"] == "grpc_code_not_literal")
    if nl != truth["unread_kinds"]["grpc_code_not_literal"]:
        out.append(
            f"grpc_code_not_literal unread {nl} != {truth['unread_kinds']['grpc_code_not_literal']}"
        )
    if extract["routes"]:
        out.append("a gRPC-only module has HTTP routes")
    if extract["status_writes"]:
        out.append(
            f"the HTTP status_writes were not emptied of the rpc writes: {extract['status_writes']}"
        )
    return out
