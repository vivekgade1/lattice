"""The visible smoke run of the edit slice (plan 2026-09-27 thin slice, node E3).

What a developer sees locally before shipping, without the hidden acceptance suite: the
app starts (or its traceback), and the ticket's happy-path requests -- taken from the
ticket text alone, never from the oracle -- answer as the ticket says (or their status,
body and, for a server error, the traceback). Both arms get exactly this as repair
feedback, besides arm L's checker diagnostics.

The smoke database is built the way a developer's is: the candidate app creates its
tables on an empty file (so new columns exist), then the app's own seed rows are copied
into the columns both have -- the template's users and items
(:mod:`bench.template_app.seed`), or a held-out repo's ``seed.py`` (TaskFlow's users and
tasks, JustNotes' users, notes, tags and keywords), per ``Ticket.repo``. The requests
sign in as that seed's users, through the app's own login route
(:mod:`bench.edit_slice.apps`).
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from bench.edit_slice.apps import Repo, repo
from bench.edit_slice.tickets import ticket
from bench.template_app.seed import TEMPLATE_USERS

API = "/api/v1"
AppFactory = Callable[[Path], Any]
TRACEBACK_LINES = 30
BODY_CHARS = 400
CREATED = "{created}"  # a path placeholder: the id of the item the scenario created


@dataclass(frozen=True)
class SmokeRequest:
    """One request as ``user`` (a seed handle, or None). ``expect``: the status the
    ticket implies; ``save``: remember the response's ``id`` as ``{created}``."""

    user: str | None
    method: str
    path: str
    expect: int = 200
    params: dict[str, Any] | None = None
    json: Any = None
    save: bool = False

    def label(self) -> str:
        query = "&".join(f"{k}={v}" for k, v in (self.params or {}).items())
        who = f" as {self.user}" if self.user else ""
        body = f" {self.json}" if self.json is not None else ""
        return f"{self.method} {self.path}{'?' + query if query else ''}{body}{who}"


_ITEMS = f"{API}/items/"
_ITEM = f"{API}/items/{CREATED}"
_USERS = f"{API}/users/"
_ALICE = TEMPLATE_USERS["alice"][2]
_BOB_EMAIL = TEMPLATE_USERS["bob"][0]
_BOB_ID = TEMPLATE_USERS["bob"][2]
_TASKS = f"{API}/tasks"
_TASK = f"{_TASKS}/{CREATED}"
_TASKFLOW_BOB_EMAIL = "bob@example.com"

# The happy paths each ticket's text describes (the requests a developer would try).
SMOKE: dict[str, tuple[SmokeRequest, ...]] = {
    "t1_search": (SmokeRequest("alice", "GET", _ITEMS, params={"q": "groc"}),),
    "t2_since": (
        SmokeRequest("alice", "GET", _ITEMS, params={"since": "2026-02-01T12:00:00Z"}),
        SmokeRequest("alice", "GET", _ITEMS, params={"sort": "oldest"}),
    ),
    "t3_soft_delete": (
        SmokeRequest("alice", "POST", _ITEMS, json={"title": "Smoke test"}, save=True),
        SmokeRequest("alice", "DELETE", _ITEM),
        SmokeRequest("alice", "GET", _ITEMS),
    ),
    "t4_restore": (
        SmokeRequest("admin", "POST", _ITEMS, json={"title": "Smoke test"}, save=True),
        SmokeRequest("admin", "DELETE", _ITEM),
        SmokeRequest("admin", "POST", f"{_ITEM}/restore"),
    ),
    "t5_title_window": (
        SmokeRequest("alice", "POST", _ITEMS, json={"title": "Smoke test"}, save=True),
        SmokeRequest("alice", "PUT", _ITEM, json={"title": "Smoke test, renamed"}),
    ),
    "t6_stats": (SmokeRequest("alice", "GET", f"{API}/items/stats"),),
    # the held-out tickets
    "t7_deactivate": (
        SmokeRequest("admin", "POST", f"{_USERS}{_ALICE}/deactivate"),
        SmokeRequest("admin", "POST", f"{_USERS}{_ALICE}/activate"),
    ),
    "t8_user_filter": (
        SmokeRequest("admin", "GET", _USERS, params={"is_active": "false"}),
        SmokeRequest("admin", "GET", _USERS, params={"q": "ALICE"}),
    ),
    "t9_item_quota": (SmokeRequest("alice", "POST", _ITEMS, json={"title": "Smoke test"}),),
    "t10_owner_counts": (SmokeRequest("admin", "GET", f"{_USERS}item-counts"),),
    "t11_title_trim": (
        SmokeRequest("alice", "POST", _ITEMS, json={"title": " Groceries "}, save=True),
        SmokeRequest("alice", "PUT", _ITEM, json={"title": " Groceries "}),
    ),
    "t12_transfer": (
        SmokeRequest("alice", "POST", _ITEMS, json={"title": "Smoke test"}, save=True),
        SmokeRequest("alice", "POST", f"{_ITEM}/transfer", json={"new_owner_email": _BOB_EMAIL}),
    ),
    # round 3's held-out template set (t13-t18; no Lattice reference)
    "t13_password_reset": (
        SmokeRequest("admin", "POST", f"{_USERS}{_ALICE}/reset-password",
                     json={"new_password": "NewSmokePass1"}),
    ),
    "t14_email_change": (
        SmokeRequest("alice", "PATCH", f"{_USERS}me/email",
                     json={"current_password": TEMPLATE_USERS["alice"][1],
                          "new_email": "alice.smoke@example.com"}),
    ),
    "t15_duplicate_item": (
        SmokeRequest("alice", "POST", _ITEMS, json={"title": "Smoke test"}, save=True),
        SmokeRequest("alice", "POST", f"{_ITEM}/duplicate"),
    ),
    "t16_admin_audit": (SmokeRequest("admin", "GET", f"{_ITEMS}audit"),),
    "t17_page_size_preference": (
        SmokeRequest("alice", "PUT", f"{_USERS}me/preferences", json={"default_page_size": 25}),
        SmokeRequest("alice", "GET", f"{_USERS}me/preferences"),
    ),
    "t18_duplicate_title_guard": (
        SmokeRequest("alice", "POST", _ITEMS, json={"title": "Smoke test"}),
    ),
    # round 3: the held-out repos (their own seed users, alice and bob)
    "t19_task_summary": (SmokeRequest("alice", "GET", f"{_TASKS}/summary"),),
    "t20_bulk_status": (  # tasks 1 and 2 are alice's in TaskFlow's seed
        SmokeRequest("alice", "POST", f"{_TASKS}/bulk-status",
                     json={"task_ids": [1, 2], "status": "done"}),
    ),
    "t21_completed_at": (
        SmokeRequest("alice", "POST", _TASKS, expect=201, json={"title": "Smoke test"},
                     save=True),
        SmokeRequest("alice", "PATCH", _TASK, json={"status": "done"}),
        SmokeRequest("alice", "GET", _TASK),
    ),
    "t22_copy_note": (
        SmokeRequest("alice", "POST", "/notes/create", expect=201,
                     json={"title": "Smoke test", "text": "Hello", "isPublic": False},
                     save=True),
        SmokeRequest("alice", "POST", f"/notes/{CREATED}/copy", expect=201),
    ),
    "t23_note_filter": (
        SmokeRequest("alice", "GET", "/notes", params={"tag_id": 1}),
        SmokeRequest("alice", "GET", "/notes", params={"q": "MILK"}),
    ),
    "t24_note_stats": (SmokeRequest("alice", "GET", "/notes/stats"),),
    # N4's fresh held-out set (t25-t30; see bench.edit_slice.tickets docstring)
    "t25_bulk_delete_items": (
        SmokeRequest("alice", "POST", f"{_ITEMS}bulk-delete", json={"item_ids": []}),
    ),
    "t26_move_items": (
        SmokeRequest("admin", "POST", f"{_USERS}{_ALICE}/move-items",
                     json={"to_user_id": str(_BOB_ID)}),
    ),
    "t27_item_description_coverage": (
        SmokeRequest("alice", "GET", f"{_ITEMS}description-coverage"),
    ),
    "t28_task_reassign": (
        SmokeRequest("alice", "POST", _TASKS, expect=201, json={"title": "Smoke test"},
                     save=True),
        SmokeRequest("alice", "POST", f"{_TASK}/reassign",
                     json={"new_owner_email": _TASKFLOW_BOB_EMAIL}),
    ),
    "t29_tag_summary": (SmokeRequest("alice", "GET", "/tags/summary"),),
    "t30_bulk_delete_notes": (
        SmokeRequest("alice", "POST", "/notes/bulk-delete", json={"note_ids": []}),
    ),
    # option 1, the multi-vendor app: vicky is a vendor with products, carol a buyer (her
    # orders 1 and 8 in the app's seed), admin the admin
    "t31_vendor_sales": (SmokeRequest("vicky", "GET", "/vendors/me/sales"),),
    "t32_monthly_report": (
        SmokeRequest("admin", "GET", "/orders/report", params={"month": "2026-02"}),
    ),
    "t33_top_sellers": (SmokeRequest(None, "GET", "/products/top-sellers"),),
    "t34_customer_spend": (SmokeRequest("admin", "GET", "/orders/customers"),),
    "t35_vendor_order_lines": (SmokeRequest("vicky", "GET", "/vendors/me/order-lines"),),
    "t36_cancel_order": (SmokeRequest("carol", "POST", "/orders/8/cancel"),),
    "t37_product_buyers": (SmokeRequest("vicky", "GET", "/products/1/buyers"),),
    "t38_reorder": (SmokeRequest("carol", "POST", "/orders/1/reorder", expect=201),),
    # option 1b (order 8 is carol's pending order: lines 12 and 13)
    "t39_bulk_price": (
        SmokeRequest("vicky", "POST", "/products/bulk-price",
                     json={"changes": [{"product_id": 1, "price": 13.0}]}),
    ),
    "t40_edit_order_items": (
        SmokeRequest("carol", "PATCH", "/orders/8/items",
                     json={"items": [{"product_id": 2, "quantity": 2}]}),
    ),
    "t41_close_shop": (SmokeRequest("vera", "POST", "/vendors/me/close"),),
    "t42_split_order": (
        SmokeRequest("carol", "POST", "/orders/8/split", expect=201, json={"item_ids": [13]}),
    ),
    "t43_vendor_onboarding": (
        SmokeRequest("frank", "POST", "/vendors/onboard", expect=201,
                     json={"business_name": "Frank's Fixtures",
                           "products": [{"name": "Desk lamp", "category": "home",
                                         "price": 30.0, "stock_quantity": 5}]}),
    ),
    "t44_merge_orders": (
        SmokeRequest("carol", "POST", "/orders/merge", expect=400, json={"order_ids": [8, 1]}),
    ),
}  # fmt: skip


@dataclass
class SmokeResult:
    """``started``: the app was built; ``lines``: one line per request (or the start
    failure); ``failed``: some request crashed or answered another status."""

    started: bool
    lines: list[str] = field(default_factory=list)
    failed: bool = False

    def render(self) -> str:
        return "\n".join(self.lines)


def _tail(text: str, n: int = TRACEBACK_LINES) -> str:
    lines = text.rstrip("\n").splitlines()
    return "\n".join((["..."] if len(lines) > n else []) + lines[-n:])


def format_exception(exc: BaseException) -> str:
    return _tail("".join(traceback.format_exception(exc)))


@cache
def _seed_db(repo_name: str = "template") -> Path:
    root = Path(tempfile.mkdtemp(prefix="edit-slice-smoke-seed-"))
    return repo(repo_name).seed()(root / "seed.db")


def _copy_seed(db_path: Path, repo_name: str = "template") -> None:
    """Copy the app's seed rows into ``db_path``'s tables (shared columns)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("ATTACH DATABASE ? AS seed", (str(_seed_db(repo_name)),))
        for table in repo(repo_name).seed_tables:
            mine = [r[1] for r in conn.execute(f'PRAGMA main.table_info("{table}")')]
            theirs = {r[1] for r in conn.execute(f'PRAGMA seed.table_info("{table}")')}
            cols = ", ".join(f'"{c}"' for c in mine if c in theirs)
            conn.execute(f'INSERT INTO main."{table}" ({cols}) SELECT {cols} FROM seed."{table}"')
        conn.commit()
    finally:
        conn.close()


def build_app(factory: AppFactory, root: Path, repo_name: str = "template") -> Any:
    """The candidate app on a fresh seeded smoke database under ``root`` (raises what
    the candidate raises)."""
    db = root / "smoke.db"
    if db.exists():
        db.unlink()
    app = factory(db)
    _copy_seed(db, repo_name)
    return app


def _login(client: Any, handle: str, app: Repo | None = None) -> str:
    app = app or repo("template")
    username, password = app.users()[handle]
    r = client.post(app.login_path, data={"username": username, "password": password})
    if r.status_code != 200:
        raise RuntimeError(f"login as {handle} answered {r.status_code}: {r.text[:BODY_CHARS]}")
    return r.json()["access_token"]


def run_smoke(ticket_id: str, factory: AppFactory) -> SmokeResult:
    """Build the app on a smoke database and issue the ticket's happy-path requests."""
    from fastapi.testclient import TestClient

    app_repo = repo(ticket(ticket_id).repo)
    root = Path(tempfile.mkdtemp(prefix="edit-slice-smoke-"))
    try:
        try:
            app = build_app(factory, root, app_repo.name)
        except Exception as exc:  # noqa: BLE001 -- the developer sees the traceback
            return SmokeResult(False, ["The app failed to start:", format_exception(exc)], True)
        result = SmokeResult(True)
        try:
            with TestClient(app, raise_server_exceptions=True) as client:
                _requests(ticket_id, client, result, app_repo)
        except Exception as exc:  # noqa: BLE001 -- e.g. the lifespan failed
            result.failed = True
            result.lines += ["The app failed while serving:", format_exception(exc)]
        finally:
            engine = getattr(getattr(app, "state", None), "engine", None)
            if engine is not None:
                engine.dispose()
        return result
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _requests(ticket_id: str, client: Any, result: SmokeResult, app: Repo) -> None:
    tokens: dict[str, str] = {}
    created: str | None = None
    for req in SMOKE[ticket_id]:
        headers = {}
        if req.user is not None:
            if req.user not in tokens:
                try:
                    tokens[req.user] = _login(client, req.user, app)
                except Exception as exc:  # noqa: BLE001
                    result.failed = True
                    result.lines += [f"login as {req.user} failed:", format_exception(exc)]
                    return
            headers["Authorization"] = f"Bearer {tokens[req.user]}"
        if CREATED in req.path and created is None:
            result.failed = True
            result.lines.append(f"{req.label()} -> skipped (no item was created)")
            continue
        path = req.path.replace(CREATED, created or "")
        try:
            r = client.request(req.method, path, params=req.params, json=req.json, headers=headers)
        except Exception as exc:  # noqa: BLE001 -- a server error, raised by TestClient
            result.failed = True
            result.lines += [f"{req.label()} -> 500 (server error)", format_exception(exc)]
            continue
        line = f"{req.label()} -> {r.status_code}"
        if r.status_code != req.expect:
            result.failed = True
            line += f" (expected {req.expect}): {r.text[:BODY_CHARS]}"
        result.lines.append(line)
        if req.save and r.status_code < 300:
            try:
                created = str(r.json()["id"])
            except Exception:  # noqa: BLE001
                created = None


def smoke_feedback(result: SmokeResult) -> str:
    """The repair message part for a failed smoke run."""
    head = (
        "Running the app locally with the ticket's basic requests"
        if result.started
        else "Starting the app locally"
    )
    return f"{head}:\n{result.render()}"


__all__ = ["SMOKE", "SmokeRequest", "SmokeResult", "build_app", "run_smoke", "smoke_feedback"]
