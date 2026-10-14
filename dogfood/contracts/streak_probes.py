"""Probe runner for layer 3 on a second provider, Streak (contracts between projects).

Implements the probes of ``docs/results/2026-10-13-contracts-streak-prereg.md`` (section 4): B0,
D1-D13, C1-C6, W1-W4, V1-V6, S1 and T1. Predictions and pass rules are copied from that document;
a probe that does not match is reported as a finding with its raw output, never adapted.

    python dogfood/contracts/streak_probes.py --runs 10 --scratch DIR \
        --out docs/results/data/2026-10-13-contracts-streak [--only B0,D1,...]

Every probe works on ``git clone --no-hardlinks`` scratch copies of Streak at a0db033 under
``DIR``; the real repository is only read (S1 proves it). No LLM and no API key are used. Every
deterministic probe runs ``--runs`` times (default 10) and all canonical outputs (``elapsed_s``
excluded) must be identical. Raw per-run outputs go to ``OUT/<ID>.json`` and ``OUT/summary.json``.
The helpers (Ctx, Edit, canonical outputs) are those of ``probes.py``, the first experiment's
runner, imported from the file next to this one.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def _load_base() -> Any:
    spec = importlib.util.spec_from_file_location(
        "lattice_contracts_first_probes", HERE / "probes.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses look the module up by name
    spec.loader.exec_module(mod)
    return mod


base = _load_base()
Edit = base.Edit
Result = base.Result
canon_call = base.canon_call
link0 = base.link0
broken_set = base.broken_set
changed_set = base.changed_set
fmt_b = base.fmt_b
fmt_c = base.fmt_c
sha = base.sha
canon_json = base.canon_json
LOCK = base.LOCK
MANIFEST = base.MANIFEST

STREAK = Path("/Users/vivekgade/Documents/streak")
REF = "a0db033"
CODE_BASE = "573aafc"  # next-phases, layer 3 included: `git diff` of lattice/ against it is empty
OLD_BASE = base.OLD_BASE  # detached worktree at c0cdefc
OLD_HEAD = base.OLD_HEAD
WIDGET = HERE / "streak-widget"
GOLDEN = HERE / "golden-streak" / "lattice.contracts.lock.json"
SPEC_PY = "spec.py"
CONSUMER = "streak-widget"
PROVIDER_NAME = "streak"
LOGIN = "POST /v1/auth/login"
ME = "GET /v1/me"
HOME = "GET /v1/home"
HABITS = "GET /v1/habits"
PROGRESS = "GET /v1/habits/{}/progress"
COMPLETIONS = "POST /v1/habits/{}/completions"
DISMISS = "POST /v1/nudges/{}/dismiss"
REGISTER = "POST /v1/auth/register"
HEALTH = "GET /health"
CLOCK = "PUT /test/clock"
IMPORT_ENDPOINTS = [LOGIN, ME, HOME, HABITS, PROGRESS, COMPLETIONS, DISMISS]

# Disclosed post hoc notes, written to summary.json (the orchestrator copies them to the results).
POST_HOC_SCRIPT_CORRECTIONS: list[str] = [
    "W3 shims: in the first complete run (32/32 pass) the shim scripts and their log were put in "
    "the run's TMPDIR, which made leftover_tmp_entries non-empty (['shim.log', 'shims']): a "
    "script artefact, not a leftover of the check. They now live under the scratch root.",
    "V2 and V6 observed text: the first complete run cut the error message after 90-100 "
    "characters before the scratch path was replaced by $WORK, so a partial scratch path showed "
    "in the data. The message is now scrubbed before it is shown. No pass rule changed.",
    "V1 'no timestamp' check: the first debug run (--runs 2, before the final run) flagged 7 "
    "'timestamps' because the check matched the field NAME \"timezone\" with a loose regex "
    "(a script bug, the lock has no timestamp). It now looks for ISO date-times in the text and "
    "for JSON object keys that name a time (time, timestamp, date, created, updated, *_at); "
    "field names listed as values are not keys.",
]
POST_HOC_PROCEDURE: list[str] = [
    "Helpers: Ctx, Edit, Result and the canonical-output functions are imported from probes.py "
    "(the first experiment's runner); Ctx is subclassed here for Streak (clone, manifest, "
    "consumer name). `str.replace` edits assert their count first; E5a's old string ends with a "
    "newline (the line-ending anchor of the prereg).",
    "Commits in the scratch clones use `-c user.name=probe -c user.email=probe@example.invalid`; "
    "the GIT_AUTHOR_*/GIT_COMMITTER_* names of the base Ctx are removed from the environment so "
    "that these flags apply (the dates stay fixed).",
    "Item notation: the code writes the auth item as 'as_user:ann'; comparisons normalise ':' "
    "after as_user to a space, nothing else.",
    "D1 item: the prereg gives no item for endpoint_missing; the expected item is the empty "
    "string (the claim 'endpoint' has none). Likewise the CHANGED rows of endpoint_added, "
    "endpoint_removed and the status/auth kinds have `where` empty; field kinds have "
    "'request' or 'response'.",
    "The expected CHANGED set of D7 (17 endpoint_removed + 24 endpoint_added, all unrelied) is "
    "derived from the clean export's 24 `/v1/` endpoints minus the 7 imported ones, as the "
    "prereg's counts say; the 26 endpoints count is checked on a `contracts export` run on the "
    "drifted clone.",
    "R1 statuses: the 26 expect_status lists below are copied from R1 (r1-streak-surface.json, "
    "templates), with names `{...}` written `{}` as the export does.",
    "W3 evidence that nothing is started: PATH is prefixed with shims for docker, go, mysqld, "
    "mysql and streak-api that log any call and exit 127; a `ps` sampler (20 ms) records the "
    "process command lines seen during the check; the clone's `git status --ignored` and bin/ "
    "are compared before and after. The check's own Python child (lattice.contracts.live) is "
    "expected: it loads the spec and refuses before any provider process.",
    "S1's git calls use --no-optional-locks so that `git status` cannot refresh the real "
    "repository's index; `find -newer` excludes .git only.",
    "T1 wall times are whole subprocess times (interpreter start included); the check's own "
    "elapsed_s is recorded beside them.",
    "W4 compares each of the 6 outputs (exit code, stdout, stderr) of the working tree and of "
    "the detached worktree lattice-contracts-base (HEAD c0cdefc, lattice/workspace.py "
    "unmodified, checked at run time), and records which lattice/workspace.py a child imports "
    "under each PYTHONPATH and whether lattice.contracts exists there.",
]

# R1's expect_status per template (r1-streak-surface.json, templates), ids and names as `{}`.
R1_STATUSES: dict[str, list[int]] = {
    "GET /health": [200],
    "PUT /test/clock": [200],
    "POST /v1/auth/login": [200, 401],
    "POST /v1/auth/logout": [204, 401],
    "POST /v1/auth/refresh": [200, 401],
    "POST /v1/auth/register": [201, 409, 422],
    "POST /v1/completions/batch": [200, 401, 422],
    "POST /v1/devices": [200, 201, 401, 422],
    "DELETE /v1/devices/{}": [204, 401, 404],
    "GET /v1/habits": [200, 401, 422],
    "POST /v1/habits": [200, 201, 401, 409, 422, 429],
    "DELETE /v1/habits/{}": [204, 401, 404],
    "GET /v1/habits/{}": [200, 401, 404],
    "PATCH /v1/habits/{}": [200, 401, 404, 409, 422],
    "GET /v1/habits/{}/completions": [200, 401, 404, 422],
    "POST /v1/habits/{}/completions": [200, 201, 401, 404, 409, 422],
    "DELETE /v1/habits/{}/completions/{}": [204, 401, 404, 409],
    "GET /v1/habits/{}/history": [200, 401, 404, 409, 422],
    "GET /v1/habits/{}/progress": [200, 401, 404],
    "GET /v1/home": [200, 401],
    "DELETE /v1/me": [204, 401],
    "GET /v1/me": [200, 401, 429],
    "PUT /v1/me": [200, 401, 422],
    "GET /v1/nudges": [200, 401, 422],
    "POST /v1/nudges/{}/dismiss": [200, 401, 404, 409],
    "GET /v1/sync": [200, 401, 422],
}
PUBLIC = {HEALTH, CLOCK, LOGIN, "POST /v1/auth/refresh", REGISTER}
NO_BODY = {HEALTH, CLOCK, "POST /v1/auth/logout"} | {
    k for k in R1_STATUSES if k.startswith("DELETE")
}
BEN_GRANTED = {
    "POST /v1/completions/batch",
    "POST /v1/devices",
    HABITS,
    "POST /v1/habits",
    PROGRESS,
    HOME,
    ME,
    "PUT /v1/me",
    "GET /v1/nudges",
    "GET /v1/sync",
}
PROGRESS_FIELDS = sorted(
    "days_elapsed days_remaining done habit_id period period_end period_start projected "
    "rate_per_day rate_per_period required_per_day required_per_period status target".split()
)
# prereg B0 table: endpoint -> (request fields, response fields)
WIDGET_TABLE: dict[str, tuple[list[str], list[str]]] = {
    LOGIN: (["email", "installation_id", "password"], ["expires_in", "token_type"]),
    ME: ([], ["email", "id", "notification_prefs", "timezone"]),
    HOME: ([], ["habits", "nudges", "today"]),
    PROGRESS: ([], PROGRESS_FIELDS),
    COMPLETIONS: (["date", "id", "note"], ["date", "habit_id", "id", "note"]),
    DISMISS: ([], ["habit_id", "id", "kind", "message"]),
}
B0_GAPS = [
    ("request_field_unobserved", HABITS, "cursor"),
    ("response_field_unobserved", LOGIN, "access_token"),
]


def ep_norm(e: str) -> str:
    return re.sub(r"\{[^{}]*\}", "{}", e)


def gaps_ordered(link: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(g["kind"], g["endpoint"], g["item"]) for g in link["gaps"]]


def changed_norm(
    rows: list[tuple[str, str, str, str, Any]],
) -> list[tuple[str, str, str, str, Any]]:
    return sorted(rows, key=lambda t: (t[0], t[1], t[2], t[3], str(t[4])))


# ---- context --------------------------------------------------------------------------------


class SCtx(base.Ctx):
    """The base Ctx on Streak: one provider project `streak`, one consumer `streak-widget`."""

    def __init__(self, runs: int, scratch: Path, out: Path) -> None:
        super().__init__(runs, scratch, out)
        for k in (
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
        ):
            self.env.pop(k, None)
        self._baseline: dict[str, Any] | None = None
        self._export: dict[str, Any] | None = None

    def git(self, repo: Path, *args: str) -> str:
        r = self.run(
            [
                "git",
                "-c",
                "user.name=probe",
                "-c",
                "user.email=probe@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "-C",
                str(repo),
                *args,
            ]
        )
        assert r.rc == 0, f"git {args}: {r.err}"
        return r.out

    def prepare_base(self) -> Path:
        """The clean clone at a0db033 (a `git clone --no-hardlinks` of the real repository)."""
        if self.base is None:
            dst = self.root / "streak-clean"
            self.run(["git", "clone", "-q", "--no-hardlinks", str(STREAK), str(dst)])
            self.git(dst, "checkout", "-q", REF)
            head = self.git(dst, "rev-parse", "HEAD").strip()
            assert head.startswith(REF), head
            assert self.git(dst, "status", "--porcelain", "--untracked-files=no") == ""
            self.base = dst
        return self.base

    def clone(self, name: str, edits: list[Any] | None = None, commit: bool = True) -> Path:
        d = self.work / name
        d.mkdir(parents=True)
        dst = d / "streak"
        self.run(["git", "clone", "-q", "--no-hardlinks", str(self.prepare_base()), str(dst)])
        self.last_edits = [e.apply(dst) for e in (edits or [])]
        if edits and commit:
            self.git(dst, "add", *sorted({e.path for e in edits}))
            self.git(dst, "commit", "-q", "-m", f"probe {name}")
        return dst

    def workspace(
        self,
        name: str,
        clone: Path,
        *,
        contract: str | None = "static",
        lock: bytes | None | str = "clean",
        variant: str = "m",
        exclude: list[str] | None = None,
    ) -> Path:
        d = self.work / name
        widget = d / CONSUMER
        if not widget.exists():
            shutil.copytree(WIDGET, widget, ignore=shutil.ignore_patterns("__pycache__", LOCK))
        data = self.clean_lock() if lock == "clean" else lock
        if isinstance(data, bytes):
            (widget / LOCK).write_bytes(data)
        md = d / variant
        md.mkdir(exist_ok=True)
        link = f'from = "{CONSUMER}"\nto = "{PROVIDER_NAME}"\nkind = "uses"\n'
        if contract is not None:
            link += f'contract = "{contract}"\n'
        # the route exclusion (2026-10-13-contracts-exclude): one line, only when asked for
        excl = f"exclude = {json.dumps(exclude)}\n" if exclude is not None else ""
        (md / MANIFEST).write_text(
            '[workspace]\nname = "probe"\n\n'
            f'[[project]]\nname = "{PROVIDER_NAME}"\npath = "{clone}"\nrole = "provider"\n'
            f"{excl}\n"
            f'[[project]]\nname = "{CONSUMER}"\npath = "{widget}"\nrole = "consumer"\n\n'
            f"[[link]]\n{link}"
        )
        return md / MANIFEST

    def lock_path(self, manifest: Path) -> Path:
        return manifest.parent.parent / CONSUMER / LOCK

    def check(self, manifest: Path, *args: str) -> Any:
        return self.contracts(manifest, "check", CONSUMER, "--json", *args)

    def pin(self, manifest: Path, *args: str) -> Any:
        return self.contracts(manifest, "pin", CONSUMER, "--json", *args)

    def export(self, manifest: Path) -> Any:
        return self.contracts(manifest, "export", PROVIDER_NAME)

    def clean_lock(self) -> bytes:
        """The lock pinned on the clean clone (the D/C/V rule): one plain pin, computed once."""
        if self._lock is None:
            clone = self.clone("pin-clean")
            m = self.workspace("pin-clean", clone, lock=None)
            c = self.pin(m)
            assert c.rc == 0, c.out + c.err
            self._lock = self.lock_path(m).read_bytes()
        return self._lock

    def baseline(self) -> dict[str, Any]:
        """B0's check on a clean clone, as a canonical record (what C1 and W2 compare with)."""
        if self._baseline is None:
            clone = self.clone("baseline")
            m = self.workspace("baseline", clone)
            self._baseline = canon_call(self.check(m))
        return self._baseline

    def clean_export(self) -> dict[str, Any]:
        if self._export is None:
            clone = self.clone("export-clean")
            m = self.workspace("export-clean", clone, lock=None)
            c = self.export(m)
            assert c.rc == 0, c.err
            self._export = json.loads(c.out)["export"]
        return self._export


def control_result(
    pid: str,
    pred: str,
    runs: list[Any],
    ident: bool,
    checks: dict[str, Any],
    obs: str,
    extra: dict[str, Any] | None = None,
) -> Any:
    return Result(pid, pred, obs, ident and all(checks.values()), runs, ident, checks, extra or {})


# ---- the edit tables (prereg section 4; exact strings and counts) ----------------------------


def _e(old: str, new: str, count: int = 1) -> Any:
    return Edit(SPEC_PY, old, new, count)


E1A = _e('"home": (200, {"today": TODAY,', '"home": (404, {"today": TODAY,', 2)
E1B = _e(
    'Step("GET", "/v1/home", "ann", expect_status=200,',
    'Step("GET", "/v1/home", "ann", expect_status=404,',
    2,
)
E5A = _e('"home": (200, {"today": TODAY,\n', '"home": (404, {"today": TODAY,\n', 1)  # line-ending
D1_EDITS = [E1A, E1B]
D2_EDITS = [
    _e(
        '"dismiss": (200, {"id": u(500), "habit_id": u(100)},',
        '"dismiss": (204, {"id": u(500), "habit_id": u(100)},',
        1,
    ),
    _e(
        'Step("POST", f"/v1/nudges/{u(500)}/dismiss", "ann", expect_status=200,',
        'Step("POST", f"/v1/nudges/{u(500)}/dismiss", "ann", expect_status=204,',
        3,
    ),
    _e(
        'Step("POST", f"/v1/nudges/{u(n)}/dismiss", "ann", expect_status=200,',
        'Step("POST", f"/v1/nudges/{u(n)}/dismiss", "ann", expect_status=204,',
        1,
    ),
]
D3_EDITS = [_e('"expires_in": 900}', '"expires_in_s": 900}', 2)]
D4_EDITS = [_e('"password": "password-ann",', '"passphrase": "password-ann",', 4)]
D5_EDITS = [E5A, E1B]
D6_EDITS = [_e('"next_cursor"', '"next"', 11)]
D7_EDITS = [_e("/v1/", "/v2/", 144)]
D8_EDITS = [
    _e(
        'Step("GET", "/v1/me", "ben", expect_status=200),',
        'Step("GET", "/v1/me", "ben", expect_status=200),\n'
        + " " * 8
        + 'Step("GET", "/v1/me/stats", "ben", expect_status=200, expect_body={"habits": 0}),',
        1,
    )
]
D9_EDITS = [_e('"expires_in": 900}', '"expires_in": "900"}', 2)]
D10_EDITS = [
    _e(
        'return {"id": u(n), "date": date, **more}',
        'return {"id": u(n), "date": date, "client_tz": "UTC", **more}',
        1,
    )
]
D11_EDITS = [
    _e(
        'Step("GET", hp(101, "/progress"), "ben", expect_status=200,',
        'Step("GET", hp(101, "/progress"), "ben", expect_status=404,',
        1,
    )
]
D12_EDITS = [
    _e(
        '"list_habits": (200, {"items": [{"id": u(100), "name": "walk"}],',
        '"list_habits": (200, {"items": [{"id": u(100), "title": "walk"}],',
        1,
    )
]
D13_EDITS = [
    _e(
        'Step("GET", "/v1/me", "ann", expect_status=429, expect_body=err("rate_limited")),',
        'Step("GET", "/v1/me", "ann", expect_status=200),',
        1,
    )
]
C2_EDIT = _e(
    'EMPTY_LIST = {"items": [], "next_cursor": None, "has_more": False}',
    'EMPTY_LIST = {"items": [], "next_cursor": None, "has_more": False}  # an empty first page\n',
    1,
)
C3_EDIT = _e("scenarios=(*ACCESS, *SCENARIOS),", "scenarios=(*SCENARIOS, *ACCESS),", 1)
C4_EDIT = _e(
    'expect_status=201, expect_body={"email": "cara@example.com", "timezone": "UTC"},',
    'expect_status=201, expect_body={"email": "cara@example.com", "timezone": "UTC", "id": 3},',
    1,
)
C5_EDITS = [
    _e(
        'habit(101, "b", user=2, start="2026-02-02")', 'habit(777, "b", user=2, start="2026-02-02")'
    ),
    _e(
        'Step("GET", hp(101, "/progress"), "ben", expect_status=200,',
        'Step("GET", hp(777, "/progress"), "ben", expect_status=200,',
    ),
]
C6_EDIT = _e(
    'return f"00000000-0000-4000-8000-{n:012d}"', 'return f"00000000-0000-4000-9000-{n:012d}"', 1
)


def d7_changed(export: dict[str, Any]) -> list[tuple[str, str, str, str, Any]]:
    """17 endpoint_removed (the /v1 endpoints no import names) + 24 endpoint_added (/v2)."""
    v1 = sorted(e for e in export["endpoints"] if " /v1/" in e)
    removed = [e for e in v1 if e not in IMPORT_ENDPOINTS]
    added = [e.replace(" /v1/", " /v2/") for e in v1]
    rows = [("endpoint_removed", "", e, "", False) for e in removed]
    rows += [("endpoint_added", "", e, "", False) for e in added]
    return rows


# id, title, edits, worst, BROKEN, CHANGED, gaps ("b0" or []); prediction text from the prereg
DRIFTS: list[dict[str, Any]] = [
    {
        "id": "D1",
        "title": "endpoint removed (every GET /v1/home 2xx becomes 404)",
        "edits": D1_EDITS,
        "worst": "BROKEN",
        "broken": [("endpoint_missing", HOME, "")],
        "changed": [],
    },
    {
        "id": "D2",
        "title": "status changed (dismiss 200 -> 204)",
        "edits": D2_EDITS,
        "worst": "BROKEN",
        "broken": [("status_not_observed", DISMISS, "200")],
        "changed": [("status_added", "", DISMISS, "204", True)],
    },
    {
        "id": "D3",
        "title": "response field renamed (expires_in -> expires_in_s)",
        "edits": D3_EDITS,
        "worst": "BROKEN",
        "broken": [("response_field_missing", LOGIN, "expires_in")],
        "changed": [
            ("field_added", "response", LOGIN, "expires_in_s", True),
            ("field_added", "response", "POST /v1/auth/refresh", "expires_in_s", False),
            ("field_removed", "response", "POST /v1/auth/refresh", "expires_in", False),
        ],
    },
    {
        "id": "D4",
        "title": "request field renamed (password -> passphrase)",
        "edits": D4_EDITS,
        "worst": "BROKEN",
        "broken": [("request_field_missing", LOGIN, "password")],
        "changed": [("field_added", "request", LOGIN, "passphrase", True)],
    },
    {
        "id": "D5",
        "title": "access removed from ann (GET /v1/home 200 -> 404)",
        "edits": D5_EDITS,
        "worst": "BROKEN",
        "broken": [("auth_changed", HOME, "as_user ann")],
        "changed": [("status_added", "", HOME, "404", True)],
    },
    {
        "id": "D6",
        "title": "next_cursor renamed to next",
        "edits": D6_EDITS,
        "worst": "BROKEN",
        "broken": [("response_field_missing", HABITS, "next_cursor")],
        "changed": [
            ("field_added", "response", HABITS, "next", True),
            ("field_added", "response", "GET /v1/habits/{}/completions", "next", False),
            ("field_removed", "response", "GET /v1/habits/{}/completions", "next_cursor", False),
            ("field_added", "response", "GET /v1/nudges", "next", False),
            ("field_removed", "response", "GET /v1/nudges", "next_cursor", False),
        ],
    },
    {
        "id": "D7",
        "title": "version bump /v1/ -> /v2/",
        "edits": D7_EDITS,
        "worst": "BROKEN",
        "broken": [("endpoint_missing", e, "") for e in IMPORT_ENDPOINTS],
        "changed": "d7",
        "gaps": [],
        "n_endpoints": 26,
    },
    {
        "id": "D8",
        "title": "endpoint added (GET /v1/me/stats)",
        "edits": D8_EDITS,
        "worst": "CHANGED",
        "broken": [],
        "changed": [("endpoint_added", "", "GET /v1/me/stats", "", False)],
    },
    {
        "id": "D9",
        "title": "field type changed (expires_in 900 -> '900')",
        "edits": D9_EDITS,
        "worst": "none",
        "broken": [],
        "changed": [],
    },
    {
        "id": "D10",
        "title": "required field added (client_tz on completions)",
        "edits": D10_EDITS,
        "worst": "CHANGED",
        "broken": [],
        "changed": [("field_added", "request", COMPLETIONS, "client_tz", True)],
    },
    {
        "id": "D11",
        "title": "ownership (ben's progress 200 -> 404)",
        "edits": D11_EDITS,
        "worst": "CHANGED",
        "broken": [],
        "changed": [("auth_tightened", "", PROGRESS, "as_user ben", True)],
    },
    {
        "id": "D12",
        "title": "nested item key renamed (name -> title)",
        "edits": D12_EDITS,
        "worst": "none",
        "broken": [],
        "changed": [],
    },
    {
        "id": "D13",
        "title": "relied noise (the only 429 on GET /v1/me)",
        "edits": D13_EDITS,
        "worst": "CHANGED",
        "broken": [],
        "changed": [("status_removed", "", ME, "429", True)],
    },
]

PROBES: dict[str, Callable[[SCtx], Any]] = {}


def probe(pid: str) -> Callable[[Callable[[SCtx], Any]], Callable[[SCtx], Any]]:
    def deco(fn: Callable[[SCtx], Any]) -> Callable[[SCtx], Any]:
        PROBES[pid] = fn
        return fn

    return deco


# ---- B0 ------------------------------------------------------------------------------------


@probe("B0")
def b0(ctx: SCtx) -> Any:
    pred = (
        "export 26 endpoints (public 5, token 21, unknown 0); error_body json_key(error), "
        "observed; statuses = R1 26/26; GET /v1/habits envelope only; granted ann 21, ben 10, "
        "refused none; /health and /test/clock exported; pin exit 0, 26 endpoints; check exit 0, "
        "0 findings, 2 gaps (cursor @ GET /v1/habits, access_token @ POST /v1/auth/login)"
    )
    clone = ctx.clone("B0")
    m = ctx.workspace("B0", clone, lock=None)
    lock_path = ctx.lock_path(m)

    def one(_: int) -> dict[str, Any]:
        lock_path.unlink(missing_ok=True)
        e = ctx.export(m)
        p = ctx.pin(m)
        lock_doc = json.loads(lock_path.read_text()) if lock_path.exists() else None
        c = ctx.check(m)
        return {
            "export": {"rc": e.rc, "doc": json.loads(e.out), "err": e.err},
            "pin": canon_call(p),
            "lock_endpoints": len(lock_doc["providers"][PROVIDER_NAME]["export"]["endpoints"])
            if lock_doc
            else None,
            "check": canon_call(c),
            "lock_sha256": sha(lock_path.read_bytes()),
        }

    runs, ident = ctx.repeat(one)
    r0 = runs[0]
    ex = r0["export"]["doc"]["export"]
    eps = ex["endpoints"]
    auth = {
        k: {e for e, v in eps.items() if v["auth"] == k} for k in ("public", "token", "unknown")
    }
    stat_mismatch = {
        k: [v["statuses"], R1_STATUSES.get(k)]
        for k, v in eps.items()
        if v["statuses"] != R1_STATUSES.get(k)
    }
    missing_r1 = sorted(set(R1_STATUSES) - set(eps))
    lk = link0(r0["check"])
    habits = eps.get(HABITS, {})
    bodies_empty = {e for e, v in eps.items() if v["body"] == []}
    bodies_obj = {e for e, v in eps.items() if v["body"] == ["object"]}
    table_bad = {
        e: [eps[e]["request_fields"], eps[e]["response_fields"], eps[e]["query_fields"]]
        for e, (req, resp) in WIDGET_TABLE.items()
        if e not in eps
        or eps[e]["request_fields"] != req
        or eps[e]["response_fields"] != resp
        or eps[e]["query_fields"] != []
    }
    ann = {e for e, v in eps.items() if "ann" in v["granted"]}
    ben = {e for e, v in eps.items() if "ben" in v["granted"]}
    imports = json.loads(  # the widget's imports, as the parser reads them
        ctx.run(
            [
                sys.executable,
                "-c",
                "import json,sys;from lattice.contracts.check import parse_imports;"
                "print(json.dumps([i.to_dict() for i in parse_imports(sys.argv[1])]))",
                str(WIDGET / "lattice.imports.toml"),
            ]
        ).out
    )
    tok = [i for i in imports if i["auth"] == "token"]
    w6 = next(i for i in imports if i["endpoint"] == COMPLETIONS)
    checks = {
        "endpoints_26": len(eps) == 26,
        "auth_public_5": auth["public"] == PUBLIC,
        "auth_token_21": len(auth["token"]) == 21 and auth["token"] == set(eps) - PUBLIC,
        "auth_unknown_0": auth["unknown"] == set(),
        "error_body_json_key_error": ex.get("error_body") == "json_key(error)",
        "basis_observed": ex.get("basis") == "observed",
        "statuses_equal_r1_26_of_26": not stat_mismatch and not missing_r1 and len(eps) == 26,
        "habits_response_fields_envelope": habits.get("response_fields")
        == ["has_more", "items", "next_cursor"],
        "habits_query_fields": habits.get("query_fields") == ["include_archived", "limit"],
        "habits_body_object": habits.get("body") == ["object"],
        "bodies_object_19": len(bodies_obj) == 19,
        "bodies_empty_7_named": bodies_empty == NO_BODY and len(bodies_empty) == 7,
        "widget_table_6_of_6": not table_bad,
        "ann_granted_21": ann == auth["token"] and len(ann) == 21,
        "ben_granted_exactly_10": ben == BEN_GRANTED,
        "public_granted_empty": all(eps[e]["granted"] == [] for e in auth["public"]),
        "refused_empty_26": all(v["refused"] == [] for v in eps.values()) and len(eps) == 26,
        "health_exported_public": eps.get(HEALTH, {}).get("auth") == "public",
        "clock_exported_public_now": eps.get(CLOCK, {}).get("auth") == "public"
        and eps[CLOCK]["request_fields"] == ["now"],
        "export_exit_0": r0["export"]["rc"] == 0,
        "pin_exit_0": r0["pin"]["rc"] == 0,
        "lock_26_endpoints": r0["lock_endpoints"] == 26,
        "check_exit_0": r0["check"]["rc"] == 0,
        "check_0_findings": lk["findings"] == [],
        "version_changed_false": lk["version_changed"] is False,
        "dirty_false": lk["current"]["dirty"] is False,
        "gaps_exactly_2_in_order": gaps_ordered(lk) == B0_GAPS,
        "as_user_ann_on_6_token_imports": len(tok) == 6 and all(i["as_user"] == "ann" for i in tok),
        "w6_200_and_201_hold": w6["status"] == [200, 201]
        and {200, 201} <= set(eps.get(COMPLETIONS, {}).get("statuses", [])),
    }
    obs = (
        f"{len(eps)} endpoints; auth public {len(auth['public'])} token {len(auth['token'])} "
        f"unknown {len(auth['unknown'])}; error_body {ex.get('error_body')}; statuses==R1 "
        f"{not stat_mismatch and not missing_r1}; ann {len(ann)} ben {len(ben)}; bodies "
        f"object {len(bodies_obj)} empty {len(bodies_empty)}; /health {HEALTH in eps} "
        f"/test/clock {CLOCK in eps}; pin exit {r0['pin']['rc']} ({r0['lock_endpoints']} "
        f"endpoints); check exit {r0['check']['rc']}, {len(lk['findings'])} findings, gaps "
        f"{fmt_b(gaps_ordered(lk))}"
    )
    extra = {
        "statuses_mismatch": stat_mismatch,
        "missing_vs_r1": missing_r1,
        "widget_table_mismatch": table_bad,
        "git_ref": r0["export"]["doc"]["version"],
        "bodies_empty": sorted(bodies_empty),
        "ben_granted": sorted(ben),
        "test_harness_endpoints": {HEALTH: eps.get(HEALTH), CLOCK: eps.get(CLOCK)},
        "lock_sha256": r0["lock_sha256"],
    }
    return control_result("B0", pred, runs, ident, checks, obs, extra)


# ---- D series -------------------------------------------------------------------------------


def drift_probe(ctx: SCtx, spec: dict[str, Any]) -> Any:
    pid = spec["id"]
    worst, broken = spec["worst"], sorted(spec["broken"])
    changed = d7_changed(ctx.clean_export()) if spec["changed"] == "d7" else spec["changed"]
    changed = changed_norm(changed)
    gaps_want = spec.get("gaps", B0_GAPS)
    label = worst if worst != "none" else "MISS (0 findings, spec_hash equal, exit 0)"
    changed_txt = ": " + fmt_c(changed) if len(changed) <= 5 else " (17 removed + 24 added)"
    pred = (
        f"{spec['title']}: {label}; BROKEN {fmt_b(broken)}; CHANGED "
        f"{len(changed)}{changed_txt}"
        f"; version_changed true; gaps {len(gaps_want)}"
    )
    clone = ctx.clone(pid, spec["edits"])
    edits = ctx.last_edits
    m = ctx.workspace(pid, clone)

    def one(_: int) -> dict[str, Any]:
        c = ctx.check(m)
        e = ctx.export(m)
        n = len(json.loads(e.out)["export"]["endpoints"]) if e.rc == 0 else None
        return {"check": canon_call(c), "export_rc": e.rc, "export_endpoints": n}

    runs, ident = ctx.repeat(one)
    r0 = runs[0]
    lk = link0(r0["check"])
    got_b, got_c = broken_set(lk), changed_norm(changed_set(lk))
    got_worst = "BROKEN" if got_b else ("CHANGED" if got_c else "none")
    want_exit = 1 if worst != "none" else 0
    checks: dict[str, Any] = {
        "worst_class": got_worst == worst,
        "broken_set": got_b == broken,
        "changed_set": got_c == changed,
        "exit": r0["check"]["rc"] == want_exit,
        "version_changed": lk["version_changed"] is True,
        "gaps": gaps_ordered(lk) == gaps_want,
        "spec_hash_changed_iff_findings": lk["spec_hash_changed"] is (worst != "none"),
    }
    if worst == "none":
        checks["no_findings"] = lk["findings"] == []
    if "n_endpoints" in spec:
        checks["endpoints_still_26"] = r0["export_endpoints"] == spec["n_endpoints"]
        checks["changed_41"] = len(got_c) == 41
    core = ident and checks["worst_class"] and checks["broken_set"]
    obs = (
        f"{got_worst}; BROKEN {fmt_b(got_b)}; CHANGED {len(got_c)}"
        f"{': ' + fmt_c(got_c) if len(got_c) <= 6 else ''}; exit {r0['check']['rc']}; "
        f"version_changed {lk['version_changed']}; spec_hash_changed {lk['spec_hash_changed']}; "
        f"gaps {len(lk['gaps'])}"
    )
    return Result(
        pid,
        pred,
        obs,
        ident and all(checks.values()),
        runs,
        ident,
        checks,
        {
            "edits": edits,
            "pass_core": core,
            "git_ref": lk["current"]["git_ref"],
            "changed_all": got_c,
            "broken_all": got_b,
            "gaps": gaps_ordered(lk),
        },
    )


for _d in DRIFTS:
    PROBES[_d["id"]] = lambda ctx, _s=_d: drift_probe(ctx, _s)  # type: ignore[misc]


# ---- C series -------------------------------------------------------------------------------


def _check_runs(
    ctx: SCtx, name: str, edits: list[Any] | None, commit: bool = True
) -> tuple[Any, ...]:
    clone = ctx.clone(name, edits, commit=commit)
    m = ctx.workspace(name, clone)
    runs, ident = ctx.repeat(lambda _: canon_call(ctx.check(m)))
    return clone, m, runs, ident, runs[0], link0(runs[0])


@probe("C1")
def c1(ctx: SCtx) -> Any:
    pred = "exit 0, 0 findings, B0's 2 gaps, version_changed false"
    _clone, _m, runs, ident, r0, lk = _check_runs(ctx, "C1", None)
    checks = {
        "exit_0": r0["rc"] == 0,
        "0_findings": lk["findings"] == [],
        "gaps_equal_b0": gaps_ordered(lk) == B0_GAPS,
        "version_changed_false": lk["version_changed"] is False,
    }
    obs = (
        f"exit {r0['rc']}, {len(lk['findings'])} findings, {len(lk['gaps'])} gap(s), "
        f"version_changed {lk['version_changed']}"
    )
    return control_result("C1", pred, runs, ident, checks, obs)


@probe("C2")
def c2(ctx: SCtx) -> Any:
    pred = "spec.py sha256 changes; spec_hash equal; exit 0; version_changed true"
    before = sha((ctx.prepare_base() / SPEC_PY).read_bytes())
    clone, _m, runs, ident, r0, lk = _check_runs(ctx, "C2", [C2_EDIT])
    after = sha((clone / SPEC_PY).read_bytes())
    checks = {
        "file_sha_changes": before != after,
        "spec_hash_equal": lk["spec_hash_changed"] is False
        and lk["current"]["spec_hash"] == lk["pinned"]["spec_hash"],
        "exit_0": r0["rc"] == 0,
        "0_findings": lk["findings"] == [],
        "version_changed_true": lk["version_changed"] is True,
    }
    obs = (
        f"file sha256 changed {before != after}; spec_hash_changed {lk['spec_hash_changed']}; "
        f"exit {r0['rc']}, {len(lk['findings'])} findings; version_changed "
        f"{lk['version_changed']}"
    )
    extra = {"sha256_before": before, "sha256_after": after, "edits": ctx.last_edits}
    return control_result("C2", pred, runs, ident, checks, obs, extra)


@probe("C3")
def c3(ctx: SCtx) -> Any:
    pred = "all 156 scenarios reordered: spec_hash equal; exit 0"
    _clone, _m, runs, ident, r0, lk = _check_runs(ctx, "C3", [C3_EDIT])
    checks = {
        "spec_hash_equal": lk["spec_hash_changed"] is False,
        "exit_0": r0["rc"] == 0,
        "0_findings": lk["findings"] == [],
    }
    obs = (
        f"spec_hash_changed {lk['spec_hash_changed']}; exit {r0['rc']}, "
        f"{len(lk['findings'])} findings"
    )
    return control_result("C3", pred, runs, ident, checks, obs, {"edits": ctx.last_edits})


@probe("C4")
def c4(ctx: SCtx) -> Any:
    pred = (
        "exit 1; exactly 1 CHANGED field_added response id @ POST /v1/auth/register, unrelied; "
        "0 BROKEN"
    )
    _clone, _m, runs, ident, r0, lk = _check_runs(ctx, "C4", [C4_EDIT])
    want = [("field_added", "response", REGISTER, "id", False)]
    checks = {
        "exit_1": r0["rc"] == 1,
        "changed_exactly": changed_set(lk) == want,
        "0_broken": broken_set(lk) == [],
        "findings_total_1": len(lk["findings"]) == 1,
    }
    obs = f"exit {r0['rc']}; CHANGED {fmt_c(changed_set(lk))}; BROKEN {fmt_b(broken_set(lk))}"
    return control_result("C4", pred, runs, ident, checks, obs, {"edits": ctx.last_edits})


@probe("C5")
def c5(ctx: SCtx) -> Any:
    pred = "one scenario's UUIDs (101 -> 777 in progress_timezone_example): spec_hash equal; exit 0"
    _clone, _m, runs, ident, r0, lk = _check_runs(ctx, "C5", C5_EDITS)
    checks = {
        "spec_hash_equal": lk["spec_hash_changed"] is False,
        "exit_0": r0["rc"] == 0,
        "0_findings": lk["findings"] == [],
    }
    obs = (
        f"spec_hash_changed {lk['spec_hash_changed']}; exit {r0['rc']}, "
        f"{len(lk['findings'])} findings"
    )
    return control_result("C5", pred, runs, ident, checks, obs, {"edits": ctx.last_edits})


@probe("C6")
def c6(ctx: SCtx) -> Any:
    pred = "every UUID of the spec becomes another valid UUID: spec_hash equal; exit 0"
    _clone, _m, runs, ident, r0, lk = _check_runs(ctx, "C6", [C6_EDIT])
    checks = {
        "spec_hash_equal": lk["spec_hash_changed"] is False,
        "exit_0": r0["rc"] == 0,
        "0_findings": lk["findings"] == [],
    }
    obs = (
        f"spec_hash_changed {lk['spec_hash_changed']}; exit {r0['rc']}, "
        f"{len(lk['findings'])} findings"
    )
    return control_result("C6", pred, runs, ident, checks, obs, {"edits": ctx.last_edits})


# ---- W series -------------------------------------------------------------------------------


@probe("W1")
def w1(ctx: SCtx) -> Any:
    pred = (
        "no contract key, spec.py ending in raise RuntimeError: exit 0, checked 0 (spec never "
        "loaded); the same link 'static': exit 2 export_failed"
    )
    clone = ctx.clone("W1")
    p = clone / SPEC_PY
    p.write_text(p.read_text() + '\nraise RuntimeError("not loaded")\n')
    ctx.git(clone, "add", SPEC_PY)
    ctx.git(clone, "commit", "-q", "-m", "probe W1 raise")
    absent = ctx.workspace("W1", clone, contract=None, variant="absent")
    static = ctx.workspace("W1", clone, contract="static", variant="static")

    def one(_: int) -> dict[str, Any]:
        a = ctx.check(absent)
        t = ctx.contracts(absent, "check", CONSUMER)
        s = ctx.check(static)
        return {
            "absent": canon_call(a),
            "absent_text": {"rc": t.rc, "out": t.out, "err": t.err},
            "static": canon_call(s),
        }

    runs, ident = ctx.repeat(one)
    r0 = runs[0]
    lk = link0(r0["static"])
    checks = {
        "absent_exit_0": r0["absent"]["rc"] == 0,
        "absent_checked_0": r0["absent"]["doc"]["checked"] == 0
        and "0 link(s) checked" in r0["absent_text"]["out"],
        "static_exit_2": r0["static"]["rc"] == 2,
        "static_export_failed": (lk["error"] or {}).get("code") == "export_failed",
    }
    obs = (
        f"absent: exit {r0['absent']['rc']}, checked {r0['absent']['doc']['checked']}; "
        f"static: exit {r0['static']['rc']} {(lk['error'] or {}).get('code')}"
    )
    return control_result("W1", pred, runs, ident, checks, obs)


@probe("W2")
def w2(ctx: SCtx) -> Any:
    pred = "'static' link: the spec loads and the check runs; the result equals B0; live is null"
    clone = ctx.clone("W2")
    m = ctx.workspace("W2", clone)
    runs, ident = ctx.repeat(lambda _: canon_call(ctx.check(m)))
    r0, lk = runs[0], link0(runs[0])
    b0_rec = ctx.baseline()
    checks = {
        "live_null": lk["live"] is None,
        "contract_static": lk["contract"] == "static",
        "result_equals_b0": r0 == b0_rec,
        "exit_0": r0["rc"] == 0,
        "gaps_equal_b0": gaps_ordered(lk) == B0_GAPS,
    }
    obs = (
        f"live {lk['live']}; result equals B0 {r0 == b0_rec}; exit {r0['rc']}; gaps "
        f"{len(lk['gaps'])}"
    )
    return control_result("W2", pred, runs, ident, checks, obs)


class ProcSampler:
    """Samples `ps` while a check runs and keeps the command lines of the processes the check
    started (the descendants of this process), paths and temp names normalised."""

    def __init__(self, strip: str) -> None:
        self.strip = strip
        self.seen: set[str] = set()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def _sample(self) -> None:
        out = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,command="], text=True, capture_output=True, check=False
        ).stdout
        rows: dict[int, tuple[int, str]] = {}
        for ln in out.splitlines():
            parts = ln.split(None, 2)
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                rows[int(parts[0])] = (int(parts[1]), parts[2])
        mine = {os.getpid()}
        grew = True
        while grew:
            grew = False
            for pid, (ppid, _cmd) in rows.items():
                if ppid in mine and pid not in mine:
                    mine.add(pid)
                    grew = True
        for pid in mine - {os.getpid()}:
            cmd = rows[pid][1]
            if cmd.startswith("ps -axo") or " ps -axo" in cmd:
                continue
            cmd = cmd.replace(self.strip, "$WORK")
            self.seen.add(re.sub(r"lattice-live-\w+", "lattice-live-XXXX", cmd).strip())

    def _loop(self) -> None:
        while not self.stop.is_set():
            self._sample()
            self.stop.wait(0.02)

    def __enter__(self) -> ProcSampler:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop.set()
        self.thread.join(5)


def _tree(ctx: SCtx, clone: Path) -> str:
    return ctx.git(clone, "--no-optional-locks", "status", "--porcelain", "--ignored")


@probe("W3")
def w3(ctx: SCtx) -> Any:
    pred = (
        "contract 'live' on Streak: check exit 2, link status error, error.code "
        "unsupported_database, findings and gaps []; nothing started (no Docker, no Go build, "
        "no provider process); --static-only exit 0 with live not_run/static-only; pin exit 0"
    )
    clone = ctx.clone("W3")
    m = ctx.workspace("W3", clone, contract="live")
    shims = ctx.root / "w3-shims"
    shims.mkdir()
    log = ctx.root / "w3-shim.log"
    log.write_text("")
    for name in ("docker", "go", "mysqld", "mysql", "streak-api"):
        sh = shims / name
        sh.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\nexit 127\n')
        sh.chmod(0o755)
    old_path = ctx.env["PATH"]
    ctx.env["PATH"] = f"{shims}:{old_path}"
    tree_before, bin_before = _tree(ctx, clone), (clone / "bin").exists()
    lock_before = ctx.lock_path(m).read_bytes()
    sampler = ProcSampler(str(ctx.root))
    try:
        with sampler:

            def one(_: int) -> dict[str, Any]:
                full = ctx.check(m)
                st = ctx.check(m, "--static-only")
                pn = ctx.pin(m)
                return {
                    "check": canon_call(full),
                    "static_only": canon_call(st),
                    "pin": canon_call(pn),
                }

            runs, ident = ctx.repeat(one)
    finally:
        ctx.env["PATH"] = old_path
    tree_after, bin_after = _tree(ctx, clone), (clone / "bin").exists()
    r0 = runs[0]
    lk, st = link0(r0["check"]), link0(r0["static_only"])
    shim_calls = [ln for ln in log.read_text().splitlines() if ln]
    seen = sorted(sampler.seen)
    bad = [
        ln for ln in seen if re.search(r"docker|streak-api|mysql|\bgo (build|run|test)|/go ", ln)
    ]
    checks = {
        "check_exit_2": r0["check"]["rc"] == 2,
        "link_status_error": lk["status"] == "error",
        "error_code_unsupported_database": (lk["error"] or {}).get("code")
        == "unsupported_database",
        "findings_empty": lk["findings"] == [],
        "gaps_empty": lk["gaps"] == [],
        "no_docker_go_mysql_called": shim_calls == [],
        "no_docker_go_mysql_seen_in_ps": bad == [],
        "bin_absent_before_and_after": not bin_before and not bin_after,
        "clone_tree_unchanged": tree_before == tree_after,
        "static_only_exit_0": r0["static_only"]["rc"] == 0,
        "static_only_not_run": st["live"] == {"status": "not_run", "reason": "static-only"},
        "pin_exit_0": r0["pin"]["rc"] == 0,
        "pin_lock_unchanged": ctx.lock_path(m).read_bytes() == lock_before,
    }
    code = (lk["error"] or {}).get("code")
    obs = (
        f"check exit {r0['check']['rc']}, link status {lk['status']}, error.code {code}, "
        f"findings {len(lk['findings'])}, gaps {len(lk['gaps'])}; docker/go/mysql calls "
        f"{len(shim_calls)}; ps lines seen {len(seen)}, provider-like {len(bad)}; bin/ present "
        f"{bin_after}; static-only exit {r0['static_only']['rc']} live {st['live']}; pin exit "
        f"{r0['pin']['rc']}"
    )
    extra = {
        "error": lk["error"],
        "link_live": lk["live"],
        "full_check_doc": r0["check"]["doc"],
        "shim_calls": shim_calls,
        "ps_command_lines_seen": seen,
        "tree_before": tree_before,
        "tree_after": tree_after,
        "which_tools": {n: shutil.which(n, path=old_path) for n in ("docker", "go")},
    }
    return control_result("W3", pred, runs, ident, checks, obs, extra)


W4_COMMANDS: list[list[str]] = [
    ["show"],
    ["show", "--json"],
    ["validate"],
    ["validate", "--json"],
    ["impact", PROVIDER_NAME],
    ["impact", PROVIDER_NAME, "--json"],
]


def old_checkout() -> dict[str, Any]:
    info: dict[str, Any] = {"path": str(OLD_BASE)}
    try:

        def g(*a: str) -> str:
            return subprocess.run(
                ["git", "-C", str(OLD_BASE), *a], text=True, capture_output=True, check=True
            ).stdout

        head = g("rev-parse", "HEAD").strip()
        st = g("status", "--porcelain", "--", "lattice/workspace.py")
        diff = g("diff", OLD_HEAD, "--", "lattice/workspace.py")
        br = g("branch", "--show-current").strip()
        info |= {
            "head": head,
            "branch": br or "(detached)",
            "workspace_py_status": st,
            "workspace_py_diff_vs_head": diff,
            "workspace_py_exists": (OLD_BASE / "lattice" / "workspace.py").exists(),
        }
        info["usable"] = (
            head.startswith(OLD_HEAD) and st == "" and diff == "" and info["workspace_py_exists"]
        )
    except Exception as e:  # noqa: BLE001
        info |= {"usable": False, "error": str(e)}
    return info


@probe("W4")
def w4(ctx: SCtx) -> Any:
    pred = (
        "a manifest without layer-3 keys: workspace show, show --json, validate, validate --json, "
        "impact streak, impact streak --json are byte-identical (6 of 6) to the old code "
        "(c0cdefc)"
    )
    clone = ctx.clone("W4")
    m = ctx.workspace("W4", clone, contract=None)
    old = old_checkout()
    roots = {"new": REPO, **({"old": OLD_BASE} if old["usable"] else {})}

    def one(_: int) -> dict[str, Any]:
        rec: dict[str, Any] = {}
        for cmd in W4_COMMANDS:
            argv = ["workspace", "--workspace", str(m), *cmd]
            row: dict[str, Any] = {}
            for name, root in roots.items():
                c = ctx.lattice_cli(root, *argv)
                row[name] = {"rc": c.rc, "out": c.out, "err": c.err}
            rec[" ".join(cmd)] = row
        return rec

    runs, ident = ctx.repeat(one)
    same = [row["new"] == row.get("old") for row in runs[0].values()]
    which = {name: base.which_code(ctx, root) for name, root in roots.items()}
    checks = {
        "old_reference_usable": bool(old["usable"]),
        "old_head_c0cdefc": str(old.get("head", "")).startswith(OLD_HEAD),
        "new_is_new": which["new"]["has_contracts"]
        and which["new"]["workspace_py"].startswith(str(REPO)),
        "old_is_old": "old" in which
        and not which["old"]["has_contracts"]
        and which["old"]["workspace_py"].startswith(str(OLD_BASE)),
        "6_of_6_identical": len(same) == 6 and all(same),
        "new_exit_codes_0": all(row["new"]["rc"] == 0 for row in runs[0].values()),
    }
    obs = (
        f"{sum(same)}/6 byte-identical to the old code; exits "
        + ",".join(str(r["new"]["rc"]) for r in runs[0].values())
        + f"; old HEAD {str(old.get('head', ''))[:7]}, workspace.py modified "
        f"{bool(old.get('workspace_py_status') or old.get('workspace_py_diff_vs_head'))}"
    )
    return control_result(
        "W4", pred, runs, ident, checks, obs, {"old_checkout": old, "imports": which}
    )


# ---- V series -------------------------------------------------------------------------------


def _time_keys(node: Any) -> list[str]:
    """JSON object keys (not field names listed as values) that name a time or a date."""
    pat = re.compile(r"(^|_)(time|timestamp|date|created|updated|pinned|generated)(_at|_on)?$|_at$")
    out: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if pat.search(k):
                out.append(k)
            out += _time_keys(v)
    elif isinstance(node, list):
        for v in node:
            out += _time_keys(v)
    return out


@probe("V1")
def v1(ctx: SCtx) -> Any:
    pred = (
        "10 pins on the clean clone: locks byte-identical to each other and to the golden; "
        "spec_hash == sha256 of the embedded export; no scratch path, no timestamp"
    )
    clone = ctx.clone("V1")
    m = ctx.workspace("V1", clone, lock=None)
    lock_path = ctx.lock_path(m)

    def one(_: int) -> dict[str, Any]:
        lock_path.unlink(missing_ok=True)
        c = ctx.pin(m)
        data = lock_path.read_bytes()
        return {"pin": canon_call(c), "lock_sha256": sha(data), "lock_text": data.decode()}

    runs, ident = ctx.repeat(one)
    text = runs[0]["lock_text"]
    doc = json.loads(text)
    prov = doc["providers"][PROVIDER_NAME]
    recomputed = sha(canon_json(prov["export"]).encode("ascii"))
    golden = GOLDEN.read_bytes() if GOLDEN.exists() else b""
    scratch_hits = sum(text.count(s) for s in (str(ctx.root), str(ctx.root.parent), "/private/tmp"))
    scratch_hits += text.count("/Users/") + text.count("/var/folders")
    timestamps = re.findall(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", text) + _time_keys(doc)
    checks = {
        "all_pins_exit_0": all(r["pin"]["rc"] == 0 for r in runs),
        "locks_identical": len({r["lock_sha256"] for r in runs}) == 1,
        "equals_golden": runs[0]["lock_sha256"] == sha(golden) and golden != b"",
        "spec_hash_recomputed": recomputed == prov["version"]["spec_hash"],
        "no_scratch_path": scratch_hits == 0,
        "no_timestamp": timestamps == [],
    }
    obs = (
        f"{len({r['lock_sha256'] for r in runs})} distinct lock(s) in {len(runs)}; equals golden "
        f"{checks['equals_golden']}; spec_hash {prov['version']['spec_hash'][:12]} == recomputed "
        f"{recomputed[:12]}; scratch path hits {scratch_hits}; timestamps {len(timestamps)}"
    )
    extra = {
        "lock_sha256": runs[0]["lock_sha256"],
        "golden_sha256": sha(golden),
        "recomputed_spec_hash": recomputed,
        "lock_keys": sorted(doc),
        "version": prov["version"],
    }
    for r in runs:
        r.pop("lock_text")
    return control_result("V1", pred, runs, ident, checks, obs, extra)


@probe("V2")
def v2(ctx: SCtx) -> Any:
    pred = "no lock: exit 2, no_pin, the message names the lock path and 'lattice contracts pin'"
    clone = ctx.clone("V2")
    m = ctx.workspace("V2", clone, lock=None)
    lock_path = ctx.lock_path(m)

    def one(_: int) -> dict[str, Any]:
        lock_path.unlink(missing_ok=True)
        return canon_call(ctx.check(m))

    runs, ident = ctx.repeat(one)
    r0 = runs[0]
    lk = link0(r0)
    msg = (lk["error"] or {}).get("message", "")
    checks = {
        "exit_2": r0["rc"] == 2,
        "no_pin": (lk["error"] or {}).get("code") == "no_pin",
        "names_lock_path": str(lock_path) in msg,
        "names_pin_command": "lattice contracts pin" in msg,
    }
    shown = msg.replace(str(ctx.root), "$WORK")
    obs = f"exit {r0['rc']} {(lk['error'] or {}).get('code')}; {shown}"
    return control_result("V2", pred, runs, ident, checks, obs, {"message": msg})


@probe("V3")
def v3(ctx: SCtx) -> Any:
    pred = (
        "after D1: pin exit 1, lock bytes unchanged; pin --force writes the lock; the next check "
        "exits 0 with a third gap endpoint_unobserved GET /v1/home"
    )
    clone = ctx.clone("V3", D1_EDITS)
    m = ctx.workspace("V3", clone)
    lock_path = ctx.lock_path(m)
    clean = ctx.clean_lock()

    def one(_: int) -> dict[str, Any]:
        lock_path.write_bytes(clean)
        p1 = ctx.pin(m)
        after1 = lock_path.read_bytes()
        p2 = ctx.pin(m, "--force")
        after2 = lock_path.read_bytes()
        c = ctx.check(m)
        return {
            "pin": canon_call(p1),
            "lock_unchanged_after_refused_pin": after1 == clean,
            "pin_force": canon_call(p2),
            "lock_written_by_force": after2 != clean,
            "check": canon_call(c),
        }

    runs, ident = ctx.repeat(one)
    r0 = runs[0]
    lk = link0(r0["check"])
    want_gaps = sorted([*B0_GAPS, ("endpoint_unobserved", HOME, "")])
    checks = {
        "pin_exit_1": r0["pin"]["rc"] == 1,
        "lock_unchanged": r0["lock_unchanged_after_refused_pin"],
        "force_exit_0": r0["pin_force"]["rc"] == 0,
        "force_wrote": r0["lock_written_by_force"],
        "check_exit_0": r0["check"]["rc"] == 0,
        "gaps_exactly_3": sorted(gaps_ordered(lk)) == want_gaps and len(lk["gaps"]) == 3,
        "no_findings": lk["findings"] == [],
    }
    obs = (
        f"pin exit {r0['pin']['rc']}, lock unchanged {r0['lock_unchanged_after_refused_pin']}; "
        f"--force exit {r0['pin_force']['rc']}, wrote {r0['lock_written_by_force']}; check exit "
        f"{r0['check']['rc']}, {len(lk['findings'])} findings, gaps "
        f"{fmt_b(sorted(gaps_ordered(lk)))}"
    )
    return control_result("V3", pred, runs, ident, checks, obs)


@probe("V4")
def v4(ctx: SCtx) -> Any:
    pred = (
        "README-only commit: exit 0, 0 findings, version_changed true, same spec_hash, git_ref "
        "other than a0db033"
    )
    clone = ctx.clone("V4")
    readme = clone / "README.md"
    readme.write_text(readme.read_text() + "\nA probe note.\n")
    ctx.git(clone, "add", "README.md")
    ctx.git(clone, "commit", "-q", "-m", "probe V4 readme")
    m = ctx.workspace("V4", clone)
    runs, ident = ctx.repeat(lambda _: canon_call(ctx.check(m)))
    r0, lk = runs[0], link0(runs[0])
    checks = {
        "exit_0": r0["rc"] == 0,
        "0_findings": lk["findings"] == [],
        "version_changed": lk["version_changed"] is True,
        "same_spec_hash": lk["current"]["spec_hash"] == lk["pinned"]["spec_hash"],
        "git_ref_differs": lk["current"]["git_ref"] != lk["pinned"]["git_ref"]
        and not lk["current"]["git_ref"].startswith(REF),
    }
    obs = (
        f"exit {r0['rc']}, {len(lk['findings'])} findings, version_changed "
        f"{lk['version_changed']}, spec_hash_changed {lk['spec_hash_changed']}, git_ref "
        f"{lk['pinned']['git_ref'][:8]} -> {lk['current']['git_ref'][:8]}"
    )
    return control_result("V4", pred, runs, ident, checks, obs)


@probe("V5")
def v5(ctx: SCtx) -> Any:
    pred = "C2's edit left uncommitted: dirty true, version_changed true, exit 0"
    _clone, _m, runs, ident, r0, lk = _check_runs(ctx, "V5", [C2_EDIT], commit=False)
    checks = {
        "dirty_true": lk["current"]["dirty"] is True,
        "version_changed_true": lk["version_changed"] is True,
        "exit_0": r0["rc"] == 0,
        "0_findings": lk["findings"] == [],
    }
    obs = (
        f"dirty {lk['current']['dirty']}, version_changed {lk['version_changed']}, exit "
        f"{r0['rc']}, {len(lk['findings'])} findings"
    )
    return control_result("V5", pred, runs, ident, checks, obs, {"edits": ctx.last_edits})


@probe("V6")
def v6(ctx: SCtx) -> Any:
    pred = "the lock with one hex digit of spec_hash flipped: exit 2, bad_pin"
    clone = ctx.clone("V6")
    text = ctx.clean_lock().decode()
    h = json.loads(text)["providers"][PROVIDER_NAME]["version"]["spec_hash"]
    assert text.count(h) == 1
    flipped = ("0" if h[0] != "0" else "1") + h[1:]
    m = ctx.workspace("V6", clone, lock=text.replace(h, flipped).encode())
    runs, ident = ctx.repeat(lambda _: canon_call(ctx.check(m)))
    r0, lk = runs[0], link0(runs[0])
    code = (lk["error"] or {}).get("code")
    checks = {"exit_2": r0["rc"] == 2, "bad_pin": code == "bad_pin"}
    shown = (lk["error"] or {}).get("message", "").replace(str(ctx.root), "$WORK")
    obs = f"exit {r0['rc']} {code}; {shown}"
    return control_result("V6", pred, runs, ident, checks, obs, {"flipped": [h[:8], flipped[:8]]})


# ---- safety and cost --------------------------------------------------------------------------


class S1State:
    """The safety record of the real Streak checkout, taken before the whole run."""

    def __init__(self, ctx: SCtx) -> None:
        self.marker = ctx.root / "s1-marker"
        self.marker.write_text("start\n")
        time.sleep(1.1)  # a clear mtime gap for find -newer
        self.before = self.snapshot(ctx)

    def snapshot(self, ctx: SCtx) -> dict[str, Any]:
        # --no-optional-locks: `git status` must not even refresh the real repository's index
        head = ctx.git(STREAK, "rev-parse", "HEAD").strip()
        status = ctx.git(STREAK, "--no-optional-locks", "status", "--short").rstrip("\n")
        studio = base.sha((STREAK / "studio.toml").read_bytes())
        return {"head": head, "status": status, "studio_toml_sha256": studio}

    def newer(self, ctx: SCtx) -> list[str]:
        c = ctx.run(
            [
                "find",
                str(STREAK),
                "-newer",
                str(self.marker),
                "-not",
                "-path",
                "*/.git/*",
                "-not",
                "-path",
                "*/.git",
            ]  # fmt: skip
        )
        return sorted(ln for ln in c.out.splitlines() if ln)


S1_STATE: dict[str, S1State] = {}


@probe("S1")
def s1(ctx: SCtx) -> Any:
    pred = (
        f"Streak HEAD {REF}... and ' M studio.toml' before and after the whole run; studio.toml "
        "sha256 unchanged; find -newer finds no file"
    )
    st = S1_STATE["s"]
    after = st.snapshot(ctx)
    newer = st.newer(ctx)
    checks = {
        "head_before": st.before["head"].startswith(REF),
        "head_after": after["head"].startswith(REF),
        "status_before": st.before["status"] == " M studio.toml",
        "status_after": after["status"] == " M studio.toml",
        "studio_sha_unchanged": after["studio_toml_sha256"] == st.before["studio_toml_sha256"],
        "no_newer_file": newer == [],
        "identical": after == st.before,
    }
    obs = (
        f"before {st.before['head'][:7]} {st.before['status']!r} studio.toml "
        f"{st.before['studio_toml_sha256'][:12]}; after {after['head'][:7]} {after['status']!r} "
        f"{after['studio_toml_sha256'][:12]}; files newer than the start marker: {len(newer)} "
        f"{newer[:5]}"
    )
    return Result(
        "S1",
        pred,
        obs,
        all(checks.values()),
        [{"before": st.before, "after": after, "newer": newer}],
        True,
        checks,
        {},
    )


@probe("T1")
def t1(ctx: SCtx) -> Any:
    pred = (
        "median wall time of the static check of one link over 10 runs <= 10 s (predicted <= "
        "1 s); `contracts export streak` time reported"
    )
    clone = ctx.clone("T1")
    m = ctx.workspace("T1", clone)
    checks_s, checks_e, exports_s = [], [], []
    runs = []
    for _ in range(ctx.runs):
        c = ctx.check(m)
        assert c.rc == 0, c.out + c.err
        e = ctx.export(m)
        assert e.rc == 0, e.err
        checks_s.append(c.wall)
        checks_e.append(c.json()["elapsed_s"])
        exports_s.append(e.wall)
        runs.append(
            {
                "check_wall_s": round(c.wall, 3),
                "check_elapsed_s": c.json()["elapsed_s"],
                "export_wall_s": round(e.wall, 3),
            }  # fmt: skip
        )
    med, med_e, med_x = (
        statistics.median(checks_s),
        statistics.median(checks_e),
        statistics.median(exports_s),
    )
    checks = {"static_median_le_10s": med <= 10, "static_prediction_le_1s": med <= 1}
    obs = (
        f"static check median {med:.2f} s wall ({med_e:.2f} s elapsed_s); export median "
        f"{med_x:.2f} s wall"
    )
    extra = {
        "median_check_wall_s": round(med, 3),
        "median_check_elapsed_s": round(med_e, 3),
        "median_export_wall_s": round(med_x, 3),
        "note": "timing is not deterministic: 'identical' is not applicable",
    }
    # the pass rule is the hard threshold; the prediction is reported
    return Result("T1", pred, obs, checks["static_median_le_10s"], runs, True, checks, extra)


# ---- driver ----------------------------------------------------------------------------------

ORDER = (
    ["B0"]
    + [d["id"] for d in DRIFTS]
    + [f"C{i}" for i in range(1, 7)]
    + [f"W{i}" for i in range(1, 5)]
    + [f"V{i}" for i in range(1, 7)]
    + ["S1", "T1"]
)


def preflight() -> dict[str, Any]:
    """`git diff 573aafc -- lattice/` must be empty: the experiment runs without code changes."""
    out = subprocess.run(
        ["git", "-C", str(REPO), "diff", "--stat", CODE_BASE, "--", "lattice/"],
        text=True,
        capture_output=True,
        check=False,
    )
    head = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True, capture_output=True, check=False
    ).stdout.strip()
    return {
        "lattice_diff_vs_573aafc": out.stdout.strip(),
        "lattice_diff_empty": out.returncode == 0 and out.stdout.strip() == "",
        "code_head": head,
        "golden_exists": GOLDEN.exists(),
        "widget_files": sorted(p.name for p in WIDGET.iterdir() if p.is_file()),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--scratch", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--only", default="", help="comma-separated probe ids")
    ap.add_argument("--keep", action="store_true", help="keep the scratch clones (default: delete)")
    ap.add_argument("--allow-code-diff", action="store_true",
                    help=f"run although lattice/ differs from {CODE_BASE} (the diff stat is "
                    "recorded in summary.json)")  # fmt: skip
    ns = ap.parse_args(argv)
    ids = [i for i in ns.only.split(",") if i] or ORDER
    unknown = [i for i in ids if i not in PROBES]
    if unknown:
        print(f"unknown probe ids: {unknown}", file=sys.stderr)
        return 2
    ids = [i for i in ORDER if i in ids]
    pre = preflight()
    pre["allow_code_diff"] = ns.allow_code_diff
    if not pre["lattice_diff_empty"] and not ns.allow_code_diff:
        print(f"lattice/ differs from {CODE_BASE}: the experiment needs no code change\n"
              f"{pre['lattice_diff_vs_573aafc']}", file=sys.stderr)  # fmt: skip
        return 2
    ns.out.mkdir(parents=True, exist_ok=True)
    ns.scratch.mkdir(parents=True, exist_ok=True)
    ctx = SCtx(ns.runs, ns.scratch.resolve(), ns.out)
    S1_STATE["s"] = S1State(ctx)
    t_start = time.time()
    results: list[Any] = []
    try:
        for pid in ids:
            if pid == "S1":
                continue  # after everything else (T1 included) has run
            print(f"... {pid}", file=sys.stderr, flush=True)
            res = PROBES[pid](ctx)
            results.append(res)
            base.write_probe(ctx, res)
        if "S1" in ids:
            res = PROBES["S1"](ctx)
            results.append(res)
            base.write_probe(ctx, res)
        results.sort(key=lambda r: ORDER.index(r.id))
    finally:
        procs = base.leftover_processes(ctx)
        tmp_left = sorted(p.name for p in ctx.tmp.iterdir()) if ctx.tmp.exists() else []
        if not ns.keep:
            shutil.rmtree(ctx.root, ignore_errors=True)
    passed = sum(1 for r in results if r.passed)
    print(f"{'id':<4} | {'predicted':<60} | {'observed':<80} | pass")
    for r in results:
        print(f"{r.id:<4} | {r.prediction} | {r.observed} | {'PASS' if r.passed else 'FAIL'}")
    print(f"probes: {passed}/{len(results)} pass")
    summary = {
        "experiment": "2026-10-13-contracts-streak",
        "command": "python dogfood/contracts/streak_probes.py " + " ".join(sys.argv[1:]),
        "runs": ns.runs,
        "code_root": str(REPO),
        "preflight": pre,
        "streak_ref": REF,
        "wall_s": round(time.time() - t_start, 1),
        "probes": [
            {
                "id": r.id,
                "prediction": r.prediction,
                "observed": r.observed,
                "pass": r.passed,
                "runs": len(r.runs),
                "identical": r.identical,
                "checks": r.checks,
            }
            for r in results
        ],
        "totals": {"pass": passed, "total": len(results)},
        "d_series": {
            "core_pass": sum(
                1 for r in results if r.id in {d["id"] for d in DRIFTS} and r.extra.get("pass_core")
            ),  # fmt: skip
            "full_pass": sum(
                1 for r in results if r.id in {d["id"] for d in DRIFTS} and r.passed
            ),  # fmt: skip
        },
        "s1": {"before": S1_STATE["s"].before},
        "leftover_processes": procs,
        "leftover_tmp_entries": tmp_left,
        "scratch_removed": not ns.keep,
        "post_hoc": {
            "script_corrections": POST_HOC_SCRIPT_CORRECTIONS,
            "procedure": POST_HOC_PROCEDURE,
        },
    }
    (ns.out / "summary.json").write_text(json.dumps(base.scrub(summary, ctx.root), indent=1) + "\n")
    return 0 if passed == len(results) and not procs else 1


if __name__ == "__main__":
    sys.exit(main())
