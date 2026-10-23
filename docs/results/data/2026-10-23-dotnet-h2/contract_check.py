#!/usr/bin/env python3
"""H2 contract check (prereg 3.2), run by the node after the author, never by the author's own judgement.

    python3 contract_check.py [--author DIR] [--out contract-check.json] [--archive]

Works from a CLEAN COPY of the author's delivered tree in a scratch directory (bin, obj and __pycache__ left out), never in
place. Nothing of Lattice runs: the harness is imported with the stand-ins of smoke.py (HARNESS_API_DOTNET.md section 12).
Every check is printed as PASS/FAIL with its detail and written to the output file as
{"pass": bool, "checks": [{"name", "pass", "detail"}, ...]}.

Checks (names are the JSON names):
  1 build         clean-copy `dotnet build -c Release -p:RestoreLockedMode=true`: 0 errors, lock file unchanged by it
  2 smoke         the byte-identical smoke.py of section 12 (extracted from the doc, hashed two ways): no FAIL line,
                  last line `ALL CHECKS PASSED`, exit status 0
  3 route-sweep   the harness started the way the environment does (process runtime), a seed user of each role logged in
                  with the harness's own LOGIN, every truth route called with a minimal body built from the truth's own
                  bounds: neither 404 nor 405
  4 truth-schema  truth.json validates against schema 3.3 and every file:line it cites exists (line within the file)
  5 no-bin-obj    no bin/ or obj/ directory in the delivered tree (the author's scratch/ is not part of it)
  6 counts        10-16 routes; owners >= 2; owner_via >= 1; a never-returned column; a machine with >= 2 edges; >= 1
                  enforced bound; seed users of >= 2 roles; the domain not in the forbidden list

With --archive and a passing check: h2.tgz (tests/ of the delivered tree, deterministic) and h2-truth.json (byte copy) are
written next to this script.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOC = HERE / "brief" / "HARNESS_API_DOTNET.md"
ARCHIVE_MTIME = 1792713600  # 2026-10-23T00:00:00Z, fixed so the archive is reproducible

# prereg 3.1 rule 3: the forbidden subjects (D1: habits, streaks, check-ins, reminders; H1: tickets, runs, jobs, notes,
# expenses, expense reports, reimbursements, payouts). Matched against whole words, singular or plural.
# ("expense report" is covered by "expense", "check-in" by the word pair check + in.)
FORBIDDEN = ["habit", "streak", "reminder", "ticket", "run", "job", "note", "expense", "reimbursement", "payout"]

RESULTS: list[dict] = []


def record(name: str, passed: bool, detail: str) -> bool:
    RESULTS.append({"name": name, "pass": bool(passed), "detail": detail})
    print(f"{'PASS' if passed else 'FAIL'}  {name}")
    for line in detail.splitlines():
        print(f"      {line}")
    return bool(passed)


def toolchain_env(scratch: Path) -> dict[str, str]:
    """Section 2 of the harness doc: everything the toolchain writes goes under the scratch directory."""
    home = scratch / "home"
    tmp = scratch / ".tmp"
    home.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
    env.update({
        "DOTNET_ROOT": "/opt/homebrew/opt/dotnet/libexec", "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1",
        "HOME": str(home), "DOTNET_CLI_HOME": str(home), "NUGET_PACKAGES": str(home / ".nuget" / "packages"),
        "TMPDIR": str(tmp),
    })
    for k in [k for k in env if k.startswith("ASPNETCORE_") or k == "DOTNET_ENVIRONMENT"]:
        del env[k]
    return env


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def find_module(author: Path) -> str:
    cands = sorted(p.parent.name for p in (author / "tests" / "spec").glob("*/truth.json"))
    if len(cands) != 1:
        raise RuntimeError(f"expected exactly one tests/spec/<x>/truth.json, found {cands}")
    return cands[0]


def clean_copy(author: Path, dest: Path) -> None:
    shutil.copytree(author / "tests", dest / "tests",
                    ignore=shutil.ignore_patterns("bin", "obj", "__pycache__"))


# ------------------------------------------------------------------------------------------------ 1 build


def check_build(out_root: Path, name: str, scratch: Path, env: dict[str, str]) -> Path | None:
    # the build runs in its own copy of the clean copy, so out_root (what smoke.py and the sweep see) never gets bin/obj
    build_root = scratch / "build-src"
    shutil.copytree(out_root, build_root)
    module = build_root / "tests" / "spec" / name
    build_out = scratch / "build-out"
    lock = module / "packages.lock.json"
    if not lock.is_file():
        record("build", False, f"{name}/packages.lock.json is missing: RestoreLockedMode cannot hold")
        return None
    before = lock.read_bytes()
    p = subprocess.run(["dotnet", "build", str(module / f"{name}.csproj"), "-c", "Release", "-p:RestoreLockedMode=true",
                        "-o", str(build_out), "--nologo"], cwd=module, env=env, capture_output=True, text=True,
                       timeout=900, check=False)
    text = (p.stdout + p.stderr).strip()
    errs = re.findall(r"^\s*(\d+) Error\(s\)", text, re.M)
    warns = re.findall(r"^\s*(\d+) Warning\(s\)", text, re.M)
    unchanged = lock.read_bytes() == before
    stale = re.findall(r"error NU1004[^\n]*|error NU1403[^\n]*|error NU1005[^\n]*", text)
    passed = p.returncode == 0 and errs[-1:] == ["0"] and unchanged and (build_out / f"{name}.dll").is_file()
    detail = (f"exit {p.returncode}; {errs[-1] if errs else '?'} Error(s), {warns[-1] if warns else '?'} Warning(s); "
              f"packages.lock.json {'unchanged' if unchanged else 'CHANGED by the build (stale)'}")
    if stale:
        detail += "\nlock file problem: " + "; ".join(sorted(set(stale))[:3])
    if not passed:
        detail += "\n" + text[-1500:]
    record("build", passed, detail)
    return build_out if passed else None


# ------------------------------------------------------------------------------------------------ 2 smoke


def extract_smoke() -> tuple[str, str, str]:
    """The smoke.py block of section 12, extracted two independent ways; answers (text, sha_line_scan, sha_regex)."""
    doc = DOC.read_text()
    lines = doc.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("## 12."))
    fence = next(i for i in range(start, len(lines)) if lines[i].startswith("```python"))
    end = next(i for i in range(fence + 1, len(lines)) if lines[i] == "```")
    text = "\n".join(lines[fence + 1:end]) + "\n"
    sec = doc[doc.index("\n## 12."):]
    m = re.search(r"```python\n(.*?)\n```\n", sec, re.S)
    assert m, "no python block in section 12"
    return text, sha256_bytes(text.encode()), sha256_bytes((m.group(1) + "\n").encode())


def check_smoke(out_root: Path, name: str, scratch: Path, env: dict[str, str], author: Path) -> None:
    text, h1, h2 = extract_smoke()
    smoke = scratch / "smoke.py"
    smoke.write_bytes(text.encode())
    same = h1 == h2 == sha256_bytes(smoke.read_bytes())
    author_copy = author / "scratch" / "smoke.py"
    note = ""
    if author_copy.is_file():
        note = f"; the author's own copy {'is identical' if author_copy.read_bytes() == text.encode() else 'DIFFERS'}"
    if not same:
        record("smoke", False, f"extracted smoke.py does not hash the same two ways: {h1} {h2}")
        return
    p = subprocess.run([sys.executable, str(smoke), str(out_root), name], cwd=scratch, env=env, capture_output=True,
                       text=True, timeout=1800, check=False)
    lines = [ln for ln in (p.stdout + p.stderr).splitlines() if ln.strip()]
    fails = [ln for ln in lines if ln.startswith("FAIL")]
    warns = [ln for ln in lines if ln.startswith(("WARN", "SKIP"))]
    npass = sum(ln.startswith("PASS") for ln in lines)
    other = [ln for ln in lines if not ln.startswith(("PASS", "FAIL", "WARN", "SKIP", "ALL CHECKS PASSED"))]
    last = lines[-1] if lines else ""
    passed = p.returncode == 0 and not fails and last == "ALL CHECKS PASSED" and not other
    detail = (f"smoke.py sha256 {h1}{note}; exit {p.returncode}; {npass} PASS lines, {len(fails)} FAIL, {len(warns)} "
              f"WARN/SKIP; last line {last!r}")
    if warns:
        detail += "\n" + "\n".join(warns[:6])
    if fails or other:
        detail += "\n" + "\n".join((fails + other)[:10])
    record("smoke", passed, detail)
    (scratch / "smoke-output.txt").write_text("\n".join(lines) + "\n")


# ------------------------------------------------------------------------------------------------ 3 route sweep


def load_smoke_module(scratch: Path):
    spec = importlib.util.spec_from_file_location("smoke_ref", scratch / "smoke.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["smoke_ref"] = mod
    spec.loader.exec_module(mod)
    return mod


def minimal_body(route: dict, bounds: list[dict], login: dict, creds: tuple[str, str]) -> dict:
    """A valid-ish JSON body: the route's required fields, numbers inside their bounds, other values a short string."""
    if route["template"] == login["path"]:
        return dict(zip(login["fields"], creds))
    body: dict = {}
    for b in bounds:
        if b["route"] != f'{route["method"]} {route["template"]}' or not b.get("required"):
            continue
        ge, le = b.get("ge"), b.get("le")
        if isinstance(ge, (int, float)) and isinstance(le, (int, float)):
            body[b["field"]] = (ge + le) // 2 if isinstance(ge, int) and isinstance(le, int) else (ge + le) / 2
        elif isinstance(ge, (int, float)):
            body[b["field"]] = ge
        elif isinstance(le, (int, float)):
            body[b["field"]] = le
        else:
            body[b["field"]] = "x" * max(1, int(b.get("min_length") or 1))
    return body


def check_sweep(out_root: Path, name: str, scratch: Path, env: dict[str, str], build_out: Path | None) -> None:
    if build_out is None:
        record("route-sweep", False, "not run: the build failed")
        return
    srv = None
    try:
        sm = load_smoke_module(scratch)
        sm.install_stand_ins()
        module = out_root / "tests" / "spec" / name
        hspec = importlib.util.spec_from_file_location(f"tests.spec.{name}_harness",
                                                      out_root / "tests" / "spec" / f"{name}_harness.py")
        h = importlib.util.module_from_spec(hspec)
        sys.modules[hspec.name] = h
        os.environ[f"{name}_OUT"] = str(build_out)  # the harness's own hook: use the clean build of check 1
        hspec.loader.exec_module(h)
        truth = json.loads((module / "truth.json").read_text())
        db = scratch / "sweep" / "seeded.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        h.build_db(db)
        home, tmp = scratch / "sweep-home", scratch / "sweep-tmp"
        home.mkdir(exist_ok=True)
        tmp.mkdir(exist_ok=True)
        confine = {"HOME": str(home), "DOTNET_CLI_HOME": str(home), "TMPDIR": str(tmp)}
        srv = sm.Server(h.make_app(db), confine)
        base = srv.start()
        client = sm.Client(base)
        roles = {u["name"]: u["role"] for u in truth["seed_users"]}
        token_by_role: dict[str, tuple[str, str]] = {}  # role -> (token, password)
        creds_by_role: dict[str, tuple[str, str]] = {}
        for handle, (login_name, pw) in h.USERS.items():
            role = roles.get(login_name)
            if role and role not in token_by_role:
                token_by_role[role] = (h.LOGIN(client, handle), pw)
                creds_by_role[role] = (login_name, pw)
        order = {"GET": 0, "POST": 1, "PUT": 1, "PATCH": 1, "DELETE": 2}
        bad, rows = [], []
        for r in sorted(truth["routes"], key=lambda r: order.get(r["method"], 1)):
            url = base + re.sub(r"\{[^}]*\}", "1", r["template"])
            token = None if r["auth"] == "Public" else token_by_role.get(r["auth"], (None, None))[0]
            if r["auth"] != "Public" and token is None:
                bad.append(f'{r["method"]} {r["template"]}: no seed user of role {r["auth"]}')
                continue
            body = None
            if r["method"] in ("POST", "PUT", "PATCH"):
                creds = creds_by_role[next(iter(creds_by_role))]
                body = minimal_body(r, truth["bounds"], truth["login"], creds)
            resp = sm.http(r["method"], url, token=token, body=body)
            rows.append(f'{resp.status_code} {r["method"]} {r["template"]} as {r["auth"]}')
            if resp.status_code in (404, 405):
                bad.append(f'{r["method"]} {r["template"]} answered {resp.status_code}')
        record("route-sweep", not bad and len(rows) == len(truth["routes"]),
               f"{len(rows)} of {len(truth['routes'])} routes called, none 404/405\n" + "\n".join(rows)
               + ("\nFAILURES: " + "; ".join(bad) if bad else ""))
    except Exception:  # noqa: BLE001
        record("route-sweep", False, "exception:\n" + traceback.format_exc()[-1500:])
    finally:
        if srv is not None:
            srv.stop()


# ------------------------------------------------------------------------------------------------ 4 truth schema


def file_line_ok(module: Path, file: object, line: object) -> bool:
    if not isinstance(file, str) or not isinstance(line, int) or isinstance(line, bool):
        return False
    p = (module / file).resolve()
    if module.resolve() not in p.parents or not p.is_file():
        return False
    return 1 <= line <= len(p.read_text(errors="replace").splitlines())


def validate_truth(t: dict, module: Path) -> list[str]:
    errs: list[str] = []
    E = errs.append
    is_str = lambda v: isinstance(v, str) and v != ""  # noqa: E731
    is_int = lambda v: isinstance(v, int) and not isinstance(v, bool)  # noqa: E731
    num_or_none = lambda v: v is None or (isinstance(v, (int, float)) and not isinstance(v, bool)) or is_str(v)  # noqa: E731

    for k in ("routes", "tables", "owners", "owner_via", "machines", "refusals", "bounds", "bind_status", "json_naming",
              "login", "seed_users", "not_returned"):
        if k not in t:
            E(f"missing key {k}")
    if errs:
        return errs

    def cite(where: str, o: dict) -> None:
        if not file_line_ok(module, o.get("file"), o.get("line")):
            E(f'{where}: file:line {o.get("file")}:{o.get("line")} does not exist')

    routes = t["routes"]
    keys = set()
    for i, r in enumerate(routes):
        w = f"routes[{i}]"
        if not (isinstance(r, dict) and r.get("method") in {"GET", "POST", "PUT", "PATCH", "DELETE"}
                and is_str(r.get("template")) and r["template"].startswith("/")
                and r.get("auth") in ("Public", "Member", "Admin") and r.get("style") in ("controller", "minimal")):
            E(f"{w}: bad method/template/auth/style: {r}")
            continue
        cite(w, r)
        keys.add(f'{r["method"]} {r["template"]}')
    if len(keys) != len(routes):
        E("routes are not distinct")

    ents: dict[str, dict[str, dict]] = {}
    for i, tb in enumerate(t["tables"]):
        if not (isinstance(tb, dict) and is_str(tb.get("entity")) and is_str(tb.get("table"))
                and isinstance(tb.get("columns"), list) and tb["columns"]):
            E(f"tables[{i}]: bad entity/table/columns")
            continue
        cols = {}
        for j, c in enumerate(tb["columns"]):
            w = f'tables[{i}].columns[{j}]'
            if not (isinstance(c, dict) and is_str(c.get("name")) and (c.get("wire") is None or is_str(c["wire"]))
                    and all(isinstance(c.get(b), bool) for b in ("private", "nullable", "unique", "pk"))
                    and (c.get("max_length") is None or is_int(c["max_length"]))
                    and (c.get("fk") is None or is_str(c["fk"]))):
                E(f"{w}: bad column {c}")
                continue
            cite(w, c)
            cols[c["name"]] = c
        ents[tb["entity"]] = cols

    for ent, col in t["owners"].items():
        if not (is_str(ent) and is_str(col) and col in ents.get(ent, {})):
            E(f"owners {ent}:{col}: entity or column not in tables")
    for ent, v in t["owner_via"].items():
        if not (isinstance(v, list) and len(v) == 2 and v[0] in ents and v[1] in ents.get(ent, {})):
            E(f"owner_via {ent}: {v} is not [parent_entity, fk_column] of known tables")
    for key, m in t["machines"].items():
        e, _, c = key.partition(".")
        if not (e in ents and c in ents[e]):
            E(f"machines {key}: entity.column not in tables")
        if not (isinstance(m, dict) and is_str(m.get("initial")) and m.get("storage") in ("string", "int")
                and isinstance(m.get("edges"), list)
                and all(isinstance(x, list) and len(x) == 2 and all(is_str(y) for y in x) for x in m["edges"])):
            E(f"machines {key}: bad initial/edges/storage")
        elif "file" in m or "line" in m:
            cite(f"machines {key}", m)
    for i, r in enumerate(t["refusals"]):
        w = f"refusals[{i}]"
        if not (isinstance(r, dict) and r.get("route") in keys and is_int(r.get("status")) and is_str(r.get("detail"))
                and r.get("cause") in ("auth", "not_found", "not_owned", "invalid", "conflict", "other")):
            E(f"{w}: bad route/status/detail/cause: {r}")
            continue
        cite(w, r)
    for i, b in enumerate(t["bounds"]):
        w = f"bounds[{i}]"
        if not (isinstance(b, dict) and b.get("route") in keys and is_str(b.get("field")) and isinstance(b.get("required"), bool)
                and all(k in b for k in ("ge", "le", "min_length", "max_length"))
                and num_or_none(b["ge"]) and num_or_none(b["le"]) and num_or_none(b["min_length"])
                and num_or_none(b["max_length"]) and is_str(b.get("enforced_by"))):
            E(f"{w}: bad bound: {b}")
            continue
        cite(w, b)
    if not is_int(t["bind_status"]):
        E("bind_status is not an int")
    if not is_str(t["json_naming"]):
        E("json_naming is not a string")
    lg = t["login"]
    if not (isinstance(lg, dict) and f'POST {lg.get("path")}' in keys and isinstance(lg.get("fields"), list)
            and lg["fields"] and all(is_str(f) for f in lg["fields"]) and is_str(lg.get("token_field"))):
        E(f"login: bad path/fields/token_field: {lg}")
    for i, u in enumerate(t["seed_users"]):
        if not (isinstance(u, dict) and is_str(u.get("name")) and u.get("role") in ("Member", "Admin")):
            E(f"seed_users[{i}]: {u}")
    for i, n in enumerate(t["not_returned"]):
        if not (isinstance(n, dict) and n.get("entity") in ents and n.get("column") in ents[n["entity"]]):
            E(f"not_returned[{i}]: {n} is not a known entity/column")
    return errs


def check_truth(module: Path) -> dict | None:
    try:
        t = json.loads((module / "truth.json").read_text())
    except Exception as err:  # noqa: BLE001
        record("truth-schema", False, f"truth.json unreadable: {err!r}")
        return None
    errs = validate_truth(t, module)
    n_cites = sum(1 for _ in _cites(t))
    record("truth-schema", not errs, f"{n_cites} file:line citations checked against the module files"
           + ("\n" + "\n".join(errs[:20]) if errs else "; all exist, schema 3.3 holds"))
    return t


def _cites(t: dict):
    for r in t["routes"]:
        yield r
    for tb in t["tables"]:
        yield from tb["columns"]
    for k in ("refusals", "bounds"):
        yield from t[k]
    for m in t["machines"].values():
        if "file" in m:
            yield m


# ------------------------------------------------------------------------------------------------ 5, 6


def check_no_bin_obj(author: Path) -> None:
    bad = []
    for root, dirs, _files in os.walk(author):
        rel = Path(root).relative_to(author)
        if rel.parts[:1] == ("scratch",):
            dirs[:] = []
            continue
        for d in dirs:
            if d in ("bin", "obj"):
                bad.append(str(rel / d))
    record("no-bin-obj", not bad, "no bin/ or obj/ directory in the delivered tree (author's scratch/ excluded)"
           if not bad else "found: " + ", ".join(bad))


def words(s: str) -> set[str]:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    return {w for w in re.split(r"[^a-z]+", s.lower()) if w}


def check_counts(t: dict | None) -> None:
    if t is None:
        record("counts", False, "not run: truth.json unreadable")
        return
    problems, facts = [], []

    def need(label: str, cond: bool, value: str) -> None:
        facts.append(f"{label}: {value} -> {'ok' if cond else 'FAIL'}")
        if not cond:
            problems.append(label)

    need("routes 10-16", 10 <= len(t["routes"]) <= 16, str(len(t["routes"])))
    need("owners >= 2 entries", len(t["owners"]) >= 2, str(sorted(t["owners"])))
    need("owner_via >= 1 entry", len(t["owner_via"]) >= 1, str(sorted(t["owner_via"])))
    private = [(tb["entity"], c["name"]) for tb in t["tables"] for c in tb["columns"] if c["private"]]
    nr = [(n["entity"], n["column"]) for n in t["not_returned"]]
    need("never-returned column (not_returned or private)", bool(nr or private), f"not_returned {nr}, private {private}")
    best = max(((k, len(m["edges"])) for k, m in t["machines"].items()), key=lambda x: x[1], default=("none", 0))
    need("a machine with >= 2 edges", best[1] >= 2, f"{best[0]} has {best[1]} edges")
    enforced = [b for b in t["bounds"] if b.get("enforced_by")]
    need(">= 1 enforced bound", len(enforced) >= 1, f"{len(enforced)} of {len(t['bounds'])} bounds name an enforcer")
    roles = sorted({u["role"] for u in t["seed_users"]})
    need("seed users of >= 2 roles", len(roles) >= 2, f"{roles} over {len(t['seed_users'])} users")
    # the domain: the words of every entity, table, column, wire name and route segment (plus a `domain` key if any)
    bag: set[str] = set()
    for tb in t["tables"]:
        bag |= words(tb["entity"]) | words(tb["table"])
        for c in tb["columns"]:
            bag |= words(c["name"]) | words(c["wire"] or "")
    for r in t["routes"]:
        bag |= words(r["template"])
    bag |= words(str(t.get("domain", ""))) | words(str(t.get("service", "")))
    stems = {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in bag}
    hits = sorted(f for f in FORBIDDEN if f in stems)
    if {"check", "in"} <= bag or "checkin" in stems:
        hits.append("check-in")
    need("domain not in the forbidden list", not hits,
         f"forbidden hits {sorted(set(hits))}; entities {sorted(tb['entity'] for tb in t['tables'])}; "
         f"words checked: {len(bag)}")
    record("counts", not problems, "\n".join(facts))


# ------------------------------------------------------------------------------------------------ archive


def write_archive(author: Path, name: str, out_dir: Path) -> tuple[str, str]:
    """tests/ of the delivered tree as a deterministic tar.gz in the layout of 2026-10-18-dotnet/h1.tgz (directories
    first-class entries, tests/ at the root, no briefs, no scratch, no bin/obj/__pycache__)."""
    src = author / "tests"
    entries: list[Path] = [src]
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in ("bin", "obj", "__pycache__"))
        for d in dirs:
            entries.append(Path(root) / d)
        for f in sorted(files):
            if not f.endswith(".pyc") and f != ".DS_Store":
                entries.append(Path(root) / f)
    entries.sort(key=lambda p: p.relative_to(author).as_posix())
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, compresslevel=9) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tf:
            for p in entries:
                ti = tarfile.TarInfo(p.relative_to(author).as_posix() + ("/" if p.is_dir() else ""))
                ti.mtime, ti.uid, ti.gid, ti.uname, ti.gname = ARCHIVE_MTIME, 0, 0, "", ""
                if p.is_dir():
                    ti.type, ti.mode, ti.size = tarfile.DIRTYPE, 0o755, 0
                    tf.addfile(ti)
                else:
                    data = p.read_bytes()
                    ti.type, ti.mode, ti.size = tarfile.REGTYPE, 0o644, len(data)
                    tf.addfile(ti, io.BytesIO(data))
    tgz = out_dir / "h2.tgz"
    tgz.write_bytes(buf.getvalue())
    truth_copy = out_dir / "h2-truth.json"
    shutil.copyfile(author / "tests" / "spec" / name / "truth.json", truth_copy)
    return sha256_bytes(tgz.read_bytes()), sha256_bytes(truth_copy.read_bytes())


# ------------------------------------------------------------------------------------------------ main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--author", default=str(Path.home() / "Documents" / "dn-h2-author"))
    ap.add_argument("--out", default=str(HERE / "author" / "contract-check.json"))
    ap.add_argument("--archive", action="store_true")
    args = ap.parse_args()
    author = Path(args.author).resolve()
    scratch = Path(tempfile.mkdtemp(prefix="h2-contract-"))
    env = toolchain_env(scratch)
    os.environ["TMPDIR"] = env["TMPDIR"]
    tempfile.tempdir = env["TMPDIR"]
    name = find_module(author)
    out_root = scratch / "OUT"
    out_root.mkdir()
    clean_copy(author, out_root)
    print(f"author tree {author}\nmodule {name}\nclean copy {out_root}\n")

    build_out = check_build(out_root, name, scratch, env)
    check_smoke(out_root, name, scratch, env, author)
    check_sweep(out_root, name, scratch, env, build_out)
    truth = check_truth(out_root / "tests" / "spec" / name)
    check_no_bin_obj(author)
    check_counts(truth)

    passed = all(r["pass"] for r in RESULTS)
    result = {"pass": passed, "checks": RESULTS}
    if passed and args.archive:
        tgz_sha, truth_sha = write_archive(author, name, HERE)
        result["h2.tgz sha256"] = tgz_sha
        result["h2-truth.json sha256"] = truth_sha
        print(f"\nh2.tgz sha256 {tgz_sha}\nh2-truth.json sha256 {truth_sha}")
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    print(f"\nCONTRACT CHECK {'PASSED' if passed else 'FAILED'} -> {args.out}")
    shutil.rmtree(scratch, ignore_errors=True)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
