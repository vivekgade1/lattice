"""Sub-project 1c node T2b fixture: an API program that calls procedures from its
endpoints, its profile and a seeded SQLite database.

Covers scalar procedures in derive / where / require (T2-post reads, a procedure filter
after page), whole-value calls returned as a List or bound as a scalar and written by
update, insert_many of a procedure's rows, an effectful procedure sent with for_each,
the l12 login-lockout state machine as a procedure, and R620 (fuel, recursion depth).

Shared by the runtime tests (tests/api/test_t2_runtime.py) and the codegen differential
(tests/api/test_codegen_differential.py).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from lattice.profile import ApiProfile, NativeSig, Profile
from lattice.typesys import Scalar, Schema
from tests.api.t2_natives import NATIVES as NATIVES

S = Scalar
NATIVES_FILE = Path(__file__).resolve().parent / "t2_natives.py"
FUEL = 20_000  # small, so that running out is quick

USERS = Schema.from_pairs(
    [("id", S.INT), ("email", S.STR), ("full_name", S.STR), ("role", S.STR), ("token", S.STR)],
    keys=[("id",)],
    private=["token"],
)
ITEMS = Schema.from_pairs(
    [
        ("id", S.INT),
        ("owner_id", S.INT),
        ("title", S.STR),
        ("due", S.DATE),
        ("done", S.BOOL),
        ("created_at", S.DATETIME),
        ("score", S.FLOAT),
    ],
    keys=[("id",)],
    nullable=["due"],
    owner="owner_id",
)
LOGIN_EVENTS = Schema.from_pairs(
    [("id", S.INT), ("user_id", S.INT), ("minute", S.INT), ("kind", S.STR)],
    keys=[("id",)],
)

API = ApiProfile(
    tables={"Users": USERS, "Items": ITEMS, "LoginEvents": LOGIN_EVENTS},
    table_names={"Users": "users", "Items": "items", "LoginEvents": "login_events"},
    roles=frozenset({"Public", "Customer", "Admin"}),
    natives={
        "send_digest": NativeSig((S.STR, S.STR), effect=True, capability="mail"),
        "slugify": NativeSig((S.STR,), S.STR),
    },
    page_shape={"items": "items", "total": "total", "limit": "limit", "offset": "offset"},
    fuel=FUEL,
)
PROFILE = Profile(schemas={}, api=API)

TOKENS = {"admin": "tok-admin", "alice": "tok-alice", "bob": "tok-bob", "carol": "tok-carol"}
USER_ROWS = [
    (1, "admin@example.com", "Admin", "Admin", TOKENS["admin"]),
    (2, "alice@example.com", "Alice", "Customer", TOKENS["alice"]),
    (3, "bob@example.com", "Bob", "Customer", TOKENS["bob"]),
    (4, "carol@example.com", "Carol", "Customer", TOKENS["carol"]),  # owns no items
]
# (id, owner, title, due, done, created_at, score)
ITEM_ROWS = [
    (1, 2, "alpha task", "2000-01-05", 0, "2000-01-01T10:00:00.000000Z", 1.5),
    (2, 2, "be", None, 0, "2000-01-02T10:00:00.000000Z", 2.0),
    (3, 3, "gamma ray", "2000-01-07", 1, "2000-01-03T10:00:00.000000Z", 3.25),
    (4, 2, "boom box", "2000-01-09", 0, "2000-01-04T10:00:00.000000Z", 0.5),
    (5, 2, "delta", None, 1, "2000-01-05T10:00:00.000000Z", 4.0),
    (6, 1, "admin note", None, 0, "2000-01-06T10:00:00.000000Z", 0.0),
]
# (id, user_id, minute, kind): the l12 state machine's input
EVENT_ROWS = [
    (1, 2, 0, "fail"),
    (2, 2, 1, "fail"),
    (3, 2, 2, "fail"),
    (4, 2, 5, "ok"),
    (5, 2, 40, "ok"),
    (6, 3, 3, "ok"),
    (7, 3, 4, "reset"),
    (8, 2, 41, "fail"),
    (9, 3, 7, "fail"),
    (10, 2, 42, "reset"),
]


def build_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, full_name TEXT NOT NULL,
                role TEXT NOT NULL, token TEXT NOT NULL UNIQUE
            );
            CREATE TABLE items (
                id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id),
                title TEXT NOT NULL, due TEXT, done INTEGER NOT NULL,
                created_at TEXT NOT NULL, score REAL NOT NULL
            );
            CREATE TABLE login_events (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, minute INTEGER NOT NULL,
                kind TEXT NOT NULL
            );
            """
        )
        conn.executemany("INSERT INTO users VALUES (?, ?, ?, ?, ?)", USER_ROWS)
        conn.executemany("INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?)", ITEM_ROWS)
        conn.executemany("INSERT INTO login_events VALUES (?, ?, ?, ?)", EVENT_ROWS)
        conn.commit()
    finally:
        conn.close()


PROCEDURES = '''# lattice: v1
import math

LOCK_AFTER = 3
LOCK_MINUTES = 30
PREFIX = "* "


@record
class Line:
    id: Int
    text: Str
    slug: Str


@record
class LoginEvent:
    id: Int
    user_id: Int
    minute: Int
    kind: Str


@record
class LoginState:
    failures: Int
    locked_until: Int | None
    sessions: Int


@record
class LoginResult:
    id: Int
    user_id: Int
    minute: Int
    kind: Str
    status: Str
    sessions: Int


@record
class NewEvent:
    user_id: Int
    minute: Int
    kind: Str


@procedure
def is_stale(item: Row[Items], caller: Caller, now: DateTime) -> Bool:
    """Not done, and created more than a day ago (Admins see nothing stale)."""
    if caller.is_admin:
        return False
    return not item.done and now - item.created_at > hours(24)


@procedure
def label(title: Str, due: Date | None) -> Str:
    words = [w.capitalize() for w in title.split()]
    text = " ".join(words)
    if due is None:
        return text + " (no due date)"
    return f"{text} (due {due:%b %d})"


@procedure
def weekday_of(due: Date | None) -> Int | None:
    if due is None:
        return None
    return due.weekday()


@procedure
def is_long(title: Str, min_len: Int) -> Bool:
    return len(title.replace(" ", "")) >= min_len


@procedure
def visible_to(item: Row[Items], caller: Caller) -> Bool:
    """Admins see every item; others the ones not done, or their own."""
    return caller.role == "Admin" or not item.done or item.owner_id == caller.id


@procedure
def digest(rows: list[Row[Items]], prefix: Str) -> list[Line]:
    out: list[Line] = []
    for r in sorted(rows, key=lambda r: (-r.score, r.id)):
        text = prefix + r.title
        if r.due is not None:
            text = text + " @" + r.due.isoformat()
        out.append(Line(id=r.id, text=text, slug=native.slugify(r.title)))
    return out


@procedure
def weighted(rows: list[Row[Items]], factor: Float) -> Float:
    total = 0.0
    for r in rows:
        total += r.score * factor
    return math.floor(total * 100) / 100


@procedure
def expand(n: Int, uid: Int, kind: Str) -> list[NewEvent]:
    return [NewEvent(user_id=uid, minute=100 + i * 7, kind=kind) for i in range(n)]


@procedure(effects=["mail"])
def notify(to: Str, title: Str) -> None:
    native.send_digest(to, title.upper())


@procedure
def spin(n: Int) -> Int:
    total = 0
    while True:
        total += n
    return total


@procedure
def depth(n: Int) -> Int:
    if n <= 0:
        return 0
    return 1 + depth(n - 1)


@procedure
def replay_logins(events: list[LoginEvent]) -> list[LoginResult]:
    """The l12 login-lockout state machine (tests/fixtures/loops/l12_*)."""
    state: dict[Int, LoginState] = {}
    out: list[LoginResult] = []
    for ev in sorted(events, key=lambda e: (e.user_id, e.minute, e.id)):
        s = state.get(ev.user_id)
        if s is None:
            s = LoginState(failures=0, locked_until=None, sessions=0)
        failures = s.failures
        locked_until = s.locked_until
        sessions = s.sessions
        if locked_until is not None and ev.minute >= locked_until:
            locked_until = None
            failures = 0
        if ev.kind == "reset":
            locked_until = None
            failures = 0
            status = "reset"
        elif locked_until is not None:
            status = "refused"
        elif ev.kind == "fail":
            failures += 1
            if failures >= LOCK_AFTER:
                locked_until = ev.minute + LOCK_MINUTES
                status = "locked"
            else:
                status = "failed"
        else:
            failures = 0
            sessions += 1
            status = "ok"
        state[ev.user_id] = LoginState(
            failures=failures, locked_until=locked_until, sessions=sessions
        )
        out.append(
            LoginResult(
                id=ev.id,
                user_id=ev.user_id,
                minute=ev.minute,
                kind=ev.kind,
                status=status,
                sessions=sessions,
            )
        )
    return out
'''

ENDPOINTS = """

@endpoint("GET", "/items", auth=Customer)
def list_items(user: CurrentUser, limit: Int(ge=1, le=20) = 3, offset: Int(ge=0) = 0) -> Page:
    items = fetch(Items)
    mine = accessible(items, by=user)
    ordered = sort(mine, by=["id"])
    flagged = derive(
        ordered,
        stale=is_stale(row(), user, fn.now()),
        text=label(col("title"), col("due")),
        weekday=weekday_of(col("due")),
        twice=col("score") * 2,
    )
    shown = select(flagged, cols=["id", "text", "stale", "weekday", "twice"])
    result = page(shown, limit=limit, offset=offset, total=True)
    return result


@endpoint("GET", "/items/long", auth=Customer)
def long_items(
    user: CurrentUser, min_len: Int(ge=0, le=20) = 5, limit: Int(ge=1, le=20) = 4
) -> Page:
    items = fetch(Items)
    mine = accessible(items, by=user)
    ordered = sort(mine, by=["id"], desc=True)
    shown = select(ordered, cols=["id", "title"])
    paged = page(shown, limit=limit)
    long = where(paged, is_long(col("title"), min_len))
    return long


@endpoint("GET", "/items/{item_id}", auth=Customer)
def get_item(item_id: Int(ge=1), user: CurrentUser) -> One | NotFound | Forbidden:
    items = fetch(Items)
    mine = accessible(items, by=user)
    found = where(mine, col("id") == item_id)
    item = one(found, else_=NotFound("item"))
    allowed = require(item, visible_to(row(), user), else_=Forbidden("done"))
    long = require(allowed, is_long(col("title"), 3), else_=Forbidden("title too short"))
    named = derive(long, text=label(title=col("title"), due=col("due")))
    shown = select(named, cols=["id", "text"])
    return shown


@endpoint("GET", "/items/{item_id}/long", auth=Customer)
def item_if_long(item_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
    items = fetch(Items)
    mine = accessible(items, by=user)
    found = where(mine, col("id") == item_id)
    long = where(found, is_long(col("title"), 5))
    shown = select(long, cols=["id", "title"])
    item = one(shown, else_=NotFound("no long item"))
    return item


@endpoint("GET", "/digest", auth=Customer)
def get_digest(user: CurrentUser) -> List:
    items = fetch(Items)
    mine = accessible(items, by=user)
    ordered = sort(mine, by=["id"])
    few = limit(ordered, n=10)
    lines = call(digest, rows=few, prefix=PREFIX)
    return lines


@endpoint("PUT", "/items/{item_id}/rescore", auth=Customer)
def rescore(
    item_id: Int(ge=1), user: CurrentUser, factor: Float(ge=0, le=10) = 1.5
) -> One | NotFound:
    items = fetch(Items)
    mine = accessible(items, by=user)
    found = where(mine, col("id") == item_id)
    item = one(found, else_=NotFound("item"))
    s = call(weighted, rows=item, factor=factor)
    updated = update(item, score=s)
    shown = select(updated, cols=["id", "score"])
    return shown


@endpoint("POST", "/events/expand", auth=Admin)
def expand_events(
    user: CurrentUser, n: Int(ge=0, le=4), kind: OneOf("fail", "ok", "reset") = "fail"
) -> List:
    events = call(expand, n=n, uid=user.id, kind=kind)
    written = insert_many(LoginEvents, rows=events)
    return written


@endpoint("POST", "/items/{item_id}/notify", auth=Customer)
def notify_item(item_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
    items = fetch(Items)
    mine = accessible(items, by=user)
    found = where(mine, col("id") == item_id)
    item = one(found, else_=NotFound("item"))
    for_each(item, notify, user.email, col("title"))
    shown = select(item, cols=["id", "title"])
    return shown


@endpoint("GET", "/logins/replay", auth=Admin)
def replay(user: CurrentUser) -> List:
    events = fetch(LoginEvents)
    ordered = sort(events, by=["id"])
    few = limit(ordered, n=100)
    results = call(replay_logins, events=few)
    return results


@endpoint("GET", "/spin", auth=Customer)
def spin_forever(user: CurrentUser) -> One | NotFound:
    items = fetch(Items)
    mine = accessible(items, by=user)
    ordered = sort(mine, by=["id"])
    first = limit(ordered, n=1)
    spun = derive(first, total=spin(col("id")))
    shown = select(spun, cols=["id", "total"])
    item = one(shown, else_=NotFound("item"))
    return item


@endpoint("GET", "/depth/{n}", auth=Customer)
def deep(n: Int(ge=0, le=100000), user: CurrentUser) -> One | NotFound:
    items = fetch(Items)
    mine = accessible(items, by=user)
    ordered = sort(mine, by=["id"])
    first = limit(ordered, n=1)
    went = derive(first, depth=depth(n))
    shown = select(went, cols=["id", "depth"])
    item = one(shown, else_=NotFound("item"))
    return item
"""

PROGRAM = PROCEDURES + ENDPOINTS

# L880: the same list with the procedure filter before page() (rejected).
L880_PROGRAM = PROGRAM.replace(
    """    shown = select(ordered, cols=["id", "title"])
    paged = page(shown, limit=limit)
    long = where(paged, is_long(col("title"), min_len))
    return long""",
    """    shown = select(ordered, cols=["id", "title"])
    long = where(shown, is_long(col("title"), min_len))
    paged = page(long, limit=limit)
    return paged""",
)

OUTBOX_AFTER = (
    "SELECT endpoint, native, args, status, error, attempts, claimed_at IS NOT NULL "
    "FROM lattice_outbox ORDER BY id"
)
EVENTS_AFTER = "SELECT id, user_id, minute, kind FROM login_events ORDER BY id"
SCORES_AFTER = "SELECT id, score FROM items ORDER BY id"
