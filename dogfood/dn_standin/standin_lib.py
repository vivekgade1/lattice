"""Stand-ins for the code of nodes B2, B3 and B4 (the self-test of dogfood/dotnet_probes.py, until the orchestrator merges those nodes).

They implement the ``Lib`` interface with the same return shapes as ``RealLib``, from the real extract of B1 and the truths, so the row
functions run their whole code path (scoring included). Nothing here is a result: a stand-in answers what the truth says, and says so in
``standin_lib.STANDIN_NOTE``. A node that IS in the tree is not stood in for: its methods go to the real library.
"""

# ruff: noqa: E501
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
STANDIN_NOTE = "answered by dogfood/dn_standin/standin_lib.py, not by the library"


def node(name: str):
    """Mark a method as the stand-in of a node's code: used when that node is not merged, else the real method runs."""

    def deco(fn):
        fn.node = name
        return fn

    return deco


class StandInLib:
    def __init__(self, real_cls: type, nodes: set[str]) -> None:
        self.real = real_cls()
        self.nodes = set(nodes)

    def __getattr__(self, name: str):  # the real method (unless a stand-in overrides it below)
        return getattr(self.real, name)

    def _use(self, fn_name: str) -> bool:
        return getattr(type(self), fn_name).node in self.nodes

    # ---- helpers --------------------------------------------------------------------------------------------------
    @staticmethod
    def _extract(root: Path) -> dict[str, Any]:
        from lattice import dncheck

        return dncheck.extract_json(root, policy=True)

    @staticmethod
    def _dm_of(copy: Path) -> str | None:
        """Which D1 dev patch a scratch copy carries: the marker the mutant runners write beside the copy is the patch's sha256."""
        import hashlib

        marker = Path(copy).parent / "PATCH"
        if not marker.is_file():
            return None
        for i in range(1, 7):
            f = REPO / f"docs/results/data/2026-10-18-dotnet/d1-mutants/dm{i}.diff"
            if hashlib.sha256(f.read_bytes()).hexdigest() == marker.read_text().strip():
                return f"DM{i}"
        return None

    @staticmethod
    def _truth_for(ctx: Any, root: Path) -> dict[str, Any]:
        for t in ctx.targets():
            if Path(t["module_dir"]).resolve() == Path(root).resolve() or str(root).startswith(
                str(t["module_dir"])
            ):
                return t["truth"]
        return json.loads((REPO / "tests/dncheck/d1/truth.json").read_text())

    # ---- B2 ---------------------------------------------------------------------------------------------------------
    @node("B2")
    def select_names(self, ctx, cases, tree):
        if not self._use("select_names") or tree != REPO:
            return self.real.select_names(ctx, cases, tree)
        out = {}
        for c in cases:
            r = Path(c["root"]) / c["package"]
            if not r.is_absolute():
                out[c["id"]] = self.real.select_names(ctx, [c], tree)[c["id"]]
                continue
            projs = sorted(r.glob("*.csproj"))
            if len(projs) != 1:
                out[c["id"]] = (
                    f"LookupError: {len(projs)} project files here: {[p.name for p in projs]} (pass the project directory)"
                )
                continue
            text = projs[0].read_text()
            out[c["id"]] = (
                "aspnetcore"
                if "EntityFrameworkCore" in text or "Pomelo" in text
                else "LookupError: no front end recognises the package (no EF Core)"
            )
        return out

    @node("B2")
    def model_view(self, ctx, tgt):
        if not self._use("model_view"):
            return self.real.model_view(ctx, tgt)
        ex = self._extract(Path(tgt["module_dir"]))
        a = ex["auth"]
        return {"framework": "aspnetcore", "orm": "efcore", "routes": len(ex["routes"]), "tables": len(ex["tables"]), "models": sorted(m["name"] for m in ex["models"]) or ["Body"], "login_path": a.get("login_path"), "username_field": a.get("username_field"), "password_field": a.get("password_field"), "token_path": [a.get("token_field")]}  # fmt: skip

    @node("B2")
    def runtime_view(self, ctx, tgt):
        if not self._use("runtime_view"):
            return self.real.runtime_view(ctx, tgt)
        return {
            "with_harness": {"framework": "dotnet", "is_harness_runtime": True, "one_server": True},
            "without_harness": {
                "framework": "dotnet",
                "one_server": True,
                "auth": "AuthDriver(json)",
            },
        }

    @node("B2")
    def reconcile(self, ctx, tgt):
        if not self._use("reconcile"):
            return self.real.reconcile(ctx, tgt)
        ex = self._extract(Path(tgt["module_dir"]))
        return {
            "routes": {
                f"{r['method']} {r['path']}": {"status": 401, "control": 404, "observed": True}
                for r in ex["routes"]
            },
            "containers_left": 0,
        }

    @node("B2")
    def import_rules(self, ctx, tgt, out_dir, *, root=None):
        if not self._use("import_rules"):
            return self.real.import_rules(ctx, tgt, out_dir, root=root)
        shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True)
        mod = Path(root or tgt["module_dir"])
        ex = self._extract(mod)
        t = tgt["truth"]
        mysql = (
            'database = "mysql"' in (mod / "studio.toml").read_text()
            if (mod / "studio.toml").is_file()
            else False
        )
        items = {} if mysql else self._items(ex, t)
        inf = [
            {"rule": "owner_uses", "table": e, "column": c, "evidence": []}
            for e, c in t.get("owners", {}).items()
        ]
        inf += [
            {"rule": "json_ignore", "table": n["entity"], "column": n["column"]}
            for n in t.get("not_returned", ())
        ]
        inf += [
            {"rule": "enum_choices", "table": "Nudge", "column": "delivery"},
            {"rule": "bind_status", "status": t.get("bind_status")},
            {"rule": "json_naming", "policy": ex["dotnet"]["json_naming"]},
        ]
        inf += [
            {"rule": "route_constraint", "param": p["name"]}
            for r in ex["routes"]
            for p in r["params"]
            if p.get("parse")
        ]
        rules = {
            "framework": "aspnetcore",
            "status": "not_run:server_dialect" if mysql else "ok",
            "dialect": "mysql" if mysql else "sqlite",
            "items": items,
            "corrections": [],
            "baseline": [],
            "inferences": inf,
            "timing": {},
        }
        (out_dir / "report.json").write_text(
            json.dumps(
                {"cost": {"wall_s": 0.0}, "repo": str(mod), "rules": rules},
                indent=2,
                sort_keys=True,
            )
        )
        contracts = [(r["method"], r["path"], r["auth"]) for r in ex["routes"]]
        (out_dir / "candidate_spec.py").write_text(
            "from types import SimpleNamespace as N\n"
            f"CONTRACTS = [N(method=m, path=p, auth=a) for m, p, a in {contracts!r}]\n"
            f"PROFILE = N(api=N(table_names={{t: t for t in {[x['table'] for x in ex['tables']]!r}}}))\nSPEC = None\n"
        )
        props = [
            {
                "kind": "machine",
                "spec_object": {
                    "table": "nudges",
                    "column": "delivery",
                    "edges": [
                        list(e) for e in t["machines"].get("Nudge.delivery", {"edges": []})["edges"]
                    ]
                    if t.get("machines")
                    else [],
                },
            }
        ]
        (out_dir / "proposals.json").write_text(json.dumps(props))
        return {"rc": 0, "stdout": "", "stderr": "", "wall_s": 0.5, "dir": out_dir}

    @staticmethod
    def _items(ex, t):
        items = {}
        for r in ex["routes"]:
            items[f"R1 {r['method']} {r['path']}"] = {"verdict": "verified"}
            for s in sorted({x["status"] for x in r["raises"]}):
                items[f"R3 {r['method']} {r['path']} {s}"] = {"verdict": "verified"}
        for n in t.get("not_returned", ()):
            items[f"T2 {n['entity']}.{n['column']}"] = {"verdict": "verified"}
        items["T1 unexercised"] = {"verdict": "unexercised", "reason": "stand-in"}
        return items

    @node("B2")
    def r_verdicts(self, ctx, tgt, cand_dir, variant, *, spec=None):
        if not self._use("r_verdicts"):
            return self.real.r_verdicts(ctx, tgt, cand_dir, variant, spec=spec)
        import os

        rep = json.loads((cand_dir / "report.json").read_text())
        v = {i: x["verdict"] for i, x in rep["rules"]["items"].items()}
        dm = (
            self._dm_of(Path(env_dir).parent / "x")
            if (env_dir := os.environ.get(tgt.get("build_env") or "_", ""))
            else None
        )
        if dm == "DM4":
            v["R1 POST /v1/auth/logout"] = "refuted"
        if dm == "DM5":
            for i in v:
                if i.startswith("R3 GET /v1/habits/{id} 404"):
                    v[i] = "refuted"
        return v

    @node("B2")
    def review_list(self, ctx, tgt, import_dir):
        if not self._use("review_list"):
            return self.real.review_list(ctx, tgt, import_dir)
        rep = json.loads((import_dir / "report.json").read_text())
        return {
            "rc": 0,
            "ids": sorted(
                i
                for i, x in rep["rules"]["items"].items()
                if x["verdict"] not in ("verified", "verified (corrected)")
            ),
            "stderr": "",
        }

    @node("B2")
    def import_without_sdk(self, ctx, tgt, out_dir, path):
        if not self._use("import_without_sdk"):
            return self.real.import_without_sdk(ctx, tgt, out_dir, path)
        return {
            "rc": 1,
            "stderr": "DotnetToolchainMissing: dncheck needs the .NET SDK: `dotnet` is not on PATH",
            "stdout": "",
        }

    @node("B4")
    def dnapp_check(self, ctx, layers, bug=""):
        if not self._use("dnapp_check"):
            return self.real.dnapp_check(ctx, layers, bug)
        vm = {
            "R1 GET /runs/{rid}": "verified",
            "R3 GET /runs/{rid} 404": "verified",
            "R2 GET /runs": "verified",
            "T1 Run": "unexercised",
        } | {f"R9 x{i}": ("verified" if i < 12 else "unexercised") for i in range(19)}
        refuted: list[str] = []
        findings: list[str] = []
        eps: list[str] = []
        if layers == "R" and bug in ("jm1", "jm2", "noauth", "forbidden"):
            vm["R1 GET /runs/{rid}" if bug in ("jm1", "noauth") else "R3 GET /runs/{rid} 404"] = (
                "refuted"
            )
            refuted = [i for i, v in vm.items() if v == "refuted"]
        if layers == "B" and bug in ("nocheck", "nocancel"):
            ep = {"nocheck": "POST /configs/{cid}/runs", "nocancel": "POST /runs/{rid}/cancel"}[bug]
            findings, eps = [f"B|{ep}|case"], [ep]
        n = len(vm)
        return {
            "items": n,
            "verdicts": {},
            "verified_share": round(sum(v == "verified" for v in vm.values()) / n, 4),
            "refuted": refuted,
            "verdict_map": vm,
            "findings": findings,
            "endpoints": eps,
            "cases": 12,
        }

    # ---- B3 ---------------------------------------------------------------------------------------------------------
    @node("B3")
    def layer_a(self, ctx, root, kind, *, spec=None, truth_policy=None):
        if not self._use("layer_a"):
            return self.real.layer_a(ctx, root, kind, spec=spec, truth_policy=truth_policy)
        root = Path(root)
        if root.name.endswith("bad"):
            return {
                "findings": [
                    {
                        "code": "DN800",
                        "key": "DN800|parse|x",
                        "file": "Broken.cs",
                        "line": 1,
                        "message": "does not parse",
                    }
                ],
                "baseline": [],
            }
        if (
            kind == "truth"
            and root.resolve() == (REPO / "tests/dncheck/fixtures/dnparity1").resolve()
        ):
            truth = json.loads((root / "truth.json").read_text())
            return {
                "findings": [
                    {
                        "code": f["code"],
                        "key": f["key"] or f"{f['code']}|{f['route']}|{f['subject']}",
                        "file": f["file"],
                        "line": f["line"],
                        "message": "stand-in",
                    }
                    for f in truth["layer_a"]["findings"]
                ],
                "baseline": [],
            }
        found = []
        dm = self._dm_of(root)
        spec_of = {
            "DM1": ("DN801", "GET /v1/habits/{id}", "Habit"),
            "DM2": ("DN801", "PATCH /v1/habits/{id}", "Habit"),
            "DM3": ("DN802", "GET /v1/me", "User.hashed_password"),
        }
        if dm in spec_of:
            c, r, s = spec_of[dm]
            found = [
                {"code": c, "key": f"{c}|{r}|{s}", "file": "x.cs", "line": 1, "message": "stand-in"}
            ]
        if kind == "truth" and truth_policy is not None and not found:
            found = []
        return {"findings": found, "baseline": []}

    @node("B3")
    def gated(self, ctx):
        if not self._use("gated"):
            return self.real.gated(ctx)
        g = {"DN804": "endpoint routing orders by template precedence"}
        return {
            "gated": g,
            "note": "layer A (dncheck): not available for .NET: "
            + "; ".join(f"{k} -- {v}" for k, v in g.items()),
            "codes": [f"DN80{i}" for i in range(9)],
        }

    @node("B3")
    def derive(self, ctx, root):
        if not self._use("derive"):
            return self.real.derive(ctx, root)
        root = Path(root)
        if root.resolve() == (REPO / "tests/dncheck/fixtures/dnparity1").resolve():
            p = json.loads((root / "truth.json").read_text())["layer_a"]["policy"]
            return {
                "owners": p["owners"],
                "owner_via": p["owner_via"],
                "private": {k: sorted(v) for k, v in p["private"].items()},
                "baseline": [],
                "users_table": p["users_table"],
                "tables": p["tables"],
            }
        t = self._truth_for(ctx, root)
        priv: dict[str, list[str]] = {}
        for tb in t["tables"]:
            cols = [c["name"] for c in tb["columns"] if c.get("private")]
            if cols:
                priv[tb["entity"]] = sorted(cols)
        for n in t.get("not_returned", ()):
            priv.setdefault(n["entity"], [])
            if n["column"] not in priv[n["entity"]]:
                priv[n["entity"]] = sorted([*priv[n["entity"]], n["column"]])
        return {
            "owners": dict(t.get("owners", {})),
            "owner_via": {k: list(v) for k, v in t.get("owner_via", {}).items()},
            "private": priv,
            "baseline": [],
            "users_table": "User",
            "tables": {},
        }

    @node("B3")
    def judged(self, ctx, root, truth_policy):
        if not self._use("judged"):
            return self.real.judged(ctx, root, truth_policy)
        t = self._truth_for(ctx, Path(root))
        ids = sorted(
            {
                r["route"]
                for r in t.get("refusals", ())
                if r.get("cause") in ("not_owned", "not_found")
            }
        )
        return {"method": "stand-in", "judged": ids}

    @node("B3")
    def layer_a_cli(self, ctx, tgt, proj, *, no_sdk=False):
        if not self._use("layer_a_cli"):
            return self.real.layer_a_cli(ctx, tgt, proj, no_sdk=no_sdk)
        note = (
            "layer A (dncheck): the .NET SDK (dotnet) is not on PATH: layer A skipped"
            if no_sdk
            else "layer A (dncheck): 0 findings"
        )
        return {
            "rc": 0,
            "stderr": "",
            "json": {"findings": [], "layers": "A", "notes": [note]},
            "stdout": note,
            "wall_s": 0.1,
        }

    @node("B3")
    def not_owned(self, ctx, root):
        if not self._use("not_owned"):
            return self.real.not_owned(ctx, root)
        if "d1-403" in Path(root).as_posix():
            return {"status": 404, "findings": ["DN805|GET /v1/habits/{id}|not_owned"]}
        return {"status": 404, "findings": []}

    @node("B3")
    def check_run(self, ctx, tgt, proj, layers, database="mysql"):
        if not self._use("check_run"):
            return self.real.check_run(ctx, tgt, proj, layers, database)
        f = {
            "layer": "D",
            "endpoint": "POST /v1/auth/refresh",
            "case": "access_token_expires_after_15_minutes",
        }
        return {
            "rc": 1,
            "stderr": "",
            "wall_s": 0.1,
            "json": {
                "verdict": "fail",
                "layers": layers,
                "counts": {"A": 0, "D": 1},
                "findings": [f],
                "cases": 97,
                "notes": [],
            },
        }

    # ---- B4 ---------------------------------------------------------------------------------------------------------
    @node("B4")
    def workflows(self, ctx, tgt, mode, out_dir):
        if not self._use("workflows"):
            return self.real.workflows(ctx, tgt, mode, out_dir)
        shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True)
        ex = self._extract(Path(tgt["module_dir"]))
        keys = [f"{r['method']} {r['path']}" for r in ex["routes"]]
        skeletons = [
            ["POST /v1/auth/register", "POST /v1/auth/login"],
            [
                "POST /v1/habits",
                "POST /v1/habits/{habit_id}/completions",
                "GET /v1/habits/{id}/progress",
            ],
            ["POST /v1/completions/batch"],
            ["POST /v1/devices"],
        ]
        flows = [
            {
                "id": f"wf-{i}",
                "skeleton": s,
                "kind": "pair",
                "status": "static" if mode == "static" else "verified",
                "steps": [{"route": k} for k in s],
            }
            for i, s in enumerate(skeletons)
            if all(k in keys for k in s)
        ]
        if not flows:  # a target that is not Streak: one flow per POST route with its GET
            flows = [
                {
                    "id": f"wf-{i}",
                    "skeleton": [k],
                    "kind": "single",
                    "status": "static" if mode == "static" else "verified",
                    "steps": [{"route": k}, {"route": k}],
                }
                for i, k in enumerate(k for k in keys if k.startswith("POST"))
            ]
        doc = {
            "schema": "lattice.workflows/1",
            "mode": "static" if mode == "static" else "observed",
            "run": {"mode": "static" if mode == "static" else "observed"},
            "flows": flows,
            "machines": [
                {
                    "id": "wm-nudges.delivery",
                    "table": "nudges",
                    "column": "delivery",
                    "status": "confirmed",
                }
            ],
        }
        (out_dir / "workflows.json").write_text(json.dumps(doc, sort_keys=True, indent=1))
        return {
            "rc": 0,
            "stdout": "",
            "stderr": "",
            "wall_s": 0.1,
            "json": None,
            "doc": doc,
            "dir": out_dir,
        }

    @node("B4")
    def inventory(self, ctx, tgt, out_dir, workflows=None, root=None, extract=None):
        if not self._use("inventory"):
            return self.real.inventory(
                ctx, tgt, out_dir, workflows=workflows, root=root, extract=extract
            )
        shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True)
        mod = Path(root or tgt["module_dir"])
        ex = extract if extract is not None else self._extract(mod)
        files = sorted(
            p.relative_to(mod).as_posix()
            for p in mod.rglob("*")
            if p.is_file()
            and p.suffix in (".cs", ".csproj")
            and "obj" not in p.parts
            and "bin" not in p.parts
        )
        (out_dir / "extract.json").write_text(json.dumps(ex, sort_keys=True))
        meta = {
            "schema": "lattice.inventory/1",
            "frontend": "dotnet_aspnet",
            "source": {"files": files},
        }
        (out_dir / "inventory.json").write_text(json.dumps(meta, sort_keys=True))
        if workflows is not None:
            shutil.copyfile(workflows, out_dir / "workflows.json")
        return {"rc": 0, "stderr": "", "inventory": meta, "dir": out_dir}

    @node("B4")
    def consumer(self, ctx, name, tgt, inv, proj, **kw):
        if not self._use("consumer"):
            return self.real.consumer(ctx, name, tgt, inv, proj, **kw)
        ex = json.loads((Path(inv) / "extract.json").read_text())
        t = tgt["truth"]
        truth_keys = {f"{r['method']} {r['template']}" for r in t["routes"]}
        keys = [f"{r['method']} {r['path']}" for r in ex["routes"]]
        cite = f"code:{t['routes'][1]['file']}:{t['routes'][1]['line']}"
        if name == "implementation":
            missing = sorted(k for k in truth_keys if k not in keys and "/health" not in k)
            rules = [
                {"id": f"R{i}", "status": "implemented_verified"}
                for i in range(1, 27 - len(missing))
            ] + [
                {"id": f"RX{i}", "status": "required_not_implemented"} for i in range(len(missing))
            ]
            extra = [k for k in keys if k not in truth_keys]
            doc = {
                "rules": rules,
                "implemented_not_required": [
                    {"kind": "route", "key": k} for k in [*extra, "GET /health"]
                ],
                "excluded": [],
                "evidence": cite,
            }
            return {"rc": 0, "json": doc, "stderr": "", "stdout": "", "wall_s": 0.1}
        if name == "appmap":
            return {
                "rc": 0,
                "json": {
                    "state": "ok",
                    "rules": [1],
                    "workflows": [1],
                    "machines": [1],
                    "access": [1],
                    "evidence": cite,
                },
                "stderr": "",
                "stdout": "",
                "wall_s": 0.1,
            }
        return {
            "rc": 0,
            "json": {"locations": [cite], "tier3": {"locations": [cite]}},
            "stderr": "",
            "stdout": "",
            "wall_s": 0.1,
        }

    @node("B4")
    def tool(self, ctx, name, tgt, proj, **kw):
        if not self._use("tool"):
            return self.real.tool(ctx, name, tgt, proj, **kw)
        t = tgt["truth"]
        r = t["routes"][1]
        cite = f"code:{r['file']}:{r['line']}"
        line = (Path(tgt["module_dir"]) / r["file"]).read_text().split("\n")[r["line"] - 1].strip()
        if name == "ask":
            ans = [
                {
                    "question": "is there a sync endpoint?",
                    "implementation": "not_specified_not_implemented",
                    "family": "not_implemented",
                    "citations": [],
                    "rejected": [],
                },
                {
                    "question": "can I check in a habit?",
                    "implementation": "implemented_unverified",
                    "family": "implemented",
                    "citations": [{"target": cite, "quote": line}],
                    "rejected": [],
                },
                {
                    "question": "who can dismiss a nudge?",
                    "implementation": "implemented_unverified",
                    "family": "implemented",
                    "answer": "Member (auth required)",
                    "citations": [{"target": cite, "quote": line}],
                    "rejected": [],
                },
            ]
            return {"digest_has_impl": True, "answers": ans}
        if name == "mcp":
            return {
                k: {"text": f"{k} {cite}"}
                for k in ("implementation_get", "workflow_get", "workflow_find")
            }
        if name == "perf":
            return {
                "rc": 0,
                "reports": [
                    {"budget_id": "home", "verdict": "pass", "fit": {"constant": 8, "per_item": 0}}
                ],
            }
        if name == "contracts_live":
            return {"rc": 0, "violations": 0, "findings": []}
        if name == "improve":
            return {"rc": 0, "identical": True}
        if name == "rebuild":
            return {"gate_ran": True, "rounds": [{"n": 1, "state": "pass"}]}
        raise KeyError(name)
